#!/usr/bin/env python3
"""The app with and without hearing calls as the home's own sentences.

adapt_ctc/grammar.py makes every sentence the commands say into one graph --
each keyword command's words in any order, with its required ones, its names
and numbers, and fillers; each example as written; anything (at a cost)
before and after -- and searches Parakeet's CTC evidence for the best of them
(adapt_ctc/_grammar.c), from just before the wake word's end
(engine.SENTENCE_LEAD_IN). A sentence within ``settings.grammar_max_cost``
nats of what the model heard on its own (``grammar.path_cost``) is matched as
typed; anything else goes to the passes after it (``Engine.hear_sentence``).

    scripts/grammar_mode.py build                         # the graph's size and build time
    scripts/grammar_mode.py decode call.wav
    scripts/grammar_mode.py bench ~/.../office-multicap/mike-merged [more sessions]
    scripts/grammar_mode.py report /tmp/grammar_mode.jsonl --max-cost 12

``bench`` runs each recording of office-multicap sessions' VPEs (AGC)
through ``Engine.recognize_call`` twice, the sentences on and off, and scores
both against tests/corpus_expected.yaml (command and slots, or refused).
It keeps each call's sentence, its cost and what matching it as typed came
to, so ``report`` can say what another ``--max-cost`` would have done.

What it found, with adapt-ctc's home-extras config: see DESIGN.md ("Heard as
one of the home's sentences first").
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

CTC = ROOT / "models/speech-to-phrase/stt_en_parakeet_tdt_ctc_110m-int8"
TDT = ROOT / "models/parakeet-tdt-int8"
COMMANDS = ROOT / "adapt_ctc/examples/home-extras.yaml"


def _engine(commands: Path, tdt: bool = True):
    from bench_corpus import load_commands  # pylint: disable=import-outside-toplevel

    from voice_labs import languages  # pylint: disable=import-outside-toplevel
    from voice_labs.engine import Engine  # pylint: disable=import-outside-toplevel

    languages.use("en")
    engine = Engine(CTC, TDT if tdt else None, threads=1)
    engine.build(load_commands(commands))
    return engine


def build(commands: Path) -> None:
    from adapt_ctc.grammar import build as build_graph  # pylint: disable=import-outside-toplevel

    engine = _engine(commands, tdt=False)
    recognizer = engine.recognizer
    started = time.monotonic()
    words = build_graph(recognizer.config)
    word_seconds = time.monotonic() - started
    graph = recognizer.grammar
    print(f"{len(graph.words)} words, {len(words.arcs)} word arcs ({word_seconds:.2f} s); "
          f"{graph.num_states} states, {graph.num_arcs} arcs in the model's pieces, {graph.nbytes / 1e6:.1f} MB; "
          f"recognizer built in {engine.build_seconds:.2f} s")
    if graph.unspeakable:
        print(f"no pieces spell: {' '.join(graph.unspeakable)}")
    for sentence in words.sample(10):
        print("  e.g.", sentence)


# ------------------------------------------------------------------ the bench

_W: dict = {}


def _init(commands: str) -> None:
    _W["engine"] = _engine(Path(commands))


def _verdict(expect, decision) -> str:
    from adapt_ctc.score import _slots  # pylint: disable=import-outside-toplevel

    from voice_labs.commands import command_of  # pylint: disable=import-outside-toplevel

    got = decision.intent if decision is not None else None
    if expect is None:
        return "unknown"
    intent, slots = expect
    if intent is None:
        return "false accept" if got is not None else "correct"
    if got is None:
        return "missed"
    if command_of(got.intent) != intent:
        return "wrong intent"
    if slots is not None and _slots(got.slots) != _slots(slots):
        return "wrong slots"
    return "correct"


def _one(job: tuple) -> dict:
    try:
        return _scored(job)
    except Exception:  # noqa: BLE001 - one recording's failure is its row's
        import traceback  # pylint: disable=import-outside-toplevel

        return {"file": job[0], "phrase": job[1], "error": traceback.format_exc()}


def _scored(job: tuple) -> dict:
    import numpy as np  # pylint: disable=import-outside-toplevel

    from _corpus import read_wav  # pylint: disable=import-outside-toplevel

    from voice_labs.engine import Satellite  # pylint: disable=import-outside-toplevel

    path, phrase, expect = job
    engine, satellite = _W["engine"], Satellite("bench", "bench")
    audio = read_wav(Path(path)).astype(np.float32) / 32768.0
    row: dict = {"file": path, "phrase": phrase, "expect": expect}
    recognizer = engine.recognizer
    grammar = recognizer.grammar
    for name in ("on", "off"):
        recognizer.grammar = grammar if name == "on" else None
        if hasattr(engine.model, "_last"):
            engine.model._last = None  # pylint: disable=protected-access  # each its own encoder run
        started = time.monotonic()
        decision, _split = engine.recognize_call(audio, 0.0, 2.5, satellite)
        row[name] = {"verdict": _verdict(expect, decision), "ms": round((time.monotonic() - started) * 1000, 1),
                     "matched_by": decision.matched_by if decision.command else None}
        if name == "on":
            row["sentence"] = decision.sentence
            row["sentence_ms"] = round(decision.timings.get("sentence", 0.0) * 1000, 1)
    recognizer.grammar = grammar
    sentence = row["sentence"] or {}
    if sentence.get("text"):
        # What the sentence comes to as typed, taken or not: for other costs.
        decision = engine.recognize_text(sentence["text"], satellite)
        usable = decision.command is not None or decision.refused is not None
        row["as_typed"] = _verdict(expect, decision) if usable else None
    return row


def jobs_of(sessions: list[Path], channels: list[str], limit: int | None) -> list[tuple]:
    from _corpus import corrections, session_key  # pylint: disable=import-outside-toplevel

    from adapt_ctc.score import _key, load_manifest  # pylint: disable=import-outside-toplevel

    truth = load_manifest(ROOT / "tests/corpus_expected.yaml").sentences
    fixes = corrections(ROOT / "tests/corpus_corrections.csv")
    jobs = []
    for session in sessions:
        speaker = session_key(session)
        index = json.loads((session / "prompts/index.json").read_text())
        for prompt in index["phrases"][:limit] if limit else index["phrases"]:
            phrase = _key(prompt["phrase"].split(", ", 1)[1])
            fix = fixes.get((speaker, prompt["index"]))
            if fix is not None and not fix.usable:
                continue
            if fix is not None and fix.verdict == "fix":
                phrase = _key(fix.phrase.split(", ", 1)[-1])
            expectation = truth.get(phrase)
            expect = None if expectation is None else (expectation.intent, expectation.slots)
            for channel in channels:
                if channel in prompt["files"]:
                    jobs.append((str(session / "prompts" / prompt["files"][channel]), phrase, expect))
    return jobs


def bench(sessions: list[Path], channels: list[str], out: Path, workers: int, limit: int | None,
          commands: Path, max_cost: float | None) -> None:
    jobs = jobs_of(sessions, channels, limit)
    print(f"{len(jobs)} recordings, {workers} workers", flush=True)
    rows = []
    with out.open("w") as handle, ProcessPoolExecutor(workers, initializer=_init, initargs=(str(commands),)) as pool:
        for row in pool.map(_one, jobs, chunksize=4):
            rows.append(row)
            handle.write(json.dumps(row) + "\n")
    report(rows, max_cost)


def _at(row: dict, max_cost: float) -> str:
    """The verdict had the sentence been taken at no more than ``max_cost``."""
    sentence = row["sentence"] or {}
    if sentence.get("cost") is not None and sentence["cost"] <= max_cost and row.get("as_typed") is not None:
        return row["as_typed"]
    return row["off"]["verdict"]


def report(rows: list[dict], max_cost: float | None) -> None:
    failed = [r for r in rows if "error" in r]
    if failed:
        print(f"{len(failed)} failed, the first: {failed[0]['phrase']!r}\n{failed[0]['error']}")
    rows = [r for r in rows if "error" not in r]
    scored = [r for r in rows if r["expect"] is not None]
    within = [r for r in scored if r["expect"][0] is not None]
    without = [r for r in scored if r["expect"][0] is None]
    print(f"{len(scored)} scored of {len(rows)} ({len(within)} commands it has, {len(without)} it must refuse); "
          f"taken as sentences: {sum(bool((r['sentence'] or {}).get('taken')) for r in rows)}")
    verdicts = {"sentences off": lambda r: r["off"]["verdict"], "sentences on": lambda r: r["on"]["verdict"]}
    if max_cost is not None:
        verdicts[f"at {max_cost:g} nats"] = lambda r: _at(r, max_cost)
    for name, verdict in verdicts.items():
        counts: dict[str, int] = {}
        for r in scored:
            counts[verdict(r)] = counts.get(verdict(r), 0) + 1
        line = ", ".join(f"{k} {v / len(scored):.1%}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))
        print(f"  {name:14s} {line}")
        print(f"  {'':14s} commands it has right {sum(verdict(r) == 'correct' for r in within) / max(1, len(within)):.1%}, "
              f"refused {sum(verdict(r) == 'correct' for r in without) / max(1, len(without)):.1%} of the rest")
    for label, values in (("recognize_call, off", [r["off"]["ms"] for r in rows]),
                          ("recognize_call, on", [r["on"]["ms"] for r in rows]),
                          ("the sentence's search", [r["sentence_ms"] for r in rows])):
        values = sorted(values)
        print(f"  {label:22s} median {values[len(values) // 2]:.0f} ms, 90% {values[int(len(values) * 0.9)]:.0f} ms")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="what", required=True)
    b = sub.add_parser("build")
    d = sub.add_parser("decode")
    d.add_argument("wav", type=Path)
    r = sub.add_parser("bench")
    r.add_argument("sessions", type=Path, nargs="+")
    r.add_argument("--channels", nargs="+", default=["pc-vpe.agc", "capture1-vpe.agc", "capture2-vpe.agc", "capture3-vpe.agc"])
    r.add_argument("--out", type=Path, default=Path(tempfile.gettempdir()) / "grammar_mode.jsonl")
    r.add_argument("--workers", type=int, default=8)
    r.add_argument("--limit", type=int, help="the first prompts of each session")
    p = sub.add_parser("report")
    p.add_argument("jsonl", type=Path)
    for each in (b, d, r):
        each.add_argument("--commands", type=Path, default=COMMANDS)
    for each in (r, p):
        each.add_argument("--max-cost", type=float, help="also what this settings.grammar_max_cost would have done")
    args = parser.parse_args()
    if args.what == "build":
        build(args.commands)
    elif args.what == "decode":
        import numpy as np  # pylint: disable=import-outside-toplevel

        from _corpus import read_wav  # pylint: disable=import-outside-toplevel

        from voice_labs.engine import Satellite  # pylint: disable=import-outside-toplevel

        engine = _engine(args.commands)
        decision, split = engine.recognize_call(read_wav(args.wav).astype(np.float32) / 32768.0, 0.0, 2.5,
                                                Satellite("bench", "bench"))
        print(json.dumps({"sentence": decision.sentence, "command": decision.command.id if decision.command else None,
                          "slots": decision.slots, "matched_by": decision.matched_by, "cut": split.cut,
                          "timings": {k: round(v * 1000, 1) for k, v in decision.timings.items()}}))
    elif args.what == "bench":
        bench(args.sessions, args.channels, args.out, args.workers, args.limit, args.commands, args.max_cost)
    else:
        report([json.loads(line) for line in args.jsonl.read_text().splitlines()], args.max_cost)
    return 0


if __name__ == "__main__":
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    raise SystemExit(main())
