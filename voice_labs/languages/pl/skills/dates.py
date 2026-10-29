"""How Polish reads a date ("25 grudnia", "25.12.2026", "pierwsza niedziela
maja", "2 dni przed Wielkanocą"), asks about dates ("czy 2024 jest
przestępny?", "kiedy jest Boże Narodzenie?", "ile dni zostało do
Wigilii?") and says them; and the holidays it knows (Poland's;
voice_labs/skills/dates.py). A holiday's date may also be written as English
writes it ("December 25"), as the UI is in English.

A date is said as Polish says it, its day and year by their ordinals in the
genitive: "dwudziestego piątego grudnia dwa tysiące dwudziestego siódmego
roku" -- a voice reads digits as counting numbers. A holiday is asked by its
name in any case ("do Bożego Narodzenia", "w Wigilię"): names are looked up
by their stems (``key``), and said in the nominative ("Boże Narodzenie jest
za 71 dni"). Questions are read folded (numbers.fold)."""

from __future__ import annotations

import calendar
import datetime as dt
import re

from voice_labs.languages.en.skills import dates as english
from voice_labs.skills import dates as shared
from voice_labs.skills.common import reply
from voice_labs.skills.dates import Holiday, Settings, When, easter, leap

from .numbers import N, clean, fold, ordinal, parse_num, plural, read_ordinal

EXAMPLES = ("czy 2024 jest przestępny", "kiedy jest Boże Narodzenie", "ile dni zostało do Bożego Narodzenia")

MONTH_NAMES = ["styczeń", "luty", "marzec", "kwiecień", "maj", "czerwiec", "lipiec", "sierpień", "wrzesień",
               "październik", "listopad", "grudzień"]
MONTH_OF = ["stycznia", "lutego", "marca", "kwietnia", "maja", "czerwca", "lipca", "sierpnia", "września",
            "października", "listopada", "grudnia"]
"""A month in a date: "25 grudnia"."""
_MONTH_FORMS = [
    "styczen stycznia styczniu styczniem sty", "luty lutego lutym lut", "marzec marca marcu marcem mar",
    "kwiecien kwietnia kwietniu kwietniem kwi", "maj maja maju majem", "czerwiec czerwca czerwcu czerwcem cze",
    "lipiec lipca lipcu lipcem lip", "sierpien sierpnia sierpniu sierpniem sie", "wrzesien wrzesnia wrzesniu wrzesniem wrz",
    "pazdziernik pazdziernika pazdzierniku pazdziernikiem paz", "listopad listopada listopadzie listopadem lis",
    "grudzien grudnia grudniu grudniem gru",
]
MONTHS = {form: index for index, forms in enumerate(_MONTH_FORMS, 1) for form in forms.split()}
"""Every case of a month (folded): "grudnia", "w grudniu", "przed grudniem"."""

DAY_NAMES = ["poniedziałek", "wtorek", "środa", "czwartek", "piątek", "sobota", "niedziela"]
_DAY_ON = ["poniedziałek", "wtorek", "środę", "czwartek", "piątek", "sobotę", "niedzielę"]
"""A weekday after "w": "w sobotę"."""
_DAY_FORMS = [
    "poniedzialek poniedzialku poniedzialkiem", "wtorek wtorku wtorkiem", "sroda srode srody srodzie sroda",
    "czwartek czwartku czwartkiem", "piatek piatku piatkiem", "sobota sobote soboty sobocie",
    "niedziela niedziele niedzieli niedziela",
]
DAYS = {form: index for index, forms in enumerate(_DAY_FORMS) for form in forms.split()}
"""Every case of a weekday (folded): "środa", "w środę", "po środzie"."""


def _ordinal_of(word: str) -> int | None:
    """The nth weekday of a month: "pierwsza", "drugi", "ostatnia" (-1)."""
    if re.fullmatch(r"ostatni\w*", word):
        return -1
    value = read_ordinal(word)
    return value if value is not None and value <= 5 else None


def day_of_month(text: str) -> int | None:
    """A day of the month: "25", "25.", "dwudziesty piąty", "dwudziestego
    piątego"."""
    text = fold(text.lower()).strip().rstrip(".")
    value = int(text) if text.isdigit() else read_ordinal(text)
    return value if value is not None and 1 <= value <= 31 else None


