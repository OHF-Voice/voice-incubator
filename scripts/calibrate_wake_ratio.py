#!/usr/bin/env python3
"""Calibrate the second opinion on a rejected wake word (voice_labs/wake_ratio.py).

The ratio is only ever asked about a window the Kaldi verifier has already
rejected on `last` or `prefix`, so that is what it is measured on -- found the
way the app finds it: the on-device detector at the app's cutoff, the 1.5 s
pre-roll ending at the detection, Kaldi verification with the adaptive tail.
Against those, everything that should stay rejected:

  positives    wake words Kaldi rejects on last/prefix: every prompt of each
               speaker session ("okay nabu, <command>"), the eight Voice PE
               taps, as captured and level-matched (as bench_corpus.py does)
  commands     1.5 s of the command right after each of those wake words
  speech       1.5 s windows of the speech-and-music recording (neg-noise01)
               and of the quiet room (neg-background), Voice PE taps
  false wakes  every detection the on-device model makes on those two
               recordings, on every channel
  confusables  TTS "okay now", "okay maybe", "okay natalie", ... that the
               detector fires on

Every window gets Kaldi's verdict and is scored by the new ratio, by the
variants of it in VARIANTS (the README's table of what carries the
separation), and -- when speech_to_phrase is installed -- by the
grammar-vs-free-loop ratio it replaced, all on the same log-probabilities.

The detector binary is mixednet-executor's host harness (see "Multi-microphone
corpus" in the README for how to build run_okay_nabu). The TTS confusables are
<phrase>/*.wav directories (--confusables; skipped if missing).

    .venv/bin/python scripts/calibrate_wake_ratio.py \\
        --wake-binary ~/opt/mixednet-executor/run_okay_nabu \\
        --out /tmp/wake_ratio.jsonl --workers 8
    .venv/bin/python scripts/calibrate_wake_ratio.py --report /tmp/wake_ratio.jsonl

About 27 minutes with 8 workers on x86. --report reprints the tables from a
previous run; --threshold and --sweep change what they are printed at.
"""

from __future__ import annotations

import os

# One thread per worker: the mel front end's matrix product otherwise starts a
# BLAS thread per core in every worker.
for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_var, "1")

import argparse  # noqa: E402
import asyncio  # noqa: E402
import json  # noqa: E402
import statistics  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
import wave  # noqa: E402
import zlib  # noqa: E402
from collections import Counter  # noqa: E402
from multiprocessing import Pool  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402

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
from bench_corpus import PREROLL_SECONDS, TAIL_EDGE_SECONDS, channel_gains  # noqa: E402
from false_accepts import GAINS, detections  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
MODEL_DIR = ROOT / "models/speech-to-phrase/stt_en_parakeet_tdt_ctc_110m-int8"
PREROLL = int(PREROLL_SECONDS * SAMPLE_RATE)
LP = ("last", "prefix")
VPE = tuple(
    f"{device}.{tap}"
    for device in ("pc-vpe", "capture1-vpe", "capture2-vpe", "capture3-vpe")
    for tap in ("agc", "aec")
)
OLD_THRESHOLD = 1.2
"""The speech-to-phrase ratio's default, for the side-by-side."""

_W: dict = {}


class OldRatio:
    """The speech-to-phrase ratio this replaced, on the same log-probabilities.

    "okay nabu" as a speech-to-phrase grammar against a free loop over the
    words of the first 63 pieces, beam 12, per frame -- what
    voice_labs/wake_ratio.py did before it moved to adapt-ctc. The acoustic
    front ends are the same code, so the log-probabilities are identical.
    """

    def __init__(self, model_dir: Path) -> None:
        from speech_to_phrase.grammar import Grammar
        from speech_to_phrase.templates import templates_to_fst
        from speech_to_phrase.tokenizer import SubwordTokenizer

        tokenizer = SubwordTokenizer.from_model_dir(model_dir / "tokens.txt")
        self.blank = tokenizer.blank_id
        self.wake = Grammar.build(
            tokenizer.num_tokens,
            tokenizer.blank_id,
            templates_to_fst(["okay nabu"], tokenizer=tokenizer, locale="en"),
        )
        self.free = Grammar.build(
            tokenizer.num_tokens,
            tokenizer.blank_id,
            templates_to_fst(
                ["{any}"],
                tokenizer=tokenizer,
                locale="en",
                list_values={"any": [tokenizer.ids_to_text([i]) for i in range(1, 64)]},
            ),
        )

    def score(self, log_probs: np.ndarray) -> float | None:
        _, wake_cost, _ = self.wake.decode(log_probs, self.blank, beam=12.0)
        _, free_cost, _ = self.free.decode(log_probs, self.blank, beam=12.0)
        if wake_cost is None or free_cost is None:
            return None
        return float((wake_cost - free_cost) / len(log_probs))


