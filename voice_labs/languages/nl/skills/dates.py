"""How Dutch reads a date ("25 december", "25-12-2026", "tweede zondag in
mei", "2 dagen voor Pasen"), asks about dates ("is 2024 een schrikkeljaar",
"wanneer is Pasen", "hoeveel dagen nog tot kerst") and says them; and the
holidays it knows (the Netherlands'; voice_labs/skills/dates.py). A
holiday's date may also be written as English writes it ("December 25")."""

from __future__ import annotations

import calendar
import datetime as dt
import re

from voice_labs.languages.en.skills import dates as english
from voice_labs.skills import dates as shared
from voice_labs.skills.common import reply
from voice_labs.skills.dates import Holiday, Settings, When, easter, leap

from .numbers import N, clean, parse_num

EXAMPLES = ("is 2024 een schrikkeljaar", "wanneer is Pasen", "hoeveel dagen nog tot kerst")

MONTH_NAMES = ["januari", "februari", "maart", "april", "mei", "juni", "juli", "augustus", "september", "oktober",
               "november", "december"]
DAY_NAMES = ["maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag"]
MONTHS = {m: i for i, m in enumerate(MONTH_NAMES, 1)} | {
    "jan": 1, "feb": 2, "mrt": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9, "okt": 10, "nov": 11,
    "dec": 12,
}
DAYS = {d: i for i, d in enumerate(DAY_NAMES)}

_UNITS = ["", "een", "twee", "drie", "vier", "vijf", "zes", "zeven", "acht", "negen"]
_SMALL = {10: "tien", 11: "elf", 12: "twaalf", 13: "dertien", 14: "veertien", 15: "vijftien", 16: "zestien",
          17: "zeventien", 18: "achttien", 19: "negentien", 20: "twintig", 30: "dertig"}


def _cardinal(n: int) -> str:
    if n < 10:
        return _UNITS[n]
    if n in _SMALL:
        return _SMALL[n]
    unit = _UNITS[n % 10]
    return f"{unit}{'ën' if unit.endswith('e') else 'en'}{_SMALL[n - n % 10]}"


_ORDINALS = {(_cardinal(n) + ("de" if n < 20 else "ste")): n for n in range(1, 32)} | {
    "eerste": 1, "derde": 3, "achtste": 8,
}
"""Ordinals: "vijfentwintigste", "derde"."""


def ordinal(word: str) -> int | None:
    """A day of the month: "25", "25e", "25ste", "vijfentwintigste", "eerste"."""
    word = word.lower()
    digits = re.fullmatch(r"(\d{1,2})(?:e|ste|de)?", word)
    return int(digits.group(1)) if digits else _ORDINALS.get(word)


NTH = {"laatste": -1}


def _nth(word: str) -> int | None:
    return NTH.get(word) or ordinal(word)


def _year(text: str) -> int | None:
    value = parse_num(text)
    return int(value) if value is not None and float(value).is_integer() and 1 <= value <= 9999 else None


