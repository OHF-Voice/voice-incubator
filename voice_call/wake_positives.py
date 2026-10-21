"""A new wake word's positives: said by Piper, made into what the detector sees.

Run by the app (voice_call/wake_training.py) as a process of its own, with
the app's Python -- its Piper -- and below the app's priority::

    python -m voice_call.wake_positives --phrase "hey computer" \\
        --voice models/piper/en_US-libritts_r-medium.onnx --out <dir>

Each clip is the phrase in one of the voice's speakers, at a random pace and
expression, followed by a moment of silence (where the detector fires), put
at the end of a few seconds of quiet, then given a room (an impulse
response: the kit's, or a synthetic one) and noise behind it (the kit's
background recordings, or colored noise), at a random level -- as
microWakeWord's own Augmentation does. Its features are the micro frontend's,
as the device makes them, and only the frames the model looks at are kept:
``frames`` at the end, ``slides`` times, each ending one frame earlier.

Writes ``training.npy`` and ``validation.npy`` (uint16, ``(n, frames, 40)``,
as microWakeWord stores unscaled features) and prints its progress as JSON
lines.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import wave
from pathlib import Path

import numpy as np

RATE = 16000
FEATURES = 40
FRAME_SAMPLES = 160
"""The frontend's step: 10 ms."""
SCALE = 25.6
"""pymicro-features returns the frontend's uint16 output divided by this."""
CLIP_SECONDS = 3.2
"""Each augmented clip, as microWakeWord's ``augmentation_duration_s``."""
MIN_SECONDS, MAX_SECONDS = 0.3, 3.0
"""A clip Piper made shorter or longer than this is dropped (a glitch)."""
MIN_RMS = 0.005


def variants(phrase: str) -> list[str]:
    """Ways of writing the phrase, which Piper says a little differently."""
    plain = " ".join(phrase.split())
    words = plain.split()
    out = [plain, plain + "!", plain + ".", plain.capitalize() + "?", plain.capitalize() + "!"]
    if len(words) > 1 and words[0].lower() in ("hey", "hi", "okay", "ok", "hello", "yo"):
        out.append(f"{words[0]}, {' '.join(words[1:])}!")
    return out


def _read_wav(path: Path) -> np.ndarray | None:
    try:
        with wave.open(str(path), "rb") as wav:
            if wav.getsampwidth() != 2:
                return None
            audio = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
            if wav.getnchannels() > 1:
                audio = audio.reshape(-1, wav.getnchannels())[:, 0]
            rate = wav.getframerate()
    except (OSError, wave.Error, EOFError):
        return None
    if rate != RATE:
        import soxr  # pylint: disable=import-outside-toplevel

        audio = soxr.resample(audio, rate, RATE).astype(np.float32)
    return audio


def _wavs(directory: Path | None) -> list[Path]:
    if directory is None or not directory.is_dir():
        return []
    return sorted(directory.rglob("*.wav"))


def synthetic_room(rng: np.random.Generator) -> np.ndarray:
    """An impulse response: the direct sound, then exponentially decaying
    reflections (RT60 from a small room to a large one)."""
    rt60 = rng.uniform(0.15, 0.9)
    length = int(RATE * min(1.0, rt60))
    t = np.arange(length) / RATE
    tail = rng.standard_normal(length) * np.exp(-6.9 * t / rt60)
    delay = int(RATE * rng.uniform(0.001, 0.006))
    tail[:delay] = 0.0
    impulse = tail * rng.uniform(0.2, 0.6)
    impulse[0] = 1.0
    return (impulse / np.sqrt(np.sum(impulse**2))).astype(np.float32)


