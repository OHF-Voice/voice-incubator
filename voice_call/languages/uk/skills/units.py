"""How Ukrainian names units ("склянка", "склянки", "склянок", "американська
склянка"), which it knows built in, and how it asks a conversion ("скільки
грамів у фунті", "скільки кілометрів у п'яти милях", "переведи 5 миль у
кілометри", "100 градусів за Фаренгейтом у Цельсіях") and says one
(voice_call/skills/units.py).

A unit is asked for in any case ("у фунті", "у п'яти милях", "чайних
ложок"), so a name heard is looked up by the nominatives it may be the case
of (``forms``). A count takes its noun's form (numerals.plural): 1
кілометр, 2 кілометри, 5 кілометрів, 8,047 кілометра. A teaspoon, a
tablespoon and a cup (склянка) are metric (5, 15 and 250 ml) as built in,
the larger volumes US customary."""

from __future__ import annotations

import itertools
import re

from voice_call.skills.common import fmt, reply
from voice_call.skills.units import Settings, _scale, _u

from .numbers import N, apostrophes, clean, counted, parse_num

EXAMPLES = ("скільки грамів у фунті", "скільки кілометрів у п'яти милях", "100 градусів за Фаренгейтом у Цельсіях")

_IRREGULAR = {
    "день": ("день", "дні", "днів", "дня", "m"),
    "тиждень": ("тиждень", "тижні", "тижнів", "тижня", "m"),
    "рік": ("рік", "роки", "років", "року", "m"),
    "доба": ("доба", "доби", "діб", "доби", "f"),
    "кіло": ("кіло", "кіло", "кіло", "кіло", "n"),
}
_CASES_OF = {
    "дні": "день", "днів": "день", "дня": "день", "дню": "день", "днем": "день", "днях": "день", "дням": "день",
    "тижні": "тиждень", "тижнів": "тиждень", "тижня": "тиждень", "тижню": "тиждень", "тижнем": "тиждень",
    "тижнях": "тиждень", "роки": "рік", "років": "рік", "року": "рік", "році": "рік", "роком": "рік",
    "роках": "рік", "літ": "рік", "діб": "доба", "добі": "доба",
}
"""Cases whose stem changes: "днів" is of "день", "році" of "рік"."""

_VOWELS = "аеєиіїоуюя"


def _noun(word: str) -> tuple[str, str, str, str, str]:
    """A noun's forms after 1, 2-4, 5 and more, and a fraction, and its
    gender: "метр" -> метр, метри, метрів, метра; "склянка" -> склянка,
    склянки, склянок, склянки; "миля" -> миля, милі, миль, милі."""
    if word in _IRREGULAR:
        return _IRREGULAR[word]
    if word.endswith("тя"):
        return word, word, word[:-2] + "ть", word, "n"
    if word.endswith("а"):
        stem = word[:-1]
        many = stem[:-1] + "ок" if re.search(rf"[^{_VOWELS}]к$", stem) else stem
        return word, stem + "и", many, stem + "и", "f"
    if word.endswith("я"):
        stem = word[:-1]
        if stem.endswith("і"):
            return word, stem + "ї", stem + "й", stem + "ї", "f"
        return word, stem + "і", stem + "ь", stem + "і", "f"
    if word[-1:] in "оеє":
        return word, word, word, word, "n"
    return word, word + "и", word + "ів", word + "а", "m"


def _adjective(word: str) -> tuple[str, str, str, str] | None:
    """An adjective's forms with its noun: "чайна" -> чайна, чайні, чайних,
    чайної; "квадратний" -> квадратний, квадратні, квадратних,
    квадратного."""
    for ending, (part, gender) in (("ий", ("ого", "m")), ("ій", ("ього", "m")), ("а", ("ої", "f")), ("е", ("ого", "n"))):
        if word.endswith(ending) and len(word) > 4:
            stem = word[: -len(ending)]
            return word, stem + "і", stem + "их", stem + part
    return None


def inflected(name: str) -> tuple[tuple[str, str, str, str], str]:
    """A unit's forms (after 1, 2-4, 5 and more, a fraction) and its gender:
    "чайна ложка" -> (чайна ложка, чайні ложки, чайних ложок, чайної ложки),
    "f"."""
    words = name.split()
    if not words:
        return (name, name, name, name), "m"
    *before, last = words
    one, few, many, part, gender = _noun(last.lower())
    if last != last.lower():
        one, few, many, part = (w[:1].upper() + w[1:] for w in (one, few, many, part))
    adjectives = [_adjective(w) for w in before]
    if None in adjectives:
        # Not adjectives ("фунт стерлінгів"): the first word declines.
        first = _noun(words[0].lower())
        rest = " ".join(words[1:])
        return tuple(f"{form} {rest}" for form in first[:4]), first[4]  # type: ignore[return-value]
    forms = []
    for index, own in enumerate((one, few, many, part)):
        forms.append(" ".join([*(a[index] for a in adjectives), own]))  # type: ignore[index]
    return (forms[0], forms[1], forms[2], forms[3]), gender


