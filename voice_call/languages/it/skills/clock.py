"""How Italian asks clock arithmetic ("che ore sono 45 minuti dopo le 15",
"che ora sarà un'ora e mezza prima di mezzogiorno") and says a time: on a
24-hour clock, "le 16 e 45", "l'una e 5", "mezzogiorno", as the app says one
(voice_call/skills/clock.py)."""

from __future__ import annotations

import re

from voice_call.skills.clock import Settings
from voice_call.skills.common import reply

from .. import reading
from ..speech import clock as said_clock
from .numbers import N, clean, fold, parse_num

EXAMPLES = ("che ore sono 45 minuti dopo le 15", "che ora sarà 90 minuti prima di mezzogiorno")

HOURS = 24
"""The clock it answers in unless set: "le 16 e 45"."""


def parse_clock(s):
    """Minutes into the day of a time said ("le 15", "le tre del pomeriggio",
    "le sette e mezza", "l'una", "mezzogiorno"), with the preposition before
    it ("delle 15", "dell'una", "di mezzogiorno"); None."""
    s = re.sub(r"^(?:de|da|a)ll(e |')", r"l\1", s.strip())
    s = re.sub(r"^(?:di|da|a) ", "", s)
    found = reading.clock(s)
    if found is None:
        return None
    hour, minute, meridiem = found
    if meridiem == "pm" and hour < 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    return hour * 60 + minute


_PARTS = {"mezzo": 30, "mezza": 30, "un quarto": 15, "tre quarti": 45}


def minutes(s):
    """A length of time said: "45 minuti", "un'ora e mezza", "2 ore e 10
    minuti", "mezz'ora", "un quarto d'ora"; None."""
    if s in ("mezz'ora", "mezzora", "mezza ora"):
        return 30
    if m := re.fullmatch(rf"(un quarto|tre quarti|{N} quarti) d'ora", s):
        count = parse_num(m.group(2)) if m.group(2) else (1 if m.group(1) == "un quarto" else 3)
        return None if count is None else count * 15
    if m := re.fullmatch(rf"{N} minut[oi]", s):
        return parse_num(m.group(1))
    if m := re.fullmatch(rf"(?:un'|{N} )?or[ae](?: e (mezzo|mezza|un quarto|tre quarti|{N}(?: minut[oi])?))?", s):
        hours = parse_num(m.group(1)) if m.group(1) else 1.0
        if hours is None:
            return None
        if m.group(2) is None:
            return hours * 60
        extra = _PARTS.get(m.group(2))
        extra = parse_num(m.group(3)) if extra is None else extra
        return None if extra is None else hours * 60 + extra
    return None


def show_clock(mins, hours=24):
    """"le 16 e 45", "l'una", "mezzogiorno"; on 12 hours, "le 4 e 45 del
    pomeriggio"."""
    h, m = divmod(mins % 1440, 60)
    if hours == 24:
        return f"mezzanotte e {m}" if h == 0 and m else said_clock(h, m)
    if (h, m) in ((12, 0), (0, 0)):
        return said_clock(h, m)
    part = "di notte" if h < 6 else "di mattina" if h < 12 else "del pomeriggio" if h < 18 else "di sera"
    return f"{said_clock((h % 12) or 12, m)} {part}"


_ASK = (
    r"(?:che or[ae] (?:e|sono|sara|saranno|era|erano|sarebbe|sarebbero|fa|fanno|fara|faranno)|a che ora (?:e|sara)"
    r"|che or[ae]|quale ora|quanto fa|cosa fa)"
)


def solve(q, hours=24):
    """The time, and what was asked ("45 minuti dopo le 15"); None."""
    s = fold(clean(q))
    m = re.fullmatch(rf"{_ASK} ((.+?) (dopo|prima) (.+))", s)
    if not m:
        return None
    length, t = minutes(m.group(2)), parse_clock(m.group(4))
    if length is None or t is None:
        return None
    return show_clock(int(t + (-length if m.group(3) == "prima" else length)), hours), m.group(1)


def answer(text: str, settings: Settings):
    found = solve(text, settings.hours)
    if not found:
        return None
    said, asked = found
    verb = "è" if said.startswith(("l'", "mezzogiorno", "mezzanotte")) else "sono"
    return reply(said, text, f"{asked} {verb} {said}", brief=f"{said[:1].upper()}{said[1:]}.",
                 specific=f"{verb.capitalize()} {said}.")
