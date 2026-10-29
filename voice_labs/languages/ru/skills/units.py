"""How Russian names units ("стакан", "стакана", "стаканов"), which it knows
built in, and how it asks a conversion ("сколько граммов в фунте?",
"сколько километров в пяти милях?", "переведи 100 градусов по Фаренгейту в
Цельсий") and says one (voice_labs/skills/units.py).

A unit is asked in any case ("в фунте", "граммов", "в милях"): it is looked
up by the nominatives its words may come from (``forms``). Said, it takes the
form its count asks for: 1, 21 "метр"; 2-4 "метра"; 5 and on "метров"; a
fraction "1,5 метра" -- built in, each unit's own (``SAID``), else its name
and plural. A teaspoon, a tablespoon and a cup are metric (5, 15 and 250 ml)
as built in -- a Russian стакан is 250 ml --, the larger volumes US
customary."""

from __future__ import annotations

import re

from voice_labs.skills.common import fmt, reply
from voice_labs.skills.units import Settings, _scale, _u

from ..numerals import cardinal, plural
from .numbers import N, clean, fold, parse_num

EXAMPLES = ("сколько граммов в фунте", "сколько километров в пяти милях", "100 градусов по Фаренгейту в Цельсий")


def key(text: str) -> str:
    """A unit's name as looked up: lowercase, "ё" as "е", without periods:
    "Чайная ложка" -> "чайная ложка"."""
    return " ".join(fold(text).replace(".", " ").replace("-", " ").split())


_ADJECTIVE = re.compile(r"(?:ый|ий|ой|ая|яя|ое|ее)$")


def plural_of(name: str) -> str:
    """After five and on, as most plurals are said: "метр" -> "метров",
    "миля" -> "миль", "унция" -> "унций", "чайная ложка" -> "чайных ложек"
    (roughly: a unit of yours may give its own)."""
    return " ".join(_many(word) for word in name.split())


def _many(word: str) -> str:
    lower = word.lower()
    if _ADJECTIVE.search(lower):
        return word[:-2] + ("их" if lower[-3:-2] in "кгхжшчщ" or lower.endswith(("ий", "яя", "ее")) else "ых")
    if lower.endswith(("ия", "ие")):
        return word[:-1] + "й"
    if lower.endswith("ка") and lower[-3:-2] not in "аеиоуыэюя":
        return word[:-2] + ("ек" if lower[-3:-2] in "жшчщ" else "ок")  # чашек, соток
    if lower.endswith(("а", "о")):
        return word[:-1]
    if lower.endswith("я"):
        return word[:-1] + "ь"
    if lower.endswith("ь"):
        return word[:-1] + "ей"
    if lower.endswith(("ж", "ш", "ч", "щ")):
        return word + "ей"
    if lower[-1:] in "аеиоуыэюя" or not lower[-1:].isalpha():
        return word
    return word + "ов"


