"""How Dutch asks clock arithmetic ("wat is 45 minuten na 15 uur", "hoe laat
is het 90 minuten voor 12 uur") and says a time: on a 24-hour clock, "15 uur
45" (voice_call/skills/clock.py)."""

from __future__ import annotations

import re

from voice_call.skills.clock import Settings
from voice_call.skills.common import reply

from .. import reading
from .numbers import N, clean, parse_num

EXAMPLES = ("wat is 45 minuten na 15 uur", "hoe laat is het 90 minuten voor 12 uur")

HOURS = 24


def parse_clock(s):
    """Minutes into the day of a time said ("15 uur", "drie uur 's middags",
    "half drie", "de middag"); None."""
    if s.strip() in ("middag", "de middag", "het middaguur"):
        return 12 * 60
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
    """"16 uur 45", "16 uur"; on 12 hours, "4 uur 45 's middags"."""
    h, m = divmod(mins % 1440, 60)
    said = f"{h if hours == 24 else (h % 12) or 12} uur" + (f" {m}" if m else "")
    if hours == 12:
        said += " 's ochtends" if h < 12 else (" 's middags" if h < 18 else " 's avonds")
    return said


def solve(q, hours=24):
    s = clean(q)
    m = re.fullmatch(
        rf"(?:wat is|hoe laat is het|hoe laat is|welke tijd is het|welke tijd is)"
        rf" ({N} (minuten|minuut|uur|uren) (voor|na|over) (.+))",
        s,
    )
    if not m:
        return None
    v, unit, rel, t = parse_num(m.group(2)), m.group(3), m.group(4), parse_clock(m.group(5))
    if v is None or t is None:
        return None
    delta = v * (60 if unit.startswith("u") else 1)
    return show_clock(int(t + (-delta if rel == "voor" else delta)), hours), m.group(1)


def answer(text: str, settings: Settings):
    found = solve(text, settings.hours)
    if not found:
        return None
    return reply(found[0], text, f"{found[1]} is {found[0]}", specific=f"Dat is {found[0]}.")
