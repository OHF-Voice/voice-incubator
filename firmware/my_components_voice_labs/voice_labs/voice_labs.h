#pragma once

#include "esphome/core/automation.h"
#include "esphome/core/component.h"
#include "esphome/core/helpers.h"

#include "wake_detector.h"

#include "esphome/components/microphone/microphone_source.h"
#include "esphome/components/ring_buffer/ring_buffer.h"
#include "esphome/components/socket/socket.h"
#include "esphome/components/speaker/speaker.h"

#include <atomic>
#include <cstdint>
#include <map>
#include <memory>
#include <string>
#include <vector>

namespace esphome::voice_labs {

static constexpr uint32_t SAMPLE_RATE = 16000;
static constexpr uint8_t BYTES_PER_SAMPLE = 2;
static constexpr uint32_t BYTE_RATE = SAMPLE_RATE * BYTES_PER_SAMPLE;

/// Wire frame: uint8 type | uint16 big-endian length | payload.
static constexpr size_t HEADER_SIZE = 3;
static constexpr size_t MAX_PAYLOAD_SIZE = 4096;

enum class MessageType : uint8_t {
  // Device to Home Assistant
  HELLO = 0x01,
  CALL_START = 0x02,
  AUDIO_IN = 0x03,
  CALL_END = 0x04,
  STATUS = 0x05,
  PLAYBACK_DRAINED = 0x06,
  MODEL_RESULT = 0x07,
  AUDIO_IN_AEC = 0x08,
  DEVICE_EVENT = 0x09,
  /// The microphone outside a call, while the app monitors it.
  AUDIO_MONITOR = 0x0A,
  /// While monitored, about ten times a second: each model's highest
  /// probabilities since the last, and wake words detected (not answered).
  WAKE_REPORT = 0x0B,
  /// The second capture outside a call, while monitored (what speakers are
  /// enrolled on).
  AUDIO_MONITOR_AEC = 0x0C,

  // Home Assistant to device
  AUDIO_OUT = 0x10,
  AUDIO_OUT_END = 0x11,
  EVENT = 0x12,
  HANGUP = 0x13,
  /// {"active": true | false, "intercom": true | false}: stream the
  /// microphone outside calls (someone is listening live, or another
  /// satellite is joined by the intercom: the first capture alone), or stop.
  MONITOR = 0x14,
  MODEL_START = 0x20,
  MODEL_DATA = 0x21,
  MODEL_END = 0x22,
  MODEL_CLEAR = 0x23,
};

/// Flattened top-level fields of an EVENT payload. Nested objects and arrays
/// are kept as their raw JSON text so a lambda can still get at them.
struct CallEventData {
  std::map<std::string, std::string> values;

  bool has(const std::string &key) const { return this->values.count(key) > 0; }
  std::string get(const std::string &key, const std::string &default_value = "") const {
    auto it = this->values.find(key);
    return it == this->values.end() ? default_value : it->second;
  }
  int get_int(const std::string &key, int default_value = 0) const;
  float get_float(const std::string &key, float default_value = 0.0f) const;
  bool get_bool(const std::string &key, bool default_value = false) const;
};

class VoiceLabs : public Component {
 public:
  void setup() override;
  void loop() override;
  void dump_config() override;
  float get_setup_priority() const override { return setup_priority::AFTER_CONNECTION; }

  void set_microphone_source(microphone::MicrophoneSource *mic_source) { this->mic_source_ = mic_source; }
  void set_wake_microphone_source(microphone::MicrophoneSource *mic_source) {
    this->wake_detector_.set_microphone_source(mic_source);
  }
  /// Second capture of the same call, from an earlier point in the XMOS
  /// pipeline. Recorded only; the command is still split from the first.
  void set_aec_microphone_source(microphone::MicrophoneSource *mic_source) { this->aec_source_ = mic_source; }
  /// How many samples the second capture runs ahead of the first, told to the
  /// app (HELLO): the XMOS's AEC tap leads its AGC tap. Negative: not said (the
  /// app assumes the XMOS's).
  void set_aec_lead_samples(int32_t samples) { this->aec_lead_samples_ = samples; }
  void set_feature_step_size(uint8_t step_ms) { this->wake_detector_.set_feature_step_size(step_ms); }
  void set_wake_task_stack_in_psram(bool in_psram) { this->wake_detector_.set_task_stack_in_psram(in_psram); }
  void set_speaker(speaker::Speaker *spk) { this->speaker_ = spk; }
  void set_port(uint16_t port) { this->port_ = port; }
  void set_preroll_ms(uint32_t ms) { this->preroll_ms_ = ms; }
  void set_output_buffer_ms(uint32_t ms) { this->output_buffer_ms_ = ms; }
  void set_call_timeout_ms(uint32_t ms) { this->call_timeout_ms_ = ms; }
  void set_status_interval_ms(uint32_t ms) { this->status_interval_ms_ = ms; }
  void set_volume(float volume) { this->volume_ = volume; }

