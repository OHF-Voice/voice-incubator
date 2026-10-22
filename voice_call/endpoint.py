"""End-of-command detection: a voice activity detector's probabilities, with
hysteresis -- Silero VAD (pysilero-vad) or microVAD (pymicro-vad).

Each chunk of audio is speech or silence. Speech begins at ``speech_threshold``
and lasts until the probability falls below ``silence_threshold``; between the
two, a chunk is what the one before it was. The command has begun after
``speech_seconds`` of speech -- until then, silence ends nothing -- and ends
after ``silence_seconds`` of silence. Speech in the silence starts it over only
after ``resume_seconds`` in a row: shorter sounds (a cough, a click) pause it.

Silero hears the command louder when the wake word was quiet (``vad_gain``):
it misses speech far below full scale, as a capture without AGC carries it
(the learn-tv firmware's, ~-53 dBFS), and leaves the command unstarted. Only
the VAD's copy is raised; what speech-to-text hears is as it came.

Silero also hears the wake word first (``warm``): started cold at the split,
it scored a command said straight after it ~0.4, where it had heard speech
all along ~0.8, and the command waited for a second try (or music) to begin.
What it carries over from the wake word lasts ~0.15 s, so its first
SETTLE_SECONDS after the split count for nothing, lest a pause after the wake
word end a command not yet said.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, fields, replace
from typing import Any
from collections.abc import Callable

import numpy as np

from .protocol import BYTE_RATE, BYTES_PER_SAMPLE

_LOGGER = logging.getLogger(__name__)

VADS = {"silero": "Silero VAD", "micro": "microVAD"}

LIMITS = {
    "speech_threshold": (0.05, 0.99),
    "silence_threshold": (0.01, 0.99),
    "speech_seconds": (0.0, 3.0),
    "silence_seconds": (0.3, 5.0),
    "resume_seconds": (0.0, 2.0),
    "max_seconds": (3.0, 60.0),
}
"""Each number's range, inclusive."""


@dataclass(frozen=True)
class EndOfSpeech:
    """How the end of a command is found: the Settings page's "End of speech"
    (settings.yaml ``end_of_speech``), over the app's options."""

    vad: str = "silero"
    speech_threshold: float = 0.5
    """A chunk this likely to be speech, or more, is speech."""
    silence_threshold: float = 0.35
    """Below this, silence; between the two, what the chunk before was.
    Silero's own default (its threshold less 0.15)."""
    speech_seconds: float = 0.2
    """Speech before the command has begun."""
    silence_seconds: float = 1.0
    """Silence after it that ends it."""
    resume_seconds: float = 0.1
    """Speech in a row that starts the silence over."""
    max_seconds: float = 15.0
    """The longest a command may be, from the wake word."""

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def parse(value: Any, base: EndOfSpeech) -> EndOfSpeech:
    """``value`` (what is stored: a mapping, any key may be left out) over
    ``base``. Raises ValueError naming what is wrong."""
    if value is None:
        return base
    if not isinstance(value, dict):
        raise ValueError("end_of_speech: expected a mapping")
    known = {f.name for f in fields(EndOfSpeech)}
    unknown = sorted(set(value) - known)
    if unknown:
        raise ValueError(f"end_of_speech: unknown {', '.join(unknown)} (use {', '.join(sorted(known))})")
    changes: dict[str, Any] = {}
    for key, item in value.items():
        if item is None:
            continue
        if key == "vad":
            if item not in VADS:
                raise ValueError(f"end_of_speech.vad: one of {', '.join(VADS)}")
            changes[key] = item
            continue
        low, high = LIMITS[key]
        try:
            number = float(item)
        except (TypeError, ValueError) as err:
            raise ValueError(f"end_of_speech.{key}: a number from {low:g} to {high:g}") from err
        if not low <= number <= high:
            raise ValueError(f"end_of_speech.{key}: a number from {low:g} to {high:g}")
        changes[key] = number
    settings = replace(base, **changes)
    if settings.silence_threshold > settings.speech_threshold:
        raise ValueError(
            f"end_of_speech: the silence threshold ({settings.silence_threshold:g}) is above "
            f"the speech threshold ({settings.speech_threshold:g})"
        )
    return settings


VAD_LEVEL_DBFS = -30.0
"""Where Silero hears the wake word's loudest moment (gain only ever raises it)."""
MAX_VAD_GAIN = 16.0
WAKE_SECONDS = 1.2
"""How much audio before the split holds the wake word."""
SETTLE_SECONDS = 0.2
"""After a warm start, Silero's chunks that count for nothing."""
_FRAME = 512


