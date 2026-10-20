"""How Polish asks clock arithmetic ("która godzina będzie 45 minut po
piętnastej?", "która była godzina półtorej godziny przed południem?") and
says a time: on a 24-hour clock, its hour as Polish says it, "piętnasta
czterdzieści pięć", as the app says one (voice_call/skills/clock.py)."""

from __future__ import annotations

import re

from voice_call.skills.clock import Settings
from voice_call.skills.common import reply

from .. import reading
from ..speech import clock as said_clock
from .numbers import N, clean, fold, parse_num

EXAMPLES = ("która godzina będzie 45 minut po piętnastej", "która była godzina 90 minut przed południem")

HOURS = 24
"""The clock it answers in unless set: "szesnasta czterdzieści pięć"."""


def parse_clock(s):
    """Minutes into the day of a time said ("piętnastej", "trzeciej po
    południu", "wpół do trzeciej", "15:30", "południem"); None."""
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
    """"szesnasta czterdzieści pięć", "pierwsza"; on 12 hours, "czwarta
    czterdzieści pięć po południu"."""
    h, m = divmod(mins % 1440, 60)
    if hours == 24:
        return said_clock(h, m)
    if (h, m) == (12, 0):
        return "południe"
    said = said_clock((h % 12) or 12, m)
    part = "w nocy" if h < 5 else "rano" if h < 12 else "po południu" if h < 18 else "wieczorem"
    return f"{said} {part}"


# "Która godzina będzie 45 minut po 15", "która będzie 45 minut po
# piętnastej", "ile to jest godzinę po 15", "jaka godzina była 2 godziny
# przed 10".
_ASKED = re.compile(
    r"(?:ktora (?:(?:godzina )?(?:jest|bedzie|byla|to)|godzina)(?: godzina)?|jaka (?:(?:godzina )?(?:jest|bedzie|byla)|godzina)"
    r"(?: godzina)?|ile to (?:jest|bedzie)|ile jest|o ktorej (?:jest|bedzie))"
    rf" ((?:{N} )?(minut\w*|godzin\w*|kwadrans\w*)( i pol| i kwadrans)? (po|przed) (.+))"
)


def solve(q, hours=24):
    """The time, the verb asked with ("będzie"), and what was asked ("45
    minut po piętnastej"); None."""
    s = clean(q)
    m = _ASKED.fullmatch(fold(s))
    if not m:
        return None
    said = s[m.start(1):m.end(1)]
    number, unit, extra, rel, at = m.group(2), m.group(3), m.group(4), m.group(5), m.group(6)
    if number:
        v = parse_num(number)
    else:
        v = 1.0  # "godzinę", "kwadrans"
    t = parse_clock(at)
    if v is None or t is None:
        return None
    per = 60 if unit.startswith("godzin") else 15 if unit.startswith("kwadrans") else 1
    delta = v * per + (30 if extra == " i pol" else 15 if extra else 0)
    verb = next((w for w in ("bedzie", "byla") if f" {w} " in f" {fold(s)} "), "jest")
    found = show_clock(int(t + (-delta if rel == "przed" else delta)), hours)
    return found, {"bedzie": "będzie", "byla": "była"}.get(verb, "jest"), said


def answer(text: str, settings: Settings):
    found = solve(text, settings.hours)
    if not found:
        return None
    said, verb, asked = found
    return reply(said, text, f"{asked} {verb} {said}", brief=f"{said[:1].upper()}{said[1:]}.",
                 specific=f"{verb.capitalize()} {said}.")
