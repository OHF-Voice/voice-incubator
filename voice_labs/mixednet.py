"""Lowers a micro_wake_word ``.tflite`` to the binary the device loads.

The device runs mixednet-executor, which has no TFLite interpreter and no
flatbuffer parser: ``_mixednet_compiler`` walks the streaming graph here and
emits a flat op table plus weights. Upstream writes that table out as C to be
compiled into the firmware; ``pack()`` writes the same table as a relocatable
binary instead, so the model can arrive over the wire at runtime.

Pointers become byte offsets into a trailing data section. Every array the
executor reads as ``int32_t`` is 4-byte aligned, which is what lets the device
point ``MixedNetOp`` straight at the received buffer with no fixups beyond
adding a base address.
"""

from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import dataclass
from pathlib import Path

from ._mixednet_compiler import EXTERNAL_SRC, MN_MAX_POOL, Compiler

MAGIC = b"MNB1"
FORMAT_VERSION = 1

HEADER = struct.Struct("<4sHHHHHBBBxxxIII")
assert HEADER.size == 32

BUFFER = struct.Struct("<HHHHBx")
assert BUFFER.size == 10

OP = struct.Struct("<BBBxHHHHHHHxxiiiiIIII")
assert OP.size == 52

NULL_OFFSET = 0xFFFFFFFF

# The feature frame width every microWakeWord model is trained on. The device
# refuses a model that disagrees rather than silently reading past its input.
FEATURE_SIZE = 40


@dataclass(frozen=True)
class WakeModel:
    """A lowered model plus the manifest fields the detector needs."""

    model_id: str
    wake_word: str
    blob: bytes
    probability_cutoff: float
    sliding_window_size: int
    feature_step_size: int
    is_vad: bool
    max_pool: bool = False
    """Pooled (the app's trained words): firmware without the op can't run it."""

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.blob).hexdigest()

    def header(self, slot: int) -> dict:
        """The JSON the device needs before the first byte of ``blob``."""
        return {
            "slot": slot,
            "id": self.model_id,
            "wake_word": self.wake_word,
            "kind": "vad" if self.is_vad else "wake",
            "size": len(self.blob),
            "sha256": self.sha256,
            "probability_cutoff": self.probability_cutoff,
            "sliding_window_size": self.sliding_window_size,
            "feature_step_size": self.feature_step_size,
        }


def _align4(data: bytearray) -> None:
    while len(data) % 4:
        data.append(0)


def pack(tflite_path: Path) -> bytes:
    """Lower ``tflite_path`` and serialize it into one relocatable blob."""
    return _serialize(Compiler(Path(tflite_path)).run())


def _serialize(compiler: Compiler) -> bytes:

    data = bytearray()
    offsets: dict[int, int] = {}

    def place(name: str | None) -> int:
        if name is None:
            return NULL_OFFSET
        arr = arrays[name]
        key = id(arr)
        if key not in offsets:
            _align4(data)
            offsets[key] = len(data)
            data.extend(arr.tobytes())
        return offsets[key]

    arrays = {name: arr.reshape(-1) for name, arr, _ in compiler.arrays}

    packed_ops = bytearray()
    for op in compiler.ops:
        src = op["src_buf"]
        packed_ops.extend(
            OP.pack(
                op["type"],
                0xFF if src == EXTERNAL_SRC else src,
                op["dst_buf"],
                op["src_t0"],
                op["src_c0"],
                op["src_nt"],
                op["src_nc"],
                op["src_stride"],
                op["dst_off"],
                op.get("out_channels", 0),
                op.get("in_offset", 0),
                op.get("out_offset", 0),
                op.get("act_min", 0),
                op.get("act_max", 0),
                place(op.get("weights")),
                place(op.get("bias")),
                place(op.get("mult")),
                place(op.get("shift")),
            )
        )
    _align4(data)

    packed_buffers = bytearray()
    arena_size = 0
    for buf in compiler.buffers:
        packed_buffers.extend(
            BUFFER.pack(buf.frames, buf.channels, buf.keep, buf.push, buf.init)
        )
        arena_size += buf.frames * buf.channels

    logit_view, _, _ = compiler.logit
    body = bytearray()
    body.extend(packed_buffers)
    body.extend(packed_ops)
    body.extend(bytes(compiler.shifts))
    _align4(body)
    body.extend(compiler.sigmoid_lut().tobytes())
    body.extend(data)

    header = HEADER.pack(
        MAGIC,
        FORMAT_VERSION,
        0,
        len(compiler.buffers),
        len(compiler.ops),
        len(compiler.shifts),
        compiler.input_frames,
        logit_view.buf,
        FEATURE_SIZE,
        arena_size,
        len(data),
        0,
    )
    return bytes(header + body)


def load(tflite_path: Path, manifest_path: Path | None = None) -> WakeModel:
    """Lower a model and read its microWakeWord manifest beside it.

    The manifest is the ``.json`` microWakeWord ships with the model; its
    ``micro`` block holds the detection settings the model was trained for.
    """
    tflite_path = Path(tflite_path)
    if manifest_path is None:
        manifest_path = tflite_path.with_suffix(".json")
    manifest = json.loads(Path(manifest_path).read_text())
    micro = manifest.get("micro", {})

    wake_word = manifest.get("wake_word", tflite_path.stem)
    compiler = Compiler(tflite_path).run()
    return WakeModel(
        model_id=tflite_path.stem,
        wake_word=wake_word,
        blob=_serialize(compiler),
        probability_cutoff=float(micro.get("probability_cutoff", 0.85)),
        sliding_window_size=int(micro.get("sliding_window_size", 5)),
        feature_step_size=int(micro.get("feature_step_size", 10)),
        is_vad=wake_word == "vad",
        max_pool=any(op["type"] == MN_MAX_POOL for op in compiler.ops),
    )
