"""Time each stage of a call on this machine.

    python -m scripts.benchmark [--runs 5] [--wav FILE] [--skip-recognize]
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from voice_labs.endpoint import Endpointer  # noqa: E402
from voice_labs.protocol import BYTE_RATE, ms_to_bytes  # noqa: E402
from voice_labs.wake import WakeVerifier  # noqa: E402


def _report(label: str, samples: list[float], audio_seconds: float | None = None) -> None:
    best, median = min(samples), statistics.median(samples)
    extra = ""
    if audio_seconds:
        extra = f"   {median / 1000 / audio_seconds:.2f}x realtime"
    print(f"  {label:<24} median {median:7.1f} ms   best {best:7.1f} ms{extra}")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wav", default=str(ROOT / "tests/okay_nabu_turn_on_the_lights.wav"))
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument(
        "--window",
        type=float,
        default=1.5,
        help="seconds of pre-roll handed to the verifier (0 = whole file)",
    )
    parser.add_argument("--skip-recognize", action="store_true")
    parser.add_argument("--threads", type=int, default=2, help="ONNX threads per model, as the app")
    parser.add_argument("--text", default="Turned on the light.")
    args = parser.parse_args()

    with wave.open(args.wav) as wav:
        pcm = wav.readframes(wav.getnframes())
    window = pcm if args.window <= 0 else pcm[: ms_to_bytes(args.window * 1000)]
    print(
        f"audio: {Path(args.wav).name} ({len(pcm) / BYTE_RATE:.2f}s), "
        f"wake window {len(window) / BYTE_RATE:.2f}s, {args.runs} runs\n"
    )

    print("startup")
    started = time.monotonic()
    wake = WakeVerifier(ROOT / "wake.yaml")
    await wake.start()
    print(f"  {'wake verifier load':<24} {(time.monotonic() - started) * 1000:7.0f} ms")

    engine = None
    if not args.skip_recognize:
        from voice_labs.engine import Engine
        from voice_labs.store import compile_store
        from voice_labs.web import load_library

        started = time.monotonic()
        ctc_dir = ROOT / "models/speech-to-phrase/stt_en_parakeet_tdt_ctc_110m-int8"
        if not (ctc_dir / "model.onnx").exists():
            ctc_dir = ROOT / "models/speech-to-phrase/stt_en_parakeet_tdt_ctc_110m"
        engine = Engine(ctc_dir, ROOT / "models/parakeet-tdt-int8", None, threads=args.threads)
        print(f"  {'models load':<24} {(time.monotonic() - started) * 1000:7.0f} ms")
        # The library's commands: a realistic graph, not a single sentence.
        started = time.monotonic()
        commands = {
            e["id"]: {k: v for k, v in e["spec"].items() if k != "needs"} for e in load_library()
        }
        engine.build(compile_store({"commands": commands}))
        print(f"  {'commands build':<24} {(time.monotonic() - started) * 1000:7.0f} ms")

    from voice_labs.tts import Speech

    started = time.monotonic()
    speech = Speech(
        ROOT / "models/piper/en_GB-jenny_dioco-medium.onnx",
        ROOT / "models",
    )
    await speech.start()
    print(f"  {'piper load':<24} {(time.monotonic() - started) * 1000:7.0f} ms")

    print("\nper call")

    timings: list[float] = []
    result = None
    for _ in range(args.runs):
        started = time.monotonic()
        result = await wake.verify(window)
        timings.append((time.monotonic() - started) * 1000)
    _report("wake verify", timings, len(window) / BYTE_RATE)
    assert result is not None
    print(f"  {'':<24} accepted={result.accepted} split={result.split}s")

    command = pcm[ms_to_bytes(result.split * 1000) :] if result.split else pcm
    command_seconds = len(command) / BYTE_RATE

    timings = []
    for _ in range(args.runs):
        started = time.monotonic()
        endpointer = Endpointer()
        endpointer.process(command)
        timings.append((time.monotonic() - started) * 1000)
    _report("vad endpointing", timings, command_seconds)

    if engine is not None:
        import numpy as np

        from voice_labs.engine import Satellite

        # As the app does it: the whole call, the wake phrase placed again in
        # the CTC output, the commands recognized after it.
        call = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        kaldi_split = result.split or len(window) / BYTE_RATE
        timings = []
        for _ in range(args.runs):
            started = time.monotonic()
            decision, split = engine.recognize_call(
                call, result.wake_start, kaldi_split, Satellite("bench", "bench")
            )
            timings.append((time.monotonic() - started) * 1000)
        _report("recognize", timings, len(call) / 16000 - split.cut)
        print(
            f"  {'':<24} command={decision.command.id if decision.command else None} "
            f"slots={decision.slots} cut={split.cut:.2f}s (Kaldi {split.kaldi_split:.2f}s)"
        )

    timings = []
    for _ in range(args.runs):
        started = time.monotonic()
        audio = await speech.say(args.text)
        timings.append((time.monotonic() - started) * 1000)
    _report("piper synthesize", timings)
    print(f"  {'':<24} {len(audio) / BYTE_RATE:.2f}s of speech for {args.text!r}")

    await wake.stop()


if __name__ == "__main__":
    asyncio.run(main())
