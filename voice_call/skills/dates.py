"""Dates: "is 2024 a leap year", "what day is Christmas", "how many days in
February", "what day of the week is July 4 2026", "how many days until
Halloween"."""

from __future__ import annotations

import calendar
import datetime as dt
import re

from .common import N, Skill, clean, parse_num, reply

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
DAYS = [d.lower() for d in calendar.day_name]
FIXED_HOLIDAYS = {"christmas": (12, 25), "christmas day": (12, 25), "christmas eve": (12, 24), "new year's day": (1, 1),
                  "new years day": (1, 1), "new year's eve": (12, 31), "new years eve": (12, 31), "halloween": (10, 31),
                  "valentine's day": (2, 14), "valentines day": (2, 14), "independence day": (7, 4), "the fourth of july": (7, 4),
                  "st. patrick's day": (3, 17), "saint patrick's day": (3, 17), "st patrick's day": (3, 17),
                  "veterans day": (11, 11), "juneteenth": (6, 19), "april fools' day": (4, 1), "april fools day": (4, 1)}


def leap(y):
    return calendar.isleap(y)


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


def _day_of(text, today):
    """The next date a holiday or "month day" falls on, from ``today``."""
    text = re.sub(r"^the ", "", text.strip().lower()) if not text.strip().lower().startswith("the fourth") else text.strip().lower()
    text = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", text)
    found = FIXED_HOLIDAYS.get(text)
    if found is None:
        m = re.fullmatch(r"([a-z]+) (\d{1,2})", text) or re.fullmatch(r"(\d{1,2}) (?:of )?([a-z]+)", text)
        if not m:
            return None
        month, day = (m.group(1), m.group(2)) if m.group(1).isalpha() else (m.group(2), m.group(1))
        if month not in MONTHS:
            return None
        found = (MONTHS[month], int(day))
    for year in (today.year, today.year + 1):
        try:
            date = dt.date(year, *found)
        except ValueError:
            return None
        if date >= today:
            return date
    return None


def solve(q):
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
    if m := re.fullmatch(r"(?:what|which) day (?:of the week )?(?:is|was|will be|does) (.+?)(?: fall on| be| land on)?", s):
        d = parse_date(m.group(1))
        if d:
            return calendar.day_name[d.weekday()]
    if m := re.fullmatch(r"(?:what|which) (?:date|day) is (.+)|when is (.+)", s):
        h = (m.group(1) or m.group(2)).strip()
        h = re.sub(r"^the ", "", h) if h.startswith("the fourth") is False else h
        if h in FIXED_HOLIDAYS:
            mo, d = FIXED_HOLIDAYS[h]
            return f"{calendar.month_name[mo]} {d}"
    if m := re.fullmatch(r"how many (days|weeks|sleeps) (?:are )?(?:left )?(?:until|till|til|to|before) (.+)", s):
        target = _day_of(m.group(2), dt.date.today())
        if target is not None:
            days = (target - dt.date.today()).days
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


def answer(text: str):
    return reply(solve(text), text)


SKILL = Skill(
    id="dates",
    name="Dates",
    description="Leap years, days in a month, the day of the week a date falls on, holidays, days until a date.",
    examples=("is 2024 a leap year", "what day is Christmas", "how many days until Halloween"),
    icon="mdi:calendar-month-outline",
    answer=answer,
)
