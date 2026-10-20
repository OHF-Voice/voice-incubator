"""Dates: "is 2024 a leap year", "what day is Christmas", "how many days in
February", "what day of the week is July 4 2026", "how many days until
Halloween".

Holidays are set on its page (``HOLIDAYS`` unless changed): each a name,
others it goes by, and when -- "December 25", "fourth Thursday of November",
"last Monday of May", "Easter", "2 days before Easter", or a date with its
year ("June 20 2027"), for one that happens once."""

from __future__ import annotations

import calendar
import datetime as dt
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from . import common
from .common import N, Skill, clean, options, parse_num, reply

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MONTHS |= {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}
DAYS = [d.lower() for d in calendar.day_name]
NTH = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "last": -1, "1": 1, "2": 2, "3": 3, "4": 4, "5": 5}
"""The nth weekday of a month ("4th": "4", its ending gone)."""


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


def when(text: str) -> When | None:
    """"December 25", "25 December", "fourth Thursday of November", "last
    Monday in May", "Easter", "2 days before Easter", "1 week after ...",
    "June 20 2027" -> when it falls; None when it can't be read."""
    s = " ".join(re.sub(r"[,.]", " ", text.lower()).split())
    s = re.sub(r"^the ", "", s)
    s = re.sub(r"\b(\d{1,2})(st|nd|rd|th)\b", r"\1", s)
    if m := re.fullmatch(r"(.+?) (days?|weeks?) (before|after) (.+)", s):
        count = parse_num(m.group(1))
        inner = when(m.group(4))
        if count is None or not float(count).is_integer() or count < 0 or inner is None:
            return None
        delta = dt.timedelta(days=int(count) * (7 if m.group(2).startswith("week") else 1))
        delta = -delta if m.group(3) == "before" else delta

        def shifted(year: int) -> dt.date | None:
            date = inner.on(year)
            return date + delta if date is not None else None

        return When(shifted, year=inner.year)
    if s in ("easter", "easter sunday"):
        return When(easter)
    if m := re.fullmatch(r"(\w+) (\w+) (?:of|in) (\w+)", s):
        nth, day, month = NTH.get(m.group(1)), m.group(2), MONTHS.get(m.group(3))
        if nth is not None and day in DAYS and month:
            weekday = DAYS.index(day)

            def nth_weekday(year: int) -> dt.date | None:
                days = [d for d in calendar.Calendar().itermonthdates(year, month)
                        if d.month == month and d.weekday() == weekday]
                return days[-1] if nth == -1 else days[nth - 1] if nth <= len(days) else None

            return When(nth_weekday)
    if m := re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", s):
        year, month, day = (int(g) for g in m.groups())
    elif m := (re.fullmatch(r"([a-z]+) (\d{1,2})(?: (\d{4}))?", s)
               or re.fullmatch(r"(\d{1,2}) (?:of )?([a-z]+)(?: (\d{4}))?", s)):
        first, second = m.group(1), m.group(2)
        month_name, day = (first, second) if first.isalpha() else (second, first)
        if month_name not in MONTHS:
            return None
        month, day, year = MONTHS[month_name], int(day), int(m.group(3)) if m.group(3) else None
    else:
        return None
    try:
        dt.date(year or 2000, month, day)  # 2000: a leap year, so February 29 is a day
    except ValueError:
        return None
    if year is not None:
        date = dt.date(year, month, day)
        return When(lambda y: date if y == year else None, year=year)

    def yearly(y: int) -> dt.date | None:
        try:
            return dt.date(y, month, day)
        except ValueError:  # February 29, not a leap year
            return None

    return When(yearly, fixed=(month, day))


def key(name: str) -> str:
    """A holiday's name as looked up: "St. Patrick's Day" -> "saint patricks
    day", "the Fourth of July" -> "fourth of july"."""
    s = name.lower().replace("’", "'").replace("'", "")
    s = re.sub(r"\bst\b\.?", "saint", s)
    s = " ".join(re.sub(r"[,.]", " ", s).split())
    return re.sub(r"^the ", "", s)


@dataclass(frozen=True)
class Holiday:
    name: str
    when: str
    also: tuple[str, ...] = ()
    """Other names it goes by."""

    @property
    def date(self) -> When:
        found = when(self.when)
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


def _h(name: str, on: str, *also: str) -> Holiday:
    return Holiday(name, on, also)


