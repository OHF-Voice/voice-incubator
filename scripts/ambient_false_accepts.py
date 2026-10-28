"""Wake words' false accepts per hour on ambient audio (a microWakeWord
feature set: DiPCo, say), with and without microWakeWord's VAD gate, by the
voice_call firmware's detection rule (wake_detector.cpp):

- each model runs every 3 feature slices (30 ms) and keeps its last 5 uint8
  outputs; a wake word fires when their sum > cutoff * 5 (cutoff in 0-255,
  rounded as the firmware does), unless muted;
- after firing its window is zeroed and it is muted 100 slices, counted down
  only on slices whose latest output is below the cutoff;
- the gate: the VAD's last 5 outputs summed > its cutoff (0.5) * 5, read every
  slice; a suppressed detection doesn't zero or mute anything.

    scripts/ambient_false_accepts.py MMAP_DIR MODEL.tflite[:name] ... --vad models/wake/vad.tflite --out results.json

MMAP_DIR: a microWakeWord feature set (RaggedMmap), e.g. dinner_party_eval's
testing_ambient/dipco_full_eval_mmap (91 h). Needs ai_edge_litert and
mmap_ninja (the wake trainer's Python has both).
"""
import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
from ai_edge_litert.interpreter import Interpreter
from mmap_ninja.ragged import RaggedMmap

WINDOW, MUTE = 5, 100
CUTOFFS = (0.3, 0.4, 0.5, 0.56, 0.69, 0.85)


def u8(c):
    return int(min(255, max(0, int(c * 255.0 + 0.5))))


def probabilities(model_path, spectrogram):
    """The model's uint8 output at each 30 ms step (streaming, state kept)."""
    it = Interpreter(model_path=str(model_path))
    it.allocate_tensors()
    inp, out = it.get_input_details()[0], it.get_output_details()[0]
    scale, zero = inp["quantization"]
    n = inp["shape"][1]
    q = np.clip(np.round(spectrogram / scale + zero), -128, 127).astype(np.int8)
    res = np.empty(len(q) // n, dtype=np.uint8)
    for k in range(len(res)):
        it.set_tensor(inp["index"], q[k * n:(k + 1) * n].reshape(inp["shape"]))
        it.invoke()
        res[k] = it.get_tensor(out["index"]).reshape(-1)[0]
    return res


def detections(ww, vad, cutoff, gated):
    """Firing times (in 30 ms steps) by the firmware's rule."""
    cut, vcut = u8(cutoff), u8(0.5)
    recent, vrecent = [0] * WINDOW, [0] * WINDOW
    ignore, out = -MUTE, []
    for k in range(len(ww)):
        recent[k % WINDOW] = int(ww[k])
        vrecent[k % WINDOW] = int(vad[k])
        if recent[k % WINDOW] < cut:
            ignore = min(ignore + 1, 0)
        if ignore >= 0 and sum(recent) > cut * WINDOW:
            if not gated or sum(vrecent) > vcut * WINDOW:
                out.append(k)
                recent = [0] * WINDOW
                ignore = -MUTE
                continue
        if recent[k % WINDOW] < cut:  # the two slices to the next inference
            ignore = min(ignore + 2, 0)
    return out


def one(job):
    mmap_dir, index, models, vad_path = job
    spec = np.asarray(RaggedMmap(mmap_dir)[index], dtype=np.float32)
    vad = probabilities(vad_path, spec)
    result = {"index": index, "seconds": len(spec) * 0.01, "vad_open": float(np.mean(
        np.convolve(vad.astype(int), np.ones(WINDOW, int), "full")[: len(vad)] > u8(0.5) * WINDOW))}
    for name, path in models:
        ww = probabilities(path, spec)
        m = min(len(ww), len(vad))
        result[name] = {f"{c}": {"gated": detections(ww[:m], vad[:m], c, True),
                                 "ungated": detections(ww[:m], vad[:m], c, False)} for c in CUTOFFS}
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mmap_dir")
    ap.add_argument("models", nargs="+")
    ap.add_argument("--vad", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--jobs", type=int, default=14)
    args = ap.parse_args()
    models = [(m.split(":")[1] if ":" in m else Path(m).stem, m.split(":")[0]) for m in args.models]
    count = len(RaggedMmap(args.mmap_dir))
    print(f"{count} recordings", flush=True)
    results = []
    with ProcessPoolExecutor(args.jobs) as ex:
        for i, r in enumerate(ex.map(one, [(args.mmap_dir, i, models, args.vad) for i in range(count)])):
            results.append(r)
            if (i + 1) % 10 == 0 or i + 1 == count:
                print(f"{i + 1}/{count}", flush=True)
    Path(args.out).write_text(json.dumps(results))
    hours = sum(r["seconds"] for r in results) / 3600
    print(f"\n{hours:.2f} h of audio; the VAD gate open {np.average([r['vad_open'] for r in results], weights=[r['seconds'] for r in results]):.1%} of the time")
    for name, _ in models:
        print(f"\n{name}: false accepts (per hour)")
        for c in CUTOFFS:
            g = sum(len(r[name][f"{c}"]["gated"]) for r in results)
            u = sum(len(r[name][f"{c}"]["ungated"]) for r in results)
            print(f"  cutoff {c:<5} gated {g:5d} ({g / hours:6.2f}/h)   ungated {u:5d} ({u / hours:6.2f}/h)")


if __name__ == "__main__":
    sys.exit(main())
