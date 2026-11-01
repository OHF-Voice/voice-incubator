"""Follow-up turns: the satellite's microphone opened again, with no wake
word, for an answer the app asked for -- the conversation agent's question
(Home Assistant's ``continue_conversation``), or a command's yes-or-no one
before it acts (its ``confirm``: "Cancel all 3 timers?").

The reply says the question, and the ``listening`` sound after it (when the
call's sounds are on) says it is the speaker's turn. The app then hangs up
with ``listen``, to firmware that can (HELLO ``listen``): the satellite starts
a call of its own with the wake word FOLLOW_UP and no pre-roll -- unless it
is muted -- and its ring spins slowly. That call is the answer: no wake word
to check, no other satellite to arbitrate with, and the speaker is whoever
was answered. Nothing said within the satellite's no-speech time (its End of
speech, eight seconds unless changed) ends it quietly, and it is kept
nowhere. A satellite told to listen whose call hasn't started
WAIT_SECONDS later (muted) is asked nothing more; a wake word meanwhile
starts over.

An answer to the agent goes to it as said, in the area's conversation, even
one that sounds like a command: it asked. Only an answer that ``ends`` the
conversation -- "cancel", "never mind", "no thanks" (the language's
``ANSWER_ENDS``), or the Never mind command -- goes nowhere. The agent may ask
again, as often as it likes: the speaker gets out by ending it, or by saying
nothing.

A command's question is answered yes or no (``yes_or_no``): by its words
first -- one of the language's ``ANSWER_YES`` and none of its ``ANSWER_NO``,
or the other way round; one that ends the conversation is no -- and only
when they leave it open (neither, or both), by meaning (``by_meaning``):
EmbeddingGemma's closest of the ways yes and no are said whole
(``YES_SAID``, ``NO_SAID``), when it is close (MEANING_FLOOR) and clearly one
side's (MEANING_MARGIN). Sentence embeddings are weak at negation, so they
never overrule the words. Of 37 English answers, the words read 22 and
meaning 2 more ("make it so", "please don't"); the 13 left open were 7 that
are neither ("uh huh", "Okay, no.", "yes but not the pizza one") and 6 that
are ("you bet", "absolutely not", "maybe later") -- none read wrong. Yes runs the command as it would have run (re-planned: a
timer may have finished meanwhile); no doesn't; the command itself said
again is yes; another command said instead runs instead; anything else is
not caught, and nothing is done -- it is never asked twice.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from . import languages

FOLLOW_UP = "follow_up"
"""The wake word of a call the satellite started for an answer."""

WAIT_SECONDS = 5.0
"""How long after the hang-up the satellite's call may start."""

MEANING_FLOOR = 0.8
"""How close (cosine) an answer must come to a way of saying yes or no to be
read by meaning: unrelated sentences ("what time is it", "banana") came
within 0.72."""

MEANING_MARGIN = 0.07
"""How much closer to one side's than to the other's: answers that are
neither came within 0.03 of even, "make it so" 0.074 to yes."""


@dataclass
class FollowUp:
    """What a satellite's reopened microphone answers."""

    kind: str
    """``agent``: the conversation agent asked something back; ``confirm``: a
    command asks before it acts."""
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
    decision: Any = None
    """What yes runs (``confirm``): the command as it was heard (engine.Decision)."""
    wake_word: dict[str, Any] | None = None
    """The wake word that started it, as commands see it."""

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


def _says(words: str, phrases: set[str]) -> bool:
    padded = f" {words} "
    return any(f" {phrase} " in padded for phrase in phrases)


def yes_or_no(text: str) -> str | None:
    """A yes-or-no question's answer by its words: ``yes``, ``no``, or None
    (neither, or both)."""
    words = _words(text)
    if not words:
        return None
    reading = languages.current().reading
    if words in reading.ANSWER_ENDS:
        return "no"
    yes, no = _says(words, reading.ANSWER_YES), _says(words, reading.ANSWER_NO)
    if yes == no:
        return None
    return "yes" if yes else "no"


def by_meaning(embedder: Any, text: str) -> str | None:
    """An answer that ``yes_or_no`` left open, by meaning: ``yes`` or ``no``
    when it is close to that side's phrases and clearly closer than to the
    other's, else None. ``embedder``: the engine's (EmbeddingGemma)."""
    if not _words(text):
        return None
    reading = languages.current().reading
    yes = embedder.embed(list(reading.YES_SAID), True)
    no = embedder.embed(list(reading.NO_SAID), True)
    [answer] = embedder.embed([text])
    to_yes, to_no = float(np.max(yes @ answer)), float(np.max(no @ answer))
    if max(to_yes, to_no) < MEANING_FLOOR or abs(to_yes - to_no) < MEANING_MARGIN:
        return None
    return "yes" if to_yes > to_no else "no"
