"""How Russian reads a date ("7 января", "седьмое января", "07.01.2027",
"последнее воскресенье ноября", "49 дней до Пасхи"), asks about dates
("2024 год високосный?", "когда Масленица?", "сколько дней до Нового
года?") and says them; and the holidays it knows (Russia's;
voice_labs/skills/dates.py). A holiday's date may also be written as English
writes it ("December 25"), as the UI is in English.

Easter is the Orthodox one (the Julian computus, 13 days on in the
Gregorian calendar till 2099), and so are the feasts by it: Масленица,
Вербное воскресенье, Троица; "католическая Пасха" is the Western one. A
holiday is asked in any case ("до Нового года", "к Масленице"): names are
looked up by their words' stems (``key``), and said back in the case they
were asked in. A date is said in words ("седьмое января"), as a voice
reads "7 января" as "семь января"."""

from __future__ import annotations

import calendar
import datetime as dt
import re

from voice_labs.languages.en.skills import dates as english
from voice_labs.skills import dates as shared
from voice_labs.skills.common import reply
from voice_labs.skills.dates import Holiday, Settings, When, easter, leap

from ..numerals import ordinal, plural
from ..speech import count
from .numbers import N, clean, fold, parse_num

EXAMPLES = ("2024 год високосный", "когда Масленица", "сколько дней до Нового года")

_MONTHS = (
    # nominative, genitive, prepositional, instrumental
    ("январь", "января", "январе", "январём"), ("февраль", "февраля", "феврале", "февралём"),
    ("март", "марта", "марте", "мартом"), ("апрель", "апреля", "апреле", "апрелем"), ("май", "мая", "мае", "маем"),
    ("июнь", "июня", "июне", "июнем"), ("июль", "июля", "июле", "июлем"), ("август", "августа", "августе", "августом"),
    ("сентябрь", "сентября", "сентябре", "сентябрём"), ("октябрь", "октября", "октябре", "октябрём"),
    ("ноябрь", "ноября", "ноябре", "ноябрём"), ("декабрь", "декабря", "декабре", "декабрём"),
)
MONTHS = {fold(form): i for i, forms in enumerate(_MONTHS, 1) for form in forms} | {
    "янв": 1, "фев": 2, "февр": 2, "мар": 3, "апр": 4, "июн": 6, "июл": 7, "авг": 8, "сен": 9, "сент": 9, "окт": 10,
    "ноя": 11, "нояб": 11, "дек": 12,
}
_DAYS = (
    # nominative, accusative, genitive, instrumental, gender
    ("понедельник", "понедельник", "понедельника", "понедельником", "m"),
    ("вторник", "вторник", "вторника", "вторником", "m"),
    ("среда", "среду", "среды", "средой", "f"),
    ("четверг", "четверг", "четверга", "четвергом", "m"),
    ("пятница", "пятницу", "пятницы", "пятницей", "f"),
    ("суббота", "субботу", "субботы", "субботой", "f"),
    ("воскресенье", "воскресенье", "воскресенья", "воскресеньем", "n"),
)
DAYS = {form: i for i, forms in enumerate(_DAYS) for form in forms[:4]}

_NTH = {
    **dict.fromkeys(("первый", "первая", "первое", "первую", "первого", "первой", "первом"), 1),
    **dict.fromkeys(("второй", "вторая", "второе", "вторую", "второго", "втором"), 2),
    **dict.fromkeys(("третий", "третья", "третье", "третью", "третьего", "третьей", "третьем"), 3),
    **dict.fromkeys(("четвертый", "четвертая", "четвертое", "четвертую", "четвертого", "четвертой", "четвертом"), 4),
    **dict.fromkeys(("пятый", "пятая", "пятое", "пятую", "пятого", "пятой", "пятом"), 5),
    **dict.fromkeys(("последний", "последняя", "последнее", "последнюю", "последнего", "последней", "последнем"), -1),
}
"""The nth weekday of a month: "первое воскресенье", "последний понедельник"."""

_DAY_ORDINALS = {fold(ordinal(n, gender, case)): n for n in range(1, 32) for gender in ("n", "m")
                 for case in ("nom", "gen", "prep", "acc")}
"""A day of the month in words: "первое", "двадцать пятого"."""


def day_number(text: str) -> int | None:
    """A day of the month: "25", "25-е", "25-го", "двадцать пятое"."""
    s = fold(text.strip())
    if m := re.fullmatch(r"(\d{1,2})(?:-?(?:е|го|ое|ого|м|ом))?", s):
        value = int(m.group(1))
    elif s in _DAY_ORDINALS:
        value = _DAY_ORDINALS[s]
    else:
        number = parse_num(s)
        value = int(number) if number is not None and float(number).is_integer() else 0
    return value if 1 <= value <= 31 else None


