#!/usr/bin/env python3
"""Prototype: the command recognized as one of the home's own sentences.

adapt_ctc/grammar.py makes every sentence the commands say into one word
graph -- each keyword command's words in any order, with its required ones,
its names and numbers, and fillers; each example as written; the wake phrase
(or anything, at a cost) before and anything after -- and spells it for
Parakeet's CTC head. sherpa-onnx's FST decoder transcribes the whole call as
one of the sentences, and the app's matcher reads it as typed text. A
transcript too far from what the model heard without the graph
(``grammar.path_cost`` of the words after the wake phrase, in nats, free up
to where the app puts the wake word's end) is refused.

    scripts/grammar_mode.py build                         # the graph, in local/grammar-mode
    scripts/grammar_mode.py decode call.wav
    scripts/grammar_mode.py bench ~/.../office-multicap/mike-merged [more sessions]
    scripts/grammar_mode.py report /tmp/grammar_mode.jsonl --refuse-over 10

``bench`` scores it against the app as it is (the CTC head's keywords, the
TDT head from before the cut) on office-multicap sessions' VPEs (AGC),
command and slots as tests/corpus_expected.yaml has them (as
scripts/kaldi_mode.py does). The grammar's time is sherpa-onnx's whole
decode, its own encoder run included; ``greedy_ms`` is the same encoder with
no graph, so the search is the difference.

What it found, with adapt-ctc's home-extras config (220 words, 41k word
arcs, a 59 MB graph built in 2 s), refusing over 10 nats (chosen on
mike-merged; the other sessions held out):

                     the app   grammar mode
    mike-merged       79.2%      84.6%
    jenn-merged       95.6%      97.2%
    natalie-merged    80.4%      88.8%
    oliver-merged     62.6%      73.2%
    (held out, all)   79.5%      86.4%   commands right 76.7% -> 84.5%
                                         (89.9% before refusing), the
                                         commands it has none of refused
                                         95.4% -> 96.7%

The corpus's own sentences as the grammar (scripts/kaldi_mode.py) got 95.2%
on mike-merged: a home's grammar, every command's words in any order, is
much looser. Each optional word said at most once, in any order, was 713k
word arcs (each name and number copied into every state); free order with
repeats is what fits. On the Pi 5 (one thread) the search took ~130 ms on
top of sherpa-onnx's own encoder run (384 ms, against 251 ms for the encoder
alone and 278 ms for the app), the same accuracy as on x86 on the 200
recordings timed (89.5%), and the graph ~136 MB of memory.
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
FST_BIN = ROOT / "models/wake-kaldi-tools/openfst/bin"
WORK = ROOT / "local/grammar-mode"
COMMANDS = ROOT / "adapt_ctc/examples/home-extras.yaml"
WAKE = ["okay nabu", "okay naboo", "ok nabu", "ok naboo", "okay now"]


def build(commands: Path, work: Path) -> None:
    from bench_corpus import load_commands  # pylint: disable=import-outside-toplevel

    from adapt_ctc.acoustic import Vocabulary  # pylint: disable=import-outside-toplevel
    from adapt_ctc.config import parse_config  # pylint: disable=import-outside-toplevel
    from adapt_ctc.grammar import build as build_graph, write_fst  # pylint: disable=import-outside-toplevel

    started = time.monotonic()
    config = parse_config(load_commands(commands).config)
    graph = build_graph(config, wake=WAKE)
    fst = write_fst(graph, Vocabulary.from_tokens_txt(CTC / "tokens.txt"), work, FST_BIN)
    print(f"{len(graph.words)} words, {len(graph.arcs)} word arcs, {fst.stat().st_size / 1e6:.1f} MB "
          f"in {time.monotonic() - started:.1f} s: {fst}")
    for sentence in graph.sample(10):
        print("  e.g.", sentence)


# ------------------------------------------------------------------ the bench

_W: dict = {}


def _init(commands: str, work: str, app: bool) -> None:
    import sherpa_onnx  # pylint: disable=import-outside-toplevel
    from bench_corpus import load_commands  # pylint: disable=import-outside-toplevel

    from adapt_ctc.acoustic import CtcModel  # pylint: disable=import-outside-toplevel
    from adapt_ctc.grammar import GrammarDecoder  # pylint: disable=import-outside-toplevel
    from voice_labs import languages  # pylint: disable=import-outside-toplevel
    from voice_labs.engine import Engine  # pylint: disable=import-outside-toplevel

    languages.use("en")
    engine = Engine(CTC, TDT if app else None, threads=1)
    engine.build(load_commands(Path(commands)))
    _W.update(
        engine=engine, app=app, ctc=CtcModel(CTC), decoder=GrammarDecoder(CTC, Path(work)),
        greedy=sherpa_onnx.OfflineRecognizer.from_nemo_ctc(model=str(CTC / "model.onnx"), tokens=str(CTC / "tokens.txt")),
    )


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

    from adapt_ctc.grammar import path_cost  # pylint: disable=import-outside-toplevel
    from voice_labs.commands import command_of  # pylint: disable=import-outside-toplevel
    from voice_labs.engine import Satellite  # pylint: disable=import-outside-toplevel

    path, phrase, expect = job
    engine, satellite = _W["engine"], Satellite("bench", "bench")
    audio = read_wav(Path(path)).astype(np.float32) / 32768.0
    row: dict = {"file": path, "phrase": phrase, "expect": expect}
    if _W["app"]:
        started = time.monotonic()
        decision, _split = engine.recognize_call(audio, 0.0, 2.5, satellite)
        row["app_ms"] = round((time.monotonic() - started) * 1000)
        row["app"] = {"verdict": _verdict(expect, decision),
                      "intent": command_of(decision.intent.intent) if decision.intent else None}

    started = time.monotonic()
    words = _W["decoder"].transcribe(audio)
    row["grammar_ms"] = round((time.monotonic() - started) * 1000)
    started = time.monotonic()
    stream = _W["greedy"].create_stream()
    stream.accept_waveform(16000, audio)
    _W["greedy"].decode_stream(stream)
    row["greedy_ms"] = round((time.monotonic() - started) * 1000)
    row["greedy"] = stream.result.text

    said = [(w, mode) for w, mode in words or () if mode != "wake"]
    text = " ".join(w for w, _ in said)
    decision = engine.recognize_text(text, satellite) if text else None
    # Free up to where the app puts the wake word's end.
    _log_probs, _seconds, split = engine.place_command(audio, 0.0, 2.5)
    log_probs, seconds = _W["ctc"].log_probs(audio)
    row["cut"] = round(split.cut, 2)
    row["grammar"] = {
        "words": text if words is not None else None,
        "cost": round(path_cost(log_probs, _W["ctc"].vocab, said, int(split.cut / seconds)), 2) if said else None,
        "count": len(said),
        "verdict": _verdict(expect, decision),
        "intent": command_of(decision.intent.intent) if decision is not None and decision.intent else None,
        "slots": decision.intent.slots if decision is not None and decision.intent else {},
    }
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
          commands: Path, work: Path, app: bool, refuse_over: float) -> None:
    jobs = jobs_of(sessions, channels, limit)
    print(f"{len(jobs)} recordings, {workers} workers", flush=True)
    rows = []
    with out.open("w") as handle, ProcessPoolExecutor(workers, initializer=_init,
                                                      initargs=(str(commands), str(work), app)) as pool:
        for row in pool.map(_one, jobs, chunksize=4):
            rows.append(row)
            handle.write(json.dumps(row) + "\n")
    report(rows, refuse_over)


def _refused(row: dict, refuse_over: float) -> str:
    """The grammar's verdict with a transcript too costly refused."""
    grammar = row["grammar"]
    if grammar["cost"] is None or grammar["cost"] <= refuse_over:
        return grammar["verdict"]
    return "correct" if row["expect"] and row["expect"][0] is None else "missed"