VARIANTS = (
    # (how "okay nabu" is spelled, filler): the choices behind the score
    ("any case, breaks anywhere", "greedy path"),
    ("any case, breaks anywhere", "255-piece words"),
    ("any case, whole words", "255-piece words"),
    ("lowercase, breaks anywhere", "255-piece words"),
    ("lowercase, whole words", "63-piece words"),
    ("lowercase, whole words", "255-piece words"),
    ("lowercase, whole words", "1023-piece words"),
    ('lowercase, whole words, + "okay now"', "255-piece words"),
)


class Variants:
    """The score with each of its choices made differently, for the README's
    table of what carries the separation. "lowercase, whole words" against
    "255-piece words" is the shipped score."""

    def __init__(self, vocab) -> None:
        from adapt_ctc.graph import Meaning, PhraseGraph
        from adapt_ctc.text import name_units
        from voice_labs.wake_ratio import (
            filler_tokens,
            lowercase_vocabulary,
            strict_graph,
        )

        lower = lowercase_vocabulary(vocab)

        def flexible(spelling_vocab, phrases):
            graph = PhraseGraph(spelling_vocab)
            for phrase in phrases:
                for units in name_units(phrase, "en"):
                    graph.add(units, Meaning("keyword", phrase), flexible=True)
            return graph.compile()

        self.vocab = vocab
        self.wakes = {
            "any case, breaks anywhere": flexible(vocab, ["okay nabu"]),
            "any case, whole words": strict_graph(vocab, ["okay nabu"]).compile(),
            "lowercase, breaks anywhere": flexible(lower, ["okay nabu"]),
            "lowercase, whole words": strict_graph(lower, ["okay nabu"]).compile(),
            'lowercase, whole words, + "okay now"': strict_graph(lower, ["okay nabu", "okay now"]).compile(),
        }
        self.fillers = {
            "greedy path": None,
            "63-piece words": filler_tokens(vocab, 63),
            "255-piece words": filler_tokens(vocab, 255),
            "1023-piece words": filler_tokens(vocab, 1023),
        }

    def scores(self, log_probs: np.ndarray) -> dict[str, float]:
        from voice_labs.wake_ratio import gain_over_blank, loop_gain, phrase_gain

        over_blank = gain_over_blank(log_probs, self.vocab)
        frames = over_blank.shape[0]
        wakes = {name: phrase_gain(graph, over_blank) for name, graph in self.wakes.items()}
        fillers = {name: loop_gain(tokens, over_blank) for name, tokens in self.fillers.items()}
        return {
            f"{wake} / {filler}": round((fillers[filler] - wakes[wake]) / frames, 4)
            for wake, filler in VARIANTS
            if np.isfinite(wakes[wake])
        }


# --- workers -----------------------------------------------------------------


def worker_init(
    wake_binary: str, wake_manifest: str, scratch: str, cutoff: float, tail: float, old: bool, variants: bool
) -> None:
    from adapt_ctc.acoustic import CtcModel
    from voice_labs.wake import WakeVerifier
    from voice_labs.wake_ratio import WakeRatio

    model = CtcModel(MODEL_DIR, num_threads=1)
    ratio = WakeRatio()
    ratio.train(model)
    loop = asyncio.new_event_loop()
    verifier = WakeVerifier(ROOT / "wake.yaml")
    loop.run_until_complete(verifier.start())
    _W.update(
        model=model,
        ratio=ratio,
        old=OldRatio(MODEL_DIR) if old else None,
        variants=Variants(model.vocab) if variants else None,
        loop=loop,
        verifier=verifier,
        wake=WakeWord(
            Path(wake_binary), Path(wake_manifest), Path(scratch) / f"calibrate-{os.getpid()}.bin"
        ),
        cutoff=int(cutoff * 255),
        tail=int(tail * SAMPLE_RATE),
    )


