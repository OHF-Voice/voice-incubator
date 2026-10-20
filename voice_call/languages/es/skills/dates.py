"""How Spanish reads a date ("25 de diciembre", "25/12/2026", "primer domingo
de mayo", "2 días antes de Pascua"), asks about dates ("¿2024 es bisiesto?",
"¿cuándo es Navidad?", "¿cuántos días faltan para Navidad?") and says them;
and the holidays it knows (Spain's; voice_call/skills/dates.py). A holiday's
date may also be written as English writes it ("December 25"), as the UI is
in English.

Questions and holidays are read without their accents ("cuando es la
Asuncion"); what is said has them."""

from __future__ import annotations

import calendar
import datetime as dt
import re

from voice_call.languages.en.skills import dates as english
from voice_call.skills import dates as shared
from voice_call.skills.common import reply
from voice_call.skills.dates import Holiday, Settings, When, easter, leap

from .numbers import N, clean, fold, parse_num

EXAMPLES = ("es 2024 bisiesto", "cuándo es Navidad", "cuántos días faltan para Navidad")

MONTH_NAMES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
               "noviembre", "diciembre"]
DAY_NAMES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MONTHS = {m: i for i, m in enumerate(MONTH_NAMES, 1)} | {
    "setiembre": 9, "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8, "sep": 9,
    "sept": 9, "oct": 10, "nov": 11, "dic": 12,
}
DAYS = {fold(d): i for i, d in enumerate(DAY_NAMES)}

_ORDINALS = {
    "primero": 1, "primer": 1, "primera": 1, "segundo": 2, "segunda": 2, "tercero": 3, "tercer": 3, "tercera": 3,
    "cuarto": 4, "cuarta": 4, "quinto": 5, "quinta": 5, "ultimo": -1, "ultima": -1,
}
"""The nth weekday of a month: "primer domingo", "último lunes"."""


def ordinal(word: str) -> int | None:
    """A day of the month: "25", "1º", "primero", "veinticinco"."""
    word = fold(word.lower()).rstrip("º°ª.")
    if word in ("primero", "1o"):
        return 1
    value = parse_num(word)
    return int(value) if value is not None and float(value).is_integer() and 1 <= value <= 31 else None


def _year(text: str) -> int | None:
    value = parse_num(text)
    return int(value) if value is not None and float(value).is_integer() and 1 <= value <= 9999 else None


