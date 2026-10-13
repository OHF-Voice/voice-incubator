#!/usr/bin/env python3
"""Compare bench runs from several speakers over the same prompts and rig.

The same 102 prompts, the same 35 channels, the same room -- so a difference
between two runs is the speaker. That also separates two things a single run
confounds: a prompt that fails for one speaker is a bad take, a prompt that
fails for all of them is a hard prompt or a wrong expectation.

    scripts/compare_speakers.py mike=tests/a.jsonl oliver=tests/b.jsonl ...
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _corpus import ground_truth  # noqa: E402


def good(row: dict) -> bool:
    if row["kind"] == "oov":
        return (
            not row["wake_detected"]
            or not row.get("verified")
            or row.get("outcome") == "rejected"
        )
    return row.get("outcome") == "correct" and bool(row.get("verified"))


def load(spec: str, mode: str) -> tuple[str, list[dict]]:
    label, _, path = spec.partition("=")
    rows = [json.loads(line) for line in Path(path).read_text().splitlines() if line]
    return label, [r for r in rows if r["mode"] == mode]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("runs", nargs="+", help="label=path.jsonl")
    parser.add_argument("--mode", default="matched")
    parser.add_argument("--expected", type=Path, default=Path(__file__).parent.parent / "tests/corpus_expected.tsv")
    parser.add_argument("--top", type=int, default=14)
    args = parser.parse_args()

    runs = dict(load(spec, args.mode) for spec in args.runs)
    truth = ground_truth(args.expected)

    print(f"mode={args.mode}\n")
    print(f"{'speaker':12s} {'n':>6s} {'wake':>7s} {'verified':>9s} {'ASR|wake':>9s} "
          f"{'in-vocab':>9s} {'OOV rej':>8s} {'app':>7s}")
    print("-" * 74)
    for label, rows in runs.items():
        woke = [r for r in rows if r["wake_detected"]]
        ver = [r for r in woke if r.get("verified")]
        gram = [r for r in woke if r["kind"] == "grammar"]
        voc = [r for r in rows if r["kind"] in ("grammar", "wildcard")]
        oov = [r for r in rows if r["kind"] == "oov"]
        print(
            f"{label:12s} {len(rows):6d} {len(woke)/len(rows):6.1%} "
            f"{len(ver)/len(woke) if woke else 0:8.1%} "
            f"{sum(r['outcome']=='correct' for r in gram)/len(gram) if gram else 0:8.1%} "
            f"{sum(good(r) for r in voc)/len(voc):8.1%} "
            f"{sum(good(r) for r in oov)/len(oov):7.1%} "
            f"{sum(good(r) for r in rows)/len(rows):6.1%}"
        )

    print("\n=== channel ranking, does it hold across speakers? ===")
    per: dict[str, dict[str, float]] = defaultdict(dict)
    for label, rows in runs.items():
        by = defaultdict(list)
        for r in rows:
            by[r["channel"]].append(r)
        for channel, items in by.items():
            per[channel][label] = sum(good(r) for r in items) / len(items)
    labels = list(runs)
    print(f"{'channel':24s} " + " ".join(f"{x:>8s}" for x in labels) + "     mean")
    ranked = sorted(per.items(), key=lambda kv: -sum(kv[1].values()) / len(kv[1]))
    for channel, scores in ranked[: args.top]:
        mean = sum(scores.values()) / len(scores)
        print(f"{channel:24s} " + " ".join(f"{scores.get(x,0):8.1%}" for x in labels) + f" {mean:8.1%}")
    print("  ...")
    for channel, scores in ranked[-3:]:
        mean = sum(scores.values()) / len(scores)
        print(f"{channel:24s} " + " ".join(f"{scores.get(x,0):8.1%}" for x in labels) + f" {mean:8.1%}")

    # Spearman-free agreement: how many of each run's top 10 are shared.
    tops = {
        label: {c for c, _ in sorted(per.items(), key=lambda kv: -kv[1].get(label, 0))[:10]}
        for label in labels
    }
    pairs = [(a, b) for i, a in enumerate(labels) for b in labels[i + 1 :]]
    print("\ntop-10 channel overlap: " + ", ".join(
        f"{a}/{b} {len(tops[a] & tops[b])}/10" for a, b in pairs
    ))

    print("\n=== prompts by how many speakers they fail for ===")
    phrase_rate: dict[str, dict[str, float]] = defaultdict(dict)
    for label, rows in runs.items():
        by = defaultdict(list)
        for r in rows:
            by[r["phrase"]].append(r)
        for phrase, items in by.items():
            phrase_rate[phrase][label] = sum(good(r) for r in items) / len(items)
    universal, specific = [], []
    for phrase, scores in phrase_rate.items():
        values = [scores.get(x, 0) for x in labels]
        spread = max(values) - min(values)
        if max(values) < 0.5:
            universal.append((sum(values) / len(values), phrase, scores))
        elif spread > 0.45:
            specific.append((spread, phrase, scores))

    print(f"\nfails for every speaker ({len(universal)}) -- a hard prompt or a wrong expectation:")
    for mean, phrase, scores in sorted(universal)[: args.top]:
        kind = truth[phrase].kind
        print(f"  {mean:5.0%} [{kind:8s}] {phrase:50s} " +
              " ".join(f"{x}={scores.get(x,0):.0%}" for x in labels))
    print(f"\nfails for one speaker only ({len(specific)}) -- look at those takes:")
    for spread, phrase, scores in sorted(specific, reverse=True)[: args.top]:
        kind = truth[phrase].kind
        print(f"  spread {spread:4.0%} [{kind:8s}] {phrase:46s} " +
              " ".join(f"{x}={scores.get(x,0):.0%}" for x in labels))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
