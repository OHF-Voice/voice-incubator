"""One utterance, several satellites: which one answers.

A wake word heard by more than one satellite -- the same word, their calls
starting within ``WINDOW`` seconds of the first, whatever their areas (an open
plan is two areas with no wall between) -- is one utterance. The satellite
that heard it best answers; the others end their call (``answered_elsewhere``:
the firmware flashes the ring dimly). Best is how far the wake word stands
above the background (``levels``), on the echo-cancelled capture: it comes
before automatic gain, so it still says how near the talker was, and the
satellite's own playback is taken out of it. The detection's probability
breaks a tie.

The first call the verifier accepts decides, among the calls that have heard
it by then; one that starts later answers nothing. Should the one chosen turn
out not to be the wake word (the verifier refuses it), the next best answers.
A satellite alone waits for no one.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

WINDOW = 1.0
"""How long after the first satellite's call another's for the same wake word
is the same utterance, in seconds."""

DECIDED_WAIT = 1.0
"""How long a call accepted but not chosen waits for the chosen one's verdict
before giving way to it anyway, in seconds."""

FRAME = 320
"""20 ms at 16 kHz."""


def levels(pcm: bytes) -> tuple[float, float] | None:
    """A pre-roll's wake word and background, in dB of full scale: its loudest
    stretch (the 90th percentile of its 20 ms frames' levels) and its quietest
    (the 10th). The pre-roll ends at the detection, so the wake word is in it.
    None for too little audio."""
    samples = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2").astype(np.float64) / 32768.0
    frames = len(samples) // FRAME
    if frames < 10:
        return None
    rms = np.sqrt(np.mean(samples[: frames * FRAME].reshape(frames, FRAME) ** 2, axis=1)) + 1e-9
    db = 20 * np.log10(rms)
    return float(np.percentile(db, 90)), float(np.percentile(db, 10))


@dataclass
class Heard:
    """One satellite's call for the utterance."""

    key: str
    started: float
    probability: float | None = None
    """The detection's, 0-1."""
    clarity: float | None = None
    """How far the wake word stands above the background, in dB."""
    verdict: bool | None = None
    """The verifier's: None until it has said."""

    def to_json(self) -> dict[str, object]:
        return {"satellite": self.key, "clarity": None if self.clarity is None else round(self.clarity, 1),
                "probability": self.probability, "verdict": self.verdict}


@dataclass
class Utterance:
    """A wake word, and the satellites that heard it."""

    wake_word: str
    started: float
    heard: dict[str, Heard] = field(default_factory=dict)
    winner: str | None = None
    changed: asyncio.Event = field(default_factory=asyncio.Event)

    def best(self) -> str | None:
        """The one that heard it best, of those not refused, with how well
        known; else the first heard."""
        alive = [h for h in self.heard.values() if h.verdict is not False]
        measured = [h for h in alive if h.clarity is not None]
        if measured:
            return max(measured, key=lambda h: (h.clarity, h.probability or 0.0)).key
        return alive[0].key if alive else None

    def to_json(self) -> dict[str, object]:
        return {"wake_word": self.wake_word, "winner": self.winner, "heard": [h.to_json() for h in self.heard.values()]}


class Arbiter:
    """The utterances being heard, by wake word."""

    def __init__(self, window: float = WINDOW, clock: Callable[[], float] = time.monotonic) -> None:
        self.window = window
        self.clock = clock
        self._utterances: list[Utterance] = []

    def heard(self, key: str, wake_word: str, probability: float | None = None) -> Utterance:
        """A satellite's call started for ``wake_word``: the utterance it is
        one of -- another's started within the window, else its own."""
        now = self.clock()
        self._utterances = [u for u in self._utterances if now - u.started < max(self.window, DECIDED_WAIT) * 4]
        utterance = next(
            (u for u in reversed(self._utterances) if u.wake_word == wake_word and now - u.started <= self.window and key not in u.heard),
            None,
        )
        if utterance is None:
            utterance = Utterance(wake_word, now)
            self._utterances.append(utterance)
        utterance.heard[key] = Heard(key, now, probability)
        return utterance

    @staticmethod
    def measured(utterance: Utterance, key: str, clarity: float | None) -> None:
        if key in utterance.heard:
            utterance.heard[key].clarity = clarity

    async def answers(self, utterance: Utterance | None, key: str, accepted: bool) -> bool:
        """Whether this satellite answers, now that the verifier has said
        (``accepted``). One refused answers nothing, and gives way if it was
        the one chosen; one accepted answers if it heard it best of those
        heard -- or, the one chosen refused, best of the rest."""
        if utterance is None or key not in utterance.heard:
            return accepted
        utterance.heard[key].verdict = accepted
        utterance.changed.set()
        if not accepted:
            if utterance.winner == key:
                utterance.winner = None
            return False
        while True:
            if utterance.winner is None:
                utterance.winner = utterance.best()
            if utterance.winner == key:
                return True
            chosen = utterance.heard.get(utterance.winner or "")
            if chosen is None or chosen.verdict is True:
                return False
            if chosen.verdict is False:
                utterance.winner = None
                continue
            # The one chosen is still being verified: its verdict decides.
            utterance.changed.clear()
            try:
                await asyncio.wait_for(utterance.changed.wait(), DECIDED_WAIT)
            except asyncio.TimeoutError:
                return False
