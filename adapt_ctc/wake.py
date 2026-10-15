"""Second-stage wake word verification, and commands taken from whole calls.

The device's detector fires on the wake word and on things that sound like it
("okay Natalie"). The verifier scores the call's pre-roll -- the audio up to
the detection -- against the wake phrase and against confusables, using the
same CTC acoustics as the commands. A call is accepted when the wake word's
evidence -- summed over every alignment and spelling -- is not beaten by any
confusable's by more than a margin.

Phrases are spelled as the model writes them. Parakeet has no word "Nabu", so
it writes "Naboo", "Nabu" or "now"; all are spellings of the wake word, not
confusables. Confusables should be words the model can write: near-homophone
nonsense ("okay napu") is just another spelling of the wake word to it.

A whole call is recognized in two passes: the pre-roll alone, for an early
verdict, then the call from the wake phrase on, in which the wake phrase is
found again and the command is recognized from the frames after it. There is
no cut in the audio, so no syllable of the wake word leaks into the command.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

from .acoustic import SAMPLE_RATE, CtcModel, Vocabulary
from .config import WakeConfig
from .graph import Meaning, PhraseGraph
from .matcher import Recognition, Recognizer
from .spotter import _logsumexp, forward_numpy, relative_log_probs, viterbi
from .text import name_units

WAKE = "wake"
CONFUSABLE = "confusable"

LEAD = 0.5
"""Seconds kept before the wake phrase when decoding the rest of the call.
With less, the model loses its footing: from 0.1 s before "okay nabu" it
heard "Okay, nowab do you turn on" where the whole call reads "Okay, Nab
turn on"."""

SPLIT_SLACK = 2.0
"""Nats. The command starts after the latest wake placement, of any spelling,
within this much of the best one: "okay now" may fit best while "okay nabu",
nearly as good, also covers the "-bu" the model heard afterwards."""


@dataclass
class WakeResult:
    accepted: bool
    reason: str = ""
    phrase: str | None = None
    cost: float = float("inf")
    """Total cost (nats) of the best wake phrase placement."""

    cost_per_token: float = float("inf")
    start: int = 0
    end: int = 0
    """Frames of the wake phrase (inclusive)."""

    confusable: str | None = None
    confusable_cost: float = float("inf")
    margin: float = float("inf")
    """Wake evidence minus the strongest confusable's (nats): negative when the
    confusable is better supported. Summed or best-path, per ``scoring``."""

    evidence: float | None = None
    """Summed log-evidence for the wake word, relative to the greedy path."""

    frame_seconds: float = 0.08


@dataclass
class CallResult:
    wake: WakeResult
    command: Recognition | None
    timings: dict[str, float] = field(default_factory=dict)