def report(rows: list[dict], refuse_over: float) -> None:
    failed = [r for r in rows if "error" in r]
    if failed:
        print(f"{len(failed)} failed, the first: {failed[0]['phrase']!r}\n{failed[0]['error']}")
    rows = [r for r in rows if "error" not in r]
    scored = [r for r in rows if r["expect"] is not None]
    print(f"{len(scored)} scored of {len(rows)}; no sentence reached: "
          f"{sum(r['grammar']['words'] is None for r in rows)}")
    verdicts = {"grammar": lambda r: r["grammar"]["verdict"],
                f"refused over {refuse_over:g}": lambda r: _refused(r, refuse_over)}
    if "app" in rows[0]:
        verdicts = {"app": lambda r: r["app"]["verdict"], **verdicts}
    for name, verdict in verdicts.items():
        counts: dict[str, int] = {}
        for r in scored:
            counts[verdict(r)] = counts.get(verdict(r), 0) + 1
        line = ", ".join(f"{k} {v / len(scored):.1%}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))
        print(f"  {name:18s} {line}")
    for key in ("app_ms", "grammar_ms", "greedy_ms"):
        if key in rows[0]:
            ms = sorted(r[key] for r in rows)
            print(f"  {key:10s} median {ms[len(ms) // 2]}, 90% {ms[int(len(ms) * 0.9)]}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="what", required=True)
    b = sub.add_parser("build")
    b.add_argument("--commands", type=Path, default=COMMANDS)
    d = sub.add_parser("decode")
    d.add_argument("wav", type=Path)
    r = sub.add_parser("bench")
    r.add_argument("sessions", type=Path, nargs="+")
    r.add_argument("--channels", nargs="+", default=["pc-vpe.agc", "capture1-vpe.agc", "capture2-vpe.agc", "capture3-vpe.agc"])
    r.add_argument("--out", type=Path, default=Path(tempfile.gettempdir()) / "grammar_mode.jsonl")
    r.add_argument("--workers", type=int, default=8)
    r.add_argument("--limit", type=int, help="the first prompts of each session")
    r.add_argument("--commands", type=Path, default=COMMANDS)
    r.add_argument("--no-app", action="store_true", help="not the app as it is")
    p = sub.add_parser("report")
    p.add_argument("jsonl", type=Path)
    for each in (r, p):
        each.add_argument("--refuse-over", type=float, default=10.0, help="the most nats a transcript may cost")
    for each in (b, d, r):
        each.add_argument("--work", type=Path, default=WORK, help="where the graph is")
    args = parser.parse_args()
    if args.what == "build":
        build(args.commands, args.work)
    elif args.what == "decode":
        from _corpus import read_wav  # pylint: disable=import-outside-toplevel

        from adapt_ctc.grammar import GrammarDecoder  # pylint: disable=import-outside-toplevel

        audio = read_wav(args.wav) / 32768.0
        print(GrammarDecoder(CTC, args.work).transcribe(audio))
    elif args.what == "bench":
        bench(args.sessions, args.channels, args.out, args.workers, args.limit, args.commands, args.work,
              not args.no_app, args.refuse_over)
    else:
        report([json.loads(line) for line in args.jsonl.read_text().splitlines()], args.refuse_over)
    return 0


if __name__ == "__main__":
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    raise SystemExit(main())