def window(signal: np.ndarray, end: int) -> np.ndarray:
    """The pre-roll-sized window ending at sample ``end``, zero-padded in front."""
    end = min(len(signal), end)
    piece = signal[max(0, end - PREROLL) : end]
    if len(piece) < PREROLL:
        piece = np.concatenate([np.zeros(PREROLL - len(piece), dtype=np.int16), piece])
    return piece


def judge(piece: np.ndarray) -> dict:
    """Kaldi's verdict and both scores for one window."""
    result = _W["loop"].run_until_complete(_W["verifier"].verify(piece.tobytes()))
    log_probs, _ = _W["model"].log_probs(piece.astype(np.float32) / 32768.0)
    new = _W["ratio"].score_log_probs(log_probs)
    old = _W["old"].score(log_probs) if _W["old"] is not None else None
    row = {
        "accepted": bool(result.accepted),
        "rejected_by": result.rejected_by,
        "split": result.split,
        "new": None if new is None else round(new, 4),
        "old": None if old is None else round(old, 4),
    }
    if _W["variants"] is not None:
        row["variants"] = _W["variants"].scores(log_probs)
    return row


def judge_detection(signal: np.ndarray, detected: int) -> tuple[dict, int]:
    """Verify the pre-roll ending at a detection, with the app's adaptive tail."""
    verdict, offset = judge(window(signal, detected)), 0
    tail = _W["tail"]
    if (
        not verdict["accepted"]
        and tail > 0
        and verdict["split"] is not None
        and verdict["split"] >= PREROLL_SECONDS - TAIL_EDGE_SECONDS
    ):
        retry = judge(window(signal, detected + tail))
        if retry["accepted"] or retry["split"] is not None:
            verdict, offset = retry, tail
    verdict["offset"] = offset
    return verdict, offset


def run_prompt(job: tuple) -> list[dict]:
    meta, path, gain = job
    signal = apply_gain(read_wav(Path(path)), gain)
    probabilities = _W["wake"].probabilities(signal)
    detected = _W["wake"].detect(probabilities, _W["cutoff"])
    row = {"set": "prompt", **meta, "detected": detected is not None}
    if detected is None:
        return [row]
    verdict, offset = judge_detection(signal, detected)
    end = detected + offset
    row.update(verdict, end=end)
    rows = [row]

    # The command said right after it: the app's split, or the detection.
    split = verdict["split"]
    start = end - PREROLL + int(split * SAMPLE_RATE) if split else detected
    command = signal[max(0, start) : start + PREROLL]
    if len(command) == PREROLL:
        rows.append({"set": "command", **meta, **judge(command)})
    return rows


def run_stream(job: tuple) -> list[dict]:
    meta, path, gain, start, stop, hop = job
    with wave.open(path, "rb") as handle:
        handle.setpos(start)
        audio = np.frombuffer(handle.readframes(stop - start), dtype=np.int16)
    signal = apply_gain(audio, gain)
    return [
        {"set": "speech", **meta, "at": start + at, **judge(signal[at : at + PREROLL])}
        for at in range(0, len(signal) - PREROLL + 1, hop)
    ]


def run_stage1(job: tuple) -> list[dict]:
    meta, path, gain = job
    signal = apply_gain(read_wav(Path(path)), gain)
    probabilities = _W["wake"].probabilities(signal)
    rows = [{"set": "false_wake_scan", **meta, "hours": round(len(signal) / SAMPLE_RATE / 3600, 4)}]
    for at in detections(probabilities, _W["cutoff"], _W["wake"].window):
        verdict, _ = judge_detection(signal, at)
        rows.append({"set": "false_wake", **meta, "at": at, **verdict})
    return rows


