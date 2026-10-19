#!/usr/bin/env python3
"""Questions through the app, on the smart-home quick-questions benchmark.

Each question is spoken by Piper and recognized as a call would be -- with the
library's commands in the bench home (scripts/bench_fallback.py) -- or, with
``--text``, handed to the skills as written. What each comes to:

- a command (it should not: these are questions),
- an exact answer, a skill's (graded against ``expected_answer``),
- the live notice, the live skill's (graded against the CSV's ``online_required``),
- nothing: what a Home Assistant conversation agent would get, when one is
  chosen (voice_call/agent.py), and otherwise the hint.

    scripts/bench_questions.py ~/Downloads/smart_home_quick_questions_benchmark.csv --out /tmp/q.json
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from bench_fallback import library_store  # noqa: E402

from voice_call import skills  # noqa: E402
from voice_call.skills.common import normalize  # noqa: E402
from voice_call.skills.live import is_live  # noqa: E402
from voice_call.store import compile_store  # noqa: E402


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9.]+", " ", str(text).lower().replace(",", "")).strip()


def exact_right(answer: dict, expected: str) -> bool:
    got = answer.get("text") or ""
    if answer.get("skill") == "spelling":
        return re.sub(r"[^A-Z]", "", got.split(" is spelled ")[-1]) == re.sub(r"[^A-Z]", "", expected.upper())
    want, said = _norm(expected), _norm(got)
    if want and want in said:
        return True
    numbers_want = re.findall(r"\d+(?:\.\d+)?", want)
    numbers_said = re.findall(r"\d+(?:\.\d+)?", said)
    return bool(numbers_want) and any(
        abs(float(numbers_want[0]) - float(n)) <= 0.051 * max(1.0, abs(float(numbers_want[0]))) for n in numbers_said
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("csv", type=Path)
    parser.add_argument("--text", action="store_true", help="the question as written, not spoken")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    from adapt_ctc.embedding import DEFAULT_EMBEDDING_MODEL  # pylint: disable=import-outside-toplevel

    from voice_call.engine import Engine, Satellite  # pylint: disable=import-outside-toplevel
    from voice_call.fallback import Fallback  # pylint: disable=import-outside-toplevel
    from voice_call.server import _pcm_to_float, tdt_models  # pylint: disable=import-outside-toplevel
    from voice_call.tts import Speech  # pylint: disable=import-outside-toplevel

    rows = list(csv.DictReader(args.csv.open()))
    if args.limit:
        rows = rows[: args.limit]
    engine = speech = None
    if not args.text:
        ctc = ROOT / "models/speech-to-phrase/stt_en_parakeet_tdt_ctc_110m-int8"
        engine = Engine(ctc, tdt_models(ROOT)["en"], DEFAULT_EMBEDDING_MODEL, threads=args.threads)
        engine.build(compile_store(library_store()))
        engine.fallback = Fallback(embedding_accept=True)
        speech = Speech(ROOT / "models/piper/en_GB-jenny_dioco-medium.onnx", ROOT / "sounds/acknowledge.mp3")
        await speech.start()
    satellite = Satellite("bench", "Bench", "kitchen", "Kitchen")
    out = []
    for row in rows:
        question = row["question"]
        started = time.perf_counter()
        if args.text:
            answer = skills.answer(question)
            heard, command = question, None
            answer = answer.to_json() if answer else None
        else:
            pcm = await speech.say(question)
            decision = engine.recognize_command(_pcm_to_float(pcm), satellite)
            heard, command, answer = decision.transcript, decision.command.id if decision.command else None, decision.answer
        seconds = time.perf_counter() - started
        # A question about now or here is no skill's (skills.live.is_live):
        # counted as "live" when the skills left it alone for that.
        kind = "command" if command else (answer or {}).get("kind") or ("live" if is_live(normalize(question)) else "nothing")
        live = row.get("online_required") == "yes" or row.get("resource_class") == "live_online"
        right = None
        if kind == "exact" and row.get("expected_answer"):
            # (None without one: "how many days until Christmas" depends on today.)
            right = exact_right(answer, row["expected_answer"])
        elif kind == "live":
            right = live
        out.append(
            {"question": question, "heard": heard, "kind": kind, "command": command, "answer": answer,
             "expected": row.get("expected_answer"), "live": live, "right": right, "seconds": round(seconds, 3)}
        )
        print(f"{kind:9s} {'' if right is None else ('ok ' if right else 'BAD')} {seconds:5.2f}s {question!r} -> "
              f"{command or (answer or {}).get('text')!r} [heard {heard!r}]", flush=True)
    kinds = Counter(o["kind"] for o in out)
    graded = [o for o in out if o["right"] is not None]
    summary = {
        "questions": len(out),
        "spoken": not args.text,
        "kinds": dict(kinds),
        "exact_right": f"{sum(o['right'] is True for o in out if o['kind'] == 'exact')}/"
        f"{sum(o['right'] is not None for o in out if o['kind'] == 'exact')} graded",
        "live_right": f"{sum(o['right'] is True for o in out if o['kind'] == 'live')}/{kinds['live']}",
        "live_missed": sum(1 for o in out if o["live"] and o["kind"] != "live"),
        "graded_right": f"{sum(o['right'] for o in graded)}/{len(graded)}",
    }
    print(json.dumps(summary, indent=1))
    if args.out:
        args.out.write_text(json.dumps({"summary": summary, "rows": out}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
