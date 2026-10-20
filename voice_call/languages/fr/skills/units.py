"""How French names units ("tasse", "tasses", "une tasse américaine"), which
it knows built in, and how it asks a conversion ("combien de tasses dans un
litre", "combien font 5 miles en kilomètres", "100 degrés Fahrenheit en
Celsius") and says one (voice_call/skills/units.py).

A "livre" is 500 grams, as France weighs at the market; the US and UK pound
is the "livre anglaise" ("lb"). Spoons and a cup are metric (5, 15 and 250
ml) as built in, the larger volumes US customary."""

from __future__ import annotations

import re

from voice_call.skills.common import fmt, reply
from voice_call.skills.units import Settings, _scale, _u

from .numbers import N, clean, parse_num

EXAMPLES = ("combien de grammes dans une livre", "combien font 5 miles en kilomètres", "100 degrés Fahrenheit en Celsius")

_LINKS = ("à", "de", "d'")
"""Where a unit's name stops agreeing: "cuillères à café"."""


def _plural(word: str) -> str:
    return word if word[-1:] in ("s", "x", "z") else word + "s"


def plural_of(name: str) -> str:
    """"tasse" -> "tasses", "mètre carré" -> "mètres carrés", "cuillère à
    café" -> "cuillères à café"; "pouces", "mois" stay."""
    words = name.split()
    for index, word in enumerate(words):
        if word in _LINKS:
            break
        words[index] = _plural(word)
    return " ".join(words)


def key(text: str) -> str:
    """A unit's name as looked up: lowercase, no periods ("c. à s." as "c à
    s"), no hyphens."""
    return " ".join(text.lower().replace("’", "'").replace(".", " ").replace("-", " ").split())


UNITS = (
    _u("millimètre", "length", 0.001, "mm"),
    _u("centimètre", "length", 0.01, "cm"),
    _u("mètre", "length", 1, "m"),
    _u("kilomètre", "length", 1000, "km"),
    _u("pouce", "length", 0.0254, "in", "inch", "inches"),
    _u("pied", "length", 0.3048, "ft", "foot", "feet"),
    _u("yard", "length", 0.9144, "yd"),
    _u("mile", "length", 1609.344, "mi"),
    _u("mille marin", "length", 1852, "mille nautique", "milles nautiques"),
    _u("pouce carré", "area", 0.00064516),
    _u("pied carré", "area", 0.09290304),
    _u("mètre carré", "area", 1, "m2"),
    _u("kilomètre carré", "area", 1e6, "km2"),
    _u("mile carré", "area", 2589988.110336),
    _u("acre", "area", 4046.8564224),
    _u("are", "area", 100),
    _u("hectare", "area", 10000, "ha"),
    _u("millilitre", "volume", 1, "ml"),
    _u("centilitre", "volume", 10, "cl"),
    _u("décilitre", "volume", 100, "dl"),
    _u("litre", "volume", 1000, "l"),
    _u("cuillère à café", "volume", 5, "c à c", "cc", "cuillère à thé", "cuillères à thé", "petite cuillère", "petites cuillères"),
    _u("cuillère à soupe", "volume", 15, "c à s", "cs", "cuillère à table", "cuillères à table", "grande cuillère", "grandes cuillères"),
    _u("once liquide", "volume", 29.5735295625, "fl oz"),
    _u("tasse", "volume", 250, "cup", "cups"),
    _u("pinte", "volume", 473.176473, "pint", "pints"),
    _u("quart", "volume", 946.352946),
    _u("gallon", "volume", 3785.411784),
    _u("milligramme", "weight", 0.001, "mg"),
    _u("gramme", "weight", 1, "g"),
    _u("kilogramme", "weight", 1000, "kg", "kilo", "kilos"),
    _u("livre", "weight", 500),
    _u("once", "weight", 28.349523125, "oz", "ounce", "ounces"),
    _u("livre anglaise", "weight", 453.59237, "lb", "lbs", "pound", "pounds"),
    _u("stone", "weight", 6350.29318),
    _u("tonne", "weight", 1e6, "t"),
    _u("milliseconde", "time", 0.001, "ms"),
    _u("seconde", "time", 1, "s", "sec"),
    _u("minute", "time", 60, "min"),
    _u("heure", "time", 3600, "h"),
    _u("jour", "time", 86400, "journée", "journées"),
    _u("semaine", "time", 604800),
    _u("an", "time", 31536000, "année", "années"),
    _u("décennie", "time", 315360000),
    _u("siècle", "time", 3153600000),
    _u("octet", "data", 1, "byte", "bytes"),
    _u("kilooctet", "data", 1000, "ko", "kb", "kilobyte", "kilobytes"),
    _u("mégaoctet", "data", 1e6, "mo", "mb", "megabyte", "megabytes"),
    _u("gigaoctet", "data", 1e9, "go", "gb", "gigabyte", "gigabytes"),
    _u("téraoctet", "data", 1e12, "to", "tb", "terabyte", "terabytes"),
    _scale("Celsius", 0, 100, "c"),
    _scale("Fahrenheit", 32, 212, "f"),
    _scale("Kelvin", 273.15, 373.15, "k"),
)

_US = {"cuillère à café": 4.92892159375, "cuillère à soupe": 14.78676478125, "once liquide": 29.5735295625,
       "tasse": 236.5882365, "pinte": 473.176473, "quart": 946.352946, "gallon": 3785.411784}
