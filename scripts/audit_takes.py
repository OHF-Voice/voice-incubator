#!/usr/bin/env python3
"""Find prompts the speaker got wrong, independently of the wake word.

`audit_corpus.py` judges a prompt by how the chain handled it, which only works
when the chain mostly works. On a speaker the wake word model struggles with --
a child, say -- almost every prompt fails, and the ranking stops discriminating.

So this ignores the chain. It transcribes the whole recorded window on a handful
of good channels with no grammar and no wake gating, and asks a narrower
question: does what the speaker said match what was on the prompter? Several
channels recorded the same moment, so their agreement is the confidence.

    scripts/audit_takes.py SESSION --workers 8
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _corpus import ground_truth, read_wav  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PUNCT = re.compile(r"[^a-z0-9 ]+")
# How the decoder tends to render the wake word, which is stripped before the
# command is compared. Anything starting "ok"/"okay" plus one short word.
WAKE = re.compile(r"^(ok|okay|okey|o k)\b( \w{1,7})?\b")

_W: dict = {}


def normalize(text: str) -> str:
    return " ".join(PUNCT.sub(" ", text.lower()).split())


def dedupe(text: str) -> str:
    """Collapse the subword doubling greedy CTC produces: 'setet' -> 'set'."""
    out = []
    for word in text.split():
        for size in range(len(word) // 2, 1, -1):
            if word[-size:] == word[-2 * size : -size]:
                word = word[:-size]
                break
        out.append(word)
    return " ".join(out)


def worker_init() -> None:
    from speech_to_phrase import load_recognizer

    model_dir = ROOT / "models/speech-to-phrase/stt_en_parakeet_tdt_ctc_110m-int8"
    if not (model_dir / "model.onnx").exists():
        model_dir = ROOT / "models/speech-to-phrase/stt_en_parakeet_tdt_ctc_110m"
    _W["recognizer"] = load_recognizer("nemo", model_dir, "en")


def greedy(audio: np.ndarray) -> str:
    from speech_to_phrase.audio import load_audio

    model = _W["recognizer"].model
    log_probs = model.log_probs(load_audio(audio.astype(np.float32) / 32768.0))
    blank, ids, previous = model.tokenizer.blank_id, [], None
    for token_id in log_probs.argmax(axis=-1):
        current = int(token_id)
        if current != previous and current != blank:
            ids.append(current)
        previous = current
    return model.tokenizer.ids_to_text(ids)


def run_one(job: tuple) -> tuple[int, str, str, float, float, float]:
    index, channel, path, gain = job
    audio = read_wav(Path(path))
    seconds = len(audio) / 16000
    scaled = np.clip(audio.astype(np.float32) * gain, -32768, 32767).astype(np.int16)
    # Speech pressed against either edge means the window cut the utterance.
    n = max(1, int(0.15 * 16000))
    rms = float(np.sqrt((scaled.astype(np.float32) ** 2).mean())) or 1e-9
    head = float(np.sqrt((scaled[:n].astype(np.float32) ** 2).mean())) / rms
    tail = float(np.sqrt((scaled[-n:].astype(np.float32) ** 2).mean())) / rms
    return index, channel, greedy(scaled), seconds, head, tail


def pick_channels(prompts: Path, index: dict, count: int) -> dict[str, float]:
    """The loudest channels, with a scalar bringing each to a common level."""
    levels = {}
    for channel in sorted(index["phrases"][0]["files"]):
        values = []
        for prompt in index["phrases"][:25]:
            audio = read_wav(prompts / prompt["files"][channel]).astype(np.float32)
            values.append(float(np.sqrt((audio * audio).mean())))
        values.sort()
        levels[channel] = values[len(values) // 2]
    best = sorted((v, k) for k, v in levels.items() if v > 0)[-count:]
    return {channel: 1600.0 / level for level, channel in best}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("session", type=Path)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--channels", type=int, default=6, help="how many to pool")
    parser.add_argument("--expected", type=Path, default=ROOT / "tests/corpus_expected.tsv")
    parser.add_argument("--threshold", type=float, default=0.75)
    parser.add_argument("--show", type=int, default=40)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    prompts = args.session / "prompts"
    index = json.loads((prompts / "index.json").read_text())
    truth = ground_truth(args.expected)
    gains = pick_channels(prompts, index, args.channels)
    print("pooling " + ", ".join(f"{c} (x{g:.1f})" for c, g in gains.items()))

    jobs = [
        (p["index"], c, str(prompts / p["files"][c]), g)
        for p in index["phrases"]
        for c, g in gains.items()
    ]
    heard: dict[int, list[str]] = {}
    shape: dict[int, tuple[float, float, float]] = {}
    with ProcessPoolExecutor(args.workers, initializer=worker_init) as pool:
        for idx, _, text, seconds, head, tail in pool.map(run_one, jobs, chunksize=4):
            heard.setdefault(idx, []).append(dedupe(normalize(text)))
            shape[idx] = (seconds, head, tail)

    report = []
    for prompt in index["phrases"]:
        idx = prompt["index"]
        phrase = prompt["phrase"].split(", ", 1)[1].lower()
        expectation = truth[phrase]
        reference = normalize(
            expectation.accepted[0] if expectation.accepted else phrase
        )
        commands = [WAKE.sub("", t).strip() for t in heard[idx]]
        best = max(
            (SequenceMatcher(None, c, reference).ratio(), c) for c in commands
        )
        consensus, support = Counter(commands).most_common(1)[0]
        wake_heard = sum(1 for t in heard[idx] if WAKE.match(t)) / len(heard[idx])
        seconds, head, tail = shape[idx]
        report.append(
            {
                "index": idx,
                "take": prompt["take"],
                "phrase": phrase,
                "best_similarity": round(best[0], 2),
                "best_text": best[1],
                "consensus": consensus,
                "support": round(support / len(commands), 2),
                "wake_heard": round(wake_heard, 2),
                "seconds": round(seconds, 2),
                "head": round(head, 2),
                "tail": round(tail, 2),
            }
        )

    report.sort(key=lambda r: r["best_similarity"])
    flagged = [r for r in report if r["best_similarity"] < args.threshold]
    print(
        f"\n{len(report)} prompts; {len(flagged)} below similarity {args.threshold}\n"
    )
    print(f"{'idx':>4s} {'take':>4s} {'sim':>5s} {'sup':>5s} {'wake':>5s} {'secs':>5s} "
          f"{'head':>5s} {'tail':>5s}  prompt")
    for item in report[: args.show]:
        print(
            f"{item['index']:4d} {item['take']:4d} {item['best_similarity']:5.2f} "
            f"{item['support']:5.2f} {item['wake_heard']:5.2f} {item['seconds']:5.2f} "
            f"{item['head']:5.2f} {item['tail']:5.2f}  {item['phrase']}"
        )
        print(f"{'':42s}  heard: {item['best_text'] or '(nothing)'}")

    if args.out:
        args.out.write_text(json.dumps(report, indent=1) + "\n")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
