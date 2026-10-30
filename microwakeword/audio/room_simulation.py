# coding=utf-8
"""Spatially-consistent room simulation: reverberate the wake word and the background noise from
different positions in the same room, then mix them at a measured SNR.

This replaces the stock `AddBackgroundNoise` + `ApplyImpulseResponse` pair, which cannot express
this. In the stock `Compose`, background noise is added BEFORE the reverb, so the noise is
convolved with the talker's response -- the TV ends up at the speaker's mouth. `ApplyImpulseResponse`
also peak-normalises its output to 0.5, which discards the level relationship the SNR was set
against, and it convolves from sample 0 of the response, so any propagation delay in the impulse
response slides the audio later in the window (and, because the result is truncated back to the
input length while microWakeWord right-aligns positives, off the end of the wake word).

What happens here, per training sample:

  1. Draw one multi-channel RIR from the bank (see local/generate_pra_rirs.py). Channel 0 is the
     talker; the rest are interferer positions in the SAME room. The bank is pre-aligned so
     channel 0's direct path is at sample ~0, so convolution preserves the wake word's timing.
  2. Convolve the speech with channel 0, `full` truncated to the window length: the word keeps its
     position, and reverb running past the window edge is discarded, as it would be on device.
  3. Draw 1..max_noise_sources background clips, each `length + len(rir) - 1` samples long, and
     convolve each with a DIFFERENT channel using `valid` mode. That yields exactly `length`
     samples of steady-state reverberated noise with no fade-in at the window start.
  4. Remove the residual DC from both wet signals before mixing.
  5. Measure the ACTIVE speech level (frames within 40 dB of the loudest frame) and the noise level
     AFTER convolution, and scale the noise to the drawn SNR. Measuring the speech over the whole
     window instead would count the silent padding around a right-aligned wake word as signal and
     make every mixture noisier than requested.
"""

import os
import warnings
from functools import lru_cache
from typing import List, Sequence

import numpy as np
import soundfile as sf
from scipy.signal import fftconvolve

AUDIO_EXTENSIONS = (".wav", ".flac", ".ogg", ".mp3", ".opus")
TARGET_SAMPLE_RATE = 16000

# Frames this far below the loudest frame are treated as pauses, not signal.
ACTIVE_FRAME_FLOOR_DB = 40.0
ACTIVE_FRAME_SAMPLES = 320  # 20 ms


def _list_audio(paths: Sequence[str]) -> List[str]:
    found = []
    for path in paths:
        if os.path.isfile(path):
            found.append(path)
            continue
        for root, _dirs, files in os.walk(path):
            found.extend(
                os.path.join(root, f)
                for f in files
                if f.lower().endswith(AUDIO_EXTENSIONS)
            )
    return sorted(found)


@lru_cache(maxsize=256)
def _load_rir(path: str) -> np.ndarray:
    """Load one multi-channel RIR as (samples, channels), float32."""
    rir, sample_rate = sf.read(path, dtype="float32", always_2d=True)
    if sample_rate != TARGET_SAMPLE_RATE:
        raise ValueError(
            f"{path}: RIR bank must be {TARGET_SAMPLE_RATE} Hz, found {sample_rate} Hz"
        )
    if rir.shape[1] < 2:
        raise ValueError(
            f"{path}: expected a multi-channel RIR (channel 0 talker, 1+ interferers), "
            f"found {rir.shape[1]} channel"
        )
    return rir


def _read_noise(path: str, num_samples: int, rng: np.random.Generator) -> np.ndarray:
    """Read `num_samples` of mono 16 kHz audio from a random offset, tiling if the file is short."""
    info = sf.info(path)
    if info.samplerate != TARGET_SAMPLE_RATE:
        import librosa

        audio, _ = librosa.load(path, sr=TARGET_SAMPLE_RATE, mono=True)
    else:
        if info.frames > num_samples:
            start = int(rng.integers(0, info.frames - num_samples))
            audio, _ = sf.read(
                path, start=start, frames=num_samples, dtype="float32", always_2d=True
            )
        else:
            audio, _ = sf.read(path, dtype="float32", always_2d=True)
        audio = audio.mean(axis=1)

    if len(audio) < num_samples:
        if len(audio) == 0:
            return np.zeros(num_samples, dtype=np.float32)
        audio = np.tile(audio, int(np.ceil(num_samples / len(audio))))
    return np.ascontiguousarray(audio[:num_samples], dtype=np.float32)


def active_rms(audio: np.ndarray) -> float:
    """RMS over the frames that carry the speech, ignoring leading/trailing padding and pauses."""
    usable = len(audio) - (len(audio) % ACTIVE_FRAME_SAMPLES)
    if usable < ACTIVE_FRAME_SAMPLES:
        return float(np.sqrt(np.mean(np.square(audio)) + 1e-20))
    frames = audio[:usable].reshape(-1, ACTIVE_FRAME_SAMPLES)
    power = np.mean(np.square(frames), axis=1)
    peak = power.max()
    if peak <= 0:
        return 0.0
    active = power[power >= peak * 10 ** (-ACTIVE_FRAME_FLOOR_DB / 10)]
    return float(np.sqrt(active.mean()))