class WakeVerifier:
    def __init__(self, config: WakeConfig, vocab: Vocabulary, language: str = "en") -> None:
        self.config = config
        self.vocab = vocab
        graph = PhraseGraph(vocab)
        for kind, phrases in ((WAKE, config.phrases), (CONFUSABLE, config.confusables)):
            for phrase in phrases:
                for units in name_units(phrase, language):
                    graph.add(units, Meaning("keyword", phrase, name_type=kind), flexible=True)
        self.unspeakable = graph.unspeakable
        self.graph = graph.compile()
        kinds = [m[0].name_type for m in self.graph.meanings]
        self._wake = np.asarray([i for i, k in enumerate(kinds) if k == WAKE], dtype=np.int64)
        self._confusable = np.asarray([i for i, k in enumerate(kinds) if k == CONFUSABLE], dtype=np.int64)

    def verify_log_probs(
        self, log_probs: np.ndarray, frame_seconds: float, max_end: int | None = None
    ) -> WakeResult:
        """Verdict on log-probabilities whose first frames hold the wake word.
        ``max_end`` limits where the wake phrase may end."""
        config = self.config
        rel_tokens, rel_blank = relative_log_probs(log_probs, self.vocab)
        score, start, tokens = viterbi(self.graph, rel_tokens, rel_blank)
        if max_end is not None:
            score = score[: max_end + 1]
        penalty = np.where(np.isfinite(score), -score.astype(np.float64), np.inf)

        result = WakeResult(accepted=False, frame_seconds=frame_seconds)
        best = self._best(penalty, self._wake)
        if best is None:
            result.reason = "no wake phrase"
            return result
        end, column = best
        result.phrase = self.graph.meanings[column][0].phrase
        result.cost = float(penalty[end, column])
        result.cost_per_token = result.cost / max(1, int(tokens[end, column]))
        result.start, result.end = int(start[end, column]), end
        # Where the wake word ends, for the split: the latest good placement.
        block = penalty[:, self._wake]
        near = np.nonzero((block <= result.cost + SPLIT_SLACK).any(axis=1))[0]
        if near.size:
            result.end = max(end, int(near.max()))

        if config.scoring == "sum":
            self._sum_margin(result, rel_tokens, rel_blank, max_end)
        else:
            rival = self._best(penalty, self._confusable)
            if rival is not None:
                result.confusable = self.graph.meanings[rival[1]][0].phrase
                result.confusable_cost = float(penalty[rival])
                result.margin = result.confusable_cost - result.cost

        # A confusable that wins says more about a rejection than a poor fit.
        if result.margin < config.min_margin:
            result.reason = (
                f"'{result.confusable}' is better supported than the wake word by "
                f"{-result.margin:.2f} (limit {-config.min_margin:g})"
            )
        elif (
            config.max_cost_per_token is not None
            and result.cost_per_token > config.max_cost_per_token
        ):
            result.reason = (
                f"'{result.phrase}' fits poorly: {result.cost_per_token:.2f}/token "
                f"(limit {config.max_cost_per_token:g})"
            )
        else:
            result.accepted = True
        return result

    def _sum_margin(
        self,
        result: WakeResult,
        rel_tokens: np.ndarray,
        rel_blank: np.ndarray,
        max_end: int | None,
    ) -> None:
        """Margin on summed evidence: every alignment and spelling of the wake
        word pooled at each end frame, against each confusable's own sum."""
        evidence = forward_numpy(self.graph, rel_tokens, rel_blank)
        if max_end is not None:
            evidence = evidence[: max_end + 1]
        result.evidence = float(_logsumexp(evidence[:, self._wake], axis=1).max())
        if self._confusable.size:
            per_confusable = evidence[:, self._confusable].max(axis=0)
            strongest = int(per_confusable.argmax())
            result.confusable = self.graph.meanings[self._confusable[strongest]][0].phrase
            result.confusable_cost = -float(per_confusable[strongest])
            result.margin = result.evidence + result.confusable_cost

    @staticmethod
    def _best(penalty: np.ndarray, columns: np.ndarray) -> tuple[int, int] | None:
        """Placement with the lowest total cost among these phrases."""
        if columns.size == 0 or penalty.shape[0] == 0:
            return None
        block = penalty[:, columns]
        end, index = np.unravel_index(np.argmin(block), block.shape)
        if not np.isfinite(block[end, index]):
            return None
        return int(end), int(columns[index])


def recognize_call(
    recognizer: Recognizer, verifier: WakeVerifier, audio: np.ndarray
) -> CallResult:
    """Verify the wake word in the pre-roll, then recognize the command after it."""
    model: CtcModel = recognizer.model
    window = int(verifier.config.window * SAMPLE_RATE)

    started = time.perf_counter()
    log_probs, frame_seconds = model.log_probs(audio[:window])
    early = time.perf_counter()
    wake = verifier.verify_log_probs(log_probs, frame_seconds)
    verified = time.perf_counter()
    timings = {"wake acoustic": early - started, "wake verify": verified - early}
    if not wake.accepted:
        return CallResult(wake, None, timings)

    # The rest of the call, from just before the wake phrase: find the phrase
    # again (its end may lie past the pre-roll) and take the command after it.
    offset = max(0, int((wake.start * frame_seconds - LEAD) * SAMPLE_RATE))
    log_probs, frame_seconds = model.log_probs(audio[offset:])
    decoded = time.perf_counter()
    shift = int(round(offset / SAMPLE_RATE / frame_seconds))
    latest = int(LEAD / frame_seconds) + (wake.end - wake.start + 1) + int(0.5 / frame_seconds)
    again = verifier.verify_log_probs(log_probs, frame_seconds, max_end=latest)
    end = again.end if again.phrase is not None else max(0, wake.end - shift)
    command = recognizer.recognize_log_probs(log_probs[end + 1 :], frame_seconds)
    command.timings = {"acoustic": decoded - verified, **command.timings}
    cut = offset + int(round((end + 1) * frame_seconds * SAMPLE_RATE))
    recognizer.match_examples(command, audio[cut:])
    wake.end = end + shift
    return CallResult(wake, command, {**timings, **command.timings})


