#!/usr/bin/env python3
"""The fallback chain on sentences the keywords mostly miss
(tests/fallback_utterances.yaml), with the library's commands in a made-up
home (HOME).

Two modes:

- ``--mode llm``: the sentence as text straight to the LLM. What the LLM step
  is worth on its own, and what it costs -- runs anywhere llama-cpp-python
  does (a Pi 5), no other model needed.
- ``--mode chain``: the whole chain as a call would run it -- Piper speaks the
  sentence, the CTC matcher tries it, then meaning (EmbeddingGemma), then
  the LLM -- and which step decided each one.

Every command is chosen for both steps (``fallback: [meaning, llm]``).

    scripts/bench_fallback.py --mode llm --llm hammer-2.1-0.5b --threads 4 --out /tmp/llm.json
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

from voice_call.fallback import (  # noqa: E402
    DEFAULT_TOOL_MODEL,
    NOT_A_COMMAND,
    SYSTEM_PROMPT,
    TOOL_MODELS,
    ToolModel,
    Tools,
    with_way_out,
)
from voice_call.store import _yaml, compile_store, plain  # noqa: E402

LIBRARY = ROOT / "voice_call/library"
UTTERANCES = ROOT / "tests/fallback_utterances.yaml"

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


def library_store() -> dict[str, Any]:
    """Every library command, with the value lists they use, in HOME."""
    commands: dict[str, Any] = {}
    lists: dict[str, Any] = {}
    for path in sorted(LIBRARY.glob("*.yaml")):
        data = plain(_yaml().load(path.read_text()))  # YAML 1.2: "on" is a word
        lists.update(data.get("lists") or {})
        for command_id, spec in (data.get("commands") or {}).items():
            commands[command_id] = {k: v for k, v in spec.items() if k != "needs"} | {"fallback": ["meaning", "llm"]}
    return {"names": HOME, "lists": lists, "commands": commands}


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
            from voice_call.timers import parse_clock  # pylint: disable=import-outside-toplevel

            want_clock, got_clock = parse_clock(str(value)), parse_clock(str(got or ""))
            if got_clock is None or want_clock[:2] != got_clock[:2]:
                return False, f"time {got!r}, wanted {value!r}"
        elif isinstance(value, str):
            if value.lower() not in str(got or "").lower():
                return False, f"{name} {got!r}, wanted {value!r}"
        elif got != value:
            return False, f"{name} {got!r}, wanted {value!r}"
    return True, ""


def rankings(args, compiled, utterances) -> dict[str, list[str]]:
    """Every command by meaning, per sentence: from --ranking, or computed
    here with EmbeddingGemma (and saved with --save-ranking)."""
    if args.ranking:
        return json.loads(Path(args.ranking).read_text())
    from adapt_ctc.embedding import DEFAULT_EMBEDDING_MODEL  # pylint: disable=import-outside-toplevel

    from voice_call.engine import Engine  # pylint: disable=import-outside-toplevel

    ctc = ROOT / "models/speech-to-phrase/stt_en_parakeet_tdt_ctc_110m-int8"
    engine = Engine(ctc, None, DEFAULT_EMBEDDING_MODEL, threads=args.threads)
    engine.build(compiled)
    out = {u["text"]: [c for c, _ in engine.recognizer.rank_by_meaning([u["text"]])] for u in utterances}
    if args.save_ranking:
        Path(args.save_ranking).write_text(json.dumps(out))
    return out


def run_llm(args, compiled, utterances) -> dict[str, Any]:
    tools = Tools(compiled)
    if args.chosen:
        # Only these are tools; a sentence for any other command should be refused.
        chosen = [c.strip() for c in args.chosen.split(",") if c.strip()]
        tools.specs = tools.subset(sorted(chosen))
        utterances = [u if u.get("expect") in chosen else {**u, "expect": None, "slots": {}} for u in utterances]
    llm = _tool_model(args)
    ranked = rankings(args, compiled, utterances) if args.top_k else {}

    def way_out(specs):
        return with_way_out(specs, llm.way_out)

    llm.load(way_out(tools.specs if not args.top_k else tools.subset(_largest(ranked, args.top_k, tools))), tools.signature)
    rows = []
    for utterance in utterances:
        subset = way_out(tools.subset(ranked[utterance["text"]][: args.top_k])) if args.top_k else None
        calls, raw, seconds = llm.call(utterance["text"], subset)
        command_id, slots, problems = None, {}, []
        for name, arguments in calls:
            if name == NOT_A_COMMAND:
                break
            command = compiled.commands.get(name)
            if command is None:
                problems.append(f"no command {name}")
                continue
            slots, problems = tools.slots(command, arguments, utterance["text"])
            if not problems:
                command_id = name
            break
        ok, why = expected(utterance, command_id, slots)
        rows.append(
            {"text": utterance["text"], "expect": utterance.get("expect"), "got": command_id, "slots": slots,
             "ok": ok, "why": why, "problems": problems, "raw": raw, "seconds": round(seconds, 3)}
        )
        print(f"{'ok ' if ok else 'BAD'} {seconds:5.2f}s {utterance['text']!r} -> {command_id} {slots if command_id else raw[:80]!r} {why}", flush=True)
    seconds = [r["seconds"] for r in rows]
    return {
        "mode": "llm",
        "llm": args.llm,
        "tools": len(tools.specs),
        "context": llm.llm.n_ctx(),
        "prefix_seconds": round(llm.load_seconds or 0, 2),
        "threads": args.threads,
        "top_k": args.top_k,
        "way_out": llm.way_out,
        "chosen": args.chosen,
        "accuracy": sum(r["ok"] for r in rows) / len(rows),
        "commands_ok": _rate([r for r in rows if r["expect"]]),
        "non_commands_ok": _rate([r for r in rows if not r["expect"]]),
        "wrong_command": sum(1 for r in rows if r["got"] and not r["ok"]),
        "median_seconds": statistics.median(seconds),
        "p90_seconds": sorted(seconds)[int(0.9 * (len(seconds) - 1))],
        "max_seconds": max(seconds),
        "rows": rows,
    }


def _tool_model(args) -> ToolModel:
    path = Path(args.model) if args.model else ToolModel.find_model(args.llm, download=True)
    llm = ToolModel(path, n_threads=args.threads, model=args.llm)
    if args.no_way_out:
        llm.way_out, llm.prompt = False, SYSTEM_PROMPT
    return llm


def _largest(ranked: dict[str, list[str]], k: int, tools: Tools) -> list[str]:
    """The K commands whose tools are longest: the context must hold any K."""
    sizes = {spec["function"]["name"]: len(json.dumps(spec)) for spec in tools.specs}
    return sorted(sizes, key=lambda c: -sizes[c])[:k]


def _rate(rows: list[dict[str, Any]]) -> float | None:
    return sum(r["ok"] for r in rows) / len(rows) if rows else None


async def run_chain(args, compiled, utterances) -> dict[str, Any]:
    from adapt_ctc.embedding import DEFAULT_EMBEDDING_MODEL  # pylint: disable=import-outside-toplevel

    from voice_call.engine import Engine, Satellite  # pylint: disable=import-outside-toplevel
    from voice_call.fallback import Fallback  # pylint: disable=import-outside-toplevel
    from voice_call.server import _pcm_to_float, tdt_models  # pylint: disable=import-outside-toplevel
    from voice_call.tts import Speech  # pylint: disable=import-outside-toplevel

    ctc = ROOT / "models/speech-to-phrase/stt_en_parakeet_tdt_ctc_110m-int8"
    engine = Engine(ctc, tdt_models(ROOT)["en"], DEFAULT_EMBEDDING_MODEL, threads=args.threads)
    engine.build(compiled)
    llm = None if args.no_llm else _tool_model(args)
    engine.fallback = Fallback(embedding_accept=args.embedding_accept, llm=llm, top_k=args.top_k)
    engine.fallback.prepare(engine, wait=True)
    speech = Speech(ROOT / "models/piper/en_GB-jenny_dioco-medium.onnx", ROOT / "sounds/acknowledge.mp3")
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
        "llm": None if llm is None else args.llm,
        "top_k": args.top_k,
        "accuracy": sum(r["ok"] for r in rows) / len(rows),
        "commands_ok": _rate([r for r in rows if r["expect"]]),
        "non_commands_ok": _rate([r for r in rows if not r["expect"]]),
        "by_step": by_step,
        "median_seconds": statistics.median(r["seconds"] for r in rows),
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=("llm", "chain"), default="llm")
    parser.add_argument("--llm", choices=sorted(TOOL_MODELS), default=DEFAULT_TOOL_MODEL)
    parser.add_argument("--model", help="the LLM's GGUF (default: from the Hugging Face cache)")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--embedding-accept", action="store_true")
    parser.add_argument("--top-k", type=int, help="give the LLM only the K commands closest in meaning")
    parser.add_argument("--ranking", help="per-sentence command ranking (JSON) instead of computing it")
    parser.add_argument("--save-ranking", help="write the ranking computed here")
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--no-way-out", action="store_true", help="without the not_a_command tool (and its prompt) for a model that has it")
    parser.add_argument("--chosen", help="only these commands (comma-separated) are tools, in --mode llm")
    parser.add_argument("--only", help="only utterances containing this")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    compiled = compile_store(library_store())
    utterances = yaml.safe_load(UTTERANCES.read_text())["utterances"]
    if args.only:
        utterances = [u for u in utterances if args.only in u["text"]]
    if args.mode == "llm":
        result = run_llm(args, compiled, utterances)
    else:
        result = asyncio.run(run_chain(args, compiled, utterances))
    summary = {k: v for k, v in result.items() if k != "rows"}
    print(json.dumps(summary, indent=1))
    if args.out:
        args.out.write_text(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
