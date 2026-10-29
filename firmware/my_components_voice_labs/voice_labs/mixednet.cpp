// Vendored from https://github.com/synesthesiam/mixednet-executor (namespace changed).
// Edit there, not here.
#include "mixednet.h"

#include <algorithm>
#include <cstring>
#include <climits>

namespace esphome::voice_labs {

namespace {

// TFLite has two requantization roundings, chosen at build time by
// TFLITE_SINGLE_ROUNDING. ESPHome does not define it, so esp-tflite-micro and
// esp-nn both use the older double-rounding (gemmlowp) path, which is the
// default here. Define MIXEDNET_SINGLE_ROUNDING to match a desktop TFLite build.
#ifdef MIXEDNET_SINGLE_ROUNDING

int32_t multiply_by_quantized_multiplier(int32_t x, int32_t multiplier, int32_t shift) {
  const int64_t total_shift = 31 - shift;
  const int64_t round = int64_t{1} << (total_shift - 1);
  return static_cast<int32_t>((static_cast<int64_t>(x) * multiplier + round) >> total_shift);
}

#else

// Port of gemmlowp's SaturatingRoundingDoublingHighMul, matching esp_nn_sat_round_doubling_high_mul.
int32_t saturating_rounding_doubling_high_mul(int32_t a, int32_t b) {
  const bool overflow = (a == b) && (a == INT32_MIN);
  const int64_t ab = static_cast<int64_t>(a) * static_cast<int64_t>(b);
  const int32_t nudge = ab >= 0 ? (1 << 30) : (1 - (1 << 30));
  const int32_t result = static_cast<int32_t>((ab + nudge) / (int64_t{1} << 31));
  return overflow ? INT32_MAX : result;
}

int32_t rounding_divide_by_pot(int32_t x, int32_t exponent) {
  if (exponent == 0)
    return x;
  const int32_t mask = (int32_t{1} << exponent) - 1;
  const int32_t remainder = x & mask;
  const int32_t threshold = (mask >> 1) + (x < 0 ? 1 : 0);
  return (x >> exponent) + (remainder > threshold ? 1 : 0);
}

int32_t multiply_by_quantized_multiplier(int32_t x, int32_t multiplier, int32_t shift) {
  const int32_t left_shift = shift > 0 ? shift : 0;
  const int32_t right_shift = shift > 0 ? 0 : -shift;
  return rounding_divide_by_pot(
      saturating_rounding_doubling_high_mul(x * (int32_t{1} << left_shift), multiplier), right_shift);
}

#endif

}  // namespace

bool MixedNetExecutor::setup(const MixedNetModel *model) {
  this->model_ = model;
  this->offsets_ = std::make_unique<uint16_t[]>(model->num_buffers);
  size_t offset = 0;
  for (uint8_t i = 0; i < model->num_buffers; i++) {
    const MixedNetBuffer &b = model->buffers[i];
    this->offsets_[i] = static_cast<uint16_t>(offset);
    offset += static_cast<size_t>(b.frames) * b.channels;
  }
  this->arena_size_ = offset;
  this->arena_ = std::make_unique<int8_t[]>(offset);
  if (this->arena_ == nullptr)
    return false;
  this->reset();
  return true;
}

void MixedNetExecutor::reset() {
  for (uint8_t i = 0; i < this->model_->num_buffers; i++) {
    const MixedNetBuffer &b = this->model_->buffers[i];
    std::memset(this->buffer_(i), static_cast<int>(b.init), static_cast<size_t>(b.frames) * b.channels);
  }
}

void MixedNetExecutor::ring_push_(const MixedNetOp &op, const int8_t *features) {
  const MixedNetBuffer &dst_buf = this->model_->buffers[op.dst_buf];
  int8_t *dst = this->buffer_(op.dst_buf) + static_cast<size_t>(dst_buf.keep) * dst_buf.channels;
  if (op.src_buf == MIXEDNET_SRC_EXTERNAL) {
    std::memcpy(dst, features, static_cast<size_t>(op.src_nt) * op.src_nc);
    return;
  }
  const int8_t *src = this->buffer_(op.src_buf) + static_cast<size_t>(op.src_t0) * op.src_stride + op.src_c0;
  for (uint16_t t = 0; t < op.src_nt; t++) {
    std::memcpy(dst + static_cast<size_t>(t) * dst_buf.channels, src + static_cast<size_t>(t) * op.src_stride,
                op.src_nc);
  }
}

void MixedNetExecutor::copy_(const MixedNetOp &op) {
  const int8_t *src = this->buffer_(op.src_buf) + static_cast<size_t>(op.src_t0) * op.src_stride + op.src_c0;
  int8_t *dst = this->buffer_(op.dst_buf) + op.dst_off;
  std::memcpy(dst, src, op.src_nc);
}

void MixedNetExecutor::dense_(const MixedNetOp &op) {
  const int8_t *src = this->buffer_(op.src_buf) + static_cast<size_t>(op.src_t0) * op.src_stride;
  const uint32_t row = static_cast<uint32_t>(op.src_nt) * op.src_nc;
  int8_t *dst = this->buffer_(op.dst_buf) + op.dst_off;
  for (uint16_t oc = 0; oc < op.out_channels; oc++) {
    const int8_t *w = op.weights + static_cast<size_t>(oc) * row;
    int32_t acc = op.bias[oc];
    for (uint32_t i = 0; i < row; i++) {
      acc += static_cast<int32_t>(w[i]) * (static_cast<int32_t>(src[i]) + op.in_offset);
    }
    acc = multiply_by_quantized_multiplier(acc, op.mult[oc], op.shift[oc]) + op.out_offset;
    dst[oc] = static_cast<int8_t>(std::min(std::max(acc, op.act_min), op.act_max));
  }
}

void MixedNetExecutor::depthwise_(const MixedNetOp &op) {
  const int8_t *src = this->buffer_(op.src_buf) + static_cast<size_t>(op.src_t0) * op.src_stride + op.src_c0;
  int8_t *dst = this->buffer_(op.dst_buf) + op.dst_off;
  for (uint16_t c = 0; c < op.out_channels; c++) {
    int32_t acc = op.bias[c];
    for (uint16_t k = 0; k < op.src_nt; k++) {
      acc += static_cast<int32_t>(op.weights[static_cast<size_t>(k) * op.src_nc + c]) *
             (static_cast<int32_t>(src[static_cast<size_t>(k) * op.src_stride + c]) + op.in_offset);
    }
    acc = multiply_by_quantized_multiplier(acc, op.mult[c], op.shift[c]) + op.out_offset;
    dst[c] = static_cast<int8_t>(std::min(std::max(acc, op.act_min), op.act_max));
  }
}

// Max pooling keeps the input's quantization, so no requantization: the int8
// maximum of K frames, channel by channel.
void MixedNetExecutor::max_pool_(const MixedNetOp &op) {
  const int8_t *src = this->buffer_(op.src_buf) + static_cast<size_t>(op.src_t0) * op.src_stride + op.src_c0;
  int8_t *dst = this->buffer_(op.dst_buf) + op.dst_off;
  for (uint16_t c = 0; c < op.src_nc; c++) {
    int32_t best = src[c];
    for (uint16_t k = 1; k < op.src_nt; k++) {
      best = std::max(best, static_cast<int32_t>(src[static_cast<size_t>(k) * op.src_stride + c]));
    }
    dst[c] = static_cast<int8_t>(std::min(std::max(best, op.act_min), op.act_max));
  }
}

uint8_t MixedNetExecutor::invoke(const int8_t *features) {
  const MixedNetModel *m = this->model_;
  for (uint8_t i = 0; i < m->num_ops; i++) {
    const MixedNetOp &op = m->ops[i];
    switch (op.type) {
      case MIXEDNET_OP_RING_PUSH:
        this->ring_push_(op, features);
        break;
      case MIXEDNET_OP_DENSE:
        this->dense_(op);
        break;
      case MIXEDNET_OP_DEPTHWISE:
        this->depthwise_(op);
        break;
      case MIXEDNET_OP_COPY:
        this->copy_(op);
        break;
      case MIXEDNET_OP_MAX_POOL:
        this->max_pool_(op);
        break;
      default:
        break;
    }
  }

  this->last_logit_ = *this->buffer_(m->logit_buffer);
  const uint8_t probability = m->sigmoid_lut[static_cast<uint8_t>(this->last_logit_)];

  // Advance every ring window now that all reads of this invoke's data are done.
  for (uint8_t i = 0; i < m->num_shifts; i++) {
    const uint8_t index = m->shift_buffers[i];
    const MixedNetBuffer &b = m->buffers[index];
    int8_t *base = this->buffer_(index);
    std::memmove(base, base + static_cast<size_t>(b.push) * b.channels,
                 static_cast<size_t>(b.keep) * b.channels);
  }
  return probability;
}

}  // namespace esphome::voice_labs
