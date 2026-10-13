#!/usr/bin/env python3
"""Compile a micro_wake_word mixednet TFLite model into a static C description.

The streaming mixednet graph has a very narrow shape: every convolution has VALID
padding and produces exactly one output frame, because the ring-buffer state
variables supply the whole receptive field. That makes every arithmetic layer a
dot product, so the whole graph lowers to four op kinds:

    RING_PUSH  append new frames to a ring window
    DENSE      per-output-channel dot product over a contiguous window
    DEPTHWISE  per-channel dot product over K taps, with a channel offset
    COPY       channel-axis concatenation

This script walks the TFLite graph symbolically, checks those assumptions hold,
and emits the resulting op table plus weights as a C header.
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from tflite import (
    BuiltinOperator as BO,
    ConcatenationOptions,
    Conv2DOptions,
    DepthwiseConv2DOptions,
    FullyConnectedOptions,
    Model,
    StridedSliceOptions,
    VarHandleOptions,
)

OP_NAMES = {v: k for k, v in BO.__dict__.items() if not k.startswith("_")}

TYPE_NP = {0: np.float32, 2: np.int32, 3: np.uint8, 9: np.int8}

MN_RING_PUSH, MN_DENSE, MN_DEPTHWISE, MN_COPY = 0, 1, 2, 3
EXTERNAL_SRC = 0xFF


@dataclass
class View:
    """A window into a buffer laid out as [frames][stride] int8, channels innermost."""

    buf: int
    t0: int
    nt: int
    c0: int
    nc: int
    stride: int

    def contiguous(self) -> bool:
        return self.c0 == 0 and self.nc == self.stride


@dataclass
class Buffer:
    name: str
    frames: int
    channels: int
    keep: int = 0
    push: int = 0
    init: int = 0


def quantize_multiplier(double_multiplier: float) -> tuple[int, int]:
    """Port of tflite::QuantizeMultiplier."""
    if double_multiplier == 0.0:
        return 0, 0
    mantissa, exponent = math.frexp(double_multiplier)
    q = int(round(mantissa * (1 << 31)))
    if q == (1 << 31):
        q //= 2
        exponent += 1
    if exponent < -31:
        return 0, 0
    assert q <= (1 << 31) - 1
    return q, exponent


class Compiler:
    def __init__(self, path: Path):
        self.raw = path.read_bytes()
        self.model = Model.GetRootAsModel(self.raw, 0)
        self.g = self.model.Subgraphs(0)
        self.opcodes = [
            OP_NAMES.get(self.model.OperatorCodes(i).BuiltinCode(), "?")
            for i in range(self.model.OperatorCodesLength())
        ]
        self.values: dict[int, object] = {}
        self.buffers: list[Buffer] = []
        self.ops: list[dict] = []
        self.shifts: list[int] = []
        self.arrays: list[tuple[str, np.ndarray, str]] = []
        self.var_of_tensor: dict[int, str] = {}
        self.ring_of_var: dict[str, int] = {}
        self.input_buf = None
        self.input_frames = None
        self.logit = None

    # ---- tflite accessors -------------------------------------------------
    def tensor(self, i):
        return self.g.Tensors(i)

    def shape(self, i):
        t = self.tensor(i)
        return [int(x) for x in t.ShapeAsNumpy()] if t.ShapeLength() else []

    def data(self, i, graph=None):
        g = graph or self.g
        t = g.Tensors(i)
        b = self.model.Buffers(t.Buffer())
        if b.DataLength() == 0:
            return None
        return np.frombuffer(b.DataAsNumpy().tobytes(), dtype=TYPE_NP[t.Type()])

    def quant(self, i):
        q = self.tensor(i).Quantization()
        scales = [float(x) for x in q.ScaleAsNumpy()]
        zps = [int(x) for x in q.ZeroPointAsNumpy()]
        return scales, zps

    def new_buffer(self, name, frames, channels, keep=0, push=0, init=0) -> int:
        self.buffers.append(Buffer(name, frames, channels, keep, push, init))
        return len(self.buffers) - 1

    def add_array(self, name, arr, ctype) -> str:
        self.arrays.append((name, arr, ctype))
        return name

    # ---- initial state values from the CALL_ONCE subgraph -----------------
    def read_init_values(self) -> dict[str, int]:
        out = {}
        if self.model.SubgraphsLength() < 2:
            return out
        g1 = self.model.Subgraphs(1)
        names = {}
        for i in range(g1.OperatorsLength()):
            op = g1.Operators(i)
            kind = self.opcodes[op.OpcodeIndex()]
            if kind == "VAR_HANDLE":
                tbl = op.BuiltinOptions()
                o = VarHandleOptions()
                o.Init(tbl.Bytes, tbl.Pos)
                names[int(op.OutputsAsNumpy()[0])] = o.SharedName().decode()
            elif kind == "ASSIGN_VARIABLE":
                ins = [int(x) for x in op.InputsAsNumpy()]
                vals = self.data(ins[1], graph=g1)
                uniq = np.unique(vals)
                assert len(uniq) == 1, f"non-uniform state init for {names[ins[0]]}"
                out[names[ins[0]]] = int(uniq[0]) & 0xFF
        return out

    # ---- main symbolic pass ----------------------------------------------
    def run(self):
        inits = self.read_init_values()
        g = self.g
        inp = int(g.InputsAsNumpy()[0])
        in_shape = self.shape(inp)
        assert len(in_shape) == 3 and in_shape[0] == 1, f"unexpected input {in_shape}"
        self.input_frames = in_shape[1]
        self.values[inp] = View(EXTERNAL_SRC, 0, in_shape[1], 0, in_shape[2], in_shape[2])

        for i in range(g.OperatorsLength()):
            op = g.Operators(i)
            kind = self.opcodes[op.OpcodeIndex()]
            ins = [int(x) for x in op.InputsAsNumpy()] if op.InputsLength() else []
            outs = [int(x) for x in op.OutputsAsNumpy()] if op.OutputsLength() else []
            handler = getattr(self, f"op_{kind.lower()}", None)
            if handler is None:
                raise NotImplementedError(f"op {kind} at index {i}")
            handler(op, ins, outs, inits)

        assert self.logit is not None, "no LOGISTIC/QUANTIZE output found"
        return self

    # ---- per-op handlers --------------------------------------------------
    def op_call_once(self, op, ins, outs, inits):
        pass

    def op_var_handle(self, op, ins, outs, inits):
        tbl = op.BuiltinOptions()
        o = VarHandleOptions()
        o.Init(tbl.Bytes, tbl.Pos)
        self.var_of_tensor[outs[0]] = o.SharedName().decode()

    def op_reshape(self, op, ins, outs, inits):
        src = self.values[ins[0]]
        self.values[outs[0]] = src

    def op_read_variable(self, op, ins, outs, inits):
        name = self.var_of_tensor[ins[0]]
        shape = self.shape(outs[0])
        assert len(shape) == 4 and shape[0] == 1 and shape[2] == 1, shape
        keep, channels = shape[1], shape[3]
        assert self.tensor(outs[0]).Type() == 9, (
            "float state variables are not supported by this prototype"
        )
        buf = self.new_buffer(name, keep, channels, keep=keep, init=inits.get(name, 0))
        self.ring_of_var[name] = buf
        self.values[outs[0]] = View(buf, 0, keep, 0, channels, channels)

    def op_concatenation(self, op, ins, outs, inits):
        tbl = op.BuiltinOptions()
        o = ConcatenationOptions()
        o.Init(tbl.Bytes, tbl.Pos)
        axis = o.Axis()
        if axis < 0:
            axis += 4
        views = [self.values[i] for i in ins]
        if axis == 1:
            state, new = views
            buf = self.buffers[state.buf]
            assert state.t0 == 0 and state.nt == buf.keep
            buf.push = new.nt
            buf.frames = buf.keep + new.nt
            self.ops.append(
                dict(
                    type=MN_RING_PUSH,
                    src_buf=new.buf,
                    src_t0=new.t0,
                    src_c0=new.c0,
                    src_nt=new.nt,
                    src_nc=new.nc,
                    src_stride=new.stride,
                    dst_buf=state.buf,
                    dst_off=0,
                )
            )
            self.shifts.append(state.buf)
            self.values[outs[0]] = View(
                state.buf, 0, buf.frames, 0, buf.channels, buf.channels
            )
        elif axis == 3:
            total = sum(v.nc for v in views)
            assert all(v.nt == 1 for v in views), "channel concat of multi-frame views"
            dst = self.new_buffer(f"concat{outs[0]}", 1, total)
            off = 0
            for v in views:
                self.ops.append(
                    dict(
                        type=MN_COPY,
                        src_buf=v.buf,
                        src_t0=v.t0,
                        src_c0=v.c0,
                        src_nt=1,
                        src_nc=v.nc,
                        src_stride=v.stride,
                        dst_buf=dst,
                        dst_off=off,
                    )
                )
                off += v.nc
            self.values[outs[0]] = View(dst, 0, 1, 0, total, total)
        else:
            raise NotImplementedError(f"concat axis {axis}")

    def op_strided_slice(self, op, ins, outs, inits):
        src = self.values[ins[0]]
        begin = self.data(ins[1])
        end = self.data(ins[2])
        strides = self.data(ins[3])
        tbl = op.BuiltinOptions()
        o = StridedSliceOptions()
        o.Init(tbl.Bytes, tbl.Pos)
        assert o.EllipsisMask() == 0 and o.NewAxisMask() == 0 and o.ShrinkAxisMask() == 0
        assert all(int(s) == 1 for s in strides), "only unit strides supported"
        dims = [1, src.nt, 1, src.nc]
        lo, hi = [], []
        for d in range(4):
            b = 0 if (o.BeginMask() >> d) & 1 else int(begin[d])
            e = dims[d] if (o.EndMask() >> d) & 1 else int(end[d])
            if b < 0:
                b += dims[d]
            if e < 0:
                e += dims[d]
            lo.append(max(0, min(b, dims[d])))
            hi.append(max(0, min(e, dims[d])))
        assert (lo[0], hi[0]) == (0, 1) and (lo[2], hi[2]) == (0, 1)
        self.values[outs[0]] = View(
            src.buf,
            src.t0 + lo[1],
            hi[1] - lo[1],
            src.c0 + lo[3],
            hi[3] - lo[3],
            src.stride,
        )

    def op_assign_variable(self, op, ins, outs, inits):
        name = self.var_of_tensor[ins[0]]
        buf_idx = self.ring_of_var[name]
        buf = self.buffers[buf_idx]
        v = self.values[ins[1]]
        # The assigned slice must be the tail of the pushed window: that is exactly
        # what a shift-by-push at the end of the invoke reproduces.
        assert v.buf == buf_idx and v.nt == buf.keep and v.t0 == buf.push, (
            f"{name}: unexpected state update {v}"
        )

    def op_split_v(self, op, ins, outs, inits):
        src = self.values[ins[0]]
        sizes = [int(x) for x in self.data(ins[1])]
        axis = int(self.data(ins[2])[0])
        if axis < 0:
            axis += 4
        assert axis == 3, f"split axis {axis}"
        off = 0
        for size, out in zip(sizes, outs):
            self.values[out] = View(src.buf, src.t0, src.nt, src.c0 + off, size, src.stride)
            off += size
        assert off == src.nc

    def _requant(self, name, in_idx, w_idx, out_idx, out_channels):
        in_scales, in_zps = self.quant(in_idx)
        w_scales, _ = self.quant(w_idx)
        out_scales, out_zps = self.quant(out_idx)
        if len(w_scales) == 1:
            w_scales = w_scales * out_channels
        mult, shift = [], []
        for oc in range(out_channels):
            m, s = quantize_multiplier(in_scales[0] * w_scales[oc] / out_scales[0])
            mult.append(m)
            shift.append(s)
        return (
            self.add_array(f"{name}_mult", np.array(mult, dtype=np.int32), "int32_t"),
            self.add_array(f"{name}_shift", np.array(shift, dtype=np.int32), "int32_t"),
            -in_zps[0],
            out_zps[0],
        )

    def _act_range(self, fused, out_idx):
        scales, zps = self.quant(out_idx)
        if fused == 0:  # NONE
            return -128, 127
        if fused == 1:  # RELU
            lo = max(-128, min(127, zps[0] + int(round(0.0 / scales[0]))))
            return lo, 127
        raise NotImplementedError(f"fused activation {fused}")

    def _dense(self, op, ins, outs, name, fused, dw=False):
        src = self.values[ins[0]]
        w = self.data(ins[1]).copy()
        bias = self.data(ins[2]).copy()
        wshape = self.shape(ins[1])
        if dw:
            taps, channels = wshape[1], wshape[3]
            assert src.nt == taps, f"{name}: window {src.nt} != kernel {taps}"
            assert src.nc == channels
            out_buf = self.new_buffer(f"{name}_out", 1, channels)
            mult, shift, in_off, out_off = self._requant(
                name, ins[0], ins[1], outs[0], channels
            )
            act_min, act_max = self._act_range(fused, outs[0])
            self.ops.append(
                dict(
                    type=MN_DEPTHWISE,
                    src_buf=src.buf,
                    src_t0=src.t0,
                    src_c0=src.c0,
                    src_nt=taps,
                    src_nc=channels,
                    src_stride=src.stride,
                    dst_buf=out_buf,
                    dst_off=0,
                    out_channels=channels,
                    weights=self.add_array(f"{name}_w", w, "int8_t"),
                    bias=self.add_array(f"{name}_b", bias, "int32_t"),
                    mult=mult,
                    shift=shift,
                    in_offset=in_off,
                    out_offset=out_off,
                    act_min=act_min,
                    act_max=act_max,
                )
            )
            self.values[outs[0]] = View(out_buf, 0, 1, 0, channels, channels)
            return

        out_channels = wshape[0]
        row = int(np.prod(wshape[1:]))
        assert src.contiguous(), f"{name}: non-contiguous input view {src}"
        assert src.nt * src.nc == row, f"{name}: window {src.nt}x{src.nc} != row {row}"
        out_buf = self.new_buffer(f"{name}_out", 1, out_channels)
        mult, shift, in_off, out_off = self._requant(
            name, ins[0], ins[1], outs[0], out_channels
        )
        act_min, act_max = self._act_range(fused, outs[0])
        self.ops.append(
            dict(
                type=MN_DENSE,
                src_buf=src.buf,
                src_t0=src.t0,
                src_c0=0,
                src_nt=src.nt,
                src_nc=src.nc,
                src_stride=src.stride,
                dst_buf=out_buf,
                dst_off=0,
                out_channels=out_channels,
                weights=self.add_array(f"{name}_w", w, "int8_t"),
                bias=self.add_array(f"{name}_b", bias, "int32_t"),
                mult=mult,
                shift=shift,
                in_offset=in_off,
                out_offset=out_off,
                act_min=act_min,
                act_max=act_max,
            )
        )
        self.values[outs[0]] = View(out_buf, 0, 1, 0, out_channels, out_channels)

    def op_conv_2d(self, op, ins, outs, inits):
        tbl = op.BuiltinOptions()
        o = Conv2DOptions()
        o.Init(tbl.Bytes, tbl.Pos)
        assert o.DilationHFactor() == 1 and o.DilationWFactor() == 1
        self._dense(op, ins, outs, f"conv{outs[0]}", o.FusedActivationFunction())

    def op_depthwise_conv_2d(self, op, ins, outs, inits):
        tbl = op.BuiltinOptions()
        o = DepthwiseConv2DOptions()
        o.Init(tbl.Bytes, tbl.Pos)
        assert o.DepthMultiplier() == 1, "depth multiplier != 1"
        self._dense(op, ins, outs, f"dw{outs[0]}", o.FusedActivationFunction(), dw=True)

    def op_fully_connected(self, op, ins, outs, inits):
        tbl = op.BuiltinOptions()
        o = FullyConnectedOptions()
        o.Init(tbl.Bytes, tbl.Pos)
        self._dense(op, ins, outs, f"fc{outs[0]}", o.FusedActivationFunction())

    def op_logistic(self, op, ins, outs, inits):
        self.logit = (self.values[ins[0]], ins[0], outs[0])

    def op_quantize(self, op, ins, outs, inits):
        view, logit_idx, _ = self.logit
        self.logit = (view, logit_idx, outs[0])

    # ---- emit -------------------------------------------------------------
    def sigmoid_lut(self) -> np.ndarray:
        _, logit_idx, out_idx = self.logit
        in_scales, in_zps = self.quant(logit_idx)
        out_scales, out_zps = self.quant(out_idx)
        lut = np.zeros(256, dtype=np.uint8)
        for q in range(-128, 128):
            real = in_scales[0] * (q - in_zps[0])
            p = 1.0 / (1.0 + math.exp(-real))
            v = int(round(p / out_scales[0])) + out_zps[0]
            lut[q & 0xFF] = max(0, min(255, v))
        return lut

    def emit(self, prefix: str) -> str:
        view, _, _ = self.logit
        lines = []
        w = lines.append
        w("// Generated by compile_mixednet.py -- do not edit.")
        w('#include "mixednet.h"')
        w("")
        w("namespace esphome::micro_wake_word {")
        w("namespace {")
        for name, arr, ctype in self.arrays:
            vals = ", ".join(str(int(x)) for x in arr.reshape(-1))
            w(f"const {ctype} {name}[] = {{{vals}}};")
        lut = self.sigmoid_lut()
        w(
            "const uint8_t sigmoid_lut[256] = {"
            + ", ".join(str(int(x)) for x in lut)
            + "};"
        )
        w("")
        w("const MixedNetBuffer buffers[] = {")
        for b in self.buffers:
            w(
                f"    {{{b.frames}, {b.channels}, {b.keep}, {b.push}, {b.init}}},"
                f"  // {b.name}"
            )
        w("};")
        w("")
        w("const MixedNetOp ops[] = {")
        for o in self.ops:
            src = o["src_buf"]
            src_c = "0xFF" if src == EXTERNAL_SRC else str(src)
            w(
                "    {{{t}, {src}, {dst}, {t0}, {c0}, {nt}, {nc}, {stride}, {doff}, {oc}, "
                "{inoff}, {outoff}, {amin}, {amax}, {wts}, {bias}, {mult}, {shift}}},".format(
                    t=o["type"],
                    src=src_c,
                    dst=o["dst_buf"],
                    t0=o["src_t0"],
                    c0=o["src_c0"],
                    nt=o["src_nt"],
                    nc=o["src_nc"],
                    stride=o["src_stride"],
                    doff=o["dst_off"],
                    oc=o.get("out_channels", 0),
                    inoff=o.get("in_offset", 0),
                    outoff=o.get("out_offset", 0),
                    amin=o.get("act_min", 0),
                    amax=o.get("act_max", 0),
                    wts=o.get("weights", "nullptr"),
                    bias=o.get("bias", "nullptr"),
                    mult=o.get("mult", "nullptr"),
                    shift=o.get("shift", "nullptr"),
                )
            )
        w("};")
        w("")
        w("#ifdef MIXEDNET_DEBUG")
        w("const char *const buffer_names[] = {")
        for b in self.buffers:
            w(f'    "{b.name}",')
        w("};")
        w("#endif")
        w("")
        w(
            "const uint8_t shift_buffers[] = {"
            + ", ".join(str(s) for s in self.shifts)
            + "};"
        )
        w("")
        w("}  // namespace")
        w("")
        w(f"extern const MixedNetModel {prefix};")
        w(f"const MixedNetModel {prefix} = {{")
        w("    buffers,")
        w("#ifdef MIXEDNET_DEBUG")
        w("    buffer_names,")
        w("#endif")
        w(f"    {len(self.buffers)},")
        w("    ops,")
        w(f"    {len(self.ops)},")
        w("    shift_buffers,")
        w(f"    {len(self.shifts)},")
        w(f"    {self.input_frames},")
        w(f"    {view.buf},")
        w("    sigmoid_lut,")
        w("};")
        w("")
        w("}  // namespace esphome::micro_wake_word")
        return "\n".join(lines) + "\n"

    def report(self):
        total_w = sum(a.nbytes for _, a, _ in self.arrays)
        state = sum(b.frames * b.channels for b in self.buffers)
        macs = 0
        for o in self.ops:
            if o["type"] == MN_DENSE:
                macs += o["out_channels"] * o["src_nt"] * o["src_nc"]
            elif o["type"] == MN_DEPTHWISE:
                macs += o["out_channels"] * o["src_nt"]
        kinds = {MN_RING_PUSH: "ring", MN_DENSE: "dense", MN_DEPTHWISE: "dw", MN_COPY: "copy"}
        seq = " ".join(kinds[o["type"]] for o in self.ops)
        return (
            f"ops={len(self.ops)} buffers={len(self.buffers)} "
            f"weights={total_w}B scratch+state={state}B MAC/invoke={macs} "
            f"input_frames={self.input_frames}\n  {seq}"
        )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("model")
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("-n", "--name", required=True)
    args = ap.parse_args()
    c = Compiler(Path(args.model)).run()
    Path(args.output).write_text(c.emit(args.name))
    print(f"{Path(args.model).name}: {c.report()}")


if __name__ == "__main__":
    main()
