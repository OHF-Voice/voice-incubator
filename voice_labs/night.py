"""Night mode: light and sound kept to a minimum in an area.

Each area with a satellite has a night mode switch (the Voice Labs
integration's, beside do not disturb; integration.Integration follows them).
On, its satellites:

- play none of the call's sounds: not heard, asking, not understood,
  acknowledge, listening;
- say nothing for a command that acts: the satellite blinks how it went --
  one dim pulse when it was done, two red blinks when it wasn't, or nothing
  was understood (the ``result`` event, ``ok``);
- say quietly (QUIET_DB down) what can't be told with a blink (``says``): an
  answer -- a skill's, the conversation agent's, a command that only answers
  -- and a command's question before it acts, and so whatever it asks back.
  Piper says it on the satellite, even where a Home Assistant TTS entity
  speaks otherwise, whose loudness the app can't set;
- take nothing from other areas: night mode is do not disturb too
  (integration.quiet_areas).

Timers and alarms ring, and say what rang, as loud as ever; the firmware
keeps its ring dim and warm, but for a ring (the ``night`` event).
"""

from __future__ import annotations

from typing import Any

import numpy as np

QUIET_DB = -15.0
"""How much quieter what is said at night is."""

OK = ("done", "dry run", "declined", "ended")
"""How a call turned out (_carry_out's) when it went as asked: ran, shown,
not wanted after all, or a conversation ended."""


def says(outcome: str, decision: Any) -> bool:
    """Whether what a call came to is said at night (quietly): an answer, or
    a question before acting -- not what a command did."""
    if outcome in ("answered", "asked"):
        return True
    behavior = getattr(decision, "behavior", None)
    return outcome == "done" and behavior is not None and behavior.question


def went_well(outcome: str, decision: Any) -> bool:
    """Whether the blink says it went well: the call turned out as asked, and
    the command it ran was done (not ``no_timers``, ``failed`` ...)."""
    return outcome in OK and getattr(decision, "outcome", None) in (None, "done")


def quieter(pcm: bytes) -> bytes:
    """16-bit PCM, QUIET_DB down."""
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) * 10 ** (QUIET_DB / 20)
    return samples.astype("<i2").tobytes()
