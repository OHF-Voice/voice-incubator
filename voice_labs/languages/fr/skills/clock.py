"""How French asks times from now ("quelle heure sera-t-il dans 45 minutes",
"combien de temps jusqu'à 17 heures") and says them: on a 24-hour clock, "17
heures 5", "midi"; "dans 40 minutes" (voice_labs/skills/clock.py)."""

from __future__ import annotations

import re

from voice_labs.skills.clock import Settings, later, until
from voice_labs.skills.common import reply

from .. import reading
from ..speech import duration
from .numbers import clean, parse_num

EXAMPLES = ("quelle heure sera t il dans 45 minutes", "combien de temps jusqu'à 17 heures")

HOURS = 24

_FIXED = {"une demi heure": 30, "demi heure": 30, "un quart d'heure": 15, "quart d'heure": 15,
          "trois quarts d'heure": 45}
_AMOUNT = re.compile(r"(?P<n>.+?) (?P<unit>minutes?|heures?)(?: et (?P<part>demie|quart)| et (?P<more>.+?) minutes?| (?P<bare>\d+))?")
_ASK = r"(?:quelle heure (?:sera t il|il sera|est ce qu'il sera|est il)|il sera quelle heure)"
_IN = re.compile(rf"{_ASK} dans (?P<a>.+)|dans (?P<b>.+?),? {_ASK}")
_UNTIL = re.compile(
    r"(?:combien de temps|combien de minutes|combien d'heures)"
    r"(?: (?:reste t il|il reste|me reste t il|nous reste t il|il me reste|il nous reste|y a t il|il y a))?(?: encore)?"
    r" (?:jusqu'à|jusqu'au|avant|d'ici) (?:(?:.+ )?à )?(?P<time>.+)"
)


def minutes_in(s):
    """"45 minutes", "une heure", "une heure et demie", "une demi-heure", "2
    heures et 10 minutes" as minutes; None."""
    if s in _FIXED:
        return _FIXED[s]
    m = _AMOUNT.fullmatch(s)
    if not m:
        return None
    n = parse_num(m.group("n"))
    more = m.group("more") or m.group("bare")
    more = parse_num(more) if more else 0
    if n is None or more is None:
        return None
    part = {"demie": 30, "quart": 15}.get(m.group("part") or "", 0)
    minutes = n * (60 if m.group("unit").startswith("h") else 1) + part + more
    return minutes if minutes > 0 else None


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


def answer(text: str, settings: Settings, now):
    s = clean(text)
    if m := _IN.fullmatch(s):
        amount = m.group("a") or m.group("b")
        minutes = minutes_in(amount)
        if minutes is None:
            return None
        hour, minute = later(now, minutes)
        then = show_clock(hour * 60 + minute, settings.hours)
        return reply(then, text, f"Dans {amount}, il sera {then}", specific=f"Il sera {then}.")
    if m := _UNTIL.fullmatch(s):
        found = reading.clock(m.group("time"))
        if found is None:
            return None
        wait = until(now, *found)
        if wait == 0:
            return reply("Maintenant", text, "C'est l'heure", specific="C'est l'heure.")
        hour, minute = later(now, wait)
        then = show_clock(hour * 60 + minute, settings.hours)
        said = duration(wait * 60)
        return reply(said, text, f"{then[:1].upper()}{then[1:]}, c'est dans {said}", specific=f"Dans {said}.")
    return None
