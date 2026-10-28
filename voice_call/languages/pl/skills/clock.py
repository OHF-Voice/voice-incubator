"""How Polish asks times from now ("która godzina będzie za 45 minut?", "ile
jeszcze do siedemnastej?") and says them: on a 24-hour clock, its hour as
Polish says it, "siedemnasta pięć", as the app says one; "jeszcze 40 minut"
(voice_call/skills/clock.py)."""

from __future__ import annotations

import re

from voice_call.skills.clock import Settings, later, until
from voice_call.skills.common import reply

from .. import reading
from ..speech import clock as said_clock
from ..speech import duration
from .numbers import clean, fold, parse_num

EXAMPLES = ("która godzina będzie za 45 minut", "ile jeszcze do siedemnastej")

HOURS = 24
"""The clock it answers in unless set: "szesnasta czterdzieści pięć"."""

# Read without diacritics ("bedzie"), the words taken from the question as said.
_FIXED = {"pol godziny": 30, "kwadrans": 15, "trzy kwadranse": 45}
_AMOUNT = re.compile(r"(?:(?P<n>.+?) )?(?P<unit>minut\w*|godzin\w*|kwadrans\w*)(?: i (?P<part>pol|kwadrans)| i (?P<more>.+?) minut\w*)?")
_ASK = r"(?:(?:ktora|jaka) (?:godzina )?bedzie(?: godzina)?)"
_IN = re.compile(rf"{_ASK} za (?P<a>.+)|za (?P<b>.+?),? {_ASK}")
_UNTIL = re.compile(
    r"(?:ile|ile czasu|ile minut|ile godzin|jak dlugo)(?: (?:jeszcze|zostalo|pozostalo|czasu|mamy|mam))*"
    r" do (?P<time>.+)"
)


def minutes_in(s, folded):
    """"45 minut", "godzinę", "półtorej godziny", "pół godziny", "2 godziny i
    10 minut" as minutes (``folded`` read, ``s`` its numbers); None."""
    if folded in _FIXED:
        return _FIXED[folded]
    m = _AMOUNT.fullmatch(folded)
    if not m:
        return None
    n = parse_num(s[m.start("n"):m.end("n")]) if m.group("n") else 1.0  # "godzinę"
    more = parse_num(s[m.start("more"):m.end("more")]) if m.group("more") else 0
    if n is None or more is None:
        return None
    unit = m.group("unit")
    per = 60 if unit.startswith("godzin") else 15 if unit.startswith("kwadrans") else 1
    minutes = n * per + {"pol": 30, "kwadrans": 15}.get(m.group("part") or "", 0) + more
    return minutes if minutes > 0 else None


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


def answer(text: str, settings: Settings, now):
    s = clean(text)
    folded = fold(s)
    if m := _IN.fullmatch(folded):
        part = "a" if m.group("a") else "b"
        amount = s[m.start(part):m.end(part)]
        minutes = minutes_in(amount, m.group(part))
        if minutes is None:
            return None
        hour, minute = later(now, minutes)
        then = show_clock(hour * 60 + minute, settings.hours)
        return reply(then, text, f"Za {amount} będzie {then}", specific=f"Będzie {then}.")
    if m := _UNTIL.fullmatch(folded):
        time = s[m.start("time"):m.end("time")]
        found = reading.clock(time)
        if found is None:
            return None
        wait = until(now, *found)
        if wait == 0:
            return reply("Teraz", text, "To już teraz", specific="To już teraz.")
        said = duration(wait * 60)
        return reply(said, text, f"Do {time} jeszcze {said}", specific=f"Jeszcze {said}.")
    return None
