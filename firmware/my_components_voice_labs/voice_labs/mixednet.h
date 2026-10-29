// Vendored from https://github.com/synesthesiam/mixednet-executor (namespace changed).
// Edit there, not here.
#pragma once

#include <cstddef>
#include <cstdint>
#include <memory>

namespace esphome::voice_labs {

enum MixedNetOpType : uint8_t {
  MIXEDNET_OP_RING_PUSH = 0,  // append frames to a ring window
  MIXEDNET_OP_DENSE = 1,      // per-channel dot product over a contiguous window
  MIXEDNET_OP_DEPTHWISE = 2,  // per-channel dot product over K taps
  MIXEDNET_OP_COPY = 3,       // channel-axis concatenation
  MIXEDNET_OP_MAX_POOL = 4,   // per-channel maximum over K frames
};

// Sentinel source buffer meaning "the feature frames passed to invoke()".
static const uint8_t MIXEDNET_SRC_EXTERNAL = 0xFF;

// A buffer holds `frames` rows of `channels` int8 values, channels innermost.
// Ring buffers keep the oldest `keep` frames and receive `push` new ones per invoke.
struct MixedNetBuffer {
  uint16_t frames;
  uint16_t channels;
  uint16_t keep;
  uint16_t push;
  uint8_t init;  // value the buffer resets to
};

struct MixedNetOp {
  uint8_t type;
  uint8_t src_buf;
  uint8_t dst_buf;
  uint16_t src_t0;
  uint16_t src_c0;
  uint16_t src_nt;
  uint16_t src_nc;
  uint16_t src_stride;
  uint16_t dst_off;
  uint16_t out_channels;
  int32_t in_offset;
  int32_t out_offset;
  int32_t act_min;
  int32_t act_max;
  const int8_t *weights;
  const int32_t *bias;
  const int32_t *mult;
  const int32_t *shift;
};

struct MixedNetModel {
  const MixedNetBuffer *buffers;
#ifdef MIXEDNET_DEBUG
  const char *const *buffer_names;
#endif
  uint8_t num_buffers;
  const MixedNetOp *ops;
  uint8_t num_ops;
  const uint8_t *shift_buffers;  // ring buffers to advance at the end of an invoke
  uint8_t num_shifts;
  uint8_t input_frames;  // feature frames consumed per invoke
  uint8_t logit_buffer;
  const uint8_t *sigmoid_lut;  // 256 entries, int8 logit -> uint8 probability
};

/// Runs a streaming mixednet wake word model without a TFLite interpreter.
class MixedNetExecutor {
 public:
  /// Allocates the working arena. Returns false if allocation failed.
  bool setup(const MixedNetModel *model);

  /// Clears all streaming state back to the model's initial values.
  void reset();

  /// Runs one inference step over `model->input_frames` feature frames.
  /// Returns the wake word probability quantized to 0-255.
  uint8_t invoke(const int8_t *features);

  size_t arena_size() const { return this->arena_size_; }

  /// The int8 logit of the most recent invoke, before the sigmoid lookup.
  int8_t last_logit() const { return this->last_logit_; }

 protected:
  int8_t *buffer_(uint8_t index) { return this->arena_.get() + this->offsets_[index]; }

 public:
#ifdef MIXEDNET_DEBUG
  /// The raw contents of one working buffer.
  const int8_t *peek(uint8_t index) { return this->buffer_(index); }
#endif

 protected:

  void ring_push_(const MixedNetOp &op, const int8_t *features);
  void copy_(const MixedNetOp &op);
  void dense_(const MixedNetOp &op);
  void depthwise_(const MixedNetOp &op);
  void max_pool_(const MixedNetOp &op);

  const MixedNetModel *model_{nullptr};
  std::unique_ptr<int8_t[]> arena_;
  std::unique_ptr<uint16_t[]> offsets_;
  size_t arena_size_{0};
  int8_t last_logit_{0};
};

}  // namespace esphome::voice_labs
