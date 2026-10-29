"""How Ukrainian reads a date ("25 грудня", "25.12.2026", "друга неділя
травня", "49 днів після Великодня"), asks about dates ("чи 2024 високосний
рік", "коли Різдво", "скільки днів до Різдва") and says them; and the
holidays it knows (Ukraine's; voice_labs/skills/dates.py). A holiday's date
may also be written as English writes it ("December 25"), as the UI is in
English.

Easter is the Orthodox one, by the Julian computus, as Ukraine keeps it;
its fixed feasts follow the new calendar since 2023 (Різдво on 25 December).
A holiday is asked for in any case ("до Різдва", "Нового року", "Дня
Незалежності"), so its names are looked up by their stems (``key``). A date
is said in words: "двадцять п'ятого грудня", "дві тисячі двадцять сьомого
року"."""

from __future__ import annotations

import calendar
import datetime as dt
import re

from voice_labs.languages.en.skills import dates as english
from voice_labs.skills import dates as shared
from voice_labs.skills.common import reply
from voice_labs.skills.dates import Holiday, Settings, When, leap

from ..numerals import ORDINAL_ENDINGS, ordinal, plural, whole
from .numbers import apostrophes, clean, parse_num

EXAMPLES = ("чи 2024 високосний рік", "коли Великдень", "скільки днів до Різдва")

MONTH_NAMES = ["січень", "лютий", "березень", "квітень", "травень", "червень", "липень", "серпень", "вересень",
               "жовтень", "листопад", "грудень"]
MONTHS_OF = ["січня", "лютого", "березня", "квітня", "травня", "червня", "липня", "серпня", "вересня", "жовтня",
             "листопада", "грудня"]
"""A month in the genitive, as a date names it: "25 грудня"."""
MONTHS_IN = ["січні", "лютому", "березні", "квітні", "травні", "червні", "липні", "серпні", "вересні", "жовтні",
             "листопаді", "грудні"]
MONTHS = {m: i for names in (MONTH_NAMES, MONTHS_OF, MONTHS_IN) for i, m in enumerate(names, 1)} | {
    "січ": 1, "лют": 2, "бер": 3, "квіт": 4, "трав": 5, "черв": 6, "лип": 7, "серп": 8, "вер": 9, "жовт": 10,
    "лист": 11, "груд": 12,
}
DAY_NAMES = ["понеділок", "вівторок", "середа", "четвер", "п'ятниця", "субота", "неділя"]
_DAYS_ON = ["понеділок", "вівторок", "середу", "четвер", "п'ятницю", "суботу", "неділю"]
_DAYS_OF = ["понеділка", "вівторка", "середи", "четверга", "п'ятниці", "суботи", "неділі"]
_DAYS_AS = ["понеділком", "вівторком", "середою", "четвергом", "п'ятницею", "суботою", "неділею"]
DAYS = {d: i for names in (DAY_NAMES, _DAYS_ON, _DAYS_OF, _DAYS_AS) for i, d in enumerate(names)}

_NTH = {
    **{ordinal(n, form): n for n in range(1, 6) for form in ("m", "f", "n", "f_acc")},
    **dict.fromkeys(["останній", "остання", "останнє", "останню"], -1),
}
"""The nth weekday of a month: "перша неділя", "останній понеділок"."""


def _ordinal_words() -> dict[str, int]:
    """Ordinals of a day or a year's last word, in any case: "п'яте",
    "двадцятого", "сьомого"."""
    table: dict[str, int] = {}
    for n in [*range(1, 32), *range(40, 100, 10), *range(100, 1000, 100), 1000, 2000]:
        for form in ORDINAL_ENDINGS:
            word = ordinal(n, form).split()[-1]
            table.setdefault(word, n if n < 20 or n % 10 == 0 else n % 10)
    return table


_ORDINALS = _ordinal_words()


