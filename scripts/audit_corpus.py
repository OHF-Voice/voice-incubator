#!/usr/bin/env python3
"""Find prompts whose *recording* is bad, as opposed to channels that are bad.

The two confound each other in a single number: a prompt can score badly because
the speaker fumbled it, or because half the microphones in the room are poor. The
way out is that the corpus records every prompt on every channel at once, so the
two have different shapes -- a bad microphone fails across prompts, a bad take
fails across channels.

So each prompt is scored against what its channels manage on everything *else*.
A prompt that 30 channels agree on, and that the speaker said differently from
what was on screen, is a re-record; a prompt that only the quiet channels miss is
just a hard prompt.

    scripts/audit_corpus.py tests/bench_oliver_c050.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import wave
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _corpus import ground_truth  # noqa: E402

PUNCT = re.compile(r"[^a-z0-9 ]+")


def normalize(text: str) -> str:
    return " ".join(PUNCT.sub(" ", text.lower()).split())


def dedupe(text: str) -> str:
    """Undo the subword doubling greedy CTC produces: 'setet' -> 'set'.

    The decoder hedges between 'set' and 'se'+'t' and greedy walks across both,
    so a word often carries its own tail twice. Collapsing repeats makes the
    transcript comparable without pretending it is clean.
    """
    out = []
    for word in text.split():
        for size in range(len(word) // 2, 1, -1):
            if word[-size:] == word[-2 * size : -size]:
                word = word[:-size]
                break
        out.append(word)
    return " ".join(out)


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def edges(path: Path, window: float = 0.15) -> tuple[float, float]:
    """RMS of the first and last slice of the window, relative to the whole.

    The recorder pads 100 ms before and 500 ms after, so speech pressed against
    either edge means the window cut the utterance.
    """
    with wave.open(str(path), "rb") as wav:
        rate = wav.getframerate()
        audio = np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16)
    audio = audio.astype(np.float32)
    if audio.size == 0:
        return 0.0, 0.0
    n = max(1, int(window * rate))
    overall = float(np.sqrt((audio * audio).mean())) or 1e-9
    head = float(np.sqrt((audio[:n] * audio[:n]).mean()))
    tail = float(np.sqrt((audio[-n:] * audio[-n:]).mean()))
    return head / overall, tail / overall


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("results", type=Path)
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--expected", type=Path, default=Path(__file__).parent.parent / "tests/corpus_expected.tsv")
    parser.add_argument("--mode", default="matched")
    parser.add_argument("--show", type=int, default=30)
    parser.add_argument(
        "--reference-channel",
        default="",
        help="measure window edges on this channel (default: the loudest)",
    )
    parser.add_argument("--out", type=Path, help="write the flagged indices here")
    args = parser.parse_args()

    rows = [
        json.loads(line)
        for line in args.results.read_text().splitlines()
        if line and json.loads(line)["mode"] == args.mode
    ]
    truth = ground_truth(args.expected)
    prompts = args.session / "prompts"
    index = json.loads((prompts / "index.json").read_text())
    meta = {p["index"]: p for p in index["phrases"]}

    reference = args.reference_channel or Counter(
        r["channel"] for r in rows
    ).most_common(1)[0][0]

    by_channel: dict[str, list[dict]] = defaultdict(list)
    by_prompt: dict[int, list[dict]] = defaultdict(list)
    for row in rows:
        by_channel[row["channel"]].append(row)
        by_prompt[row["index"]].append(row)

    def is_good(row: dict) -> bool:
        if row["kind"] == "oov":
            return (
                not row["wake_detected"]
                or not row.get("verified")
                or row.get("outcome") == "rejected"
            )
        return row.get("outcome") == "correct" and bool(row.get("verified"))

    # How each channel does on everything else, so a prompt is judged against
    # the channels that actually recorded it rather than against the room.
    channel_rate = {
        channel: sum(is_good(r) for r in items) / len(items)
        for channel, items in by_channel.items()
    }

    report = []
    for idx, items in by_prompt.items():
        prompt = meta[idx]
        phrase = prompt["phrase"].split(", ", 1)[1].lower()
        expectation = truth[phrase]
        reference_text = normalize(
            expectation.accepted[0] if expectation.accepted else phrase
        )

        observed = sum(is_good(r) for r in items) / len(items)
        # Expected score if this prompt were as good as the average prompt on
        # exactly these channels.
        expected = sum(channel_rate[r["channel"]] for r in items) / len(items)

        heard = [
            dedupe(normalize(r["greedy"]))
            for r in items
            if r.get("greedy")
        ]
        consensus, support = ("", 0)
        if heard:
            consensus, count = Counter(heard).most_common(1)[0]
            support = count / len(heard)
        # Agreement on the *content* rather than the exact string: how many
        # channels heard something close to what was on the prompter.
        agree = (
            sum(1 for h in heard if similarity(h, reference_text) >= 0.8) / len(heard)
            if heard
            else 0.0
        )

        head, tail = edges(prompts / prompt["files"][reference])
        report.append(
            {
                "index": idx,
                "take": prompt["take"],
                "phrase": phrase,
                "kind": expectation.kind,
                "observed": observed,
                "expected": expected,
                "deficit": expected - observed,
                "wake": sum(r["wake_detected"] for r in items) / len(items),
                "consensus": consensus,
                "support": support,
                "agree": agree,
                "similarity": similarity(consensus, reference_text) if consensus else 0.0,
                "seconds": round(prompt["end_t"] - prompt["start_t"], 2),
                "head": round(head, 2),
                "tail": round(tail, 2),
            }
        )

    report.sort(key=lambda r: (-r["deficit"], r["agree"]))
    print(
        f"{len(report)} prompts, {len(by_channel)} channels, mode={args.mode}, "
        f"edges measured on {reference}\n"
    )
    print(
        f"{'idx':>4s} {'take':>4s} {'got':>6s} {'exp':>6s} {'wake':>6s} "
        f"{'agree':>6s} {'sim':>5s} {'secs':>5s} {'head':>5s} {'tail':>5s}  prompt / consensus"
    )
    for item in report[: args.show]:
        print(
            f"{item['index']:4d} {item['take']:4d} {item['observed']:6.0%} "
            f"{item['expected']:6.0%} {item['wake']:6.0%} {item['agree']:6.0%} "
            f"{item['similarity']:5.2f} {item['seconds']:5.2f} "
            f"{item['head']:5.2f} {item['tail']:5.2f}  {item['phrase']}"
        )
        print(f"{'':58s}  heard: {item['consensus'] or '(no wake on any channel)'}")

    if args.out:
        args.out.write_text(json.dumps(report, indent=1) + "\n")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
