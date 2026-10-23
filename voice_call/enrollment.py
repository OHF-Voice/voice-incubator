"""Enrolling a speaker from a satellite: they read aloud while the device
streams what speakers are identified on (the second capture, AUDIO_MONITOR_AEC,
while monitored), and the app makes their profile from it.

One enrollment at a time, for the whole app. It collects until it is saved or
cancelled, or until MAX_SECONDS of audio; how much of it was speech (Silero) is
what the page shows against TARGET_SECONDS.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .protocol import BYTE_RATE

TARGET_SECONDS = 45.0
"""Speech to aim for: about a minute of reading."""
MIN_SECONDS = 10.0
"""Speech it takes to save a profile."""
MAX_SECONDS = 180.0
"""Audio collected at most (then it stops listening)."""
_CHUNK = 1024
"""Silero's 512 samples."""

PASSAGE = """\
Okay, here goes. Turn on the lights in the kitchen, and set them to fifty \
percent. What's the weather going to be like tomorrow morning? Remind me to \
water the plants on Thursday at six. Play some quiet jazz in the living room, \
then turn the volume down a little.

Every spring, the old orchard behind our house fills with blossoms, pale pink \
and white, and the bees arrive before anyone has finished breakfast. My \
grandmother used to say that a good harvest starts with patience: you can't \
hurry an apple, and you can't argue with frost. She kept a thick notebook of \
every tree, when it flowered, how much fruit it gave, and which jam turned out \
best. Her quince marmalade was famous, though nobody could ever quite \
reproduce it.

Set a timer for twelve minutes. Is the garage door closed? Add eggs, butter \
and brown sugar to the shopping list. Switch off everything downstairs when \
we go to bed."""
"""What the page asks them to read: commands, as they will be spoken to the
satellite, and plain sentences, for the rest of a voice."""


@dataclass
class Enrollment:
    satellite: str
    """The satellite's key."""
    satellite_name: str
    name: str
    """Whose voice: a new speaker, or one to add to."""
    started: float = field(default_factory=time.time)
    audio: bytearray = field(default_factory=bytearray)
    speech_s: float = 0.0
    level: float | None = None
    """The last ~0.1 s, in dBFS."""
    state: str = "listening"
    """``listening``, ``saving``, ``done``, ``failed`` or ``cancelled``."""
    error: str | None = None
    profile: dict[str, Any] | None = None
    """What was saved."""
    _vad: Any = None
    _leftover: bytes = b""

    def __post_init__(self) -> None:
        from pysilero_vad import SileroVoiceActivityDetector  # pylint: disable=import-outside-toplevel

        self._vad = SileroVoiceActivityDetector()

    @property
    def full(self) -> bool:
        return len(self.audio) >= MAX_SECONDS * BYTE_RATE

    @property
    def listening(self) -> bool:
        return self.state == "listening" and not self.full

    def add(self, pcm: bytes) -> None:
        if not self.listening:
            return
        self.audio += pcm
        samples = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2")
        if samples.size:
            tail = samples[-1600:].astype(np.float64) / 32768.0
            self.level = round(float(10 * np.log10(np.mean(tail**2) + 1e-12)), 1)
        self._leftover += pcm
        while len(self._leftover) >= _CHUNK:
            chunk, self._leftover = self._leftover[:_CHUNK], self._leftover[_CHUNK:]
            if self._vad.process_chunk(chunk) >= 0.5:
                self.speech_s += _CHUNK / BYTE_RATE

    def to_json(self) -> dict[str, Any]:
        return {
            "satellite": self.satellite,
            "satellite_name": self.satellite_name,
            "name": self.name,
            "started": self.started,
            "seconds": round(len(self.audio) / BYTE_RATE, 1),
            "speech_s": round(self.speech_s, 1),
            "target_s": TARGET_SECONDS,
            "min_s": MIN_SECONDS,
            "full": self.full,
            "level": self.level,
            "state": self.state,
            "error": self.error,
            "profile": self.profile,
        }