HOLIDAYS = (
    _h("New Year's Day", "January 1"),
    _h("Martin Luther King Jr. Day", "third Monday of January", "MLK Day", "Martin Luther King Day"),
    _h("Valentine's Day", "February 14"),
    _h("Presidents' Day", "third Monday of February"),
    _h("St. Patrick's Day", "March 17", "Saint Paddy's Day"),
    _h("April Fools' Day", "April 1"),
    _h("Good Friday", "2 days before Easter"),
    _h("Easter", "Easter", "Easter Sunday"),
    _h("Mother's Day", "second Sunday of May"),
    _h("Memorial Day", "last Monday of May"),
    _h("Father's Day", "third Sunday of June"),
    _h("Juneteenth", "June 19"),
    _h("Independence Day", "July 4", "the Fourth of July"),
    _h("Labor Day", "first Monday of September"),
    _h("Halloween", "October 31"),
    _h("Veterans Day", "November 11"),
    _h("Thanksgiving", "fourth Thursday of November", "Thanksgiving Day"),
    _h("Christmas Eve", "December 24"),
    _h("Christmas", "December 25", "Christmas Day"),
    _h("New Year's Eve", "December 31"),
)
"""The built-in holidays (US)."""

WHEN_EXAMPLES = "December 25, fourth Thursday of November, last Monday of May, Easter, 2 days before Easter, June 20 2027"


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
    if when(on) is None:
        raise ValueError(f"{name}: when is “{on}”? Like {WHEN_EXAMPLES}")
    return Holiday(name, on, tuple(dict.fromkeys(a for a in (" ".join(str(x).split()) for x in also) if a)))