def ordinal_number(text: str) -> int | None:
    """An ordinal said ("двадцять п'ятого", "дві тисячі двадцять сьомого") or
    written ("25", "25-го"); None."""
    s = text.strip().lower()
    if m := re.fullmatch(r"(\d+)(?:-?(?:го|е|й|ого|ий|ій|а|у))?", s):
        return int(m.group(1))
    words = s.split()
    if not words or words[-1] not in _ORDINALS:
        return None
    last = _ORDINALS[words[-1]]
    if len(words) == 1:
        return last
    if words[-1].startswith(("двохтисячн", "тисячн")):
        return None
    head = whole(words[:-1])
    return None if head is None else head + last


def _day(text: str) -> int | None:
    """A day of the month: "25", "двадцять п'ятого", "двадцять п'ять"."""
    value = ordinal_number(text)
    if value is None:
        number = parse_num(text)
        value = int(number) if number is not None and float(number).is_integer() else None
    return value if value is not None and 1 <= value <= 31 else None


def _year(text: str) -> int | None:
    text = re.sub(r"\s+(?:року|рік|р)\.?$", "", text.strip())
    value = ordinal_number(text)
    if value is None:
        number = parse_num(text)
        value = int(number) if number is not None and float(number).is_integer() else None
    return value if value is not None and 1 <= value <= 9999 else None


