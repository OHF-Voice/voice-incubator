#!/usr/bin/env python3
"""Kaldi mode (voice_labs/kaldi_mode.py) on the office-multicap corpus.

Kaldi mode hears only the home's example sentences, word for word. Here the
examples are the corpus's own command sentences (tests/corpus_expected.yaml,
those of a command): each recording -- the whole call, "okay nabu" in it,
which the grammar's noise takes -- is decoded against them, matched as typed
by the app (adapt-ctc's home-extras config) and scored as grammar_mode.py
scores it. The corpus's sentences that are no command stay out of the
grammar: they must be refused. ``--overheard`` also decodes calls cut at the
utterances of talk played into the room (``neg-noise01``): what a false wake
would hear.

    scripts/kaldi_mode.py bench ~/.../office-multicap/{mike,jenn,natalie,oliver}-merged \\
        --overheard ~/.../office-multicap/neg-noise01-merged
    scripts/kaldi_mode.py report /tmp/kaldi_mode.jsonl

``report`` says what other ``kaldi_max_cost`` values would have done.
Guessed pronunciations need phonetisaurus and the model's g2p.fst in
``models/`` (voice_labs/downloads.py fetches them for the image); without
them, sentences with words the dictionary lacks are left out.

What it found: DESIGN.md ("Kaldi mode").

An earlier prototype decoded against "okay nabu" and any of the commands'
words: 40.3% of mike-merged's commands right, against Parakeet's 78.9%.
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

from grammar_mode import COMMANDS, _engine, _verdict, jobs_of

WORK = Path(tempfile.gettempdir()) / "kaldi-mode-bench"
OVERHEARD_SECONDS = 5.0
OVERHEARD_CALLS = 400

_W: dict = {}


def corpus_sentences() -> list[str]:
    """The corpus's sentences of a command, numbers spelled as said."""
    from adapt_ctc.score import load_manifest  # pylint: disable=import-outside-toplevel

    truth = load_manifest(ROOT / "tests/corpus_expected.yaml").sentences
    return sorted(phrase for phrase, expectation in truth.items() if expectation.intent is not None)


def kaldi(work: Path):
    from voice_labs.kaldi_mode import (
        KaldiMode,  # pylint: disable=import-outside-toplevel
    )

    mode = KaldiMode(ROOT, work)
    if os.environ.get("KALDI_G2P"):  # elsewhere than models/ (--g2p)
        mode.pronouncer.g2p = Path(os.environ["KALDI_G2P"])
    if os.environ.get("KALDI_PHONETISAURUS"):
        mode.pronouncer.phonetisaurus = Path(os.environ["KALDI_PHONETISAURUS"])
    return mode


def build(work: Path) -> None:
    """The graphs, once, before the workers start their decoders."""
    from voice_labs.kaldi_mode import (  # pylint: disable=import-outside-toplevel
        Sentences,
        words_of,
    )

    mode = kaldi(work)
    heard = Sentences({"corpus": [w for s in corpus_sentences() for w in words_of(s, "en")]})
    built = mode.build(heard, "en", {})
    print(f"{built.sentences} sentences; built in {built.seconds:.1f} s; guessed: "
          f"{sorted(w for w, e in built.words.items() if e.source == 'guessed')}; can't say: {built.unspeakable}", flush=True)
    mode.close()


def _init(work: str) -> None:
    from voice_labs.kaldi_mode import (  # pylint: disable=import-outside-toplevel
        Sentences,
        words_of,
    )

    _W["engine"] = _engine(COMMANDS, tdt=False)
    mode = kaldi(Path(work))
    mode.build(Sentences({"corpus": [w for s in corpus_sentences() for w in words_of(s, "en")]}), "en", {})
    _W["kaldi"] = mode


def _one(job: tuple) -> dict:
    import numpy as np  # pylint: disable=import-outside-toplevel
    from _corpus import read_wav  # pylint: disable=import-outside-toplevel

    from voice_labs.engine import Satellite  # pylint: disable=import-outside-toplevel

    path, phrase, expect, start, seconds = job
    signal = read_wav(Path(path)).astype(np.float32) / 32768.0
    if start is not None:
        signal = signal[int(start * 16000): int((start + seconds) * 16000)]
    heard = _W["kaldi"].decode(signal)
    row = {"file": path, "phrase": phrase, "expect": expect, "start": start, "text": heard.text,
           "margin": round(heard.margin, 4), "background": heard.background_words, "seconds": round(heard.seconds, 3),
           "audio": round(len(signal) / 16000, 2)}
    if heard.text:
        decision = _W["engine"].recognize_text(heard.text, Satellite("bench", "bench"))
        if expect == "overheard":
            row["as_typed"] = "false accept" if decision.command is not None else "correct"
        else:
            row["as_typed"] = _verdict(expect, decision)
    return row