@dataclass(frozen=True)
class Settings(common.Settings):
    holidays: tuple[Holiday, ...] = HOLIDAYS
    names: dict[str, Holiday] = field(init=False, repr=False, compare=False)
    """Each name a holiday goes by (``key``) -> it."""

    def __post_init__(self) -> None:
        names: dict[str, Holiday] = {}
        for holiday in self.holidays:
            for name in (holiday.name, *holiday.also):
                other = names.setdefault(key(name), holiday)
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
        holidays = {key(h.name): h for h in HOLIDAYS}
        for name, value in changes.items():
            if value is False:
                holidays.pop(key(str(name)), None)
            else:
                holiday = _holiday(name, value)
                holidays[key(holiday.name)] = holiday
        return cls(tuple(holidays.values()))

    def to_json(self) -> dict[str, Any]:
        today = dt.date.today()
        built_in = {key(h.name): h for h in HOLIDAYS}
        here = {key(h.name) for h in self.holidays}

        def item(holiday: Holiday) -> dict[str, Any]:
            following = holiday.next(today)
            return holiday.to_json() | {
                "next": following.isoformat() if following else None,
                "built_in": built_in.get(key(holiday.name)) == holiday,
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
            if given.setdefault(key(holiday.name), holiday) is not holiday:
                raise ValueError(f"{holiday.name} is given twice")
        cls(tuple(holidays))  # each name one holiday's
        built_in = {key(h.name): h for h in HOLIDAYS}
        changes: dict[str, Any] = {h.name: False for k, h in built_in.items() if k not in given}
        changes |= {h.name: h.stored() for k, h in given.items() if built_in.get(k) != h}
        return {"holidays": changes} if changes else None

    def holiday(self, name: str) -> Holiday | None:
        return self.names.get(key(name))


def parse_date(s):
    s = s.strip().lower().replace(",", "")
    s = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", s)
    m = re.fullmatch(r"(?:the )?(\d{1,2}) (?:of )?([a-z]+) (\d{4})", s) or None
    if m:
        d, mo, y = int(m.group(1)), MONTHS.get(m.group(2)), int(m.group(3))
    else:
        m = re.fullmatch(r"([a-z]+) (\d{1,2}) (\d{4})", s)
        if not m:
            return None
        mo, d, y = MONTHS.get(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return dt.date(y, mo, d) if mo else None
    except ValueError:
        return None


def _day_of(text, today, settings):
    """The next date a holiday or "month day" falls on, from ``today``."""
    holiday = settings.holiday(text)
    if holiday is not None:
        return holiday.next(today)
    found = when(text)
    if found is None or found.fixed is None:
        return None
    for year in (today.year, today.year + 1):
        date = found.on(year)
        if date is not None and date >= today:
            return date
    return None


def _said(date: dt.date, today: dt.date, year: bool = False) -> str:
    """"December 25"; with its year when not this one, or ``year``."""
    said = f"{calendar.month_name[date.month]} {date.day}"
    return f"{said}, {date.year}" if year or date.year != today.year else said


def solve(q, settings=None):
    settings = settings or DEFAULT
    s = clean(q)
    if m := re.fullmatch(rf"(?:is|was|will) {N} (?:be )?a leap year", s):
        year = parse_num(m.group(1))
        if year is not None and float(year).is_integer():
            return "Yes" if leap(int(year)) else "No"
    if re.fullmatch(r"how many days (?:are )?(?:there )?in a leap year", s):
        return "366"
    if re.fullmatch(r"how many days (?:are )?(?:there )?in a (?:normal |regular |common |non-leap )?year", s):
        return "365"
    if m := re.fullmatch(rf"how many days (?:are )?(?:there )?in ([a-z]+)(?: (?:of |in )?{N})?", s):
        mo = MONTHS.get(m.group(1))
        year = parse_num(m.group(2)) if m.group(2) else None
        if mo and (year is None or 1 <= year <= 9999):
            if mo == 2 and year is None:
                return "28, or 29 in a leap year"
            return str(calendar.monthrange(int(year or 2001), mo)[1])
    if re.fullmatch(r"how many (months|weeks|days) (?:are )?(?:there )?in a (year|week)", s):
        a, b = re.fullmatch(r"how many (months|weeks|days) (?:are )?(?:there )?in a (year|week)", s).groups()
        return {("months", "year"): "12", ("weeks", "year"): "52", ("days", "week"): "7", ("days", "year"): "365"}.get((a, b))
    if m := re.fullmatch(r"(?:what|which) day (of the week )?(?:is|was|will be|(does)) (.+?)(?: fall on| be| land on)?", s):
        d = parse_date(m.group(3))
        # A holiday's weekday, next time; "what day is Christmas" is its date.
        if d is None and (m.group(1) or m.group(2)) and (holiday := settings.holiday(m.group(3))) is not None:
            d = holiday.next(dt.date.today())
        if d:
            return calendar.day_name[d.weekday()]
    if m := re.fullmatch(r"(?:what|which) (?:date|day) is (.+)|when is (.+)", s):
        holiday = settings.holiday(m.group(1) or m.group(2))
        if holiday is not None:
            found, today = holiday.date, dt.date.today()
            if found.fixed:
                return f"{calendar.month_name[found.fixed[0]]} {found.fixed[1]}"
            if found.year is not None:
                date = found.on(found.year)
                return _said(date, today, year=True) if date else None
            date = holiday.next(today)
            return _said(date, today) if date else None
    if m := re.fullmatch(r"how many (days|weeks|sleeps) (?:are )?(?:left )?(?:until|till|til|to|before) (.+)", s):
        today = dt.date.today()
        target = _day_of(m.group(2), today, settings)
        if target is not None:
            days = (target - today).days
            return f"{days // 7} weeks" if m.group(1) == "weeks" else f"{days} day{'s' if days != 1 else ''}"
    if m := re.fullmatch(r"(?:what|which) month (?:comes|is) (after|before) ([a-z]+)", s):
        mo = MONTHS.get(m.group(2))
        if mo:
            return calendar.month_name[(mo % 12) + 1 if m.group(1) == "after" else ((mo - 2) % 12) + 1]
    if m := re.fullmatch(r"what(?: is|'s) the (?:next )?day (after|before) ([a-z]+)|what day comes (after|before) ([a-z]+)", s):
        rel, day = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        if day in DAYS:
            return calendar.day_name[(DAYS.index(day) + (1 if rel == "after" else -1)) % 7]
    if m := re.fullmatch(r"what(?: is|'s) the (first|last) (month|day) of the (year|week)", s):
        first = m.group(1) == "first"
        if m.group(2) == "month" and m.group(3) == "year":
            return "January" if first else "December"
    return None


DEFAULT = Settings()


def answer(text: str, settings: Settings = DEFAULT):
    return reply(solve(text, settings), text)


SKILL = Skill(
    id="dates",
    name="Dates",
    description="Leap years, days in a month, the day of the week a date falls on, holidays, days until a date.",
    examples=("is 2024 a leap year", "what day is Christmas", "how many days until Halloween"),
    icon="mdi:calendar-month-outline",
    answer=answer,
    settings=Settings,
)
