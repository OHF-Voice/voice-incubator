"""Arithmetic: "what's twelve times eight", "15 percent of 80", "the square
root of 144", "the average of four, eight and twelve". How it is asked and
said is the app's language's (its pack's ``skills/arithmetic.py``)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .. import languages
from . import common
from .common import DIGITS, Reply, Skill, options


@dataclass(frozen=True)
class Settings(common.Settings):
    digits: int = DIGITS
    """Significant digits in an answer that isn't whole: 14.29 (4), 14.3 (3)."""

    @classmethod
    def load(cls, stored: Any) -> Settings:
        return cls(common.digits(options(stored, ("digits",)).get("digits")))

    def to_json(self) -> dict[str, Any]:
        return {"digits": self.digits}

    @classmethod
    def store(cls, edited: Any) -> Any:
        settings = cls.load(edited)
        return {"digits": settings.digits} if settings != cls() else None


def answer(text: str, settings: Settings | None = None) -> Reply | None:
    return languages.grammar("arithmetic").answer(text, settings or Settings())


SKILL = Skill(
    id="arithmetic",
    name="Math",
    description="Adds, subtracts, multiplies and divides; percentages, fractions, powers, roots, averages.",
    icon="mdi:calculator-variant-outline",
    answer=answer,
    settings=Settings,
)
