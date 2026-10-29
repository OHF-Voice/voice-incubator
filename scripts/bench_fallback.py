#!/usr/bin/env python3
"""The fallback chain on sentences the keywords mostly miss
(tests/fallback_utterances.yaml), with the library's commands in a made-up
home (HOME).

Two modes:

- ``--mode fallback``: the sentence as text to the fallback as the app runs
  it (``Fallback.decide``): meaning (EmbeddingGemma), then GLiNER.
- ``--mode chain``: the whole chain as a call would run it -- Piper speaks the
  sentence, the CTC matcher tries it, then meaning, then GLiNER -- and which
  step decided each one.

Every command is chosen for both steps (``fallback: [meaning, llm]``), or
only those given with ``--chosen``. ``--language de`` runs a language pack's
library and models on its sentences (``tests/fallback_utterances.<code>.yaml``,
which names its own home).

    scripts/bench_fallback.py --mode fallback --chosen play_music,lights_on --out /tmp/fallback.json
    scripts/bench_fallback.py --mode chain --embedding-accept --out /tmp/chain.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from voice_labs import languages  # noqa: E402
from voice_labs.fallback import (  # noqa: E402
    DEFAULT_FALLBACK_MODEL,
    GLINER_MODELS,
    GLINER_THRESHOLD,
    GlinerPicker,
)
from voice_labs.store import compile_store  # noqa: E402

UTTERANCES = ROOT / "tests/fallback_utterances.yaml"
"""English's; another language's is ``fallback_utterances.<code>.yaml``."""

HOME = {
    "areas": {
        "kitchen": {"name": "Kitchen"},
        "living_room": {"name": "Living Room"},
        "bedroom": {"name": "Bedroom"},
        "office": {"name": "Office"},
    },
    "floors": {"ground_floor": {"name": "Downstairs"}, "first_floor": {"name": "Upstairs"}},
    "entities": {
        "light.kitchen_ceiling": {"name": "Kitchen Light"},
        "switch.coffee_maker": {"name": "Coffee Maker"},
        "fan.bedroom_fan": {"name": "Bedroom Fan"},
        "cover.garage_door": {"name": "Garage Door", "aliases": ["Garage"]},
        "lock.front_door": {"name": "Front Door"},
        "climate.thermostat": {"name": "Thermostat"},
        "media_player.living_room_tv": {"name": "TV"},
    },
}


def library_store(chosen: list[str] | None = None, home: dict[str, Any] | None = None) -> dict[str, Any]:
    """Every library command -- each version of each, as added from the
    library -- with the value lists they use, in HOME: each chosen for
    meaning and the model step, or only those in ``chosen`` (a version's id,
    or a command's for all its versions)."""
    from voice_labs.web import load_library  # pylint: disable=import-outside-toplevel

    commands: dict[str, Any] = {}
    lists: dict[str, Any] = {}
    for entry in load_library():
        lists.update(entry["lists"])
        steps = ["meaning", "llm"] if chosen is None or {entry["id"], entry["family"]} & set(chosen) else []
        commands[entry["id"]] = {**entry["spec"], "fallback": steps}
    return {"language": languages.current().code, "names": home or HOME, "lists": lists, "commands": commands}


def expected(utterance: dict[str, Any], command_id: str | None, slots: dict[str, Any]) -> tuple[bool, str]:
    """Whether what was decided is what the sentence meant, and what differs."""
    want = utterance.get("expect")
    if want != command_id:
        return False, f"{command_id or 'nothing'} instead of {want or 'nothing'}"
    for name, value in (utterance.get("slots") or {}).items():
        got = slots.get(name)
        if name == "duration":
            seconds = (got or {}).get("total_seconds") if isinstance(got, dict) else got
            if seconds is None or abs(float(seconds) - float(value)) > 1:
                return False, f"{name} {got!r}, wanted {value!r}"
        elif name == "time":
            clock = languages.reading().clock
            want_clock, got_clock = clock(str(value)), clock(str(got or ""))
            if got_clock is None or want_clock[:2] != got_clock[:2]:
                return False, f"time {got!r}, wanted {value!r}"
        elif isinstance(value, str):
            if value.lower() not in str(got or "").lower():
                return False, f"{name} {got!r}, wanted {value!r}"
        elif got != value:
            return False, f"{name} {got!r}, wanted {value!r}"
    return True, ""


def _chosen(args) -> list[str] | None:
    return [c.strip() for c in args.chosen.split(",") if c.strip()] if args.chosen else None


def _versions(chosen: list[str] | None) -> list[str] | None:
    """The library versions ``--chosen`` names: each one named, and every
    version of a command named."""
    if chosen is None:
        return None
    return sorted(i for i, spec in library_store(chosen)["commands"].items() if spec["fallback"])


def _for_chosen(utterances, chosen: list[str] | None) -> list[dict[str, Any]]:
    """A sentence for a command not chosen should be refused."""
    versions = _versions(chosen)
    if versions is None:
        return utterances
    return [u if u.get("expect") in versions else {**u, "expect": None, "slots": {}} for u in utterances]


def run_fallback(args, compiled, utterances) -> dict[str, Any]:
    """Meaning, then the model step, on the sentence as text -- as
    ``Engine._fall_back`` runs them, without speech or the CTC matcher."""
    from adapt_ctc.embedding import DEFAULT_EMBEDDING_MODEL  # pylint: disable=import-outside-toplevel

    from voice_labs.engine import Engine, Satellite  # pylint: disable=import-outside-toplevel
    from voice_labs.commands import command_of  # pylint: disable=import-outside-toplevel
    from voice_labs.fallback import Fallback, label_of  # pylint: disable=import-outside-toplevel

    utterances = _for_chosen(utterances, _chosen(args))
    engine = Engine(languages.current().ctc_dir(ROOT), None, DEFAULT_EMBEDDING_MODEL, threads=args.threads)
    engine.build(compiled)
    model = None if args.no_model else _model(args)
    fallback = Fallback(embedding_accept=not args.no_meaning, model=model, top_k=args.top_k or 8)
    fallback.prepare(engine, wait=True)
    recognizer = engine.recognizer
    satellite = Satellite("bench", "Bench", "kitchen", "Kitchen")

    def same(intent: str) -> str:
        # As Engine._fall_back: a command's versions are no rivals.
        command = compiled.commands.get(command_of(intent))
        return label_of(command) if command is not None else intent

    rows = []
    for utterance in utterances:
        texts = [utterance["text"]]
        started = time.perf_counter()
        found = fallback.decide(
            utterance["text"], lambda texts=texts: recognizer.closest_by_meaning(texts, group=same), compiled, satellite,
            lambda texts=texts: recognizer.rank_by_meaning(texts),
        )
        seconds = time.perf_counter() - started
        command_id = found.command.id if found.command else None
        slots = found.slots if found.command else {}
        ok, why = expected(utterance, command_id, slots)
        rows.append(
            {"text": utterance["text"], "expect": utterance.get("expect"), "got": command_id, "slots": slots,
             "by": found.by, "ok": ok, "why": why, "trace": found.trace, "seconds": round(seconds, 3)}
        )
        print(f"{'ok ' if ok else 'BAD'} {found.by or '-':7s} {seconds:5.2f}s {utterance['text']!r} -> {command_id} {slots} {why}", flush=True)
    seconds = [r["seconds"] for r in rows]
    model_seconds = [r["trace"]["llm"]["seconds"] for r in rows if "seconds" in r["trace"].get("llm", {})]
    return {
        "mode": "fallback",
        "meaning": not args.no_meaning,
        "model": None if model is None else model.model,
        "threshold": None if model is None else model.threshold,
        "threads": args.threads,
        "chosen": args.chosen,
        "accuracy": sum(r["ok"] for r in rows) / len(rows),
        "commands_ok": _rate([r for r in rows if r["expect"]]),
        "non_commands_ok": _rate([r for r in rows if not r["expect"]]),
        "wrong_command": sum(1 for r in rows if r["got"] and not r["ok"]),
        "by_step": {by: sum(1 for r in rows if r["by"] == by) for by in ("meaning", "gliner")},
        "median_seconds": statistics.median(seconds),
        "model_median_seconds": statistics.median(model_seconds) if model_seconds else None,
        "rows": rows,
    }


def _model(args) -> GlinerPicker:
    path = Path(args.model_dir) if args.model_dir else GlinerPicker.find_model(args.model, ROOT)
    if path is None:
        raise SystemExit(f"no models/{GLINER_MODELS[args.model].directory}: scripts/export_gliner.py")
    return GlinerPicker(path, n_threads=args.threads, model=args.model, threshold=args.threshold)


def _rate(rows: list[dict[str, Any]]) -> float | None:
    return sum(r["ok"] for r in rows) / len(rows) if rows else None


async def run_chain(args, compiled, utterances) -> dict[str, Any]:
    from adapt_ctc.embedding import DEFAULT_EMBEDDING_MODEL  # pylint: disable=import-outside-toplevel

    from voice_labs.engine import Engine, Satellite  # pylint: disable=import-outside-toplevel
    from voice_labs.fallback import Fallback  # pylint: disable=import-outside-toplevel
    from voice_labs.server import _pcm_to_float  # pylint: disable=import-outside-toplevel
    from voice_labs.tts import Speech  # pylint: disable=import-outside-toplevel

    pack = languages.current()
    engine = Engine(pack.ctc_dir(ROOT), pack.transducer_dir(ROOT), DEFAULT_EMBEDDING_MODEL, threads=args.threads)
    engine.build(compiled)
    model = None if args.no_model else _model(args)
    engine.fallback = Fallback(embedding_accept=args.embedding_accept, model=model, top_k=args.top_k)
    engine.fallback.prepare(engine, wait=True)
    speech = Speech(ROOT / "models/piper" / pack.piper, ROOT / "sounds")
    await speech.start()
    satellite = Satellite("bench", "Bench", "kitchen", "Kitchen")
    rows = []
    for utterance in utterances:
        pcm = await speech.say(utterance["text"])
        started = time.perf_counter()
        decision = engine.recognize_command(_pcm_to_float(pcm), satellite)
        seconds = time.perf_counter() - started
        command_id = decision.command.id if decision.command else None
        ok, why = expected(utterance, command_id, decision.slots if decision.command else {})
        by = decision.matched_by if decision.command else (decision.fallback or {}).get("stopped", "none")
        rows.append(
            {"text": utterance["text"], "expect": utterance.get("expect"), "got": command_id,
             "slots": decision.slots if decision.command else {}, "by": by, "ok": ok, "why": why,
             "fallback": decision.fallback, "seconds": round(seconds, 3),
             "transcript": decision.recognition.get("tdt_transcript")}
        )
        print(f"{'ok ' if ok else 'BAD'} {by:9s} {seconds:5.2f}s {utterance['text']!r} -> {command_id} {why}", flush=True)
    by_step: dict[str, dict[str, int]] = {}
    for row in rows:
        step = by_step.setdefault(row["by"], {"decided": 0, "ok": 0})
        step["decided"] += 1
        step["ok"] += row["ok"]
    return {
        "mode": "chain",
        "embedding_accept": args.embedding_accept,
        "model": None if model is None else args.model,
        "top_k": args.top_k,
        "accuracy": sum(r["ok"] for r in rows) / len(rows),
        "commands_ok": _rate([r for r in rows if r["expect"]]),
        "non_commands_ok": _rate([r for r in rows if not r["expect"]]),
        "wrong_command": sum(1 for r in rows if r["got"] and not r["ok"]),
        "by_step": by_step,
        "median_seconds": statistics.median(r["seconds"] for r in rows),
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=("fallback", "chain"), default="fallback")
    parser.add_argument("--language", default=languages.DEFAULT, help="a language pack: its library, models and sentences")
    parser.add_argument("--model", choices=sorted(GLINER_MODELS), default=DEFAULT_FALLBACK_MODEL, help="GLiNER's")
    parser.add_argument("--model-dir", help="its exported folder (default: under models/)")
    parser.add_argument("--threshold", type=float, default=GLINER_THRESHOLD, help="GLiNER's")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--embedding-accept", action="store_true")
    parser.add_argument("--top-k", type=int, help="how many commands GLiNER chooses among, the closest in meaning")
    parser.add_argument("--no-model", action="store_true", help="meaning alone")
    parser.add_argument("--no-meaning", action="store_true", help="--mode fallback without the meaning step")
    parser.add_argument("--chosen", help="only these commands (comma-separated) are chosen for the fallback")
    parser.add_argument("--only", help="only utterances containing this")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    languages.use(args.language)
    path = UTTERANCES if args.language == "en" else UTTERANCES.with_name(f"fallback_utterances.{args.language}.yaml")
    data = yaml.safe_load(path.read_text("utf-8"))
    compiled = compile_store(library_store(_chosen(args), data.get("home")))
    utterances = data["utterances"]
    if args.only:
        utterances = [u for u in utterances if args.only in u["text"]]
    if args.mode == "fallback":
        result = run_fallback(args, compiled, utterances)
    else:
        result = asyncio.run(run_chain(args, compiled, utterances))
    summary = {k: v for k, v in result.items() if k != "rows"}
    print(json.dumps(summary, indent=1))
    if args.out:
        args.out.write_text(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
