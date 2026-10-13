#!/usr/bin/env python3
"""Write where the wake word is in every prompt of a capture session.

The on-device detector cannot be used to find it: it misses a third of a
child's utterances, so localising with it would find only the ones it already
handles. The Kaldi verifier can, but it answers "does this window END with the
wake word" -- so sliding the window end across the recording and keeping the
position it likes best turns it into a locator that owes the wake word model
nothing.

What comes out is a **span**, in samples into the recording, plus the word
boundaries inside it. Earlier passes over this corpus computed the same
alignments and kept only summary statistics; a span is what actually lets a
clip be cut, or a review screen open with its marks already in the right place.

    scripts/align_sessions.py --sessions ~/…/office-multicap --out tests/alignments
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _corpus import apply_gain, read_wav  # noqa: E402

from voice_call.wake import WakeVerifier  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RATE = 16000
WINDOW = 1.4
"""What the verifier decodes. Its graphs expect the wake word at the end."""

STEP = 0.12
"""How far the window moves between tries. Finer than the ~0.2 s of slack the
alignment itself has, and 22 positions is already most of a second per prompt."""

TARGET_RMS = 1600.0
"""Channels in one session span 70 dB, so each is brought to a common speech
level first -- the thresholds in wake.yaml are calibrated against one."""


async def align_session(
    verifier: WakeVerifier,
    prompts: Path,
    index: dict,
    channel: str,
    first: float,
    last: float,
) -> list[dict]:
    levels = []
    for prompt in index["phrases"][:15]:
        audio = read_wav(prompts / prompt["files"][channel]).astype(np.float32)
        levels.append(float(np.sqrt((audio * audio).mean())))
    levels.sort()
    median = levels[len(levels) // 2]
    gain = TARGET_RMS / median if median > 0 else 1.0

    ends = []
    position = first
    while position <= last:
        ends.append(round(position, 3))
        position += STEP

    rows = []
    for prompt in index["phrases"]:
        audio = apply_gain(read_wav(prompts / prompt["files"][channel]), gain)
        frames = len(audio)
        best = None
        for end_seconds in ends:
            end = int(end_seconds * RATE)
            if end > frames + int(WINDOW * RATE):
                break
            start = max(0, end - int(WINDOW * RATE))
            window = audio[start:end]
            if len(window) < int(WINDOW * RATE):
                window = np.concatenate(
                    [np.zeros(int(WINDOW * RATE) - len(window), dtype=np.int16), window]
                )
            result = await verifier.verify(window.tobytes())
            if result.lr is None or result.split is None:
                continue
            if best is None or result.lr > best[0].lr:
                # The window's own start, in the recording's samples, so the
                # alignment can be lifted out of window time.
                best = (result, end - int(WINDOW * RATE))

        if best is None:
            rows.append(
                {
                    "prompt": prompt["index"],
                    "take": prompt.get("take", 1),
                    "phrase": prompt.get("phrase", ""),
                    "channel": channel,
                    "sample_rate": RATE,
                    "frame_count": frames,
                    "gain": round(gain, 3),
                    "wake": None,
                    "words": [],
                    "lr": None,
                    "accepted": False,
                    "rejected_by": "no_alignment",
                }
            )
            continue

        result, offset = best

        def to_sample(seconds: float) -> int:
            return max(0, min(frames, offset + int(seconds * RATE)))

        start = (
            to_sample(result.wake_start)
            if result.wake_start is not None
            else to_sample(max(0.0, result.split - 1.0))
        )
        end = to_sample(result.split)
        if end <= start:
            # The alignment ran past the end of a recording too short to hold
            # the wake word, and clamping collapsed it. A zero-length span is
            # not a worse answer than no answer, it is no answer -- and saying
            # so keeps it out of a count of usable examples.
            rows.append(
                {
                    "prompt": prompt["index"],
                    "take": prompt.get("take", 1),
                    "phrase": prompt.get("phrase", ""),
                    "channel": channel,
                    "sample_rate": RATE,
                    "frame_count": frames,
                    "gain": round(gain, 3),
                    "wake": None,
                    "words": [],
                    "lr": None if result.lr is None else round(result.lr, 4),
                    "accepted": False,
                    "rejected_by": "no_alignment",
                    "window_start": offset,
                }
            )
            continue

        rows.append(
            {
                "prompt": prompt["index"],
                "take": prompt.get("take", 1),
                "phrase": prompt.get("phrase", ""),
                "channel": channel,
                "sample_rate": RATE,
                "frame_count": frames,
                "gain": round(gain, 3),
                "wake": {"start": start, "end": end},
                "words": [
                    {"word": word, "start": to_sample(a), "end": to_sample(b)}
                    for word, a, b in result.aligned_words
                    # The background model emits filler tokens for whatever is
                    # not the wake word; they are not words anybody said.
                    if not word.startswith("f_")
                ],
                "lr": None if result.lr is None else round(result.lr, 4),
                "lr_last": None if result.lr_last is None else round(result.lr_last, 4),
                "lr_prefix": (
                    None if result.lr_prefix is None else round(result.lr_prefix, 4)
                ),
                "confusable_margin": (
                    None
                    if result.confusable_margin is None
                    else round(result.confusable_margin, 4)
                ),
                "accepted": bool(result.accepted),
                "rejected_by": result.rejected_by,
                # The window the alignment was found in, so a doubtful row can
                # be listened to exactly as the verifier heard it.
                "window_start": offset,
            }
        )
    return rows


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sessions",
        type=Path,
        default=Path("/media/cache/12tb/en-us/office-multicap"),
    )
    parser.add_argument("--session", action="append", help="default: all of them")
    parser.add_argument("--channel", default="mark2-sj201.ch1")
    parser.add_argument("--out", type=Path, default=ROOT / "tests/alignments")
    parser.add_argument("--first", type=float, default=0.8, help="earliest window end")
    parser.add_argument("--last", type=float, default=3.4, help="latest window end")
    args = parser.parse_args()

    names = args.session or sorted(
        entry.name
        for entry in args.sessions.iterdir()
        if (entry / "prompts" / "index.json").is_file()
    )
    args.out.mkdir(parents=True, exist_ok=True)

    verifier = WakeVerifier(ROOT / "wake.yaml")
    await verifier.start()
    try:
        for name in names:
            prompts = args.sessions / name / "prompts"
            index = json.loads((prompts / "index.json").read_text())
            if args.channel not in index["phrases"][0]["files"]:
                print(f"{name}: no channel {args.channel!r}", file=sys.stderr)
                continue
            started = time.monotonic()
            rows = await align_session(
                verifier, prompts, index, args.channel, args.first, args.last
            )
            path = args.out / f"{name}.jsonl"
            with path.open("w", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(json.dumps(row, sort_keys=True) + "\n")
            found = sum(1 for row in rows if row["wake"])
            accepted = sum(1 for row in rows if row["accepted"])
            spans = [
                (row["wake"]["end"] - row["wake"]["start"]) / RATE
                for row in rows
                if row["wake"]
            ]
            print(
                f"{name}: {found}/{len(rows)} aligned, {accepted} accepted, "
                f"median span {np.median(spans):.2f}s -> {path} "
                f"({time.monotonic() - started:.0f}s)",
                flush=True,
            )
    finally:
        await verifier.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
