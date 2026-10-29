"""Dates: "is 2024 a leap year", "what day is Christmas", "how many days in
February", "what day of the week is July 4 2026", "how many days until
Halloween".

Holidays are set on its page (the language's ``HOLIDAYS`` unless changed):
each a name, others it goes by, and when -- "December 25", "fourth Thursday
of November", "last Monday of May", "Easter", "2 days before Easter", or a
date with its year ("June 20 2027"), for one that happens once. How a date
is written, the holidays built in, and how questions are asked and answered
are the app's language's (its pack's ``skills/dates.py``)."""

from __future__ import annotations

import calendar
import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any

from .. import languages
from . import common
from .common import Reply, Skill, options


def _words() -> ModuleType:
    """The app's language's: ``when``, ``key``, ``HOLIDAYS``, ``WHEN_EXAMPLES``."""
    return languages.grammar("dates")


def today() -> dt.date:
    return dt.date.today()


def leap(y):
    return calendar.isleap(y)


def easter(year: int) -> dt.date:
    """Western Easter Sunday (the anonymous Gregorian computus)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    g = (8 * b + 13) // 25
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 19 * l) // 433
    month = (h + l - 7 * m + 90) // 25
    return dt.date(year, month, (h + l - 7 * m + 33 * month + 19) % 32)


@dataclass(frozen=True)
class When:
    """When a holiday falls, read from what was written: the date in a year
    (None: not that year), and whether it's always the same day."""

    on: Callable[[int], dt.date | None]
    fixed: tuple[int, int] | None = None
    """(month, day), every year."""
    year: int | None = None
    """It happens once, that year."""


@dataclass(frozen=True)
class Holiday:
    name: str
    when: str
    also: tuple[str, ...] = ()
    """Other names it goes by."""

    @property
    def date(self) -> When:
        found = _words().when(self.when)
        assert found is not None
        return found

    def next(self, today: dt.date) -> dt.date | None:
        """The next day it falls on, from ``today``; None when it won't."""
        found = self.date
        dates = [found.on(year) for year in (today.year - 1, today.year, today.year + 1)]
        return min((d for d in dates if d is not None and d >= today), default=None)

    def stored(self) -> Any:
        return {"when": self.when, "also": list(self.also)} if self.also else self.when

    def to_json(self) -> dict[str, Any]:
        return {"name": self.name, "when": self.when, "also": list(self.also)}


def _holiday(name: Any, value: Any) -> Holiday:
    """One holiday as stored: when (``"December 25"``), or ``{when, also}``."""
    name = " ".join(str(name or "").split())
    if not name:
        raise ValueError("A holiday needs a name")
    if isinstance(value, dict):
        options(value, ("when", "also"))
        on, also = value.get("when"), value.get("also") or []
    else:
        on, also = value, []
    if isinstance(also, str):
        also = also.split(",")
    if not isinstance(also, list):
        raise ValueError(f"{name}: also, a list of names")
    on = " ".join(str(on or "").split())
    if _words().when(on) is None:
        raise ValueError(f"{name}: when is “{on}”? Like {_words().WHEN_EXAMPLES}")
    return Holiday(name, on, tuple(dict.fromkeys(a for a in (" ".join(str(x).split()) for x in also) if a)))


@dataclass(frozen=True)
class Settings(common.Settings):
    holidays: tuple[Holiday, ...] = field(default_factory=lambda: tuple(_words().HOLIDAYS))
    names: dict[str, Holiday] = field(init=False, repr=False, compare=False)
    """Each name a holiday goes by (``key``) -> it."""

    def __post_init__(self) -> None:
        names: dict[str, Holiday] = {}
        for holiday in self.holidays:
            for name in (holiday.name, *holiday.also):
                other = names.setdefault(_words().key(name), holiday)
                if other is not holiday:
                    raise ValueError(f"“{name}” is both {other.name} and {holiday.name}")
        object.__setattr__(self, "names", names)

    @classmethod
    def load(cls, stored: Any) -> Settings:
        """Stored: ``holidays``, by name -- when, ``{when, also}``, or false
        for a built-in one taken out."""
        changes = options(stored, ("holidays",)).get("holidays") or {}
        if not isinstance(changes, dict):
            raise ValueError("holidays: expected {name: when}")
        holidays = {_words().key(h.name): h for h in _words().HOLIDAYS}
        for name, value in changes.items():
            if value is False:
                holidays.pop(_words().key(str(name)), None)
            else:
                holiday = _holiday(name, value)
                holidays[_words().key(holiday.name)] = holiday
        return cls(tuple(holidays.values()))

    def to_json(self) -> dict[str, Any]:
        now = today()
        built_in = {_words().key(h.name): h for h in _words().HOLIDAYS}
        here = {_words().key(h.name) for h in self.holidays}

        def item(holiday: Holiday) -> dict[str, Any]:
            following = holiday.next(now)
            return holiday.to_json() | {
                "next": following.isoformat() if following else None,
                "built_in": built_in.get(_words().key(holiday.name)) == holiday,
            }

        return {
            "holidays": [item(h) for h in self.holidays],
            "removed": [item(h) for k, h in built_in.items() if k not in here],
        }

    @classmethod
    def store(cls, edited: Any) -> Any:
        """The page sends every holiday (``holidays``: ``[{name, when, also}]``):
        stored, the built-in ones left out (false), changed, and added."""
        items = options(edited, ("holidays", "removed")).get("holidays") or []
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            raise ValueError("holidays: expected a list of {name, when, also}")
        holidays = [_holiday(item.get("name"), {"when": item.get("when"), "also": item.get("also") or []}) for item in items]
        given = {}
        for holiday in holidays:
            if given.setdefault(_words().key(holiday.name), holiday) is not holiday:
                raise ValueError(f"{holiday.name} is given twice")
        cls(tuple(holidays))  # each name one holiday's
        built_in = {_words().key(h.name): h for h in _words().HOLIDAYS}
        changes: dict[str, Any] = {h.name: False for k, h in built_in.items() if k not in given}
        changes |= {h.name: h.stored() for k, h in given.items() if built_in.get(k) != h}
        return {"holidays": changes} if changes else None

    def holiday(self, name: str) -> Holiday | None:
        return self.names.get(_words().key(name))


def answer(text: str, settings: Settings | None = None) -> Reply | None:
    return _words().answer(text, settings or Settings())


SKILL = Skill(
    id="dates",
    name="Dates",
    description="Leap years, days in a month, the day of the week a date falls on, holidays, days until a date.",
    icon="mdi:calendar-month-outline",
    answer=answer,
    settings=Settings,
)


def __getattr__(name: str) -> Any:
    """``HOLIDAYS``: the app's language's built-in holidays."""
    if name == "HOLIDAYS":
        return _words().HOLIDAYS
    raise AttributeError(name)