def run_tts(job: tuple) -> list[dict]:
    category, path = job
    import soxr

    with wave.open(path, "rb") as handle:
        rate, channels = handle.getframerate(), handle.getnchannels()
        audio = np.frombuffer(handle.readframes(handle.getnframes()), dtype=np.int16)
    audio = audio.reshape(-1, channels)[:, 0].astype(np.float32)
    if rate != SAMPLE_RATE:
        audio = soxr.resample(audio, rate, SAMPLE_RATE)
    # A second of near-silence before, a little after, and a trace of dither
    # throughout: digital zero is not something a microphone ever sends.
    rng = np.random.default_rng(zlib.crc32(path.encode()))
    padded = np.concatenate(
        [np.zeros(SAMPLE_RATE, np.float32), audio, np.zeros(int(0.6 * SAMPLE_RATE), np.float32)]
    )
    signal = np.clip(padded + rng.normal(0, 3, len(padded)), -32768, 32767).astype(np.int16)
    probabilities = _W["wake"].probabilities(signal)
    detected = _W["wake"].detect(probabilities, _W["cutoff"])
    row = {"set": "confusable", "category": category, "path": path, "detected": detected is not None}
    if detected is not None:
        row.update(judge_detection(signal, detected)[0])
    return [row]


def run(job: tuple) -> list[dict]:
    kind, rest = job[0], job[1:]
    return {"prompt": run_prompt, "stream": run_stream, "stage1": run_stage1, "tts": run_tts}[kind](rest)


# --- jobs --------------------------------------------------------------------


def prompt_jobs(args) -> list[tuple]:
    fixes = corrections(args.corrections)
    jobs = []
    for speaker in args.speakers:
        session = args.sessions / f"{speaker}-merged"
        prompts = session / "prompts"
        index = json.loads((prompts / "index.json").read_text())
        gains = channel_gains(prompts, index, 40)
        for prompt in index["phrases"][: args.limit]:
            fix = fixes.get((session_key(session), prompt["index"]))
            if fix is not None and not fix.usable:
                continue  # the recorded window is wrong; nothing in it to score
            for channel in VPE:
                for mode, gain in (("raw", 1.0), ("matched", gains[channel])):
                    meta = {"speaker": speaker, "channel": channel, "mode": mode, "index": prompt["index"]}
                    jobs.append(("prompt", meta, str(prompts / prompt["files"][channel]), gain))
    return jobs


def negative_jobs(args) -> list[tuple]:
    jobs = []
    for name in args.negatives:
        session = args.sessions / f"{name}-merged" / "channels"
        index = json.loads((session / "index.json").read_text())
        for channel, relative in sorted(index["files"].items()):
            path = str(session / relative)
            gain = GAINS.get(channel.rsplit(".", 1)[-1], 1.0)
            meta = {"session": name, "channel": channel}
            jobs.append(("stage1", meta, path, gain))
            if channel not in VPE:
                continue
            with wave.open(path, "rb") as handle:
                frames = handle.getnframes()
            block = 300 * SAMPLE_RATE
            for start in range(0, frames, block):
                jobs.append(
                    ("stream", meta, path, gain, start, min(frames, start + block), int(args.hop * SAMPLE_RATE))
                )
    return jobs


def tts_jobs(args) -> list[tuple]:
    import random

    rng = random.Random(0)
    jobs = []
    for root in args.confusables:
        if not root.is_dir():
            print(f"skipping {root}: not found")
            continue
        for directory in sorted(p for p in root.iterdir() if p.is_dir()):
            files = sorted(str(p) for p in directory.glob("*.wav"))
            for path in rng.sample(files, min(args.per_confusable, len(files))):
                jobs.append(("tts", f"{root.name}/{directory.name}", path))
    return jobs


# --- report ------------------------------------------------------------------


def accepted(rows: list[dict], key: str, threshold: float) -> float:
    if not rows:
        return float("nan")
    return 100.0 * sum(r[key] is not None and r[key] <= threshold for r in rows) / len(rows)