def overheard_jobs(session: Path, channel: str = "capture1-vpe") -> list[tuple]:
    """Calls cut at the utterances of the talk played into the room."""
    utterances = [
        line for line in (json.loads(raw) for raw in (session / "playback.jsonl").read_text().splitlines())
        if line.get("type") == "utterance" and len(str(line.get("words") or "").split()) >= 3
    ]
    step = max(1, len(utterances) // OVERHEARD_CALLS)
    wav = session / "channels" / channel / "agc.wav"
    return [(str(wav), str(u["words"]), "overheard", float(u["t0"]), OVERHEARD_SECONDS) for u in utterances[::step][:OVERHEARD_CALLS]]


def bench(sessions: list[Path], channels: list[str], overheard: Path | None, out: Path, workers: int, work: Path) -> None:
    build(work)
    jobs = [(*job, None, None) for job in jobs_of(sessions, channels, None)]
    if overheard is not None:
        jobs += overheard_jobs(overheard)
    print(f"{len(jobs)} recordings, {workers} workers", flush=True)
    rows = []
    with out.open("w") as handle, ProcessPoolExecutor(workers, initializer=_init, initargs=(str(work),)) as pool:
        for row in pool.map(_one, jobs, chunksize=4):
            rows.append(row)
            handle.write(json.dumps(row) + "\n")
    report(rows)


def _verdict_at(row: dict, max_cost: float) -> str:
    """The verdict had ``max_cost`` been the setting."""
    taken = bool(row["text"]) and row["margin"] <= max_cost and row.get("as_typed") is not None
    if row["expect"] == "overheard":
        return "false accept" if taken and row["as_typed"] == "false accept" else "correct"
    if taken:
        return row["as_typed"]
    return "correct" if row["expect"] is not None and row["expect"][0] is None else "missed"


def report(rows: list[dict], costs: tuple[float, ...] = (-0.15, -0.1, -0.05, 0.0, 0.05, 0.1, 0.2, 99.0)) -> None:
    calls = [r for r in rows if r["expect"] not in (None, "overheard")]
    commands = [r for r in calls if r["expect"][0] is not None]
    refuse = [r for r in calls if r["expect"][0] is None]
    overheard = [r for r in rows if r["expect"] == "overheard"]
    bad = {"wrong intent", "wrong slots", "false accept"}
    print(f"{len(commands)} commands, {len(refuse)} to refuse, {len(overheard)} overheard")
    for cost in costs:
        verdicts = [_verdict_at(r, cost) for r in commands]
        print(f"  max {cost:+.2f}: right {verdicts.count('correct') / max(1, len(commands)):.1%}, "
              f"wrong {sum(v in bad for v in verdicts) / max(1, len(commands)):.1%}; "
              f"refused {sum(_verdict_at(r, cost) == 'correct' for r in refuse) / max(1, len(refuse)):.1%}; "
              f"overheard ran {sum(_verdict_at(r, cost) != 'correct' for r in overheard)} of {len(overheard)}")
    seconds = sorted(r["seconds"] / max(0.1, r["audio"]) for r in rows)
    if seconds:
        print(f"  decoding: median {seconds[len(seconds) // 2]:.3f} s a second of audio")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="what", required=True)
    b = sub.add_parser("bench")
    b.add_argument("sessions", type=Path, nargs="+")
    b.add_argument("--channels", nargs="+", default=["pc-vpe.agc", "capture1-vpe.agc", "capture2-vpe.agc", "capture3-vpe.agc"])
    b.add_argument("--overheard", type=Path, help="neg-noise01-merged: calls cut at its talk")
    b.add_argument("--out", type=Path, default=Path(tempfile.gettempdir()) / "kaldi_mode.jsonl")
    b.add_argument("--workers", type=int, default=8)
    b.add_argument("--work", type=Path, default=WORK, help="where the graphs are built")
    b.add_argument("--g2p", type=Path, help="the model's g2p.fst, when not in models/")
    b.add_argument("--phonetisaurus", type=Path, help="phonetisaurus, when not in models/wake-kaldi-tools")
    r = sub.add_parser("report")
    r.add_argument("jsonl", type=Path)
    args = parser.parse_args()
    if args.what == "bench":
        if args.g2p:
            os.environ["KALDI_G2P"] = str(args.g2p)
        if args.phonetisaurus:
            os.environ["KALDI_PHONETISAURUS"] = str(args.phonetisaurus)
        started = time.monotonic()
        bench(args.sessions, args.channels, args.overheard, args.out, args.workers, args.work)
        print(f"{time.monotonic() - started:.0f} s")
    else:
        report([json.loads(line) for line in args.jsonl.read_text().splitlines()])
    return 0


if __name__ == "__main__":
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    raise SystemExit(main())
