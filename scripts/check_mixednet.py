#!/usr/bin/env python3
"""Check that a pushed model decodes to the same probabilities as a built-in one.

The device runs mixednet-executor against a blob the app sends at runtime,
instead of the C table compile_mixednet.py normally compiles into the firmware.
Both paths come out of the same lowering, so the only thing that can drift is
the serialization: voice_labs/mixednet.py writing it and mixednet_blob.cpp
reading it back. This builds both harnesses and diffs them frame by frame.

    scripts/check_mixednet.py models/wake/okay_nabu.tflite tests/*.wav

Needs mixednet-executor and the ESPHome component checked out; pass --upstream
and --component if they are not where they are by default.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice_labs.mixednet import pack  # noqa: E402

DEFAULT_UPSTREAM = Path.home() / "opt/mixednet-executor"
DEFAULT_COMPONENT = Path.home() / "opt/voice-pe-alt-firmware/components/voice_labs"

BLOB_MAIN = """
#include "mixednet.h"
#include "mixednet_blob.h"
#include <cstdio>
#include <cstdlib>
#include <vector>

static std::vector<uint8_t> slurp(const char *path) {
  std::FILE *f = std::fopen(path, "rb");
  if (f == nullptr) { std::perror(path); std::exit(2); }
  std::fseek(f, 0, SEEK_END);
  long size = std::ftell(f);
  std::fseek(f, 0, SEEK_SET);
  std::vector<uint8_t> out(size);
  if (std::fread(out.data(), 1, size, f) != (size_t) size) { std::perror("read"); std::exit(2); }
  std::fclose(f);
  return out;
}

int main(int argc, char **argv) {
  using namespace esphome::voice_labs;
  std::vector<uint8_t> blob_bytes = slurp(argv[1]);
  std::vector<int8_t> features;
  { auto raw = slurp(argv[2]); features.assign(raw.begin(), raw.end()); }

  MixedNetBlob blob;
  if (!blob.parse(blob_bytes.data(), blob_bytes.size())) {
    std::fprintf(stderr, "parse failed: %s\\n", blob.error());
    return 1;
  }
  MixedNetExecutor executor;
  if (!executor.setup(blob.model())) { std::fprintf(stderr, "setup failed\\n"); return 1; }
  std::fprintf(stderr, "arena: %zu bytes\\n", executor.arena_size());

  const size_t frame = blob.feature_size();
  const size_t step = blob.input_frames();
  for (size_t i = 0; i + step <= features.size() / frame; i += step)
    std::printf("%d %u\\n", executor.last_logit(), executor.invoke(features.data() + i * frame));
  return 0;
}
"""


def features_from_wavs(paths: list[Path], random_frames: int, seed: int) -> np.ndarray:
    from pymicro_features import MicroFrontend

    blocks = []
    for path in paths:
        with wave.open(str(path), "rb") as wav:
            assert wav.getframerate() == 16000, f"{path}: not 16 kHz"
            assert wav.getsampwidth() == 2, f"{path}: not 16-bit"
            channels = wav.getnchannels()
            audio = np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16)
        if channels > 1:
            audio = audio.reshape(-1, channels)[:, 0].copy()

        frontend = MicroFrontend()
        raw = audio.tobytes()
        rows = []
        index = 0
        while index + 320 <= len(raw):
            result = frontend.process_samples(raw[index : index + 320])
            index += result.samples_read * 2
            if result.features:
                rows.append(result.features)
        # pymicro-features already divides by 25.6; the device sees the raw value.
        values = np.rint(np.array(rows) * 25.6).astype(np.int32)
        blocks.append(np.clip((values * 256 + 333) // 666 - 128, -128, 127).astype(np.int8))

    if random_frames:
        # Correlated runs rather than white noise, so the sweep looks like a
        # spectrogram and still reaches both ends of the int8 range.
        rng = np.random.default_rng(seed)
        base = rng.integers(-128, 128, size=(random_frames, 40))
        smooth = np.cumsum(rng.integers(-40, 41, size=(random_frames, 40)), axis=0)
        blocks.append(np.clip((base + smooth) % 256 - 128, -128, 127).astype(np.int8))

    return np.concatenate(blocks, axis=0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("model", type=Path)
    parser.add_argument("wav", nargs="*", type=Path)
    parser.add_argument("--upstream", type=Path, default=DEFAULT_UPSTREAM)
    parser.add_argument("--component", type=Path, default=DEFAULT_COMPONENT)
    parser.add_argument("--random-frames", type=int, default=3000)
    parser.add_argument("--seed", type=int, default=1234)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        features = features_from_wavs(args.wav, args.random_frames, args.seed)
        (work / "features.bin").write_bytes(features.tobytes())
        print(f"features: {features.shape[0]} frames")

        subprocess.run(
            [
                sys.executable,
                str(args.upstream / "compile_mixednet.py"),
                str(args.model),
                "-o",
                str(work / "model.cpp"),
                "-n",
                "MODEL",
            ],
            check=True,
        )
        (work / "blob.mnb").write_bytes(pack(args.model))
        (work / "blob_main.cpp").write_text(BLOB_MAIN)

        common = ["g++", "-O2", "-std=gnu++17"]
        subprocess.run(
            common
            + [f"-I{args.upstream}", "-DMODEL_SYMBOL=MODEL", "-o", str(work / "ref"),
               str(args.upstream / "mixednet.cpp"), str(work / "model.cpp"),
               str(args.upstream / "host_main.cpp")],
            check=True,
        )
        subprocess.run(
            common
            + [f"-I{args.component}", "-o", str(work / "blob"),
               str(args.component / "mixednet.cpp"), str(args.component / "mixednet_blob.cpp"),
               str(work / "blob_main.cpp")],
            check=True,
        )

        ref = subprocess.run(
            [str(work / "ref"), str(work / "features.bin")], capture_output=True, text=True, check=True
        ).stdout
        blob = subprocess.run(
            [str(work / "blob"), str(work / "blob.mnb"), str(work / "features.bin")],
            capture_output=True,
            text=True,
            check=True,
        ).stdout

    steps = ref.count("\n")
    if ref != blob:
        mismatches = sum(a != b for a, b in zip(ref.splitlines(), blob.splitlines()))
        print(f"MISMATCH: {mismatches} of {steps} steps differ", file=sys.stderr)
        return 1
    print(f"identical over {steps} inference steps")
    return 0


if __name__ == "__main__":
    sys.exit(main())