def report(rows: list[dict], new_thresholds: list[float], old_thresholds: list[float], default: float) -> None:
    prompts = [r for r in rows if r["set"] == "prompt"]
    order = ["jenn", "mike", "natalie", "oliver", "mike-old"]
    speakers = sorted({r["speaker"] for r in prompts}, key=lambda s: (s not in order, order.index(s) if s in order else 0, s))
    detected = [r for r in prompts if r.get("detected")]
    positives = [r for r in detected if r.get("rejected_by") in LP]
    by_speaker = {s: [r for r in positives if r["speaker"] == s] for s in speakers}
    speech = [r for r in rows if r["set"] == "speech" and r["session"] == "neg-noise01"]
    quiet = [r for r in rows if r["set"] == "speech" and r["session"] == "neg-background"]
    commands = [r for r in rows if r["set"] == "command"]
    commands_lp = [r for r in commands if r.get("rejected_by") in LP]
    tts = [r for r in rows if r["set"] == "confusable"]
    tts_lp = [r for r in tts if r.get("detected") and r.get("rejected_by") in LP]
    okay_now = [r for r in tts_lp if r["category"].endswith("okay_now")]
    wakes = [r for r in rows if r["set"] == "false_wake"]

    print(f"\n{len(detected)} detections on {len(prompts)} prompt files; {len(positives)} rejected on last/prefix")
    print("positives:", {s: len(v) for s, v in by_speaker.items()})
    print(
        f"speech/music windows {len(speech)} ({Counter(r['rejected_by'] for r in speech)}), "
        f"quiet {len(quiet)}, commands {len(commands)} ({len(commands_lp)} on last/prefix)"
    )
    print(
        f"TTS confusables {len(tts)}, {sum(r.get('detected', False) for r in tts)} detected, "
        f"{len(tts_lp)} of those rejected on last/prefix"
    )

    if any("variants" in r for r in positives):
        print(
            "\n### What carries the separation: rejections recovered at a fixed rate of false accepts, "
            '"okay now" passed at the speech/music threshold\n'
        )
        print('| "okay nabu" spelled | filler | 0.5% speech/music | 0.5% commands | 5% TTS confusables | "okay now" passed |')
        print("|---|---|---|---|---|---|")

        def variant(r: dict, name: str) -> float:
            if name == "old":
                value = r.get("old")
            else:
                value = r.get("variants", {}).get(name)
            return 99.0 if value is None else value

        names = ["old"] + [f"{wake} / {filler}" for wake, filler in VARIANTS]
        for name in names:
            cells = []
            for negatives, rate, judged in (
                (speech, 0.5, positives),
                (commands_lp, 0.5, positives),
                (tts_lp, 5.0, positives),
                (speech, 0.5, okay_now),
            ):
                if not negatives or not judged:
                    cells.append("-")
                    continue
                threshold = float(np.quantile([variant(r, name) for r in negatives], rate / 100))
                cells.append(f"{100 * np.mean([variant(r, name) <= threshold for r in judged]):.1f}%")
            label = "speech-to-phrase ratio | 63-piece loop" if name == "old" else name.replace(" / ", " | ")
            print(f"| {label} | " + " | ".join(cells) + " |")

    for key, thresholds in (("new", new_thresholds), ("old", old_thresholds)):
        if not any(r.get(key) is not None for r in positives):
            continue
        print(f"\n### {key}: rejections overturned (positives) and windows accepted (negatives)\n")
        print("| threshold | " + " | ".join(speakers) + " | speech/music | quiet room | commands | TTS confusables | TTS okay now |")
        print("|---" * (len(speakers) + 6) + "|")
        for threshold in thresholds:
            cells = [f"{accepted(by_speaker[s], key, threshold):.1f}%" for s in speakers]
            cells += [
                f"{accepted(speech, key, threshold):.2f}%",
                f"{accepted(quiet, key, threshold):.2f}%",
                f"{accepted(commands_lp, key, threshold):.2f}%",
                f"{accepted(tts_lp, key, threshold):.1f}%",
                f"{accepted(okay_now, key, threshold):.1f}%",
            ]
            print(f"| {threshold:.2f} | " + " | ".join(cells) + " |")

    print("\n### Wake words (every detection) against the command right after them\n")
    print("| speaker | score | wake p90 | command p10 | separation |")
    print("|---|---|---|---|---|")
    for speaker in speakers:
        for key in ("old", "new"):
            wake = [r[key] for r in detected if r["speaker"] == speaker and r.get(key) is not None]
            command = [r[key] for r in commands if r["speaker"] == speaker and r.get(key) is not None]
            if not wake or not command:
                continue
            p90, p10 = float(np.percentile(wake, 90)), float(np.percentile(command, 10))
            print(f"| {speaker} | {key} | {p90:.3f} | {p10:.3f} | {p10 - p90:+.3f} |")

    has_old = any(r.get("old") is not None for r in positives)
    print("\n### Same false-accept rate, old vs new: positives recovered (all speakers)\n")
    print("| negatives | at | old | new |")
    print("|---|---|---|---|")
    for name, negatives in (("speech/music", speech), ("commands", commands_lp), ("TTS confusables", tts_lp)):
        for target in (0.5, 1.0, 2.0, 5.0):
            cells = []
            for key in ("old", "new"):
                values = [r[key] for r in negatives if r.get(key) is not None]
                if not values or (key == "old" and not has_old):
                    cells.append("-")
                    continue
                threshold = float(np.quantile(values, target / 100))
                cells.append(f"{accepted(positives, key, threshold):.1f}%")
            print(f"| {name} | {target:g}% | " + " | ".join(cells) + " |")

    print(f"\n### Verified, of detections (new at {default:g}, old at {OLD_THRESHOLD:g})\n")
    print("| | Kaldi | + old ratio | + new ratio |")
    print("|---|---|---|---|")
    for speaker in speakers:
        mine = [r for r in detected if r["speaker"] == speaker]
        kaldi = sum(r["accepted"] for r in mine)
        old = kaldi + sum(r.get("rejected_by") in LP and r.get("old") is not None and r["old"] <= OLD_THRESHOLD for r in mine)
        new = kaldi + sum(r.get("rejected_by") in LP and r.get("new") is not None and r["new"] <= default for r in mine)
        print(f"| {speaker} | {100 * kaldi / len(mine):.1f}% | {100 * old / len(mine):.1f}% | {100 * new / len(mine):.1f}% |")

    print("\n### On-device false wakes in the negative recordings (all channels)\n")
    scans = [r for r in rows if r["set"] == "false_wake_scan"]
    print("| recording | channel-hours | detections | Kaldi accepts | last/prefix | overturned old | overturned new |")
    print("|---|---|---|---|---|---|---|")
    for name in dict.fromkeys(r["session"] for r in scans):
        hours = sum(r["hours"] for r in scans if r["session"] == name)
        mine = [r for r in wakes if r["session"] == name]
        lp = [r for r in mine if r.get("rejected_by") in LP]
        print(
            f"| {name} | {hours:.1f} | {len(mine)} | {sum(r['accepted'] for r in mine)} | {len(lp)} | "
            f"{sum(r.get('old') is not None and r['old'] <= OLD_THRESHOLD for r in lp)} | "
            f"{sum(r.get('new') is not None and r['new'] <= default for r in lp)} |"
        )

    print(f"\n### TTS confusables the detector fires on, by phrase (new at {default:g}, old at {OLD_THRESHOLD:g})\n")
    print("| phrase | clips | detected | Kaldi: confusable | last/prefix | overturned old | overturned new |")
    print("|---|---|---|---|---|---|---|")
    for category in sorted({r["category"] for r in tts}):
        mine = [r for r in tts if r["category"] == category]
        fired = [r for r in mine if r.get("detected")]
        lp = [r for r in fired if r.get("rejected_by") in LP]
        print(
            f"| {category} | {len(mine)} | {len(fired)} | {sum(r.get('rejected_by') == 'confusable' for r in fired)} | "
            f"{len(lp)} | {sum(r.get('old') is not None and r['old'] <= OLD_THRESHOLD for r in lp)} | "
            f"{sum(r.get('new') is not None and r['new'] <= default for r in lp)} |"
        )


