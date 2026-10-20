"""How German asks clock arithmetic ("was ist 45 Minuten nach 15 Uhr", "wie
spät ist es 90 Minuten vor Mittag") and says a time: on a 24-hour clock,
"15 Uhr 45" (voice_call/skills/clock.py)."""

from __future__ import annotations

import re

from voice_call.skills.clock import Settings
from voice_call.skills.common import reply

from .. import reading
from .numbers import N, clean, nouns, parse_num

EXAMPLES = ("was ist 45 Minuten nach 15 Uhr", "wie spät ist es 90 Minuten vor Mittag")

HOURS = 24
"""The clock it answers in unless set: "16 Uhr 45"."""


def parse_clock(s):
    """Minutes into the day of a time said ("15 Uhr", "drei Uhr nachmittags",
    "halb drei", "Mittag"); None."""
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
    """"16 Uhr 45", "16 Uhr"; on 12 hours, "4 Uhr 45 nachmittags"."""
    h, m = divmod(mins % 1440, 60)
    said = f"{h if hours == 24 else (h % 12) or 12} Uhr" + (f" {m}" if m else "")
    if hours == 12:
        said += " vormittags" if h < 12 else (" nachmittags" if h < 18 else " abends")
    return said


def solve(q, hours=24):
    """The time, and what was asked ("45 Minuten nach 15 Uhr"); None."""
    s = clean(q)
    m = re.fullmatch(
        rf"(?:was ist|wie spät ist es|wie spät ist|wie viel uhr ist es|welche uhrzeit ist|wann ist)"
        rf" ({N} (minuten?|stunden?) (vor|nach) (.+))",
        s,
    )
    if not m:
        return None
    v, unit, rel, t = parse_num(m.group(2)), m.group(3), m.group(4), parse_clock(m.group(5))
    if v is None or t is None:
        return None
    delta = v * (60 if unit.startswith("stunde") else 1)
    return show_clock(int(t + (-delta if rel == "vor" else delta)), hours), m.group(1)


def answer(text: str, settings: Settings):
    found = solve(text, settings.hours)
    if not found:
        return None
    return reply(found[0], text, f"{nouns(found[1])} ist {found[0]}", specific=f"Das ist {found[0]}.")
