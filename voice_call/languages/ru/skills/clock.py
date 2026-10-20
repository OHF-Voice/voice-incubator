"""How Russian asks clock arithmetic ("который час будет через 45 минут после
15:00?", "сколько времени будет за полтора часа до полудня?") and says a
time: on a 24-hour clock, "16:30", as the app says one
(voice_call/skills/clock.py).

"Через ... после" is after, "за ... до" before; the time it is from is read
as an alarm's ("трёх часов дня", "полудня", "половины восьмого")."""

from __future__ import annotations

import re

from voice_call.skills.clock import Settings
from voice_call.skills.common import reply

from .. import reading
from ..speech import clock as said_clock
from .numbers import N, clean, parse_num

EXAMPLES = ("который час будет через 45 минут после 15:00", "сколько времени будет за 90 минут до полудня")

HOURS = 24
"""The clock it answers in unless set: "16:45"."""

_AMOUNT = (
    rf"(?:(?P<n>{N}) )?(?P<unit>минут[уы]?|час(?:а|ов)?|полчаса|полтора часа|полторы минуты|четверть часа)"
    rf"(?: с половиной)?"
)
_ASKED = re.compile(
    rf"(?:который (?:сейчас )?час|сколько (?:сейчас )?времени|какое (?:сейчас )?время|во сколько|что за время)"
    rf"(?: будет| было| получится)? (?P<asked>(?P<rel>через|за) {_AMOUNT} (?P<dir>после|до) (?P<from>.+))"
)


def parse_clock(s):
    """Minutes into the day of a time said ("15:00", "трёх часов дня",
    "полудня", "половины восьмого"); None."""
    found = reading.clock(s)
    if found is None:
        return None
    hour, minute, meridiem = found
    if meridiem == "pm" and hour < 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    return hour * 60 + minute


def show_clock(mins, hours=24):
    """"16:45", "полночь"; on 12 hours, "4:45 дня"."""
    h, m = divmod(mins % 1440, 60)
    if hours == 24 or (h, m) in ((0, 0), (12, 0)):
        return said_clock(h, m)
    part = "ночи" if h < 4 else "утра" if h < 12 else "дня" if h < 17 else "вечера"
    return f"{said_clock((h % 12) or 12, m)} {part}"


def _minutes(amount: str | None, unit: str, half: bool) -> float | None:
    """How many minutes "45 минут", "полтора часа", "2 часа с половиной"
    are."""
    if unit == "полчаса":
        return 30
    if unit == "полтора часа":
        return 90
    if unit == "полторы минуты":
        return 1.5
    if unit == "четверть часа":
        return 15
    value = parse_num(amount) if amount else 1.0  # "через час"
    if value is None:
        return None
    value += 0.5 if half else 0
    return value * (60 if unit.startswith("час") else 1)


def solve(q, hours=24):
    """The time, and what was asked ("через 45 минут после 15:00"); None."""
    s = clean(q)
    m = _ASKED.fullmatch(s)
    if not m:
        return None
    if (m.group("rel") == "через") != (m.group("dir") == "после"):
        return None  # "через ... до", "за ... после": not said
    delta = _minutes(m.group("n"), m.group("unit"), " с половиной" in m.group("asked"))
    start = parse_clock(m.group("from"))
    if delta is None or start is None:
        return None
    sign = 1 if m.group("dir") == "после" else -1
    return show_clock(int(start + sign * delta), hours), m.group("asked")


def answer(text: str, settings: Settings):
    found = solve(text, settings.hours)
    if not found:
        return None
    said, asked = found
    return reply(said, text, f"{asked} будет {said}", brief=f"{said[:1].upper()}{said[1:]}.",
                 specific=f"Будет {said}.")