def main(argv: list[str] | None = None) -> int:
    """``adapt-ctc-wake CONFIG GENUINE... --false FALSE...``: verifier accuracy and speed."""
    import argparse
    import json
    import os
    import statistics
    import sys
    from pathlib import Path

    from .audio import load_wav
    from .cli import DEFAULT_MODEL
    from .config import ConfigError, load_config
    from .report import wake_json

    parser = argparse.ArgumentParser(
        prog="adapt-ctc-wake",
        description="Verify the wake word on calls that have it and on false wakes.",
    )
    parser.add_argument("config", type=Path)
    parser.add_argument("genuine", type=Path, nargs="*", help="Calls that start with the wake word")
    parser.add_argument("--false", dest="false", type=Path, nargs="+", default=[], help="False wakes")
    parser.add_argument(
        "--model", type=Path, default=Path(os.environ.get("ADAPT_CTC_MODEL", DEFAULT_MODEL))
    )
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--all", action="store_true", help="List every file, not just the errors")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except (ConfigError, OSError) as err:
        print(f"adapt-ctc-wake: {err}", file=sys.stderr)
        return 2
    if config.wake is None:
        print(f"adapt-ctc-wake: {args.config} has no `wake` section", file=sys.stderr)
        return 2

    started = time.perf_counter()
    model = CtcModel(args.model, num_threads=args.threads)
    loaded = time.perf_counter() - started
    verifier = WakeVerifier(config.wake, model.vocab, config.language)
    window = int(config.wake.window * SAMPLE_RATE)

    rows = []
    for label, files in (("genuine", args.genuine), ("false", args.false)):
        for path in files:
            audio = load_wav(path)[:window]
            t0 = time.perf_counter()
            log_probs, frame_seconds = model.log_probs(audio)
            t1 = time.perf_counter()
            wake = verifier.verify_log_probs(log_probs, frame_seconds)
            t2 = time.perf_counter()
            correct = wake.accepted == (label == "genuine")
            rows.append((label, path, wake, correct, t1 - t0, t2 - t1))

    if args.json:
        print(
            json.dumps(
                {
                    "model_load_seconds": round(loaded, 3),
                    "results": [
                        {
                            "file": str(path),
                            "label": label,
                            "correct": correct,
                            **wake_json(wake),
                            "acoustic_ms": round(acoustic * 1000, 2),
                            "verify_ms": round(verify * 1000, 3),
                        }
                        for label, path, wake, correct, acoustic, verify in rows
                    ],
                },
                indent=2,
            )
        )
        return 0

    from .report import render_wake

    for label, path, wake, correct, _, _ in rows:
        if args.all or not correct:
            mark = "ok" if correct else "XX"
            print(f"{mark} {label:7s} {path.name}\n     {render_wake(wake)}")
    print()
    for label in ("genuine", "false"):
        items = [r for r in rows if r[0] == label]
        if not items:
            continue
        accepted = sum(r[2].accepted for r in items)
        margins = [r[2].margin for r in items if np.isfinite(r[2].margin)]
        costs = [r[2].cost_per_token for r in items if np.isfinite(r[2].cost_per_token)]
        verb = "accepted" if label == "genuine" else "rejected"
        good = accepted if label == "genuine" else len(items) - accepted
        print(
            f"{label:7s}: {good}/{len(items)} {verb}"
            + (
                f"   margin min {min(margins):+.2f} median {statistics.median(margins):+.2f} max {max(margins):+.2f}"
                if margins
                else ""
            )
            + (
                f"   cost/token median {statistics.median(costs):.2f} max {max(costs):.2f}"
                if costs
                else ""
            )
        )
    if rows:
        acoustic = [r[4] * 1000 for r in rows]
        verify = [r[5] * 1000 for r in rows]
        print(
            f"\ntiming ({config.wake.window:g} s window, {args.threads} threads): "
            f"acoustic median {statistics.median(acoustic):.0f} ms (max {max(acoustic):.0f}), "
            f"verify median {statistics.median(verify):.2f} ms (max {max(verify):.2f}); "
            f"model load {loaded * 1000:.0f} ms"
        )
    return 0
