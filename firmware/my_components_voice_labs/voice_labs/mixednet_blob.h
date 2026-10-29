#pragma once

#include "mixednet.h"

#include <cstddef>
#include <cstdint>
#include <vector>

namespace esphome::voice_labs {

/// Binds a MixedNetModel to a model blob received over the wire.
///
/// The blob is the same op table compile_mixednet.py normally emits as C, with
/// pointers written as offsets into a trailing data section. Parsing copies out
/// the small op and buffer tables (so they land aligned in internal RAM) and
/// points the weight pointers straight into the blob, which is where the 45 kB
/// that actually matter stay. The caller owns the blob and must keep it alive
/// and 4-byte aligned for as long as the model is in use.
class MixedNetBlob {
 public:
  /// Validates and binds. Returns false and sets error() on a malformed blob.
  bool parse(const uint8_t *data, size_t len);

  const MixedNetModel *model() const { return &this->model_; }
  const char *error() const { return this->error_; }

  uint8_t input_frames() const { return this->model_.input_frames; }
  uint8_t feature_size() const { return this->feature_size_; }
  size_t arena_size() const { return this->arena_size_; }

 protected:
  bool fail_(const char *reason);

  std::vector<MixedNetBuffer> buffers_;
  std::vector<MixedNetOp> ops_;
  std::vector<uint8_t> shifts_;
  MixedNetModel model_{};
  const char *error_{nullptr};
  size_t arena_size_{0};
  uint8_t feature_size_{0};
};

}  // namespace esphome::voice_labs
