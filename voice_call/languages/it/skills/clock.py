"""How Italian asks times from now ("che ore saranno tra 45 minuti", "quanto
manca alle 17") and says them: on a 24-hour clock, "le 17 e 5", "l'una e 5",
"mezzogiorno", as the app says one; "mancano 40 minuti"
(voice_call/skills/clock.py)."""

from __future__ import annotations

import re

from voice_call.skills.clock import Settings, later, until
from voice_call.skills.common import reply

from .. import reading
from ..speech import clock as said_clock
from ..speech import duration
from .numbers import N, clean, fold, parse_num

EXAMPLES = ("che ore saranno tra 45 minuti", "quanto manca alle 17")

HOURS = 24
"""The clock it answers in unless set: "le 16 e 45"."""


def parse_clock(s):
    """A time said ("le 15", "le tre del pomeriggio", "le sette e mezza",
    "l'una", "mezzogiorno"), with the preposition before it ("alle 15",
    "all'una", "a mezzogiorno", "fino alle 15"): (hour, minute, meridiem);
    None."""
    s = re.sub(r"^(?:fino |prima )", "", s.strip())
    s = re.sub(r"^(?:de|da|a)ll(e |')", r"l\1", s)
    s = re.sub(r"^(?:di|da|a|per) ", "", s)
    return reading.clock(s)


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


_ASK = r"(?:che or[ae] (?:sara|saranno|e|sono|fara|faranno)|che ora)"
_IN = re.compile(rf"{_ASK} (?:tra|fra) (?P<a>.+)|(?:tra|fra) (?P<b>.+?),? {_ASK}")
_UNTIL = re.compile(
    r"(?:quanto|quanto tempo|quanti minuti|quante ore) (?:ci |mi |ti )?(?:manca|mancano|resta|restano)"
    r"(?: ancora)? (?P<time>.+)"
)


def answer(text: str, settings: Settings, now):
    s = fold(clean(text))
    if m := _IN.fullmatch(s):
        amount = m.group("a") or m.group("b")
        length = minutes(amount)
        if length is None:
            return None
        hour, minute = later(now, length)
        then = show_clock(hour * 60 + minute, settings.hours)
        verb = "sarà" if then.startswith(("l'", "mezzogiorno", "mezzanotte")) else "saranno"
        return reply(then, text, f"Tra {amount} {verb} {then}", specific=f"{verb.capitalize()} {then}.")
    if m := _UNTIL.fullmatch(s):
        found = parse_clock(m.group("time"))
        if found is None:
            return None
        wait = until(now, *found)
        if wait == 0:
            return reply("Adesso", text, "Ci siamo", specific="Ci siamo.")
        hour, minute = later(now, wait)
        then = show_clock(hour * 60 + minute, settings.hours)
        said = duration(wait * 60)
        verb = "manca" if said.startswith(("un ", "un'", "una ")) else "mancano"
        at = f"al{then}" if then.startswith(("le ", "l'")) else f"a {then}"
        return reply(said, text, f"{at[:1].upper()}{at[1:]} {verb} {said}", specific=f"{verb.capitalize()} {said}.")
    return None
