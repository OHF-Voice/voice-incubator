"""How German asks times from now ("wie spät ist es in 45 Minuten", "wie lange
noch bis 17 Uhr") and says them: on a 24-hour clock, "17 Uhr 5"; "noch 40
Minuten" (voice_labs/skills/clock.py)."""

from __future__ import annotations

import re

from voice_labs.skills.clock import Settings, later, until
from voice_labs.skills.common import reply

from .. import reading
from ..speech import duration
from .numbers import clean, nouns, parse_num

EXAMPLES = ("wie spät ist es in 45 Minuten", "wie lange noch bis 17 Uhr")

HOURS = 24
"""The clock it answers in unless set: "16 Uhr 45"."""

_FIXED = {"einer halben stunde": 30, "eine halbe stunde": 30, "einer viertelstunde": 15, "eine viertelstunde": 15,
          "einer dreiviertelstunde": 45, "eine dreiviertelstunde": 45}
_AMOUNT = re.compile(r"(?P<n>.+?) (?P<unit>minuten?|stunden?)(?: und (?P<more>.+?) minuten?)?")
_IN = re.compile(
    r"(?:(?:wie spät|wie viel uhr|wieviel uhr|welche uhrzeit) (?:ist es|wird es sein|wird es|haben wir)(?: dann)?"
    r" in (?P<a>.+?)(?: sein)?|in (?P<b>.+?),? (?:wie spät|wie viel uhr|wieviel uhr) (?:ist es|wird es sein|wird es)(?: dann)?)"
)
_UNTIL = re.compile(
    r"(?:wie lange|wie viel zeit|wieviel zeit|wie viele minuten|wie viele stunden)"
    r"(?: (?:ist es|dauert es|habe ich|haben wir|bleibt|bleiben|sind es))?(?: noch)? bis (?:zum |zur )?(?:(?:.+ )?um )?(?P<time>.+)"
)


def minutes_in(s):
    """"45 Minuten", "einer Stunde", "anderthalb Stunden", "einer halben
    Stunde", "2 Stunden und 10 Minuten" as minutes; None."""
    if s in _FIXED:
        return _FIXED[s]
    m = _AMOUNT.fullmatch(s)
    if not m:
        return None
    n = parse_num(m.group("n"))
    more = parse_num(m.group("more")) if m.group("more") else 0
    if n is None or more is None:
        return None
    minutes = n * (60 if m.group("unit").startswith("stunde") else 1) + more
    return minutes if minutes > 0 else None


def show_clock(mins, hours=24):
    """"16 Uhr 45", "16 Uhr", "Mitternacht"; on 12 hours, "4 Uhr 45 nachmittags"."""
    h, m = divmod(mins % 1440, 60)
    if (h, m) == (0, 0):
        return "Mitternacht"
    said = f"{h if hours == 24 else (h % 12) or 12} Uhr" + (f" {m}" if m else "")
    if hours == 12:
        said += " vormittags" if h < 12 else (" nachmittags" if h < 18 else " abends")
    return said


def answer(text: str, settings: Settings, now):
    s = clean(text)
    if m := _IN.fullmatch(s):
        amount = m.group("a") or m.group("b")
        minutes = minutes_in(amount)
        if minutes is None:
            return None
        hour, minute = later(now, minutes)
        then = show_clock(hour * 60 + minute, settings.hours)
        return reply(then, text, f"In {nouns(amount)} ist es {then}", specific=f"Dann ist es {then}.")
    if m := _UNTIL.fullmatch(s):
        found = reading.clock(m.group("time"))
        if found is None:
            return None
        wait = until(now, *found)
        if wait == 0:
            return reply("Jetzt", text, "Es ist jetzt so weit", specific="Es ist jetzt so weit.")
        hour, minute = later(now, wait)
        said = duration(wait * 60)
        return reply(said, text, f"Bis {show_clock(hour * 60 + minute, settings.hours)} sind es noch {said}",
                     specific=f"Noch {said}.")
    return None
