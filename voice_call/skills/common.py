"""What the skills share (not a skill itself): what a skill is and its
settings, and an answer said at each level, its numbers as the app's
language says them. How a question is read -- numbers in words, a question
normalized and cleaned up -- is the language's (its pack's
``skills/numbers.py``: ``languages.grammar``)."""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .. import languages

LEVELS = ("brief", "specific", "verbose")
"""How much a skill says (commands.SPOKEN): the answer alone ("96."), as a
sentence ("It's 96."), or with the question said back ("Twelve times eight
is 96."). A question always answers, so never silent or a beep."""

DEFAULT_LEVEL = "specific"


@dataclass(frozen=True)
class Reply:
    """What a skill found: the answer itself ("96"), and how it is said at
    each level (``LEVELS``)."""

    answer: str
    brief: str
    specific: str
    verbose: str

    def at(self, level: str) -> str:
        return getattr(self, level) if level in LEVELS else self.specific


class Settings:
    """A skill's settings, as stored (``settings.json`` ``skill_settings``, by
    skill: only what differs from the built-in, so what an update adds
    reaches you) and as its page edits them (in full). None of its own: this."""

    @classmethod
    def load(cls, stored: Any) -> Settings:
        """As stored (None: the built-in). Raises ValueError saying what is
        wrong."""
        options(stored, ())
        return cls()

    def to_json(self) -> dict[str, Any]:
        """What its page edits."""
        return {}

    @classmethod
    def store(cls, edited: Any) -> Any:
        """What its page sent, as stored: what differs from the built-in, or
        None. Raises ValueError saying what is wrong."""
        cls.load(edited)
        return None


def options(stored: Any, known: tuple[str, ...]) -> dict[str, Any]:
    """Stored settings as a mapping of ``known`` keys (None: empty)."""
    if stored is None:
        return {}
    if not isinstance(stored, dict):
        raise ValueError("expected a mapping")
    unknown = sorted(str(key) for key in stored if key not in known)
    if unknown:
        raise ValueError(f"unknown {', '.join(unknown)}" + (f" (use {', '.join(known)})" if known else ""))
    return stored


DIGITS = 4
"""Significant digits in an answer, unless set: 8.047, 0.3333, 14.29."""


def digits(value: Any) -> int:
    """A precision setting: 1 to 12 significant digits (None: ``DIGITS``)."""
    if value is None:
        return DIGITS
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError("precision: a whole number of digits from 1 to 12")
    try:
        number = float(value)
    except ValueError as err:
        raise ValueError("precision: a whole number of digits from 1 to 12") from err
    if not number.is_integer() or not 1 <= number <= 12:
        raise ValueError("precision: a whole number of digits from 1 to 12")
    return int(number)


@dataclass(frozen=True)
class Skill:
    """A kind of question answered without an LLM. ``answer`` parses the whole
    question *and* computes the answer, or returns None: a partial parse falls
    through rather than guessing. How is the app's language's (its
    ``skills/<id>.py``: ``answer``, and ``EXAMPLES``)."""

    id: str
    name: str
    description: str
    icon: str
    answer: Callable[[str, Any], Reply | None]
    """The question, normalized (``normalize``), and the skill's settings
    (``settings``) -> what is said, or None."""
    settings: type[Settings] = Settings
    """What can be set (on its page)."""
    live_too: bool = False
    """Asked even when the question is about now or here (live.is_live)."""

    @property
    def examples(self) -> tuple[str, ...]:
        """Questions it answers, in the app's language."""
        return tuple(languages.grammar(self.id).EXAMPLES)

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "examples": list(self.examples),
            "icon": self.icon,
        }


def reply(
    answer: str | None, question: str, restated: str | None = None, brief: str | None = None, specific: str | None = None
) -> Reply | None:
    """``answer`` at every level: brief, the answer ("96."); specific, as a
    sentence in the app's language ("It's 96."; numbers.sentence) unless
    given; verbose, ``restated`` ("twelve times eight is 96"), else the
    question said back first ("Is 2024 a leap year? Yes.")."""
    if answer is None:
        return None
    specific = specific or languages.grammar("numbers").sentence(answer)
    if restated:
        verbose = _period(restated[:1].upper() + restated[1:])
    else:
        asked = re.sub(r"[?!.\s]+$", "", question.strip())
        verbose = f"{asked[:1].upper()}{asked[1:]}? {specific}" if asked else specific
    return Reply(answer, brief or _period(answer), specific, verbose)


def _period(text: str) -> str:
    text = text.strip()
    return text if text[-1:] in ".?!" else text + "."


def rounded(x: float, places: int = DIGITS) -> float:
    """``x`` to ``places`` significant digits -- a whole number past them is
    kept whole (31536000, not 31540000)."""
    if not math.isfinite(x) or x == 0:
        return x
    if abs(x) >= 10**places:
        return float(round(x))
    return float(f"{x:.{places}g}")


def fmt(x, places: int = DIGITS):
    """A number as said: a whole number in full ("1,609", "31,536,000"),
    others to ``places`` significant digits ("8.047"); never in e-notation.
    The separators are the app's language's (numbers.DECIMAL, THOUSANDS):
    "8,047" in German."""
    words = languages.grammar("numbers")
    if isinstance(x, float) and math.isfinite(x):
        x = rounded(x, places)
        if x.is_integer():
            x = int(x)
    if isinstance(x, float):
        text = format(Decimal(repr(x)), "f")
        text = text.rstrip("0").rstrip(".") if "." in text else text
        return text.replace(".", words.DECIMAL)
    return f"{x:,}".replace(",", words.THOUSANDS) if abs(x) >= 10000 else str(x)


def normalize(text: str) -> str:
    """A question as typed, or written out by a speech-to-text, in the words
    the app's language's grammars read (its numbers.normalize)."""
    return languages.grammar("numbers").normalize(text)


