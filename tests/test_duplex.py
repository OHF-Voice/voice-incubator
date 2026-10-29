"""The intercom's relay: no squeal between two satellites."""

from __future__ import annotations

import numpy as np

from voice_labs.duplex import DUCK, Duplex

RATE = 16000


def tone(hz: float, level: float, seconds: float = 0.032) -> bytes:
    t = np.arange(int(RATE * seconds)) / RATE
    return (np.sin(2 * np.pi * hz * t) * level).astype("<i2").tobytes()


def loudness(pcm: bytes) -> float:
    x = np.frombuffer(pcm, dtype="<i2").astype(np.float64)
    return float(np.sqrt(np.mean(x * x)))


def through(duplex: Duplex, side: str, other: str, pcm: bytes, times: int = 10) -> bytes:
    out = b""
    for _ in range(times):
        out = duplex.relay(side, other, pcm)
    return out


def test_the_voice_band_and_nothing_above():
    duplex = Duplex()
    voice = through(duplex, "a", "b", tone(1000, 8000))
    squeal = through(Duplex(), "a", "b", tone(6000, 8000))
    assert loudness(voice) > 0.9 * loudness(tone(1000, 8000))
    assert loudness(squeal) < 0.05 * loudness(tone(6000, 8000))


def test_who_speaks_first_has_the_floor():
    duplex = Duplex()
    # Quiet on both sides: nobody turned down.
    through(duplex, "a", "b", tone(500, 50))
    assert loudness(through(duplex, "b", "a", tone(500, 50))) > 0.5 * loudness(tone(500, 50))
    # A speaks; B hears it from its speaker, quieter: B's echo goes back turned down.
    through(duplex, "a", "b", tone(800, 6000))
    echo = through(duplex, "b", "a", tone(800, 2000))
    assert loudness(echo) < 1.5 * DUCK * loudness(tone(800, 2000))
    # B speaks up over it: B has the floor, and A is turned down.
    loud = through(duplex, "b", "a", tone(800, 16000))
    assert loudness(loud) > 0.5 * loudness(tone(800, 16000))
    assert duplex.floor == "b"
    assert loudness(through(duplex, "a", "b", tone(800, 6000))) < 1.5 * DUCK * loudness(tone(800, 6000))
