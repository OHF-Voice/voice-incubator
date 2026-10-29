#!/usr/bin/env python3
"""A wake word through both stages, on labeled clips: what is found and what is
refused, at several first-stage cutoffs.

Each clip goes through what the satellite and the app do --

    micro_wake_word frontend -> mixednet-executor -> determine_detected
    -> 1.5 s pre-roll -> Kaldi verification, retried with a tail when the
       wake word ran off the window, and the second opinion on a `last` or
       `prefix` rejection

-- once per cutoff (a lower cutoff fires earlier, so the window differs).
Every Kaldi score is kept, so the verifier's thresholds can be swept
afterwards without decoding again (``--report``).

    scripts/bench_wake_words.py --wake-word hey_mycroft \\
        --binary /tmp/run_hey_mycroft \\
        --set positive=/media/cache/12tb/en-us/hey-mycroft/wake-word \\
        --set negative=/media/cache/12tb/en-us/hey-mycroft/not-wake-word \\
        --limit 1500 --out /tmp/hey_mycroft.jsonl
    scripts/bench_wake_words.py --report /tmp/hey_mycroft.jsonl

Clips should have quiet before the wake word (the detector warms up) and after
it (so it can fire); ``--pad`` adds both.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
import tempfile
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _corpus import SAMPLE_RATE, WakeWord, read_wav  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PREROLL_SECONDS = 1.5
TAIL_SECONDS = 0.3
TAIL_EDGE_SECONDS = 0.03
CUTOFFS = (0.2, 0.35, 0.5, 0.7, 0.9)

_W: dict = {}


def _init(wake_word: str, binary: str, scratch: str, ratio_phrase: str | None) -> None:
    from voice_labs.engine import Engine
    from voice_labs.wake import WakeVerifier
    from voice_labs.wake_ratio import WakeRatio

    loop = asyncio.new_event_loop()
    verifier = WakeVerifier(ROOT / "wake.yaml", wake_word)
    loop.run_until_complete(verifier.start())
    ratio = None
    if ratio_phrase:
        ctc_dir = ROOT / "models/speech-to-phrase/stt_en_parakeet_tdt_ctc_110m-int8"
        if not (ctc_dir / "model.onnx").exists():
            ctc_dir = ROOT / "models/speech-to-phrase/stt_en_parakeet_tdt_ctc_110m"
        engine = Engine(ctc_dir, None, None, threads=1)
        ratio = WakeRatio(ratio_phrase)
        ratio.train(engine.model)
    manifest = ROOT / "models/wake" / f"{wake_word}.json"
    _W.update(
        loop=loop,
        verifier=verifier,
        ratio=ratio,
        wake=WakeWord(Path(binary), manifest, Path(scratch) / f"bench-{os.getpid()}.bin"),
    )


def _verify(pcm: bytes):
    return _W["loop"].run_until_complete(_W["verifier"].verify(pcm))


def _scores(result) -> dict:
    out = {
        "accepted": bool(result.accepted),
        "rejected_by": result.rejected_by,
        "split": result.split,
        "confusable_words": result.confusable_words or "",
    }
    for field in ("lr", "lr_prefix", "lr_last", "confusable_margin"):
        value = getattr(result, field, None)
        out[field] = None if value is None else round(float(value), 4)
    return out


def run_one(job: tuple) -> dict:
    label, path, pad, cutoffs = job
    audio = read_wav(Path(path))
    if pad:
        quiet = np.zeros(int(pad * SAMPLE_RATE), dtype=np.int16)
        audio = np.concatenate([quiet, audio, quiet])
    row: dict = {"set": label, "file": str(path)}
    probabilities = _W["wake"].probabilities(audio)
    row["peak"] = int(probabilities.max()) if len(probabilities) else 0
    preroll = int(PREROLL_SECONDS * SAMPLE_RATE)

    def at(detected: int, offset: int) -> bytes:
        end = min(len(audio), detected + offset)
        piece = audio[max(0, end - preroll) : end]
        if len(piece) < preroll:
            piece = np.concatenate([np.zeros(preroll - len(piece), dtype=np.int16), piece])
        return piece.tobytes()

    seen: dict[int, dict] = {}
    row["cutoffs"] = {}
    for cutoff in cutoffs:
        detected = _W["wake"].detect(probabilities, int(cutoff * 255))
        if detected is None:
            row["cutoffs"][str(cutoff)] = None
            continue
        if detected not in seen:
            pcm = at(detected, 0)
            result = _verify(pcm)
            verdict = {"time": round(detected / SAMPLE_RATE, 3), **_scores(result)}
            if (
                not result.accepted
                and result.split is not None
                and result.split >= PREROLL_SECONDS - TAIL_EDGE_SECONDS
            ):
                retry_pcm = at(detected, int(TAIL_SECONDS * SAMPLE_RATE))
                retry = _verify(retry_pcm)
                verdict["retried"] = True
                if retry.accepted or retry.split is not None:
                    verdict.update(_scores(retry))
                    pcm = retry_pcm
            if _W["ratio"] is not None:
                # Every window, so the thresholds can be swept afterwards.
                score = _W["ratio"].score(pcm)
                verdict["ratio"] = None if score is None else round(float(score), 4)
            seen[detected] = verdict
        row["cutoffs"][str(cutoff)] = seen[detected]
    return row


# ------------------------------------------------------------------ report


def _accept(v: dict | None, thresholds: dict, ratio: float | None) -> bool:
    """The verdict at other thresholds, from the scores kept."""
    if v is None or v.get("lr_prefix") is None:
        return False
    if v.get("rejected_by") in ("no_alignment", "error"):
        return False
    prefix_ok = v["lr_prefix"] >= -thresholds["theta_prefix"]
    last_ok = v["lr_last"] >= -thresholds["theta_last"]
    if not (prefix_ok and last_ok):
        # Kaldi tests these first; the app's second opinion may overturn them.
        return ratio is not None and v.get("ratio") is not None and v["ratio"] <= ratio
    return (v.get("confusable_margin") or 0.0) <= thresholds["theta_competitor"]


def report(paths: list[Path], thresholds: dict, ratio: float | None) -> None:
    rows = [json.loads(line) for p in paths for line in p.read_text().splitlines() if line]
    by_set: dict[str, list] = defaultdict(list)
    for row in rows:
        by_set[row["set"]].append(row)
    cutoffs = sorted({c for r in rows for c in r["cutoffs"]}, key=float)
    print(f"thresholds {thresholds}, ratio {ratio}\n")
    print("| set | clips | cutoff | detected | accepted | of detected |")
    print("|---|---|---|---|---|---|")
    for name, items in sorted(by_set.items()):
        for cutoff in cutoffs:
            det = [r["cutoffs"].get(cutoff) for r in items if r["cutoffs"].get(cutoff)]
            acc = [v for v in det if _accept(v, thresholds, ratio)]
            n = len(items)
            print(
                f"| {name} | {n} | {cutoff} | {len(det) / n:.1%} | {len(acc) / n:.1%} | "
                f"{len(acc) / max(1, len(det)):.1%} |"
            )
    for name, items in sorted(by_set.items()):
        rejected = Counter()
        rivals = Counter()
        for r in items:
            v = r["cutoffs"].get(cutoffs[0])
            if v and not _accept(v, thresholds, ratio):
                rejected[v.get("rejected_by") or "threshold"] += 1
            if v and v.get("confusable_words"):
                rivals[v["confusable_words"]] += 1
        print(f"\n{name} at {cutoffs[0]}: rejected by {dict(rejected)}")
        print(f"  closest rivals: {rivals.most_common(12)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--wake-word")
    parser.add_argument("--binary", help="mixednet-executor host harness built for this model")
    parser.add_argument("--set", action="append", default=[], help="label=directory (or a .txt list of files)")
    parser.add_argument("--limit", type=int, default=0, help="at most this many clips per set (random)")
    parser.add_argument("--pad", type=float, default=0.0, help="seconds of quiet added either side")
    parser.add_argument("--cutoffs", type=float, nargs="*", default=list(CUTOFFS))
    parser.add_argument("--ratio-phrase", help="score the second opinion with this phrase")
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--report", type=Path, nargs="*")
    parser.add_argument("--theta-prefix", type=float, default=0.85)
    parser.add_argument("--theta-last", type=float, default=0.35)
    parser.add_argument("--theta-competitor", type=float, default=0.10)
    parser.add_argument("--ratio", type=float, default=1.3, help="second-opinion threshold; negative: off")
    args = parser.parse_args()

    thresholds = {
        "theta_prefix": args.theta_prefix,
        "theta_last": args.theta_last,
        "theta_competitor": args.theta_competitor,
    }
    if args.report:
        report(args.report, thresholds, None if args.ratio < 0 else args.ratio)
        return 0
    if not (args.wake_word and args.binary and args.set and args.out):
        parser.error("--wake-word, --binary, --set and --out are needed to run")

    rng = random.Random(0)
    jobs = []
    for spec in args.set:
        label, _, where = spec.partition("=")
        where_path = Path(where)
        files = (
            [Path(line) for line in where_path.read_text().splitlines() if line]
            if where_path.suffix == ".txt"
            else sorted(where_path.rglob("*.wav"))
        )
        if args.limit and len(files) > args.limit:
            files = rng.sample(files, args.limit)
        jobs += [(label, str(f), args.pad, args.cutoffs) for f in files]
    scratch = tempfile.mkdtemp(prefix="bench-wake-")
    done = 0
    with args.out.open("w") as out, ProcessPoolExecutor(
        args.workers, initializer=_init, initargs=(args.wake_word, args.binary, scratch, args.ratio_phrase)
    ) as pool:
        for row in pool.map(run_one, jobs, chunksize=4):
            out.write(json.dumps(row) + "\n")
            done += 1
            if done % 500 == 0:
                print(f"{done}/{len(jobs)}", file=sys.stderr, flush=True)
    print(f"{done} clips -> {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