def easter(year: int) -> dt.date:
    """Orthodox Easter Sunday: the Julian computus, on the Gregorian calendar."""
    a, b, c = year % 4, year % 7, year % 19
    d = (19 * c + 15) % 30
    e = (2 * a + 4 * b - d + 34) % 7
    month, day = divmod(d + e + 114, 31)
    return dt.date(year, month, day + 1) + dt.timedelta(days=year // 100 - year // 400 - 2)


_EASTER = {"великодн", "пасх", "паск", "воскресінн христов"}


def when(text: str) -> When | None:
    """"25 грудня", "друга неділя травня", "остання субота листопада",
    "Великдень", "49 днів після Великодня", "1 тиждень до ...", "20 червня 2027
    року", "20.06.2027" -> when it falls -- or as English writes it
    ("December 25"); None when it can't be read."""
    s = " ".join(apostrophes(re.sub(r",", " ", text.lower())).split())
    if m := re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})(?:[-/.](\d{4}))?(?: року| р\.?)?", s):
        return _fixed(int(m.group(1)), int(m.group(2)), int(m.group(3)) if m.group(3) else None)
    if m := re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", s):
        return _fixed(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    if m := re.fullmatch(r"(.+?) (днів|дні|день|тижнів|тижні|тиждень) (до|після|перед) (.+)", s):
        count = parse_num(m.group(1))
        inner = when(m.group(4))
        if count is None or not float(count).is_integer() or count < 0 or inner is None:
            return None
        delta = dt.timedelta(days=int(count) * (7 if m.group(2).startswith("тиж") else 1))
        delta = delta if m.group(3) == "після" else -delta

        def shifted(year: int) -> dt.date | None:
            date = inner.on(year)
            return date + delta if date is not None else None

        return When(shifted, year=inner.year)
    if key(s) in _EASTER:
        return When(easter)
    if m := re.fullmatch(r"(\S+) (\S+) (?:у |в )?(\S+)", s):
        nth, day, month = _NTH.get(m.group(1)), DAYS.get(m.group(2)), MONTHS.get(m.group(3))
        if nth is not None and day is not None and month:

            def nth_weekday(year: int) -> dt.date | None:
                days = [d for d in calendar.Calendar().itermonthdates(year, month)
                        if d.month == month and d.weekday() == day]
                return days[-1] if nth == -1 else days[nth - 1] if nth <= len(days) else None

            return When(nth_weekday)
    words = s.split()
    for index, word in enumerate(words[1:], 1):
        if word in MONTHS:
            day, month = _day(" ".join(words[:index])), MONTHS[word]
            rest = " ".join(words[index + 1 :])
            year = _year(rest) if rest else None
            if day and (not rest or year):
                return _fixed(day, month, year)
            break
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


_LEMMAS = {
    "день": "дн", "дня": "дн", "дню": "дн", "днем": "дн", "рік": "рок", "року": "рок", "році": "рок",
    "роком": "рок", "великдень": "великодн", "вечір": "вечор", "святвечір": "святвечор", "та": "і", "й": "і",
}
"""Words whose stem changes as they decline: "день", "дня"; "рік", "року"."""

_ENDINGS = sorted(["ами", "ями", "ого", "ього", "ому", "ьому", "ої", "ьої", "ою", "ею", "єю", "ий", "ій", "их", "іх",
                   "им", "ім", "ів", "їв", "ах", "ях", "ам", "ям", "ом", "ем", "єм", "а", "я", "у", "ю", "і", "ї",
                   "и", "е", "є", "о", "ь", "й"], key=len, reverse=True)


def _stem(word: str) -> str:
    if word in _LEMMAS or not word.isalpha() and "'" not in word:
        return _LEMMAS.get(word, word)
    for ending in _ENDINGS:
        if word.endswith(ending) and len(word) - len(ending) >= 3:
            return word[: -len(ending)]
    return word


def key(name: str) -> str:
    """A holiday's name as looked up, in whatever case it was said: "День
    Незалежності", "Дня Незалежності" -> "дн незалежност"; "Новий рік",
    "Нового року" -> "нов рок"."""
    s = apostrophes(name.lower()).replace("-", " ").replace('"', " ").replace("«", " ").replace("»", " ")
    return " ".join(_stem(w) for w in re.sub(r"[,.!?]", " ", s).split())


def _h(name: str, on: str, *also: str) -> Holiday:
    return Holiday(name, on, also)


HOLIDAYS = (
    _h("Новий рік", "1 січня"),
    _h("Водохреще", "6 січня", "Хрещення Господнє"),
    _h("День Соборності", "22 січня", "День Соборності України"),
    _h("День святого Валентина", "14 лютого", "День закоханих", "Валентинів день"),
    _h("Міжнародний жіночий день", "8 березня", "Восьме березня", "Жіночий день"),
    _h("Вербна неділя", "7 днів до Великодня"),
    _h("Страсна п'ятниця", "2 дні до Великодня", "Велика п'ятниця"),
    _h("Великдень", "Великдень", "Пасха", "Паска", "Воскресіння Христове"),
    _h("Трійця", "49 днів після Великодня", "Зелені свята", "П'ятидесятниця"),
    _h("День праці", "1 травня", "Перше травня", "Свято праці"),
    _h("День пам'яті та перемоги", "8 травня", "День пам'яті та перемоги над нацизмом", "День перемоги"),
    _h("День матері", "друга неділя травня", "День мами"),
    _h("День вишиванки", "третій четвер травня"),
    _h("День батька", "третя неділя червня", "День тата"),
    _h("День Конституції", "28 червня", "День Конституції України"),
    _h("День Української Державності", "15 липня", "День Державності"),
    _h("День Незалежності", "24 серпня", "День Незалежності України"),
    _h("День знань", "1 вересня", "Перше вересня"),
    _h("День захисників і захисниць", "1 жовтня", "День захисників і захисниць України", "День захисника",
       "День захисника України", "Покрова"),
    _h("Хелловін", "31 жовтня", "Гелловін"),
    _h("День Гідності та Свободи", "21 листопада"),
    _h("День пам'яті жертв голодоморів", "четверта субота листопада", "День пам'яті жертв Голодомору"),
    _h("День Збройних Сил", "6 грудня", "День Збройних Сил України", "День ЗСУ"),
    _h("День святого Миколая", "6 грудня", "Святого Миколая", "Миколая", "Миколай"),
    _h("Святвечір", "24 грудня", "Святий вечір"),
    _h("Різдво", "25 грудня", "Різдво Христове"),
)
"""The built-in holidays (Ukraine)."""

WHEN_EXAMPLES = ("25 грудня, друга неділя травня, остання субота листопада, Великдень, 49 днів після Великодня, "
                 "20 червня 2027 року")


def parse_date(s):
    """A date with its year: "4 липня 2026 року", "04.07.2026"; None."""
    found = when(s)
    if found is None or found.year is None:
        return None
    return found.on(found.year)


def _named(holiday: Holiday, asked: str) -> str:
    """The holiday as it was asked for, in its case, capitalized as its
    name: "Різдва", "Дня Незалежності"; else its name."""
    words = asked.split()
    for name in (holiday.name, *holiday.also):
        written = name.split()
        if key(name) == key(asked) and len(written) == len(words):
            return " ".join(w[:1].upper() + w[1:] if n[:1].isupper() else w for w, n in zip(words, written))
    return holiday.name


def _day_of(text, today, settings):
    """The next date a holiday or "25 грудня" falls on, from ``today``."""
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


def _written(date: dt.date, today: dt.date, year: bool = False) -> str:
    """"25 грудня"; with its year when not this one, or ``year``: "25 грудня
    2027 року"."""
    said = f"{date.day} {MONTHS_OF[date.month - 1]}"
    return f"{said} {date.year} року" if year or date.year != today.year else said


def _said(date: dt.date, today: dt.date, year: bool = False, form: str = "gen") -> str:
    """A date in words, its day an ordinal in ``form``: on it ("gen"),
    "двадцять п'ятого грудня"; naming it ("n"), "двадцять п'яте грудня"; with
    its year as ``_written``: "дві тисячі двадцять сьомого року"."""
    said = f"{ordinal(date.day, form)} {MONTHS_OF[date.month - 1]}"
    if year or date.year != today.year:
        said += f" {ordinal(date.year, 'gen')} року"
    return said


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


def _days(count: int, unit: str = "day") -> str:
    """"66 днів", "1 день", "3 тижні" (masculine: the voice reads them)."""
    forms = ("день", "дні", "днів") if unit == "day" else ("тиждень", "тижні", "тижнів")
    return f"{count} {plural(count, *forms)}"


_HOLIDAY_ASK = re.compile(
    r"(?:коли|якого числа|котрого числа|на яке число|яка дата|в який день|у який день)(?: (?:буде|був|була|було|настане|"
    r"святкують|святкуємо|відзначають|відзначаємо|відзначається|святкується|у нас|цього року|наступного року))*"
    r" (.+)"
)
_WEEKDAY_ASK = re.compile(
    r"(?:(?:який|яким|у який|в який|на який) день тижня|(?:у|в|на) який день)(?: (?:буде|був|була|було|припадає|"
    r"припаде|припав|припала|святкують|відзначають))?(?: на)? (.+)"
)


def solve(q, settings):
    """The answer, and how it is said briefly, as a sentence, and with the
    question ("Різдво — двадцять п'ятого грудня"); None."""
    s = clean(q)
    today = shared.today()
    if "високосн" in s and "скільки" not in s:
        rest = " ".join(re.sub(r"\b(?:чи|є|рік|року|роком|високосн\w*|був|буде|це)\b", " ", s).split())
        year = _year(rest) if rest else None
        if year is not None:
            yes = leap(year)
            said = f"{ordinal(year)} рік {'високосний' if yes else 'не високосний'}"
            word = "Так" if yes else "Ні"
            return word, f"{word}.", f"{word}.", f"{word}, {said}"
    if re.fullmatch(r"скільки днів (?:у|в|має) (?:високосному році|високосний рік)", s):
        return "366 днів", "366 днів.", "Це 366 днів.", "У високосному році 366 днів"
    if re.fullmatch(r"скільки днів (?:у|в|має) (?:році|рік|звичайному році|невисокосному році)", s):
        return "365 днів", "365 днів.", "Це 365 днів.", "У році 365 днів"
    if m := re.fullmatch(r"скільки днів (?:у|в|має) ([а-яії']+)(?: (.+?))?", s):
        month = MONTHS.get(m.group(1))
        year = _year(m.group(2)) if m.group(2) else None
        if month and (m.group(2) is None or year):
            if month == 2 and year is None:
                said = "двадцять вісім, або двадцять дев'ять у високосному році"
                return "28 або 29", f"{_capital(said)}.", f"{_capital(said)}.", f"У лютому {said}"
            days = _days(calendar.monthrange(year or 2001, month)[1])
            name = f"{MONTHS_IN[month - 1]} {ordinal(year, 'gen')} року" if year else MONTHS_IN[month - 1]
            return days, f"{days}.", f"Це {days}.", f"У {name} {days}"
    if m := re.fullmatch(r"скільки (місяців|тижнів|днів) (?:у|в|має) (році|рік|тижні|тиждень)", s):
        found = {("місяців", "р"): "12 місяців", ("тижнів", "р"): "52 тижні", ("днів", "т"): "7 днів",
                 ("днів", "р"): "365 днів"}.get((m.group(1), m.group(2)[0]))
        if found:
            of = "У році" if m.group(2).startswith("р") else "У тижні"
            return found, f"{found}.", f"Це {found}.", f"{of} {found}"
    if m := re.fullmatch(r"який (місяць|день) (?:іде |йде |буде |настає |є )?(після|перед|до) ([а-яії']+)", s):
        where = "Після" if m.group(2) == "після" else "Перед"
        if m.group(1) == "місяць" and (month := MONTHS.get(m.group(3))):
            found = MONTH_NAMES[month % 12 if m.group(2) == "після" else (month - 2) % 12]
            return found, f"{_capital(found)}.", f"Це {found}.", f"{where} {MONTHS_OF[month - 1]} іде {found}"
        if m.group(1) == "день" and (day := DAYS.get(m.group(3))) is not None:
            found = DAY_NAMES[(day + (1 if m.group(2) == "після" else -1)) % 7]
            return found, f"{_capital(found)}.", f"Це {found}.", f"{where} {_DAYS_OF[day]} іде {found}"
    if m := re.fullmatch(r"який (?:(перший|останній) місяць (?:року|у році)|місяць (?:року )?(перший|останній))", s):
        first = (m.group(1) or m.group(2)) == "перший"
        found = "січень" if first else "грудень"
        return found, f"{_capital(found)}.", f"Це {found}.", f"{'Перший' if first else 'Останній'} місяць року — {found}"
    # The day of the week a date or holiday falls on.
    if m := _WEEKDAY_ASK.fullmatch(s):
        asked = m.group(1)
        date = parse_date(asked)
        holiday = settings.holiday(asked) if date is None else None
        if date is None:
            # A holiday, or a date without its year: the next time it comes.
            date = _day_of(asked, today, settings)
        if date:
            day = DAY_NAMES[date.weekday()]
            if holiday:
                restated = f"{_named(holiday, asked)} — у {_DAYS_ON[date.weekday()]}"
            else:
                restated = f"{_capital(_said(date, today, parse_date(asked) is not None, 'n'))} — {day}"
            return day, f"{_capital(day)}.", f"Це {day}.", restated
    # A holiday's date.
    if m := _HOLIDAY_ASK.fullmatch(s):
        holiday = settings.holiday(m.group(1))
        if holiday is not None:
            found = holiday.date
            if found.fixed:
                on = dt.date(2000, *found.fixed)
                date, written = _said(on, on), f"{found.fixed[1]} {MONTHS_OF[found.fixed[0] - 1]}"
            else:
                on = found.on(found.year) if found.year is not None else holiday.next(today)
                date = _said(on, today, year=found.year is not None) if on else None
                written = _written(on, today, year=found.year is not None) if on else None
            if date:
                return written, f"{_capital(date)}.", f"{_capital(date)}.", f"{_named(holiday, m.group(1))} — {date}"
    # Days until a holiday or a date.
    if m := (re.fullmatch(r"(?:через )?скільки (днів|тижнів)(?: (?:залишилось|залишилося|лишилось|лишилося|ще|"
                          r"залишається|лишається))? (?:до|буде|настане) (.+)", s)
             or re.fullmatch(r"скільки (?:часу |ще )?(?:залишилось|залишилося|лишилось|лишилося|залишається) до (.+)", s)):
        unit = m.group(1) if m.lastindex == 2 else "днів"
        target_text = m.group(m.lastindex)
        target = _day_of(target_text, today, settings)
        if target is not None:
            days = (target - today).days
            found = _days(days // 7, "week") if unit == "тижнів" else _days(days)
            holiday = settings.holiday(target_text)
            to = f"до {_named(holiday, target_text)}" if holiday else f"до {_said(target, today)}"
            return found, f"{found}.", f"Ще {found}.", f"{_capital(to)} ще {found}"
    return None


def answer(text: str, settings: Settings):
    found = solve(text, settings)
    if not found:
        return None
    said, brief, specific, restated = found
    return reply(said, text, restated, brief=brief, specific=specific)