def when(text: str) -> When | None:
    """"25 december", "tweede zondag in mei", "laatste maandag van mei",
    "Pasen", "2 dagen voor Pasen", "1 week na ...", "20 juni 2027",
    "20-06-2027" -> when it falls -- or as English writes it ("December
    25"); None when it can't be read."""
    s = " ".join(re.sub(r",", " ", text.lower()).split())
    s = re.sub(r"^(?:de|het|op) ", "", s)
    if m := re.fullmatch(r"(\d{1,2})[-./](\d{1,2})(?:[-./](\d{4}))?", s):
        return _fixed(int(m.group(1)), int(m.group(2)), int(m.group(3)) if m.group(3) else None)
    if m := re.fullmatch(r"(.+?) (dagen|dag|weken|week) (voor|na) (.+)", s):
        count = parse_num(m.group(1))
        inner = when(m.group(4))
        if count is None or not float(count).is_integer() or count < 0 or inner is None:
            return None
        delta = dt.timedelta(days=int(count) * (7 if m.group(2).startswith("we") else 1))
        delta = -delta if m.group(3) == "voor" else delta

        def shifted(year: int) -> dt.date | None:
            date = inner.on(year)
            return date + delta if date is not None else None

        return When(shifted, year=inner.year)
    if s in ("pasen", "eerste paasdag", "paaszondag"):
        return When(easter)
    if m := re.fullmatch(r"(\S+) (\S+) (?:in|van) (\S+)", s):
        nth, day, month = _nth(m.group(1)), DAYS.get(m.group(2)), MONTHS.get(m.group(3))
        if nth is not None and 1 <= abs(nth) <= 5 and day is not None and month:

            def nth_weekday(year: int) -> dt.date | None:
                days = [d for d in calendar.Calendar().itermonthdates(year, month)
                        if d.month == month and d.weekday() == day]
                return days[-1] if nth == -1 else days[nth - 1] if nth <= len(days) else None

            return When(nth_weekday)
    if m := re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", s):
        return _fixed(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    if m := re.fullmatch(r"(\S+) ([a-z]+)(?: (.+))?", s):
        day, month = ordinal(m.group(1)), MONTHS.get(m.group(2))
        year = _year(m.group(3)) if m.group(3) else None
        if day and month and (m.group(3) is None or year):
            return _fixed(day, month, year)
    return english.when(text)


def _fixed(day: int, month: int, year: int | None) -> When | None:
    try:
        dt.date(year or 2000, month, day)
    except ValueError:
        return None
    if year is not None:
        date = dt.date(year, month, day)
        return When(lambda y: date if y == year else None, year=year)

    def yearly(y: int) -> dt.date | None:
        try:
            return dt.date(y, month, day)
        except ValueError:
            return None

    return When(yearly, fixed=(month, day))


def key(name: str) -> str:
    """A holiday's name as looked up: "de Eerste Kerstdag" -> "eerste
    kerstdag", "Sint-Maarten" -> "sint maarten"."""
    s = name.lower().replace("’", "'").replace("'", "").replace("-", " ")
    s = " ".join(re.sub(r"[,.]", " ", s).split())
    return re.sub(r"^(?:de|het) ", "", s)


def _h(name: str, on: str, *also: str) -> Holiday:
    return Holiday(name, on, also)


HOLIDAYS = (
    _h("Nieuwjaarsdag", "1 januari", "Nieuwjaar"),
    _h("Driekoningen", "6 januari"),
    _h("Valentijnsdag", "14 februari"),
    _h("Carnaval", "49 dagen voor Pasen", "Carnavalszondag"),
    _h("Goede Vrijdag", "2 dagen voor Pasen"),
    _h("Pasen", "Pasen", "Eerste Paasdag", "Paaszondag"),
    _h("Tweede Paasdag", "1 dag na Pasen", "Paasmaandag"),
    _h("Koningsdag", "27 april"),
    _h("Dodenherdenking", "4 mei"),
    _h("Bevrijdingsdag", "5 mei"),
    _h("Moederdag", "tweede zondag in mei"),
    _h("Hemelvaartsdag", "39 dagen na Pasen", "Hemelvaart"),
    _h("Pinksteren", "49 dagen na Pasen", "Eerste Pinksterdag"),
    _h("Tweede Pinksterdag", "50 dagen na Pasen"),
    _h("Vaderdag", "derde zondag in juni"),
    _h("Prinsjesdag", "derde dinsdag in september"),
    _h("Dierendag", "4 oktober"),
    _h("Halloween", "31 oktober"),
    _h("Sint-Maarten", "11 november", "Sint Maarten"),
    _h("Sinterklaas", "5 december", "Pakjesavond", "Sinterklaasavond"),
    _h("Kerstavond", "24 december"),
    _h("Eerste Kerstdag", "25 december", "Kerstmis", "Kerst"),
    _h("Tweede Kerstdag", "26 december"),
    _h("Oudejaarsavond", "31 december", "Oudjaar", "Oudejaarsdag"),
)
"""The built-in holidays (the Netherlands)."""

WHEN_EXAMPLES = "25 december, tweede zondag in mei, laatste maandag in mei, Pasen, 2 dagen voor Pasen, 20 juni 2027"


def parse_date(s):
    found = when(s)
    if found is None or found.year is None:
        return None
    return found.on(found.year)


def _named(holiday: Holiday, asked: str) -> str:
    return next((name for name in (holiday.name, *holiday.also) if key(name) == key(asked)), holiday.name)


def _day_of(text, today, settings):
    holiday = settings.holiday(text)
    if holiday is not None:
        return holiday.next(today)
    found = when(text)
    if found is None or found.fixed is None:
        return None
    for year in (today.year, today.year + 1):
        date = found.on(year)
        if date is not None and date >= today:
            return date
    return None


def _said(date: dt.date, today: dt.date, year: bool = False) -> str:
    """"25 december"; with its year when not this one, or ``year``."""
    said = f"{date.day} {MONTH_NAMES[date.month - 1]}"
    return f"{said} {date.year}" if year or date.year != today.year else said


def solve(q, settings):
    s = clean(q)
    if m := re.fullmatch(rf"(?:is|was|wordt) (?:het jaar )?{N} (?:een )?schrikkeljaar", s):
        year = parse_num(m.group(1))
        if year is not None and float(year).is_integer():
            yes = leap(int(year))
            return ("Ja" if yes else "Nee"), None, f"{'Ja' if yes else 'Nee'}, {int(year)} is {'een' if yes else 'geen'} schrikkeljaar"
    if re.fullmatch(r"hoeveel dagen (?:heeft|zijn er in|telt) (?:een )?schrikkeljaar", s):
        return "366", "Dat zijn 366 dagen.", "Een schrikkeljaar heeft 366 dagen"
    if re.fullmatch(r"hoeveel dagen (?:heeft|zijn er in|telt) (?:een )?(?:normaal |gewoon )?jaar", s):
        return "365", "Dat zijn 365 dagen.", "Een jaar heeft 365 dagen"
    if m := re.fullmatch(rf"hoeveel dagen (?:heeft|zijn er in|telt) ([a-z]+)(?: (?:in )?{N})?", s):
        month = MONTHS.get(m.group(1))
        year = parse_num(m.group(2)) if m.group(2) else None
        if month and (year is None or 1 <= year <= 9999):
            name = MONTH_NAMES[month - 1]
            if month == 2 and year is None:
                return "28, in een schrikkeljaar 29", "28, in een schrikkeljaar 29.", "Februari heeft 28 dagen, in een schrikkeljaar 29"
            days = calendar.monthrange(int(year or 2001), month)[1]
            said = f"{name} {int(year)}" if year else name
            return str(days), f"Dat zijn {days} dagen.", f"{said[:1].upper()}{said[1:]} heeft {days} dagen"
    if m := re.fullmatch(r"hoeveel (maanden|weken|dagen) (?:heeft|zijn er in|telt) een (jaar|week)", s):
        found = {("maanden", "jaar"): "12", ("weken", "jaar"): "52", ("dagen", "week"): "7", ("dagen", "jaar"): "365"}.get(m.groups())
        if found:
            return found, f"Dat zijn {found} {m.group(1)}.", f"Een {m.group(2)} heeft {found} {m.group(1)}"
    if m := (re.fullmatch(r"(?:welke|wat voor) (dag van de week|weekdag|dag) (?:is|was|wordt|valt) (?:het )?(.+?)(?: op)?", s)
             or re.fullmatch(r"op (?:welke|wat voor) (dag van de week|weekdag|dag) (?:valt|viel|is|was) (.+)", s)):
        asked = m.group(2)
        date = parse_date(asked)
        holiday = settings.holiday(asked) if date is None else None
        if date is None and holiday is not None and (m.group(1) != "dag" or s.startswith("op ")):
            date = holiday.next(shared.today())
        if date:
            day = DAY_NAMES[date.weekday()]
            name = _named(holiday, asked) if holiday else _said(date, shared.today(), year=True)
            return day, f"Dat is een {day}.", f"{name[:1].upper()}{name[1:]} is een {day}"
    if m := (re.fullmatch(r"wanneer is (?:het |de )?(.+)", s)
             or re.fullmatch(r"(?:welke datum|op welke datum|op welke dag|welke dag) (?:is|valt) (?:het |de )?(.+)", s)):
        holiday = settings.holiday(m.group(1))
        if holiday is not None:
            found, today = holiday.date, shared.today()
            if found.fixed:
                date = f"{found.fixed[1]} {MONTH_NAMES[found.fixed[0] - 1]}"
            elif found.year is not None:
                on = found.on(found.year)
                date = _said(on, today, year=True) if on else None
            else:
                on = holiday.next(today)
                date = _said(on, today) if on else None
            if date:
                return date, f"Op {date}.", f"{_named(holiday, m.group(1))} is op {date}"
    if m := (re.fullmatch(r"hoeveel (dagen|weken|nachtjes) (?:is het |zijn het )?(?:nog )?(?:tot|voor) (?:de |het )?(.+)", s)
             or re.fullmatch(r"hoe lang (?:is het |duurt het )?(?:nog )?(?:tot|voor) (?:de |het )?(.+)", s)):
        unit = m.group(1) if m.lastindex == 2 else "dagen"
        target_text = m.group(m.lastindex)
        today = shared.today()
        target = _day_of(target_text, today, settings)
        if target is not None:
            days = (target - today).days
            if unit == "weken":
                found = f"{days // 7} {'week' if days // 7 == 1 else 'weken'}"
            else:
                found = f"{days} {'dag' if days == 1 else 'dagen'}"
            holiday = settings.holiday(target_text)
            name = _named(holiday, target_text) if holiday else _said(target, today)
            return found, f"Nog {found}.", f"Het is nog {found} tot {name}"
    if m := re.fullmatch(r"(?:welke maand komt|wat is de maand) (na|voor) ([a-z]+)", s):
        month = MONTHS.get(m.group(2))
        if month:
            found = MONTH_NAMES[month % 12 if m.group(1) == "na" else (month - 2) % 12]
            return found, f"Dat is {found}.", f"Na {MONTH_NAMES[month - 1]} komt {found}" if m.group(1) == "na" else f"Voor {MONTH_NAMES[month - 1]} komt {found}"
    if m := re.fullmatch(r"(?:welke dag komt|wat is de (?:volgende )?dag) (na|voor) ([a-z]+)", s):
        day = DAYS.get(m.group(2))
        if day is not None:
            found = DAY_NAMES[(day + (1 if m.group(1) == "na" else -1)) % 7]
            return found, f"Dat is {found}.", None
    if m := re.fullmatch(r"(?:wat|welke) is de (eerste|laatste) maand van het jaar", s):
        found = "januari" if m.group(1) == "eerste" else "december"
        return found, f"Dat is {found}.", None
    return None


def answer(text: str, settings: Settings):
    found = solve(text, settings)
    if not found:
        return None
    said, specific, restated = found
    return reply(said, text, restated, specific=specific)
