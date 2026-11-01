"""Follow-up turns: the satellite's microphone opened again, with no wake
word, for an answer the app asked for -- the conversation agent's question
(Home Assistant's ``continue_conversation``).

The reply says the question, and the ``listening`` sound after it (when the
call's sounds are on) says it is the speaker's turn. The app then hangs up
with ``listen``, to firmware that can (HELLO ``listen``): the satellite starts
a call of its own with the wake word FOLLOW_UP and no pre-roll -- unless it
is muted -- and its ring spins slowly. That call is the answer: no wake word
to check, no other satellite to arbitrate with, and the speaker is whoever
was answered. Nothing said within NO_SPEECH_SECONDS ends it quietly, and it
is kept nowhere. A satellite told to listen whose call hasn't started
WAIT_SECONDS later (muted) is asked nothing more; a wake word meanwhile
starts over.

An answer to the agent goes to it as said, in the area's conversation, even
one that sounds like a command: it asked. Only an answer that ``ends`` the
conversation -- "cancel", "never mind", "no thanks" (the language's
``ANSWER_ENDS``), or the Never mind command -- goes nowhere. The agent may ask
again, as often as it likes: the speaker gets out by ending it, or by saying
nothing.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from . import languages

FOLLOW_UP = "follow_up"
"""The wake word of a call the satellite started for an answer."""

NO_SPEECH_SECONDS = 5.0
"""Nothing said this long after the microphone opened: no answer. (The
command's own ``no_speech_seconds`` when that is shorter.)"""

WAIT_SECONDS = 5.0
"""How long after the hang-up the satellite's call may start."""


@dataclass
class FollowUp:
    """What a satellite's reopened microphone answers."""

    kind: str
    """``agent``: the conversation agent asked something back."""
    question: str
    """What was asked, as it was said."""
    after: str | None
    """The call it follows: its record's id."""
    who: Any
    """Who was answered (people.Speaker): the speaker of the answer too."""
    gain: float = 1.0
    """What the call's VAD heard the speaker louder by (endpoint.vad_gain)."""
    warm: bytes = b""
    """Their wake word, which the VAD hears first (Endpointer ``warm``)."""
    told: float = field(default_factory=time.monotonic)
    """When the satellite was told to listen."""

    def stale(self, now: float | None = None) -> bool:
        """Its call didn't start in time: nobody is answering."""
        return (time.monotonic() if now is None else now) - self.told > WAIT_SECONDS

    def to_json(self) -> dict[str, Any]:
        return {"kind": self.kind, "question": self.question, "after": self.after, "speaker": self.who.name}


def _words(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s']", " ", text.lower()).split())


def ends(text: str) -> bool:
    """Is ``text`` -- an answer -- only a way to end the conversation?"""
    return _words(text) in languages.current().reading.ANSWER_ENDS
