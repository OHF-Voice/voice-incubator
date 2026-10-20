"""How German reads a date ("25. Dezember", "25.12.2026", "zweiter Sonntag im
Mai", "2 Tage vor Ostern"), asks about dates ("ist 2024 ein Schaltjahr",
"wann ist Ostern", "wie viele Tage noch bis Weihnachten") and says them; and
the holidays it knows (Germany's; voice_call/skills/dates.py). A holiday's
date may also be written as English writes it ("December 25"), as the UI is
in English."""

from __future__ import annotations

import calendar
import datetime as dt
import re

from voice_call.languages.en.skills import dates as english
from voice_call.skills import dates as shared
from voice_call.skills.common import reply
from voice_call.skills.dates import Holiday, Settings, When, easter, leap

from .numbers import N, clean, parse_num

EXAMPLES = ("ist 2024 ein Schaltjahr", "wann ist Ostern", "wie viele Tage noch bis Weihnachten")

MONTH_NAMES = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober",
               "November", "Dezember"]
DAY_NAMES = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
MONTHS = {m.lower(): i for i, m in enumerate(MONTH_NAMES, 1)} | {
    "maerz": 3, "jänner": 1, "feber": 2, "jan": 1, "feb": 2, "mär": 3, "mrz": 3, "apr": 4, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "sept": 9, "okt": 10, "nov": 11, "dez": 12,
}
DAYS = {d.lower(): i for i, d in enumerate(DAY_NAMES)} | {"sonnabend": 5}

_UNITS = ["", "ein", "zwei", "drei", "vier", "fünf", "sechs", "sieben", "acht", "neun"]
_SMALL = {10: "zehn", 11: "elf", 12: "zwölf", 13: "dreizehn", 14: "vierzehn", 15: "fünfzehn", 16: "sechzehn",
          17: "siebzehn", 18: "achtzehn", 19: "neunzehn", 20: "zwanzig", 30: "dreißig"}


def _cardinal(n: int) -> str:
    if n < 10:
        return _UNITS[n] if n != 1 else "eins"
    if n in _SMALL:
        return _SMALL[n]
    return f"{_UNITS[n % 10]}und{_SMALL[n - n % 10]}"


_ORDINALS = {
    (_cardinal(n) + ("t" if n < 20 else "st")): n for n in range(2, 32)
} | {"erst": 1, "dritt": 3, "siebt": 7, "siebent": 7, "acht": 8, "dreissigst": 30, "einunddreissigst": 31}
"""Ordinal stems, before their ending: "fünfundzwanzigst(e)", "dritt(en)"."""


def ordinal(word: str) -> int | None:
    """A day of the month: "25", "25.", "fünfundzwanzigste", "ersten"."""
    word = word.lower().rstrip(".")
    if word.isdigit():
        return int(word)
    return _ORDINALS.get(re.sub(r"(?:e|er|en|em|es)$", "", word))


NTH = {"letzte": -1, "letzter": -1, "letzten": -1, "letztem": -1}
"""The last weekday of a month; else ``ordinal``."""


def _nth(word: str) -> int | None:
    return NTH.get(word) or ordinal(word)


def _year(text: str) -> int | None:
    value = parse_num(text)
    return int(value) if value is not None and float(value).is_integer() and 1 <= value <= 9999 else None


