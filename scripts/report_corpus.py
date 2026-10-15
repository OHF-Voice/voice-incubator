#!/usr/bin/env python3
"""Summarize a bench_corpus.py run: per channel, per prompt, per failure.

Rows are prompts that expect a command (kind ``command``; ``grammar`` and
``wildcard`` in runs of the old sentence-trigger chain) or must be refused
(``oov``). "chain" scores recognition on its own; "app" what the app would do,
where a call the wake verifier rejects is hung up before the command is heard.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path


def pct(part: int, whole: int) -> str:
    return f"{100.0 * part / whole:5.1f}%" if whole else "    - "


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("results", type=Path)
    parser.add_argument("--mode", choices=("raw", "matched", "both"), default="both")
    parser.add_argument("--top", type=int, default=15)
    args = parser.parse_args()

    rows = [json.loads(line) for line in args.results.read_text().splitlines() if line]
    modes = ("raw", "matched") if args.mode == "both" else (args.mode,)

    for mode in modes:
        subset = [r for r in rows if r["mode"] == mode]
        by_channel: dict[str, list[dict]] = defaultdict(list)
        for row in subset:
            by_channel[row["channel"]].append(row)

        print(f"\n=== {mode} ({len(subset)} decisions) ===")
        header = (
            f"{'channel':24s} {'gain':>7s} {'wake':>7s} {'verify':>7s} "
            f"{'in-vocab':>9s} {'wrong':>7s} {'OOV rej':>8s} {'chain':>7s} {'app':>7s}"
        )
        print(header)
        print("-" * len(header))

        ranked = []
        for channel, items in by_channel.items():
            woke = [r for r in items if r["wake_detected"]]
            verified = [r for r in woke if r.get("verified")]
            vocab = [r for r in items if r["kind"] in ("command", "grammar", "wildcard")]
            oov = [r for r in items if r["kind"] == "oov"]
            correct = [r for r in vocab if r.get("outcome") == "correct"]
            wrong = [r for r in vocab if r.get("outcome") in ("wrong", "wildcard_other")]
            oov_rejected = [
                r
                for r in oov
                if not r["wake_detected"] or r.get("outcome") == "rejected"
            ]
            # What the app would actually do: a call the verifier rejects is
            # hung up before the command is ever decoded.
            app_correct = [r for r in correct if r.get("verified")]
            app_refused = [
                r
                for r in oov
                if not r["wake_detected"]
                or not r.get("verified")
                or r.get("outcome") == "rejected"
            ]
            # Everything the chain has to get right: say the in-vocabulary
            # commands, refuse the rest.
            overall = (len(correct) + len(oov_rejected)) / len(items)
            app = (len(app_correct) + len(app_refused)) / len(items)
            ranked.append(
                (
                    overall,
                    channel,
                    items[0]["gain"],
                    len(woke) / len(items),
                    len(verified) / len(woke) if woke else 0.0,
                    len(correct) / len(vocab) if vocab else 0.0,
                    len(wrong) / len(vocab) if vocab else 0.0,
                    (len(oov_rejected) / len(oov)) if oov else None,
                    app,
                )
            )

        for overall, channel, gain, wake, ver, good, bad, rej, app in sorted(
            ranked, key=lambda r: -r[-1]
        ):
            print(
                f"{channel:24s} {gain:7.2f} {wake:6.1%} {ver:7.1%} "
                f"{good:8.1%} {bad:6.1%} "
                f"{'      -' if rej is None else format(rej, '7.1%')} "
                f"{overall:6.1%} {app:6.1%}"
            )

    print("\n=== best channel per device (level-matched) ===")
    matched = [r for r in rows if r["mode"] == "matched"]
    per_channel: dict[str, list[dict]] = defaultdict(list)
    for row in matched:
        per_channel[row["channel"]].append(row)
    best: dict[str, tuple[float, str, float]] = {}
    for channel, items in per_channel.items():
        good = sum(
            1
            for r in items
            if (r.get("outcome") == "correct" and r.get("verified"))
            or (
                r["kind"] == "oov"
                and (
                    not r["wake_detected"]
                    or not r.get("verified")
                    or r.get("outcome") == "rejected"
                )
            )
        )
        score = good / len(items)
        woke = sum(1 for r in items if r["wake_detected"]) / len(items)
        device = items[0]["device"]
        if device not in best or score > best[device][0]:
            best[device] = (score, channel, woke)
    for device, (score, channel, woke) in sorted(best.items(), key=lambda kv: -kv[1][0]):
        print(f"{device:22s} {channel.rsplit('.', 1)[1]:8s} wake {woke:6.1%}  overall {score:6.1%}")

    print("\n=== hardest prompts (level-matched, all channels) ===")
    by_phrase: dict[str, list[dict]] = defaultdict(list)
    for row in matched:
        by_phrase[row["phrase"]].append(row)
    scored = []
    for phrase, items in by_phrase.items():
        kind = items[0]["kind"]
        if kind == "oov":
            good = [
                r for r in items if not r["wake_detected"] or r.get("outcome") == "rejected"
            ]
        else:
            good = [r for r in items if r.get("outcome") == "correct"]
        scored.append((len(good) / len(items), kind, phrase, items))
    for rate, kind, phrase, items in sorted(scored)[: args.top]:
        # A refusal is a success only for a prompt that must be refused.
        ok = ("correct", "rejected") if kind == "oov" else ("correct",)
        failures = [r for r in items if r.get("outcome") not in ok]
        missed = sum(1 for r in failures if not r["wake_detected"])
        heard = Counter(
            r.get("text") or r.get("greedy") or "?"
            for r in failures
            if r["wake_detected"]
        )
        top = ", ".join(f"{t!r}x{n}" for t, n in heard.most_common(2))
        if missed:
            top = f"no wake x{missed}" + (f", {top}" if top else "")
        print(f"{rate:6.1%} [{kind:8s}] {phrase:52s} {top or '-'}")

    print("\n=== what was accepted for a prompt that must be refused ===")
    bad = Counter(
        (r["phrase"], r["text"])
        for r in matched
        if r["kind"] == "oov" and r.get("accepted")
    )
    for (phrase, text), count in bad.most_common(args.top):
        print(f"{count:4d}  {phrase!r}\n      -> {text!r}")
    if not bad:
        print("none")

    print("\n=== verdicts, where the wake word was detected ===")
    for mode in modes:
        subset = [r for r in rows if r["mode"] == mode and r["wake_detected"]]
        verdicts = Counter(r.get("verdict", r.get("outcome")) for r in subset)
        print(f"{mode:8s} " + "  ".join(f"{k} {pct(n, len(subset))}" for k, n in verdicts.most_common()))

    print("\n=== wake word ===")
    for mode in modes:
        subset = [r for r in rows if r["mode"] == mode]
        woke = sum(1 for r in subset if r["wake_detected"])
        ver = sum(1 for r in subset if r.get("verified"))
        rejected = Counter(
            r["rejected_by"] for r in subset if r["wake_detected"] and not r.get("verified")
        )
        print(
            f"{mode:8s} detected {pct(woke, len(subset))}  "
            f"verified {pct(ver, woke)} of those  "
            f"rejections {dict(rejected)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