def vad_gain(wake: bytes) -> float:
    """The gain Silero hears the command at: the loudest 32 ms of ``wake``
    (the audio just before the split) brought to VAD_LEVEL_DBFS, at least 1
    and at most MAX_VAD_GAIN."""
    samples = np.frombuffer(wake[: len(wake) // (_FRAME * BYTES_PER_SAMPLE) * _FRAME * BYTES_PER_SAMPLE], dtype="<i2")
    if not samples.size:
        return 1.0
    frames = samples.astype(np.float64).reshape(-1, _FRAME) / 32768.0
    loudest = float(np.sqrt((frames**2).mean(axis=1)).max())
    if loudest <= 0.0:
        return MAX_VAD_GAIN
    return float(np.clip(10 ** (VAD_LEVEL_DBFS / 20) / loudest, 1.0, MAX_VAD_GAIN))


def _amplify(chunk: bytes, gain: float) -> bytes:
    samples = np.frombuffer(chunk, dtype="<i2").astype(np.float64) * gain
    return np.clip(samples, -32768, 32767).astype("<i2").tobytes()


def micro_available() -> bool:
    try:
        import pymicro_vad  # noqa: F401  # pylint: disable=import-outside-toplevel,unused-import
    except ImportError:
        return False
    return True


class Detector:
    """A VAD over fixed-size chunks: each one's probability of speech, or
    None while it has nothing to say yet."""

    chunk_bytes: int

    def __call__(self, chunk: bytes) -> float | None:
        raise NotImplementedError


class SileroDetector(Detector):
    """pysilero-vad: 512 samples (32 ms) a chunk."""

    chunk_bytes = 512 * BYTES_PER_SAMPLE

    def __init__(self) -> None:
        from pysilero_vad import SileroVoiceActivityDetector  # pylint: disable=import-outside-toplevel

        self._vad = SileroVoiceActivityDetector()

    def __call__(self, chunk: bytes) -> float | None:
        return self._vad.process_chunk(chunk)


class MicroDetector(Detector):
    """pymicro-vad (microWakeWord's VAD): 160 samples (10 ms) a chunk. It says
    nothing (-1) for its first 0.76 s, while its window fills; its
    probabilities are smoother than Silero's, and fall about half a second
    after the speech does."""

    chunk_bytes = 160 * BYTES_PER_SAMPLE

    def __init__(self) -> None:
        from pymicro_vad import MicroVad  # pylint: disable=import-outside-toplevel

        self._vad = MicroVad()

    def __call__(self, chunk: bytes) -> float | None:
        probability = self._vad.process_10ms(chunk)
        return None if probability < 0 else probability


def detector(vad: str) -> Detector:
    """``vad``'s detector; Silero when microVAD is not installed."""
    if vad == "micro":
        try:
            return MicroDetector()
        except ImportError:
            _LOGGER.warning("pymicro-vad is not installed; using Silero VAD")
    return SileroDetector()


class Endpointer:
    """Marks the end of the command after enough trailing silence."""

    def __init__(
        self,
        settings: EndOfSpeech | None = None,
        make_detector: Callable[[str], Detector] = detector,
        gain: float = 1.0,
        warm: bytes = b"",
    ) -> None:
        self.settings = settings or EndOfSpeech()
        self._detector = make_detector(self.settings.vad)
        self.vad = "micro" if isinstance(self._detector, MicroDetector) else self.settings.vad
        self.gain = gain if self.vad == "silero" else 1.0
        """What the VAD's copy of the audio is raised by (Silero's only)."""
        self._chunk_seconds = self._detector.chunk_bytes / BYTE_RATE
        self._leftover = b""
        self._run = 0.0
        """Speech in a row, since the last silence."""
        self.speaking = False
        self.speech_seconds = 0.0
        self.silence_seconds = 0.0
        self.speech_started = False
        self._settle = 0
        if warm and self.vad == "silero":
            self._warm(warm)

    def _warm(self, pcm: bytes) -> None:
        """Silero's state from ``pcm`` (the audio before the split); what it says
        of it, and of the first SETTLE_SECONDS after, counts for nothing."""
        size = self._detector.chunk_bytes
        pcm = pcm[len(pcm) % size :]
        for start in range(0, len(pcm), size):
            chunk = pcm[start : start + size]
            self._detector(_amplify(chunk, self.gain) if self.gain != 1.0 else chunk)
        self._settle = round(SETTLE_SECONDS / self._chunk_seconds)

    def process(self, pcm: bytes) -> bool:
        """Feed command audio; True once the command has ended."""
        self._leftover += pcm
        size, seconds, settings = self._detector.chunk_bytes, self._chunk_seconds, self.settings
        while len(self._leftover) >= size:
            chunk = self._leftover[:size]
            self._leftover = self._leftover[size:]
            if self.gain != 1.0:
                chunk = _amplify(chunk, self.gain)
            probability = self._detector(chunk)
            if probability is None:
                continue
            if self._settle:
                self._settle -= 1
                continue
            self.speaking = probability >= (
                settings.silence_threshold if self.speaking else settings.speech_threshold
            )
            if self.speaking:
                self.speech_seconds += seconds
                self._run += seconds
                if not self.speech_started:
                    if self.speech_seconds >= settings.speech_seconds - 1e-9:
                        self.speech_started = True
                        _LOGGER.debug("Speech started")
                elif self._run >= settings.resume_seconds - 1e-9:
                    self.silence_seconds = 0.0
            else:
                self._run = 0.0
                if self.speech_started:
                    self.silence_seconds += seconds
                    if self.silence_seconds >= settings.silence_seconds - 1e-9:
                        _LOGGER.info(
                            "Command ended (%s: %.2fs speech, %.2fs silence)",
                            VADS[self.vad],
                            self.speech_seconds,
                            self.silence_seconds,
                        )
                        return True
        return False
