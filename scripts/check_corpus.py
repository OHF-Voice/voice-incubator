#!/usr/bin/env python3
"""Check the ground truth against the sentence triggers before benching.

Every `grammar` expectation must be a sentence the triggers can produce, every
`wildcard` one must match a wildcard trigger with hassil, and no `oov` one may
match anything. A bench run on a mismatched pair of files measures the files.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _corpus import ground_truth, sentence_triggers  # noqa: E402
from voice_call.wildcards import WildcardMatcher, _expand  # noqa: E402

from hassil.parse_expression import parse_sentence  # noqa: E402


def expand_plain(templates: list[str]) -> set[str]:
    out: set[str] = set()
    for template in templates:
        for branch in _expand(parse_sentence(template).expression):
            words = [w for w in branch if isinstance(w, str)]
            if len(words) == len(branch):
                out.add(" ".join(words).lower())
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--automations",
        type=Path,
        default=Path.home() / "opt/homeassistant/config/automations.yaml",
    )
    parser.add_argument(
        "--expected", type=Path, default=Path(__file__).parent.parent / "tests/corpus_expected.tsv"
    )
    args = parser.parse_args()

    sentences = sentence_triggers(args.automations)
    plain = expand_plain(sentences.plain)
    matcher = WildcardMatcher()
    matcher.train(sentences.wildcard)
    truth = ground_truth(args.expected)

    print(
        f"{len(sentences.plain)} plain trigger(s) -> {len(plain)} sentences; "
        f"{len(sentences.wildcard)} wildcard trigger(s); {len(truth)} prompts"
    )

    problems = 0
    for expectation in truth.values():
        if expectation.kind == "grammar":
            if not expectation.accepted:
                print(f"NO ACCEPTED FORM  {expectation.phrase!r}")
                problems += 1
                continue
            missing = [a for a in expectation.accepted if a.lower() not in plain]
            if len(missing) == len(expectation.accepted):
                print(f"NOT IN GRAMMAR    {expectation.phrase!r} -> {missing}")
                problems += 1
            elif missing:
                print(f"  note: variant not in grammar {missing} ({expectation.phrase!r})")
        elif expectation.kind == "wildcard":
            for accepted in expectation.accepted:
                if accepted.lower() in plain:
                    print(f"WILDCARD IN GRAMMAR {accepted!r}")
                    problems += 1
                if matcher.match(accepted) is None:
                    print(f"NO WILDCARD MATCH {accepted!r}")
                    problems += 1
        elif expectation.kind == "oov":
            if expectation.phrase.lower() in plain:
                print(f"OOV IS IN GRAMMAR {expectation.phrase!r}")
                problems += 1
            match = matcher.match(expectation.phrase)
            if match is not None:
                print(f"OOV MATCHES WILDCARD {expectation.phrase!r} -> {match.sentence!r}")
                problems += 1
        else:
            print(f"UNKNOWN KIND {expectation.kind!r}")
            problems += 1

    print("OK" if not problems else f"{problems} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
