#!/usr/bin/env python3
"""Kaldi mode (voice_labs/kaldi_mode.py) on the office-multicap corpus.

Kaldi mode hears only the home's example sentences, word for word. Here the
examples are the corpus's own command sentences (tests/corpus_expected.yaml,
those of a command): each recording is decoded against them, matched as
typed by the app (adapt-ctc's home-extras config) and scored as
grammar_mode.py scores it. With ``--wake`` (the wake detector's harness, as
bench_corpus.py's), only the calls the wake chain accepts, each as the app
has it -- ``wake``, decoded whole after "okay nabu", the background too, as
the app does; ``call``, whole without it; ``split``, from 0.3 s before
Kaldi's split -- else the whole recording (``whole``, ``wake``). The corpus's sentences that are no command stay out of the
grammar: they must be refused. ``--overheard`` also decodes calls cut at the
utterances of talk played into the room (``neg-noise01``): what a false wake
would hear.

    scripts/kaldi_mode.py bench ~/.../office-multicap/{mike,jenn,natalie,oliver}-merged \\
        --overheard ~/.../office-multicap/neg-noise01-merged --wake .../run_okay_nabu
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
    built = mode.build(heard, "en", {}, (), *_wake())
    print(f"{built.sentences} sentences; built in {built.seconds:.1f} s; guessed: "
          f"{sorted(w for w, e in built.words.items() if e.source == 'guessed')}; can't say: {built.unspeakable}", flush=True)
    mode.close()


def _init(work: str, wake: str | None) -> None:
    from voice_labs.kaldi_mode import (  # pylint: disable=import-outside-toplevel
        Sentences,
        words_of,
    )

    _W["engine"] = _engine(COMMANDS, tdt=False)
    mode = kaldi(Path(work))
    mode.build(Sentences({"corpus": [w for s in corpus_sentences() for w in words_of(s, "en")]}), "en", {}, (), *_wake())
    _W["kaldi"] = mode
    if wake:
        import bench_corpus  # pylint: disable=import-outside-toplevel

        bench_corpus.worker_init(
            str(bench_corpus.DEFAULT_COMMANDS), wake, str(ROOT / "models/wake/okay_nabu.json"),
            tempfile.mkdtemp(prefix="kaldi-mode-wake-"), None, None, 0.3, False,
        )
        _W["wake"] = bench_corpus


def _wake() -> tuple[list[tuple[str, ...]], dict[str, list[str]]]:
    """"okay nabu" as the wake word verifier has it (wake.yaml): what a call
    is heard to start with."""
    from voice_labs.wake import WakeVerifier  # pylint: disable=import-outside-toplevel

    entry = WakeVerifier(ROOT / "wake.yaml").config.resolve("okay_nabu")
    return [tuple(p.words) for p in entry.phrases], {w: list(p) for w, p in entry.lexicon.items()}


def _woken(signal):
    """The call as the app has it, when the whole wake chain accepts it (as
    bench_corpus.run_one): from its pre-roll, and Kaldi's split in it
    (seconds); else None."""
    import numpy as np  # pylint: disable=import-outside-toplevel

    bc = _W["wake"]
    detected = bc._W["wake"].detect(bc._W["wake"].probabilities(signal), bc._W["cutoff"])  # pylint: disable=protected-access
    if detected is None:
        return None
    preroll = int(bc.PREROLL_SECONDS * 16000)

    def at(offset: int) -> bytes:
        end = min(len(signal), detected + offset)
        piece = signal[max(0, end - preroll): end]
        if len(piece) < preroll:
            piece = np.concatenate([np.zeros(preroll - len(piece), dtype=np.int16), piece])
        return piece.tobytes()

    offset, pcm = 0, at(0)
    result = bc._verify(pcm)  # pylint: disable=protected-access
    if not result.accepted and result.split is not None and result.split >= bc.PREROLL_SECONDS - bc.TAIL_EDGE_SECONDS:
        retry_pcm = at(int(0.3 * 16000))
        retry = bc._verify(retry_pcm)  # pylint: disable=protected-access
        if retry.accepted or retry.split is not None:
            result, pcm, offset = retry, retry_pcm, int(0.3 * 16000)
    accepted = bool(result.accepted)
    ratio = bc._W["ratio"]  # pylint: disable=protected-access
    if not accepted and ratio is not None:
        accepted = bool(ratio.would_accept(result.rejected_by, pcm)[0])
    if not accepted:
        return None
    call_start = max(0, detected - preroll)
    window_start = detected + offset - preroll
    split = (window_start + int(result.split * 16000) if result.split else detected) - call_start
    return signal[call_start:], split / 16000


def _heard(audio, expect, graphs: dict[str, str], after: float = 0.0) -> dict:
    """``audio`` decoded once; for each variant (name -> graph), what its
    graph's sentence came to."""
    from voice_labs.engine import Satellite  # pylint: disable=import-outside-toplevel

    from voice_labs.kaldi_mode import BACKGROUNDS  # pylint: disable=import-outside-toplevel

    heard = _W["kaldi"].decode(audio, tuple(dict.fromkeys(
        t for g in graphs.values() for t in (g, BACKGROUNDS.get(g, "background"))
    )))
    out = {}
    for name, graph in graphs.items():
        text = heard.text(graph)
        row = {"text": text, "margin": round(heard.margin(graph), 4), "background": heard.background_words(graph),
               "background_after": heard.background_after(after, graph),
               "seconds": round(heard.seconds, 3), "audio": round(len(audio) / 16000, 2)}
        if text:
            decision = _W["engine"].recognize_text(text, Satellite("bench", "bench"))
            if expect == "overheard":
                row["as_typed"] = "false accept" if decision.command is not None else "correct"
            else:
                row["as_typed"] = _verdict(expect, decision)
        out[name] = row
    return out


