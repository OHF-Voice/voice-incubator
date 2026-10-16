#!/usr/bin/env python3
"""Can these custom wake words run on a satellite, and do they hear themselves?

For every microWakeWord manifest under the given directories (Tater's
catalog, an ESPHome model repository, the app's wake_words/), three checks:

1. **Lowered.** The app turns the ``.tflite`` into the op table the device runs
   (voice_call/mixednet.py). A graph it cannot walk is refused here, and would
   be refused by the app.
2. **Same numbers as TFLite.** The upstream host harness (mixednet-executor,
   the code the firmware compiles) and TFLite's reference kernels run over the
   same feature stream; the largest difference in the int8 output is reported.
   (The executor rounds as esp-nn does, so a difference of 1 here and there is
   expected and harmless.)
3. **Hears its own phrase.** Synthetic clips of the manifest's ``wake_word``
   (scripts/synth_wake_words.py, quiet before and after) through the detector
   at the manifest's cutoff. Piper's voices are not what a model was trained
   on and some names are spoken oddly, so this is a sanity check, not a
   recall figure: a model that hears none of them is suspect.

    scripts/check_wake_words.py ~/opt/Tater-Wake-Words --out /tmp/tater.jsonl
    scripts/check_wake_words.py --report /tmp/tater.jsonl

Needs mixednet-executor (``--upstream``) and a Python with ai_edge_litert
(``--tflite-python``) for check 2.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _corpus import WakeWord, features, read_wav  # noqa: E402

from voice_call.mixednet import pack  # noqa: E402
from voice_call.wake_words import MAX_CUTOFF, read_manifest, spoken  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
UPSTREAM = Path.home() / "opt/mixednet-executor"
TFLITE_PYTHON = Path.home() / ".pyenv/versions/micro-wake-word/bin/python"
SPEECH = ROOT / "tests/okay_nabu_turn_on_the_lights.wav"

REFERENCE = """
import sys
from pathlib import Path
import numpy as np
from ai_edge_litert.interpreter import Interpreter, OpResolverType
interpreter = Interpreter(model_path=sys.argv[1], experimental_op_resolver_type=OpResolverType.BUILTIN_REF)
interpreter.allocate_tensors()
inp = interpreter.get_input_details()[0]
out = interpreter.get_output_details()[0]
step = int(inp["shape"][1])
features = np.frombuffer(Path(sys.argv[2]).read_bytes(), dtype=np.int8).reshape(-1, 40)
for i in range(0, len(features) - step + 1, step):
    interpreter.set_tensor(inp["index"], features[i : i + step][np.newaxis, :, :])
    interpreter.invoke()
    print(int(interpreter.get_tensor(out["index"])[0][0]))