def _year(text: str) -> int | None:
    text = re.sub(r"\s+(?:r|roku|rok)$", "", text.strip().rstrip("."))
    value = parse_num(text)
    return int(value) if value is not None and float(value).is_integer() and 1 <= value <= 9999 else None


_EASTER = {"wielkanoc", "wielkanocy", "wielkanoca", "niedziela wielkanocna", "niedzieli wielkanocnej", "wielka niedziela",
           "wielkiej niedzieli"}


def when(text: str) -> When | None:
    """"25 grudnia", "pierwsza niedziela maja", "ostatni poniedziałek maja",
    "Wielkanoc", "2 dni przed Wielkanocą", "1 dzień po Wielkanocy", "tydzień
    po ...", "20 czerwca 2027", "20.06.2027" -> when it falls -- or as
    English writes it ("December 25"); None when it can't be read."""
    s = " ".join(fold(re.sub(r",", " ", text.lower())).split())
    if m := re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})(?:[-/.](\d{4}))?\.?(?: r\.?| roku)?", s):
        return _fixed(int(m.group(1)), int(m.group(2)), int(m.group(3)) if m.group(3) else None)
    if m := re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", s):
        return _fixed(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    if m := re.fullmatch(r"(?:(.+?) )?(dni|dzien|dnia|tydzien|tygodnie|tygodni|tygodnia) (przed|po) (.+)", s):
        number = parse_num(m.group(1)) if m.group(1) else 1
        inner = when(m.group(4))
        if number is None or not float(number).is_integer() or number < 0 or inner is None:
            return None
        delta = dt.timedelta(days=int(number) * (7 if m.group(2).startswith("tydz") or m.group(2).startswith("tyg") else 1))
        delta = -delta if m.group(3) == "przed" else delta

        def shifted(year: int) -> dt.date | None:
            date = inner.on(year)
            return date + delta if date is not None else None

        return When(shifted, year=inner.year)
    if s in _EASTER:
        return When(easter)
    if m := re.fullmatch(r"(\S+) (\S+) (?:w )?(\S+)", s):
        nth, day, month = _ordinal_of(m.group(1)), DAYS.get(m.group(2)), MONTHS.get(m.group(3))
        if nth is not None and day is not None and month:

            def nth_weekday(year: int) -> dt.date | None:
                days = [d for d in calendar.Calendar().itermonthdates(year, month)
                        if d.month == month and d.weekday() == day]
                return days[-1] if nth == -1 else days[nth - 1] if nth <= len(days) else None

            return When(nth_weekday)
    if m := re.fullmatch(r"(.+?) ([a-z]+)(?: (.+))?", s):
        day, month = day_of_month(m.group(1)), MONTHS.get(m.group(2))
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


_IRREGULAR = {
    "dnia": "dzien", "dniu": "dzien", "dni": "dzien", "czwartku": "czwartek", "piatku": "piatek",
    "poniedzialku": "poniedzialek", "sylwestra": "sylwester", "popielca": "popielec", "walentynek": "walentynki",
    "andrzejek": "andrzejki", "mikolajek": "mikolajki", "swiatek": "swiatki", "zaduszek": "zaduszki", "swiat": "swieta",
}
"""Words whose cases change their stem ("Dzień", "Dnia"; "Świąt"): as the
nominative."""

_ENDINGS = re.compile(r"(?:iego|ego|iemu|emu|ich|ych|imi|ymi|iej|ej|ami|ach|om|ow|im|ym|ii|ia|ie|a|e|i|y|o|u)$")


def key(name: str) -> str:
    """A holiday's name as looked up: folded, each word by its stem, so a
    case of it is the same ("Boże Narodzenie", "Bożego Narodzenia" -> "boz
    narodzen"; "Wigilia", "Wigilii", "Wigilię" -> "wigil")."""
    words = re.sub(r"[,.'\-]", " ", fold(name.lower())).split()
    out = []
    for word in words:
        word = _IRREGULAR.get(word, word)
        stem = _ENDINGS.sub("", word)
        out.append(stem if len(stem) >= 3 else word)
    return " ".join(out)


def _h(name: str, on: str, *also: str) -> Holiday:
    return Holiday(name, on, also)


HOLIDAYS = (
    _h("Nowy Rok", "1 stycznia"),
    _h("Trzech Króli", "6 stycznia", "Święto Trzech Króli", "Objawienie Pańskie"),
    _h("Dzień Babci", "21 stycznia"),
    _h("Dzień Dziadka", "22 stycznia"),
    _h("Tłusty Czwartek", "52 dni przed Wielkanocą"),
    _h("Walentynki", "14 lutego", "Dzień Zakochanych", "Święto Zakochanych"),
    _h("Popielec", "46 dni przed Wielkanocą", "Środa Popielcowa"),
    _h("Dzień Kobiet", "8 marca"),
    _h("Prima Aprilis", "1 kwietnia"),
    _h("Niedziela Palmowa", "7 dni przed Wielkanocą"),
    _h("Wielki Piątek", "2 dni przed Wielkanocą"),
    _h("Wielkanoc", "Wielkanoc", "Niedziela Wielkanocna", "Święta Wielkanocne", "Wielka Niedziela"),
    _h("Poniedziałek Wielkanocny", "1 dzień po Wielkanocy", "Lany Poniedziałek", "Śmigus-dyngus"),
    _h("Święto Pracy", "1 maja", "Pierwszy Maja"),
    _h("Święto Konstytucji 3 Maja", "3 maja", "Święto Konstytucji", "Trzeci Maja"),
    _h("Zielone Świątki", "49 dni po Wielkanocy", "Zesłanie Ducha Świętego"),
    _h("Boże Ciało", "60 dni po Wielkanocy"),
    _h("Dzień Matki", "26 maja"),
    _h("Dzień Dziecka", "1 czerwca"),
    _h("Dzień Ojca", "23 czerwca"),
    _h("Wniebowzięcie", "15 sierpnia", "Wniebowzięcie Najświętszej Maryi Panny", "Matki Boskiej Zielnej",
       "Święto Wojska Polskiego"),
    _h("Halloween", "31 października"),
    _h("Wszystkich Świętych", "1 listopada", "Dzień Wszystkich Świętych"),
    _h("Zaduszki", "2 listopada", "Dzień Zaduszny"),
    _h("Narodowe Święto Niepodległości", "11 listopada", "Święto Niepodległości"),
    _h("Andrzejki", "29 listopada"),
    _h("Mikołajki", "6 grudnia", "Dzień Świętego Mikołaja"),
    _h("Wigilia", "24 grudnia", "Wigilia Bożego Narodzenia"),
    _h("Boże Narodzenie", "25 grudnia", "Gwiazdka", "Święta", "Pierwszy dzień świąt"),
    _h("Drugi dzień świąt", "26 grudnia", "Drugi dzień Bożego Narodzenia"),
    _h("Sylwester", "31 grudnia"),
)
"""The built-in holidays (Poland)."""

WHEN_EXAMPLES = "25 grudnia, pierwsza niedziela maja, ostatni piątek listopada, Wielkanoc, 2 dni przed Wielkanocą, 20 czerwca 2027"


def parse_date(s):
    """A date with its year: "4 lipca 2026", "czwartego lipca 2026 roku",
    "04.07.2026"; None."""
    found = when(s)
    if found is None or found.year is None:
        return None
    return found.on(found.year)


def _named(holiday: Holiday, asked: str) -> str:
    """The holiday by the name it was asked by, in the nominative:
    "Gwiazdka", not "Boże Narodzenie"."""
    return next((name for name in (holiday.name, *holiday.also) if key(name) == key(asked)), holiday.name)


def _are(name: str) -> str:
    """"jest", or "są" for a name in the plural: "Andrzejki są", "Zielone
    Świątki są"."""
    words = fold(name.lower()).split()
    plural = words and (words[0] in ("swieta", "zielone") or (len(words) == 1 and words[0].endswith("ki")))
    return "są" if plural else "jest"


def _day_of(text, today, settings):
    """The next date a holiday or "25 grudnia" falls on, from ``today``."""
    holiday = settings.holiday(text)
    if holiday is not None:
        return holiday.next(today)
    found = when(text)
    if found is None:
        return None
    if found.year is not None:
        return found.on(found.year)
    if found.fixed is None:
        return None
    for year in (today.year, today.year + 1):
        date = found.on(year)
        if date is not None and date >= today:
            return date
    return None


def _said(date: dt.date, today: dt.date, year: bool = False) -> str:
    """"dwudziestego piątego grudnia" (on that day); with its year when not
    this one, or ``year``: "... dwa tysiące dwudziestego siódmego roku"."""
    said = f"{ordinal(date.day, 'gen')} {MONTH_OF[date.month - 1]}"
    return f"{said} {ordinal(date.year, 'gen')} roku" if year or date.year != today.year else said


def _on(weekday: int) -> str:
    """"w sobotę", "we wtorek"."""
    return f"{'we' if weekday == 1 else 'w'} {_DAY_ON[weekday]}"


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


def solve(q, settings):
    """The answer, the sentence it is said in, and with the question
    ("Boże Narodzenie jest dwudziestego piątego grudnia"); None."""
    said_text = clean(q)
    s = fold(said_text)
    if m := (re.fullmatch(rf"(?:czy )?(?:rok )?{N} (?:rok )?(?:jest|byl|bedzie|to)? ?(?:rokiem |rok )?przestepn\w*", s)
             or re.fullmatch(rf"czy (?:rok )?{N} (?:rok )?(?:jest|byl|bedzie|to) (?:rokiem |rok )?przestepn\w*", s)):
        year = parse_num(m.group(1))
        if year is not None and float(year).is_integer():
            yes = leap(int(year))
            return ("Tak" if yes else "Nie"), None, f"{'Tak' if yes else 'Nie'}, rok {int(year)} {'jest' if yes else 'nie jest'} przestępny"
    if re.fullmatch(r"ile dni (?:ma|liczy|jest w) (?:roku przestepnym|rok przestepny)", s):
        return "366", "To 366 dni.", "Rok przestępny ma 366 dni"
    if re.fullmatch(r"ile dni (?:ma|liczy|jest w) (?:roku|rok)(?: zwyklym| nieprzestepnym| zwykly| nieprzestepny)?", s):
        return "365", "To 365 dni.", "Rok ma 365 dni"
    if m := re.fullmatch(rf"ile dni (?:ma|liczy|jest w) (?:miesiac |miesiacu )?([a-z]+)(?: (?:roku |w roku )?{N}(?: roku| r)?)?", s):
        month = MONTHS.get(m.group(1))
        year = parse_num(m.group(2)) if m.group(2) else None
        if month and (year is None or 1 <= year <= 9999):
            name = _capital(MONTH_NAMES[month - 1])
            if month == 2 and year is None:
                return ("28 dni, a w roku przestępnym 29", "28 dni, a w roku przestępnym 29.",
                        "Luty ma 28 dni, a w roku przestępnym 29")
            days = calendar.monthrange(int(year or 2001), month)[1]
            said = f"{name} w roku {int(year)}" if year else name
            return f"{days} dni", f"To {days} dni.", f"{said} ma {days} dni"
    if m := re.fullmatch(r"ile (miesiecy|tygodni|dni) (?:ma|liczy|jest w) (rok|roku|tydzien|tygodniu)", s):
        found = {("miesiecy", "rok"): "12", ("tygodni", "rok"): "52", ("dni", "tydz"): "7", ("dni", "rok"): "365"}.get(
            (m.group(1), m.group(2)[:4] if m.group(2).startswith("tydz") else m.group(2)[:3]))
        if found:
            forms = {"miesiecy": ("miesiąc", "miesiące", "miesięcy"), "tygodni": ("tydzień", "tygodnie", "tygodni"),
                     "dni": ("dzień", "dni", "dni")}[m.group(1)]
            what = plural(int(found), *forms)
            of = "Rok" if m.group(2).startswith("rok") else "Tydzień"
            return f"{found} {what}", f"To {found} {what}.", f"{of} ma {found} {what}"
    if m := re.fullmatch(r"(?:jaki|ktory|co) (miesiac|dzien(?: tygodnia)?)? ?(?:jest|bedzie|nastepuje|wypada|przypada)? ?((po|przed) ([a-z]+))", s):
        asked = said_text[m.start(2):m.end(2)]
        if m.group(1) != "miesiac" and (day := DAYS.get(m.group(4))) is not None:
            found = DAY_NAMES[(day + (1 if m.group(3) == "po" else -1)) % 7]
            return found, f"To {found}.", f"{_capital(asked)} jest {found}"
        if m.group(1) in (None, "miesiac") and (month := MONTHS.get(m.group(4))):
            found = MONTH_NAMES[month % 12 if m.group(3) == "po" else (month - 2) % 12]
            return found, f"To {found}.", f"{_capital(asked)} jest {found}"
    if m := re.fullmatch(r"(?:jaki|ktory) (?:jest )?(pierwszy|ostatni) miesiac (?:w )?roku", s):
        found = "styczeń" if m.group(1) == "pierwszy" else "grudzień"
        return found, f"To {found}.", f"{_capital(m.group(1))} miesiąc roku to {found}"
    # The day of the week a date or holiday falls on.
    if m := (re.fullmatch(r"(?:w )?(?:jaki|ktory|jakim) (?:dzien|dniem)(?: tygodnia)? (?:jest|byl|bedzie|wypada|wypadnie"
                          r"|wypadl|wypadla|wypadlo|przypada|przypadnie) (?:w )?(.+)", s)):
        asked = said_text[m.start(1):m.end(1)]
        date = parse_date(asked)
        holiday = settings.holiday(asked) if date is None else None
        today = shared.today()
        if date is None:
            # A holiday, or a date without its year: the next time it comes.
            date = _day_of(asked, today, settings)
        if date:
            day = DAY_NAMES[date.weekday()]
            if holiday:
                return day, f"To {day}.", f"{_named(holiday, asked)} wypada {_on(date.weekday())}"
            return day, f"To {day}.", f"{_capital(_said(date, today, year=parse_date(asked) is not None))} jest {day}"
    # A holiday's date.
    if m := (re.fullmatch(r"kiedy (?:jest|sa|bedzie|beda|wypada|wypadaja|wypadnie|przypada|przypadaja|obchodzimy|swietujemy"
                          r"|mamy|obchodzi sie) (.+)", s)
             or re.fullmatch(r"(?:ktorego|jakiego dnia) (?:jest|sa|bedzie|wypada|przypada|obchodzimy) (.+)", s)):
        asked = said_text[m.start(1):m.end(1)]
        holiday = settings.holiday(asked)
        if holiday is not None:
            found, today = holiday.date, shared.today()
            if found.fixed:
                date = f"{ordinal(found.fixed[1], 'gen')} {MONTH_OF[found.fixed[0] - 1]}"
            elif found.year is not None:
                on = found.on(found.year)
                date = _said(on, today, year=True) if on else None
            else:
                on = holiday.next(today)
                date = _said(on, today) if on else None
            if date:
                name = _named(holiday, asked)
                return date, f"{_capital(date)}.", f"{name} {_are(name)} {date}"
    # Days until a holiday or a date.
    if m := (re.fullmatch(r"ile (?:jeszcze )?(dni|tygodni) (?:jeszcze )?(?:zostalo |pozostalo |zostaje |jest |brakuje )?"
                          r"(?:jeszcze )?do (.+)", s)
             or re.fullmatch(r"za ile (dni|tygodni) (?:jest|sa|bedzie|beda|wypada|wypadaja|mamy) (.+)", s)
             or re.fullmatch(r"(?:ile (?:czasu|jeszcze) (?:zostalo |pozostalo )?|jak dlugo (?:jeszcze )?)()do (.+)", s)):
        unit = m.group(1) or "dni"
        asked = said_text[m.start(2):m.end(2)]
        today = shared.today()
        target = _day_of(asked, today, settings)
        if target is not None and target >= today:
            days = (target - today).days
            number = days // 7 if unit == "tygodni" else days
            forms = ("tydzień", "tygodnie", "tygodni") if unit == "tygodni" else ("dzień", "dni", "dni")
            found = f"{number} {plural(number, *forms)}"
            left = {"one": "Został", "few": "Zostały", "many": "Zostało"}[plural(number, "one", "few", "many")]
            holiday = settings.holiday(asked)
            if holiday:
                name = _named(holiday, asked)
                restated = f"{name} {_are(name)} {'dzisiaj' if not days else 'za ' + found}"
            else:
                restated = f"Do {_said(target, today)} {left.lower()} {found}"
            return found, f"{left} {found}.", restated
    return None


def answer(text: str, settings: Settings):
    found = solve(text, settings)
    if not found:
        return None
    said, specific, restated = found
    return reply(said, text, restated, brief=f"{_capital(said)}.", specific=specific)