def _one(job: tuple) -> dict:
    """A recording decoded as the app would: with ``--wake``, the call from
    0.3 s before Kaldi's split (``split``), the whole call from its pre-roll
    (``call``), and the same after "okay nabu" (``wake``: the app's); else the
    whole recording (``whole``, and ``wake``)."""
    import numpy as np  # pylint: disable=import-outside-toplevel
    from _corpus import read_wav  # pylint: disable=import-outside-toplevel

    from voice_labs.kaldi_mode import LEAD  # pylint: disable=import-outside-toplevel

    path, phrase, expect, start, seconds = job
    signal = read_wav(Path(path))
    row = {"file": path, "phrase": phrase, "expect": expect, "start": start}
    if start is not None:
        audio = signal[int(start * 16000): int((start + seconds) * 16000)].astype(np.float32) / 32768.0
        variants = {"split": "home", "call": "home", "wake": "call"} if "wake" in _W else {"whole": "home", "wake": "call"}
        row["variants"] = _heard(audio, expect, variants)
        return row
    if "wake" not in _W:
        row["variants"] = _heard(signal.astype(np.float32) / 32768.0, expect, {"whole": "home", "wake": "call"})
        return row
    woken = _woken(signal)
    if woken is None:
        row["woke"] = False
        return row
    call, split = woken
    call = call.astype(np.float32) / 32768.0
    row["split"] = round(split, 3)
    row["variants"] = {
        **_heard(call[int(max(0.0, split - LEAD) * 16000):], expect, {"split": "home"}, min(split, LEAD)),
        **_heard(call, expect, {"call": "home", "wake": "call"}, split),
    }
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


def bench(sessions: list[Path], channels: list[str], overheard: Path | None, out: Path, workers: int, work: Path,
          wake: Path | None = None) -> None:
    build(work)
    jobs = [(*job, None, None) for job in jobs_of(sessions, channels, None)]
    if overheard is not None:
        jobs += overheard_jobs(overheard)
    print(f"{len(jobs)} recordings, {workers} workers", flush=True)
    rows = []
    with out.open("w") as handle, ProcessPoolExecutor(workers, initializer=_init, initargs=(str(work), str(wake) if wake else None)) as pool:
        for row in pool.map(_one, jobs, chunksize=4):
            rows.append(row)
            handle.write(json.dumps(row) + "\n")
    report(rows)


def _verdict_at(expect, heard: dict, max_cost: float) -> str:
    """The verdict had ``max_cost`` been the setting."""
    taken = bool(heard["text"]) and heard["margin"] <= max_cost and heard.get("as_typed") is not None
    if expect == "overheard":
        return "false accept" if taken and heard["as_typed"] == "false accept" else "correct"
    if taken:
        return heard["as_typed"]
    return "correct" if expect is not None and expect[0] is None else "missed"


def report(rows: list[dict], costs: tuple[float, ...] = (-0.1, -0.05, -0.02, 0.0, 0.02, 0.05, 0.1, 0.2)) -> None:
    rows = [r for r in rows if "variants" in r]
    bad = {"wrong intent", "wrong slots", "false accept"}
    for variant in dict.fromkeys(v for r in rows for v in r["variants"]):
        calls = [(r["expect"], r["variants"][variant]) for r in rows if variant in r["variants"]]
        commands = [(e, h) for e, h in calls if e not in (None, "overheard") and e[0] is not None]
        refuse = [(e, h) for e, h in calls if e not in (None, "overheard") and e[0] is None]
        overheard = [(e, h) for e, h in calls if e == "overheard"]
        print(f"{variant}: {len(commands)} commands, {len(refuse)} to refuse, {len(overheard)} overheard")
        for cost in costs:
            verdicts = [_verdict_at(e, h, cost) for e, h in commands]
            print(f"  max {cost:+.2f}: right {verdicts.count('correct') / max(1, len(commands)):.1%}, "
                  f"wrong {sum(v in bad for v in verdicts) / max(1, len(commands)):.1%}; "
                  f"refused {sum(_verdict_at(e, h, cost) == 'correct' for e, h in refuse) / max(1, len(refuse)):.1%}; "
                  f"overheard ran {sum(_verdict_at(e, h, cost) != 'correct' for e, h in overheard)} of {len(overheard)}")
        print(f"  background heard no word: {sum(not h['background'] for _, h in commands)} of {len(commands)} commands, "
              f"{sum(not h['background'] for _, h in overheard)} of {len(overheard)} overheard"
              + (f"; none after the wake word: {sum(not h['background_after'] for _, h in commands)} commands" if commands and "background_after" in commands[0][1] else ""))
        seconds = sorted(h["seconds"] / max(0.1, h["audio"]) for _, h in calls)
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
    b.add_argument("--wake", type=Path, help="the wake detector's harness (bench_corpus): each call as the app has "
                   "it, decoded from Kaldi's split and whole")
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
        bench(args.sessions, args.channels, args.overheard, args.out, args.workers, args.work, args.wake)
        print(f"{time.monotonic() - started:.0f} s")
    else:
        report([json.loads(line) for line in args.jsonl.read_text().splitlines()])
    return 0


if __name__ == "__main__":
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    raise SystemExit(main())
