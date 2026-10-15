"""WAV input. The recognizer runs on 16 kHz mono float32."""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

from .acoustic import SAMPLE_RATE


def load_wav(path: str | Path) -> np.ndarray:
    """Samples in ``[-1, 1]`` at 16 kHz, downmixed to mono."""
    with wave.open(str(path), "rb") as wav_file:
        width = wav_file.getsampwidth()
        channels = wav_file.getnchannels()
        rate = wav_file.getframerate()
        raw = wav_file.readframes(wav_file.getnframes())

    if width == 1:
        audio = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif width == 2:
        audio = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 3:
        bytes3 = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
        ints = (
            bytes3[:, 0].astype(np.int32)
            | (bytes3[:, 1].astype(np.int32) << 8)
            | (bytes3[:, 2].astype(np.int32) << 16)
        )
        ints = np.where(ints >= 1 << 23, ints - (1 << 24), ints)
        audio = ints.astype(np.float32) / float(1 << 23)
    elif width == 4:
        audio = np.frombuffer(raw, dtype="<i4").astype(np.float32) / float(1 << 31)
    else:
        raise ValueError(f"Unsupported sample width {width} in {path}")

    audio = audio.reshape(-1, channels).mean(axis=1)
    if rate != SAMPLE_RATE:
        audio = _resample(audio, rate)
    return np.ascontiguousarray(audio, dtype=np.float32)


def _resample(audio: np.ndarray, rate: int) -> np.ndarray:
    try:
        import soxr  # pylint: disable=import-outside-toplevel
    except ImportError as err:
        raise RuntimeError(
            f"Audio is {rate} Hz; resampling to {SAMPLE_RATE} Hz needs the "
            "'audio' extra (pip install 'adapt-ctc[audio]')"
        ) from err
    return soxr.resample(audio, rate, SAMPLE_RATE, quality="HQ")