"""


def manifests(roots: list[Path]) -> list[tuple[str, Path, Path, dict]]:
    """(id, manifest, model, manifest data): one per model, the first
    manifest found for it (Tater ships an ESPHome twin beside each)."""
    seen: dict[Path, tuple] = {}
    for root in roots:
        for path in sorted(Path(root).rglob("*.json")):
            found = read_manifest(path)
            if found is None:
                continue
            data, tflite = found
            seen.setdefault(tflite.resolve(), (tflite.stem, path, tflite, data))
    return list(seen.values())


def check(job: tuple) -> dict:
    word_id, manifest_path, tflite, data, clips_dir, scratch, upstream, tflite_python = job
    micro = data.get("micro") or {}
    row: dict = {
        "id": word_id,
        "name": data.get("wake_word"),
        "manifest": str(manifest_path),
        "cutoff": micro.get("probability_cutoff"),
        "window": micro.get("sliding_window_size"),
        "step_ms": micro.get("feature_step_size"),
    }
    try:
        blob = pack(tflite)
        row["lowered"] = True
        row["blob_bytes"] = len(blob)
    except Exception as err:  # noqa: BLE001 - reported per model
        row["lowered"] = False
        row["error"] = f"{type(err).__name__}: {err}"
        return row

    work = Path(tempfile.mkdtemp(dir=scratch))
    try:
        symbol = "WAKE_MODEL"
        subprocess.run(
            [sys.executable, str(upstream / "compile_mixednet.py"), str(tflite), "-o", str(work / "model.cpp"), "-n", symbol],
            check=True, capture_output=True, text=True,
        )
        subprocess.run(
            ["g++", "-O2", "-std=gnu++17", f"-DMODEL_SYMBOL={symbol}", f"-I{upstream}", "-o", str(work / "run"),
             str(upstream / "mixednet.cpp"), str(work / "model.cpp"), str(upstream / "host_main.cpp")],
            check=True, capture_output=True, text=True,
        )
    except subprocess.CalledProcessError as err:
        row["executor"] = False
        row["error"] = (err.stderr or err.stdout or str(err))[-400:]
        shutil.rmtree(work, ignore_errors=True)
        return row
    row["executor"] = True

    manifest = work / "manifest.json"
    # At the cutoff the app runs it at.
    manifest.write_text(json.dumps({"micro": {"probability_cutoff": min(micro.get("probability_cutoff", 0.85), MAX_CUTOFF),
                                              "sliding_window_size": micro.get("sliding_window_size", 5)}}))
    wake = WakeWord(work / "run", manifest, work / "features.bin")

    # Check 2: against TFLite's reference kernels, on speech.
    if tflite_python and Path(tflite_python).exists():
        speech = read_wav(SPEECH)
        feats = work / "speech.bin"
        feats.write_bytes(features(speech).tobytes())
        ours = wake.probabilities(speech)
        result = subprocess.run([str(tflite_python), "-c", REFERENCE, str(tflite), str(feats)],
                                capture_output=True, text=True)
        if result.returncode == 0:
            ref = np.array([int(x) for x in result.stdout.split()], dtype=np.int32)
            n = min(len(ref), len(ours))
            ref_prob = ref[:n]  # uint8, as the executor prints it
            row["tflite_steps"] = n
            row["tflite_max_diff"] = int(np.abs(ours[:n] - ref_prob).max()) if n else None
        else:
            row["tflite_error"] = result.stderr[-300:]

    # Check 3: its own phrase.
    clips = sorted(Path(clips_dir).glob(f"{word_id}__*.wav")) if clips_dir else []
    peaks, hits = [], 0
    for clip in clips:
        probabilities = wake.probabilities(read_wav(clip))
        peaks.append(int(probabilities.max()) if len(probabilities) else 0)
        hits += wake.detect(probabilities) is not None
    if clips:
        row["clips"] = len(clips)
        row["detected"] = hits
        row["peak_median"] = float(np.median(peaks)) / 255
    shutil.rmtree(work, ignore_errors=True)
    return row


def report(path: Path) -> None:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line]
    n = len(rows)
    lowered = [r for r in rows if r.get("lowered")]
    print(f"{n} models; lowered for the device: {len(lowered)}; executor built: {sum(1 for r in rows if r.get('executor'))}")
    errors = Counter(r.get("error", "").split(":")[0] for r in rows if not r.get("lowered"))
    if errors:
        print("not lowered:", dict(errors))
        for r in rows:
            if not r.get("lowered"):
                print(f"  {r['id']}: {r.get('error')}")
    diffs = Counter(r.get("tflite_max_diff") for r in rows if "tflite_max_diff" in r)
    print("largest output difference from TFLite (int8 steps):", dict(sorted(diffs.items(), key=lambda kv: (kv[0] is None, kv[0]))))
    with_clips = [r for r in rows if r.get("clips")]
    if with_clips:
        rate = [r["detected"] / r["clips"] for r in with_clips]
        none = [r for r in with_clips if r["detected"] == 0]
        print(f"own phrase (synthetic, manifest cutoff): median {np.median(rate):.0%} of clips detected; "
              f"{sum(1 for x in rate if x >= 0.5)}/{len(rate)} models detect half or more; {len(none)} detect none")
        for r in none[:40]:
            print(f"  heard none: {r['id']} ({r['name']!r}, cutoff {r['cutoff']}, median peak {r['peak_median']:.2f})")
    cutoffs = Counter(r.get("cutoff") for r in rows)
    print("manifest cutoffs:", dict(sorted(cutoffs.items(), key=lambda kv: (kv[0] is None, kv[0]))))
    steps = Counter(r.get("step_ms") for r in rows)
    print("feature step (ms):", dict(steps))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("roots", nargs="*", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--clips", type=int, default=6, help="synthetic clips of each wake word (0: skip)")
    parser.add_argument("--upstream", type=Path, default=UPSTREAM)
    parser.add_argument("--tflite-python", type=Path, default=TFLITE_PYTHON)
    parser.add_argument("--workers", type=int, default=os.cpu_count() or 4)
    args = parser.parse_args()
    if args.report:
        report(args.report)
        return 0
    if not args.roots or not args.out:
        parser.error("directories and --out are needed")

    found = manifests(args.roots)
    print(f"{len(found)} wake words", file=sys.stderr)
    scratch = Path(tempfile.mkdtemp(prefix="check-wake-"))
    clips_dir = None
    if args.clips:
        clips_dir = scratch / "clips"
        clips_dir.mkdir()
        # One phrases file; the synthesizer names clips by the phrase's slug,
        # so each id is synthesized under its own name.
        phrases = scratch / "phrases.txt"
        by_slug: dict[str, list[str]] = {}
        texts: dict[str, str] = {}
        for word_id, _, _, data in found:
            text = spoken(str(data.get("wake_word") or word_id))
            slug = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
            by_slug.setdefault(slug, []).append(word_id)
            texts.setdefault(slug, text)
        phrases.write_text("\n".join(texts.values()) + "\n")
        subprocess.run(
            [sys.executable, str(Path(__file__).parent / "synth_wake_words.py"), "--phrases-file", str(phrases),
             "--count", str(args.clips), "--out", str(clips_dir), "--workers", str(args.workers)],
            check=True,
        )
        # Rename clips from the phrase's slug to every id that says it.
        for clip in list(clips_dir.glob("*.wav")):
            slug, rest = clip.name.split("__", 1)
            for word_id in by_slug.get(slug, []):
                target = clips_dir / f"{word_id}__{rest}"
                if target != clip:
                    shutil.copy(clip, target)
    jobs = [(w, m, t, d, clips_dir, scratch, args.upstream, args.tflite_python) for w, m, t, d in found]
    done = 0
    with args.out.open("w") as out, ProcessPoolExecutor(args.workers) as pool:
        for row in pool.map(check, jobs, chunksize=2):
            out.write(json.dumps(row) + "\n")
            done += 1
            if done % 100 == 0:
                print(f"{done}/{len(jobs)}", file=sys.stderr, flush=True)
    shutil.rmtree(scratch, ignore_errors=True)
    report(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
