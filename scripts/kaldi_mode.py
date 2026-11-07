#!/usr/bin/env python3
"""Prototype: the command recognized by Kaldi with the home's own words.

The wake verifier's Kaldi model (wyoming_wake_kaldi, ``models/wake-kaldi-*``)
decodes the whole call -- from before the wake word -- against a grammar of
"okay nabu" followed by any of the commands' words: their keywords, the
names (aliases too), the numbers, the fillers; or (``--sentences``) only the
sentences given. Anything else is a costed loop of phones (``<unk>``). The
words after the wake phrase go to the app's matcher as typed text. No cut:
the wake phrase is in the grammar, so where it ends is the decoder's to find.

    scripts/kaldi_mode.py build                         # the graph, in local/kaldi-mode
    scripts/kaldi_mode.py decode call.wav               # one recording
    scripts/kaldi_mode.py bench ~/.../office-multicap/mike-merged
    scripts/kaldi_mode.py build --sentences tests/corpus_expected.yaml --work /tmp/oracle
    scripts/kaldi_mode.py bench ~/.../mike-merged --sentences tests/corpus_expected.yaml --work /tmp/oracle

``bench`` scores it against the app as it is (the CTC head's keywords, the
TDT head from before the cut) on an office-multicap session's four VPEs
(AGC), command and slots as tests/corpus_expected.yaml has them (as
scripts/bench_corpus.py, without its wake word stage: both start from the
recording). With ``--sentences`` it also scores Parakeet's CTC head held to
the same sentences (the wake phrase before each, the whole call searched,
the cheapest taken), so the two acoustic models are compared on one grammar.

What it found, on mike-merged (816 recordings of the commands adapt-ctc's
home-extras config hears, 202 words, everyone pronounced):

    the app as it is                                          78.9% correct
    Kaldi, "okay nabu" + any of the words                     40.3%  (missed 48.0%)
    Kaldi, "okay nabu" + the corpus's own sentences (oracle)  90.6%
    Parakeet's CTC head, the same oracle sentences            95.2%  (93.6% after the cut)

Kaldi's model hears far-field speech far worse than Parakeet: with any of
the words it wrote "shut house you right" for "turn on the ceiling light",
and no word or phone cost changed that (0 to 3, 4 to none: 43-46% on 240).
Held to whole sentences it does well, and that is where the gain is -- but
Parakeet does it better on the same sentences, and the app has the CTC
search for example sentences already. The oracle is the test's own ~200
sentences; a home's grammar would be many more, so these are upper bounds.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from wyoming_wake_kaldi.kaldi import SpeechTools, decode_best_path  # noqa: E402
from wyoming_wake_kaldi.lexicon import ModelLexicon  # noqa: E402
from wyoming_wake_kaldi.model import prepare_model  # noqa: E402

MODEL_ID = "en_US-rhasspy"
MODELS = ROOT / "models/wake-kaldi-models"
TOOLS = ROOT / "models/wake-kaldi-tools"
WORK = ROOT / "local/kaldi-mode"
COMMANDS = ROOT / "adapt_ctc/examples/home-extras.yaml"

WAKE = ("okay", "nabu")
WAKE_PRONUNCIATIONS = {"okay": None, "nabu": ["n ˈæ b u"]}  # as wake.yaml has it
WORD_COST = 1.0
"""Each word of the loop: a word insertion penalty, so silence isn't words
(0 to 3 made no difference)."""
FILLER_COST = 4.0
"""Each phone of what is none of the words: dearer than a word, or the loop
would spell everything with phones (4, 8 or none made no difference)."""
SEARCH = ["--beam=13.0", "--max-active=7000", "--lattice-beam=6.0"]
UNK, SIL = "<unk>", "<sil>"

ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
TENS = "twenty thirty forty fifty sixty seventy eighty ninety".split()
NUMBER_WORDS = [*ONES, *TENS, "hundred", "thousand", "point", "and", "a", "half", "quarter", "an"]


# ------------------------------------------------------------------ the words


def vocabulary(commands_path: Path) -> list[str]:
    """Every word the commands can hear: keywords and examples, names and
    their aliases, value lists, fillers and name cues, number words."""
    from bench_corpus import load_commands  # pylint: disable=import-outside-toplevel

    from adapt_ctc.template import expand  # pylint: disable=import-outside-toplevel

    config = load_commands(commands_path).config
    phrases: list[str] = [*config.get("filler", []), *config.get("name_cues", [])]

    def walk(value, key=""):
        if key in ("domains", "ids", "slot", "list", "name_type"):
            return
        if isinstance(value, str):
            if key == "examples":
                phrases.extend(expand(value))
            else:
                phrases.append(value)
        elif isinstance(value, dict):
            for k, v in value.items():
                # A value list's keys are what Home Assistant gets, not words.
                walk(v, key if key == "values" else str(k))
        elif isinstance(value, list):
            for item in value:
                walk(item, key)

    walk(config.get("intents", {}))
    for entries in (config.get("names") or {}).values():
        for entry in entries.values():
            if isinstance(entry, str):
                phrases.append(entry)
            elif isinstance(entry, dict):
                phrases += [str(entry.get("name") or ""), *map(str, entry.get("aliases") or [])]
    words = {w for phrase in phrases for w in re.findall(r"[a-z']+", phrase.lower().replace("...", " "))}
    words |= set(NUMBER_WORDS)
    return sorted(w for w in words if w not in WAKE)


def pronunciations(words: list[str]) -> tuple[dict[str, list[str]], list[str]]:
    """Each word's pronunciations from the model's lexicon, else espeak's
    guess (voice_labs/pronounce.py); and the words with neither."""
    from voice_labs import pronounce  # pylint: disable=import-outside-toplevel

    lexicon = ModelLexicon(MODELS / MODEL_ID)
    out: dict[str, list[str]] = {}
    missing = []
    try:
        for word in [*WAKE, *words]:
            prons = (WAKE_PRONUNCIATIONS.get(word) or lexicon.pronunciations(word)) if word in WAKE else lexicon.pronunciations(word)
            if not prons:
                guess = pronounce.guess(word, lexicon.phones)
                prons = [guess] if guess else []
            if prons:
                out[word] = prons
            else:
                missing.append(word)
        fillers = lexicon.filler_inventory()
    finally:
        lexicon.close()
    for name, variants in fillers:
        out[name] = variants
    return out, missing


# ------------------------------------------------------------------ the graph


def _work() -> Path:
    return Path(os.environ.get("KALDI_WORK") or WORK)


def build(commands_path: Path = COMMANDS) -> Path:
    """The decoding graph: prepare_lang, the grammar, mkgraph (as the
    verifier's train.py builds its own). Its directory."""
    work = _work()
    word_cost = float(os.environ.get("KALDI_WORD_COST", WORD_COST))
    filler_cost = float(os.environ.get("KALDI_FILLER_COST", FILLER_COST))
    tools = SpeechTools.from_dir(TOOLS)
    model = prepare_model(MODEL_ID, MODELS / MODEL_ID, work)
    words = vocabulary(commands_path)
    sentences = _sentences()
    words = sorted({*words, *(w for sentence in sentences for w in sentence)})
    lexicon, missing = pronunciations(words)
    if missing:
        print(f"no pronunciation, left out: {' '.join(missing)}")
    if work.exists():
        for name in ("data", "graph"):
            shutil.rmtree(work / name, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    (work / "path.sh").write_text("", encoding="utf-8")
    utils = work / "utils"
    utils.unlink(missing_ok=True)
    utils.symlink_to(tools.egs_utils_dir, target_is_directory=True)

    dict_dir = work / "data/local/dict"
    dict_dir.mkdir(parents=True, exist_ok=True)
    for phone_file in model.phones_dir.glob("*.txt"):
        shutil.copy(phone_file, dict_dir / phone_file.name)
    with open(dict_dir / "lexicon.txt", "w", encoding="utf-8") as out:
        for word, prons in sorted(lexicon.items()):
            for pron in prons:
                print(word, pron, file=out)
        print(f"{UNK} SPN", file=out)
        print(f"{SIL} SIL", file=out)
    lang = work / "data/lang"
    tools.run("bash", [str(tools.egs_utils_dir / "prepare_lang.sh"), str(dict_dir), UNK, str(work / "data/local/lang"), str(lang)], cwd=work)

    # "okay nabu", then any of the words, or phones for what is none of them.
    fillers = [w for w in lexicon if w.startswith("f_")]
    grammar = lang / "grammar.fst.txt"
    with open(grammar, "w", encoding="utf-8") as out:
        print("0 1 okay", file=out)
        print("1 2 nabu", file=out)
        if sentences:
            # Only these sentences after the wake phrase (an upper bound: the
            # corpus's own), each a chain of its words.
            state = 3
            for sentence in sentences:
                if any(w not in lexicon for w in sentence):
                    continue
                at = 2
                for index, word in enumerate(sentence):
                    target = 3 if index == len(sentence) - 1 else state + 1
                    print(f"{at} {target} {word}", file=out)
                    at, state = target, max(state, target)
            print("3", file=out)
            words = []
        for word in words:
            if word in lexicon:
                print(f"2 2 {word} {word_cost}", file=out)
        for filler in fillers if filler_cost < 100 and not sentences else ():
            print(f"2 2 {filler} {filler_cost}", file=out)
        if not sentences:
            print("2", file=out)
    tools.run_pipeline(
        ["fstcompile", f"--isymbols={lang / 'words.txt'}", f"--osymbols={lang / 'words.txt'}", "--acceptor", str(grammar)],
        ["fstarcsort", "--sort_type=ilabel", "-", str(lang / "G.fst")],
        cwd=work,
    )
    tools.run("bash", [str(tools.egs_utils_dir / "mkgraph.sh"), "--self-loop-scale", "1.0", str(lang), str(model.am_dir), str(work / "graph")], cwd=work)
    (work / "vocabulary.txt").write_text("\n".join(w for w in words if w in lexicon) + "\n", encoding="utf-8")
    print(f"{sum(w in lexicon for w in words)} words, graph in {work / 'graph'}")
    return work / "graph"


def _sentences() -> list[list[str]]:
    """With KALDI_SENTENCES (a corpus_expected.yaml), its sentences, number
    digits spelled: the grammar is then those alone."""
    path = os.environ.get("KALDI_SENTENCES")
    if not path:
        return []
    import yaml  # pylint: disable=import-outside-toplevel

    out = []
    for sentence in yaml.safe_load(Path(path).read_text())["sentences"]:
        text = re.sub(r"\d+(\.\d+)?", lambda m: _spelled(m.group()), str(sentence).lower().replace("%", " percent"))
        out.append(re.findall(r"[a-z']+", text))
    return out


def _spelled(number: str) -> str:
    whole, _, part = number.partition(".")
    n = int(whole)

    def words(n: int) -> str:
        if n < 20:
            return ONES[n]
        if n < 100:
            return TENS[n // 10 - 2] + ("" if n % 10 == 0 else " " + ONES[n % 10])
        if n < 1000:
            return ONES[n // 100] + " hundred" + ("" if n % 100 == 0 else " " + words(n % 100))
        if n % 100 == 0:
            return words(n // 100) + " hundred"  # 2700: "twenty seven hundred"
        return words(n // 1000) + " thousand" + ("" if n % 1000 == 0 else " " + words(n % 1000))

    return words(n) + (" point " + " ".join(ONES[int(d)] for d in part) if part else "")


# ------------------------------------------------------------------ decoding


def decode(wav: Path) -> tuple[str, float | None, list]:
    """The words after the wake phrase (phones of none of the words as
    <unk>, once for each run of them), where the wake phrase ended (seconds),
    and every word with its times."""
    work = _work()
    tools = SpeechTools.from_dir(TOOLS)
    model = prepare_model(MODEL_ID, MODELS / MODEL_ID, work)
    _cost, ctm = decode_best_path(tools, model.final_mdl, model.online_conf, work / "graph", work / "data/lang", wav, True, SEARCH)
    words, wake_end = [], None
    for word, _start, end in ctm:
        if word == "nabu" and wake_end is None:
            wake_end = end
            continue
        if wake_end is None or word in (SIL, "okay"):
            continue
        if word.startswith("f_"):
            if not words or words[-1] != UNK:
                words.append(UNK)
            continue
        words.append(word)
    return " ".join(words), wake_end, list(ctm)


# ------------------------------------------------------------------ the bench

_W: dict = {}


def _init(commands: str) -> None:
    from bench_corpus import load_commands  # pylint: disable=import-outside-toplevel

    from voice_labs import languages  # pylint: disable=import-outside-toplevel
    from voice_labs.engine import Engine  # pylint: disable=import-outside-toplevel

    languages.use("en")
    engine = Engine(ROOT / "models/speech-to-phrase/stt_en_parakeet_tdt_ctc_110m-int8", ROOT / "models/parakeet-tdt-int8", threads=1)
    engine.build(load_commands(Path(commands)))
    _W["engine"] = engine
    sentences = [" ".join(s) for s in _sentences()]
    if sentences:
        # Parakeet's CTC head with the same sentences: each an example, the
        # wake phrase before it as the CTC head spells it, searched for in
        # the whole call (no cut), the cheapest taken outright.
        from voice_labs.store import compile_store  # pylint: disable=import-outside-toplevel

        wake = "(okay|ok) (nabu|naboo|now boo) "
        store = {"commands": {f"s{i}": {"description": s, "examples": [wake + s], "response": "ok"} for i, s in enumerate(sentences)}}
        oracle = Engine(ROOT / "models/speech-to-phrase/stt_en_parakeet_tdt_ctc_110m-int8", None, threads=1)
        oracle.build(compile_store(store))
        _W.update(oracle=oracle, sentences=sentences)


def _verdict(expect, decision) -> str:
    from adapt_ctc.score import _slots  # pylint: disable=import-outside-toplevel

    from voice_labs.commands import command_of  # pylint: disable=import-outside-toplevel

    got = decision.intent
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

    from voice_labs.commands import command_of  # pylint: disable=import-outside-toplevel
    from voice_labs.engine import Satellite  # pylint: disable=import-outside-toplevel

    path, phrase, expect = job
    engine, satellite = _W["engine"], Satellite("bench", "bench")
    audio = read_wav(Path(path)).astype(np.float32) / 32768.0
    row = {"file": path, "phrase": phrase}
    started = time.monotonic()
    if os.environ.get("KALDI_ONLY"):
        decision, split = engine.recognize_text("", satellite), type("S", (), {"cut": 0.0})()
    else:
        decision, split = engine.recognize_call(audio, 0.0, 2.5, satellite)
    row["app_ms"] = round((time.monotonic() - started) * 1000)
    row["app"] = {"verdict": _verdict(expect, decision), "tdt": decision.recognition.get("tdt_transcript"),
                  "intent": command_of(decision.intent.intent) if decision.intent else None,
                  "slots": decision.intent.slots if decision.intent else {}, "cut": split.cut}
    started = time.monotonic()
    text, wake_end, _words = decode(Path(path))
    row["kaldi_ms"] = round((time.monotonic() - started) * 1000)
    decision = engine.recognize_text(text.replace(UNK, "").strip(), satellite) if text.replace(UNK, "").strip() else None
    if "oracle" in _W:
        started = time.monotonic()
        log_probs, frame_seconds = _W["oracle"].model.log_probs(audio)
        ranked = _W["oracle"].recognizer._example_recognizer.recognize_log_probs(log_probs, frame_seconds).matches  # pylint: disable=protected-access
        best = min(ranked, key=lambda r: r.cost) if ranked else None
        said = _W["sentences"][int(best.intent.split(":")[0][1:])] if best else ""
        heard = engine.recognize_text(said, satellite) if said else None
        row["parakeet_ms"] = round((time.monotonic() - started) * 1000)
        row["parakeet"] = {"verdict": _verdict(expect, heard) if heard else "missed", "text": said}
    row["kaldi"] = {"verdict": _verdict(expect, decision) if decision else ("correct" if expect and expect[0] is None else "missed"),
                    "text": text, "wake_end": wake_end,
                    "intent": command_of(decision.intent.intent) if decision and decision.intent else None,
                    "slots": decision.intent.slots if decision and decision.intent else {}}
    return row


def bench(session: Path, channels: list[str], out: Path, workers: int, limit: int | None, commands: Path) -> None:
    from _corpus import corrections, session_key  # pylint: disable=import-outside-toplevel

    from adapt_ctc.score import _key, load_manifest  # pylint: disable=import-outside-toplevel

    truth = load_manifest(ROOT / "tests/corpus_expected.yaml").sentences
    fixes, speaker = corrections(ROOT / "tests/corpus_corrections.csv"), session_key(session)
    index = json.loads((session / "prompts/index.json").read_text())
    jobs = []
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
    print(f"{len(jobs)} recordings, {workers} workers", flush=True)
    with out.open("w") as handle, ProcessPoolExecutor(workers, initializer=_init, initargs=(str(commands),)) as pool:
        rows = []
        for row in pool.map(_one, jobs, chunksize=4):
            rows.append(row)
            handle.write(json.dumps(row) + "\n")
    report(rows)


def report(rows: list[dict]) -> None:
    failed = [r for r in rows if "error" in r]
    if failed:
        print(f"{len(failed)} failed, the first: {failed[0]['phrase']!r}\n{failed[0]['error']}")
    rows = [r for r in rows if "error" not in r]
    scored = [r for r in rows if r["app"]["verdict"] != "unknown"]
    print(f"{len(scored)} scored of {len(rows)}")
    for name in (n for n in ("app", "kaldi", "parakeet") if n in rows[0] and not (n == "app" and os.environ.get("KALDI_ONLY"))):
        counts: dict[str, int] = {}
        for r in scored:
            counts[r[name]["verdict"]] = counts.get(r[name]["verdict"], 0) + 1
        ms = sorted(r[f"{name}_ms"] for r in rows)
        line = ", ".join(f"{k} {v / len(scored):.1%}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))
        print(f"  {name:6s} {line}  (median {ms[len(ms) // 2]} ms)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="what", required=True)
    b = sub.add_parser("build")
    b.add_argument("--commands", type=Path, default=COMMANDS)
    b.add_argument("--word-cost", type=float, default=WORD_COST)
    b.add_argument("--filler-cost", type=float, default=FILLER_COST, help="100 or more: no phone loop")
    d = sub.add_parser("decode")
    d.add_argument("wav", type=Path)
    r = sub.add_parser("bench")
    r.add_argument("session", type=Path)
    r.add_argument("--channels", nargs="+", default=["pc-vpe.agc", "capture1-vpe.agc", "capture2-vpe.agc", "capture3-vpe.agc"])
    r.add_argument("--out", type=Path, default=Path(tempfile.gettempdir()) / "kaldi_mode.jsonl")
    r.add_argument("--workers", type=int, default=8)
    r.add_argument("--limit", type=int)
    r.add_argument("--commands", type=Path, default=COMMANDS)
    r.add_argument("--kaldi-only", action="store_true", help="not the app as it is")
    for each in (b, d, r):
        each.add_argument("--work", type=Path, default=WORK, help="where the graph is")
        each.add_argument("--sentences", type=Path, help="a corpus_expected.yaml: only its sentences after the wake phrase "
                          "(an upper bound), and bench also scores Parakeet's CTC head with them")
    args = parser.parse_args()
    os.environ["KALDI_WORK"] = str(args.work)
    if args.sentences:
        os.environ["KALDI_SENTENCES"] = str(args.sentences)
    if args.what == "build":
        os.environ["KALDI_WORD_COST"], os.environ["KALDI_FILLER_COST"] = str(args.word_cost), str(args.filler_cost)
    if getattr(args, "kaldi_only", False):
        os.environ["KALDI_ONLY"] = "1"
    if args.what == "build":
        build(args.commands)
    elif args.what == "decode":
        text, wake_end, words = decode(args.wav)
        print(json.dumps({"text": text, "wake_end": wake_end, "words": words}))
    else:
        bench(args.session, args.channels, args.out, args.workers, args.limit, args.commands)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
