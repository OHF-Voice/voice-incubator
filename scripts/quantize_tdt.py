"""Dynamically quantize the Parakeet TDT encoder to int8.

The fp32 encoder is 456 MB, which does not leave usable headroom on a 1.8 GB
Pi 4 once speech-to-phrase, Piper and the Kaldi decoders are resident. Dynamic
quantization touches weights only -- no calibration data needed -- and the
decoder/joiner are small enough to leave alone.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from onnxruntime.quantization import QuantType, quantize_dynamic


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--src", default="models/parakeet-tdt/encoder.onnx")
    parser.add_argument("--dst", default="models/parakeet-tdt-int8/encoder.onnx")
    args = parser.parse_args()

    src, dst = Path(args.src), Path(args.dst)
    dst.parent.mkdir(parents=True, exist_ok=True)

    started = time.monotonic()
    quantize_dynamic(
        model_input=src,
        model_output=dst,
        weight_type=QuantType.QInt8,
        # Only the big linear layers; quantizing everything tends to cost
        # accuracy for little extra size on a conformer encoder.
        op_types_to_quantize=["MatMul"],
        extra_options={"MatMulConstBOnly": True},
    )
    print(
        f"{src.stat().st_size / 1e6:.0f} MB -> {dst.stat().st_size / 1e6:.0f} MB "
        f"in {time.monotonic() - started:.0f}s"
    )


if __name__ == "__main__":
    main()
