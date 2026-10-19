"""Skills: questions no command takes, answered without an LLM.

One module each (``SKILL`` in it: what it is, and ``answer``); ``common`` is
what they share. Ported from the smart-home quick-questions benchmark
(llama.cpp's smart-home-qa-bench: router.py; README "Skills and
questions"): each parses the whole question *and* computes an answer, or
lets it fall through -- on 180 held-out questions none of their answers was
wrong.

Every skill is the Home's unless taken off it (``settings.yaml``
``skills: {spelling: false}``), and answers on every satellite that takes the
Home's commands and skills. A satellite may also be given a skill of its own
(``satellites.yaml``: ``skills: {spelling: true}``) -- one the Home doesn't
have, or for a satellite that doesn't take the Home's.

When they are asked, in ``SKILLS`` order:

Each gets the question normalized (``common.normalize``): "What’s",
"50°F", "12 × 8", "½ cup" and "$80" as a typed question, or another
speech-to-text, may write them, in the words the grammars read.

1. Before the command fallback (meaning, the LLM) -- they are instant and
   never wrong: spelling first ("how do you spell weather" is not about the
   weather), then, unless the question is about now or here
   (``live.is_live``), dates, clock arithmetic, units, arithmetic.
2. After it, when it found nothing: the live notice ("do I need an
   umbrella" may still be the weather command). Then the question model
   (voice_call/questions.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import arithmetic, clock, dates, live, spelling, units
from .common import Skill, normalize
from .live import is_live

SKILLS: dict[str, Skill] = {
    skill.id: skill
    for skill in (spelling.SKILL, dates.SKILL, clock.SKILL, units.SKILL, arithmetic.SKILL, live.SKILL)
}


@dataclass
class Answer:
    """What a question comes to."""

    kind: str
    """exact | live (a skill's) | knowledge (the question model's)"""
    text: str | None = None
    skill: str | None = None
    """The skill that answered."""
    seconds: float | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "text": self.text,
            "skill": self.skill,
            "seconds": None if self.seconds is None else round(self.seconds, 3),
        }


def home_skills(home: Any = None) -> frozenset[str]:
    """The Home's skills: each unless ``home`` (``settings.yaml``) turns it
    off."""
    home = home if isinstance(home, dict) else {}
    return frozenset(skill_id for skill_id in SKILLS if home.get(skill_id) is not False)


def own_skills(own: Any = None) -> frozenset[str]:
    """A satellite's own skills: each its setting gives it. (False, from
    before the Home's could be left out, takes nothing away.)"""
    own = own if isinstance(own, dict) else {}
    return frozenset(skill_id for skill_id in SKILLS if own.get(skill_id) is True)


def enabled(home: Any = None, own: Any = None, takes_home: bool = True) -> frozenset[str]:
    """The skills that answer on a satellite: its own, and the Home's when it
    takes them."""
    return own_skills(own) | (home_skills(home) if takes_home else frozenset())


def answer(text: str, on: frozenset[str] | None = None, last: bool = False) -> Answer | None:
    """The first answer from the skills that are ``on`` (None: every one), of
    those asked before the command fallback, or (``last``) after it."""
    text = normalize(text)
    if not text:
        return None
    live_question: bool | None = None
    for skill in SKILLS.values():
        if skill.last != last or (on is not None and skill.id not in on):
            continue
        if not skill.live_too:
            if live_question is None:
                live_question = is_live(text)
            if live_question:
                continue
        try:
            said = skill.answer(text)
        except (ValueError, OverflowError, KeyError, ZeroDivisionError):
            said = None
        if said:
            return Answer(skill.kind, said, skill.id)
    return None