def plural_of(name: str) -> str:
    """After 5 and more: "метрів", "склянок", "чайних ложок"."""
    return inflected(name)[0][2]


def key(text: str) -> str:
    """A unit's name as looked up: lowercase, without periods: "Кілометрів"
    -> "кілометрів"."""
    return " ".join(apostrophes(text.lower()).replace(".", " ").replace("-", " ").split())


UNITS = (
    _u("міліметр", "length", 0.001, "мм", "mm"),
    _u("сантиметр", "length", 0.01, "см", "cm"),
    _u("метр", "length", 1, "м", "m"),
    _u("кілометр", "length", 1000, "км", "km"),
    _u("дюйм", "length", 0.0254, "in", "inch", "inches"),
    _u("фут", "length", 0.3048, "ft", "feet", "foot"),
    _u("ярд", "length", 0.9144, "yd"),
    _u("миля", "length", 1609.344, "mi"),
    _u("морська миля", "length", 1852),
    _u("квадратний дюйм", "area", 0.00064516),
    _u("квадратний фут", "area", 0.09290304),
    _u("квадратний метр", "area", 1, "м2", "кв м"),
    _u("квадратний кілометр", "area", 1e6, "км2"),
    _u("квадратна миля", "area", 2589988.110336),
    _u("акр", "area", 4046.8564224),
    _u("сотка", "area", 100),
    _u("гектар", "area", 10000, "га"),
    _u("мілілітр", "volume", 1, "мл", "ml"),
    _u("літр", "volume", 1000, "л", "l"),
    _u("чайна ложка", "volume", 5, "ч л"),
    _u("столова ложка", "volume", 15, "ст л"),
    _u("рідка унція", "volume", 29.5735295625, "fl oz"),
    _u("склянка", "volume", 250, "чашка", "cup", "cups"),
    _u("пінта", "volume", 473.176473, "pint", "pints"),
    _u("кварта", "volume", 946.352946, "quart", "quarts"),
    _u("галон", "volume", 3785.411784, "gallon", "gallons"),
    _u("міліграм", "weight", 0.001, "мг", "mg"),
    _u("грам", "weight", 1, "г", "g", "грамм"),
    _u("кілограм", "weight", 1000, "кг", "kg", "кіло", "кілограмм"),
    _u("унція", "weight", 28.349523125, "oz"),
    _u("фунт", "weight", 453.59237, "lb", "lbs"),
    _u("стоун", "weight", 6350.29318),
    _u("центнер", "weight", 100000, "ц"),
    _u("тонна", "weight", 1e6, "т"),
    _u("мілісекунда", "time", 0.001, "мс", "ms"),
    _u("секунда", "time", 1, "с", "сек"),
    _u("хвилина", "time", 60, "хв", "min"),
    _u("година", "time", 3600, "год"),
    _u("доба", "time", 86400),
    _u("день", "time", 86400),
    _u("тиждень", "time", 604800),
    _u("рік", "time", 31536000),
    _u("десятиліття", "time", 315360000),
    _u("століття", "time", 3153600000),
    _u("байт", "data", 1),
    _u("кілобайт", "data", 1000, "кб", "kb"),
    _u("мегабайт", "data", 1e6, "мб", "mb"),
    _u("гігабайт", "data", 1e9, "гб", "gb"),
    _u("терабайт", "data", 1e12, "тб", "tb"),
    _scale("Цельсій", 0, 100, "c", "цельсія", "цельсієм", "цельсії", "цельсіях", "цельсіям"),
    _scale("Фаренгейт", 32, 212, "f", "фаренгейта", "фаренгейтом", "фаренгейті", "фаренгейтах", "фаренгейтам"),
    _scale("Кельвін", 273.15, 373.15, "k", "кельвіна", "кельвіном", "кельвіні", "кельвінах", "кельвінів"),
)
"""The built-in units."""

_SCALE_OF = {"цельсій": "Цельсія", "фаренгейт": "Фаренгейта", "кельвін": "Кельвіна"}
"""A temperature scale after "градусів"."""

