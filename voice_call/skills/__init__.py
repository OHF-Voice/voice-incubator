"""Skills: questions no command takes, answered without an LLM.

One module each (``SKILL`` in it: what it is, and ``answer``); ``common`` is
what they share. Ported from the smart-home quick-questions benchmark
(llama.cpp's smart-home-qa-bench: router.py; README "Skills and
questions"): each parses the whole question *and* computes an answer, or
lets it fall through -- on 180 held-out questions none of their answers was
wrong.

Every skill is on unless turned off: for the whole home (``settings.yaml``
``skills: {spelling: false}``) or for one satellite (``satellites.yaml``, the
same), the satellite's own setting winning.

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


def enabled(home: Any = None, own: Any = None) -> frozenset[str]:
    """The skills that are on: each as the satellite's own setting (``own``)
    says, else the home's, else on."""
    out = set()
    for skill_id in SKILLS:
        on = True
        for setting in (home, own):
            if isinstance(setting, dict) and isinstance(setting.get(skill_id), bool):
                on = setting[skill_id]
        if on:
            out.add(skill_id)
    return frozenset(out)


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
