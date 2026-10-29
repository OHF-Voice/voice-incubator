#include "voice_labs.h"

#include "esphome/components/json/json_util.h"
#include "esphome/components/network/util.h"
#include "esphome/core/alloc_helpers.h"
#include "esphome/core/application.h"
#include "esphome/core/hal.h"
#include "esphome/core/log.h"

#include <cerrno>
#include <cinttypes>
#include <algorithm>
#include <cstring>
#include <utility>

namespace esphome::voice_labs {

static const char *const TAG = "voice_labs";

/// Keeps a stalled socket from growing the outgoing queue without bound.
static constexpr size_t TX_BUFFER_LIMIT = 16384;
/// Upper bound on bytes moved out of the microphone per loop iteration.
static constexpr size_t MIC_BYTES_PER_LOOP = 8192;
/// Chunk size handed to the speaker at a time.
static constexpr size_t PLAY_CHUNK_SIZE = 2048;
/// A refused client retries, so the warning is rate limited.
static constexpr uint32_t REFUSAL_LOG_INTERVAL_MS = 10000;
/// Monitoring outside calls: half a second of headroom for a slow loop.
static constexpr size_t MONITOR_BUFFER_BYTES = BYTE_RATE / 2;
/// How often a monitored device reports its models' probabilities.
static constexpr uint32_t WAKE_REPORT_INTERVAL_MS = 100;

int CallEventData::get_int(const std::string &key, int default_value) const {
  auto it = this->values.find(key);
  if (it == this->values.end()) {
    return default_value;
  }
  return strtol(it->second.c_str(), nullptr, 10);
}

float CallEventData::get_float(const std::string &key, float default_value) const {
  auto it = this->values.find(key);
  if (it == this->values.end()) {
    return default_value;
  }
  return strtof(it->second.c_str(), nullptr);
}

bool CallEventData::get_bool(const std::string &key, bool default_value) const {
  auto it = this->values.find(key);
  if (it == this->values.end()) {
    return default_value;
  }
  return it->second == "true" || it->second == "1";
}

void VoiceLabs::setup() {
  // Headroom above the pre-roll so a slow loop does not immediately overwrite
  // audio that is already part of an active call.
  const size_t mic_bytes = (this->preroll_ms_ + 500) * BYTE_RATE / 1000;
  this->mic_ring_ = ring_buffer::RingBuffer::create(mic_bytes);
  if (this->mic_ring_ == nullptr) {
    ESP_LOGE(TAG, "Failed to allocate %zu byte mic buffer", mic_bytes);
    this->mark_failed();
    return;
  }

  if (this->aec_source_ != nullptr) {
    this->aec_ring_ = ring_buffer::RingBuffer::create(mic_bytes);
    if (this->aec_ring_ == nullptr) {
      ESP_LOGE(TAG, "Failed to allocate %zu byte AEC buffer", mic_bytes);
      this->mark_failed();
      return;
    }
  }

  if (this->aec_source_ != nullptr) {
    this->monitor_aec_ring_ = ring_buffer::RingBuffer::create(MONITOR_BUFFER_BYTES);
    if (this->monitor_aec_ring_ == nullptr)
      ESP_LOGW(TAG, "Failed to allocate %zu byte AEC monitor buffer", MONITOR_BUFFER_BYTES);
  }
  this->monitor_ring_ = ring_buffer::RingBuffer::create(MONITOR_BUFFER_BYTES);
  if (this->monitor_ring_ == nullptr) {
    ESP_LOGE(TAG, "Failed to allocate %zu byte monitor buffer", MONITOR_BUFFER_BYTES);
    this->mark_failed();
    return;
  }

  const size_t out_bytes = this->output_buffer_ms_ * BYTE_RATE / 1000;
  this->out_ring_ = ring_buffer::RingBuffer::create(out_bytes);
  if (this->out_ring_ == nullptr) {
    ESP_LOGE(TAG, "Failed to allocate %zu byte output buffer", out_bytes);
    this->mark_failed();
    return;
  }

  this->rx_payload_.reserve(MAX_PAYLOAD_SIZE);
  this->scratch_.resize(MAX_PAYLOAD_SIZE);
  // send_frame_ keeps it within TX_BUFFER_LIMIT, so it never grows again.
  this->tx_buffer_.reserve(TX_BUFFER_LIMIT);

  this->speaker_->set_audio_stream_info(audio::AudioStreamInfo(16, 1, SAMPLE_RATE));
  this->speaker_->set_volume(this->volume_);

  // Runs on the microphone's task, not the main loop.
  this->mic_source_->add_data_callback([this](const std::vector<uint8_t> &data) {
    if (this->mic_ring_ == nullptr || data.empty()) {
      return;
    }
    if (this->in_call_) {
      // Overwriting mid-call would silently corrupt the stream, so drop and count.
      const size_t written = this->mic_ring_->write_without_replacement(data.data(), data.size(), 0);
      this->mic_dropped_bytes_ += data.size() - written;
    } else {
      // Idle: oldest-out is exactly the pre-roll behaviour we want.
      this->mic_ring_->write(data.data(), data.size());
      if (this->monitoring_.load(std::memory_order_relaxed)) {
        // Someone is listening: the same audio, streamed as it comes.
        this->monitor_ring_->write(data.data(), data.size());
      }
    }
  });

  if (!this->wake_detector_.setup()) {
    ESP_LOGE(TAG, "Wake detector setup failed");
    this->mark_failed();
    return;
  }

  if (this->aec_source_ != nullptr) {
    this->aec_source_->add_data_callback([this](const std::vector<uint8_t> &data) {
      if (this->aec_ring_ == nullptr || data.empty()) {
        return;
      }
      if (this->in_call_) {
        const size_t written = this->aec_ring_->write_without_replacement(data.data(), data.size(), 0);
        this->aec_dropped_bytes_ += data.size() - written;
      } else {
        this->aec_ring_->write(data.data(), data.size());
        if (this->monitor_aec_ring_ != nullptr && this->monitoring_.load(std::memory_order_relaxed))
          this->monitor_aec_ring_->write(data.data(), data.size());
      }
    });
  }

  this->listen_socket_ = socket::socket_ip_loop_monitored(SOCK_STREAM, 0);
  if (this->listen_socket_ == nullptr) {
    ESP_LOGE(TAG, "Socket creation failed: errno %d", errno);
    this->mark_failed();
    return;
  }

  int enable = 1;
  this->listen_socket_->setsockopt(SOL_SOCKET, SO_REUSEADDR, &enable, sizeof(int));

  if (this->listen_socket_->setblocking(false) != 0) {
    ESP_LOGE(TAG, "Socket nonblocking failed: errno %d", errno);
    this->mark_failed();
    return;
  }

  struct sockaddr_storage server;
  socklen_t sl = socket::set_sockaddr_any((struct sockaddr *) &server, sizeof(server), this->port_);
  if (sl == 0 || this->listen_socket_->bind((struct sockaddr *) &server, sl) != 0) {
    ESP_LOGE(TAG, "Socket bind to %u failed: errno %d", this->port_, errno);
    this->mark_failed();
    return;
  }

  if (this->listen_socket_->listen(2) != 0) {
    ESP_LOGE(TAG, "Socket listen failed: errno %d", errno);
    this->mark_failed();
    return;
  }
}

void VoiceLabs::dump_config() {
  ESP_LOGCONFIG(TAG, "Voice Labs:");
  ESP_LOGCONFIG(TAG, "  Port: %u", this->port_);
  ESP_LOGCONFIG(TAG, "  Pre-roll: %" PRIu32 " ms", this->preroll_ms_);
  ESP_LOGCONFIG(TAG, "  Output buffer: %" PRIu32 " ms", this->output_buffer_ms_);
  ESP_LOGCONFIG(TAG, "  Call timeout: %" PRIu32 " ms", this->call_timeout_ms_);
  ESP_LOGCONFIG(TAG, "  Volume: %.2f", this->volume_);
  ESP_LOGCONFIG(TAG, "  Wake word models: %zu (pushed by the app at connect)", this->wake_detector_.model_count());
}

void VoiceLabs::loop() {
  if (this->listen_socket_ == nullptr) {
    return;
  }

  this->accept_client_();
  if (this->client_ == nullptr) {
    return;
  }

  this->poll_wake_detector_();

  this->read_socket_();
  if (this->client_ == nullptr) {
    return;
  }

  if (this->in_call_) {
    this->pump_microphone_();

    const uint32_t now = App.get_loop_component_start_time();
    if (now - this->call_started_at_ > this->call_timeout_ms_) {
      ESP_LOGW(TAG, "Call timed out after %" PRIu32 " ms", this->call_timeout_ms_);
      this->end_call("timeout");
    } else if (now - this->last_status_at_ >= this->status_interval_ms_) {
      this->last_status_at_ = now;
      this->send_status_();
    }
  } else if (this->monitoring_) {
    // A call streams its own audio; between calls, the monitor's: both
    // captures, taking turns at going first (as a call's do) -- the first
    // alone for the intercom, which plays it on another satellite.
    ring_buffer::RingBuffer *aec = this->intercom_ ? nullptr : this->monitor_aec_ring_.get();
    if (this->pump_monitor_aec_first_)
      this->pump_ring_(aec, MessageType::AUDIO_MONITOR_AEC, MIC_BYTES_PER_LOOP);
    this->pump_ring_(this->monitor_ring_.get(), MessageType::AUDIO_MONITOR, MIC_BYTES_PER_LOOP);
    if (!this->pump_monitor_aec_first_)
      this->pump_ring_(aec, MessageType::AUDIO_MONITOR_AEC, MIC_BYTES_PER_LOOP);
    this->pump_monitor_aec_first_ = !this->pump_monitor_aec_first_;
  }
  if (this->monitoring_ && !this->intercom_) {
    const uint32_t now = App.get_loop_component_start_time();
    if (now - this->last_wake_report_at_ >= WAKE_REPORT_INTERVAL_MS) {
      this->last_wake_report_at_ = now;
      this->send_wake_report_();
    }
  }

  this->pump_speaker_();
  this->check_playback_drained_();
  this->flush_tx_();
}

void VoiceLabs::accept_client_() {
  struct sockaddr_storage source_addr;
  socklen_t addr_len = sizeof(source_addr);
  auto sock = this->listen_socket_->accept_loop_monitored((struct sockaddr *) &source_addr, &addr_len);
  if (sock == nullptr) {
    return;
  }

  // One client at a time. Replacing the live one instead would put two apps
  // into a reconnect loop, each kicking the other off forever, with nothing in
  // either log pointing at the cause. A peer that died without a FIN is cleared
  // by SO_KEEPALIVE below within about 25 s, so refusing cannot wedge the
  // device the way it would without keepalive.
  if (this->client_ != nullptr) {
    char addr_buf[socket::SOCKADDR_STR_LEN];
    sock->getpeername_to(addr_buf);
    const uint32_t now = App.get_loop_component_start_time();
    if (now - this->last_refusal_log_ >= REFUSAL_LOG_INTERVAL_MS) {
      this->last_refusal_log_ = now;
      ESP_LOGW(TAG, "Refusing connection from %s: already connected to another client", addr_buf);
    }
    sock->close();
    return;
  }

  if (sock->setblocking(false) != 0) {
    ESP_LOGW(TAG, "Client nonblocking failed: errno %d", errno);
    return;
  }

  int enable = 1;
  // Audio frames are small and latency-sensitive; waiting to coalesce them hurts.
  sock->setsockopt(IPPROTO_TCP, TCP_NODELAY, &enable, sizeof(int));

  // Without this a peer that disappears without a FIN holds the slot forever.
  int idle = 10, interval = 5, count = 3;
  sock->setsockopt(SOL_SOCKET, SO_KEEPALIVE, &enable, sizeof(int));
  sock->setsockopt(IPPROTO_TCP, TCP_KEEPIDLE, &idle, sizeof(int));
  sock->setsockopt(IPPROTO_TCP, TCP_KEEPINTVL, &interval, sizeof(int));
  sock->setsockopt(IPPROTO_TCP, TCP_KEEPCNT, &count, sizeof(int));

  char addr_buf[socket::SOCKADDR_STR_LEN];
  sock->getpeername_to(addr_buf);
  ESP_LOGI(TAG, "Home Assistant connected from %s", addr_buf);

  this->client_ = std::move(sock);
  this->tx_buffer_.clear();
  this->tx_sent_ = 0;
  this->rx_header_filled_ = 0;
  this->rx_payload_filled_ = 0;
  this->rx_in_payload_ = false;

  const std::string hello = json::build_json([this](JsonObject root) {
    root["protocol"] = 1;
    root["name"] = App.get_name();
    root["mac"] = get_mac_address();
    root["sample_rate"] = SAMPLE_RATE;
    root["channels"] = 1;
    root["bits_per_sample"] = 16;
    root["preroll_ms"] = this->preroll_ms_;
    root["output_buffer_bytes"] = this->out_ring_->free() + this->out_ring_->available();
    // No model is compiled in, so the app has to push one before this device
    // can detect anything.
    root["wake_models"] = this->wake_detector_.model_count();
    root["model_slots"] = MAX_MODEL_SLOTS;
    root["feature_size"] = FEATURE_SIZE;
    // Its executor runs pooled models (MIXEDNET_OP_MAX_POOL).
    root["max_pool"] = true;
    // It can stream the microphone outside calls (MONITOR), and the second
    // capture with it; and be joined to another satellite by the intercom.
    root["monitor"] = true;
    root["intercom"] = true;
    if (this->monitor_aec_ring_ != nullptr)
      root["monitor_aec"] = true;
    if (this->aec_lead_samples_ >= 0)
      root["aec_lead_samples"] = this->aec_lead_samples_;
  });
  this->send_json_frame_(MessageType::HELLO, hello);
  this->flush_tx_();

  this->connected_trigger_.trigger();
}

void VoiceLabs::disconnect_client_(const char *reason) {
  if (this->client_ == nullptr) {
    return;
  }
  ESP_LOGI(TAG, "Home Assistant disconnected: %s (errno %d)", reason, errno);
  this->client_->close();
  this->client_ = nullptr;
  // Nobody can be listening any more.
  this->set_monitoring_(false);

  if (this->in_call_) {
    this->in_call_ = false;
    this->speaker_->stop();
    this->out_ring_->reset();
    this->play_pending_.clear();
    this->play_sent_ = 0;
    this->call_end_trigger_.trigger("disconnected");
  }

  this->wake_detector_.stop();
  this->wake_detector_.abort_model();
  this->tx_buffer_.clear();
  this->tx_sent_ = 0;
  this->disconnected_trigger_.trigger();
}

void VoiceLabs::read_socket_() {
  // Stop pulling frames once the playback buffer is nearly full; the TCP receive
  // window then closes and backpressure propagates all the way to the app.
  while (this->client_ != nullptr && this->out_ring_->free() >= MAX_PAYLOAD_SIZE) {
    if (!this->rx_in_payload_) {
      ssize_t ret = this->client_->read(this->rx_header_ + this->rx_header_filled_,
                                        HEADER_SIZE - this->rx_header_filled_);
      if (ret == 0) {
        this->disconnect_client_("closed by peer");
        return;
      }
      if (ret < 0) {
        if (errno != EAGAIN && errno != EWOULDBLOCK) {
          this->disconnect_client_("read error");
        }
        return;
      }
      this->rx_header_filled_ += ret;
      if (this->rx_header_filled_ < HEADER_SIZE) {
        continue;
      }

      this->rx_payload_expected_ = (static_cast<size_t>(this->rx_header_[1]) << 8) | this->rx_header_[2];
      if (this->rx_payload_expected_ > MAX_PAYLOAD_SIZE) {
        ESP_LOGE(TAG, "Frame payload %zu exceeds limit", this->rx_payload_expected_);
        this->disconnect_client_("oversized frame");
        return;
      }
      this->rx_payload_.resize(this->rx_payload_expected_);
      this->rx_payload_filled_ = 0;
      this->rx_in_payload_ = true;
    }

    if (this->rx_payload_filled_ < this->rx_payload_expected_) {
      ssize_t ret = this->client_->read(this->rx_payload_.data() + this->rx_payload_filled_,
                                        this->rx_payload_expected_ - this->rx_payload_filled_);
      if (ret == 0) {
        this->disconnect_client_("closed by peer");
        return;
      }
      if (ret < 0) {
        if (errno != EAGAIN && errno != EWOULDBLOCK) {
          this->disconnect_client_("read error");
        }
        return;
      }
      this->rx_payload_filled_ += ret;
      if (this->rx_payload_filled_ < this->rx_payload_expected_) {
        continue;
      }
    }

    const auto type = static_cast<MessageType>(this->rx_header_[0]);
    this->rx_header_filled_ = 0;
    this->rx_in_payload_ = false;
    if (!this->handle_frame_(type, this->rx_payload_.data(), this->rx_payload_expected_)) {
      return;
    }
  }
}

bool VoiceLabs::handle_frame_(MessageType type, const uint8_t *payload, size_t len) {
  switch (type) {
    case MessageType::AUDIO_OUT: {
      if (this->discard_audio_out_) {
        break;  // what a cancelled call was still being sent
      }
      const size_t written = this->out_ring_->write_without_replacement(payload, len, 0);
      this->out_dropped_bytes_ += len - written;
      // New audio after a drained playback (a response after a "drained"
      // left over from before the call): drained is owed again.
      this->audio_out_done_ = false;
      this->playback_drained_sent_ = false;
      break;
    }
    case MessageType::AUDIO_OUT_END:
      this->audio_out_done_ = true;
      this->discard_audio_out_ = false;
      break;
    case MessageType::EVENT:
      this->handle_event_(payload, len);
      break;
    case MessageType::MODEL_START:
      this->handle_model_start_(payload, len);
      break;
    case MessageType::MODEL_DATA:
      if (this->staging_ok_ && !this->wake_detector_.append_model(payload, len)) {
        ESP_LOGE(TAG, "Model '%s' overran its declared size", this->staging_id_.c_str());
        this->staging_ok_ = false;
        this->wake_detector_.abort_model();
      }
      break;
    case MessageType::MODEL_END:
      this->handle_model_end_();
      break;
    case MessageType::MODEL_CLEAR:
      this->wake_detector_.stop();
      this->wake_detector_.clear_models();
      ESP_LOGI(TAG, "Wake word models cleared");
      break;
    case MessageType::MONITOR: {
      bool active = false;
      bool intercom = false;
      json::parse_json(payload, len, [&active, &intercom](JsonObject root) {
        active = root["active"] | false;
        intercom = root["intercom"] | false;
        return true;
      });
      this->set_monitoring_(active, intercom);
      break;
    }
    case MessageType::HANGUP: {
      std::string reason = "remote";
      json::parse_json(payload, len, [&reason](JsonObject root) {
        if (root["reason"].is<const char *>()) {
          reason = root["reason"].as<std::string>();
        }
        return true;
      });
      this->end_call(reason);
      return false;
    }
    default:
      ESP_LOGW(TAG, "Unexpected message type 0x%02X", static_cast<uint8_t>(type));
      break;
  }
  return true;
}

void VoiceLabs::handle_model_start_(const uint8_t *payload, size_t len) {
  ModelConfig config;
  size_t size = 0;

  const bool parsed = json::parse_json(payload, len, [&](JsonObject root) {
    config.id = root["id"] | "";
    config.wake_word = root["wake_word"] | "";
    config.is_vad = std::string(root["kind"] | "wake") == "vad";
    config.sliding_window_size = root["sliding_window_size"] | 5;
    config.feature_step_size = root["feature_step_size"] | 10;
    const float cutoff = root["probability_cutoff"] | 0.85f;
    config.probability_cutoff = static_cast<uint8_t>(clamp<int>(static_cast<int>(cutoff * 255.0f + 0.5f), 0, 255));
    size = root["size"] | 0;
    return true;
  });

  this->staging_id_ = config.id;
  this->staging_ok_ = false;
  if (!parsed || config.id.empty() || size == 0) {
    ESP_LOGE(TAG, "Malformed model header");
    this->send_model_result_(false, "malformed header");
    return;
  }
  if (config.wake_word.empty()) {
    config.wake_word = config.id;
  }

  if (!this->wake_detector_.begin_model(config, size)) {
    this->send_model_result_(false, "rejected");
    return;
  }
  this->staging_ok_ = true;
  ESP_LOGI(TAG, "Receiving %s model '%s' (%zu bytes)", config.is_vad ? "VAD" : "wake word", config.id.c_str(), size);
}

void VoiceLabs::handle_model_end_() {
  if (!this->staging_ok_) {
    this->send_model_result_(false, "no model staged");
    return;
  }
  this->staging_ok_ = false;

  std::string error;
  if (!this->wake_detector_.finish_model(&error)) {
    ESP_LOGE(TAG, "Model '%s' rejected: %s", this->staging_id_.c_str(), error.c_str());
    this->send_model_result_(false, error.c_str());
    return;
  }

  ESP_LOGI(TAG, "Model '%s' loaded (%zu bytes resident across %zu model(s))", this->staging_id_.c_str(),
           this->wake_detector_.total_arena_size(), this->wake_detector_.model_count());
  this->send_model_result_(true, "");
  this->model_loaded_trigger_.trigger(this->staging_id_);

  // A model can arrive while the detector is already listening; starting is a
  // no-op then, and the swap has already taken effect.
  this->wake_detector_.start();
}

void VoiceLabs::send_model_result_(bool ok, const char *error) {
  const std::string json_str = json::build_json([this, ok, error](JsonObject root) {
    root["id"] = this->staging_id_;
    root["ok"] = ok;
    if (!ok) {
      root["error"] = error;
    }
    root["models"] = this->wake_detector_.model_count();
  });
  this->send_json_frame_(MessageType::MODEL_RESULT, json_str);
  this->flush_tx_();
}

void VoiceLabs::poll_wake_detector_() {
  Detection detection;
  while (this->wake_detector_.take_detection(&detection)) {
    if (this->monitoring_) {
      // Someone is listening, not talking to it: say so, don't answer.
      ESP_LOGI(TAG, "Wake word detected while monitored, not answered: %s", detection.wake_word.c_str());
      this->monitored_detections_.push_back(detection.model_id);
      continue;
    }
    ESP_LOGI(TAG, "Wake word detected: %s (average %u, max %u, cutoff %u)", detection.wake_word.c_str(),
             detection.average, detection.max, detection.cutoff);
    this->start_call(detection.wake_word, &detection);
  }
}

void VoiceLabs::send_wake_report_() {
  std::vector<ModelReport> reports;
  this->wake_detector_.take_report(&reports);
  const std::string json_str = json::build_json([&](JsonObject root) {
    JsonArray models = root["models"].to<JsonArray>();
    for (const auto &report : reports) {
      JsonObject model = models.add<JsonObject>();
      model["id"] = report.id;
      if (report.is_vad) {
        model["vad"] = true;
      }
      model["cutoff"] = report.cutoff;
      model["peak"] = report.peak;
      model["window"] = report.window;
    }
    JsonArray detected = root["detected"].to<JsonArray>();
    for (const auto &id : this->monitored_detections_) {
      detected.add(id);
    }
  });
  this->monitored_detections_.clear();
  this->send_json_frame_(MessageType::WAKE_REPORT, json_str);
}

void VoiceLabs::handle_event_(const uint8_t *payload, size_t len) {
  std::string event_type;
  CallEventData event_data;

  const bool ok = json::parse_json(payload, len, [&](JsonObject root) {
    if (root["type"].is<const char *>()) {
      event_type = root["type"].as<std::string>();
    }
    JsonObject data = root["data"];
    if (!data.isNull()) {
      for (JsonPair kv : data) {
        JsonVariantConst value = kv.value();
        if (value.is<const char *>()) {
          event_data.values[kv.key().c_str()] = value.as<std::string>();
        } else if (value.is<bool>()) {
          event_data.values[kv.key().c_str()] = value.as<bool>() ? "true" : "false";
        } else {
          // Numbers stringify; nested objects and arrays keep their raw JSON.
          std::string text;
          serializeJson(value, text);
          event_data.values[kv.key().c_str()] = text;
        }
      }
    }
    return true;
  });

  if (!ok || event_type.empty()) {
    ESP_LOGW(TAG, "Dropping malformed event");
    return;
  }

  ESP_LOGD(TAG, "Event: %s (%zu fields)", event_type.c_str(), event_data.values.size());
  this->event_trigger_.trigger(event_type, event_data);
}

size_t VoiceLabs::trim_preroll_(ring_buffer::RingBuffer *ring, size_t target) {
  if (ring == nullptr) {
    return 0;
  }
  size_t buffered = ring->available();
  while (buffered > target) {
    const size_t discard = std::min(buffered - target, this->scratch_.size());
    const size_t dropped = ring->read(this->scratch_.data(), discard);
    if (dropped == 0) {
      break;
    }
    buffered -= dropped;
  }
  return buffered;
}

void VoiceLabs::start_call(const std::string &wake_word, const Detection *detection) {
  if (this->client_ == nullptr) {
    ESP_LOGW(TAG, "Cannot start call: Home Assistant not connected");
    return;
  }
  if (this->in_call_) {
    ESP_LOGD(TAG, "Call already in progress");
    return;
  }

  this->out_ring_->reset();
  this->play_pending_.clear();
  this->play_sent_ = 0;
  this->audio_out_done_ = false;
  this->playback_drained_sent_ = false;
  this->discard_audio_out_ = false;
  this->mic_dropped_bytes_ = 0;
  this->aec_dropped_bytes_ = 0;
  this->out_dropped_bytes_ = 0;

  // The rings carry headroom beyond the configured pre-roll; discard the oldest
  // audio so the call starts exactly preroll_ms back, wake word included. Both
  // captures are trimmed to the same target so they line up.
  const size_t preroll_target = this->preroll_ms_ * BYTE_RATE / 1000;
  this->preroll_bytes_ = this->trim_preroll_(this->mic_ring_.get(), preroll_target);
  this->aec_preroll_bytes_ = this->trim_preroll_(this->aec_ring_.get(), preroll_target);

  const std::string json_str = json::build_json([this, &wake_word, detection](JsonObject root) {
    root["wake_word"] = wake_word;
    if (detection != nullptr) {
      // 0-255, as in WAKE_REPORT: the window's mean met the cutoff.
      root["probability"] = detection->average;
      root["max_probability"] = detection->max;
      root["cutoff"] = detection->cutoff;
    }
    root["preroll_bytes"] = this->preroll_bytes_;
    root["preroll_ms"] = this->preroll_bytes_ * 1000 / BYTE_RATE;
    if (this->aec_ring_ != nullptr) {
      root["aec"] = true;
      root["aec_preroll_bytes"] = this->aec_preroll_bytes_;
    }
    root["sample_rate"] = SAMPLE_RATE;
    root["channels"] = 1;
    root["bits_per_sample"] = 16;
  });
  this->send_json_frame_(MessageType::CALL_START, json_str);

  this->in_call_ = true;
  this->call_started_at_ = App.get_loop_component_start_time();
  this->last_status_at_ = this->call_started_at_;
  this->speaker_->start();

  ESP_LOGI(TAG, "Call started (wake word '%s', %zu byte pre-roll)", wake_word.c_str(), this->preroll_bytes_);
  this->call_start_trigger_.trigger(wake_word);
}

void VoiceLabs::end_call(const std::string &reason) {
  if (!this->in_call_) {
    return;
  }

  this->in_call_ = false;

  const std::string json_str = json::build_json([&reason](JsonObject root) { root["reason"] = reason; });
  this->send_json_frame_(MessageType::CALL_END, json_str);
  this->flush_tx_();

  // Let whatever is already buffered finish playing rather than cutting it off.
  this->speaker_->finish();

  ESP_LOGI(TAG, "Call ended: %s (mic dropped %zu, AEC dropped %zu, output dropped %zu bytes)", reason.c_str(),
           this->mic_dropped_bytes_, this->aec_dropped_bytes_, this->out_dropped_bytes_);
  this->call_end_trigger_.trigger(reason);
}

void VoiceLabs::cancel_call() {
  if (!this->in_call_) {
    return;
  }

  this->in_call_ = false;
  this->send_json_frame_(MessageType::CALL_END, "{\"reason\":\"cancelled\"}");
  this->flush_tx_();

  // Silent at once, and nothing more of it played.
  this->speaker_->stop();
  this->out_ring_->reset();
  this->play_pending_.clear();
  this->play_sent_ = 0;
  this->discard_audio_out_ = true;

  ESP_LOGI(TAG, "Call cancelled");
  this->call_end_trigger_.trigger("cancelled");
}

size_t VoiceLabs::pump_ring_(ring_buffer::RingBuffer *ring, MessageType type, size_t budget) {
  if (ring == nullptr) {
    return 0;
  }
  size_t moved = 0;
  while (moved < budget) {
    const size_t queued = this->tx_buffer_.size() - this->tx_sent_;
    if (queued + HEADER_SIZE >= TX_BUFFER_LIMIT) {
      break;  // socket is behind; the rest stays in the ring buffer
    }
    const size_t available = ring->available();
    if (available == 0) {
      break;
    }
    const size_t chunk =
        std::min({available, MAX_PAYLOAD_SIZE, TX_BUFFER_LIMIT - queued - HEADER_SIZE, budget - moved});
    if (chunk == 0) {
      break;
    }
    const size_t read = ring->read(this->scratch_.data(), chunk);
    if (read == 0) {
      break;
    }
    this->send_frame_(type, this->scratch_.data(), read);
    moved += read;
    // Drain as we go so a large pre-roll burst is not capped by one buffer's worth.
    this->flush_tx_();
  }
  return moved;
}

void VoiceLabs::pump_microphone_() {
  // One chunk from each capture per round, swapping which goes first. Whichever
  // runs first can fill the outgoing buffer and leave the other with no room,
  // so a fixed order would let the pre-roll of one stream run ahead of the
  // other for the whole call.
  ring_buffer::RingBuffer *call = this->mic_ring_.get();
  ring_buffer::RingBuffer *other = this->aec_ring_.get();
  size_t moved = 0;
  while (moved < MIC_BYTES_PER_LOOP) {
    const size_t budget = std::min(MAX_PAYLOAD_SIZE, MIC_BYTES_PER_LOOP - moved);
    size_t first, second;
    if (this->pump_aec_first_) {
      second = this->pump_ring_(other, MessageType::AUDIO_IN_AEC, budget);
      first = this->pump_ring_(call, MessageType::AUDIO_IN, budget);
    } else {
      first = this->pump_ring_(call, MessageType::AUDIO_IN, budget);
      second = this->pump_ring_(other, MessageType::AUDIO_IN_AEC, budget);
    }
    this->pump_aec_first_ = !this->pump_aec_first_;
    if (first == 0 && second == 0) {
      break;
    }
    moved += std::max(first, second);
  }
}

void VoiceLabs::pump_speaker_() {
  // Finish the chunk the speaker only partially accepted last time.
  if (this->play_sent_ < this->play_pending_.size()) {
    this->play_sent_ += this->speaker_->play(this->play_pending_.data() + this->play_sent_,
                                             this->play_pending_.size() - this->play_sent_, 0);
    if (this->play_sent_ < this->play_pending_.size()) {
      return;
    }
    this->play_pending_.clear();
    this->play_sent_ = 0;
  }

  const size_t available = this->out_ring_->available();
  if (available == 0) {
    return;
  }

  const size_t chunk = std::min(available, PLAY_CHUNK_SIZE);
  this->play_pending_.resize(chunk);
  const size_t read = this->out_ring_->read(this->play_pending_.data(), chunk);
  this->play_pending_.resize(read);
  this->play_sent_ = this->speaker_->play(this->play_pending_.data(), read, 0);
  if (this->play_sent_ >= this->play_pending_.size()) {
    this->play_pending_.clear();
    this->play_sent_ = 0;
  }
}

void VoiceLabs::check_playback_drained_() {
  if (!this->audio_out_done_ || this->playback_drained_sent_) {
    return;
  }
  if (this->out_ring_->available() > 0 || this->play_sent_ < this->play_pending_.size()) {
    return;
  }
  if (this->speaker_->has_buffered_data()) {
    return;
  }

  this->playback_drained_sent_ = true;
  this->send_json_frame_(MessageType::PLAYBACK_DRAINED, "{}");
  ESP_LOGD(TAG, "Playback drained");
}

void VoiceLabs::set_monitoring_(bool active, bool intercom) {
  intercom = active && intercom;
  if (this->monitoring_ == active && this->intercom_ == intercom) {
    return;
  }
  if (active && !this->monitoring_) {
    this->monitor_ring_->reset();
    if (this->monitor_aec_ring_ != nullptr)
      this->monitor_aec_ring_->reset();
    this->monitored_detections_.clear();
    std::vector<ModelReport> stale;
    this->wake_detector_.take_report(&stale);
  }
  if (this->intercom_ && !intercom) {
    // What was on its way from the other satellite is not played.
    this->out_ring_->reset();
    this->play_pending_.clear();
    this->play_sent_ = 0;
  }
  this->monitoring_ = active;
  this->intercom_ = intercom;
  ESP_LOGI(TAG, "Monitoring %s", !active ? "stopped" : intercom ? "started: the intercom" : "started: the microphone is streamed");
  // The device's own word on it, not the app's: the ring shows someone is
  // listening, or the intercom, exactly while the microphone is streamed.
  CallEventData data;
  data.values["active"] = active ? "true" : "false";
  data.values["intercom"] = intercom ? "true" : "false";
  this->event_trigger_.trigger("monitoring", data);
}

void VoiceLabs::end_intercom() {
  if (!this->intercom_) {
    return;
  }
  this->send_event("intercom_end");
  this->flush_tx_();
  // Silent at once, and the microphone goes no further: the app ends it on
  // the other satellite, and drops what was still coming (until it ends the
  // stream: AUDIO_OUT_END).
  this->set_monitoring_(false);
  this->speaker_->stop();
  this->discard_audio_out_ = true;
  ESP_LOGI(TAG, "Intercom ended here");
}

void VoiceLabs::send_event(const std::string &type) {
  const std::string json_str = json::build_json([&](JsonObject root) { root["type"] = type; });
  if (!this->send_json_frame_(MessageType::DEVICE_EVENT, json_str)) {
    ESP_LOGD(TAG, "Event %s not sent (no app connected)", type.c_str());
  }
}

void VoiceLabs::send_status_() {
  const size_t free_bytes = this->out_ring_->free();
  const size_t queued_bytes = this->out_ring_->available();
  const std::string json_str = json::build_json([&](JsonObject root) {
    root["free_bytes"] = free_bytes;
    root["queued_bytes"] = queued_bytes;
    root["queued_ms"] = queued_bytes * 1000 / BYTE_RATE;
    root["speaker_busy"] = this->speaker_->has_buffered_data();
    root["mic_dropped_bytes"] = this->mic_dropped_bytes_;
    root["aec_dropped_bytes"] = this->aec_dropped_bytes_;
  });
  this->send_json_frame_(MessageType::STATUS, json_str);
}

bool VoiceLabs::send_frame_(MessageType type, const uint8_t *payload, size_t len) {
  if (this->client_ == nullptr || len > MAX_PAYLOAD_SIZE) {
    return false;
  }
  if (this->tx_buffer_.size() - this->tx_sent_ + HEADER_SIZE + len > TX_BUFFER_LIMIT) {
    ESP_LOGW(TAG, "Outgoing buffer full, dropping %zu byte frame", len);
    return false;
  }

  // Reclaim the already-sent prefix before growing the buffer.
  if (this->tx_sent_ > 0) {
    this->tx_buffer_.erase(this->tx_buffer_.begin(), this->tx_buffer_.begin() + this->tx_sent_);
    this->tx_sent_ = 0;
  }

  this->tx_buffer_.push_back(static_cast<uint8_t>(type));
  this->tx_buffer_.push_back(static_cast<uint8_t>(len >> 8));
  this->tx_buffer_.push_back(static_cast<uint8_t>(len & 0xFF));
  this->tx_buffer_.insert(this->tx_buffer_.end(), payload, payload + len);
  return true;
}

bool VoiceLabs::send_json_frame_(MessageType type, const std::string &json_str) {
  return this->send_frame_(type, reinterpret_cast<const uint8_t *>(json_str.data()), json_str.size());
}

void VoiceLabs::flush_tx_() {
  while (this->client_ != nullptr && this->tx_sent_ < this->tx_buffer_.size()) {
    ssize_t ret = this->client_->write(this->tx_buffer_.data() + this->tx_sent_,
                                       this->tx_buffer_.size() - this->tx_sent_);
    if (ret < 0) {
      if (errno != EAGAIN && errno != EWOULDBLOCK) {
        this->disconnect_client_("write error");
      }
      return;
    }
    this->tx_sent_ += ret;
  }

  if (this->tx_sent_ >= this->tx_buffer_.size()) {
    this->tx_buffer_.clear();
    this->tx_sent_ = 0;
  }
}

}  // namespace esphome::voice_labs
