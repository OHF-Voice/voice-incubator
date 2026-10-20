"""How French reads a date ("25 décembre", "1er mai", "25/12/2026",
"deuxième dimanche de mai", "2 jours avant Pâques"), asks about dates
("2024 est-elle une année bissextile", "quand tombe Pâques", "combien de
jours avant Noël") and says them; and the holidays it knows (France's;
voice_call/skills/dates.py). A holiday's date may also be written as English
writes it ("December 25").

A built-in holiday is said with its article ("la Toussaint", "le Jour de
l'an"); one of yours as you named it."""

from __future__ import annotations

import calendar
import datetime as dt
import re
import unicodedata

from voice_call.languages.en.skills import dates as english
from voice_call.skills import dates as shared
from voice_call.skills.common import reply
from voice_call.skills.dates import Holiday, Settings, When, easter, leap

from .numbers import N, clean, parse_num

EXAMPLES = ("2024 est-elle une année bissextile", "quand tombe Pâques", "combien de jours avant Noël")

MONTH_NAMES = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
               "novembre", "décembre"]
DAY_NAMES = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


def _plain(text: str) -> str:
    """Lowercase, without accents: "Février" -> "fevrier"."""
    decomposed = unicodedata.normalize("NFD", text.lower().replace("’", "'"))
    return "".join(c for c in decomposed if not unicodedata.combining(c))