UNITS = (
    _u("миллиметр", "length", 0.001, "мм"),
    _u("сантиметр", "length", 0.01, "см"),
    _u("метр", "length", 1, "м"),
    _u("километр", "length", 1000, "км"),
    _u("дюйм", "length", 0.0254, "inch", "inches"),
    _u("фут", "length", 0.3048, "foot", "feet"),
    _u("ярд", "length", 0.9144),
    _u("миля", "length", 1609.344, "mi"),
    _u("морская миля", "length", 1852),
    _u("квадратный дюйм", "area", 0.00064516),
    _u("квадратный фут", "area", 0.09290304),
    _u("квадратный метр", "area", 1, "м2", "кв м"),
    _u("квадратный километр", "area", 1e6, "км2", "кв км"),
    _u("квадратная миля", "area", 2589988.110336),
    _u("сотка", "area", 100, "ар"),
    _u("акр", "area", 4046.8564224),
    _u("гектар", "area", 10000, "га"),
    _u("миллилитр", "volume", 1, "мл"),
    _u("литр", "volume", 1000, "л"),
    _u("чайная ложка", "volume", 5, "ч л", "чайных ложек"),
    _u("столовая ложка", "volume", 15, "ст л", "столовых ложек"),
    _u("жидкая унция", "volume", 29.5735295625, "fl oz"),
    _u("стакан", "volume", 250, "cup", "cups"),
    _u("пинта", "volume", 473.176473, "pint", "pints"),
    _u("кварта", "volume", 946.352946, "quart", "quarts"),
    _u("галлон", "volume", 3785.411784, "gallon", "gallons"),
    _u("миллиграмм", "weight", 0.001, "мг"),
    _u("грамм", "weight", 1, "г", "гр"),
    _u("килограмм", "weight", 1000, "кг", "кило"),
    _u("центнер", "weight", 100000, "ц"),
    _u("унция", "weight", 28.349523125, "oz"),
    _u("фунт", "weight", 453.59237, "lb", "lbs"),
    _u("стоун", "weight", 6350.29318),
    _u("тонна", "weight", 1e6, "т"),
    _u("миллисекунда", "time", 0.001, "мс"),
    _u("секунда", "time", 1, "с", "сек"),
    _u("минута", "time", 60, "мин"),
    _u("час", "time", 3600, "ч"),
    _u("день", "time", 86400, "сутки", plural="дней"),
    _u("неделя", "time", 604800),
    _u("год", "time", 31536000, plural="лет"),
    _u("десятилетие", "time", 315360000),
    _u("век", "time", 3153600000, "столетие"),
    _u("байт", "data", 1),
    _u("килобайт", "data", 1000, "кб"),
    _u("мегабайт", "data", 1e6, "мб"),
    _u("гигабайт", "data", 1e9, "гб"),
    _u("терабайт", "data", 1e12, "тб"),
    _scale("Цельсий", 0, 100, "c", "цельсия", "цельсию", "градус цельсия"),
    _scale("Фаренгейт", 32, 212, "f", "фаренгейта", "фаренгейту", "градус фаренгейта"),
    _scale("Кельвин", 273.15, 373.15, "k", "кельвина", "кельвину", "кельвинов"),
)
"""The built-in units."""

_FEMININE = {"миля", "морская миля", "квадратная миля", "сотка", "чайная ложка", "столовая ложка", "жидкая унция",
             "пинта", "кварта", "унция", "тонна", "миллисекунда", "секунда", "минута", "неделя"}

SAID = {
    # name: after 1, after 2-4 and a fraction (the genitive singular), after 5 and on
    "квадратный дюйм": ("квадратный дюйм", "квадратных дюйма", "квадратных дюймов", "квадратного дюйма"),
    "квадратный фут": ("квадратный фут", "квадратных фута", "квадратных футов", "квадратного фута"),
    "квадратный метр": ("квадратный метр", "квадратных метра", "квадратных метров", "квадратного метра"),
    "квадратный километр": ("квадратный километр", "квадратных километра", "квадратных километров",
                            "квадратного километра"),
    "квадратная миля": ("квадратная миля", "квадратные мили", "квадратных миль", "квадратной мили"),
    "морская миля": ("морская миля", "морские мили", "морских миль", "морской мили"),
    "чайная ложка": ("чайная ложка", "чайные ложки", "чайных ложек", "чайной ложки"),
    "столовая ложка": ("столовая ложка", "столовые ложки", "столовых ложек", "столовой ложки"),
    "жидкая унция": ("жидкая унция", "жидкие унции", "жидких унций", "жидкой унции"),
    "сотка": ("сотка", "сотки", "соток", "сотки"),
    "миля": ("миля", "мили", "миль", "мили"),
    "унция": ("унция", "унции", "унций", "унции"),
    "неделя": ("неделя", "недели", "недель", "недели"),
    "день": ("день", "дня", "дней", "дня"),
    "год": ("год", "года", "лет", "года"),
    "десятилетие": ("десятилетие", "десятилетия", "десятилетий", "десятилетия"),
    "кило": ("кило", "кило", "кило", "кило"),
}
"""How a unit is said after a count, where its plural doesn't say it."""

_US = {"чайная ложка": 4.92892159375, "столовая ложка": 14.78676478125, "жидкая унция": 29.5735295625,
       "стакан": 236.5882365, "пинта": 473.176473, "кварта": 946.352946, "галлон": 3785.411784}
VOLUMES = {
    "metric": _US | {"чайная ложка": 5, "столовая ложка": 15, "стакан": 250},
    "us": _US,
    "imperial": {"чайная ложка": 5, "столовая ложка": 15, "жидкая унция": 28.4130625, "стакан": 250, "пинта": 568.26125,
                 "кварта": 1136.5225, "галлон": 4546.09},
}
"""The volumes' sizes, in milliliters, by measures: metric spoons and cup
with US customary larger ones (as built in), US customary, or imperial."""

