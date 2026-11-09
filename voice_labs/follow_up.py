"""Follow-up turns: the satellite's microphone opened again, with no wake
word, for an answer the app asked for -- the conversation agent's question
(Home Assistant's ``continue_conversation``), a command's yes-or-no one
before it acts (its ``confirm``: "Cancel all 3 timers?"), or which of
several a command meant (``which``: "The 10 minute timer or the 5 minute
timer?").

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

Which of several timers, alarms or reminders (the command's ``ambiguous``
outcome, voice_labs/timers.py and alarms.py) is answered by what tells them
apart (``pick``): "both" or "all" (the language's ``WHICH_ALL``) is every
one; "the second one" one in the order they were said (``WHICH_ORDINALS``);
else the words in only one of their labels -- "the five minute one",
"pizza", "seven thirty" -- numbers said in words or digits alike. The command
then runs on that one (or each). The same command said again runs as said
(its own words pick); another runs instead; "never mind" ends it; anything
else does nothing. Kaldi hears the answers its language's ``WHICH_SAID``
templates say, with the home's lengths, timers' names and times.
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
    choices: list[dict[str, Any]] | None = None
    """What ``which`` chooses among (``id``, ``label``), in the order said;
    the command it runs on the one chosen is ``decision``."""

    def stale(self, now: float | None = None) -> bool:
        """Its call didn't start in time: nobody is answering."""
        return (time.monotonic() if now is None else now) - self.told > WAIT_SECONDS

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind, "question": self.question, "after": self.after, "speaker": self.who.name,
            **({"choices": [c["label"] for c in self.choices]} if self.choices else {}),
        }


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


def pick(text: str, choices: list[dict[str, Any]]) -> list[str] | None:
    """Which of ``choices`` (each its ``id`` and ``label``) an answer to
    which picks: their ids -- every one's for "both" -- or None. A word in
    a label ("the 30 second timer") is read as the label's, not as an
    ordinal or "all"."""
    if not choices:
        return None
    reading = languages.current().reading
    labels = [_said_words(str(c["label"])) for c in choices]
    in_labels = {w for words in labels for w in words}
    words = _said_words(text, answer=True)
    said = " ".join(words)
    if not said:
        return None

    def says(phrase: str) -> bool:
        return not set(phrase.split()) & in_labels and _says(said, {phrase})

    if any(says(p) for p in getattr(reading, "WHICH_ALL", ())):
        return [str(c["id"]) for c in choices]
    ordinals = {index for phrase, index in getattr(reading, "WHICH_ORDINALS", ()) if says(phrase)}
    if len(ordinals) == 1 and -len(choices) <= (index := ordinals.pop()) < len(choices):
        return [str(choices[index]["id"])]
    # What only some of them say, heard.
    shared = set.intersection(*(set(words) for words in labels))
    heard = set(words)
    scores = [len((set(label) - shared) & heard) for label in labels]
    best = max(scores)
    if best and scores.count(best) == 1:
        return [str(choices[scores.index(best)]["id"])]
    return None


def _said_words(text: str, answer: bool = False) -> list[str]:
    """``text``'s words, lowercase, numbers as digits ("twenty five" and
    "25" alike; "7:30" is 7 30), plurals as one ("minutes" is "minute").
    ``answer``: an answer's, whose last word, when it is the word for one,
    stands for what it picks ("the five minute one")."""
    words = re.sub(r"[^\w\s']", " ", text.lower()).split()
    numbers = _numbers()
    if answer and len(words) > 1 and words[-1] == numbers.get(1, ("one",))[0]:
        words = words[:-1]
    out: list[str] = []
    index = 0
    longest = max((len(spelled) for spelled in numbers.values()), default=1)
    spelled_as = {spelled: value for value, spelled in numbers.items()}
    while index < len(words):
        for size in range(min(longest, len(words) - index), 0, -1):
            value = spelled_as.get(tuple(words[index : index + size]))
            if value is not None:
                out.append(str(value))
                index += size
                break
        else:
            word = words[index].removesuffix("'s")
            out.append(word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word)
            index += 1
    return out


_SPELLED: dict[str, dict[int, tuple[str, ...]]] = {}


def _numbers() -> dict[int, tuple[str, ...]]:
    """The language's numbers to 200, spelled (unicode_rbnf): as words."""
    code = languages.current().code
    if code not in _SPELLED:
        spelled: dict[int, tuple[str, ...]] = {}
        try:
            from unicode_rbnf import RbnfEngine  # pylint: disable=import-outside-toplevel

            engine = RbnfEngine.for_language(code)
            for value in range(201):
                words = tuple(re.sub(r"[^\w\s']", " ", engine.format_number(value).text.lower()).split())
                if words:
                    spelled[value] = words
        except Exception:  # noqa: BLE001 - no spelling for the language: digits only
            spelled = {}
        _SPELLED[code] = spelled
    return _SPELLED[code]
