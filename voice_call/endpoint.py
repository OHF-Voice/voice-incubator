"""End-of-command detection: a voice activity detector's probabilities, with
hysteresis -- Silero VAD (pysilero-vad), microVAD (pymicro-vad), or both
(speech when either hears it).

Each chunk of audio is speech or silence. Speech begins at ``speech_threshold``
and lasts until the probability falls below ``silence_threshold``; between the
two, a chunk is what the one before it was. The command has begun after
``speech_seconds`` of speech -- until then, silence ends nothing -- and ends
after ``silence_seconds`` of silence. Speech in the silence starts it over only
after ``resume_seconds`` in a row: shorter sounds (a cough, a click) pause it.
With no speech at all ``no_speech_seconds`` after the wake word, nothing was
said, and the call ends (``no_speech``).

Silero misses some voices: a child's commands, loud and clear to the
speech-to-text, were 0.3 s of speech to it and left the command unstarted
for 15 s, where microVAD heard a third of them as speech. microVAD hears more
of what isn't speech, too. Both together (``vad`` "both") is the louder of
the two, chunk by chunk.

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

Someone else's speech is silence for the command, once it is known to be
someone else's (``watch``): the last WINDOW of the command is checked against
the identified speaker's voice every OTHER_EVERY (``due``, ``verdict``: the
caller embeds it), when it is mostly speech (OTHER_SPEECH); OTHER_RUN checks
in a row under OTHER_SCORE mean someone else is talking. The speaker's last
word is where the scores fell halfway from the speaker's to the other
voice's, half a window back -- or the pause next to it, when there is one --
and the command ends as soon as they have been quiet for ``silence_seconds``
since: most often there and then, a second or less into the other voice.
Windows, not stretches between pauses: someone who starts with no pause at
all after the command would otherwise go unchecked until they paused (three
recorded sessions were cut 0.9, 2.9 and 3.3 s later than this would have).
Simulated on Piper's voices (six enrolled speakers, each against a typical
voice and the closest of 60, after no pause, 0.1 s or 0.3 s), a typical voice
was cut 0.9-1.1 s in every time, the closest a quarter of the time (and the
call then ends as before), and neither the command nor the speaker's own
going on was ever cut.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, fields, replace
from typing import Any
from collections.abc import Callable

import numpy as np

from .protocol import BYTE_RATE, BYTES_PER_SAMPLE

_LOGGER = logging.getLogger(__name__)

VADS = {"silero": "Silero VAD", "micro": "microVAD", "both": "Both"}

LIMITS = {
    "speech_threshold": (0.05, 0.99),
    "silence_threshold": (0.01, 0.99),
    "speech_seconds": (0.0, 3.0),
    "silence_seconds": (0.3, 5.0),
    "resume_seconds": (0.0, 2.0),
    "max_seconds": (3.0, 60.0),
    "no_speech_seconds": (2.0, 60.0),
}
"""Each number's range, inclusive."""


@dataclass(frozen=True)
class EndOfSpeech:
    """How the end of a command is found: the Settings page's "End of speech"
    (settings.json ``end_of_speech``), over the app's options."""

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
    no_speech_seconds: float = 8.0
    """With no speech by then (from the wake word), nothing was said: the call
    ends."""
    end_on_other_voice: bool = True
    """Someone other than the identified speaker talking ends their command
    (``watch``): with enrolled voices to identify them by."""

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
        if key == "end_on_other_voice":
            if not isinstance(item, bool):
                raise ValueError(f"end_of_speech.{key}: true or false")
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
SEGMENT_GAP = 0.15
"""A pause this long ends a segment of speech."""
WINDOW = 1.0
"""What is checked against the speaker: the command's last second."""
OTHER_EVERY = 0.2
OTHER_SPEECH = 0.7
"""A window less speech than this is not checked (and breaks a run)."""
OTHER_SCORE = 0.20
OTHER_RUN = 2
"""Checks in a row scoring under OTHER_SCORE (cosine to the speaker's
profile) that mean someone else is talking. Higher, or fewer, cut the
speaker's own going on in simulation; the speaker's own short stretches score
as low as 0.2 with music playing."""
SNAP = (0.5, 0.3)
"""A pause this long before, or after, where the scores put the speaker's last
word is taken as it (the speech before it: their own last word)."""


@dataclass
class Segment:
    """A stretch of speech, in seconds from the split."""

    start: float
    end: float
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


