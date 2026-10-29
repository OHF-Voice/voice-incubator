"""How Spanish asks times from now ("¿qué hora será dentro de 45 minutos?",
"¿cuánto falta para las 17?") and says them: on a 24-hour clock, "las 17 y
5", as the app says one; "faltan 40 minutos" (voice_labs/skills/clock.py)."""

from __future__ import annotations

import re

from voice_labs.skills.clock import Settings, later, until
from voice_labs.skills.common import reply

from .. import reading
from ..speech import clock as said_clock
from ..speech import duration
from .numbers import clean, loose, parse_num

EXAMPLES = ("qué hora será dentro de 45 minutos", "cuánto falta para las 17")

HOURS = 24
"""The clock it answers in unless set: "las 16 y 45"."""

_FIXED = {"media hora": 30, "hora y media": 90, "hora y cuarto": 75, "un cuarto de hora": 15, "cuarto de hora": 15,
          "tres cuartos de hora": 45}
_AMOUNT = re.compile(r"(?P<n>.+?) (?P<unit>minutos?|horas?)(?: y (?P<part>media|cuarto)| y (?P<more>.+?) minutos?)?")
_ASK = r"(?:qué hora (?:será|es|va a ser|sería))"
_IN = re.compile(loose(rf"{_ASK} (?:dentro de|en|de aquí a) (?P<a>.+)|(?:dentro de|en) (?P<b>.+?),? {_ASK}"))
_UNTIL = re.compile(loose(
    r"(?:cuánto|cuánto tiempo|cuántos minutos|cuántas horas) (?:me |nos |te )?(?:falta|faltan|queda|quedan)"
    r"(?: todavía| aún)? (?:para|hasta) (?:que sean )?(?:(?:.+ )?a )?(?:el )?(?P<time>.+)"
))


def minutes_in(s):
    """"45 minutos", "una hora", "una hora y media", "media hora", "2 horas y
    10 minutos" as minutes; None."""
    if s in _FIXED:
        return _FIXED[s]
    m = _AMOUNT.fullmatch(s)
    if not m:
        return None
    n = parse_num(m.group("n"))
    more = parse_num(m.group("more")) if m.group("more") else 0
    if n is None or more is None:
        return None
    part = {"media": 30, "cuarto": 15}.get(m.group("part") or "", 0)
    minutes = n * (60 if m.group("unit").startswith("hora") else 1) + part + more
    return minutes if minutes > 0 else None


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


def answer(text: str, settings: Settings, now):
    s = clean(text)
    if m := _IN.fullmatch(s):
        amount = m.group("a") or m.group("b")
        minutes = minutes_in(amount)
        if minutes is None:
            return None
        hour, minute = later(now, minutes)
        then = show_clock(hour * 60 + minute, settings.hours)
        verb = "será" if then.startswith(("la ", "mediodía", "medianoche")) else "serán"
        return reply(then, text, f"Dentro de {amount} {verb} {then}", specific=f"{verb.capitalize()} {then}.")
    if m := _UNTIL.fullmatch(s):
        found = reading.clock(m.group("time"))
        if found is None:
            return None
        wait = until(now, *found)
        if wait == 0:
            return reply("Ya", text, "Ya es la hora", specific="Ya es la hora.")
        hour, minute = later(now, wait)
        then = show_clock(hour * 60 + minute, settings.hours)
        said = duration(wait * 60)
        verb = "falta" if said.startswith(("un ", "una ")) else "faltan"
        at = f"el {then}" if then.startswith("mediodía") else f"la {then}" if then.startswith("medianoche") else then
        return reply(said, text, f"Para {at} {verb} {said}", specific=f"{verb.capitalize()} {said}.")
    return None