def colored_noise(rng: np.random.Generator, samples: int) -> np.ndarray:
    """White, pink or brown noise."""
    white = rng.standard_normal(samples)
    exponent = rng.choice([0.0, 1.0, 2.0])
    if exponent == 0.0:
        return white.astype(np.float32)
    spectrum = np.fft.rfft(white)
    freqs = np.fft.rfftfreq(samples)
    freqs[0] = freqs[1]
    noise = np.fft.irfft(spectrum / freqs ** (exponent / 2), samples)
    return (noise / (np.std(noise) + 1e-9)).astype(np.float32)


class Augmenter:
    """A room and noise behind the clip, at a level, as microWakeWord's
    Augmentation does (RIR 0.8, background 0.75, gain always)."""

    def __init__(self, rng: np.random.Generator, rirs: list[Path], backgrounds: list[Path]) -> None:
        self.rng = rng
        self.rirs = rirs
        self.backgrounds = backgrounds
        self._cache: dict[Path, np.ndarray | None] = {}

    def _load(self, path: Path) -> np.ndarray | None:
        if path not in self._cache:
            if len(self._cache) > 64:
                self._cache.pop(next(iter(self._cache)))
            self._cache[path] = _read_wav(path)
        return self._cache[path]

    def __call__(self, audio: np.ndarray) -> np.ndarray:
        from scipy.signal import fftconvolve  # pylint: disable=import-outside-toplevel

        rng = self.rng
        if rng.random() < 0.8:
            impulse = None
            if self.rirs:
                impulse = self._load(self.rirs[rng.integers(len(self.rirs))])
                if impulse is not None and impulse.size:
                    impulse = impulse[: RATE] / (np.sqrt(np.sum(impulse[:RATE] ** 2)) + 1e-9)
            if impulse is None or not impulse.size:
                impulse = synthetic_room(rng)
            audio = fftconvolve(audio, impulse)[: audio.size].astype(np.float32)
        speech = float(np.mean(audio**2)) + 1e-12
        if rng.random() < 0.75:
            noise = None
            if self.backgrounds:
                found = self._load(self.backgrounds[rng.integers(len(self.backgrounds))])
                if found is not None and found.size:
                    if found.size < audio.size:
                        found = np.tile(found, audio.size // found.size + 1)
                    start = int(rng.integers(0, found.size - audio.size + 1))
                    noise = found[start : start + audio.size]
            # Recordings at -5 to 10 dB, as microWakeWord's; synthetic noise, being
            # nothing like speech, from 5 to 25.
            low, high = (-5.0, 10.0) if noise is not None else (5.0, 25.0)
            if noise is None:
                noise = colored_noise(rng, audio.size)
            snr = rng.uniform(low, high)
            audio = audio + noise * np.sqrt(speech / ((float(np.mean(noise**2)) + 1e-12) * 10 ** (snr / 10)))
        peak = float(np.max(np.abs(audio))) + 1e-9
        level = 10 ** (rng.uniform(-30.0, -1.0) / 20)
        return (audio / peak * level).astype(np.float32)


def features(audio: np.ndarray) -> np.ndarray:
    """The micro frontend's features, unscaled (uint16), ``(frames, 40)``."""
    # pylint: disable=import-outside-toplevel
    from pymicro_features import MicroFrontend

    frontend = MicroFrontend()
    raw = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    rows, index, chunk = [], 0, FRAME_SAMPLES * 2
    while index + chunk <= len(raw):
        result = frontend.process_samples(raw[index : index + chunk])
        index += result.samples_read * 2
        if result.features:
            rows.append(result.features)
    if not rows:
        return np.zeros((0, FEATURES), dtype=np.uint16)
    return np.clip(np.rint(np.asarray(rows, dtype=np.float32) * SCALE), 0, 65535).astype(np.uint16)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--phrase", required=True)
    parser.add_argument("--voice", required=True, type=Path, help="A Piper voice (.onnx), many speakers best")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--clips", type=int, default=2000)
    parser.add_argument("--repeats", type=int, default=2, help="Augmented copies of each training clip")
    parser.add_argument("--slides", type=int, default=5)
    parser.add_argument("--frames", type=int, default=204, help="Feature frames the model looks at")
    parser.add_argument("--validation", type=float, default=0.1, help="Share of clips held out")
    parser.add_argument("--rirs", type=Path, help="Room impulse responses (.wav), else synthetic rooms")
    parser.add_argument("--background", type=Path, help="Background recordings (.wav), else colored noise")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)

    # pylint: disable=import-outside-toplevel
    import soxr
    from piper import PiperVoice, SynthesisConfig

    def report(**progress: object) -> None:
        print(json.dumps(progress), flush=True)

    voice = PiperVoice.load(args.voice)
    speakers = max(1, int(voice.config.num_speakers or 1))
    rng = np.random.default_rng(args.seed)
    pick = random.Random(args.seed)
    augment = Augmenter(rng, _wavs(args.rirs), _wavs(args.background))
    written = variants(args.phrase)
    held_out = max(1, round(args.clips * args.validation))
    samples = int(RATE * CLIP_SECONDS)
    if (samples - 480) // FRAME_SAMPLES + 1 < args.frames + args.slides - 1:
        parser.error(f"{CLIP_SECONDS} s clips are too short for {args.frames} frames and {args.slides} slides")

    args.out.mkdir(parents=True, exist_ok=True)
    training = np.lib.format.open_memmap(
        args.out / "training.npy.part", mode="w+", dtype=np.uint16,
        shape=((args.clips - held_out) * args.repeats * args.slides, args.frames, FEATURES),
    )
    validation = np.lib.format.open_memmap(
        args.out / "validation.npy.part", mode="w+", dtype=np.uint16, shape=(held_out, args.frames, FEATURES),
    )
    counts = {"training": 0, "validation": 0}
    made = attempts = 0
    report(stage="positives", done=0, total=args.clips, speakers=speakers)
    while made < args.clips:
        attempts += 1
        if attempts > args.clips * 3:
            report(error=f"Piper made {made} usable clips of {attempts} tries")
            return 1
        config = SynthesisConfig(
            speaker_id=pick.randrange(speakers) if speakers > 1 else None,
            length_scale=pick.uniform(0.8, 1.25),
            noise_scale=pick.uniform(0.4, 0.9),
            noise_w_scale=pick.uniform(0.5, 1.0),
        )
        chunks = [c.audio_float_array for c in voice.synthesize(pick.choice(written), syn_config=config)]
        if not chunks:
            continue
        audio = soxr.resample(np.concatenate(chunks).astype(np.float32), voice.config.sample_rate, RATE)
        seconds = audio.size / RATE
        if not MIN_SECONDS <= seconds <= MAX_SECONDS or float(np.sqrt(np.mean(audio**2))) < MIN_RMS:
            continue
        held = made < held_out
        for _ in range(1 if held else args.repeats):
            # The detector fires just after the phrase: a little silence after
            # it, then the clip at the end of CLIP_SECONDS.
            after = np.zeros(int(RATE * pick.uniform(0.05, 0.25)), dtype=np.float32)
            clip = np.concatenate([audio, after])[-samples:]
            clip = np.concatenate([np.zeros(samples - clip.size, dtype=np.float32), clip])
            spectrogram = features(augment(clip))
            if held:
                validation[counts["validation"]] = spectrogram[-args.frames :]
                counts["validation"] += 1
                continue
            for slide in range(args.slides):
                end = spectrogram.shape[0] - slide
                training[counts["training"]] = spectrogram[end - args.frames : end]
                counts["training"] += 1
        made += 1
        if made % 20 == 0 or made == args.clips:
            report(stage="positives", done=made, total=args.clips)
    training.flush()
    validation.flush()
    del training, validation
    (args.out / "training.npy.part").replace(args.out / "training.npy")
    (args.out / "validation.npy.part").replace(args.out / "validation.npy")
    report(stage="positives", done=made, total=args.clips, finished=True, **counts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