def when(text: str) -> When | None:
    """"25 de diciembre", "primer domingo de mayo", "último lunes de mayo",
    "Pascua", "2 días antes de Pascua", "1 semana después de ...", "20 de
    junio de 2027", "20/06/2027" -> when it falls -- or as English writes it
    ("December 25"); None when it can't be read."""
    s = " ".join(fold(re.sub(r",", " ", text.lower())).split())
    s = re.sub(r"^(?:el|la|los|las) ", "", s)
    if m := re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})(?:[-/.](\d{4}))?", s):
        return _fixed(int(m.group(1)), int(m.group(2)), int(m.group(3)) if m.group(3) else None)
    if m := re.fullmatch(r"(.+?) (dias|dia|semanas|semana) (antes|despues) (?:de|del) (?:la |el )?(.+)", s):
        count = parse_num(m.group(1))
        inner = when(m.group(4))
        if count is None or not float(count).is_integer() or count < 0 or inner is None:
            return None
        delta = dt.timedelta(days=int(count) * (7 if m.group(2).startswith("semana") else 1))
        delta = -delta if m.group(3) == "antes" else delta

        def shifted(year: int) -> dt.date | None:
            date = inner.on(year)
            return date + delta if date is not None else None

        return When(shifted, year=inner.year)
    if s in ("pascua", "domingo de pascua", "domingo de resurreccion", "pascua de resurreccion"):
        return When(easter)
    if m := re.fullmatch(r"(\S+) (\S+) (?:de|del mes de) (\S+)", s):
        nth, day, month = _ORDINALS.get(m.group(1)), DAYS.get(m.group(2)), MONTHS.get(m.group(3))
        if nth is not None and day is not None and month:

            def nth_weekday(year: int) -> dt.date | None:
                days = [d for d in calendar.Calendar().itermonthdates(year, month)
                        if d.month == month and d.weekday() == day]
                return days[-1] if nth == -1 else days[nth - 1] if nth <= len(days) else None

            return When(nth_weekday)
    if m := re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", s):
        return _fixed(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    if m := re.fullmatch(r"(.+?) de ([a-z]+)(?: de(?:l ano)? (.+))?", s):
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
    """A holiday's name as looked up: "el Día de la Madre" -> "dia de la
    madre", "San Valentín" -> "san valentin"."""
    s = fold(name.lower()).replace("'", "").replace("-", " ")
    s = " ".join(re.sub(r"[,.]", " ", s).split())
    return re.sub(r"^(?:el|la|los|las) ", "", s)


def _h(name: str, on: str, *also: str) -> Holiday:
    return Holiday(name, on, also)


HOLIDAYS = (
    _h("Año Nuevo", "1 de enero", "Día de Año Nuevo"),
    _h("Reyes", "6 de enero", "Día de Reyes", "Reyes Magos", "Epifanía"),
    _h("San Valentín", "14 de febrero", "Día de los Enamorados"),
    _h("Carnaval", "47 días antes de Pascua", "Martes de Carnaval"),
    _h("Miércoles de Ceniza", "46 días antes de Pascua"),
    _h("Día del Padre", "19 de marzo", "San José"),
    _h("Domingo de Ramos", "7 días antes de Pascua", "Semana Santa"),
    _h("Jueves Santo", "3 días antes de Pascua"),
    _h("Viernes Santo", "2 días antes de Pascua"),
    _h("Domingo de Resurrección", "Pascua", "Pascua", "Domingo de Pascua", "Pascua de Resurrección"),
    _h("Lunes de Pascua", "1 día después de Pascua"),
    _h("Día del Libro", "23 de abril", "Sant Jordi"),
    _h("Día del Trabajador", "1 de mayo", "Día del Trabajo", "Primero de Mayo"),
    _h("Día de la Madre", "primer domingo de mayo"),
    _h("San Juan", "24 de junio", "Día de San Juan"),
    _h("Asunción", "15 de agosto", "Asunción de la Virgen", "Día de la Asunción"),
    _h("Fiesta Nacional", "12 de octubre", "Día de la Hispanidad", "Día del Pilar", "El Pilar"),
    _h("Halloween", "31 de octubre"),
    _h("Todos los Santos", "1 de noviembre", "Día de Todos los Santos"),
    _h("Día de la Constitución", "6 de diciembre"),
    _h("Inmaculada Concepción", "8 de diciembre", "Inmaculada", "Día de la Inmaculada"),
    _h("Nochebuena", "24 de diciembre"),
    _h("Navidad", "25 de diciembre", "Día de Navidad"),
    _h("Día de los Inocentes", "28 de diciembre", "Santos Inocentes"),
    _h("Nochevieja", "31 de diciembre", "Fin de Año"),
)
"""The built-in holidays (Spain)."""

WHEN_EXAMPLES = "25 de diciembre, primer domingo de mayo, último lunes de mayo, Pascua, 2 días antes de Pascua, 20 de junio de 2027"

_STARTS = {"semana santa"}
"""Asked when these are, a week's first day is said as when it starts."""


def parse_date(s):
    """A date with its year: "el 4 de julio de 2026", "04/07/2026"; None."""
    found = when(s)
    if found is None or found.year is None:
        return None
    return found.on(found.year)


def _named(holiday: Holiday, asked: str) -> str:
    """The holiday by the name it was asked by: "Navidad", not "Día de
    Navidad"."""
    return next((name for name in (holiday.name, *holiday.also) if key(name) == key(asked)), holiday.name)


_ARTICLE = {
    **dict.fromkeys(["dia", "domingo", "lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "carnaval",
                     "cumpleanos", "aniversario", "santo", "puente", "primero"], "el"),
    **dict.fromkeys(["fiesta", "semana", "asuncion", "inmaculada", "noche", "boda", "verbena"], "la"),
}
"""The article a holiday's name takes in a sentence, by its first word: "el
Día de la Madre", "la Semana Santa"; "Navidad", "Nochebuena" take none."""


def _the(name: str) -> str:
    """A holiday's name as said in a sentence: "el Día de la Madre"."""
    article = _ARTICLE.get(fold(name.split()[0].lower())) if name.split() else None
    return f"{article} {name}" if article and not re.match(r"(?:el|la|los|las) ", name.lower()) else name


def _day_of(text, today, settings):
    """The next date a holiday or "25 de diciembre" falls on, from ``today``."""
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
    """"25 de diciembre"; with its year when not this one, or ``year``."""
    said = f"{date.day} de {MONTH_NAMES[date.month - 1]}"
    return f"{said} de {date.year}" if year or date.year != today.year else said


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


def solve(q, settings):
    """The answer, the sentence it is said in, and with the question
    ("Navidad es el 25 de diciembre"); None."""
    s = fold(clean(q))
    if m := (re.fullmatch(rf"(?:es|fue|sera) (?:el ano )?{N} (?:un )?(?:ano )?bisiesto", s)
             or re.fullmatch(rf"(?:el ano )?{N} (?:es|fue|sera) (?:un )?(?:ano )?bisiesto", s)):
        year = parse_num(m.group(1))
        if year is not None and float(year).is_integer():
            yes = leap(int(year))
            return ("Sí" if yes else "No"), None, f"{'Sí' if yes else 'No'}, {int(year)} {'es' if yes else 'no es'} bisiesto"
    if re.fullmatch(r"cuantos dias (?:tiene|hay en) un ano bisiesto", s):
        return "366", "Son 366 días.", "Un año bisiesto tiene 366 días"
    if re.fullmatch(r"cuantos dias (?:tiene|hay en) un ano(?: normal| no bisiesto)?", s):
        return "365", "Son 365 días.", "Un año tiene 365 días"
    if m := re.fullmatch(rf"cuantos dias (?:tiene|hay en) (?:el mes de )?([a-z]+)(?: (?:de|del ano) {N})?", s):
        month = MONTHS.get(m.group(1))
        year = parse_num(m.group(2)) if m.group(2) else None
        if month and (year is None or 1 <= year <= 9999):
            name = MONTH_NAMES[month - 1]
            if month == 2 and year is None:
                return "28, o 29 en un año bisiesto", "28, o 29 en un año bisiesto.", "Febrero tiene 28 días, o 29 en un año bisiesto"
            days = calendar.monthrange(int(year or 2001), month)[1]
            said = f"{name} de {int(year)}" if year else name
            return str(days), f"Son {days} días.", f"{_capital(said)} tiene {days} días"
    if m := re.fullmatch(r"cuant(?:os|as) (meses|semanas|dias) (?:tiene|hay en) (?:un|una) (ano|semana)", s):
        found = {("meses", "ano"): "12", ("semanas", "ano"): "52", ("dias", "semana"): "7", ("dias", "ano"): "365"}.get(m.groups())
        if found:
            what = {"meses": "meses", "semanas": "semanas", "dias": "días"}[m.group(1)]
            of = "Un año" if m.group(2) == "ano" else "Una semana"
            return found, f"Son {found} {what}.", f"{of} tiene {found} {what}"
    if m := re.fullmatch(r"que (mes|dia) (?:va|viene|es|hay) (despues|antes) (?:de|del) (?:el |la )?([a-z]+)", s):
        if m.group(1) == "mes" and (month := MONTHS.get(m.group(3))):
            found = MONTH_NAMES[month % 12 if m.group(2) == "despues" else (month - 2) % 12]
            said = "Después" if m.group(2) == "despues" else "Antes"
            return found, f"Es {found}.", f"{said} de {MONTH_NAMES[month - 1]} va {found}"
        if m.group(1) == "dia" and (day := DAYS.get(m.group(3))) is not None:
            found = DAY_NAMES[(day + (1 if m.group(2) == "despues" else -1)) % 7]
            said = "Después" if m.group(2) == "despues" else "Antes"
            return found, f"Es {found}.", f"{said} del {DAY_NAMES[day]} va el {found}"
    if m := re.fullmatch(r"cual es el (primer|ultimo) mes del ano", s):
        found = "enero" if m.group(1) == "primer" else "diciembre"
        return found, f"Es {found}.", f"El {'primer' if found == 'enero' else 'último'} mes del año es {found}"
    # The day of the week a date or holiday falls on.
    if m := (re.fullmatch(r"(?:que|en que) dia(?: de la semana)? (es|fue|sera|cae|caera|cayo) (?:el |la )?(.+)", s)):
        asked = m.group(2)
        date = parse_date(asked)
        holiday = settings.holiday(asked) if date is None else None
        weekday = "semana" in s or m.group(1).startswith("ca")
        if date is None and weekday:
            # A holiday, or a date without its year: the next time it comes.
            date = _day_of(asked, shared.today(), settings)
        if date:
            day = DAY_NAMES[date.weekday()]
            name = _the(_named(holiday, asked)) if holiday else f"el {_said(date, shared.today(), year=parse_date(asked) is not None)}"
            return day, f"Es {day}.", f"{_capital(name)} {'cae en' if holiday else 'es'} {day}"
    # A holiday's date.
    if m := (re.fullmatch(r"cuando (?:es|sera|cae|caera|se celebra|celebramos|empieza|comienza) (?:el |la |los |las )?(.+)", s)
             or re.fullmatch(r"(?:que|en que) (?:dia|fecha) (?:es|sera|cae|se celebra) (?:el |la |los |las )?(.+)", s)):
        holiday = settings.holiday(m.group(1))
        if holiday is not None:
            found, today = holiday.date, shared.today()
            if found.fixed:
                date = f"{found.fixed[1]} de {MONTH_NAMES[found.fixed[0] - 1]}"
            elif found.year is not None:
                on = found.on(found.year)
                date = _said(on, today, year=True) if on else None
            else:
                on = holiday.next(today)
                date = _said(on, today) if on else None
            if date:
                verb = "empieza" if key(m.group(1)) in _STARTS else "es"
                return date, f"El {date}.", f"{_capital(_the(_named(holiday, m.group(1))))} {verb} el {date}"
    # Days until a holiday or a date.
    if m := (re.fullmatch(r"cuant(os|as) (dias|semanas|noches) (?:faltan|quedan) (?:para|hasta) (?:el |la |los |las )?(.+)", s)
             or re.fullmatch(r"cuanto (?:tiempo )?(?:falta|queda) (?:para|hasta) (?:el |la |los |las )?(.+)", s)):
        unit = m.group(2) if m.lastindex == 3 else "dias"
        target_text = m.group(m.lastindex)
        today = shared.today()
        target = _day_of(target_text, today, settings)
        if target is not None:
            days = (target - today).days
            count = days // 7 if unit == "semanas" else days
            what = ("semana" if count == 1 else "semanas") if unit == "semanas" else ("día" if count == 1 else "días")
            found = f"{count} {what}"
            verb = "Falta" if count == 1 else "Faltan"
            holiday = settings.holiday(target_text)
            name = _the(_named(holiday, target_text)) if holiday else f"el {_said(target, today)}"
            return found, f"{verb} {found}.", f"{verb} {found} para {name}"
    return None


def answer(text: str, settings: Settings):
    found = solve(text, settings)
    if not found:
        return None
    said, specific, restated = found
    return reply(said, text, restated, brief=f"{_capital(said)}.", specific=specific)