VOLUME_NAMES = {"metric": "Metric spoons and cup (US otherwise)", "us": "US customary", "imperial": "Imperial (UK)"}

DEFAULT_VOLUMES = "metric"

QUALIFIED = {
    **dict.fromkeys(["американский", "американская", "американского", "американской", "американском",
                     "американских", "американские", "сша", "us"], "us"),
    **dict.fromkeys(["британский", "британская", "британского", "британской", "британском", "британских",
                     "британские", "английский", "английская", "английского", "английской", "английском",
                     "английских", "имперский", "имперская", "имперского", "имперской", "имперском", "имперских",
                     "uk"], "imperial"),
    **dict.fromkeys(["метрический", "метрическая", "метрического", "метрической", "метрическом",
                     "метрических"], "metric"),
}
"""Said before a volume ("американский стакан"), or after it ("стакан
США"): its measures, whichever are set."""

OUNCE, FLUID_OUNCE = "унция", "жидкая унция"

_ONE = r"^(?:один|одна|одно|одного|одной|одном|одну|одним)\s+"
_QUALIFIERS = "|".join(sorted(QUALIFIED, key=len, reverse=True))


def read_unit(text: str) -> tuple[list[str], str, str | None]:
    """A unit as said ("американском стакане", "галлонов США"): the names to
    look up -- the last one bare -- and its measures, if said."""
    s = key(text)
    tries = [s]
    s = re.sub(_ONE, "", s)
    s = re.sub(r"^(?:градус\w*\s+)?(?:по\s+)?", "", s)
    tries.append(s)
    measures = None
    if qualified := re.search(rf"\s+({_QUALIFIERS})$", s):
        measures, s = QUALIFIED[qualified.group(1)], s[: qualified.start()]
    elif qualified := re.match(rf"^({_QUALIFIERS})\s+", s):
        measures, s = QUALIFIED[qualified.group(1)], s[qualified.end():]
    tries.append(s)
    return tries, s, measures


_CASE_ENDINGS = sorted((
    "ами", "ями", "ого", "его", "ому", "ему", "ыми", "ими", "ах", "ях", "ов", "ев", "ей", "ой", "ий", "ый", "ая",
    "яя", "ое", "ее", "ые", "ие", "ую", "юю", "ом", "ем", "ам", "ям", "ых", "их", "ым", "им", "ия", "ии", "ию",
    "иям", "иях", "иями", "ием", "а", "я", "о", "е", "ы", "и", "у", "ю", "ь", "й",
), key=len, reverse=True)
_NOMINATIVE = ("", "а", "я", "ь", "й", "о", "е", "ия", "ие", "ый", "ий", "ой", "ая", "яя", "ое", "ее")
_IRREGULAR = {"дня": "день", "дней": "день", "дню": "день", "днях": "день", "днем": "день", "дням": "день",
              "лет": "год", "годов": "год", "суток": "сутки", "ложек": "ложка", "соток": "сотка"}


def _nominatives(word: str) -> list[str]:
    """The nominatives ``word`` may be a case of: "милях" -> "миля",
    "граммов" -> "грамм", "чайных" -> "чайная", "ложек" -> "ложка"."""
    if word in _IRREGULAR:
        return [word, _IRREGULAR[word]]
    stems = [word] + [word[: -len(e)] for e in _CASE_ENDINGS if word.endswith(e) and len(word) - len(e) >= 2]
    return list(dict.fromkeys([word, *(stem + e for stem in stems for e in _NOMINATIVE)]))


def forms(name: str) -> tuple[str, ...]:
    """A name looked up as said, and as the nominative it may be a case of:
    "квадратных метрах" -> "квадратный метр"."""
    words = name.split()
    if not words or len(words) > 2:
        return (name,)
    if len(words) == 1:
        return tuple(_nominatives(words[0]))
    return (name, *(f"{a} {b}" for a in _nominatives(words[0]) for b in _nominatives(words[1])))


def temperature_pattern(scales: str) -> re.Pattern:
    """A conversion between temperature scales: "100 градусов по Фаренгейту в
    Цельсий", "сколько будет 20 градусов Цельсия по Фаренгейту"."""
    return re.compile(
        rf"(?:сколько (?:будет|это)|переведи|переведите|перевести|конвертируй)?\s*{N} (?:градус\w* )?(?:по )?({scales})\w*"
        rf"\s+(?:в|по)\s+(?:градус\w* )?(?:по )?({scales})\w*"
    )