_US = {"чайна ложка": 4.92892159375, "столова ложка": 14.78676478125, "рідка унція": 29.5735295625,
       "склянка": 236.5882365, "пінта": 473.176473, "кварта": 946.352946, "галон": 3785.411784}
VOLUMES = {
    "metric": _US | {"чайна ложка": 5, "столова ложка": 15, "склянка": 250},
    "us": _US,
    "imperial": {"чайна ложка": 5, "столова ложка": 15, "рідка унція": 28.4130625, "склянка": 250,
                 "пінта": 568.26125, "кварта": 1136.5225, "галон": 4546.09},
}
"""The volumes' sizes, in milliliters, by measures: metric spoons and cup
with US customary larger ones (as built in), US customary, or imperial."""

VOLUME_NAMES = {"metric": "Metric spoons and cup (US otherwise)", "us": "US customary", "imperial": "Imperial (UK)"}

DEFAULT_VOLUMES = "metric"

QUALIFIED = {"американськ": "us", "сша": "us", "us": "us", "британськ": "imperial", "англійськ": "imperial",
             "імперськ": "imperial", "uk": "imperial", "метричн": "metric"}
"""Said before a volume ("американська склянка") or after it ("пінта
США"), in any case: its measures, whichever are set."""

OUNCE, FLUID_OUNCE = "унція", "рідка унція"

_ONE = r"^(?:у|в|на|за|до|з|із|один|одна|одне|одну|одного|одній|одному|однієї|одним|одною)\s+"
_QUALIFIER = "|".join(sorted(QUALIFIED, key=len, reverse=True))


def read_unit(text: str) -> tuple[list[str], str, str | None]:
    """A unit as said ("одна американська склянка", "британських галонах"):
    the names to look up -- the last one bare -- and its measures, if said."""
    s = key(text)
    tries = [s]
    while re.match(_ONE, s):
        s = re.sub(_ONE, "", s)
    s = re.sub(r"^градус\w*\s+(?:за\s+)?", "", s)
    tries.append(s)
    measures = None
    if qualified := re.match(rf"^({_QUALIFIER})\w*\s+", s):
        measures, s = QUALIFIED[qualified.group(1)], s[qualified.end():]
    elif qualified := re.search(rf"\s+({_QUALIFIER})\w*$", s):
        measures, s = QUALIFIED[qualified.group(1)], s[: qualified.start()]
    tries.append(s)
    return tries, s, measures


_ENDINGS = ("ами", "ями", "ого", "ому", "ах", "ях", "ам", "ям", "ів", "їв", "ою", "ею", "єю", "ом", "ем", "єм", "ої",
            "их", "им", "ій", "ий", "и", "і", "ї", "а", "я", "у", "ю", "е", "є", "о", "ь", "й")
_ADDS = ("", "а", "я", "й", "ь", "ий", "ій", "е", "о")


def _nominatives(word: str) -> list[str]:
    """What a word heard in some case may be the nominative of: "милях" ->
    "миля", "склянок" -> "склянка", "ложці" -> "ложка", "чайних" ->
    "чайна"."""
    out = [word]
    if word in _CASES_OF:
        out.append(_CASES_OF[word])
    for ending in _ENDINGS:
        if word.endswith(ending) and len(word) - len(ending) >= 2:
            stem = word[: -len(ending)]
            stems = [stem, *({"ц": [stem[:-1] + "к"], "з": [stem[:-1] + "г"], "с": [stem[:-1] + "х"]}.get(stem[-1], []))]
            out += [s + add for s in stems for add in _ADDS]
    if re.search(rf"[^{_VOWELS}]ок$", word):
        out.append(word[:-2] + "ка")  # "склянок", "ложок"
    out.append(word + "а")  # "пінт", "хвилин"
    return list(dict.fromkeys(out))


def forms(name: str) -> tuple[str, ...]:
    """A name looked up as said, and as the nominatives its words may be the
    cases of: "чайних ложок" -> "чайна ложка"."""
    words = name.split()
    if not words or len(words) > 3:
        return (name,)
    options = [_nominatives(w) for w in words]
    return tuple(dict.fromkeys(" ".join(c) for c in itertools.product(*options)))


