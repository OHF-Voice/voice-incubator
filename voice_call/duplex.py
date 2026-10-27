"""The intercom's relay: what each satellite's microphone sends the other.

Each satellite plays the other through a speaker a few inches from its own
microphones. The XMOS cancels its own echo, but not all of it -- least up
high -- and its gain stage raises what is left in a quiet room, so the two make
a loop, and a loop with gain squeals. Two things keep it below one:

* the band a voice needs and no more: above LOWPASS_HZ is cut, where the
  squeal was;
* half duplex, as a speakerphone does it: the side that spoke first while the
  other was quiet has the floor, and the other is turned down (DUCK) -- its
  echo of the first goes back quietly -- until the first goes quiet, or the
  other speaks up over it (BARGE_IN). Loudness is followed at once up and
  slowly down (RELEASE): past the moment the first side's voice comes back
  from the other.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .protocol import SAMPLE_RATE

LOWPASS_HZ = 4000.0
TAPS = 63
DUCK = 0.1
"""The gain of the side without the floor: -20 dB."""
ACTIVE = 400.0
"""A side speaking, by its loudness (RMS of 16-bit samples): about -38 dBFS."""
BARGE_IN = 2.0
"""How much louder (6 dB) the other side must be to take the floor."""
RELEASE = 0.6
"""Seconds for loudness to fall by e."""


def _kernel() -> np.ndarray:
    n = np.arange(TAPS) - (TAPS - 1) / 2
    cutoff = LOWPASS_HZ / SAMPLE_RATE
    kernel = 2 * cutoff * np.sinc(2 * cutoff * n) * np.hamming(TAPS)
    return (kernel / kernel.sum()).astype(np.float32)


KERNEL = _kernel()


@dataclass
class _Side:
    tail: np.ndarray = field(default_factory=lambda: np.zeros(TAPS - 1, dtype=np.float32))
    """The last samples, for the filter to carry on from."""
    level: float = 0.0
    gain: float = 1.0


@dataclass
class Duplex:
    sides: dict[str, _Side] = field(default_factory=dict)
    floor: str | None = None
    """Who is speaking, the other turned down; None: nobody."""

    def relay(self, side: str, other: str, pcm: bytes) -> bytes:
        """``side``'s microphone (16-bit PCM), as ``other`` should play it."""
        own, theirs = self.sides.setdefault(side, _Side()), self.sides.setdefault(other, _Side())
        x = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
        if not len(x):
            return pcm
        rms = float(np.sqrt(np.mean(x * x)))
        own.level = max(rms, own.level * math.exp(-len(x) / SAMPLE_RATE / RELEASE))
        if self.floor is None or self.sides[self.floor].level < ACTIVE:
            self.floor = side if own.level >= ACTIVE else None
        elif self.floor == other and own.level > theirs.level * BARGE_IN:
            self.floor = side
        target = DUCK if self.floor == other else 1.0
        joined = np.concatenate([own.tail, x])
        y = np.convolve(joined, KERNEL, mode="valid")
        own.tail = joined[-(TAPS - 1):]
        # To the new gain across the chunk: no click.
        y *= np.linspace(own.gain, target, len(y), dtype=np.float32)
        own.gain = target
        return np.clip(np.round(y), -32768, 32767).astype("<i2").tobytes()
