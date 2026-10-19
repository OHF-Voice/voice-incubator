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

They are asked in ``SKILLS`` order, before the command fallback (meaning,
the LLM) -- they are instant and never wrong: spelling first ("how do you
spell weather" is not about the weather), then, unless the question is about
now or here (``live.is_live``: left to the conversation agent, or the Unknown
command), dates, clock arithmetic, units, arithmetic.

Each gets the question normalized (``common.normalize``): "What’s",
"50°F", "12 × 8", "½ cup" and "$80" as a typed question, or another
speech-to-text, may write them, in the words the grammars read.

Each answers at three levels (``common.LEVELS``): brief ("96."), specific
("It's 96.", the default) and verbose ("Twelve times eight is 96."). Which,
and a template of your own for any of them (rendered by Home Assistant), is
the skill's page's (``settings.yaml`` ``skill_responses``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import arithmetic, clock, dates, spelling, units
from .common import DEFAULT_LEVEL, LEVELS, Reply, Skill, normalize
from .live import is_live

SKILLS: dict[str, Skill] = {
    skill.id: skill for skill in (spelling.SKILL, dates.SKILL, clock.SKILL, units.SKILL, arithmetic.SKILL)
}


@dataclass
class Answer:
    """What a question comes to: a skill's answer, said at every level."""

    kind: str
    """exact: a skill's."""
    text: str | None = None
    """As said at the default level (``DEFAULT_LEVEL``)."""
    skill: str | None = None
    """The skill that answered."""
    seconds: float | None = None
    reply: Reply | None = None
    question: str = ""

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "kind": self.kind,
            "text": self.text,
            "skill": self.skill,
            "seconds": None if self.seconds is None else round(self.seconds, 3),
        }
        if self.reply is not None:
            out["answer"] = self.reply.answer
            out["levels"] = {level: self.reply.at(level) for level in LEVELS}
            out["question"] = self.question
        return out


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


def answer(text: str, on: frozenset[str] | None = None) -> Answer | None:
    """The first answer from the skills that are ``on`` (None: every one)."""
    text = normalize(text)
    if not text:
        return None
    live_question: bool | None = None
    for skill in SKILLS.values():
        if on is not None and skill.id not in on:
            continue
        if not skill.live_too:
            if live_question is None:
                live_question = is_live(text)
            if live_question:
                continue
        try:
            found = skill.answer(text)
        except (ValueError, OverflowError, KeyError, ZeroDivisionError):
            found = None
        if found:
            return Answer("exact", found.at(DEFAULT_LEVEL), skill.id, reply=found, question=text)
    return None


def skill_response(settings: dict[str, Any], skill_id: str) -> dict[str, Any]:
    """How a skill answers, as its page set it (``settings.yaml``
    ``skill_responses``): ``verbosity`` (a level, ``DEFAULT_LEVEL`` unless
    set), and ``response`` -- a template for every level, or one for some
    (``{brief, specific, verbose}``) -- by level, the closest written for a
    level without one (less first, as commands.closest_text)."""
    config = ((settings or {}).get("skill_responses") or {}).get(skill_id) or {}
    level = config.get("verbosity") if config.get("verbosity") in LEVELS else DEFAULT_LEVEL
    response = config.get("response")
    if isinstance(response, str):
        written = {lvl: response for lvl in LEVELS} if response.strip() else {}
    elif isinstance(response, dict):
        written = {lvl: str(response[lvl]) for lvl in LEVELS if str(response.get(lvl) or "").strip()}
    else:
        written = {}
    by_level = {}
    for lvl in LEVELS:
        if lvl in written:
            by_level[lvl] = written[lvl]
            continue
        # The closest written: less said first, then more.
        at = LEVELS.index(lvl)
        order = [LEVELS[i] for i in range(at - 1, -1, -1)] + [LEVELS[i] for i in range(at + 1, len(LEVELS))]
        found = next((written[o] for o in order if o in written), None)
        if found is not None:
            by_level[lvl] = found
    return {"verbosity": level, "response": by_level}