VOLUMES = {
    "metric": _US | {"cuillère à café": 5, "cuillère à soupe": 15, "tasse": 250},
    "us": _US,
    "imperial": {"cuillère à café": 5, "cuillère à soupe": 15, "once liquide": 28.4130625, "tasse": 250,
                 "pinte": 568.26125, "quart": 1136.5225, "gallon": 4546.09},
}
VOLUME_NAMES = {"metric": "Metric spoons and cup (US otherwise)", "us": "US customary", "imperial": "Imperial (UK)"}
DEFAULT_VOLUMES = "metric"

QUALIFIED = {
    **dict.fromkeys(["us", "américain", "américaine", "américains", "américaines"], "us"),
    **dict.fromkeys(["uk", "impérial", "impériale", "impériaux", "impériales", "britannique", "britanniques"], "imperial"),
    **dict.fromkeys(["métrique", "métriques"], "metric"),
}

OUNCE, FLUID_OUNCE = "once", "once liquide"

_ARTICLE = r"^(?:une?|le|la|les)\s+|^l'\s*"


def read_unit(text: str) -> tuple[list[str], str, str | None]:
    """"une tasse américaine", "une pinte impériale", "US cups": the unit,
    and the measures named before or after it."""
    s = key(text)
    tries = [s]
    s = re.sub(_ARTICLE, "", s)
    tries.append(s)
    qualifiers = "|".join(QUALIFIED)
    qualified = re.match(rf"^({qualifiers})\s+", s) or re.search(rf"\s+({qualifiers})$", s)
    measures = QUALIFIED[qualified.group(1)] if qualified else None
    if qualified:
        s = (s[qualified.end():] if qualified.start() == 0 else s[:qualified.start()]).strip()
    tries.append(s)
    return tries, s, measures


def forms(name: str) -> tuple[str, ...]:
    """A name as said, and as its plural might be: "tasses", "mètres carrés",
    "cuillères à café"."""
    words = name.split()
    head = [re.sub(r"(?<=.)[sx]$", "", words[0]), *words[1:]] if words else words
    agreed: list[str] = []
    linked = False
    for word in words:
        linked = linked or word in _LINKS
        agreed.append(word if linked else re.sub(r"(?<=.)[sx]$", "", word))
    return (name, " ".join(head), " ".join(agreed))


_CONVERT = r"(?:combien (?:font|fait|vaut|valent|ça fait)|convertis|convertir|convertissez|c'est combien)?"


def temperature_pattern(scales: str) -> re.Pattern:
    return re.compile(
        rf"{_CONVERT}\s*{N} (?:degrés? )?({scales})"
        rf"\s+(?:en|vers)\s+(?:degrés? )?({scales})(?:,? (?:ça fait|c'est) combien)?"
    )


def _said(n: float, unit, counted: bool, rest: str, places: int) -> str:
    """The unit converted from, as asked: "5 miles", "une pinte impériale"."""
    return f"{fmt(n, places)} {unit.said(n)}" if counted else rest


def solve(q, settings):
    places = settings.digits
    s = clean(q)
    temp = settings.temperature.fullmatch(s) if settings.temperature is not None else None
    if temp:
        v = parse_num(temp.group(1))
        a, b = settings.names[temp.group(2)], settings.names[temp.group(3)]
        if v is None or a == b:
            return None
        found = f"{fmt(settings.convert(v, a, b), places)} degrés {b.name}"
        return found, f"Ça fait {found}.", f"{fmt(v, places)} degrés {a.name}, ça fait {found}"
    asked = re.fullmatch(
        r"combien (?:de |d')(?:degrés )?(.+?) (?:y a t il dans|il y a dans|y a t il en|dans|en|fait|font|vaut|valent|contient|contiennent|pour) (.+)", s
    )
    if not asked and (back := re.fullmatch(r"(.+?),? (?:ça )?(?:fait|font|vaut|valent|contient|contiennent|égale|égalent) combien (?:de |d')(.+)", s)):
        asked = back
        target, rest = back.group(2), back.group(1)
    elif asked:
        target, rest = asked.groups()
    if asked:
        n = 1.0
        counted = False
        mm = re.fullmatch(rf"{N}\s*(.+)", rest)
        if mm and parse_num(mm.group(1)) is not None and settings.unit(mm.group(2)) and not re.match(_ARTICLE, rest):
            n, rest, counted = parse_num(mm.group(1)), mm.group(2), True
        ua, ub = settings.unit(rest), settings.unit(target)
        if not (ua and ub):
            return None
        r = settings.convert(n, ua, ub)
        if r is None:
            return None
        unit = settings.ounces(ub, ua)[0]
        found = f"{fmt(r, places)} degrés {unit.name}" if unit.measures == "temperature" else f"{fmt(r, places)} {unit.said(r)}"
        said = _said(n, ua, counted, rest, places)
        return found, f"Ça fait {found}.", f"Il y a {found} dans {said}"
    m = re.fullmatch(rf"{_CONVERT}\s*{N}\s+(.+?)\s+(?:en|vers)\s+(.+?)(?:,? (?:ça fait|c'est) combien)?", s)
    if m:
        v, ua, ub = parse_num(m.group(1)), settings.unit(m.group(2)), settings.unit(m.group(3))
        if v is not None and ua and ub:
            r = settings.convert(v, ua, ub)
            if r is None:
                return None
            a, b = settings.ounces(ua, ub)
            if a.measures == "temperature":
                found = f"{fmt(r, places)} degrés {b.name}"
                return found, f"Ça fait {found}.", f"{fmt(v, places)} degrés {a.name}, ça fait {found}"
            found = f"{fmt(r, places)} {b.said(r)}"
            return found, f"Ça fait {found}.", f"{fmt(v, places)} {a.said(v)}, ça fait {found}"
    return None


def answer(text: str, settings: Settings):
    found = solve(text, settings)
    if not found:
        return None
    said, specific, restated = found
    return reply(said, text, restated, specific=specific)