  /// Begin a call. No-op unless Home Assistant is connected and no call is active.
  /// ``detection``: what the wake word came in with, told to the app.
  void start_call(const std::string &wake_word, const Detection *detection = nullptr);
  void end_call(const std::string &reason);
  /// End the call now (the button), cutting off what it is playing: the app
  /// is told ``cancelled``, and audio it had already sent is not played,
  /// until it ends that stream (AUDIO_OUT_END) or a call starts.
  void cancel_call();
  /// Tell the app something happened here (``{"type": ...}``), e.g. that an
  /// announcement finished playing.
  void send_event(const std::string &type);

  bool is_in_call() const { return this->in_call_; }
  /// The app is streaming the microphone outside calls: someone is
  /// listening. The ring says so (an event, "monitoring", on every change).
  bool is_monitoring() const { return this->monitoring_.load(); }
  /// The streaming is the intercom: another satellite hears this one, and
  /// this one plays what that one hears.
  bool is_intercom() const { return this->intercom_.load(); }
  /// End the intercom (the button): stream no more and play no more of the
  /// other satellite at once, and tell the app (``intercom_end``), which ends
  /// it on that one too.
  void end_intercom();
  /// True once the app has pushed at least one wake word model.
  bool has_wake_models() const { return this->wake_detector_.has_models(); }
  bool is_connected() const { return this->client_ != nullptr; }

  Trigger<std::string> *get_call_start_trigger() { return &this->call_start_trigger_; }
  Trigger<std::string> *get_call_end_trigger() { return &this->call_end_trigger_; }
  Trigger<std::string, CallEventData> *get_event_trigger() { return &this->event_trigger_; }
  Trigger<> *get_connected_trigger() { return &this->connected_trigger_; }
  Trigger<> *get_disconnected_trigger() { return &this->disconnected_trigger_; }
  Trigger<std::string> *get_model_loaded_trigger() { return &this->model_loaded_trigger_; }

 protected:
  void accept_client_();
  void disconnect_client_(const char *reason);

  void read_socket_();
  bool handle_frame_(MessageType type, const uint8_t *payload, size_t len);
  void handle_event_(const uint8_t *payload, size_t len);

  void handle_model_start_(const uint8_t *payload, size_t len);
  void handle_model_end_();
  void send_model_result_(bool ok, const char *error);
  void poll_wake_detector_();

  void pump_microphone_();
  /// Moves one ring into one message type. Returns the bytes sent.
  size_t pump_ring_(ring_buffer::RingBuffer *ring, MessageType type, size_t budget);
  /// Drops the oldest audio so the ring holds exactly `target` bytes.
  size_t trim_preroll_(ring_buffer::RingBuffer *ring, size_t target);
  void pump_speaker_();
  void send_status_();
  void check_playback_drained_();
  void set_monitoring_(bool active, bool intercom = false);
  void send_wake_report_();

  /// Queue a frame for transmission. Returns false if the outgoing buffer is full.
  bool send_frame_(MessageType type, const uint8_t *payload, size_t len);
  bool send_json_frame_(MessageType type, const std::string &json);
  void flush_tx_();

  microphone::MicrophoneSource *mic_source_{nullptr};
  microphone::MicrophoneSource *aec_source_{nullptr};
  speaker::Speaker *speaker_{nullptr};
  WakeDetector wake_detector_;

  // Identifies the transfer in flight so MODEL_RESULT can name it.
  std::string staging_id_;
  bool staging_ok_{false};

  std::unique_ptr<socket::ListenSocket> listen_socket_;
  std::unique_ptr<socket::Socket> client_;

  // Microphone task writes, loop() drains.
  std::unique_ptr<ring_buffer::RingBuffer> mic_ring_;
  std::unique_ptr<ring_buffer::RingBuffer> aec_ring_;
  // While monitoring, the microphone task also writes here outside calls.
  std::unique_ptr<ring_buffer::RingBuffer> monitor_ring_;
  std::unique_ptr<ring_buffer::RingBuffer> monitor_aec_ring_;
  /// Which monitor ring goes first this loop (they alternate, as a call's do).
  bool pump_monitor_aec_first_{false};
  // loop() writes from the socket, loop() drains into the speaker.
  std::unique_ptr<ring_buffer::RingBuffer> out_ring_;

