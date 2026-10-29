#include "wake_detector.h"

#ifdef USE_ESP32

#include "esphome/core/application.h"
#include "esphome/core/hal.h"
#include "esphome/core/log.h"

#include <algorithm>
#include <cstdlib>
#include <cstring>

namespace esphome::voice_labs {

static const char *const TAG = "voice_labs.wake";

static constexpr uint32_t TASK_STACK_SIZE = 4096;
static constexpr UBaseType_t TASK_PRIORITY = 3;
/// Audio staged between the microphone task and the inference task.
static constexpr size_t AUDIO_RING_MS = 500;
static constexpr size_t AUDIO_CHUNK_BYTES = 1024;
static constexpr TickType_t AUDIO_TIMEOUT_TICKS = pdMS_TO_TICKS(100);
/// Largest model the device will accept, so a bad header cannot exhaust PSRAM.
static constexpr size_t MAX_MODEL_BYTES = 512 * 1024;

static constexpr uint32_t SAMPLE_RATE_HZ = 16000;

bool ModelStream::setup(const MixedNetBlob &parsed, uint8_t sliding_window_size) {
  if (!this->executor.setup(parsed.model()))
    return false;
  this->input.resize(static_cast<size_t>(parsed.input_frames()) * FEATURE_SIZE);
  this->recent.assign(sliding_window_size, 0);
  this->reset_probabilities();
  return true;
}

void ModelStream::feed(const int8_t *features, uint8_t input_frames, uint8_t cutoff, uint8_t window) {
  std::memcpy(this->input.data() + static_cast<size_t>(this->stride_step) * FEATURE_SIZE, features, FEATURE_SIZE);
  ++this->stride_step;

  if (this->stride_step >= input_frames) {
    this->stride_step = 0;
    const uint8_t probability = this->executor.invoke(this->input.data());
    ++this->last_n_index;
    if (this->last_n_index >= window)
      this->last_n_index = 0;
    this->recent[this->last_n_index] = probability;
    this->fresh = true;
  }

  // Only count down the mute window while the model is quiet, so a detection
  // cannot immediately repeat off the tail of the same utterance.
  if (this->recent[this->last_n_index] < cutoff)
    this->ignore_windows = std::min<int16_t>(this->ignore_windows + 1, 0);
}

bool ModelStream::detected(uint8_t cutoff, uint8_t window) {
  this->fresh = false;
  if (this->ignore_windows < 0)
    return false;
  uint32_t sum = 0;
  for (uint8_t probability : this->recent)
    sum += probability;
  return sum > static_cast<uint32_t>(cutoff) * window;
}

void ModelStream::reset_probabilities() {
  std::fill(this->recent.begin(), this->recent.end(), 0);
  this->ignore_windows = -static_cast<int16_t>(MIN_SLICES_BEFORE_DETECTION);
}

void ModelStream::summarize(uint8_t *average, uint8_t *max) const {
  uint32_t sum = 0;
  *max = 0;
  for (uint8_t probability : this->recent) {
    sum += probability;
    *max = std::max(*max, probability);
  }
  *average = this->recent.empty() ? 0 : static_cast<uint8_t>((sum + this->recent.size() / 2) / this->recent.size());
}

bool WakeDetector::setup() {
  if (this->mic_source_ == nullptr)
    return false;

  const size_t ring_bytes = AUDIO_RING_MS * SAMPLE_RATE_HZ * sizeof(int16_t) / 1000;
  this->main_.ring = ring_buffer::RingBuffer::create(ring_bytes);
  if (this->main_.ring == nullptr) {
    ESP_LOGE(TAG, "Failed to allocate %zu byte audio buffer", ring_bytes);
    return false;
  }
  this->main_.staging.resize(AUDIO_CHUNK_BYTES);

  this->detection_queue_ = xQueueCreate(4, sizeof(QueuedDetection));
  if (this->detection_queue_ == nullptr)
    return false;

  this->frontend_config_.window.size_ms = FEATURE_DURATION_MS;
  this->frontend_config_.window.step_size_ms = this->feature_step_size_;
  this->frontend_config_.filterbank.num_channels = FEATURE_SIZE;
  this->frontend_config_.filterbank.lower_band_limit = 125.0f;
  this->frontend_config_.filterbank.upper_band_limit = 7500.0f;
  this->frontend_config_.noise_reduction.smoothing_bits = 10;
  this->frontend_config_.noise_reduction.even_smoothing = 0.025f;
  this->frontend_config_.noise_reduction.odd_smoothing = 0.06f;
  this->frontend_config_.noise_reduction.min_signal_remaining = 0.05f;
  this->frontend_config_.pcan_gain_control.enable_pcan = true;
  this->frontend_config_.pcan_gain_control.strength = 0.95f;
  this->frontend_config_.pcan_gain_control.offset = 80.0f;
  this->frontend_config_.pcan_gain_control.gain_bits = 21;
  this->frontend_config_.log_scale.enable_log = true;
  this->frontend_config_.log_scale.scale_shift = 6;

  this->mic_source_->add_data_callback([this](const std::vector<uint8_t> &data) {
    if (this->running_ && !data.empty())
      this->main_.ring->write(data.data(), data.size());
  });
  return true;
}

bool WakeDetector::begin_model(const ModelConfig &config, size_t size) {
  if (size == 0 || size > MAX_MODEL_BYTES) {
    ESP_LOGE(TAG, "Refusing %zu byte model '%s'", size, config.id.c_str());
    return false;
  }
  if (config.feature_step_size != this->feature_step_size_) {
    ESP_LOGE(TAG, "Model '%s' wants a %u ms feature step, frontend is %u ms", config.id.c_str(),
             config.feature_step_size, this->feature_step_size_);
    return false;
  }
  if (config.sliding_window_size == 0)
    return false;

  RAMAllocator<uint8_t> allocator;
  this->staging_blob_ = allocator.make_unique_array_for_overwrite(size);
  if (this->staging_blob_ == nullptr) {
    ESP_LOGE(TAG, "No memory for a %zu byte model", size);
    return false;
  }
  this->staging_config_ = config;
  this->staging_size_ = size;
  this->staging_filled_ = 0;
  return true;
}

bool WakeDetector::append_model(const uint8_t *data, size_t len) {
  if (this->staging_blob_ == nullptr || this->staging_filled_ + len > this->staging_size_)
    return false;
  std::memcpy(this->staging_blob_.get() + this->staging_filled_, data, len);
  this->staging_filled_ += len;
  return true;
}

bool WakeDetector::finish_model(std::string *error) {
  if (this->staging_blob_ == nullptr) {
    *error = "no model staged";
    return false;
  }
  if (this->staging_filled_ != this->staging_size_) {
    *error = "short transfer";
    this->abort_model();
    return false;
  }

  auto model = make_unique<WakeModel>();
  model->config = this->staging_config_;
  model->blob = std::move(this->staging_blob_);
  model->blob_size = this->staging_size_;
  this->staging_size_ = 0;
  this->staging_filled_ = 0;

  if (!model->parsed.parse(model->blob.get(), model->blob_size)) {
    *error = model->parsed.error();
    return false;
  }
  if (model->parsed.feature_size() != FEATURE_SIZE) {
    *error = "unexpected feature size";
    return false;
  }
  if (!model->stream.setup(model->parsed, model->config.sliding_window_size)) {
    *error = "arena allocation failed";
    return false;
  }

  const std::string &id = model->config.id;
  {
    LockGuard guard(this->models_lock_);
    auto existing = std::find_if(this->models_.begin(), this->models_.end(),
                                 [&id](const std::unique_ptr<WakeModel> &m) { return m->config.id == id; });
    if (existing != this->models_.end()) {
      *existing = std::move(model);
    } else {
      if (this->models_.size() >= MAX_MODEL_SLOTS) {
        *error = "too many models";
        return false;
      }
      this->models_.push_back(std::move(model));
    }
    this->loaded_count_ = this->models_.size();
  }
  return true;
}

void WakeDetector::abort_model() {
  this->staging_blob_.reset();
  this->staging_size_ = 0;
  this->staging_filled_ = 0;
}

void WakeDetector::clear_models() {
  LockGuard guard(this->models_lock_);
  this->models_.clear();
  this->loaded_count_ = 0;
}

size_t WakeDetector::total_arena_size() const {
  size_t total = 0;
  for (const auto &model : this->models_) {
    total += model->stream.executor.arena_size() + model->blob_size;
  }
  return total;
}

bool WakeDetector::start() {
  if (this->running_)
    return true;
  if (!this->has_models()) {
    ESP_LOGD(TAG, "Not starting: no model loaded");
    return false;
  }
  if (!this->frontend_ready_) {
    if (!FrontendPopulateState(&this->frontend_config_, &this->main_.frontend, SAMPLE_RATE_HZ)) {
      ESP_LOGE(TAG, "Frontend allocation failed");
      return false;
    }
    this->frontend_ready_ = true;
  }

  this->main_.ring->reset();
  this->main_.held = 0;
  this->stop_requested_ = false;
  this->running_ = true;
  if (!this->task_.create(WakeDetector::inference_task, "vc_wake", TASK_STACK_SIZE, (void *) this, TASK_PRIORITY,
                          this->task_stack_in_psram_)) {
    ESP_LOGE(TAG, "Inference task creation failed");
    this->running_ = false;
    return false;
  }
  this->mic_source_->start();
  ESP_LOGI(TAG, "Listening with %zu model(s)", this->models_.size());
  return true;
}

void WakeDetector::stop() {
  if (!this->running_)
    return;
  this->stop_requested_ = true;
  this->mic_source_->stop();
  // The task observes stop_requested_ within one audio timeout and parks itself.
  for (uint8_t attempt = 0; attempt < 20 && this->running_; attempt++)
    delay(10);
  this->task_.destroy();
  this->running_ = false;

  LockGuard guard(this->models_lock_);
  for (auto &model : this->models_)
    model->reset_probabilities();
}

bool WakeDetector::take_detection(Detection *out) {
  QueuedDetection queued;
  if (this->detection_queue_ == nullptr || xQueueReceive(this->detection_queue_, &queued, 0) != pdTRUE)
    return false;

  LockGuard guard(this->models_lock_);
  if (queued.index >= this->models_.size())
    return false;
  const auto &config = this->models_[queued.index]->config;
  out->wake_word = config.wake_word;
  out->model_id = config.id;
  out->average = queued.average;
  out->max = queued.max;
  out->cutoff = config.probability_cutoff;
  return true;
}

void WakeDetector::take_report(std::vector<ModelReport> *out) {
  out->clear();
  LockGuard guard(this->models_lock_);
  for (auto &model : this->models_) {
    ModelReport report;
    report.id = model->config.id;
    report.is_vad = model->config.is_vad;
    report.cutoff = model->config.probability_cutoff;
    report.peak = model->report_peak;
    report.window = model->report_window;
    out->push_back(report);
    model->report_peak = 0;
    model->report_window = 0;
  }
}

void WakeDetector::inference_task(void *params) {
  auto *detector = static_cast<WakeDetector *>(params);
  detector->run_();
  detector->running_ = false;
  vTaskSuspend(nullptr);  // The main loop deletes the task once it sees running_ clear.
}

void WakeDetector::run_() {
  while (!this->stop_requested_)
    this->read_stream_(this->main_, AUDIO_TIMEOUT_TICKS);
}

void WakeDetector::read_stream_(StreamInput &input, TickType_t wait) {
  int8_t features[FEATURE_SIZE];
  const size_t read = input.ring->read(input.staging.data() + input.held, input.staging.size() - input.held, wait);
  input.held += read;

  size_t offset = 0;
  while (input.held - offset >= sizeof(int16_t)) {
    const auto *audio = reinterpret_cast<const int16_t *>(input.staging.data() + offset);
    const size_t samples_available = (input.held - offset) / sizeof(int16_t);
    size_t samples_read = 0;
    for (size_t i = 0; i < samples_available; i++)
      this->audio_peak_ = std::max(this->audio_peak_, std::abs(static_cast<int32_t>(audio[i])));
    const bool have_feature = this->generate_features_(&input.frontend, audio, samples_available, features, &samples_read);
    if (samples_read == 0)
      break;  // Cannot happen with a non-empty window, but never spin.
    offset += samples_read * sizeof(int16_t);
    if (have_feature)
      this->process_features_(features);
  }

  input.held -= offset;
  if (input.held > 0 && offset > 0)
    std::memmove(input.staging.data(), input.staging.data() + offset, input.held);
}

bool WakeDetector::generate_features_(struct FrontendState *frontend, const int16_t *audio, size_t samples_available,
                                      int8_t *features, size_t *samples_read) {
  *samples_read = 0;
  struct FrontendOutput output = FrontendProcessSamples(frontend, audio, samples_available, samples_read);
  if (output.size == 0)
    return false;

  for (size_t i = 0; i < output.size && i < FEATURE_SIZE; i++) {
    // Matches MicroWakeWord::generate_features_: the frontend emits roughly
    // 0-670, training divided that by 25.6 and then by 26.0 to reach 0.0-1.0,
    // and quantization maps that onto the int8 range.
    constexpr int32_t value_scale = 256;
    constexpr int32_t value_div = 666;  // 25.6 * 26.0, rounded
    int32_t value = ((output.values[i] * value_scale) + (value_div / 2)) / value_div;
    value += INT8_MIN;
    features[i] = static_cast<int8_t>(clamp<int32_t>(value, INT8_MIN, INT8_MAX));
  }
  return true;
}

void WakeDetector::queue_detection_(uint8_t index, const ModelStream &stream) {
  // What it came in with, before the window is cleared.
  QueuedDetection queued;
  queued.index = index;
  stream.summarize(&queued.average, &queued.max);
  ESP_LOGD(TAG, "'%s' detected (average %u, max %u, cutoff %u)", this->models_[index]->config.wake_word.c_str(),
           queued.average, queued.max, this->models_[index]->config.probability_cutoff);
  xQueueSend(this->detection_queue_, &queued, 0);
  App.wake_loop_threadsafe();
}

void WakeDetector::process_features_(const int8_t *features) {
  LockGuard guard(this->models_lock_);

  ++this->slices_seen_;
  bool voice_detected = true;
  bool have_vad = false;
  for (auto &model : this->models_) {
    model->feed(features);
    auto &stream = model->stream;
    if (model->config.is_vad) {
      have_vad = true;
      // The VAD gate is level-triggered, so read it every slice rather than
      // only on a fresh inference.
      uint32_t sum = 0;
      for (uint8_t probability : stream.recent)
        sum += probability;
      voice_detected = sum > static_cast<uint32_t>(model->config.probability_cutoff) * model->config.sliding_window_size;
      stream.fresh = false;
    }
    // Tracked across the whole second: the sliding window alone only covers the
    // last few slices, which is far too narrow to catch a wake word going by.
    uint32_t window_sum = 0;
    for (uint8_t probability : stream.recent) {
      model->peak_since_log = std::max(model->peak_since_log, probability);
      window_sum += probability;
    }
    // For whoever listens live: the latest inference, and the window's mean.
    model->report_peak = std::max(model->report_peak, stream.recent[stream.last_n_index]);
    if (!stream.recent.empty())
      model->report_window = std::max<uint8_t>(model->report_window, window_sum / stream.recent.size());
  }

  for (size_t i = 0; i < this->models_.size(); i++) {
    auto &model = this->models_[i];
    if (model->config.is_vad || !model->stream.fresh)
      continue;
    if (!model->detected())
      continue;
    if (have_vad && !voice_detected) {
      ESP_LOGD(TAG, "'%s' suppressed by VAD", model->config.wake_word.c_str());
      continue;
    }
    this->queue_detection_(static_cast<uint8_t>(i), model->stream);
    model->stream.reset_probabilities();
  }

  // Once a second, so a model that never fires can be told apart from a
  // microphone that never delivers. Verbose because it is only wanted while
  // working out why a wake word is or is not reaching its cutoff.
  if (this->slices_seen_ % 100 == 0) {
    for (auto &model : this->models_) {
      ESP_LOGV(TAG, "%s peak=%u cutoff=%u audio=%d", model->config.id.c_str(), model->peak_since_log,
               model->config.probability_cutoff, this->audio_peak_);
      model->peak_since_log = 0;
    }
    this->audio_peak_ = 0;
  }
}

}  // namespace esphome::voice_labs

#endif  // USE_ESP32