def temperature_pattern(scales: str) -> re.Pattern:
    """A conversion between temperature scales: "100 градусів за Фаренгейтом
    у Цельсіях", "скільки буде 20 градусів Цельсія за Фаренгейтом"."""
    return re.compile(
        rf"(?:скільки (?:буде |це |становить )?|переведи |конвертуй |перетвори )?{N} (?:градус\w* )?(?:за )?({scales})"
        rf"\s+(?:у|в|за|на)\s+(?:градус\w* )?(?:за )?({scales})"
    )


def _forms(unit) -> tuple[tuple[str, ...], str]:
    found, gender = inflected(unit.name)
    if unit.plural:
        found = (found[0], found[1], unit.plural, found[3])
    return found, gender


def _said(value: float, unit, places: int) -> tuple[str, str]:
    """As written and as said: ("8,047 кілометра", "вісім кома нуль сорок сім
    кілометра"), ("37,8 градуса Цельсія", ...)."""
    if unit.measures == "temperature":
        written, said = counted(fmt(value, places), ("градус", "градуси", "градусів", "градуса"))
        scale = _SCALE_OF.get(key(unit.name), unit.name)
        return f"{written} {scale}", f"{said} {scale}"
    found, gender = _forms(unit)
    return counted(fmt(value, places), found, gender)


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


_IN = r"(у|в|є у|є в|міститься у|міститься в|вміщується у|вміщується в|входить у|входить в)"
_IS = r"(?:становить|становлять|дорівнює|дорівнюють|буде|це|має|мають|важить|важать)"


def _counted(rest: str, settings) -> tuple[float, str, bool]:
    """What is converted: a count and its unit ("п'яти милях", "5 миль"), or
    a unit alone ("фунті"): (count, unit text, counted)."""
    if (m := re.fullmatch(rf"{N}\s+(.+)", rest)) and parse_num(m.group(1)) is not None and settings.unit(m.group(2)):
        return parse_num(m.group(1)), m.group(2), True
    return 1.0, rest, False


def solve(q, settings):
    """The answer as written, as said, and the conversion said whole ("У
    фунті чотириста п'ятдесят три кома шість грама"); None."""
    places = settings.digits
    s = clean(q)
    temp = settings.temperature.fullmatch(s) if settings.temperature is not None else None
    if temp:
        v = parse_num(temp.group(1))
        a, b = settings.names[temp.group(2)], settings.names[temp.group(3)]
        if v is None or a == b:
            return None
        written, said = _said(settings.convert(v, a, b), b, places)
        return written, said, f"{_said(v, a, places)[1]} — це {said}"
    # "Скільки грамів у фунті", "скільки кілометрів становлять 5 миль", "5 миль
    # це скільки кілометрів", "переведи 5 миль у кілометри": the first that
    # reads as two units.
    if m := re.fullmatch(
        rf"(?:скільки (?:буде |це )?|переведи |конвертуй |перетвори |перевести )?{N}\s+(.+?)\s+(?:у|в|на)\s+(.+)", s
    ):
        v, ua, ub = parse_num(m.group(1)), settings.unit(m.group(2)), settings.unit(m.group(3))
        if v is not None and ua and ub and (r := settings.convert(v, ua, ub)) is not None:
            a, b = settings.ounces(ua, ub)
            written, said = _said(r, b, places)
            return written, said, f"{_said(v, a, places)[1]} — це {said}"
    asked = []
    if m := re.fullmatch(rf"скільки (?:градусів )?(.+?) {_IN} (.+)", s):
        asked.append((m.group(1), m.group(3), m.group(2).split()[-1]))
    if m := re.fullmatch(rf"скільки (?:градусів )?(.+?) {_IS} (.+)", s):
        asked.append((m.group(1), m.group(2), None))
    if m := re.fullmatch(r"(.+?),? (?:це|—|-) скільки (.+)", s):
        asked.append((m.group(2), m.group(1), None))
    for target, rest, preposition in asked:
        n, unit_text, was_counted = _counted(rest, settings)
        ua, ub = settings.unit(unit_text), settings.unit(target)
        if not (ua and ub) or (r := settings.convert(n, ua, ub)) is None:
            continue
        a, b = settings.ounces(ua, ub)
        written, said = _said(r, b, places)
        if preposition:
            restated = f"{_capital(preposition)} {rest} {said}"  # "У фунті 453,6 грама", as asked
        else:
            restated = f"{_said(n, a, places)[1] if was_counted else _capital(a.name)} — це {said}"
        return written, said, restated
    return None


def answer(text: str, settings: Settings):
    found = solve(text, settings)
    if not found:
        return None
    written, said, restated = found
    return reply(written, text, restated, brief=f"{_capital(said)}.", specific=f"Це {said}.")
