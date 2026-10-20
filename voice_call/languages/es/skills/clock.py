"""How Spanish asks clock arithmetic ("¿qué hora es 45 minutos después de las
15?", "¿qué hora será hora y media antes del mediodía?") and says a time: on a
24-hour clock, "las 15 y 45", as the app says one (voice_call/skills/clock.py)."""

from __future__ import annotations

import re

from voice_call.skills.clock import Settings
from voice_call.skills.common import reply

from .. import reading
from ..speech import clock as said_clock
from .numbers import N, clean, loose, parse_num

EXAMPLES = ("qué hora es 45 minutos después de las 15", "qué hora será 90 minutos antes del mediodía")

HOURS = 24
"""The clock it answers in unless set: "las 16 y 45"."""


def parse_clock(s):
    """Minutes into the day of a time said ("las 15", "las tres de la tarde",
    "las dos y media", "mediodía"); None."""
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
    """"las 16 y 45", "la una"; on 12 hours, "las 4 y 45 de la tarde"."""
    h, m = divmod(mins % 1440, 60)
    if hours == 24:
        return said_clock(h, m)
    if (h, m) == (12, 0):
        return "mediodía"
    said = said_clock((h % 12) or 12, m)
    part = "de la madrugada" if 1 <= h < 6 else "de la mañana" if 6 <= h < 12 else "de la tarde" if 12 <= h < 21 else "de la noche"
    return f"{said} {part}"


def solve(q, hours=24):
    """The time, and what was asked ("45 minutos después de las 15"); None."""
    s = clean(q)
    m = re.fullmatch(
        loose(
            rf"(?:qué hora (?:es|será|era|sería)|a qué hora (?:es|será)|qué hora|cuál es la hora)"
            rf" ((?:{N} )?(minutos?|horas?)( y media| y cuarto)? (después|antes) (?:de |del )(.+))"
        ),
        s,
    )
    if not m:
        return None
    v = parse_num(m.group(2)) if m.group(2) else 1.0  # "hora y media"
    unit, extra, rel, t = m.group(3), m.group(4), m.group(5), parse_clock(m.group(6))
    if v is None or t is None:
        return None
    delta = v * (60 if unit.startswith("hora") else 1) + (30 if extra == " y media" else 15 if extra else 0)
    return show_clock(int(t + (-delta if rel.startswith("a") else delta)), hours), m.group(1)


def answer(text: str, settings: Settings):
    found = solve(text, settings.hours)
    if not found:
        return None
    said, asked = found
    verb = "es" if said.startswith(("la ", "mediodía", "medianoche")) else "son"
    return reply(said, text, f"{asked} {verb} {said}", brief=f"{said[:1].upper()}{said[1:]}.",
                 specific=f"{verb.capitalize()} {said}.")
