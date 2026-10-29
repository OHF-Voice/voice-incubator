"""How Russian asks times from now ("который час будет через 45 минут?",
"сколько ещё до пяти часов?") and says them: on a 24-hour clock, "17:05", as
the app says one; "ещё 40 минут" (voice_labs/skills/clock.py). The time it is
until is read as an alarm's ("пяти часов вечера", "полуночи", "половины
восьмого")."""

from __future__ import annotations

import re

from voice_labs.skills.clock import Settings, later, until
from voice_labs.skills.common import reply

from .. import reading
from ..speech import clock as said_clock
from ..speech import duration
from .numbers import N, clean, parse_num

EXAMPLES = ("который час будет через 45 минут", "сколько ещё до пяти часов")

HOURS = 24
"""The clock it answers in unless set: "16:45"."""

_AMOUNT = re.compile(
    rf"(?:(?P<n>{N}) )?(?P<unit>минут[уы]?|час(?:а|ов)?|полчаса|полтора часа|полторы минуты|четверть часа)"
    rf"(?P<half> с половиной)?(?: и (?P<more>{N}) минут[уы]?)?"
)
_ASK = r"(?:который (?:сейчас )?час|сколько (?:сейчас )?времени|какое (?:сейчас )?время)(?: будет)?"
_IN = re.compile(rf"{_ASK} через (?P<a>.+)|через (?P<b>.+?),? {_ASK}")
_UNTIL = re.compile(
    r"сколько(?: (?:ещё|еще|осталось|остаётся|остается|времени|минут|часов|у меня|у нас))* до (?P<time>.+)"
)


def show_clock(mins, hours=24):
    """"16:45", "полночь"; on 12 hours, "4:45 дня"."""
    h, m = divmod(mins % 1440, 60)
    if hours == 24 or (h, m) in ((0, 0), (12, 0)):
        return said_clock(h, m)
    part = "ночи" if h < 4 else "утра" if h < 12 else "дня" if h < 17 else "вечера"
    return f"{said_clock((h % 12) or 12, m)} {part}"


def minutes_in(s):
    """"45 минут", "час", "полтора часа", "2 часа с половиной", "час и 10
    минут" as minutes; None."""
    m = _AMOUNT.fullmatch(s)
    if not m:
        return None
    unit = m.group("unit")
    fixed = {"полчаса": 30, "полтора часа": 90, "полторы минуты": 1.5, "четверть часа": 15}.get(unit)
    if fixed is not None:
        return fixed
    value = parse_num(m.group("n")) if m.group("n") else 1.0  # "через час"
    more = parse_num(m.group("more")) if m.group("more") else 0
    if value is None or more is None:
        return None
    value += 0.5 if m.group("half") else 0
    minutes = value * (60 if unit.startswith("час") else 1) + more
    return minutes if minutes > 0 else None


def answer(text: str, settings: Settings, now):
    s = clean(text)
    if m := _IN.fullmatch(s):
        amount = m.group("a") or m.group("b")
        minutes = minutes_in(amount)
        if minutes is None:
            return None
        hour, minute = later(now, minutes)
        then = show_clock(hour * 60 + minute, settings.hours)
        return reply(then, text, f"Через {amount} будет {then}", specific=f"Будет {then}.")
    if m := _UNTIL.fullmatch(s):
        found = reading.clock(m.group("time"))
        if found is None:
            return None
        wait = until(now, *found)
        if wait == 0:
            return reply("Сейчас", text, "Это уже сейчас", specific="Это уже сейчас.")
        said = duration(wait * 60)
        return reply(said, text, f"До {m.group('time')} ещё {said}", specific=f"Ещё {said}.")
    return None
