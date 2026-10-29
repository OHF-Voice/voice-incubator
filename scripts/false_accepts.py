#!/usr/bin/env python3
"""False accepts per hour, through both stages, on audio with no wake word in it.

Every number in this project so far has been recall: how often a wake word is
found. Recall alone always argues for a lower threshold. This is the other half
-- how often the chain fires on speech, music and a room that never said
anything to it -- and without it no threshold or confusable change can be
justified.

Measured as the device would: the on-device detector over a continuous
recording, then the Kaldi verifier on the 1.5 s of pre-roll ending at each
detection. Stage 1 and stage 2 are reported separately, because the whole
argument for a generous first stage is that the second one catches what it lets
through.

    scripts/false_accepts.py --negatives ~/…/neg-noise01-merged/channels \\
        --wake-binary …/run_okay_nabu
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _corpus import WakeWord, apply_gain  # noqa: E402

from voice_labs.wake import WakeVerifier  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RATE = 16000
PREROLL = int(1.5 * RATE)
CHUNK = 10 * 60 * RATE
"""Processed in ten-minute pieces: the frontend is a Python loop and the whole
recording at once is a lot of memory for no benefit. The detector's state is
carried across pieces, so a detection on a boundary is not invented or lost."""

# What the firmware applies to each tap today.
GAINS = {"agc": 1.0, "aec": 4.0}


def read_block(handle: wave.Wave_read, frames: int) -> np.ndarray:
    raw = handle.readframes(frames)
    audio = np.frombuffer(raw, dtype=np.int16)
    if handle.getnchannels() > 1:
        audio = audio.reshape(-1, handle.getnchannels())[:, 0].copy()
    return audio


def detections(probabilities: np.ndarray, cutoff: int, window: int, cool: int = 100):
    """Where the device would fire, as a sample offset into this block."""
    out = []
    ignore = 0
    recent = np.zeros(window, dtype=np.int64)
    for i, value in enumerate(probabilities):
        recent[i % window] = value
        if recent.sum() > cutoff * window and ignore >= 0:
            out.append((i + 1) * 3 * 160)
            recent[:] = 0
            ignore = -cool
        elif value < cutoff:
            ignore = min(ignore + 1, 0)
    return out


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--negatives", type=Path, required=True)
    parser.add_argument("--wake-binary", type=Path, required=True)
    parser.add_argument("--wake-word", default="okay_nabu", help="the wake word to verify as")
    parser.add_argument("--wake-manifest", type=Path, help="default: models/wake/<wake word>.json")
    parser.add_argument("--config", type=Path, action="append",
                        help="a wake.yaml to verify with; repeat to compare")
    parser.add_argument("--channels", nargs="*")
    parser.add_argument("--cutoff", type=float, action="append")
    parser.add_argument("--scratch", type=Path, default=Path("/tmp"))
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    cutoffs = args.cutoff or [0.85, 0.5]
    configs = args.config or [ROOT / "wake.yaml"]
    index = json.loads((args.negatives / "index.json").read_text())
    channels = args.channels or [c for c in sorted(index["files"]) if "vpe" in c]

    manifest = args.wake_manifest or ROOT / "models/wake" / f"{args.wake_word}.json"
    wake = WakeWord(args.wake_binary, manifest, args.scratch / f"fa-{args.wake_word}.bin")
    verifiers = []
    for path in configs:
        verifier = WakeVerifier(Path(path), args.wake_word)
        await verifier.start()
        verifiers.append((Path(path).stem, verifier))

    rows = []
    print(f"{'channel':22s} {'hours':>6s} {'cutoff':>7s} {'stage 1/h':>10s} "
          + " ".join(f"{name + '/h':>12s}" for name, _ in verifiers))
    for channel in channels:
        path = args.negatives / index["files"][channel]
        gain = GAINS.get(channel.rsplit(".", 1)[-1], 1.0)
        with wave.open(str(path), "rb") as handle:
            rate = handle.getframerate()
            total = handle.getnframes()
            # Probabilities for the whole file, one block at a time, plus the
            # audio needed to rebuild a pre-roll around any detection.
            fired = {cutoff: [] for cutoff in cutoffs}
            carry = np.zeros(0, dtype=np.int16)
            offset = 0
            while True:
                block = read_block(handle, CHUNK)
                if block.size == 0:
                    break
                scaled = apply_gain(block, gain)
                probabilities = wake.probabilities(scaled)
                for cutoff in cutoffs:
                    for at in detections(probabilities, int(cutoff * 255), wake.window):
                        absolute = offset + at
                        window = np.concatenate([carry, scaled])
                        start = len(carry) + at - PREROLL
                        piece = window[max(0, start) : len(carry) + at]
                        if len(piece) < PREROLL:
                            piece = np.concatenate(
                                [np.zeros(PREROLL - len(piece), dtype=np.int16), piece]
                            )
                        fired[cutoff].append((absolute, piece))
                carry = scaled[-PREROLL:]
                offset += len(block)
            hours = total / rate / 3600

        for cutoff in cutoffs:
            accepted = {name: 0 for name, _ in verifiers}
            for _, piece in fired[cutoff]:
                for name, verifier in verifiers:
                    result = await verifier.verify(piece.tobytes())
                    if result.accepted:
                        accepted[name] += 1
            row = {
                "channel": channel,
                "hours": round(hours, 3),
                "cutoff": cutoff,
                "stage1": len(fired[cutoff]),
                "accepted": accepted,
            }
            rows.append(row)
            print(
                f"{channel:22s} {hours:6.2f} {cutoff:7.2f} "
                f"{len(fired[cutoff]) / hours:10.2f} "
                + " ".join(f"{accepted[name] / hours:12.2f}" for name, _ in verifiers),
                flush=True,
            )

    for _, verifier in verifiers:
        await verifier.stop()
    if args.out:
        args.out.write_text(json.dumps(rows, indent=1) + "\n")
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