def wake_speech(wake: bytes) -> float:
    """How much of ``wake`` (the wake word, as the endpointer warms on it)
    Silero hears as speech, at its gain: about 0.3-0.8 in either capture of
    an ordinary call; none in a capture whose null points at the talker."""
    from pysilero_vad import SileroVoiceActivityDetector  # pylint: disable=import-outside-toplevel

    vad = SileroVoiceActivityDetector()
    pcm = _amplify(wake[: len(wake) // 2 * 2], vad_gain(wake))
    chunk = _FRAME * BYTES_PER_SAMPLE
    probs = [vad.process_chunk(pcm[i : i + chunk]) for i in range(0, len(pcm) - chunk + 1, chunk)]
    return float(np.mean(np.asarray(probs) >= 0.5)) if probs else 0.0


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


class BothDetector(Detector):
    """Silero and microVAD on the same audio, in Silero's chunks: the likelier
    of the two (microVAD's likeliest 10 ms in the chunk). Silero hears it
    raised by ``gain``, microVAD as it came; until microVAD's window has
    filled, Silero alone."""

    chunk_bytes = SileroDetector.chunk_bytes

    def __init__(self, gain: float = 1.0) -> None:
        self.micro = MicroDetector()
        self.silero = SileroDetector()
        self.gain = gain
        self._leftover = b""

    def __call__(self, chunk: bytes) -> float | None:
        silero = self.silero(_amplify(chunk, self.gain) if self.gain != 1.0 else chunk)
        self._leftover += chunk
        size, micro = self.micro.chunk_bytes, []
        while len(self._leftover) >= size:
            probability = self.micro(self._leftover[:size])
            self._leftover = self._leftover[size:]
            if probability is not None:
                micro.append(probability)
        return max([silero, *micro]) if silero is not None else (max(micro) if micro else None)


def detector(vad: str) -> Detector:
    """``vad``'s detector; Silero when microVAD is not installed."""
    if vad in ("micro", "both"):
        try:
            return MicroDetector() if vad == "micro" else BothDetector()
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
        if isinstance(self._detector, (MicroDetector, BothDetector)):
            self.vad = "micro" if isinstance(self._detector, MicroDetector) else "both"
        elif isinstance(self._detector, SileroDetector):
            self.vad = "silero"  # what was asked for, or what it fell back to
        else:
            self.vad = self.settings.vad
        self.gain = gain if self.vad in ("silero", "both") else 1.0
        """What the VAD's copy of the audio is raised by (Silero's only)."""
        if isinstance(self._detector, BothDetector):
            self._detector.gain, self._raise = self.gain, False  # it raises Silero's copy itself
        else:
            self._raise = self.gain != 1.0
        self._chunk_seconds = self._detector.chunk_bytes / BYTE_RATE
        self._leftover = b""
        self._run = 0.0
        """Speech in a row, since the last silence."""
        self.speaking = False
        self.speech_seconds = 0.0
        self.silence_seconds = 0.0
        self.speech_started = False
        self._settle = 0
        self.elapsed = 0.0
        """Seconds of command audio judged."""
        self.segments: list[Segment] = []
        self._quiet = 0.0
        self.watching = False
        """Whether the command is checked against the speaker (``due``)."""
        self._checked = 0.0
        self._checks: list[tuple[float, float]] = []
        """Each check: (window end, score)."""
        self._low: list[tuple[float, float]] = []
        """The run of low checks."""
        self.other_from: float | None = None
        """Speech after this is someone else's (seconds from the split)."""
        self.cut: dict[str, Any] | None = None
        """When someone else's speech ended the command: the speaker's last
        word, when it was decided, and the low checks' scores."""
        self.ended = False
        self.no_speech = False
        """It ended with no speech, ``no_speech_seconds`` after the wake word."""
        if warm and self.vad in ("silero", "both"):
            self._warm(warm)

    def _warm(self, pcm: bytes) -> None:
        """Silero's state from ``pcm`` (the audio before the split); what it says
        of it, and of the first SETTLE_SECONDS after, counts for nothing."""
        size = self._detector.chunk_bytes
        pcm = pcm[len(pcm) % size :]
        for start in range(0, len(pcm), size):
            chunk = pcm[start : start + size]
            self._detector(_amplify(chunk, self.gain) if self._raise else chunk)
        self._settle = round(SETTLE_SECONDS / self._chunk_seconds)

    def watch(self, on: bool = True) -> None:
        """Check segments after the command against the speaker (or stop:
        nobody was identified)."""
        self.watching = on

    def _speech_in(self, start: float, end: float) -> float:
        return sum(max(0.0, min(s.end, end) - max(s.start, start)) for s in self.segments)

    def due(self) -> tuple[float, float] | None:
        """The window to check now, ``(start, end)``: the last WINDOW, every
        OTHER_EVERY, when it is mostly speech."""
        if not self.watching or self.ended or self.other_from is not None:
            return None
        end = self.elapsed
        if end < WINDOW - 1e-9 or end - self._checked < OTHER_EVERY - 1e-9:
            return None
        self._checked = end
        if self._speech_in(end - WINDOW, end) < OTHER_SPEECH * WINDOW - 1e-9:
            self._low.clear()  # a pause in between: a run starts over
            return None
        return end - WINDOW, end

    def verdict(self, score: float, end: float) -> bool:
        """The check of the window ending at ``end``; True when it ends the
        command (someone else talking, and the speaker quiet long enough since
        their last word)."""
        if self.ended or self.other_from is not None:
            return False
        self._checks.append((end, round(score, 3)))
        if score >= OTHER_SCORE:
            self._low.clear()
            return False
        self._low.append((end, round(score, 3)))
        if len(self._low) < OTHER_RUN:
            return False
        last = self._last_word()
        self.other_from = last
        self.silence_seconds = max(0.0, self.elapsed - last)
        self._run = 0.0
        self.cut = {"last_word": round(last, 2), "at": round(self.elapsed, 2), "scores": [s for _, s in self._low]}
        _LOGGER.info("Someone else talking since about %.2fs (decided at %.2fs)", last, self.elapsed)
        if self.speech_started and self.silence_seconds >= self.settings.silence_seconds - 1e-9:
            return self._end()
        return False

    def _last_word(self) -> float:
        """Where the speaker stopped: the scores' fall halfway from theirs (the
        command's first checks: by the run, the ones before it may be the
        other voice's already) to the other voice's (the run), half a window
        back; or a pause near it."""
        first = self._checks.index(self._low[0])
        theirs = [s for _, s in self._checks[: min(3, first)]]
        high = float(np.median(theirs)) if theirs else 0.5
        low = float(np.mean([s for _, s in self._low]))
        half = (high + low) / 2
        crossed = self._low[0][0]
        for i in range(first, 0, -1):  # the latest fall, going back from the run
            (t0, s0), (t1, s1) = self._checks[i - 1], self._checks[i]
            if s0 >= half > s1:
                crossed = t0 + (t1 - t0) * (s0 - half) / (s0 - s1)
                break
        last = crossed - WINDOW / 2
        # A segment ending close by, and not the one going on: its end.
        ends = [s.end for s in self.segments[:-1] if last - SNAP[0] <= s.end <= last + SNAP[1]]
        return max(0.0, ends[-1] if ends else last)

    def _end(self) -> bool:
        self.ended = True
        _LOGGER.info(
            "Command ended (%s: %.2fs speech, %.2fs silence)",
            VADS[self.vad],
            self.speech_seconds,
            self.silence_seconds,
        )
        return True

    def process(self, pcm: bytes) -> bool:
        """Feed command audio; True once the command has ended."""
        self._leftover += pcm
        size, seconds, settings = self._detector.chunk_bytes, self._chunk_seconds, self.settings
        while len(self._leftover) >= size:
            chunk = self._leftover[:size]
            self._leftover = self._leftover[size:]
            if self._raise:
                chunk = _amplify(chunk, self.gain)
            probability = self._detector(chunk)
            self.elapsed += seconds
            if not self.speech_started and self.elapsed >= settings.no_speech_seconds - 1e-9:
                self.no_speech = True
                self.ended = True
                _LOGGER.info("No speech %.1fs after the wake word (%s)", self.elapsed, VADS[self.vad])
                return True
            if probability is None:
                continue
            if self._settle:
                self._settle -= 1
                continue
            self.speaking = probability >= (
                settings.silence_threshold if self.speaking else settings.speech_threshold
            )
            # The speaker's speech: someone else's is silence for the command.
            speaking = self.speaking
            if self.speaking:
                if not self.segments or self._quiet >= SEGMENT_GAP - 1e-9:
                    self.segments.append(Segment(self.elapsed - seconds, self.elapsed))
                self.segments[-1].end = self.elapsed
                self._quiet = 0.0
                speaking = self.other_from is None
            else:
                self._quiet += seconds
            if speaking:
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
                        return self._end()
        return False
