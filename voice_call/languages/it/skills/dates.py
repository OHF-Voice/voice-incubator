"""How Italian reads a date ("25 dicembre", "primo maggio", "1° maggio",
"25/12/2026", "seconda domenica di maggio", "ultimo lunedì di maggio", "2
giorni prima di Pasqua"), asks about dates ("il 2024 è bisestile", "quando è
Pasqua", "quanti giorni mancano a Natale") and says them; and the holidays
it knows (Italy's; voice_call/skills/dates.py). A holiday's date may also be
written as English writes it ("December 25").

A built-in holiday is said with its article where Italian gives it one ("la
Festa della Repubblica", "l'Epifania"; "Natale", "Ferragosto" without); one
of yours as you named it. Questions and holidays are read without their
accents ("quando e Pasquetta", "lunedi"); what is said has them."""

from __future__ import annotations

import calendar
import datetime as dt
import re

from voice_call.languages.en.skills import dates as english
from voice_call.skills import dates as shared
from voice_call.skills.common import reply
from voice_call.skills.dates import Holiday, Settings, When, easter, leap

from .numbers import N, clean, fold, parse_num

EXAMPLES = ("il 2024 è bisestile", "quando è Pasqua", "quanti giorni mancano a Natale")

MONTH_NAMES = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre",
               "ottobre", "novembre", "dicembre"]
DAY_NAMES = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
MONTHS = {m: i for i, m in enumerate(MONTH_NAMES, 1)} | {
    "gen": 1, "feb": 2, "mar": 3, "apr": 4, "mag": 5, "giu": 6, "lug": 7, "ago": 8, "set": 9, "sett": 9, "ott": 10,
    "nov": 11, "dic": 12,
}
DAYS = {fold(d): i for i, d in enumerate(DAY_NAMES)}

NTH = {
    "primo": 1, "prima": 1, "secondo": 2, "seconda": 2, "terzo": 3, "terza": 3, "quarto": 4, "quarta": 4,
    "quinto": 5, "quinta": 5, "ultimo": -1, "ultima": -1,
}
"""The nth weekday of a month: "seconda domenica", "ultimo lunedì"."""


def _day(text: str) -> int | None:
    """A day of the month: "25", "venticinque", "primo", "1°"."""
    text = text.strip().rstrip("°º")
    if text in ("primo", "1o"):
        return 1
    value = parse_num(text)
    return int(value) if value is not None and float(value).is_integer() and 1 <= value <= 31 else None


def _year(text: str) -> int | None:
    value = parse_num(re.sub(r"^(?:del|nel|dell'anno) ", "", text.strip()))
    return int(value) if value is not None and float(value).is_integer() and 1 <= value <= 9999 else None