def when(text: str) -> When | None:
    """"25. Dezember", "zweiter Sonntag im Mai", "letzter Montag im Mai",
    "Ostern", "2 Tage vor Ostern", "1 Woche nach ...", "20. Juni 2027",
    "20.06.2027" -> when it falls -- or as English writes it ("December
    25"); None when it can't be read."""
    s = " ".join(re.sub(r",", " ", text.lower()).split())
    s = re.sub(r"^(?:der|die|das|den|dem|am) ", "", s)
    if m := re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})?", s):
        day, month = int(m.group(1)), int(m.group(2))
        year = int(m.group(3)) if m.group(3) else None
        return _fixed(day, month, year)
    s = re.sub(r"(\d)\.", r"\1", s)
    if m := re.fullmatch(r"(.+?) (tage?|tagen|wochen?) (vor|nach) (?:dem |der )?(.+)", s):
        count = parse_num(m.group(1))
        inner = when(m.group(4))
        if count is None or not float(count).is_integer() or count < 0 or inner is None:
            return None
        delta = dt.timedelta(days=int(count) * (7 if m.group(2).startswith("woche") else 1))
        delta = -delta if m.group(3) == "vor" else delta

        def shifted(year: int) -> dt.date | None:
            date = inner.on(year)
            return date + delta if date is not None else None

        return When(shifted, year=inner.year)
    if s in ("ostern", "ostersonntag"):
        return When(easter)
    if m := re.fullmatch(r"(\S+) (\S+) (?:im|in|des) (\S+)", s):
        nth, day, month = _nth(m.group(1)), DAYS.get(m.group(2)), MONTHS.get(m.group(3))
        if nth is not None and 1 <= abs(nth) <= 5 and day is not None and month:

            def nth_weekday(year: int) -> dt.date | None:
                days = [d for d in calendar.Calendar().itermonthdates(year, month)
                        if d.month == month and d.weekday() == day]
                return days[-1] if nth == -1 else days[nth - 1] if nth <= len(days) else None

            return When(nth_weekday)
    if m := re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", s):
        return _fixed(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    if m := re.fullmatch(r"(\S+) ([a-zäöü]+)(?: (.+))?", s):
        day, month = ordinal(m.group(1)), MONTHS.get(m.group(2))
        year = _year(m.group(3)) if m.group(3) else None
        if day and month and (m.group(3) is None or year):
            return _fixed(day, month, year)
    return english.when(text)


def _fixed(day: int, month: int, year: int | None) -> When | None:
    try:
        dt.date(year or 2000, month, day)  # 2000: a leap year, so the 29th of February is a day
    except ValueError:
        return None
    if year is not None:
        date = dt.date(year, month, day)
        return When(lambda y: date if y == year else None, year=year)

    def yearly(y: int) -> dt.date | None:
        try:
            return dt.date(y, month, day)
        except ValueError:  # the 29th of February, not a leap year
            return None

    return When(yearly, fixed=(month, day))


def key(name: str) -> str:
    """A holiday's name as looked up: "der Tag der Deutschen Einheit" -> "tag
    der deutschen einheit", "St. Martin" -> "sankt martin"."""
    s = name.lower().replace("’", "'").replace("'", "")
    s = re.sub(r"\bst\b\.?", "sankt", s)
    s = " ".join(re.sub(r"[,.]", " ", s).split())
    return re.sub(r"^(?:der|die|das|den|dem|zum|zur) ", "", s)


def _h(name: str, on: str, *also: str) -> Holiday:
    return Holiday(name, on, also)


HOLIDAYS = (
    _h("Neujahr", "1. Januar", "Neujahrstag"),
    _h("Heilige Drei Könige", "6. Januar", "Dreikönigstag"),
    _h("Valentinstag", "14. Februar"),
    _h("Weiberfastnacht", "52 Tage vor Ostern"),
    _h("Rosenmontag", "48 Tage vor Ostern"),
    _h("Faschingsdienstag", "47 Tage vor Ostern", "Fastnacht", "Karnevalsdienstag"),
    _h("Aschermittwoch", "46 Tage vor Ostern"),
    _h("Gründonnerstag", "3 Tage vor Ostern"),
    _h("Karfreitag", "2 Tage vor Ostern"),
    _h("Ostern", "Ostern", "Ostersonntag"),
    _h("Ostermontag", "1 Tag nach Ostern"),
    _h("Tag der Arbeit", "1. Mai", "Maifeiertag", "erster Mai"),
    _h("Muttertag", "zweiter Sonntag im Mai"),
    _h("Christi Himmelfahrt", "39 Tage nach Ostern", "Himmelfahrt", "Vatertag"),
    _h("Pfingsten", "49 Tage nach Ostern", "Pfingstsonntag"),
    _h("Pfingstmontag", "50 Tage nach Ostern"),
    _h("Fronleichnam", "60 Tage nach Ostern"),
    _h("Mariä Himmelfahrt", "15. August"),
    _h("Tag der Deutschen Einheit", "3. Oktober"),
    _h("Erntedankfest", "erster Sonntag im Oktober", "Erntedank"),
    _h("Halloween", "31. Oktober"),
    _h("Reformationstag", "31. Oktober"),
    _h("Allerheiligen", "1. November"),
    _h("Sankt Martin", "11. November", "Martinstag"),
    _h("Nikolaus", "6. Dezember", "Nikolaustag"),
    _h("Heiligabend", "24. Dezember", "Heiliger Abend"),
    _h("Erster Weihnachtstag", "25. Dezember", "Weihnachten", "erster Weihnachtsfeiertag", "Weihnachtstag"),
    _h("Zweiter Weihnachtstag", "26. Dezember", "zweiter Weihnachtsfeiertag", "Stephanstag"),
    _h("Silvester", "31. Dezember", "Silvesterabend"),
)
"""The built-in holidays (Germany)."""

WHEN_EXAMPLES = "25. Dezember, zweiter Sonntag im Mai, letzter Montag im Mai, Ostern, 2 Tage vor Ostern, 20. Juni 2027"


def parse_date(s):
    """A date with its year: "4. Juli 2026", "der vierte Juli 2026",
    "04.07.2026"; None."""
    found = when(s)
    if found is None or found.year is None:
        return None
    return found.on(found.year)


def _day_of(text, today, settings):
    """The next date a holiday or "25. Dezember" falls on, from ``today``."""
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


def _named(holiday: Holiday, asked: str) -> str:
    """The holiday by the name it was asked by: "Weihnachten", not "Erster
    Weihnachtstag"."""
    return next((name for name in (holiday.name, *holiday.also) if key(name) == key(asked)), holiday.name)


def _said(date: dt.date, today: dt.date, year: bool = False) -> str:
    """"25. Dezember"; with its year when not this one, or ``year``."""
    said = f"{date.day}. {MONTH_NAMES[date.month - 1]}"
    return f"{said} {date.year}" if year or date.year != today.year else said


def solve(q, settings):
    """The answer, the sentence it is said in, and with the question
    ("Weihnachten ist am 25. Dezember"); None."""
    s = clean(q)
    if m := re.fullmatch(rf"(?:ist|war|wird) (?:das jahr )?{N} (?:ein )?schaltjahr(?: sein)?", s):
        year = parse_num(m.group(1))
        if year is not None and float(year).is_integer():
            yes = leap(int(year))
            return ("Ja" if yes else "Nein"), None, f"{'Ja' if yes else 'Nein'}, {int(year)} ist {'ein' if yes else 'kein'} Schaltjahr"
    if re.fullmatch(r"wie viele tage (?:hat|haben|sind in|gibt es in) (?:ein|einem) schaltjahr", s):
        return "366", "Das sind 366 Tage.", "Ein Schaltjahr hat 366 Tage"
    if re.fullmatch(r"wie viele tage (?:hat|haben|sind in|gibt es in) (?:ein|einem) (?:normalen |normales |gewöhnlichen |gewöhnliches )?jahr", s):
        return "365", "Das sind 365 Tage.", "Ein Jahr hat 365 Tage"
    if m := re.fullmatch(rf"wie viele tage (?:hat|haben|sind im|sind in|gibt es im|gibt es in) (?:der |den |dem )?([a-zäöü]+)(?: (?:im jahr |des jahres )?{N})?", s):
        month = MONTHS.get(m.group(1))
        year = parse_num(m.group(2)) if m.group(2) else None
        if month and (year is None or 1 <= year <= 9999):
            name = MONTH_NAMES[month - 1]
            if month == 2 and year is None:
                return "28, im Schaltjahr 29", "28, im Schaltjahr 29.", "Der Februar hat 28 Tage, im Schaltjahr 29"
            days = calendar.monthrange(int(year or 2001), month)[1]
            said = f"{name} {int(year)}" if year else name
            return str(days), f"Das sind {days} Tage.", f"Der {said} hat {days} Tage"
    if m := re.fullmatch(r"wie viele (monate|wochen|tage) (?:hat|haben|sind in|gibt es in) (?:ein|einem|eine|einer) (jahr|woche)", s):
        found = {("monate", "jahr"): "12", ("wochen", "jahr"): "52", ("tage", "woche"): "7", ("tage", "jahr"): "365"}.get(m.groups())
        if found:
            what, of = m.group(1).capitalize(), "Ein Jahr" if m.group(2) == "jahr" else "Eine Woche"
            return found, f"Das sind {found} {what}.", f"{of} hat {found} {what}"
    # The day of the week a date or holiday falls on.
    if m := (re.fullmatch(r"(?:welcher|was für ein) (wochentag|tag) (?:ist|war|wird) (?:der |das )?(.+?)(?: sein)?", s)
             or re.fullmatch(r"(?:auf welchen|an welchem) (wochentag|tag) (?:fällt|fiel|ist|war|liegt) (?:der |das )?(.+)", s)):
        asked = m.group(2)
        date = parse_date(asked)
        holiday = settings.holiday(asked) if date is None else None
        if date is None and holiday is not None and (m.group(1) == "wochentag" or "fällt" in s or "fiel" in s):
            date = holiday.next(shared.today())
        if date:
            day = DAY_NAMES[date.weekday()]
            name = holiday.name if holiday else f"Der {_said(date, shared.today(), year=True)}"
            return day, f"Das ist ein {day}.", f"{name} ist ein {day}"
    # A holiday's date.
    if m := (re.fullmatch(r"wann ist (?:der |die |das )?(.+)", s)
             or re.fullmatch(r"(?:welches datum|an welchem datum|an welchem tag|welcher tag) (?:ist|hat|fällt) (?:der |die |das )?(.+)", s)):
        holiday = settings.holiday(m.group(1))
        if holiday is not None:
            found, today = holiday.date, shared.today()
            if found.fixed:
                date = f"{found.fixed[1]}. {MONTH_NAMES[found.fixed[0] - 1]}"
            elif found.year is not None:
                on = found.on(found.year)
                date = _said(on, today, year=True) if on else None
            else:
                on = holiday.next(today)
                date = _said(on, today) if on else None
            if date:
                return date, f"Am {date}.", f"{_named(holiday, m.group(1))} ist am {date}"
    # Days until a holiday or a date.
    if m := (re.fullmatch(r"wie viele (tage|wochen|nächte) (?:sind es |dauert es |ist es )?(?:noch )?bis (zu |zum |zur )?(.+)", s)
             or re.fullmatch(r"wie lange (?:ist es |dauert es |sind es )?(?:noch )?bis (zu |zum |zur )?(.+)", s)):
        unit = m.group(1) if m.lastindex == 3 else "tage"
        to = m.group(m.lastindex - 1) or ""
        target_text = m.group(m.lastindex)
        today = shared.today()
        target = _day_of(target_text, today, settings)
        if target is not None:
            days = (target - today).days
            if unit == "wochen":
                found = f"{days // 7} {'Woche' if days // 7 == 1 else 'Wochen'}"
            else:
                found = f"{days} {'Tag' if days == 1 else 'Tage'}"
            holiday = settings.holiday(target_text)
            name = f"{to}{_named(holiday, target_text)}" if holiday else f"zum {_said(target, today)}"
            return found, f"Noch {found}.", f"Bis {name} sind es noch {found}"
    if m := re.fullmatch(r"(?:welcher monat kommt|was ist der monat|welcher monat ist) (nach|vor) (?:dem )?([a-zäöü]+)", s):
        month = MONTHS.get(m.group(2))
        if month:
            found = MONTH_NAMES[month % 12 if m.group(1) == "nach" else (month - 2) % 12]
            return found, f"Das ist der {found}.", f"Nach dem {MONTH_NAMES[month - 1]} kommt der {found}" if m.group(1) == "nach" else f"Vor dem {MONTH_NAMES[month - 1]} kommt der {found}"
    if m := re.fullmatch(r"(?:welcher tag kommt|was ist der (?:nächste )?tag|welcher tag ist) (nach|vor) (?:dem )?([a-zäöü]+)", s):
        day = DAYS.get(m.group(2))
        if day is not None:
            found = DAY_NAMES[(day + (1 if m.group(1) == "nach" else -1)) % 7]
            return found, f"Das ist der {found}.", None
    if m := re.fullmatch(r"(?:was|welcher) ist der (erste|letzte) monat (?:des jahres|im jahr)", s):
        found = "Januar" if m.group(1) == "erste" else "Dezember"
        return found, f"Das ist der {found}.", None
    return None


def answer(text: str, settings: Settings):
    found = solve(text, settings)
    if not found:
        return None
    said, specific, restated = found
    return reply(said, text, restated, specific=specific)
