#pragma once

#ifdef USE_ESP32

#include "mixednet.h"
#include "mixednet_blob.h"

#include "esphome/components/microphone/microphone_source.h"
#include "esphome/components/ring_buffer/ring_buffer.h"
#include "esphome/core/helpers.h"
#include "esphome/core/static_task.h"

#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>

#include <frontend.h>
#include <frontend_util.h>

#include <cstdint>
#include <memory>
#include <string>
#include <vector>

namespace esphome::voice_labs {

/// Feature frames per spectrogram slice. Every microWakeWord model is trained
/// on this width, so a model that disagrees is rejected rather than run.
static constexpr uint8_t FEATURE_SIZE = 40;
/// Window length the frontend sees; only the step between windows is tunable.
static constexpr uint8_t FEATURE_DURATION_MS = 30;

/// Slices a model must see before it may report a detection, which is also how
/// long it is muted after one. Matches micro_wake_word.
static constexpr uint8_t MIN_SLICES_BEFORE_DETECTION = 100;

/// The VAD gate, the wake words a satellite listens for, and "stop" while a
/// timer rings.
static constexpr uint8_t MAX_MODEL_SLOTS = 6;

/// What a model heard since the last report, for someone listening live.
struct ModelReport {
  std::string id;
  bool is_vad{false};
  uint8_t cutoff{0};
  uint8_t peak{0};
  uint8_t window{0};
};

/// A wake word detected, as the main loop takes it: the model, and the
/// probabilities (0-255) it came in with -- its sliding window's mean, which
/// met the cutoff, and the highest single inference in that window.
struct Detection {
  std::string wake_word;
  std::string model_id;
  uint8_t average{0};
  uint8_t max{0};
  uint8_t cutoff{0};
};

/// Detection settings that travel with a model instead of being compiled in.
struct ModelConfig {
  std::string id;
  std::string wake_word;
  bool is_vad{false};
  uint8_t probability_cutoff{217};  // 0.85 quantized to 0-255
  uint8_t sliding_window_size{5};
  uint8_t feature_step_size{10};
};

/// A model's streaming state on the audio stream: the executor's buffers, the
/// features waiting for a full input stride, and the sliding window of
/// probabilities.
struct ModelStream {
  MixedNetExecutor executor;

  /// Features accumulate here until the model has a full input stride.
  std::vector<int8_t> input;
  uint8_t stride_step{0};

  std::vector<uint8_t> recent;
  uint8_t last_n_index{0};
  int16_t ignore_windows{-static_cast<int16_t>(MIN_SLICES_BEFORE_DETECTION)};
  bool fresh{false};

  bool setup(const MixedNetBlob &parsed, uint8_t sliding_window_size);
  void feed(const int8_t *features, uint8_t input_frames, uint8_t cutoff, uint8_t window);
  bool detected(uint8_t cutoff, uint8_t window);
  void reset_probabilities();
  /// The window's mean and highest probability, as a detection reports them.
  void summarize(uint8_t *average, uint8_t *max) const;
};

/// One loaded wake word (or VAD) model and its streaming detection state.
struct WakeModel {
  ModelConfig config;
  /// The received blob. Op weights point into it, so it outlives the executors.
  RAMUniquePtr<uint8_t[]> blob;
  size_t blob_size{0};
  MixedNetBlob parsed;
  /// On the wake microphone.
  ModelStream stream;

  uint8_t peak_since_log{0};
  /// Highest since the last report (``take_report``): each inference's
  /// probability, and the window's mean, which is what meets the cutoff.
  uint8_t report_peak{0};
  uint8_t report_window{0};

  void feed(const int8_t *features) {
    this->stream.feed(features, this->parsed.input_frames(), this->config.probability_cutoff,
                      this->config.sliding_window_size);
  }
  bool detected() { return this->stream.detected(this->config.probability_cutoff, this->config.sliding_window_size); }
  void reset_probabilities() { this->stream.reset_probabilities(); }
};

/// Runs streaming wake word models over the microphone without TFLite.
///
/// Models are not compiled in: the app pushes them over the voice_labs socket,
/// so the wake word can change without reflashing. Inference runs on its own
/// task, since doing it on the microphone's task would stall the capture that
/// also feeds the call's pre-roll buffer.
class WakeDetector {
 public:
  void set_microphone_source(microphone::MicrophoneSource *source) { this->mic_source_ = source; }
  microphone::MicrophoneSource *microphone_source() const { return this->mic_source_; }
  void set_feature_step_size(uint8_t step_ms) { this->feature_step_size_ = step_ms; }
  void set_task_stack_in_psram(bool in_psram) { this->task_stack_in_psram_ = in_psram; }

  bool setup();

  /// Starts the microphone and the inference task. No-op without a loaded model.
  bool start();
  void stop();
  bool is_running() const { return this->running_; }
  bool has_models() const { return this->loaded_count_ > 0; }

  /// Staged model transfer, driven by the socket from the main loop.
  bool begin_model(const ModelConfig &config, size_t size);
  bool append_model(const uint8_t *data, size_t len);
  /// Parses the staged blob and installs it, replacing whatever held that slot.
  bool finish_model(std::string *error);
  void abort_model();
  void clear_models();

  /// Pops one detection, or returns false. Called from the main loop.
  bool take_detection(Detection *out);
  /// Every model's highest probabilities since the last call, which resets them.
  void take_report(std::vector<ModelReport> *out);

  size_t model_count() const { return this->loaded_count_; }
  size_t total_arena_size() const;

 protected:
  static void inference_task(void *params);
  void run_();
  /// One stream's audio, staged between reads, through its own frontend.
  struct StreamInput {
    std::unique_ptr<ring_buffer::RingBuffer> ring;
    struct FrontendState frontend {};
    std::vector<uint8_t> staging;
    size_t held{0};
  };
  /// Reads what the stream has (waiting up to ``wait``) and turns it into
  /// features, each handed to the models.
  void read_stream_(StreamInput &input, TickType_t wait);
  bool generate_features_(struct FrontendState *frontend, const int16_t *audio, size_t samples_available,
                          int8_t *features, size_t *samples_read);
  void process_features_(const int8_t *features);
  void queue_detection_(uint8_t index, const ModelStream &stream);

  /// What the inference task queues for the main loop: the model by slot, and
  /// its probabilities then (``Detection``).
  struct QueuedDetection {
    uint8_t index{0};
    uint8_t average{0};
    uint8_t max{0};
  };

  microphone::MicrophoneSource *mic_source_{nullptr};
  StreamInput main_;

  struct FrontendConfig frontend_config_ {};
  bool frontend_ready_{false};

  /// Guards models_ between the inference task and the main loop.
  Mutex models_lock_;
  std::vector<std::unique_ptr<WakeModel>> models_;
  size_t loaded_count_{0};

  // Staging for an in-flight transfer; only touched from the main loop.
  ModelConfig staging_config_;
  RAMUniquePtr<uint8_t[]> staging_blob_;
  size_t staging_size_{0};
  size_t staging_filled_{0};

  QueueHandle_t detection_queue_{nullptr};
  StaticTask task_;
  uint8_t feature_step_size_{10};
  bool task_stack_in_psram_{false};
  uint32_t slices_seen_{0};
  int32_t audio_peak_{0};
  volatile bool running_{false};
  volatile bool stop_requested_{false};
};

}  // namespace esphome::voice_labs

#endif  // USE_ESP32