class RoomSimulation:
    """Reverberate speech and noise from different positions in one room, then mix at a set SNR.

    Args:
        rir_paths: Directories of multi-channel RIRs from local/generate_pra_rirs.py.
        background_paths: Directories of background audio, as in the stock pipeline.
        min_snr_db / max_snr_db: SNR range, measured after convolution.
        rir_probability: Probability the room is applied at all. On the remaining draws the
            speech and noise are mixed dry, which keeps a close-talking condition in the data.
            Noise is mixed either way, so this is not the same knob as the stock `RIR` probability.
        max_noise_sources: Up to this many interferers, each from its own file and its own
            position. Their sum is scaled to the drawn SNR.
        seed: Optional seed. Leave unset in training so workers do not share a stream.
    """

    def __init__(
        self,
        rir_paths: Sequence[str],
        background_paths: Sequence[str] = (),
        min_snr_db: float = -5.0,
        max_snr_db: float = 15.0,
        rir_probability: float = 0.8,
        max_noise_sources: int = 2,
        seed: int | None = None,
    ):
        self.rir_files = _list_audio(rir_paths)
        self.background_files = _list_audio(background_paths)
        if not self.rir_files:
            raise ValueError(f"no RIRs found in {list(rir_paths)}")
        if not self.background_files:
            warnings.warn(
                "RoomSimulation has no background files; only reverberation will be applied"
            )
        self.min_snr_db = min_snr_db
        self.max_snr_db = max_snr_db
        self.rir_probability = rir_probability
        self.max_noise_sources = max_noise_sources
        self.rng = np.random.default_rng(seed)

    def __call__(self, samples: np.ndarray, sample_rate: int = TARGET_SAMPLE_RATE):
        """audiomentations.Lambda-compatible transform."""
        if sample_rate != TARGET_SAMPLE_RATE:
            raise ValueError(f"expected {TARGET_SAMPLE_RATE} Hz, got {sample_rate}")

        samples = np.asarray(samples, dtype=np.float32)
        length = len(samples)
        apply_rir = self.rng.random() < self.rir_probability

        if apply_rir:
            rir = _load_rir(self.rir_files[int(self.rng.integers(len(self.rir_files)))])
            # `full` from sample 0, truncated to the window: the bank's channel 0 is aligned on its
            # direct path, so the wake word does not move. The tail past the window is dropped.
            wet = fftconvolve(samples, rir[:, 0])[:length]
            interferer_channels = list(range(1, rir.shape[1]))
        else:
            rir = None
            wet = samples.copy()
            interferer_channels = []

        wet -= wet.mean()  # pyroomacoustics responses leave a DC offset; do not mix it in

        if not self.background_files:
            return self._guard(wet.astype(np.float32))

        num_sources = 1
        if self.max_noise_sources > 1:
            num_sources = int(
                self.rng.integers(1, min(self.max_noise_sources, max(len(interferer_channels), 1)) + 1)
            )
        self.rng.shuffle(interferer_channels)

        noise_total = np.zeros(length, dtype=np.float64)
        for i in range(num_sources):
            path = self.background_files[
                int(self.rng.integers(len(self.background_files)))
            ]
            if rir is not None:
                channel = rir[:, interferer_channels[i % len(interferer_channels)]]
                # `valid` over length + len(rir) - 1 input samples returns exactly `length`
                # samples, every one of them fully reverberated -- no fade-in at the window start.
                noise = _read_noise(path, length + len(channel) - 1, self.rng)
                wet_noise = fftconvolve(noise, channel, mode="valid")
            else:
                wet_noise = _read_noise(path, length, self.rng).astype(np.float64)
            noise_total += wet_noise - wet_noise.mean()

        speech_level = active_rms(wet)
        noise_level = float(np.sqrt(np.mean(np.square(noise_total)) + 1e-20))
        if speech_level <= 0 or noise_level <= 0:
            return self._guard(wet.astype(np.float32))

        snr_db = self.rng.uniform(self.min_snr_db, self.max_snr_db)
        scale = speech_level / (noise_level * 10 ** (snr_db / 20.0))
        return self._guard((wet + scale * noise_total).astype(np.float32))

    @staticmethod
    def _guard(audio: np.ndarray, ceiling: float = 0.98) -> np.ndarray:
        """Scale the whole mixture down if it would clip. Scaling the mixture, rather than
        clipping it, leaves the SNR untouched."""
        peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
        if peak > ceiling:
            audio = audio * (ceiling / peak)
        return audio.astype(np.float32)
