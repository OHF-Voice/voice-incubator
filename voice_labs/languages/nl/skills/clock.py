"""How Dutch asks times from now ("hoe laat is het over 45 minuten", "hoe lang
nog tot 17 uur") and says them: on a 24-hour clock, "17 uur 5"; "nog 40
minuten" (voice_labs/skills/clock.py)."""

from __future__ import annotations

import re

from voice_labs.skills.clock import Settings, later, until
from voice_labs.skills.common import reply

from .. import reading
from ..speech import duration
from .numbers import clean, parse_num

EXAMPLES = ("hoe laat is het over 45 minuten", "hoe lang nog tot 17 uur")

HOURS = 24

_FIXED = {"een half uur": 30, "een halfuur": 30, "half uur": 30, "een kwartier": 15, "kwartier": 15,
          "drie kwartier": 45}
_AMOUNT = re.compile(r"(?P<n>.+?) (?P<unit>minuten|minuut|uur|uren)(?: en (?P<more>.+?) minuten?)?")
_IN = re.compile(
    r"(?:(?:hoe laat|welke tijd) (?:is het|zal het zijn|wordt het)(?: dan)? over (?P<a>.+)"
    r"|over (?P<b>.+?),? hoe laat (?:is het|zal het zijn|wordt het))"
)
_UNTIL = re.compile(
    r"(?:hoe lang|hoeveel tijd|hoeveel minuten|hoeveel uur)"
    r"(?: (?:is het|duurt het|hebben we|heb ik))?(?: nog)? (?:tot|totdat) (?:(?:.+ )?om )?(?P<time>.+)"
)
_NOON = ("middag", "de middag", "het middaguur", "12 uur 's middags")


def minutes_in(s):
    """"45 minuten", "een uur", "anderhalf uur", "een half uur", "2 uur en 10
    minuten" as minutes; None."""
    if s in _FIXED:
        return _FIXED[s]
    m = _AMOUNT.fullmatch(s)
    if not m:
        return None
    n = parse_num(m.group("n"))
    more = parse_num(m.group("more")) if m.group("more") else 0
    if n is None or more is None:
        return None
    minutes = n * (60 if m.group("unit").startswith("u") else 1) + more
    return minutes if minutes > 0 else None


def show_clock(mins, hours=24):
    """"16 uur 45", "16 uur", "middernacht"; on 12 hours, "4 uur 45 's middags"."""
    h, m = divmod(mins % 1440, 60)
    if (h, m) == (0, 0):
        return "middernacht"
    said = f"{h if hours == 24 else (h % 12) or 12} uur" + (f" {m}" if m else "")
    if hours == 12:
        said += " 's ochtends" if h < 12 else (" 's middags" if h < 18 else " 's avonds")
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
        return reply(then, text, f"Over {amount} is het {then}", specific=f"Dan is het {then}.")
    if m := _UNTIL.fullmatch(s):
        time = m.group("time").strip()
        found = (12, 0, "pm") if time in _NOON else reading.clock(time)
        if found is None:
            return None
        wait = until(now, *found)
        if wait == 0:
            return reply("Nu", text, "Het is nu zover", specific="Het is nu zover.")
        hour, minute = later(now, wait)
        said = duration(wait * 60)
        return reply(said, text, f"Tot {show_clock(hour * 60 + minute, settings.hours)} is het nog {said}",
                     specific=f"Nog {said}.")
    return None
