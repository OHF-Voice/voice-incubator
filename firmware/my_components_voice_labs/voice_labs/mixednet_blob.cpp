#include "mixednet_blob.h"

#include <cstring>

namespace esphome::voice_labs {

namespace {

// Must match voice_labs/mixednet.py in the app.
constexpr size_t HEADER_SIZE = 32;
constexpr size_t BUFFER_SIZE = 10;
constexpr size_t OP_SIZE = 52;
constexpr size_t SIGMOID_SIZE = 256;
constexpr uint32_t NULL_OFFSET = 0xFFFFFFFFu;

// MixedNetExecutor indexes its arena with uint16_t offsets.
constexpr size_t MAX_ARENA_SIZE = 0xFFFF;

uint16_t read_u16(const uint8_t *p) { return static_cast<uint16_t>(p[0]) | (static_cast<uint16_t>(p[1]) << 8); }

uint32_t read_u32(const uint8_t *p) {
  return static_cast<uint32_t>(p[0]) | (static_cast<uint32_t>(p[1]) << 8) | (static_cast<uint32_t>(p[2]) << 16) |
         (static_cast<uint32_t>(p[3]) << 24);
}

int32_t read_i32(const uint8_t *p) { return static_cast<int32_t>(read_u32(p)); }

}  // namespace

bool MixedNetBlob::fail_(const char *reason) {
  this->error_ = reason;
  this->buffers_.clear();
  this->ops_.clear();
  this->shifts_.clear();
  this->model_ = MixedNetModel{};
  return false;
}

bool MixedNetBlob::parse(const uint8_t *data, size_t len) {
  this->error_ = nullptr;
  if (data == nullptr || len < HEADER_SIZE)
    return this->fail_("truncated header");
  if (std::memcmp(data, "MNB1", 4) != 0)
    return this->fail_("bad magic");
  // Weight pointers are taken into this buffer, and the int32 arrays inside it
  // are only 4-byte aligned relative to a 4-byte aligned base.
  if ((reinterpret_cast<uintptr_t>(data) % 4) != 0)
    return this->fail_("blob is not 4-byte aligned");
  if (read_u16(data + 4) != 1)
    return this->fail_("unsupported format version");

  const uint16_t num_buffers = read_u16(data + 8);
  const uint16_t num_ops = read_u16(data + 10);
  const uint16_t num_shifts = read_u16(data + 12);
  const uint8_t input_frames = data[14];
  const uint8_t logit_buffer = data[15];
  this->feature_size_ = data[16];
  this->arena_size_ = read_u32(data + 20);
  const size_t data_size = read_u32(data + 24);

  if (num_buffers == 0 || num_ops == 0 || input_frames == 0)
    return this->fail_("empty model");
  // MixedNetModel counts and MIXEDNET_SRC_EXTERNAL share the uint8 range.
  if (num_buffers >= MIXEDNET_SRC_EXTERNAL || num_ops > 255 || num_shifts > 255)
    return this->fail_("model too large");
  if (logit_buffer >= num_buffers)
    return this->fail_("logit buffer out of range");
  if (this->arena_size_ > MAX_ARENA_SIZE)
    return this->fail_("arena too large");

  size_t pos = HEADER_SIZE;
  const size_t buffers_bytes = static_cast<size_t>(num_buffers) * BUFFER_SIZE;
  const size_t ops_bytes = static_cast<size_t>(num_ops) * OP_SIZE;
  if (len < pos + buffers_bytes + ops_bytes + num_shifts)
    return this->fail_("truncated tables");

  this->buffers_.resize(num_buffers);
  size_t arena = 0;
  for (uint16_t i = 0; i < num_buffers; i++) {
    const uint8_t *p = data + pos + static_cast<size_t>(i) * BUFFER_SIZE;
    MixedNetBuffer &b = this->buffers_[i];
    b.frames = read_u16(p);
    b.channels = read_u16(p + 2);
    b.keep = read_u16(p + 4);
    b.push = read_u16(p + 6);
    b.init = p[8];
    if (b.frames == 0 || b.channels == 0)
      return this->fail_("empty buffer");
    arena += static_cast<size_t>(b.frames) * b.channels;
  }
  if (arena != this->arena_size_)
    return this->fail_("arena size mismatch");
  pos += buffers_bytes;

  // Offsets are relative to the data section, which the packer pads to a
  // 4-byte boundary so the int32 arrays can be read in place.
  const size_t tail = pos + ops_bytes + num_shifts;
  const size_t data_start = ((tail + 3) & ~size_t{3}) + SIGMOID_SIZE;
  if (len < data_start + data_size)
    return this->fail_("truncated data section");
  const uint8_t *blob_data = data + data_start;

  auto resolve = [&](uint32_t offset, size_t align) -> const uint8_t * {
    if (offset == NULL_OFFSET)
      return nullptr;
    if (offset >= data_size || (offset % align) != 0)
      return nullptr;
    return blob_data + offset;
  };

  this->ops_.resize(num_ops);
  for (uint16_t i = 0; i < num_ops; i++) {
    const uint8_t *p = data + pos + static_cast<size_t>(i) * OP_SIZE;
    MixedNetOp &op = this->ops_[i];
    op.type = p[0];
    op.src_buf = p[1];
    op.dst_buf = p[2];
    op.src_t0 = read_u16(p + 4);
    op.src_c0 = read_u16(p + 6);
    op.src_nt = read_u16(p + 8);
    op.src_nc = read_u16(p + 10);
    op.src_stride = read_u16(p + 12);
    op.dst_off = read_u16(p + 14);
    op.out_channels = read_u16(p + 16);
    op.in_offset = read_i32(p + 20);
    op.out_offset = read_i32(p + 24);
    op.act_min = read_i32(p + 28);
    op.act_max = read_i32(p + 32);

    if (op.type > MIXEDNET_OP_MAX_POOL)
      return this->fail_("unknown op");
    if (op.dst_buf >= num_buffers)
      return this->fail_("op destination out of range");
    if (op.src_buf != MIXEDNET_SRC_EXTERNAL && op.src_buf >= num_buffers)
      return this->fail_("op source out of range");

    const uint32_t w_off = read_u32(p + 36);
    const uint32_t b_off = read_u32(p + 40);
    const uint32_t m_off = read_u32(p + 44);
    const uint32_t s_off = read_u32(p + 48);
    op.weights = reinterpret_cast<const int8_t *>(resolve(w_off, 1));
    op.bias = reinterpret_cast<const int32_t *>(resolve(b_off, 4));
    op.mult = reinterpret_cast<const int32_t *>(resolve(m_off, 4));
    op.shift = reinterpret_cast<const int32_t *>(resolve(s_off, 4));

    const bool arithmetic = op.type == MIXEDNET_OP_DENSE || op.type == MIXEDNET_OP_DEPTHWISE;
    if (arithmetic && (op.weights == nullptr || op.bias == nullptr || op.mult == nullptr || op.shift == nullptr))
      return this->fail_("op is missing weights");
  }
  pos += ops_bytes;

  this->shifts_.assign(data + pos, data + pos + num_shifts);
  for (uint8_t index : this->shifts_) {
    if (index >= num_buffers)
      return this->fail_("shift buffer out of range");
  }

  this->model_.buffers = this->buffers_.data();
#ifdef MIXEDNET_DEBUG
  this->model_.buffer_names = nullptr;
#endif
  this->model_.num_buffers = static_cast<uint8_t>(num_buffers);
  this->model_.ops = this->ops_.data();
  this->model_.num_ops = static_cast<uint8_t>(num_ops);
  this->model_.shift_buffers = this->shifts_.data();
  this->model_.num_shifts = static_cast<uint8_t>(num_shifts);
  this->model_.input_frames = input_frames;
  this->model_.logit_buffer = logit_buffer;
  this->model_.sigmoid_lut = data + data_start - SIGMOID_SIZE;
  return true;
}

}  // namespace esphome::voice_labs
