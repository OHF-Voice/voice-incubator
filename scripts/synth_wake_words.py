#!/usr/bin/env python3
"""Synthetic wake words and near misses, for a wake word with no recordings.

Every phrase is spoken by many Piper voices (each speaker of the multi-speaker
ones counts as a voice), at varied speed and expressiveness, then put in a room
some of the time and under some noise. One second of quiet either side lets the
on-device detector warm up and fire after the phrase, as it would live.

    scripts/synth_wake_words.py --phrase "hey jarvis" --count 600 \\
        --out /tmp/hey_jarvis/positives
    scripts/synth_wake_words.py --phrases-file confusables.txt --count 12 \\
        --out /tmp/hey_jarvis/confusables

``--count`` is per phrase. File names carry the phrase, voice and speaker.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import wave
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

RATE = 16000
VOICES = Path("/media/cache/piper-voices/en")
RIRS = Path("/media/cache/ww_datasets/MIT_environmental_impulse_responses/16khz")
NOISES = Path("/media/cache/ww_datasets/ambient_noises")

_W: dict = {}


def _voices(root: Path) -> list[tuple[Path, int]]:
    """(model, speaker) pairs: every speaker of a multi-speaker voice."""
    out = []
    for onnx in sorted(root.rglob("*.onnx")):
        config = json.loads(onnx.with_suffix(".onnx.json").read_text())
        speakers = int(config.get("num_speakers") or 1)
        out.extend((onnx, s if speakers > 1 else None) for s in range(min(speakers, 200)))
    return out


def _init(seed: int) -> None:
    _W["voices"] = {}
    _W["rirs"] = sorted(RIRS.glob("*.wav")) if RIRS.is_dir() else []
    _W["noises"] = sorted(NOISES.rglob("*.wav")) if NOISES.is_dir() else []
    _W["seed"] = seed


def _read(path: Path) -> np.ndarray:
    import soundfile as sf
    import soxr

    audio, rate = sf.read(str(path), dtype="float32", always_2d=True)
    audio = audio[:, 0]
    return soxr.resample(audio, rate, RATE) if rate != RATE else audio


def _synthesize(job: tuple) -> str | None:
    import soxr
    from piper import PiperVoice, SynthesisConfig

    index, phrase, model, speaker, out_dir = job
    rng = random.Random(f"{_W['seed']}-{index}-{phrase}")
    voice = _W["voices"].get(model)
    if voice is None:
        voice = _W["voices"][model] = PiperVoice.load(model)
    config = SynthesisConfig(
        speaker_id=speaker,
        length_scale=rng.uniform(0.85, 1.3),
        noise_scale=rng.uniform(0.4, 0.9),
        noise_w_scale=rng.uniform(0.5, 1.0),
    )
    chunks = [c.audio_float_array for c in voice.synthesize(phrase, syn_config=config)]
    if not chunks:
        return None
    audio = np.concatenate(chunks).astype(np.float32)
    audio = soxr.resample(audio, voice.config.sample_rate, RATE).astype(np.float32)
    audio /= max(1e-6, float(np.abs(audio).max()))

    if _W["rirs"] and rng.random() < 0.5:
        rir = _read(rng.choice(_W["rirs"]))
        rir = rir[: RATE // 2] / max(1e-6, float(np.abs(rir).max()))
        audio = np.convolve(audio, rir)[: len(audio) + RATE // 4]
        audio /= max(1e-6, float(np.abs(audio).max()))

    pad = np.zeros(RATE, dtype=np.float32)
    audio = np.concatenate([pad, audio, pad])
    if _W["noises"] and rng.random() < 0.7:
        noise = _read(rng.choice(_W["noises"]))
        if len(noise) < len(audio):
            noise = np.tile(noise, len(audio) // max(1, len(noise)) + 1)
        start = rng.randrange(0, len(noise) - len(audio) + 1)
        noise = noise[start : start + len(audio)]
        speech_power = float(np.mean(audio[RATE:-RATE] ** 2)) + 1e-9
        noise_power = float(np.mean(noise**2)) + 1e-9
        snr = rng.uniform(10.0, 30.0)
        audio = audio + noise * np.sqrt(speech_power / (noise_power * 10 ** (snr / 10)))
    level = rng.uniform(0.1, 0.7)
    audio = audio / max(1e-6, float(np.abs(audio).max())) * level

    slug = re.sub(r"[^a-z0-9]+", "_", phrase.lower()).strip("_")
    name = f"{slug}__{model.stem}__{speaker if speaker is not None else 0}__{index}.wav"
    path = Path(out_dir) / name
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes((np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes())
    return name


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--phrase", action="append", default=[])
    parser.add_argument("--phrases-file", type=Path)
    parser.add_argument("--count", type=int, default=100, help="clips per phrase")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--voices", type=Path, default=VOICES)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    phrases = list(args.phrase)
    if args.phrases_file:
        phrases += [line.strip() for line in args.phrases_file.read_text().splitlines() if line.strip() and not line.startswith("#")]
    if not phrases:
        parser.error("no phrases")
    args.out.mkdir(parents=True, exist_ok=True)

    voices = _voices(args.voices)
    rng = random.Random(args.seed)
    jobs = []
    for phrase in phrases:
        for index in range(args.count):
            model, speaker = rng.choice(voices)
            jobs.append((index, phrase, model, speaker, str(args.out)))
    # Grouped by voice so each worker loads few models.
    jobs.sort(key=lambda j: (str(j[2]), j[3] or 0))
    done = 0
    with ProcessPoolExecutor(args.workers, initializer=_init, initargs=(args.seed,)) as pool:
        for name in pool.map(_synthesize, jobs, chunksize=8):
            done += name is not None
    print(f"{done} clips in {args.out} ({len(phrases)} phrases, {len(voices)} voices)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