def _year(text: str) -> int | None:
    s = re.sub(r"\s*(?:года|году|год|г)\.?$", "", fold(text.strip()))
    value = parse_num(s)
    if value is None:
        # A year said as an ordinal: "две тысячи двадцать седьмого (года)".
        words = s.split()
        if words and fold(words[-1]) in _ORDINAL_UNITS:
            whole = parse_num(" ".join(words[:-1])) if len(words) > 1 else 0
            value = None if whole is None else whole + _ORDINAL_UNITS[fold(words[-1])]
    return int(value) if value is not None and float(value).is_integer() and 1 <= value <= 9999 else None


_ORDINAL_UNITS = {fold(ordinal(n, "m", case)): n for n in [*range(1, 20), *range(20, 100, 10), *range(100, 1000, 100), 1000, 2000]
                  for case in ("nom", "gen", "prep")}
"""A year's last word as an ordinal: "седьмого", "двухтысячного"."""


def orthodox_easter(year: int) -> dt.date:
    """Orthodox Easter Sunday: the Julian computus (Meeus), moved into the
    Gregorian calendar."""
    a, b, c = year % 4, year % 7, year % 19
    d = (19 * c + 15) % 30
    e = (2 * a + 4 * b - d + 34) % 7
    month, day = divmod(d + e + 114, 31)
    julian = dt.date(year, month, day + 1)
    return julian + dt.timedelta(days=year // 100 - year // 400 - 2)


_EASTER = {"пасха", "православная пасха", "светлое христово воскресение", "христово воскресение"}
_WESTERN_EASTER = {"католическая пасха", "западная пасха"}


def when(text: str) -> When | None:
    """"7 января", "седьмое января", "последнее воскресенье ноября",
    "Пасха", "49 дней до Пасхи", "1 неделя после Пасхи", "20 июня 2027
    года", "20.06.2027" -> when it falls -- or as English writes it
    ("December 25"); None when it can't be read."""
    s = " ".join(fold(re.sub(r",", " ", text)).split())
    s = re.sub(r"^(?:в|на) ", "", s)
    if m := re.fullmatch(r"(\d{1,2})[./](\d{1,2})(?:[./](\d{4}))?", s):
        return _fixed(int(m.group(1)), int(m.group(2)), int(m.group(3)) if m.group(3) else None)
    if m := re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", s):
        return _fixed(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    if m := re.fullmatch(r"(?:за |через )?(.+?) (дней|дня|день|недель|недели|неделю|неделя) (до|после) (.+)", s):
        number = parse_num(m.group(1))
        inner = when(m.group(4))
        if number is None or not float(number).is_integer() or number < 0 or inner is None:
            return None
        delta = dt.timedelta(days=int(number) * (7 if m.group(2).startswith("недел") else 1))
        delta = -delta if m.group(3) == "до" else delta

        def shifted(year: int) -> dt.date | None:
            date = inner.on(year)
            return date + delta if date is not None else None

        return When(shifted, year=inner.year)
    if _easter_word(s) in _EASTER:
        return When(orthodox_easter)
    if _easter_word(s) in _WESTERN_EASTER:
        return When(easter)
    if m := re.fullmatch(r"(\S+) (\S+) (\S+)", s):
        nth, day, month = _NTH.get(m.group(1)), DAYS.get(m.group(2)), MONTHS.get(m.group(3))
        if nth is not None and day is not None and month:

            def nth_weekday(year: int) -> dt.date | None:
                days = [d for d in calendar.Calendar().itermonthdates(year, month)
                        if d.month == month and d.weekday() == day]
                return days[-1] if nth == -1 else days[nth - 1] if nth <= len(days) else None

            return When(nth_weekday)
    if m := re.fullmatch(r"(.+?) ([а-я]+)\.?(?: (.+))?", s):
        day, month = day_number(m.group(1)), MONTHS.get(m.group(2))
        year = _year(m.group(3)) if m.group(3) else None
        if day and month and (m.group(3) is None or year):
            return _fixed(day, month, year)
    return english.when(text)


def _easter_word(s: str) -> str:
    """Easter's name in any case: "пасхи" -> "пасха"."""
    return re.sub(r"(?:и|у|е|ой)$", "а", s) if s.endswith(("пасхи", "пасху", "пасхе", "пасхой")) else s


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


_ENDINGS = sorted((
    "ьего", "ьему", "ьей", "ьем", "ами", "ями", "ого", "его", "ому", "ему", "ыми", "ими", "ах", "ях", "ов", "ев",
    "ей", "ой", "ий", "ый", "ая", "яя", "ое", "ее", "ые", "ие", "ую", "юю", "ом", "ем", "ам", "ям", "ых", "их",
    "ым", "им", "ия", "ии", "ию", "ье", "ья", "ью", "ьи", "а", "я", "о", "е", "ы", "и", "у", "ю", "ь", "й",
), key=len, reverse=True)
_DAY_FORMS = {"день", "дня", "дню", "днем", "дне", "дни", "дней", "дням", "днях"}


def _stem(word: str) -> str:
    """A word without its case ending: "нового" and "новый" are "нов"."""
    if word in _DAY_FORMS:
        return "дн"
    if word.isdigit():
        return word
    for ending in _ENDINGS:
        if word.endswith(ending) and len(word) - len(ending) >= 3:
            return word[: -len(ending)]
    return word


def key(name: str) -> str:
    """A holiday's name as looked up, in whatever case it is said: "Новый
    год", "Нового года", "к Новому году" -> "нов год"."""
    s = fold(name).replace("'", "").replace("-", " ")
    s = " ".join(re.sub(r"[,.«»\"]", " ", s).split())
    s = re.sub(r"^(?:до|к|ко|на|в|во|после|перед|с|со) ", "", s)
    return " ".join(_stem(w) for w in s.split())


def _h(name: str, on: str, *also: str) -> Holiday:
    return Holiday(name, on, also)


HOLIDAYS = (
    _h("Новый год", "1 января"),
    _h("Рождество", "7 января", "Рождество Христово", "Православное Рождество"),
    _h("Старый Новый год", "13 января"),
    _h("Крещение", "19 января", "Крещение Господне"),
    _h("Татьянин день", "25 января", "День студента"),
    _h("День святого Валентина", "14 февраля", "Валентинов день", "День всех влюблённых"),
    _h("День защитника Отечества", "23 февраля", "23 февраля", "Двадцать третье февраля"),
    _h("Масленица", "55 дней до Пасхи", "Масленичная неделя"),
    _h("Прощёное воскресенье", "49 дней до Пасхи"),
    _h("Международный женский день", "8 марта", "8 Марта", "Восьмое марта", "Женский день"),
    _h("День космонавтики", "12 апреля"),
    _h("Вербное воскресенье", "7 дней до Пасхи"),
    _h("Пасха", "Пасха", "Православная Пасха", "Светлое Христово Воскресение"),
    _h("Католическая Пасха", "католическая Пасха", "Западная Пасха"),
    _h("Праздник Весны и Труда", "1 мая", "Первомай", "1 Мая", "Первое мая", "День труда"),
    _h("День Победы", "9 мая", "9 Мая", "Девятое мая"),
    _h("Троица", "49 дней после Пасхи", "День Святой Троицы"),
    _h("День России", "12 июня"),
    _h("День семьи, любви и верности", "8 июля"),
    _h("День знаний", "1 сентября", "Первое сентября"),
    _h("День учителя", "5 октября"),
    _h("Хеллоуин", "31 октября", "Хэллоуин"),
    _h("День народного единства", "4 ноября"),
    _h("День матери", "последнее воскресенье ноября"),
    _h("Католическое Рождество", "25 декабря"),
    _h("Новогодняя ночь", "31 декабря", "Канун Нового года"),
)
"""The built-in holidays (Russia)."""

WHEN_EXAMPLES = "25 декабря, первое воскресенье мая, последнее воскресенье ноября, Пасха, 7 дней до Пасхи, 20 июня 2027 года"

_STARTS = {key("масленица"), key("масленичная неделя")}
"""Asked when these are, a week's first day is said as when it starts."""


def parse_date(s):
    """A date with its year: "4 июля 2026 года", "04.07.2026"; None."""
    found = when(s)
    if found is None or found.year is None:
        return None
    return found.on(found.year)


def _named(holiday: Holiday, asked: str) -> str:
    """The holiday as asked, in the case it was asked in ("Нового года"),
    capitalized as its name is: "до нового года" -> "Нового года"."""
    asked_words = re.sub(r"^(?:до|к|ко|на|в|во|после|перед|с|со) ", "", fold(asked)).split()
    for name in (holiday.name, *holiday.also):
        written = name.replace("-", " ").split()
        if key(name) == key(asked) and len(written) == len(asked_words):
            return " ".join(a[:1].upper() + a[1:] if w[:1].isupper() else a for a, w in zip(asked_words, written))
    return holiday.name


def _said(date: dt.date, today: dt.date, case: str = "nom", year: bool = False) -> str:
    """"седьмое января"; ``case`` "gen": "седьмого января"; with its year
    when not this one, or ``year``: "второе мая две тысячи двадцать седьмого
    года"."""
    said = f"{ordinal(date.day, 'n', case)} {_MONTHS[date.month - 1][1]}"
    if year or date.year != today.year:
        said += f" {ordinal(date.year, 'm', 'gen')} года"
    return said


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


def _year_said(year: int, case: str = "nom") -> str:
    """"две тысячи двадцать четвёртый год", "две тысячи двадцать четвёртого
    года"."""
    return f"{ordinal(year, 'm', case)} {'год' if case == 'nom' else 'года' if case == 'gen' else 'году'}"


def _day_of(text, today, settings):
    """The next date a holiday or "25 декабря" falls on, from ``today``."""
    holiday = settings.holiday(text)
    if holiday is not None:
        return holiday.next(today)
    found = when(text)
    if found is None:
        return None
    if found.year is not None:
        date = found.on(found.year)
        return date if date is not None and date >= today else None
    if found.fixed is None:
        return None
    for year in (today.year, today.year + 1):
        date = found.on(year)
        if date is not None and date >= today:
            return date
    return None


def solve(q, settings):
    """The answer, the sentence it is said in, and with the question
    ("Новый год — первое января"); None."""
    s = clean(q)
    if m := (re.fullmatch(rf"(?:является ли |будет ли |был ли |ли )?(?:год )?{N}(?: год| году| года)?"
                          rf"(?: был| будет| является)? високосн\w*(?: год\w*)?(?: или нет)?", s)
             or re.fullmatch(rf"високосн\w* ли (?:год )?{N}(?: год)?", s)):
        year = parse_num(m.group(1))
        if year is not None and float(year).is_integer() and 1 <= year <= 9999:
            yes = leap(int(year))
            return ("Да" if yes else "Нет"), None, (
                f"{'Да' if yes else 'Нет'}, {_year_said(int(year))} — {'' if yes else 'не '}високосный")
    if re.fullmatch(r"сколько (?:всего )?дней (?:в|во) високосном году", s):
        return "366 дней", "В високосном году 366 дней.", "В високосном году 366 дней"
    if re.fullmatch(r"сколько (?:всего )?дней (?:в|во) (?:обычном |невисокосном )?году", s):
        return "365 дней", "В году 365 дней.", "В обычном году 365 дней"
    if m := re.fullmatch(rf"сколько (?:всего )?дней (?:в|во) (?:месяце )?([а-я]+)(?: {N}(?: года| году| г)?)?", s):
        month = MONTHS.get(m.group(1))
        year = parse_num(m.group(2)) if m.group(2) else None
        if month and (year is None or 1 <= year <= 9999):
            prepositional = _MONTHS[month - 1][2]
            if month == 2 and year is None:
                return ("28, а в високосный год 29", "28 дней, а в високосный год 29.",
                        "В феврале 28 дней, а в високосный год 29")
            days = calendar.monthrange(int(year or 2001), month)[1]
            said = f"{prepositional} {_year_said(int(year), 'gen')}" if year else prepositional
            return f"{days} {plural(days, 'день', 'дня', 'дней')}", None, f"В {said} {days} {plural(days, 'день', 'дня', 'дней')}"
    if m := re.fullmatch(r"сколько (?:всего )?(месяцев|недель|дней) (?:в|во) (году|неделе)", s):
        found = {("месяцев", "году"): "12 месяцев", ("недель", "году"): "52 недели",
                 ("дней", "неделе"): "7 дней"}.get(m.groups())
        if found:
            of = "В году" if m.group(2) == "году" else "В неделе"
            return found, None, f"{of} {found}"
    if m := re.fullmatch(r"какой (месяц|день(?: недели)?) (?:идет |будет |наступает )?(после|перед) ([а-я]+)", s):
        after = m.group(2) == "после"
        if m.group(1) == "месяц" and (month := MONTHS.get(m.group(3))):
            forms = _MONTHS[month - 1]
            found = _MONTHS[month % 12 if after else (month - 2) % 12][0]
            said = f"После {forms[1]}" if after else f"Перед {forms[3]}"
            return found, f"{_capital(found)}.", f"{said} идёт {found}"
        if m.group(1).startswith("день") and (day := DAYS.get(m.group(3))) is not None:
            forms = _DAYS[day]
            found = _DAYS[(day + (1 if after else -1)) % 7][0]
            said = f"После {forms[2]}" if after else f"Перед {forms[3]}"
            return found, f"{_capital(found)}.", f"{said} идёт {found}"
    if m := re.fullmatch(r"какой (первый|последний) месяц(?: в году| года)?|какой месяц (?:в году |года )?(первый|последний)(?: в году| года)?", s):
        first = (m.group(1) or m.group(2)) == "первый"
        found = "январь" if first else "декабрь"
        return found, f"{_capital(found)}.", f"{'Первый' if first else 'Последний'} месяц года — {found}"
    # The day of the week a date or holiday falls on.
    if m := re.fullmatch(r"(?:в |на )?(?:какой|какое|что за) день(?: недели)? (?:будет |был |было |выпадает |выпал |"
                         r"приходится |придется |пришелся |это )?(.+)", s):
        asked = m.group(1)
        today = shared.today()
        date = parse_date(asked)
        holiday = settings.holiday(asked) if date is None else None
        if date is None:
            date = _day_of(asked, today, settings)
        if date:
            day, accusative = _DAYS[date.weekday()][0], _DAYS[date.weekday()][1]
            if holiday:
                restated = f"{_capital(_named(holiday, asked))} выпадает на {accusative}"
            else:
                restated = f"{_capital(_said(date, today, year=parse_date(asked) is not None))} — {day}"
            return day, f"{_capital(day)}.", restated
    # A holiday's date: this year's, or the year asked ("в следующем году").
    year_asked = r"(?: в (этом|следующем|прошлом) году| в (\d{4}) году)?"
    if m := (re.fullmatch(r"когда (?:будет |наступит |отмечают |празднуют |начинается |начнется |будут |"
                          rf"отмечается |празднуется |была |был |было )?(.+?){year_asked}", s)
             or re.fullmatch(rf"какого числа (?:будет |отмечают |празднуют |отмечается |был |была )?(.+?){year_asked}", s)):
        holiday = settings.holiday(m.group(1))
        if holiday is not None:
            today, found = shared.today(), holiday.date
            year = {"этом": today.year, "следующем": today.year + 1, "прошлом": today.year - 1}.get(m.group(2) or "")
            year = year or (int(m.group(3)) if m.group(3) else None)
            if found.fixed and year is None:
                day, month = found.fixed[1], _MONTHS[found.fixed[0] - 1][1]
                said = {case: f"{ordinal(day, 'n', case)} {month}" for case in ("nom", "gen")}
            else:
                on = found.on(year) if year is not None else found.on(found.year) if found.year else holiday.next(today)
                if on is None:
                    return None
                said = {case: _said(on, today, case, year=found.year is not None) for case in ("nom", "gen")}
            name = _capital(_named(holiday, m.group(1)))
            if key(holiday.name) in _STARTS:
                return said["nom"], f"{_capital(said['nom'])}.", f"{name} начинается {said['gen']}"
            return said["nom"], f"{_capital(said['nom'])}.", f"{name} — {said['nom']}"
    # Days until a holiday or a date.
    if m := (re.fullmatch(r"сколько (?:еще )?(?:осталось |остается )?(дней|недель)(?: еще)?(?: осталось| остается)? "
                          r"(?:до|к) (.+)", s)
             or re.fullmatch(r"сколько (?:еще )?(?:осталось|остается|ждать) (?:до|к) (.+)", s)):
        unit = m.group(1) if m.lastindex == 2 else "дней"
        target_text = m.group(m.lastindex)
        today = shared.today()
        target = _day_of(target_text, today, settings)
        if target is not None:
            days = (target - today).days
            weeks = unit == "недель"
            n = days // 7 if weeks else days
            noun = plural(n, "неделя", "недели", "недель") if weeks else plural(n, "день", "дня", "дней")
            found = f"{count(n, 'f' if weeks else 'm')} {noun}"
            verb = "Осталось"
            if n % 10 == 1 and n % 100 != 11:
                verb = "Осталась" if weeks else "Остался"
            holiday = settings.holiday(target_text)
            if holiday is not None and not re.search(r"\d", target_text):
                name = _named(holiday, target_text)
            else:
                # A date said back in words ("до тридцать первого декабря"),
                # with its year only when asked with one.
                with_year = parse_date(target_text) is not None
                name = _said(target, today if with_year else target, "gen", year=with_year)
            return found, f"{verb} {found}.", f"До {name} {verb.lower()} {found}"
    return None


def answer(text: str, settings: Settings):
    found = solve(text, settings)
    if not found:
        return None
    said, specific, restated = found
    return reply(said, text, restated, brief=f"{_capital(said)}.", specific=specific or f"{_capital(said)}.")
