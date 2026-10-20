"""How French asks clock arithmetic ("quelle heure est-il 45 minutes après 15
heures", "quelle heure sera-t-il 90 minutes avant midi") and says a time: on
a 24-hour clock, "15 heures 45", "midi" (voice_call/skills/clock.py)."""

from __future__ import annotations

import re

from voice_call.skills.clock import Settings
from voice_call.skills.common import reply

from .. import reading
from .numbers import N, clean, parse_num

EXAMPLES = ("quelle heure est il 45 minutes après 15 heures", "quelle heure sera t il 90 minutes avant midi")

HOURS = 24


def parse_clock(s):
    """Minutes into the day of a time said ("15 heures", "trois heures de
    l'après-midi", "sept heures et demie", "midi"); None."""
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
    """"16 heures 45", "midi", "minuit"; on 12 hours, "4 heures 45 de
    l'après-midi"."""
    h, m = divmod(mins % 1440, 60)
    if h in (0, 12):
        return ("minuit" if h == 0 else "midi") + (f" {m}" if m else "")
    shown = h if hours == 24 else h % 12
    said = f"{shown} {'heure' if shown == 1 else 'heures'}" + (f" {m}" if m else "")
    if hours == 12:
        said += " du matin" if h < 12 else (" de l'après-midi" if h < 18 else " du soir")
    return said


def solve(q, hours=24):
    s = clean(q)
    m = re.fullmatch(
        rf"(?:quelle heure (?:est il|sera t il|était il|il sera|il était)|il sera quelle heure|il était quelle heure"
        rf"|qu'est ce que|c'est quoi|combien font|combien fait)"
        rf" ({N} (minutes|minute|heures|heure) (avant|après) (.+))",
        s,
    )
    if not m:
        return None
    v, unit, rel, t = parse_num(m.group(2)), m.group(3), m.group(4), parse_clock(m.group(5))
    if v is None or t is None:
        return None
    delta = v * (60 if unit.startswith("h") else 1)
    return show_clock(int(t + (-delta if rel == "avant" else delta)), hours), m.group(1)


def answer(text: str, settings: Settings):
    found = solve(text, settings.hours)
    if not found:
        return None
    return reply(found[0], text, f"{found[1]}, ça donne {found[0]}", specific=f"Ça donne {found[0]}.")