  // Incremental frame reader.
  uint8_t rx_header_[HEADER_SIZE]{};
  std::vector<uint8_t> rx_payload_;
  size_t rx_header_filled_{0};
  size_t rx_payload_filled_{0};
  size_t rx_payload_expected_{0};
  bool rx_in_payload_{false};

  // Outgoing byte queue; tx_sent_ marks how much of the front has gone out.
  // In PSRAM, its whole limit reserved at setup: growing it in a call's
  // pre-roll burst needs a contiguous block internal RAM may no longer have.
  std::vector<uint8_t, RAMAllocator<uint8_t>> tx_buffer_;
  size_t tx_sent_{0};

  std::vector<uint8_t> scratch_;

  // Chunk pulled from out_ring_ that the speaker has not fully accepted yet.
  std::vector<uint8_t> play_pending_;
  size_t play_sent_{0};

  uint32_t preroll_ms_{1500};
  uint32_t output_buffer_ms_{1000};
  uint32_t call_timeout_ms_{30000};
  uint32_t status_interval_ms_{200};
  uint32_t call_started_at_{0};
  uint32_t last_status_at_{0};
  uint32_t last_refusal_log_{0};

  size_t preroll_bytes_{0};
  size_t aec_preroll_bytes_{0};
  int32_t aec_lead_samples_{-1};
  size_t mic_dropped_bytes_{0};
  size_t aec_dropped_bytes_{0};
  size_t out_dropped_bytes_{0};

  float volume_{0.7f};
  uint16_t port_{6055};
  bool in_call_{false};
  std::atomic<bool> monitoring_{false};
  std::atomic<bool> intercom_{false};
  uint32_t last_wake_report_at_{0};
  /// Wake words detected while monitored, for the next report: they are not
  /// answered, since someone is only listening.
  std::vector<std::string> monitored_detections_;
  bool audio_out_done_{false};
  /// A call was cancelled: AUDIO_OUT still arriving from it is dropped.
  bool discard_audio_out_{false};
  bool pump_aec_first_{false};
  bool playback_drained_sent_{false};

  Trigger<std::string> call_start_trigger_;
  Trigger<std::string> call_end_trigger_;
  Trigger<std::string, CallEventData> event_trigger_;
  Trigger<> connected_trigger_;
  Trigger<> disconnected_trigger_;
  Trigger<std::string> model_loaded_trigger_;
};

template<typename... Ts> class StartCallAction : public Action<Ts...>, public Parented<VoiceLabs> {
 public:
  TEMPLATABLE_VALUE(std::string, wake_word)
  void play(const Ts &...x) override { this->parent_->start_call(this->wake_word_.value(x...)); }
};

template<typename... Ts> class EndCallAction : public Action<Ts...>, public Parented<VoiceLabs> {
 public:
  TEMPLATABLE_VALUE(std::string, reason)
  void play(const Ts &...x) override { this->parent_->end_call(this->reason_.value(x...)); }
};

template<typename... Ts> class CancelCallAction : public Action<Ts...>, public Parented<VoiceLabs> {
 public:
  void play(const Ts &...x) override { this->parent_->cancel_call(); }
};

template<typename... Ts> class EndIntercomAction : public Action<Ts...>, public Parented<VoiceLabs> {
 public:
  void play(const Ts &...x) override { this->parent_->end_intercom(); }
};

template<typename... Ts> class SendEventAction : public Action<Ts...>, public Parented<VoiceLabs> {
 public:
  TEMPLATABLE_VALUE(std::string, type)
  void play(const Ts &...x) override { this->parent_->send_event(this->type_.value(x...)); }
};

template<typename... Ts> class IsInCallCondition : public Condition<Ts...>, public Parented<VoiceLabs> {
 public:
  bool check(const Ts &...x) override { return this->parent_->is_in_call(); }
};

template<typename... Ts> class IsIntercomCondition : public Condition<Ts...>, public Parented<VoiceLabs> {
 public:
  bool check(const Ts &...x) override { return this->parent_->is_intercom(); }
};

template<typename... Ts> class IsConnectedCondition : public Condition<Ts...>, public Parented<VoiceLabs> {
 public:
  bool check(const Ts &...x) override { return this->parent_->is_connected(); }
};

}  // namespace esphome::voice_labs
