"""Is the TDT second pass worth running, and can it overlap the CTC pass?

Measures, on whatever machine it runs on:
  * CTC-grammar decode alone (what every call already pays)
  * TDT decode alone
  * the two run sequentially  (what a strict "only on failure" second pass costs)
  * the two run concurrently  (what speculating costs, and saves)
plus peak RSS, so the memory headroom is visible.
"""

from __future__ import annotations

import argparse
import asyncio
import resource
import statistics
import sys
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from voice_call.recognize import CommandRecognizer  # noqa: E402
from voice_call.tdt import TdtTranscriber  # noqa: E402


def rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


async def timed(label: str, fn, runs: int) -> float:
    samples = []
    for _ in range(runs):
        started = time.monotonic()
        await fn()
        samples.append((time.monotonic() - started) * 1000)
    median = statistics.median(samples)
    print(f"  {label:<28} median {median:7.1f} ms   best {min(samples):7.1f} ms")
    return median


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wav", default=str(ROOT / "tests/okay_nabu_turn_on_the_lights.wav"))
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--tdt-threads", type=int, default=2)
    args = parser.parse_args()

    with wave.open(args.wav) as wav:
        pcm = wav.readframes(wav.getnframes())[int(1.35 * 32000) // 2 * 2 :]
    print(f"command audio: {len(pcm) / 32000:.2f}s, {args.runs} runs\n")

    print(f"baseline RSS {rss_mb():.0f} MB")
    recognizer = CommandRecognizer(
        ROOT / "models/speech-to-phrase/stt_en_parakeet_tdt_ctc_110m"
    )
    await recognizer.start(["turn on the lights"])
    print(f"  + speech-to-phrase (CTC) -> {rss_mb():.0f} MB")

    tdt = TdtTranscriber(ROOT / "models/parakeet-tdt", num_threads=args.tdt_threads)
    started = time.monotonic()
    await tdt.start()
    load_ms = (time.monotonic() - started) * 1000
    print(f"  + parakeet TDT           -> {rss_mb():.0f} MB   (load {load_ms:.0f} ms)")
    if not tdt.available:
        return

    print("\nlatency")
    ctc = await timed("ctc grammar only", lambda: recognizer.recognize(pcm), args.runs)
    tdt_ms = await timed("tdt only", lambda: tdt.transcribe(pcm), args.runs)

    async def sequential():
        await recognizer.recognize(pcm)
        await tdt.transcribe(pcm)

    async def concurrent():
        await asyncio.gather(recognizer.recognize(pcm), tdt.transcribe(pcm))

    seq = await timed("sequential (ctc then tdt)", sequential, args.runs)
    par = await timed("concurrent (both at once)", concurrent, args.runs)

    print(
        f"\n  contention: concurrent is {par / max(ctc, tdt_ms):.2f}x the slower pass alone"
        f"\n  speculating saves {seq - par:.0f} ms on a miss, costs {par - ctc:.0f} ms on a hit"
    )
    print(f"\npeak RSS {rss_mb():.0f} MB")


if __name__ == "__main__":
    asyncio.run(main())