def _forms(unit) -> tuple[str, str, str, str]:
    """How a unit is said after 1, 2-4, 5 and on, and a fraction: its own
    (``SAID``), else by its name's ending -- "пинта", "пинты", "пинт";
    "литр", "литра", "литров"."""
    name, many = unit.name, unit.plural_name
    if key(name) in SAID:
        return SAID[key(name)]
    lower = name.lower()
    if " " in lower:
        few = many
    elif lower.endswith("а"):
        few = name[:-1] + ("и" if lower[-2:-1] in "гкхжшчщ" else "ы")
    elif lower.endswith(("я", "ь")):
        few = name[:-1] + ("и" if lower.endswith("я") or key(name) in _FEMININE else "я")
    elif lower.endswith("ие"):
        few = name[:-1] + "я"
    elif re.search(r"[бвгджзклмнпрстфхцчшщ]$", lower):
        few = name + "а"
    else:
        few = many
    return name, few, many, few


def _said(value: float, unit, places: int) -> str:
    """"8,047 километра", "две мили", "37,8 градуса Цельсия"."""
    number = fmt(value, places)
    if unit.measures == "temperature":
        name = {"цельсий": "Цельсия", "фаренгейт": "Фаренгейта"}.get(key(unit.name))
        if key(unit.name) == "кельвин":
            return f"{number} {plural(value, 'кельвин', 'кельвина', 'кельвинов')}"
        if name is None:
            return f"{number} {unit.name}"
        return f"{number} {plural(value, 'градус', 'градуса', 'градусов')} {name}"
    one, few, many, part = _forms(unit)
    word = part if not float(value).is_integer() else plural(value, one, few, many)
    whole = int(abs(value)) if float(value).is_integer() and abs(value) < 10**6 else None
    if whole is not None and key(unit.name) in _FEMININE and whole % 10 in (1, 2) and whole % 100 not in (11, 12):
        number = cardinal(int(value), "f")  # "две мили": a voice reads "2 мили" as "два мили"
    return f"{number} {word}"


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


def solve(q, settings):
    """The answer, its sentence and the conversion said whole ("5 миль — это
    8,047 километра"); None."""
    places = settings.digits
    s = clean(q)
    temp = settings.temperature.fullmatch(s) if settings.temperature is not None else None
    if temp:
        v = parse_num(temp.group(1))
        a, b = settings.unit(temp.group(2)), settings.unit(temp.group(3))
        if v is None or a is None or b is None or a == b:
            return None
        found = _said(settings.convert(v, a, b), b, places)
        return found, f"Это {found}.", f"{_said(v, a, places)} — это {found}"
    # "Сколько граммов в фунте", "сколько километров в пяти милях".
    if m := re.fullmatch(r"сколько (?:всего )?(.+?) (?:в|во|содержится в|помещается в) (.+)", s):
        target, rest = m.groups()
        n, said = 1.0, rest
        if (mm := re.fullmatch(rf"{N}\s+(.+)", rest)) and parse_num(mm.group(1)) is not None and not re.match(_ONE, rest + " "):
            n, said = parse_num(mm.group(1)), mm.group(2)
        ua, ub = settings.unit(said), settings.unit(target)
        r = settings.convert(n, ua, ub) if ua and ub else None
        if r is not None:
            found = _said(r, settings.ounces(ub, ua)[0], places)
            return found, f"Это {found}.", f"{'Во' if re.match(r'[вф][^аеиоуыэюя]', rest) else 'В'} {rest} {found}"
    # "5 миль в километрах", "переведи 5 миль в километры", "сколько будет 5 миль в километрах".
    m = re.fullmatch(
        rf"(?:сколько (?:будет|это)|переведи|переведите|перевести|конвертируй)?\s*{N}\s+(.+?)\s+(?:в|во)\s+(.+?)"
        r"(?: это сколько| сколько это| будет)?",
        s,
    )
    if m:
        v, ua, ub = parse_num(m.group(1)), settings.unit(m.group(2)), settings.unit(m.group(3))
        if v is not None and ua and ub:
            r = settings.convert(v, ua, ub)
            if r is None:
                return None
            a, b = settings.ounces(ua, ub)
            found = _said(r, b, places)
            return found, f"Это {found}.", f"{_said(v, a, places)} — это {found}"
    return None


def answer(text: str, settings: Settings):
    found = solve(text, settings)
    if not found:
        return None
    said, specific, restated = found
    return reply(said, text, restated, specific=specific)
