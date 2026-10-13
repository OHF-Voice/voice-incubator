"""Speech output: Piper for conversation responses, a stock sound otherwise."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import numpy as np
import soundfile as sf
import soxr
from piper import PiperVoice

from .protocol import SAMPLE_RATE

_LOGGER = logging.getLogger(__name__)


def _to_device_pcm(samples: np.ndarray, rate: int) -> bytes:
    """Mono 16-bit PCM at the device's sample rate."""
    if samples.ndim > 1:
        samples = samples.mean(axis=1)
    samples = samples.astype(np.float32, copy=False)
    if rate != SAMPLE_RATE:
        samples = soxr.resample(samples, rate, SAMPLE_RATE)
    clipped = np.clip(samples, -1.0, 1.0)
    return (clipped * 32767.0).astype("<i2").tobytes()


class Speech:
    def __init__(self, voice_path: Path, acknowledge_path: Path) -> None:
        self.voice_path = Path(voice_path)
        self.acknowledge_path = Path(acknowledge_path)
        self._voice: PiperVoice | None = None
        self._acknowledge: bytes | None = None

    async def start(self) -> None:
        self._voice = await asyncio.to_thread(PiperVoice.load, self.voice_path)
        self._acknowledge = await asyncio.to_thread(
            self._load_file, self.acknowledge_path
        )
        _LOGGER.info("Speech ready (%s)", self.voice_path.name)

    @staticmethod
    def _load_file(path: Path) -> bytes:
        samples, rate = sf.read(path, dtype="float32", always_2d=False)
        return _to_device_pcm(samples, rate)

    def _synthesize(self, text: str) -> bytes:
        assert self._voice is not None
        chunks = list(self._voice.synthesize(text))
        if not chunks:
            return b""
        rate = chunks[0].sample_rate
        audio = np.concatenate(
            [np.frombuffer(c.audio_int16_bytes, dtype="<i2") for c in chunks]
        ).astype(np.float32) / 32768.0
        return _to_device_pcm(audio, rate)

    async def say(self, text: str) -> bytes:
        """Speak ``text``, or fall back to the acknowledgement sound."""
        if not text.strip():
            assert self._acknowledge is not None
            _LOGGER.info("No conversation response; playing acknowledgement")
            return self._acknowledge
        pcm = await asyncio.to_thread(self._synthesize, text)
        _LOGGER.info("Synthesized %r (%.2fs)", text, len(pcm) / (SAMPLE_RATE * 2))
        return pcm
