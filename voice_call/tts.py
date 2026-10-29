"""Speech output: Piper for conversation responses, a stock sound otherwise."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable
from pathlib import Path

import numpy as np
import onnxruntime as ort
import soundfile as sf
import soxr
from piper import PiperConfig, PiperVoice
from sentence_stream import stream_to_sentences

from adapt_ctc.runtime import session_options

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


EMOJI = re.compile(
    "[ \t]*[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0E\uFE0F\u200D\u20E3\U000E0020-\U000E007F]+"
)
"""Emoji -- pictographs, symbols, flags, and what joins and colors them --
with the space before them."""


def sentences_of(text: str) -> list[str]:
    """What of ``text`` is said, in the pieces it is synthesized and played
    in, one after another: its sentences, without markdown (a conversation
    agent's **bold** and bullets, which Piper reads as "asterisk") or emoji
    (a shopping list's "🍎 Apples", which a voice may read as "red apple"),
    a list's items apart."""
    text = EMOJI.sub("", text)
    return [line.strip() for sentence in stream_to_sentences(text) for line in sentence.splitlines() if line.strip()]


def load_voice(path: Path) -> PiperVoice:
    """``PiperVoice.load``, with a session that gives back what it used
    (adapt_ctc.runtime): 87 MB after speaking rather than 146."""
    config = json.loads(Path(f"{path}.json").read_text(encoding="utf-8"))
    session = ort.InferenceSession(str(path), session_options(), providers=["CPUExecutionProvider"])
    return PiperVoice(session=session, config=PiperConfig.from_dict(config))


ALARM_PAUSE = 0.5
"""Silence between repeats of the timer alarm, as the stock VPE firmware has."""


INSTRUMENT = "electric_piano"
"""The instrument the satellite's sounds are played on unless another is chosen."""
SOUNDS = ("heard", "acknowledge", "not_understood", "asking")
"""An instrument's sounds (sounds/<instrument>/<sound>.flac): the command
ended; done, with nothing to say; not understood, or it went wrong; the
conversation agent is asked."""


class Speech:
    def __init__(
        self, voice_path: Path, sounds_dir: Path, alarm_path: Path | None = None,
        instrument: Callable[[], str | None] | None = None,
    ) -> None:
        self.voice_path = Path(voice_path)
        self.sounds_dir = Path(sounds_dir)
        self.alarm_path = Path(alarm_path) if alarm_path is not None else None
        self._instrument = instrument or (lambda: None)
        """The instrument chosen (None: INSTRUMENT), asked each time a sound plays."""
        self._voice: PiperVoice | None = None
        self.instruments: dict[str, dict[str, bytes]] = {}
        """Each instrument's SOUNDS, by its folder's name."""
        self.alarm = b""
        """One repeat of the timer alarm, pause included."""

    async def start(self) -> None:
        self._voice = await asyncio.to_thread(load_voice, self.voice_path)
        if self.alarm_path is not None:
            sound = await asyncio.to_thread(self._load_file, self.alarm_path)
            self.alarm = sound + bytes(int(ALARM_PAUSE * SAMPLE_RATE) * 2)
        for folder in sorted(p for p in self.sounds_dir.iterdir() if p.is_dir()):
            made = {name: await asyncio.to_thread(self._load_file, folder / f"{name}.flac")
                    for name in SOUNDS if (folder / f"{name}.flac").is_file()}
            if "acknowledge" in made:
                self.instruments[folder.name] = made
        _LOGGER.info("Speech ready (%s; sounds: %s)", self.voice_path.name, ", ".join(self.instruments))

    @staticmethod
    def _load_file(path: Path) -> bytes:
        samples, rate = sf.read(path, dtype="float32", always_2d=False)
        return _to_device_pcm(samples, rate)

    def sound(self, name: str) -> bytes:
        """One of SOUNDS, on the instrument chosen (else INSTRUMENT); none when
        it hasn't that one."""
        chosen = self.instruments.get(self._instrument() or INSTRUMENT) or self.instruments.get(INSTRUMENT) or {}
        return chosen.get(name, b"")

    @property
    def heard(self) -> bytes:
        return self.sound("heard")

    @property
    def acknowledge(self) -> bytes:
        """What a reply with nothing to say plays."""
        return self.sound("acknowledge")

    @property
    def not_understood(self) -> bytes:
        return self.sound("not_understood")

    @property
    def asking(self) -> bytes:
        return self.sound("asking")

    def synthesize(self, text: str) -> bytes:
        """``text`` (a sentence of ``sentences_of``) as device PCM."""
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
        """Speak ``text`` whole (its ``sentences_of``), or fall back to the
        acknowledgement sound."""
        pieces = sentences_of(text)
        if not pieces:
            _LOGGER.info("No conversation response; playing acknowledgement")
            return self.acknowledge
        pcm = b"".join([await asyncio.to_thread(self.synthesize, piece) for piece in pieces])
        _LOGGER.info("Synthesized %r (%.2fs)", " ".join(pieces), len(pcm) / (SAMPLE_RATE * 2))
        return pcm