MONTHS = {_plain(m): i for i, m in enumerate(MONTH_NAMES, 1)} | {
    "janv": 1, "fev": 2, "fevr": 2, "avr": 4, "juil": 7, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
DAYS = {_plain(d): i for i, d in enumerate(DAY_NAMES)}

NTH = {
    "premier": 1, "premiere": 1, "1er": 1, "1re": 1, "deuxieme": 2, "second": 2, "seconde": 2, "2e": 2, "2eme": 2,
    "troisieme": 3, "3e": 3, "3eme": 3, "quatrieme": 4, "4e": 4, "4eme": 4, "cinquieme": 5, "5e": 5, "5eme": 5,
    "dernier": -1, "derniere": -1,
}


def _day(text: str) -> int | None:
    """A day of the month: "25", "vingt-cinq", "1er", "premier"."""
    if text in ("1er", "premier"):
        return 1
    value = parse_num(text)
    return int(value) if value is not None and float(value).is_integer() and 1 <= value <= 31 else None


def _year(text: str) -> int | None:
    value = parse_num(text)
    return int(value) if value is not None and float(value).is_integer() and 1 <= value <= 9999 else None


def when(text: str) -> When | None:
    """"25 décembre", "1er mai", "deuxième dimanche de mai", "dernier lundi
    de mai", "Pâques", "2 jours avant Pâques", "1 semaine après ...", "20
    juin 2027", "20/06/2027", "dernier dimanche de mai, sauf Pentecôte" ->
    when it falls -- or as English writes it ("December 25"); None when it
    can't be read."""
    s = " ".join(_plain(text).split())
    s = re.sub(r"^(?:le|la|l'|les) ?", "", s)
    if m := re.fullmatch(r"(.+?),? sauf (?:a la |la )?pentecote", s):
        inner = when(m.group(1))
        if inner is None:
            return None

        def unless_pentecost(year: int) -> dt.date | None:
            date = inner.on(year)
            pentecost = easter(year) + dt.timedelta(days=49)
            return date + dt.timedelta(days=7) if date == pentecost else date

        return When(unless_pentecost)
    if m := re.fullmatch(r"(\d{1,2})[-./](\d{1,2})(?:[-./](\d{4}))?", s):
        return _fixed(int(m.group(1)), int(m.group(2)), int(m.group(3)) if m.group(3) else None)
    if m := re.fullmatch(r"(.+?) (jours|jour|semaines|semaine) (avant|apres) (.+)", s):
        count = parse_num(m.group(1))
        inner = when(m.group(4))
        if count is None or not float(count).is_integer() or count < 0 or inner is None:
            return None
        delta = dt.timedelta(days=int(count) * (7 if m.group(2).startswith("s") else 1))
        delta = -delta if m.group(3) == "avant" else delta

        def shifted(year: int) -> dt.date | None:
            date = inner.on(year)
            return date + delta if date is not None else None

        return When(shifted, year=inner.year)
    if s in ("paques", "dimanche de paques"):
        return When(easter)
    if m := re.fullmatch(r"(\S+) (\S+) (?:de|d'|du mois de) ?(\S+)", s):
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
    if m := re.fullmatch(rf"(.+?) ({month_names})\.?(?: (.+))?", s):
        day, month = _day(m.group(1)), MONTHS[m.group(2)]
        year = _year(m.group(3)) if m.group(3) else None
        if day and (m.group(3) is None or year):
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
    """A holiday's name as looked up: "la Toussaint" -> "toussaint", "Jour de
    l'An" -> "jour de l an", "Pâques" -> "paques"."""
    s = re.sub(r"['\-]", " ", _plain(name))
    s = " ".join(re.sub(r"[,.]", " ", s).split())
    return re.sub(r"^(?:le|la|les|l) ", "", s)


_THE: dict[str, str] = {}
"""A built-in holiday's article, by its ``key``."""


def _h(name: str, on: str, *also: str) -> Holiday:
    """A holiday, each of its names given with its article ("la Toussaint")."""
    bare = []
    for given in (name, *also):
        found = re.match(r"(le |la |les |l')", given)
        if found:
            given = given[found.end():]
            _THE[key(given)] = found.group(1)
        bare.append(given)
    return Holiday(bare[0], on, tuple(bare[1:]))


def the(name: str) -> str:
    """A holiday's name as said in a sentence: "la Toussaint", "Noël"."""
    article = _THE.get(key(name))
    return f"{article}{name}" if article else name


HOLIDAYS = (
    _h("le Jour de l'an", "1er janvier", "le Nouvel An", "le Premier de l'an"),
    _h("l'Épiphanie", "6 janvier", "la Fête des rois"),
    _h("la Chandeleur", "2 février"),
    _h("la Saint-Valentin", "14 février"),
    _h("Mardi gras", "47 jours avant Pâques"),
    _h("le Vendredi saint", "2 jours avant Pâques"),
    _h("Pâques", "Pâques", "le Dimanche de Pâques"),
    _h("le Lundi de Pâques", "1 jour après Pâques"),
    _h("la Fête du Travail", "1er mai", "le Premier mai"),
    _h("la Victoire de 1945", "8 mai", "le 8 mai"),
    _h("l'Ascension", "39 jours après Pâques"),
    _h("la Pentecôte", "49 jours après Pâques"),
    _h("le Lundi de Pentecôte", "50 jours après Pâques"),
    _h("la Fête des mères", "dernier dimanche de mai, sauf Pentecôte"),
    _h("la Fête des pères", "troisième dimanche de juin"),
    _h("la Fête de la musique", "21 juin"),
    _h("la Fête nationale", "14 juillet", "le 14 juillet", "le Quatorze juillet"),
    _h("l'Assomption", "15 août"),
    _h("Halloween", "31 octobre"),
    _h("la Toussaint", "1er novembre"),
    _h("l'Armistice", "11 novembre", "le 11 novembre"),
    _h("la Saint-Nicolas", "6 décembre"),
    _h("le Réveillon de Noël", "24 décembre", "la Veille de Noël"),
    _h("Noël", "25 décembre"),
    _h("la Saint-Sylvestre", "31 décembre", "le Réveillon du Nouvel An"),
)
"""The built-in holidays (France)."""

WHEN_EXAMPLES = "25 décembre, deuxième dimanche de mai, dernier lundi de mai, Pâques, 2 jours avant Pâques, 20 juin 2027"


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
    """"25 décembre", "1er mai"; with its year when not this one, or ``year``."""
    said = f"{'1er' if date.day == 1 else date.day} {MONTH_NAMES[date.month - 1]}"
    return f"{said} {date.year}" if year or date.year != today.year else said


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


def solve(q, settings):
    s = clean(q)
    if m := (re.fullmatch(rf"(?:est ce que )?(?:l'année )?{N} (?:est|était|sera)(?: (?:il|elle))? (?:une )?(?:année )?bissextile", s)
             or re.fullmatch(rf"(?:est ce que )?(?:l'année )?{N} (?:est|était|sera) une année bissextile", s)):
        year = parse_num(m.group(1))
        if year is not None and float(year).is_integer():
            yes = leap(int(year))
            said = f"{int(year)} est une année bissextile" if yes else f"{int(year)} n'est pas une année bissextile"
            return ("Oui" if yes else "Non"), None, f"{'Oui' if yes else 'Non'}, {said}"
    days_in = r"combien (?:de jours|y a t il de jours) (?:y a t il |compte |a )?(?:dans |en )?"
    if re.fullmatch(rf"{days_in}(?:une )?année bissextile", s):
        return "366", "Ça fait 366 jours.", "Une année bissextile compte 366 jours"
    if re.fullmatch(rf"{days_in}(?:une|l') ?(?:année|an)(?: normale)?", s):
        return "365", "Ça fait 365 jours.", "Une année compte 365 jours"
    if m := re.fullmatch(rf"{days_in}(?:le mois de )?([a-zéû]+)(?: {N})?", s):
        month = MONTHS.get(_plain(m.group(1)))
        year = parse_num(m.group(2)) if m.group(2) else None
        if month and (year is None or 1 <= year <= 9999):
            name = MONTH_NAMES[month - 1]
            if month == 2 and year is None:
                return "28, 29 les années bissextiles", "28, et 29 les années bissextiles.", "Février compte 28 jours, et 29 les années bissextiles"
            days = calendar.monthrange(int(year or 2001), month)[1]
            said = f"{name} {int(year)}" if year else name
            return str(days), f"Ça fait {days} jours.", f"{_capital(said)} compte {days} jours"
    if m := re.fullmatch(r"combien (?:de|y a t il de) (mois|semaines|jours) (?:y a t il |compte |a )?(?:dans |en )?(?:une|l') ?(année|an|semaine)", s):
        unit, span = m.group(1), "année" if m.group(2) == "an" else m.group(2)
        found = {("mois", "année"): "12", ("semaines", "année"): "52", ("jours", "semaine"): "7", ("jours", "année"): "365"}.get((unit, span))
        if found:
            return found, f"Ça fait {found} {unit}.", f"Une {span} compte {found} {unit}"
    if m := (re.fullmatch(r"quel jour (?:de la semaine )?(?:est|était|sera|tombe|tombait|tombera|c'est|c'était|ce sera) (.+)", s)
             or re.fullmatch(r"(.+?),? (?:tombe|tombait|tombera|c'est|c'était|ce sera|est|était|sera) quel jour(?: de la semaine)?", s)
             or re.fullmatch(r"c'est quel jour(?: de la semaine)?,? (.+)", s)):
        asked = m.group(1)
        date = parse_date(asked)
        holiday = settings.holiday(asked) if date is None else None
        if date is None and holiday is not None:
            date = holiday.next(shared.today())
        if date:
            day = DAY_NAMES[date.weekday()]
            name = the(_named(holiday, asked)) if holiday else "le " + _said(date, shared.today(), year=True)
            return day, f"C'est un {day}.", f"{_capital(name)} tombe un {day}"
    if m := (re.fullmatch(r"(?:quand (?:est|tombe|a lieu|sera|c'est|est ce que c'est|est ce qu'on fête|fête t on|on fête)"
                          r"|c'est quand|quelle est la date (?:de|du|des|d')|à quelle date (?:est|tombe|a lieu|sera)) ?(.+)", s)
             or re.fullmatch(r"(.+?),? c'est quand", s)):
        asked = re.sub(r"^(?:le |la |les |l')", "", m.group(1))
        holiday = settings.holiday(asked)
        if holiday is not None:
            found, today = holiday.date, shared.today()
            if found.fixed:
                date = f"{'1er' if found.fixed[1] == 1 else found.fixed[1]} {MONTH_NAMES[found.fixed[0] - 1]}"
            elif found.year is not None:
                on = found.on(found.year)
                date = _said(on, today, year=True) if on else None
            else:
                on = holiday.next(today)
                date = _said(on, today) if on else None
            if date:
                return date, f"Le {date}.", f"{_capital(the(_named(holiday, asked)))} tombe le {date}"
    if m := (re.fullmatch(r"(?:il reste |dans )?combien de (jours|semaines|nuits|dodos) (?:(?:est ce qu')?il (?:nous )?reste |reste t il |encore |y a t il |)"
                          r"(?:avant|jusqu'à|jusqu'au|d'ici) (.+)", s)
             or re.fullmatch(r"combien de temps (?:(?:est ce qu')?il (?:nous )?reste |reste t il |encore |)(?:avant|jusqu'à|jusqu'au|d'ici) (.+)", s)):
        unit = m.group(1) if m.lastindex == 2 else "jours"
        target_text = m.group(m.lastindex)
        today = shared.today()
        target = _day_of(target_text, today, settings)
        if target is not None:
            days = (target - today).days
            if unit == "semaines":
                found = f"{days // 7} {'semaine' if days // 7 == 1 else 'semaines'}"
            else:
                found = f"{days} {'jour' if days == 1 else 'jours'}"
            holiday = settings.holiday(target_text)
            name = the(_named(holiday, target_text)) if holiday else "le " + _said(target, today)
            return found, f"Encore {found}.", f"Il reste {found} avant {name}"
    if m := re.fullmatch(r"(?:quel mois vient|quel est le mois|quel mois est) (après|avant) ([a-zéû]+)", s):
        month = MONTHS.get(_plain(m.group(2)))
        if month:
            found = MONTH_NAMES[month % 12 if m.group(1) == "après" else (month - 2) % 12]
            asked = MONTH_NAMES[month - 1]
            return found, f"C'est {found}.", f"Après {asked} vient {found}" if m.group(1) == "après" else f"Avant {asked} vient {found}"
    if m := re.fullmatch(r"(?:quel jour vient|quel est le jour|quel jour est) (après|avant) ([a-z]+)", s):
        day = DAYS.get(m.group(2))
        if day is not None:
            found = DAY_NAMES[(day + (1 if m.group(1) == "après" else -1)) % 7]
            return found, f"C'est {found}.", None
    if m := re.fullmatch(r"quel est le (premier|dernier) mois de l'année", s):
        found = "janvier" if m.group(1) == "premier" else "décembre"
        return found, f"C'est {found}.", None
    return None


def answer(text: str, settings: Settings):
    found = solve(text, settings)
    if not found:
        return None
    said, specific, restated = found
    return reply(said, text, restated, specific=specific)
