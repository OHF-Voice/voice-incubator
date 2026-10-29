#pragma once

// learn-tv: a two-mic null on the source that plays most of the time.
//
// Wraps a stereo microphone (the Voice PE's XMOS output) and emits four
// channels, one for each of voice_labs's uses, all delayed alike (16 ms):
//
//   channel   learn mode (XMOS: mic 0, mic 1 after AEC)   stock mode (XMOS: AGC, NS)
//   0 wake    mic 0 with the null                         NS
//   1 call    mic 0 with the null                         AGC
//   2 second  mic 0                                       NS
//   3 fallback mic 0                                      silence
//
// The null: per 512-sample frame (sqrt-Hann, 50% overlap) it averages, over
// ~tau, the inter-mic phase of every frequency bin with each frame counted
// once however loud it is, and the log level ratio; after a warm-up the output
// is X0 - h X1 with h built from them. The same algorithm as learn_tv() in
// mic_tap's okay-nabu-tv/beamform.py, which scored it offline. In stock mode
// nothing is computed; the XMOS's taps are only routed. While frozen (a call:
// the talker is not the TV), it nulls with what it learned but learns nothing
// more.

#include "esphome/components/microphone/microphone.h"
#include "esphome/core/component.h"

#include <atomic>
#include <cstdint>
#include <vector>

namespace esphome::learn_tv {

static const int N = 512;
static const int HOP = N / 2;
static const int BINS = N / 2 + 1;
/// Output channels: wake, call, second capture, fallback.
static const int CHANNELS = 4;

enum Mode : uint8_t {
  /// The XMOS's stock taps (channel 0 AGC, channel 1 NS), routed only.
  MODE_STOCK = 0,
  /// Both mics after AEC, nulled.
  MODE_LEARN = 1,
};

class LearnTVMicrophone : public microphone::Microphone, public Component {
 public:
  void setup() override;
  void loop() override;
  void dump_config() override;
  float get_setup_priority() const override { return setup_priority::LATE; }

  void start() override;
  void stop() override;

  void set_source(microphone::Microphone *source) { this->source_ = source; }
  void set_tau_ms(uint32_t tau_ms) { this->tau_s_ = tau_ms / 1000.0f; }
  void set_warmup_ms(uint32_t warmup_ms) { this->warmup_s_ = warmup_ms / 1000.0f; }
  void set_mode(uint8_t mode) { this->mode_ = mode; }
  uint8_t get_mode() const { return this->mode_; }
  void set_stream(uint8_t bits, uint8_t channels, uint32_t rate) {
    this->audio_stream_info_ = audio::AudioStreamInfo(bits, channels, rate);
  }

  /// Forget what was learned: the null starts again after the warm-up.
  void reset() { this->reset_requested_ = true; }
  /// Keep nulling with what was learned, but learn nothing more (during a call).
  void set_frozen(bool frozen) { this->frozen_ = frozen; }
  bool is_frozen() const { return this->frozen_; }
  /// Processing time per 16 ms frame, microseconds, since the last call.
  void take_stats(uint32_t *avg_us, uint32_t *max_us, uint32_t *frames);
  /// Where the null points: the learned inter-mic delay (ms; the two ends of
  /// the mic axis are about +-0.25 ms on a VPE) and how consistent the learned
  /// phase is (0..1). False before the warm-up ends, or in stock mode.
  bool null_direction(float *tau_ms, float *coherence);

 protected:
  void on_data_(const std::vector<uint8_t> &data);
  void process_frame_();
  void clear_();
  /// One hop of the four channels (``fallback`` null: silence).
  void emit_(const float *wake, const float *call, const float *second, const float *fallback);
  void shift_();

  microphone::Microphone *source_{nullptr};
  float tau_s_{20.0f};
  float warmup_s_{2.0f};
  volatile uint8_t mode_{MODE_LEARN};
  std::atomic<bool> frozen_{false};
  std::atomic<bool> reset_requested_{false};

  // Framing state.
  float *in0_{nullptr}, *in1_{nullptr};  // last N input samples per mic
  float *ola_{nullptr};                    // overlap-add of the output
  int fill_{0};                            // new samples in the current hop
  uint32_t frames_{0};                     // frames learned from

  // FFT work and tables.
  float *re_{nullptr}, *im_{nullptr};
  float *win_{nullptr}, *cos_{nullptr}, *sin_{nullptr};
  uint16_t *rev_{nullptr};

  // Learned per bin: the average unit cross-spectrum and log level ratio.
  float *c_re_{nullptr}, *c_im_{nullptr}, *l_{nullptr};
  float *y_re_{nullptr}, *y_im_{nullptr};  // the output spectrum, bins 0..N/2

  std::vector<uint8_t> out_;

  uint64_t stat_sum_us_{0};
  uint32_t stat_max_us_{0}, stat_frames_{0};
};

}  // namespace esphome::learn_tv