def when(text: str) -> When | None:
    """"25 dicembre", "primo maggio", "seconda domenica di maggio", "ultimo
    lunedì di maggio", "Pasqua", "2 giorni prima di Pasqua", "1 settimana
    dopo ...", "20 giugno 2027", "20/06/2027" -> when it falls -- or as
    English writes it ("December 25"); None when it can't be read."""
    s = " ".join(fold(text.lower().replace("’", "'")).replace(",", " ").split())
    s = re.sub(r"^(?:il|lo|la|i|gli|le) |^l'\s*", "", s)
    if m := re.fullmatch(r"(\d{1,2})[-./](\d{1,2})(?:[-./](\d{4}))?", s):
        return _fixed(int(m.group(1)), int(m.group(2)), int(m.group(3)) if m.group(3) else None)
    if m := re.fullmatch(r"(.+?) (giorni|giorno|settimane|settimana) (prima|dopo) (?:di |del |della |dell'|de )?(.+)", s):
        count = parse_num(m.group(1))
        inner = when(m.group(4))
        if count is None or not float(count).is_integer() or count < 0 or inner is None:
            return None
        delta = dt.timedelta(days=int(count) * (7 if m.group(2).startswith("settiman") else 1))
        delta = -delta if m.group(3) == "prima" else delta

        def shifted(year: int) -> dt.date | None:
            date = inner.on(year)
            return date + delta if date is not None else None

        return When(shifted, year=inner.year)
    if s in ("pasqua", "domenica di pasqua"):
        return When(easter)
    if m := re.fullmatch(r"(\S+) (\S+) (?:di|del mese di) (\S+)", s):
        nth, day, month = NTH.get(m.group(1)), DAYS.get(m.group(2)), MONTHS.get(m.group(3).strip("."))
        if nth is not None and day is not None and month:

            def nth_weekday(year: int) -> dt.date | None:
                days = [d for d in calendar.Calendar().itermonthdates(year, month)
                        if d.month == month and d.weekday() == day]
                return days[-1] if nth == -1 else days[nth - 1] if nth <= len(days) else None

            return When(nth_weekday)
    if m := re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", s):
        return _fixed(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    month_names = "|".join(sorted(MONTHS, key=len, reverse=True))
    if m := re.fullmatch(rf"(.+?) (?:di )?({month_names})\.?(?: (.+))?", s):
        day, month = _day(m.group(1)), MONTHS[m.group(2)]
        year = _year(m.group(3)) if m.group(3) else None
        if day and (m.group(3) is None or year):
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
    """A holiday's name as looked up: "la Festa della mamma" -> "festa della
    mamma", "l'Epifania" -> "epifania", "Festa del papà" -> "festa del
    papa"."""
    s = re.sub(r"['\-]", " ", fold(name.lower().replace("’", "'")))
    s = " ".join(re.sub(r"[,.]", " ", s).split())
    return re.sub(r"^(?:il|lo|la|i|gli|le|l) ", "", s)


_THE: dict[str, str] = {}
"""A built-in holiday's article, by its ``key``."""


def _h(name: str, on: str, *also: str) -> Holiday:
    """A holiday, each of its names given with its article ("la Festa della
    mamma", "l'Epifania")."""
    bare = []
    for given in (name, *also):
        found = re.match(r"(il |lo |la |i |gli |le |l')", given)
        if found:
            given = given[found.end():]
            _THE[key(given)] = found.group(1)
        bare.append(given)
    return Holiday(bare[0], on, tuple(bare[1:]))


def the(name: str) -> str:
    """A holiday's name as said in a sentence: "la Festa della mamma",
    "Natale"."""
    article = _THE.get(key(name))
    return f"{article}{name}" if article else name


HOLIDAYS = (
    _h("Capodanno", "1 gennaio", "il Primo dell'anno", "il Primo gennaio"),
    _h("l'Epifania", "6 gennaio", "la Befana"),
    _h("San Valentino", "14 febbraio", "la Festa degli innamorati"),
    _h("Martedì grasso", "47 giorni prima di Pasqua", "Carnevale"),
    _h("il Mercoledì delle Ceneri", "46 giorni prima di Pasqua", "le Ceneri"),
    _h("la Festa della donna", "8 marzo"),
    _h("la Festa del papà", "19 marzo", "San Giuseppe"),
    _h("la Domenica delle Palme", "7 giorni prima di Pasqua", "le Palme"),
    _h("il Venerdì santo", "2 giorni prima di Pasqua"),
    _h("Pasqua", "Pasqua", "la Domenica di Pasqua"),
    _h("Pasquetta", "1 giorno dopo Pasqua", "il Lunedì dell'Angelo", "il Lunedì di Pasqua"),
    _h("la Festa della Liberazione", "25 aprile", "la Liberazione", "il 25 aprile"),
    _h("la Festa dei lavoratori", "1 maggio", "il Primo maggio", "la Festa del lavoro"),
    _h("la Festa della mamma", "seconda domenica di maggio"),
    _h("la Festa della Repubblica", "2 giugno", "il 2 giugno"),
    _h("Ferragosto", "15 agosto", "l'Assunzione", "l'Assunta"),
    _h("Halloween", "31 ottobre"),
    _h("Ognissanti", "1 novembre", "Tutti i santi", "la Festa di Ognissanti"),
    _h("la Commemorazione dei defunti", "2 novembre", "il Giorno dei morti", "i Morti"),
    _h("l'Immacolata", "8 dicembre", "l'Immacolata Concezione"),
    _h("la Vigilia di Natale", "24 dicembre"),
    _h("Natale", "25 dicembre", "il Giorno di Natale"),
    _h("Santo Stefano", "26 dicembre"),
    _h("San Silvestro", "31 dicembre", "la Vigilia di Capodanno", "l'Ultimo dell'anno"),
)
"""The built-in holidays (Italy)."""

WHEN_EXAMPLES = "25 dicembre, seconda domenica di maggio, ultimo lunedì di maggio, Pasqua, 2 giorni prima di Pasqua, 20 giugno 2027"


def parse_date(s):
    """A date with its year: "il 4 luglio 2026", "04/07/2026"; None."""
    found = when(s)
    if found is None or found.year is None:
        return None
    return found.on(found.year)


def _named(holiday: Holiday, asked: str) -> str:
    """The holiday by the name it was asked by: "Pasquetta", not "Lunedì
    dell'Angelo"."""
    return next((name for name in (holiday.name, *holiday.also) if key(name) == key(asked)), holiday.name)


def _day_of(text, today, settings):
    """The next date a holiday or "25 dicembre" falls on, from ``today``."""
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
    """"25 dicembre", "primo maggio"; with its year when not this one, or
    ``year``."""
    said = f"{'primo' if date.day == 1 else date.day} {MONTH_NAMES[date.month - 1]}"
    return f"{said} {date.year}" if year or date.year != today.year else said


def _article(said: str) -> str:
    """A date with its article: "il 25 dicembre", "l'8 dicembre", "l'11
    novembre", "il primo maggio"."""
    return f"l'{said}" if re.match(r"(?:8|11)\b", said) else f"il {said}"


_JOINED = {"il ": "al ", "lo ": "allo ", "la ": "alla ", "l'": "all'", "i ": "ai ", "gli ": "agli ", "le ": "alle "}


def _to(said: str) -> str:
    """"a" joined to what follows: "a Natale", "alla Festa della mamma",
    "all'8 dicembre", "al 25 dicembre"."""
    for article, joined in _JOINED.items():
        if said.startswith(article):
            return joined + said[len(article):]
    return f"a {said}"


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


def _bare(text: str) -> str:
    """What is asked about, without its article or "a" ("alla Festa della
    mamma" -> "Festa della mamma")."""
    return re.sub(r"^(?:(?:fino )?(?:a|ad|al|alla|allo|ai|agli|alle) |(?:fino )?all'|il |lo |la |i |gli |le |l')", "", text)


def solve(q, settings):
    """The answer, the sentence it is said in, and with the question ("Natale
    è il 25 dicembre"); None."""
    s = fold(clean(q))
    # Leap years.
    if m := (re.fullmatch(rf"(?:il |l'anno |nel )?{N} (?:e|era|sara|fu) (?:un )?(?:anno )?bisestile(?: o no)?", s)
             or re.fullmatch(rf"(?:e|era|sara|fu) (?:un )?(?:anno )?bisestile (?:il |l'anno )?{N}", s)):
        year = parse_num(m.group(1))
        if year is not None and float(year).is_integer():
            yes = leap(int(year))
            said = f"il {int(year)} è bisestile" if yes else f"il {int(year)} non è bisestile"
            return ("Sì" if yes else "No"), None, f"{'Sì' if yes else 'No'}, {said}"
    days_in = r"quanti giorni (?:ha|hanno|conta|ci sono (?:in|a|nel|nell')|ha il mese di|ci sono nel mese di) ?"
    if re.fullmatch(rf"{days_in}(?:un |l')?anno bisestile", s):
        return "366", "Sono 366 giorni.", "Un anno bisestile ha 366 giorni"
    if re.fullmatch(rf"{days_in}(?:un |l')?anno(?: normale| non bisestile)?", s):
        return "365", "Sono 365 giorni.", "Un anno ha 365 giorni"
    if m := re.fullmatch(rf"{days_in}(?:il mese di )?([a-z]+)(?: (?:del |nel )?{N})?", s):
        month = MONTHS.get(m.group(1))
        year = parse_num(m.group(2)) if m.group(2) else None
        if month and (year is None or 1 <= year <= 9999):
            name = MONTH_NAMES[month - 1]
            if month == 2 and year is None:
                return ("28, o 29 negli anni bisestili", "28, o 29 negli anni bisestili.",
                        "Febbraio ha 28 giorni, o 29 negli anni bisestili")
            days = calendar.monthrange(int(year or 2001), month)[1]
            said = f"{name} {int(year)}" if year else name
            return str(days), f"Sono {days} giorni.", f"{_capital(said)} ha {days} giorni"
    if m := re.fullmatch(r"quant[ie] (mesi|settimane|giorni) (?:ha|conta|ci sono in|ci sono nell') ?(?:un |una |l')?(anno|settimana)", s):
        found = {("mesi", "anno"): "12", ("settimane", "anno"): "52", ("giorni", "settimana"): "7",
                 ("giorni", "anno"): "365"}.get(m.groups())
        if found:
            of = "Un anno" if m.group(2) == "anno" else "Una settimana"
            return found, f"Sono {found} {m.group(1)}.", f"{of} ha {found} {m.group(1)}"
    # The month or day after another.
    if m := re.fullmatch(r"(?:che|quale|qual e il) (mese|giorno) (?:viene|c'e|e) (dopo|prima) (?:di |del |della )?([a-z]+)", s):
        after = m.group(2) == "dopo"
        if m.group(1) == "mese" and (month := MONTHS.get(m.group(3))):
            found, asked = MONTH_NAMES[month % 12 if after else (month - 2) % 12], MONTH_NAMES[month - 1]
            return found, f"È {found}.", f"Dopo {asked} viene {found}" if after else f"Prima di {asked} viene {found}"
        if m.group(1) == "giorno" and (day := DAYS.get(m.group(3))) is not None:
            found, asked = DAY_NAMES[(day + (1 if after else -1)) % 7], DAY_NAMES[day]
            return found, f"È {found}.", f"Dopo {asked} viene {found}" if after else f"Prima di {asked} viene {found}"
    if m := re.fullmatch(r"(?:qual e|quale e|che e|che mese e) (?:il |l')(primo|ultimo) mese dell'anno", s):
        found, which = ("gennaio", "Il primo") if m.group(1) == "primo" else ("dicembre", "L'ultimo")
        return found, f"È {found}.", f"{which} mese dell'anno è {found}"
    # The day of the week a date or holiday falls on.
    if m := (re.fullmatch(r"(?:in )?(?:che|quale) giorno della settimana (e|era|sara|fu|cade|cadra|cadeva|cadde) (.+)", s)
             or re.fullmatch(r"(?:in )?(?:che|quale) giorno (cade|cadra|cadeva|cadde) (.+)", s)
             or re.fullmatch(r"(?:che|quale) giorno (e|era|sara|fu) (.+)", s)):
        asked = _bare(m.group(2))
        date = parse_date(asked)
        holiday = settings.holiday(asked) if date is None else None
        # "Che giorno è Natale" asks its date (below); "che giorno è il 4
        # luglio", "in che giorno cade Natale", its day of the week.
        weekday = "settimana" in s or m.group(1).startswith("cad") or holiday is None
        if date is None and weekday:
            # A holiday, or a date without its year: the next time it comes.
            date = _day_of(asked, shared.today(), settings)
        if date:
            day = DAY_NAMES[date.weekday()]
            if holiday is not None:
                restated = f"{_capital(the(_named(holiday, asked)))} cade di {day}"
            else:
                said = _said(date, shared.today(), year=parse_date(asked) is not None)
                restated = f"{_capital(_article(said))} è {day}"
            return day, f"È {day}.", restated
    # A holiday's date.
    if m := (re.fullmatch(r"(?:quando (?:e|cade|sara|cadra|c'e|si festeggia|si celebra|ricorre|arriva)|quand'e"
                          r"|in che data (?:e|cade|sara)|che data e|(?:che|quale) giorno (?:e|sara)"
                          r"|qual e la data (?:di|del|della|dello|dell'|dei|degli|delle) ?) ?(.+)", s)
             or re.fullmatch(r"(.+?),? (?:quand'e|quando (?:e|cade))", s)):
        asked = _bare(m.group(1))
        holiday = settings.holiday(asked)
        if holiday is not None:
            found, today = holiday.date, shared.today()
            if found.fixed:
                date = f"{'primo' if found.fixed[1] == 1 else found.fixed[1]} {MONTH_NAMES[found.fixed[0] - 1]}"
            elif found.year is not None:
                on = found.on(found.year)
                date = _said(on, today, year=True) if on else None
            else:
                on = holiday.next(today)
                date = _said(on, today) if on else None
            if date:
                return date, f"{_capital(_article(date))}.", f"{_capital(the(_named(holiday, asked)))} è {_article(date)}"
    # Days until a holiday or a date.
    if m := (re.fullmatch(r"quant[ie] (giorni|settimane|notti) (?:mancano|ci sono|restano|rimangono|ci separano)"
                          r" (?:ancora )?(?:da oggi )?(.+)", s)
             or re.fullmatch(r"tra quant[ie] (giorni|settimane) (?:e|sara|arriva|c'e) (.+)", s)
             or re.fullmatch(r"quanto (?:tempo )?(?:manca|resta|rimane) (?:ancora )?(?:da oggi )?(.+)", s)):
        unit = m.group(1) if m.lastindex == 2 else "giorni"
        target_text = _bare(m.group(m.lastindex))
        today = shared.today()
        target = _day_of(target_text, today, settings)
        if target is not None:
            days = (target - today).days
            count = days // 7 if unit == "settimane" else days
            one = {"giorni": "giorno", "settimane": "settimana", "notti": "notte"}[unit]
            found = f"{count} {one if count == 1 else unit}"
            verb = "Manca" if count == 1 else "Mancano"
            holiday = settings.holiday(target_text)
            name = the(_named(holiday, target_text)) if holiday else _article(_said(target, today))
            return found, f"{verb} {found}.", f"{verb} {found} {_to(name)}"
    return None


def answer(text: str, settings: Settings):
    found = solve(text, settings)
    if not found:
        return None
    said, specific, restated = found
    return reply(said, text, restated, brief=f"{_capital(said)}.", specific=specific)
