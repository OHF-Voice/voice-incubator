"""Short sounds the app makes itself, at the satellites' rate: the chime before
an announcement, and the intercom's tones as it connects and ends."""

from __future__ import annotations

import numpy as np

from .protocol import SAMPLE_RATE

LEVEL = 0.3
"""Of full scale: about as loud as speech."""


def bell(notes: list[float], note_s: float = 0.22, gap_s: float = 0.03, level: float = LEVEL) -> bytes:
    """Notes (Hz) one after another, each struck and dying away, as 16-bit PCM."""
    parts = []
    for frequency in notes:
        t = np.arange(int(note_s * SAMPLE_RATE)) / SAMPLE_RATE
        envelope = np.minimum(1.0, t / 0.004) * np.exp(-t * 7.0)
        wave = np.sin(2 * np.pi * frequency * t) + 0.25 * np.sin(4 * np.pi * frequency * t)
        parts += [wave * envelope / 1.25, np.zeros(int(gap_s * SAMPLE_RATE))]
    return (np.concatenate(parts) * level * 32767).astype("<i2").tobytes()


CHIME = bell([659.25, 523.25], note_s=0.4)
"""Before an announcement: ding, dong."""

CONNECTED = bell([523.25, 783.99])
"""The intercom connects: up."""

ENDED = bell([783.99, 523.25])
"""The intercom ends: down."""
