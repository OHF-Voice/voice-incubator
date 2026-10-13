#!/usr/bin/env python3
"""Run the voice_call recognition chain over a mic_tap corpus.

One prompt is recorded simultaneously by every microphone in the room, so the
only thing that differs between the files of one prompt is the capture path.
That makes the corpus a direct comparison of devices and channels: same words,
same speaker, same moment.

Each file goes through what the satellite and the app actually do --

    micro_wake_word frontend -> mixednet-executor -> determine_detected
    -> 1.5 s pre-roll -> Kaldi wake verification (verdict and split point)
    -> speech-to-phrase, trained on Home Assistant's sentence triggers
    -> hassil, for the triggers that capture free text

-- and the verdict is scored against tests/corpus_expected.tsv.

Levels across these captures span 70 dB, so every file is run twice: once as
captured, and once with a single per-channel gain that brings the channel to a
common speech level. The first says what a device would do today, the second is
what the microphone is worth once its gain is set properly.

    scripts/bench_corpus.py ~/opt/mic_tap/sessions/mike-merged --workers 8
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import time
import wave
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _corpus import (  # noqa: E402
    Expectation,
    SAMPLE_RATE,
    WakeWord,
    apply_gain,
    corrections,
    ground_truth,
    read_wav,
    sentence_triggers,
    session_key,
)

ROOT = Path(__file__).resolve().parent.parent
PREROLL_SECONDS = 1.5
TARGET_RMS = 1600.0
"""Common speech level for the level-matched pass, about -26 dBFS. Chosen to
sit near the middle of the captures so neither extreme is pushed into clipping
or into the noise floor."""

_W: dict = {}


def worker_init(
    plain: list[str],
    wildcard: list[str],
    wake_binary: str,
    wake_manifest: str,
    scratch: str,
    cutoff: float | None,
) -> None:
    """Load one copy of everything per worker. This is the expensive part."""
    import asyncio

    from speech_to_phrase import load_recognizer
    from speech_to_phrase.defaults import default_max_score

    from voice_call.wake import WakeVerifier
    from voice_call.wildcards import WildcardMatcher

    model_dir = ROOT / "models/speech-to-phrase/stt_en_parakeet_tdt_ctc_110m-int8"
    if not (model_dir / "model.onnx").exists():
        model_dir = ROOT / "models/speech-to-phrase/stt_en_parakeet_tdt_ctc_110m"

    recognizer = load_recognizer("nemo", model_dir, "en")
    if plain:
        recognizer.train(plain)

    matcher = WildcardMatcher()
    matcher.train(wildcard)

    loop = asyncio.new_event_loop()
    verifier = WakeVerifier(ROOT / "wake.yaml")
    loop.run_until_complete(verifier.start())

    _W.update(
        recognizer=recognizer,
        max_score=default_max_score("nemo"),
        matcher=matcher,
        loop=loop,
        verifier=verifier,
        # One feature file per worker: they all write it before every decode.
        wake=WakeWord(
            Path(wake_binary),
            Path(wake_manifest),
            Path(scratch) / f"bench-wake-{os.getpid()}.bin",
        ),
        has_grammar=bool(plain),
        cutoff=None if cutoff is None else int(cutoff * 255),
    )


def _verify(pcm: bytes):
    return _W["loop"].run_until_complete(_W["verifier"].verify(pcm))


def run_one(job: tuple) -> list[dict]:
    """One wav, once as captured and once level-matched."""
    channel, index, path, phrase, kind, accepted, gain = job
    audio = read_wav(Path(path))
    rows = []
    for mode, factor in (("raw", 1.0), ("matched", gain)):
        started = time.monotonic()
        row = {
            "channel": channel,
            "device": channel.rsplit(".", 1)[0],
            "index": index,
            "phrase": phrase,
            "kind": kind,
            "mode": mode,
            "gain": round(factor, 3),
        }
        rows.append(row)
        signal = apply_gain(audio, factor)

        probabilities = _W["wake"].probabilities(signal)
        row["wake_peak"] = int(probabilities.max()) if len(probabilities) else 0
        detected = _W["wake"].detect(probabilities, _W["cutoff"])
        row["wake_detected"] = detected is not None
        if detected is None:
            row["ms"] = round((time.monotonic() - started) * 1000)
            continue
        row["wake_time"] = round(detected / SAMPLE_RATE, 2)

        preroll = int(PREROLL_SECONDS * SAMPLE_RATE)
        start = max(0, detected - preroll)
        window = signal[start:detected]
        if len(window) < preroll:
            window = np.concatenate(
                [np.zeros(preroll - len(window), dtype=np.int16), window]
            )
        result = _verify(window.tobytes())
        row["verified"] = bool(result.accepted)
        row["rejected_by"] = result.rejected_by
        row["split"] = result.split
        row["wake_start"] = result.wake_start
        # The three tests the verdict is made of, at the operating point the
        # device actually runs at. Without these a threshold can only be swept
        # against some other window, which measures a different question.
        for field in ("lr", "lr_last", "lr_prefix", "confusable_margin"):
            value = getattr(result, field, None)
            row[field] = None if value is None else round(float(value), 4)
        row["confusable_words"] = result.confusable_words or ""

        # The app splits at the end of the wake phrase; with no alignment it
        # falls back to the end of the pre-roll, which is the detection itself.
        split_at = (
            detected - preroll + int(result.split * SAMPLE_RATE)
            if result.split
            else detected
        )
        command = signal[max(0, split_at) :]
        row["command_seconds"] = round(len(command) / SAMPLE_RATE, 2)

        text, score = "", float("inf")
        if _W["has_grammar"]:
            decoded = _W["recognizer"].transcribe(
                command.astype(np.float32) / 32768.0
            )
            text, score = decoded.text, decoded.score
            row["greedy"] = decoded.greedy_text
        row["text"] = text
        row["score"] = round(score, 2) if math.isfinite(score) else None
        row["accepted"] = bool(text) and math.isfinite(score) and score <= _W["max_score"]

        if row["accepted"]:
            row["outcome"] = "correct" if text.lower() in accepted else "wrong"
        else:
            match = _W["matcher"].match(row.get("greedy", ""))
            row["wildcard"] = match.sentence if match else None
            row["wildcard_text"] = match.text if match else None
            if match is not None:
                row["outcome"] = (
                    "correct"
                    if kind == "wildcard" and match.text.lower() in accepted
                    else "wildcard_other"
                )
            else:
                row["outcome"] = "rejected"
        row["ms"] = round((time.monotonic() - started) * 1000)
    return rows


def channel_gains(root: Path, index: dict, sample: int) -> dict[str, float]:
    """One scalar per channel, the way a fixed input gain would be set."""
    gains = {}
    for channel in sorted(index["phrases"][0]["files"]):
        levels = []
        for prompt in index["phrases"][:sample]:
            audio = read_wav(root / prompt["files"][channel]).astype(np.float32)
            levels.append(float(np.sqrt((audio * audio).mean())))
        median = statistics.median(levels)
        gains[channel] = TARGET_RMS / median if median > 0 else 0.0
    return gains


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("session", type=Path, help="a mic_tap session directory")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--out", type=Path, default=ROOT / "tests/bench_corpus.jsonl")
    parser.add_argument(
        "--automations",
        type=Path,
        default=Path.home() / "opt/homeassistant/config/automations.yaml",
    )
    parser.add_argument("--expected", type=Path, default=ROOT / "tests/corpus_expected.tsv")
    parser.add_argument(
        "--corrections",
        type=Path,
        default=ROOT / "tests/corpus_corrections.csv",
        help="what was actually said, where it differs from the prompt list",
    )
    parser.add_argument("--wake-binary", type=Path, required=True)
    parser.add_argument(
        "--wake-manifest", type=Path, default=ROOT / "models/wake/okay_nabu.json"
    )
    parser.add_argument(
        "--wake-cutoff",
        type=float,
        help="override the model manifest's probability_cutoff",
    )
    parser.add_argument("--channels", nargs="*", help="only these channels")
    parser.add_argument("--limit", type=int, help="only this many prompts per channel")
    parser.add_argument("--gain-sample", type=int, default=40)
    parser.add_argument(
        "--force-gain",
        type=float,
        help="use this gain for the level-matched pass instead of the "
        "measured per-channel scalar",
    )
    parser.add_argument(
        "--scratch", type=Path, default=Path(os.environ.get("TMPDIR", "/tmp"))
    )
    args = parser.parse_args()

    prompts = args.session / "prompts"
    index = json.loads((prompts / "index.json").read_text())
    truth = ground_truth(args.expected)
    sentences = sentence_triggers(args.automations)

    missing = {p["phrase"].split(", ", 1)[1].lower() for p in index["phrases"]} - set(truth)
    if missing:
        print(f"No ground truth for {len(missing)} prompt(s): {sorted(missing)[:3]}")
        return 1

    # What a listener heard, where it differs from what was on the prompter.
    fixes = corrections(args.corrections)
    speaker = session_key(args.session)
    changed = dropped = 0

    print(f"Measuring channel levels over {args.gain_sample} prompts...", flush=True)
    gains = channel_gains(prompts, index, args.gain_sample)
    dead = [c for c, g in gains.items() if g == 0.0]
    for channel in dead:
        print(f"  {channel}: silent, skipped")
        del gains[channel]

    if args.force_gain is not None:
        gains = {c: args.force_gain for c in gains}
    channels = [c for c in sorted(gains) if not args.channels or c in args.channels]
    jobs = []
    for prompt in index["phrases"][: args.limit] if args.limit else index["phrases"]:
        phrase = prompt["phrase"].split(", ", 1)[1].lower()
        expectation = truth[phrase]

        correction = fixes.get((speaker, prompt["index"]))
        if correction is not None and not correction.usable:
            # The recorded window is wrong, so there is nothing in it to score.
            dropped += 1
            continue
        if correction is not None and correction.verdict == "fix":
            said = correction.phrase.split(", ", 1)[-1].lower().strip()
            if said != phrase:
                changed += 1
                phrase = said
                # A corrected phrase the triggers can still produce is scored
                # against that sentence; anything else is out of vocabulary and
                # the right answer is a refusal, not the nearest trained match.
                expectation = truth.get(said) or Expectation(said, "oov", [])

        accepted = {a.lower() for a in expectation.accepted}
        for channel in channels:
            jobs.append(
                (
                    channel,
                    prompt["index"],
                    str(prompts / prompt["files"][channel]),
                    phrase,
                    expectation.kind,
                    accepted,
                    gains[channel],
                )
            )

    print(
        f"{len(jobs)} file(s) over {len(channels)} channel(s), "
        f"{len(sentences.plain)} plain and {len(sentences.wildcard)} wildcard "
        f"trigger(s), {args.workers} worker(s)",
        flush=True,
    )
    if fixes:
        print(
            f"corrections for {speaker}: {changed} prompt(s) re-labelled with "
            f"what was actually said, {dropped} dropped as mis-segmented",
            flush=True,
        )

    started = time.monotonic()
    written = 0
    with args.out.open("w") as out, ProcessPoolExecutor(
        max_workers=args.workers,
        initializer=worker_init,
        initargs=(
            sentences.plain,
            sentences.wildcard,
            str(args.wake_binary),
            str(args.wake_manifest),
            str(args.scratch),
            args.wake_cutoff,
        ),
    ) as pool:
        for rows in pool.map(run_one, jobs, chunksize=4):
            for row in rows:
                out.write(json.dumps(row) + "\n")
            written += 1
            if written % 200 == 0:
                elapsed = time.monotonic() - started
                rate = written / elapsed
                print(
                    f"  {written}/{len(jobs)}  {rate:.1f} file/s  "
                    f"eta {(len(jobs) - written) / rate / 60:.1f} min",
                    flush=True,
                )

    print(f"Wrote {args.out} in {(time.monotonic() - started) / 60:.1f} min")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
