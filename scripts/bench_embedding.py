#!/usr/bin/env python3
"""Embedding models compared on what the app asks of one, with the library's
commands and the fallback's sentences (tests/fallback_utterances*.yaml, as
scripts/bench_fallback.py): the closest command by meaning (hints, and the
meaning step), whether the meaning step would run it at thresholds set for
the model, and whether the right command is among the closest eight (what
GLiNER is given). Also each model's time per sentence and its memory.

Commands are compared by GLiNER's label: a command's versions (here, a
device, a place) are one command to meaning. Any model the app's embedder
loads (EmbeddingGemma); scripts/export_embedding.py says what other models
measured.

    scripts/bench_embedding.py --language en models/embedding/e5-small-v2 ...
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.bench_fallback import UTTERANCES, library_store  # noqa: E402
from voice_call import languages  # noqa: E402
from voice_call.store import compile_store  # noqa: E402

TOP_K = 8

MEMORY = """
import sys, numpy as np
sys.path.insert(0, sys.argv[2])
def rss():
    return next(int(l.split()[1]) for l in open('/proc/self/status') if l.startswith('VmRSS:')) / 1024
from adapt_ctc.embedding import Embedder
from adapt_ctc.runtime import release_memory
base = rss()
e = Embedder(sys.argv[1], 2)
loaded = rss()
texts = [l.strip() for l in open(sys.argv[3]) if l.strip()]
for i in range(0, len(texts), 16):
    e.embed(texts[i:i + 16], document=True)
release_memory()
peak = next(int(l.split()[1]) for l in open('/proc/self/status') if l.startswith('VmHWM:')) / 1024
print(f"{loaded - base:.0f} {peak - base:.0f} {rss() - base:.0f}")
"""


def measure(model: str, language: str, compiled, utterances) -> dict:
    from voice_call.commands import command_of  # pylint: disable=import-outside-toplevel
    from voice_call.engine import Engine  # pylint: disable=import-outside-toplevel
    from voice_call.fallback import label_of  # pylint: disable=import-outside-toplevel

    started = time.perf_counter()
    engine = Engine(languages.current().ctc_dir(ROOT), None, Path(model), threads=2)
    engine.build(compiled)
    built = time.perf_counter() - started
    recognizer = engine.recognizer
    assert engine.embedder is not None, f"no embedding model at {model}"

    def label(intent: str | None):
        command = compiled.commands.get(command_of(intent)) if intent else None
        return label_of(command) if command is not None else None

    rows = []
    for utterance in utterances:
        want = label(utterance.get("expect"))
        begun = time.perf_counter()
        closest = recognizer.closest_by_meaning([utterance["text"]], group=label)
        seconds = time.perf_counter() - begun
        ranked: list = []
        for intent, _ in recognizer.rank_by_meaning([utterance["text"]]):
            name = label(intent)
            if name is not None and name not in ranked:
                ranked.append(name)
        got = label(closest["intent"]) if closest else None
        similarity = float(closest["similarity"]) if closest else 0.0
        rows.append({
            "text": utterance["text"], "want": want, "got": got, "similarity": round(similarity, 4),
            "margin": round(similarity - float((closest or {}).get("next_similarity") or 0.0), 4),
            "rank": ranked.index(want) + 1 if want in ranked else None, "seconds": round(seconds, 4),
        })
    return {"model": Path(model).name, "language": language, "build_seconds": round(built, 1), "rows": rows}


def sweep(rows: list[dict]) -> dict:
    """The most commands the meaning step runs right, with no command (or
    one) run wrong, over thresholds set for the model; and at the app's."""
    similarities = sorted({r["similarity"] for r in rows})
    best: dict = {}
    for accept in similarities:
        for margin in (0.0, 0.01, 0.02, 0.03, 0.05, 0.075, 0.1):
            taken = [r for r in rows if r["similarity"] >= accept and r["margin"] >= margin]
            right = sum(1 for r in taken if r["want"] and r["got"] == r["want"])
            wrong = len(taken) - right
            for allowed in (0, 1):
                if wrong <= allowed and right > best.get(allowed, {}).get("right", -1):
                    best[allowed] = {"right": right, "wrong": wrong, "accept": accept, "margin": margin}
    return best


def at(rows: list[dict], accept: float, margin: float) -> dict:
    taken = [r for r in rows if r["similarity"] >= accept and r["margin"] >= margin]
    right = sum(1 for r in taken if r["want"] and r["got"] == r["want"])
    return {"right": right, "wrong": len(taken) - right, "accept": accept, "margin": margin}


def memory(model: str, texts: list[str]) -> tuple[float, float, float]:
    path = Path("/tmp") / "bench_embedding_texts.txt"
    path.write_text("\n".join(texts))
    out = subprocess.run([sys.executable, "-c", MEMORY, model, str(ROOT), str(path)], capture_output=True, text=True, check=True)
    loaded, peak, after = (float(v) for v in out.stdout.split())
    return loaded, peak, after


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("models", nargs="+", help="embedding model folders (EmbeddingGemma's snapshot, or exported)")
    parser.add_argument("--language", default=languages.DEFAULT)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    languages.use(args.language)
    path = UTTERANCES if args.language == "en" else UTTERANCES.with_name(f"fallback_utterances.{args.language}.yaml")
    data = yaml.safe_load(path.read_text("utf-8"))
    compiled = compile_store(library_store(None, data.get("home")))
    utterances = data["utterances"]
    examples = [e for spec in (compiled.data.get("commands") or {}).values() for e in (spec.get("examples") or [])][:400]
    results = []
    commands = sum(1 for u in utterances if u.get("expect"))
    print(f"{args.language}: {len(utterances)} sentences, {commands} for a command, {len(utterances) - commands} for none; "
          f"{len(compiled.commands)} library commands")
    print(f"{'model':<26}{'closest right':>14}{'in top 8':>10}{'runs right, 0 wrong (at)':>32}{'1 wrong (at)':>24}"
          f"{'at 0.80/0.05':>15}{'ms/sentence':>12}{'MB loaded/peak/after':>22}")
    for model in args.models:
        result = measure(model, args.language, compiled, utterances)
        rows = result["rows"]
        with_command = [r for r in rows if r["want"]]
        result["closest_right"] = sum(1 for r in with_command if r["got"] == r["want"])
        result["in_top"] = sum(1 for r in with_command if r["rank"] is not None and r["rank"] <= TOP_K)
        result["best"] = sweep(rows)
        result["at_app"] = at(rows, 0.80, 0.05)
        result["ms"] = round(1000 * statistics.median(r["seconds"] for r in rows), 1)
        result["memory"] = memory(model, examples)
        results.append(result)
        best0, best1 = result["best"].get(0, {}), result["best"].get(1, {})
        show = lambda b: f"{b.get('right', 0)} ({b.get('accept', 0):.3f}/{b.get('margin', 0):.3f})"  # noqa: E731
        print(f"{result['model']:<26}{result['closest_right']:>8}/{len(with_command):<5}{result['in_top']:>6}/{len(with_command):<3}"
              f"{show(best0):>32}{show(best1):>24}"
              f"{result['at_app']['right']:>8} / {result['at_app']['wrong']:<4}{result['ms']:>12}"
              f"{'/'.join(f'{v:.0f}' for v in result['memory']):>22}", flush=True)
    if args.out:
        args.out.write_text(json.dumps(results, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
