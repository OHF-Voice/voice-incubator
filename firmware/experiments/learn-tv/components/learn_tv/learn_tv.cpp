#include "learn_tv.h"

#include "esphome/core/hal.h"
#include "esphome/core/log.h"

#include <esp_heap_caps.h>
#include <esp_timer.h>

#include <cmath>
#include <cstring>
#include <utility>

namespace esphome::learn_tv {

static const char *const TAG = "learn_tv";

static float *alloc_floats(size_t n) {
  auto *p = static_cast<float *>(heap_caps_calloc(n, sizeof(float), MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT));
  if (p == nullptr)
    p = static_cast<float *>(heap_caps_calloc(n, sizeof(float), MALLOC_CAP_8BIT));
  return p;
}

// In-place iterative radix-2 complex FFT of length N; inverse without the 1/N.
static void fft(float *re, float *im, const float *cos_t, const float *sin_t, const uint16_t *rev, bool inverse) {
  for (int i = 0; i < N; i++) {
    int j = rev[i];
    if (j > i) {
      std::swap(re[i], re[j]);
      std::swap(im[i], im[j]);
    }
  }
  for (int len = 2; len <= N; len <<= 1) {
    int half = len >> 1, step = N / len;
    for (int i = 0; i < N; i += len) {
      for (int k = 0; k < half; k++) {
        float wr = cos_t[k * step], wi = inverse ? sin_t[k * step] : -sin_t[k * step];
        int a = i + k, b = a + half;
        float tr = re[b] * wr - im[b] * wi;
        float ti = re[b] * wi + im[b] * wr;
        re[b] = re[a] - tr;
        im[b] = im[a] - ti;
        re[a] += tr;
        im[a] += ti;
      }
    }
  }
}

void LearnTVMicrophone::setup() {
  this->in0_ = alloc_floats(N);
  this->in1_ = alloc_floats(N);
  this->ola_ = alloc_floats(N);
  this->re_ = alloc_floats(N);
  this->im_ = alloc_floats(N);
  this->win_ = alloc_floats(N);
  this->cos_ = alloc_floats(N / 2);
  this->sin_ = alloc_floats(N / 2);
  this->c_re_ = alloc_floats(BINS);
  this->c_im_ = alloc_floats(BINS);
  this->l_ = alloc_floats(BINS);
  this->y_re_ = alloc_floats(BINS);
  this->y_im_ = alloc_floats(BINS);
  this->rev_ = static_cast<uint16_t *>(heap_caps_calloc(N, sizeof(uint16_t), MALLOC_CAP_8BIT));
  if (!this->in0_ || !this->in1_ || !this->ola_ || !this->re_ || !this->im_ || !this->win_ || !this->cos_ ||
      !this->sin_ || !this->c_re_ || !this->c_im_ || !this->l_ || !this->y_re_ || !this->y_im_ || !this->rev_) {
    ESP_LOGE(TAG, "Out of memory");
    this->mark_failed();
    return;
  }
  for (int i = 0; i < N; i++)  // sqrt of a periodic Hann: analysis and synthesis sum to 1 at 50% overlap
    this->win_[i] = sqrtf(0.5f - 0.5f * cosf(2.0f * (float) M_PI * i / N));
  for (int k = 0; k < N / 2; k++) {
    this->cos_[k] = cosf(2.0f * (float) M_PI * k / N);
    this->sin_[k] = sinf(2.0f * (float) M_PI * k / N);
  }
  int bits = 0;
  while ((1 << bits) < N)
    bits++;
  for (int i = 0; i < N; i++) {
    int r = 0;
    for (int b = 0; b < bits; b++)
      r |= ((i >> b) & 1) << (bits - 1 - b);
    this->rev_[i] = r;
  }
  this->clear_();
  this->source_->add_data_callback([this](const std::vector<uint8_t> &data) { this->on_data_(data); });
}

void LearnTVMicrophone::clear_() {
  for (int k = 0; k < BINS; k++) {
    this->c_re_[k] = 0.0f;
    this->c_im_[k] = 0.0f;
    this->l_[k] = 0.0f;
  }
  this->frames_ = 0;
}

void LearnTVMicrophone::dump_config() {
  ESP_LOGCONFIG(TAG, "learn-tv microphone: N=%d, hop=%d, tau %.1f s, warm-up %.1f s, %s mode", N, HOP, this->tau_s_,
                this->warmup_s_, this->mode_ == MODE_LEARN ? "learn" : "stock");
}

void LearnTVMicrophone::start() {
  if (this->is_failed())
    return;
  if (this->state_ == microphone::STATE_STOPPED || this->state_ == microphone::STATE_STOPPING) {
    this->source_->start();
    this->state_ = microphone::STATE_STARTING;
  }
}

void LearnTVMicrophone::stop() {
  if (this->state_ == microphone::STATE_STOPPED)
    return;
  this->source_->stop();
  this->state_ = microphone::STATE_STOPPING;
}

void LearnTVMicrophone::loop() {
  if (this->state_ == microphone::STATE_STARTING && this->source_->is_running()) {
    this->state_ = microphone::STATE_RUNNING;
  } else if (this->state_ == microphone::STATE_STOPPING && this->source_->is_stopped()) {
    this->state_ = microphone::STATE_STOPPED;
  }
}

bool LearnTVMicrophone::null_direction(float *tau_ms, float *coherence) {
  if (this->mode_ != MODE_LEARN || this->frames_ * (float) HOP / 16000.0f <= this->warmup_s_)
    return false;
  // Phase slope over 300-1800 Hz, where the phase cannot wrap for these mic
  // spacings: each bin's angle(C) / (2 pi f), weighted by how consistent it is.
  float num = 0.0f, den = 0.0f, mag = 0.0f;
  int n = 0;
  for (int k = 300 * N / 16000; k <= 1800 * N / 16000; k++) {
    float cr = this->c_re_[k], ci = this->c_im_[k];
    float m = sqrtf(cr * cr + ci * ci);
    float f = k * 16000.0f / N;
    num += m * atan2f(ci, cr) / (2.0f * (float) M_PI * f);
    den += m;
    mag += m;
    n++;
  }
  if (den <= 0.0f)
    return false;
  *tau_ms = 1000.0f * num / den;
  *coherence = mag / n;
  return true;
}

void LearnTVMicrophone::take_stats(uint32_t *avg_us, uint32_t *max_us, uint32_t *frames) {
  *frames = this->stat_frames_;
  *avg_us = this->stat_frames_ ? (uint32_t) (this->stat_sum_us_ / this->stat_frames_) : 0;
  *max_us = this->stat_max_us_;
  this->stat_sum_us_ = 0;
  this->stat_max_us_ = 0;
  this->stat_frames_ = 0;
}

void LearnTVMicrophone::on_data_(const std::vector<uint8_t> &data) {
  // Source: interleaved stereo, 32-bit little-endian (the XMOS's Q31 samples).
  const size_t frames = data.size() / 8;
  const int32_t *s = reinterpret_cast<const int32_t *>(data.data());
  this->out_.clear();
  for (size_t i = 0; i < frames; i++) {
    int at = N - HOP + this->fill_;
    this->in0_[at] = s[2 * i] * (1.0f / 2147483648.0f);
    this->in1_[at] = s[2 * i + 1] * (1.0f / 2147483648.0f);
    if (++this->fill_ == HOP) {
      this->process_frame_();
      this->fill_ = 0;
    }
  }
  if (!this->out_.empty())
    this->data_callbacks_.call(this->out_);
}

static inline int32_t to_q31(float v) {
  float x = v * 2147483648.0f;
  if (x >= 2147483647.0f)
    return INT32_MAX;
  if (x <= -2147483648.0f)
    return INT32_MIN;
  return (int32_t) x;
}

void LearnTVMicrophone::emit_(const float *wake, const float *call, const float *second, const float *fallback) {
  size_t base = this->out_.size();
  this->out_.resize(base + HOP * CHANNELS * sizeof(int32_t));
  int32_t *o = reinterpret_cast<int32_t *>(this->out_.data() + base);
  for (int i = 0; i < HOP; i++, o += CHANNELS) {
    o[0] = to_q31(wake[i]);
    o[1] = to_q31(call[i]);
    o[2] = to_q31(second[i]);
    o[3] = fallback != nullptr ? to_q31(fallback[i]) : 0;
  }
}

void LearnTVMicrophone::shift_() {
  memmove(this->in0_, this->in0_ + HOP, (N - HOP) * sizeof(float));
  memmove(this->in1_, this->in1_ + HOP, (N - HOP) * sizeof(float));
}

void LearnTVMicrophone::process_frame_() {
  const int64_t t0 = esp_timer_get_time();
  if (this->reset_requested_.exchange(false))
    this->clear_();  // here, on the microphone's task, so a frame never sees half of it
  if (this->mode_ != MODE_LEARN) {
    // Stock: the XMOS's AGC (in0) and NS (in1) taps, routed, delayed as the
    // null's output is. The overlap-add's tail from learn-tv mode goes.
    this->emit_(this->in1_, this->in0_, this->in1_, nullptr);
    memset(this->ola_, 0, N * sizeof(float));
    this->shift_();
    return;
  }
  float *re = this->re_, *im = this->im_;

  // Both mics in one complex FFT: z = x0 + j x1.
  for (int i = 0; i < N; i++) {
    re[i] = this->in0_[i] * this->win_[i];
    im[i] = this->in1_[i] * this->win_[i];
  }
  fft(re, im, this->cos_, this->sin_, this->rev_, false);

  const float a = (float) HOP / 16000.0f / this->tau_s_;
  const bool learning = !this->frozen_;
  const bool nulling = this->frames_ * (float) HOP / 16000.0f > this->warmup_s_;
  // Y goes to its own buffers: re/im still hold Z[N-k] for the later bins.
  float *y_re = this->y_re_, *y_im = this->y_im_;
  for (int k = 0; k <= N / 2; k++) {
    int nk = (N - k) % N;
    float ar = re[k], ai = im[k], cr = re[nk], ci = im[nk];
    float x0r = 0.5f * (ar + cr), x0i = 0.5f * (ai - ci);   // (Z[k] + conj Z[N-k]) / 2
    float x1r = 0.5f * (ai + ci), x1i = -0.5f * (ar - cr);  // (Z[k] - conj Z[N-k]) / 2j
    if (learning) {
      float cr2 = x0r * x1r + x0i * x1i;  // x0 conj(x1)
      float ci2 = x0i * x1r - x0r * x1i;
      float mag = sqrtf(cr2 * cr2 + ci2 * ci2) + 1e-30f;
      this->c_re_[k] = (1.0f - a) * this->c_re_[k] + a * cr2 / mag;
      this->c_im_[k] = (1.0f - a) * this->c_im_[k] + a * ci2 / mag;
      float m0 = sqrtf(x0r * x0r + x0i * x0i) + 1e-30f, m1 = sqrtf(x1r * x1r + x1i * x1i) + 1e-30f;
      this->l_[k] = (1.0f - a) * this->l_[k] + a * logf(m0 / m1);
    }
    if (nulling) {
      float cm = sqrtf(this->c_re_[k] * this->c_re_[k] + this->c_im_[k] * this->c_im_[k]) + 1e-30f;
      float g = expf(this->l_[k]) / cm;
      float hr = g * this->c_re_[k], hi = g * this->c_im_[k];
      y_re[k] = x0r - (hr * x1r - hi * x1i);
      y_im[k] = x0i - (hr * x1i + hi * x1r);
    } else {
      y_re[k] = x0r;
      y_im[k] = x0i;
    }
  }
  if (learning)
    this->frames_++;

  // Inverse FFT of the Hermitian spectrum.
  for (int k = 0; k <= N / 2; k++) {
    re[k] = y_re[k];
    im[k] = (k == 0 || k == N / 2) ? 0.0f : y_im[k];
  }
  for (int k = 1; k < N / 2; k++) {
    re[N - k] = y_re[k];
    im[N - k] = -y_im[k];
  }
  fft(re, im, this->cos_, this->sin_, this->rev_, true);
  for (int i = 0; i < N; i++)
    this->ola_[i] += re[i] * (1.0f / N) * this->win_[i];

  // Emit HOP frames, all from the start of this frame, which is what the
  // overlap-add has just completed: the null for the wake words and the
  // call, mic 0 for the second capture and the fallback detector.
  this->emit_(this->ola_, this->ola_, this->in0_, this->in0_);
  memmove(this->ola_, this->ola_ + HOP, (N - HOP) * sizeof(float));
  memset(this->ola_ + (N - HOP), 0, HOP * sizeof(float));
  this->shift_();

  uint32_t dt = (uint32_t) (esp_timer_get_time() - t0);
  this->stat_sum_us_ += dt;
  this->stat_frames_++;
  if (dt > this->stat_max_us_)
    this->stat_max_us_ = dt;
}

}  // namespace esphome::learn_tv