def timing(threads: int, calls: int = 50) -> float:
    """Median milliseconds for one score() on a 1.5 s window, forward pass included."""
    from adapt_ctc.acoustic import CtcModel
    from voice_labs.wake_ratio import WakeRatio

    model = CtcModel(MODEL_DIR, num_threads=threads)
    ratio = WakeRatio()
    ratio.train(model)
    rng = np.random.default_rng(0)
    pcm = (rng.normal(0, 1000, PREROLL)).astype(np.int16).tobytes()
    ratio.score(pcm)
    times = []
    for _ in range(calls):
        started = time.perf_counter()
        ratio.score(pcm)
        times.append((time.perf_counter() - started) * 1000)
    return statistics.median(times)


def main() -> int:
    from voice_labs.wake_ratio import DEFAULT_THRESHOLD

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, help="where to write the per-window results (JSONL)")
    parser.add_argument("--report", type=Path, help="only print the tables from a previous --out")
    parser.add_argument("--wake-binary", type=Path)
    parser.add_argument("--wake-manifest", type=Path, default=ROOT / "models/wake/okay_nabu.json")
    parser.add_argument("--cutoff", type=float, default=0.2, help="detector cutoff (the app's default)")
    parser.add_argument("--tail", type=float, default=0.3, help="adaptive tail in seconds (the app's default)")
    parser.add_argument("--sessions", type=Path, default=Path("/media/cache/12tb/en-us/office-multicap"))
    parser.add_argument("--speakers", nargs="+", default=["jenn", "mike", "natalie", "oliver", "mike-old"])
    parser.add_argument("--negatives", nargs="*", default=["neg-noise01", "neg-background"])
    parser.add_argument("--hop", type=float, default=3.0, help="seconds between speech windows")
    parser.add_argument(
        "--confusables",
        type=Path,
        nargs="*",
        default=[
            Path("/media/cache/12tb/en-us/okay_nabu-hard-negatives"),
            Path.home() / "opt/micro-wake-word/notebooks/synthetic-negatives-de",
        ],
        help="directories of <phrase>/*.wav TTS clips",
    )
    parser.add_argument("--per-confusable", type=int, default=300)
    parser.add_argument("--limit", type=int, help="only the first N prompts of each session (a quick check)")
    parser.add_argument("--corrections", type=Path, default=ROOT / "tests/corpus_corrections.csv")
    parser.add_argument("--no-old", action="store_true", help="skip the speech-to-phrase ratio")
    parser.add_argument("--no-variants", action="store_true", help="skip the table of score variants")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--scratch", type=Path, default=Path(os.environ.get("TMPDIR", "/tmp")))
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--sweep", default="1.0,1.1,1.2,1.25,1.3,1.35,1.4,1.5")
    parser.add_argument("--old-sweep", default="1.0,1.1,1.2,1.3,1.4")
    args = parser.parse_args()

    sweep = [float(x) for x in args.sweep.split(",")]
    old_sweep = [float(x) for x in args.old_sweep.split(",")]
    if args.report:
        rows = [json.loads(line) for line in args.report.read_text().splitlines() if line]
        report(rows, sweep, old_sweep, args.threshold)
        return 0
    if args.out is None or args.wake_binary is None:
        parser.error("--out and --wake-binary are needed to collect (or --report FILE)")

    old = not args.no_old
    if old:
        try:
            import speech_to_phrase  # noqa: F401
        except ImportError:
            print("speech_to_phrase not installed: measuring the new ratio only")
            old = False

    print(
        f"score() per 1.5 s window, forward pass included: "
        f"{timing(1):.1f} ms on 1 thread, {timing(4):.1f} ms on 4",
        flush=True,
    )

    jobs = prompt_jobs(args) + negative_jobs(args) + tts_jobs(args)
    # Longest first, so one recording scanned last does not hold up the end.
    jobs.sort(key=lambda job: job[0] != "stage1")
    print(f"{len(jobs)} jobs, {args.workers} workers", flush=True)
    started = time.monotonic()
    count = Counter()
    with args.out.open("w") as out, Pool(
        args.workers,
        initializer=worker_init,
        initargs=(
            str(args.wake_binary),
            str(args.wake_manifest),
            str(args.scratch),
            args.cutoff,
            args.tail,
            old,
            not args.no_variants,
        ),
    ) as pool:
        for done, rows in enumerate(pool.imap_unordered(run, jobs, chunksize=2), 1):
            for row in rows:
                out.write(json.dumps(row) + "\n")
                count[row["set"]] += 1
            if done % 1000 == 0:
                rate = done / (time.monotonic() - started)
                print(f"  {done}/{len(jobs)}  eta {(len(jobs) - done) / rate / 60:.1f} min  {dict(count)}", flush=True)
    print(f"wrote {args.out} in {(time.monotonic() - started) / 60:.1f} min")

    rows = [json.loads(line) for line in args.out.read_text().splitlines() if line]
    report(rows, sweep, old_sweep, args.threshold)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
