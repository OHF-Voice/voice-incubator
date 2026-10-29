#!/usr/bin/env python3
"""Run the voice_labs recognition chain over a mic_tap corpus.

One prompt is recorded simultaneously by every microphone in the room, so the
only thing that differs between the files of one prompt is the capture path.
That makes the corpus a direct comparison of devices and channels: same words,
same speaker, same moment.

Each file goes through what the satellite and the app actually do --

    micro_wake_word frontend -> mixednet-executor -> determine_detected
    -> 1.5 s pre-roll -> Kaldi wake verification (verdict and split point),
       retried with a tail, and the second opinion on a rejection
    -> the app's engine: the call decoded from half a second before the wake
       word, the wake phrase found again in the CTC output, and the commands
       recognized with adapt-ctc after it

-- and the command and slots are scored against tests/corpus_expected.yaml.

The commands are a voice_labs ``commands.yaml`` or an adapt-ctc config (each
intent then a command); the default is adapt-ctc's home-extras config, which
the expectations were written for.

Levels across these captures span 70 dB, so every file is run twice: once as
captured, and once with a single per-channel gain that brings the channel to a
common speech level. The first says what a device would do today, the second is
what the microphone is worth once its gain is set properly.

    scripts/bench_corpus.py ~/opt/mic_tap/sessions/mike-merged --workers 8 \\
        --wake-binary /path/to/run_okay_nabu
    scripts/report_corpus.py tests/bench_corpus.jsonl
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _corpus import (  # noqa: E402
    SAMPLE_RATE,
    WakeWord,
    apply_gain,
    corrections,
    read_wav,
    session_key,
)

ROOT = Path(__file__).resolve().parent.parent
PREROLL_SECONDS = 1.5
TAIL_EDGE_SECONDS = 0.03
"""How close to the end of the window counts as the wake word running into it."""
TARGET_RMS = 1600.0
"""Common speech level for the level-matched pass, about -26 dBFS. Chosen to
sit near the middle of the captures so neither extreme is pushed into clipping
or into the noise floor."""
DEFAULT_COMMANDS = ROOT / "adapt_ctc/examples/home-extras.yaml"

_W: dict = {}


def load_commands(path: Path):
    """A voice_labs store (``commands:``), or an adapt-ctc config
    (``intents:``) whose intents become commands that only respond."""
    from adapt_ctc.config import _read  # pylint: disable=import-outside-toplevel

    from voice_labs.commands import BASE_CONFIG, Behavior, Command
    from voice_labs.store import Compiled, compile_store

    data = _read(Path(path), ())
    if "commands" in data:
        return compile_store(data)
    commands = {
        name: Command(name, (spec or {}).get("description") or name, Behavior(response="ok"), spec or {})
        for name, spec in (data.get("intents") or {}).items()
    }
    config = {k: copy.deepcopy(v) for k, v in data.items() if k != "wake"}
    config["settings"] = {**BASE_CONFIG["settings"], **(config.get("settings") or {})}
    return Compiled(data, commands, config)


def worker_init(
    commands: str,
    wake_binary: str,
    wake_manifest: str,
    scratch: str,
    cutoff: float | None,
    ratio_threshold: float | None,
    tail: float,
    tdt: bool,
) -> None:
    """Load one copy of everything per worker. This is the expensive part."""
    import asyncio

    from voice_labs.engine import Engine
    from voice_labs.wake import WakeVerifier
    from voice_labs.wake_ratio import WakeRatio

    ctc_dir = ROOT / "models/speech-to-phrase/stt_en_parakeet_tdt_ctc_110m-int8"
    if not (ctc_dir / "model.onnx").exists():
        ctc_dir = ROOT / "models/speech-to-phrase/stt_en_parakeet_tdt_ctc_110m"
    tdt_dir = ROOT / "models/parakeet-tdt-int8"
    engine = Engine(ctc_dir, tdt_dir if tdt else None, None, threads=1)
    engine.build(load_commands(Path(commands)))

    loop = asyncio.new_event_loop()
    verifier = WakeVerifier(ROOT / "wake.yaml")
    loop.run_until_complete(verifier.start())

    # The app overturns some of the verifier's rejections, so a bench without
    # this is measuring a configuration that no longer ships.
    wake_ratio = None
    if ratio_threshold is None or ratio_threshold >= 0:
        wake_ratio = WakeRatio(threshold=ratio_threshold)
        wake_ratio.train(engine.model)

    _W.update(
        engine=engine,
        loop=loop,
        verifier=verifier,
        ratio=wake_ratio,
        tail=tail,
        # One feature file per worker: they all write it before every decode.
        wake=WakeWord(
            Path(wake_binary),
            Path(wake_manifest),
            Path(scratch) / f"bench-wake-{os.getpid()}.bin",
        ),
        cutoff=None if cutoff is None else int(cutoff * 255),
    )


def _verify(pcm: bytes):
    return _W["loop"].run_until_complete(_W["verifier"].verify(pcm))


def run_one(job: tuple) -> list[dict]:
    """One wav, once as captured and once level-matched."""
    from adapt_ctc.score import Expectation, _slots

    from voice_labs.engine import Satellite

    channel, index, path, phrase, expect, gain = job
    expectation = None if expect is None else Expectation(expect[0], expect[1])
    audio = read_wav(Path(path))
    rows = []
    for mode, factor in (("raw", 1.0), ("matched", gain)):
        started = time.monotonic()
        row = {
            "channel": channel,
            "device": channel.rsplit(".", 1)[0],
            "index": index,
            "phrase": phrase,
            "kind": "unknown" if expectation is None else ("oov" if expectation.intent is None else "command"),
            "expect": None if expectation is None else expectation.intent,
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

        def at(offset: int):
            """The pre-roll-sized window ending `offset` samples past detection."""
            end = min(len(signal), detected + offset)
            piece = signal[max(0, end - preroll) : end]
            if len(piece) < preroll:
                piece = np.concatenate(
                    [np.zeros(preroll - len(piece), dtype=np.int16), piece]
                )
            return piece.tobytes()

        offset = 0
        pcm = at(offset)
        result = _verify(pcm)

        # The adaptive tail, gated exactly as the app gates it: a split on the
        # trailing edge means the wake word had not finished inside the window.
        tail = _W["tail"]
        if (
            not result.accepted
            and tail > 0
            and result.split is not None
            and result.split >= PREROLL_SECONDS - TAIL_EDGE_SECONDS
        ):
            row["retried"] = True
            retry_offset = int(tail * SAMPLE_RATE)
            retry_pcm = at(retry_offset)
            retry = _verify(retry_pcm)
            if retry.accepted or retry.split is not None:
                result, pcm, offset = retry, retry_pcm, retry_offset

        row["verified"] = bool(result.accepted)
        # The second accept path, gated exactly as the app gates it.
        if not result.accepted and _W["ratio"] is not None:
            overturned, score = _W["ratio"].would_accept(result.rejected_by, pcm)
            row["ratio_score"] = None if score is None else round(score, 4)
            row["ratio_overturned"] = bool(overturned)
            if overturned:
                row["verified"] = True
        row["tail_offset"] = offset
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

        # As the app has it: the call starts at the pre-roll, and Kaldi's
        # placements are measured from the start of whatever window was
        # decoded (the retry moves it forward; a short file pads its front).
        call_start = max(0, detected - preroll)
        window_start = detected + offset - preroll
        kaldi_split = (
            window_start + int(result.split * SAMPLE_RATE) if result.split else detected
        ) - call_start
        wake_start = (
            None
            if result.wake_start is None
            else (window_start + int(result.wake_start * SAMPLE_RATE) - call_start) / SAMPLE_RATE
        )
        call = signal[call_start:].astype(np.float32) / 32768.0
        decision, split = _W["engine"].recognize_call(
            call, wake_start, kaldi_split / SAMPLE_RATE, Satellite("bench", "bench")
        )
        row["kaldi_split"] = round(split.kaldi_split, 3)
        row["ctc_phrase"] = split.phrase
        row["cut"] = round(split.cut, 3)
        row["command_seconds"] = round(len(call) / SAMPLE_RATE - split.cut, 2)

        recognition = decision.recognition
        row["greedy"] = recognition.get("transcript", "")
        row["tdt"] = recognition.get("tdt_transcript")
        from voice_labs.commands import command_of  # pylint: disable=import-outside-toplevel

        got = decision.intent
        # A combination of a command (lights_off:area) is the command.
        row["text"] = command_of(got.intent) if got is not None else ""
        row["slots"] = got.slots if got is not None else {}
        row["score"] = round(got.cost, 2) if got is not None and got.cost != float("inf") else None
        row["source"] = (got.example_source or "keywords") if got is not None else None
        row["accepted"] = got is not None
        if decision.hint:
            row["hint"] = decision.hint.get("text")

        # adapt-ctc's own verdicts, and the three the report counts.
        if expectation is None:
            verdict, detail = "unknown", ""
        elif expectation.intent is None:
            verdict, detail = ("false accept", "") if got is not None else ("correct", "")
        elif got is None:
            wanted = next(
                (r for r in recognition.get("rejected", []) if command_of(r["intent"]) == expectation.intent), None
            )
            verdict = "missed"
            detail = (wanted.get("reasons") or [""])[0] if wanted else "intent not in the commands"
        elif command_of(got.intent) != expectation.intent:
            verdict, detail = "wrong intent", ""
        elif expectation.slots is not None and _slots(got.slots) != _slots(expectation.slots):
            verdict, detail = "wrong slots", f"expected {expectation.slots}"
        else:
            verdict, detail = "correct", ""
        row["verdict"] = verdict
        if detail:
            row["detail"] = detail
        if got is None:
            row["outcome"] = "rejected"
        else:
            row["outcome"] = "correct" if verdict == "correct" else "wrong"
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
    from adapt_ctc.config import ConfigError
    from adapt_ctc.score import _key, check, load_manifest

    parser = argparse.ArgumentParser()
    parser.add_argument("session", type=Path, help="a mic_tap session directory")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--out", type=Path, default=ROOT / "tests/bench_corpus.jsonl")
    parser.add_argument(
        "--commands",
        type=Path,
        default=DEFAULT_COMMANDS,
        help="a voice_labs commands.yaml, or an adapt-ctc config (default: "
        "home-extras, which the expectations are written for)",
    )
    parser.add_argument("--expected", type=Path, default=ROOT / "tests/corpus_expected.yaml")
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
        "--wake-tail",
        type=float,
        default=0.3,
        help="seconds of post-detection audio to add and re-verify when the "
        "wake phrase lands on the end of the pre-roll, as the app does it; "
        "0 measures a single decode",
    )
    parser.add_argument(
        "--wake-ratio",
        type=float,
        help="threshold for the second opinion on a rejected wake word (default: "
        "the app's); negative measures Kaldi alone",
    )
    parser.add_argument(
        "--wake-cutoff",
        type=float,
        help="override the model manifest's probability_cutoff",
    )
    parser.add_argument(
        "--no-tdt",
        action="store_true",
        help="without the TDT head: examples only in the CTC evidence, free text as "
        "the CTC head spelled it (and ~350 MB less per worker)",
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
    manifest = load_manifest(args.expected)
    try:
        compiled = load_commands(args.commands)
        # An expected command the commands lack would count as misses.
        check(manifest, set(compiled.commands))
    except ConfigError as err:
        print(f"{args.commands}: {err}")
        return 1
    truth = manifest.sentences

    missing = {
        _key(p["phrase"].split(", ", 1)[1]) for p in index["phrases"]
    } - set(truth)
    if missing:
        print(
            f"No expectation for {len(missing)} prompt(s), scored as unknown: "
            f"{sorted(missing)[:3]}"
        )

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
        phrase = _key(prompt["phrase"].split(", ", 1)[1])

        correction = fixes.get((speaker, prompt["index"]))
        if correction is not None and not correction.usable:
            # The recorded window is wrong, so there is nothing in it to score.
            dropped += 1
            continue
        if correction is not None and correction.verdict == "fix":
            said = _key(correction.phrase.split(", ", 1)[-1])
            if said != phrase:
                changed += 1
                phrase = said

        expectation = truth.get(phrase)
        expect = None if expectation is None else (expectation.intent, expectation.slots)
        for channel in channels:
            jobs.append(
                (
                    channel,
                    prompt["index"],
                    str(prompts / prompt["files"][channel]),
                    phrase,
                    expect,
                    gains[channel],
                )
            )

    print(
        f"{len(jobs)} file(s) over {len(channels)} channel(s), "
        f"{len(compiled.commands)} command(s) from {args.commands.name}, "
        f"{args.workers} worker(s)",
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
            str(args.commands),
            str(args.wake_binary),
            str(args.wake_manifest),
            str(args.scratch),
            args.wake_cutoff,
            args.wake_ratio,
            args.wake_tail,
            not args.no_tdt,
        ),
    ) as pool:
        for rows in pool.map(run_one, jobs, chunksize=4):
            for row in rows:
                out.write(json.dumps(row, default=str) + "\n")
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
