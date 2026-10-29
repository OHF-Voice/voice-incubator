"""Speech output: Piper for conversation responses, a stock sound otherwise."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import onnxruntime as ort
import soundfile as sf
import soxr
from piper import PiperConfig, PiperVoice, SynthesisConfig
from sentence_stream import stream_to_sentences

from adapt_ctc.runtime import session_options

from .piper_voices import synthesis
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
OTHER_VOICES = 2
"""Voices kept loaded besides the one replies are said in: areas' own
(voice_labs/areas.py), the least lately used let go first."""
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
        synthesis: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self.voice_path = Path(voice_path)
        """Piper's voice (its .onnx, the .onnx.json beside it)."""
        self._synthesis = synthesis or dict
        """How it speaks (piper_voices.synthesis's settings), asked each time it does."""
        self.sounds_dir = Path(sounds_dir)
        self.alarm_path = Path(alarm_path) if alarm_path is not None else None
        self._instrument = instrument or (lambda: None)
        """The instrument chosen (None: INSTRUMENT), asked each time a sound plays."""
        self._voice: PiperVoice | None = None
        self._others: OrderedDict[Path, PiperVoice] = OrderedDict()
        self.instruments: dict[str, dict[str, bytes]] = {}
        """Each instrument's SOUNDS, by its folder's name."""
        self.alarm = b""
        """One repeat of the timer alarm, pause included."""

    async def start(self) -> None:
        await self.use(self.voice_path)
        if self.alarm_path is not None:
            sound = await asyncio.to_thread(self._load_file, self.alarm_path)
            self.alarm = sound + bytes(int(ALARM_PAUSE * SAMPLE_RATE) * 2)
        for folder in sorted(p for p in self.sounds_dir.iterdir() if p.is_dir()):
            made = {name: await asyncio.to_thread(self._load_file, folder / f"{name}.flac")
                    for name in SOUNDS if (folder / f"{name}.flac").is_file()}
            if "acknowledge" in made:
                self.instruments[folder.name] = made
        _LOGGER.info("Speech ready (%s; sounds: %s)", self.voice_path.name, ", ".join(self.instruments))

    async def use(self, voice_path: Path) -> None:
        """Speak with this voice from now on."""
        self._voice = await asyncio.to_thread(load_voice, Path(voice_path))
        self.voice_path = Path(voice_path)
        self._others.pop(self.voice_path, None)

    async def other(self, voice_path: Path) -> PiperVoice:
        """Another voice to speak with now and then (an area's own), loaded
        once and kept while it is among the last OTHER_VOICES used."""
        path = Path(voice_path)
        if path == self.voice_path and self._voice is not None:
            return self._voice
        if path in self._others:
            self._others.move_to_end(path)
            return self._others[path]
        voice = await asyncio.to_thread(load_voice, path)
        self._others[path] = voice
        while len(self._others) > OTHER_VOICES:
            self._others.popitem(last=False)
        return voice

    @property
    def defaults(self) -> dict[str, Any]:
        """The voice's own inference settings, and its speakers by id."""
        assert self._voice is not None
        config = self._voice.config
        speakers = sorted(config.speaker_id_map or {}, key=lambda name: config.speaker_id_map[name])
        return {"length_scale": config.length_scale, "noise_scale": config.noise_scale,
                "noise_w_scale": config.noise_w_scale, "volume": 1.0, "speakers": speakers}

    @staticmethod
    def _load_file(path: Path) -> bytes:
        samples, rate = sf.read(path, dtype="float32", always_2d=False)
        return _to_device_pcm(samples, rate)

    def sound(self, name: str, instrument: str | None = None) -> bytes:
        """One of SOUNDS, on ``instrument`` (else the one chosen, else
        INSTRUMENT); none when it hasn't that one."""
        wanted = instrument or self._instrument() or INSTRUMENT
        chosen = self.instruments.get(wanted) or self.instruments.get(INSTRUMENT) or {}
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

    def synthesize(self, text: str, settings: dict[str, Any] | None = None, voice: PiperVoice | None = None) -> bytes:
        """``text`` (a sentence of ``sentences_of``) as device PCM, as the
        settings say (``settings``, else the Settings page's), in ``voice``
        (``other``'s; else the one replies are said in)."""
        assert self._voice is not None
        voice = voice or self._voice
        chosen = self._synthesis() if settings is None else settings
        config = SynthesisConfig(**synthesis(chosen, voice.config.speaker_id_map or {}))
        chunks = list(voice.synthesize(text, syn_config=config))
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

