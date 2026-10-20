"""How Spanish names units ("taza", "tazas", "una taza americana"), which it
knows built in, and how it asks a conversion ("¿cuántas tazas hay en un
litro?", "¿cuántos kilómetros son 5 millas?", "100 grados Fahrenheit a
Celsius") and says one (voice_call/skills/units.py).

A unit's measures come after it ("una pinta británica", "un galón
estadounidense"). Teaspoons, tablespoons and a cup are metric (5, 15 and 250
ml) as built in, the larger volumes US customary. Units are looked up, and
questions read, without their accents ("kilometros")."""

from __future__ import annotations

import re

from voice_call.skills.common import fmt, reply
from voice_call.skills.units import Settings, _scale, _u

from .numbers import N, clean, fold, parse_num, verb

EXAMPLES = ("cuántos gramos tiene una libra", "cuántos kilómetros son 5 millas", "100 grados Fahrenheit a Celsius")


def plural_of(name: str) -> str:
    """"metro" -> "metros", "galón" -> "galones", "milla náutica" -> "millas
    náuticas": each word of a name ending in a vowel takes -s, in a consonant
    -es."""
    return " ".join(_plural(word) if index == 0 or _adjective(word) else word
                    for index, word in enumerate(name.split()))


def _adjective(word: str) -> bool:
    """"cuadrado", "náutica", "líquida": agrees with its noun."""
    return fold(word.lower()) in ("cuadrado", "cuadrada", "cubico", "cubica", "nautica", "liquida")


def _plural(word: str) -> str:
    if word[-1:].lower() in "aeiouáéíóú":
        return word + "s"
    if word.endswith("ón"):
        return word[:-2] + "ones"
    if word.endswith("z"):
        return word[:-1] + "ces"
    return word + "es"


def key(text: str) -> str:
    """A unit's name as looked up: lowercase, without accents or periods:
    "Kilómetros" -> "kilometros"."""
    return " ".join(fold(text.lower()).replace(".", "").replace("-", " ").split())


UNITS = (
    _u("milímetro", "length", 0.001, "mm"),
    _u("centímetro", "length", 0.01, "cm"),
    _u("metro", "length", 1, "m"),
    _u("kilómetro", "length", 1000, "km"),
    _u("pulgada", "length", 0.0254, "in", "inch", "inches"),
    _u("pie", "length", 0.3048, "ft", "feet", "foot"),
    _u("yarda", "length", 0.9144, "yd"),
    _u("milla", "length", 1609.344, "mi"),
    _u("milla náutica", "length", 1852, "nudo"),
    _u("pulgada cuadrada", "area", 0.00064516),
    _u("pie cuadrado", "area", 0.09290304),
    _u("metro cuadrado", "area", 1, "m2"),
    _u("kilómetro cuadrado", "area", 1e6, "km2"),
    _u("milla cuadrada", "area", 2589988.110336),
    _u("acre", "area", 4046.8564224),
    _u("hectárea", "area", 10000, "ha"),
    _u("mililitro", "volume", 1, "ml"),
    _u("litro", "volume", 1000, "l"),
    _u("cucharadita", "volume", 5, "cdta", "cucharadita de té", "cucharaditas de té"),
    _u("cucharada", "volume", 15, "cda", "cucharada sopera", "cucharadas soperas"),
    _u("onza líquida", "volume", 29.5735295625, "fl oz"),
    _u("taza", "volume", 250, "cup", "cups"),
    _u("pinta", "volume", 473.176473, "pint", "pints"),
    _u("cuarto de galón", "volume", 946.352946, "quart", "quarts", plural="cuartos de galón"),
    _u("galón", "volume", 3785.411784, "gallon", "gallons"),
    _u("miligramo", "weight", 0.001, "mg"),
    _u("gramo", "weight", 1, "g"),
    _u("kilogramo", "weight", 1000, "kg", "kilo", "kilos"),
    _u("onza", "weight", 28.349523125, "oz"),
    _u("libra", "weight", 453.59237, "lb", "lbs"),
    _u("stone", "weight", 6350.29318, plural="stones"),
    _u("tonelada", "weight", 1e6, "t"),
    _u("milisegundo", "time", 0.001, "ms"),
    _u("segundo", "time", 1, "s", "seg"),
    _u("minuto", "time", 60, "min"),
    _u("hora", "time", 3600, "h"),
    _u("día", "time", 86400),
    _u("semana", "time", 604800),
    _u("año", "time", 31536000),
    _u("década", "time", 315360000),
    _u("siglo", "time", 3153600000),
    _u("byte", "data", 1, plural="bytes"),
    _u("kilobyte", "data", 1000, "kb", plural="kilobytes"),
    _u("megabyte", "data", 1e6, "mb", plural="megabytes"),
    _u("gigabyte", "data", 1e9, "gb", plural="gigabytes"),
    _u("terabyte", "data", 1e12, "tb", plural="terabytes"),
    _scale("Celsius", 0, 100, "c", "centígrado", "centígrados"),
    _scale("Fahrenheit", 32, 212, "f"),
    _scale("Kelvin", 273.15, 373.15, "k"),
)
"""The built-in units."""

_US = {"cucharadita": 4.92892159375, "cucharada": 14.78676478125, "onza liquida": 29.5735295625, "taza": 236.5882365,
       "pinta": 473.176473, "cuarto de galon": 946.352946, "galon": 3785.411784}
VOLUMES = {
    "metric": _US | {"cucharadita": 5, "cucharada": 15, "taza": 250},
    "us": _US,
    "imperial": {"cucharadita": 5, "cucharada": 15, "onza liquida": 28.4130625, "taza": 250, "pinta": 568.26125,
                 "cuarto de galon": 1136.5225, "galon": 4546.09},
}
"""The volumes' sizes, in milliliters, by measures: metric spoons and cup
with US customary larger ones (as built in), US customary, or imperial."""

VOLUME_NAMES = {"metric": "Metric spoons and cup (US otherwise)", "us": "US customary", "imperial": "Imperial (UK)"}

DEFAULT_VOLUMES = "metric"

QUALIFIED = {
    **dict.fromkeys(["us", "ee uu", "eeuu", "estadounidense", "estadounidenses", "americana", "americanas",
                     "americano", "americanos"], "us"),
    **dict.fromkeys(["uk", "britanica", "britanicas", "britanico", "britanicos", "inglesa", "inglesas", "ingles",
                     "imperial", "imperiales"], "imperial"),
    **dict.fromkeys(["metrica", "metricas", "metrico", "metricos"], "metric"),
}
"""Said after a volume ("una taza americana"), or before it: its measures,
whichever are set."""

OUNCE, FLUID_OUNCE = "onza", "onza liquida"

_ARTICLE = r"^(?:un|una|uno|unos|unas|el|la|los|las|del|al)\s+"
_QUALIFIERS = "|".join(sorted(QUALIFIED, key=len, reverse=True))


def read_unit(text: str) -> tuple[list[str], str, str | None]:
    """A unit as said ("una taza americana", "galones británicos"): the
    names to look up -- the last one bare -- and its measures, if said."""
    s = key(text)
    tries = [s]
    s = re.sub(_ARTICLE, "", s)
    s = re.sub(r"^grados\s+", "", s)
    tries.append(s)
    measures = None
    if qualified := re.search(rf"\s+(?:de )?({_QUALIFIERS})$", s):
        measures, s = QUALIFIED[qualified.group(1)], s[: qualified.start()]
    elif qualified := re.match(rf"^({_QUALIFIERS})\s+", s):
        measures, s = QUALIFIED[qualified.group(1)], s[qualified.end():]
    tries.append(s)
    return tries, s, measures


def _singular(word: str) -> str:
    """"tazas" -> "taza", "galones" -> "galon" (as looked up, without accents)."""
    return re.sub(r"(?<=[^aeiou])es$|s$", "", word)


def forms(name: str) -> tuple[str, ...]:
    """A name looked up as said, and as its plural might be: "tazas",
    "galones", "metros cuadrados" (its noun and adjective both)."""
    words = name.split()
    singular = " ".join(_singular(w) if i == 0 or _adjective(_singular(w)) else w for i, w in enumerate(words))
    return (name, singular, re.sub(r"s$", "", name), re.sub(r"es$", "", name))


def temperature_pattern(scales: str) -> re.Pattern:
    """A conversion between temperature scales: "100 grados Fahrenheit a
    Celsius", "cuánto son 20 grados Celsius en Fahrenheit"."""
    return re.compile(
        rf"(?:cuanto (?:es|son)|a cuanto (?:equivale|equivalen)|convierte|pasa|cambia)?\s*{N} (?:grados )?({scales})"
        rf"\s+(?:a|en)\s+(?:grados )?({scales})"
    )


def _said(value: float, unit, places: int) -> str:
    """"8,047 kilómetros", "37,8 grados Celsius"."""
    if unit.measures == "temperature":
        return f"{fmt(value, places)} grados {unit.name}"
    return f"{fmt(value, places)} {unit.said(value)}"


def solve(q, settings):
    """The answer, its sentence and the conversion said whole ("5 millas son
    8,047 kilómetros"); None."""
    places = settings.digits
    s = fold(clean(q))
    temp = settings.temperature.fullmatch(s) if settings.temperature is not None else None
    if temp:
        v = parse_num(temp.group(1))
        a, b = settings.names[temp.group(2)], settings.names[temp.group(3)]
        if v is None or a == b:
            return None
        found = _said(settings.convert(v, a, b), b, places)
        return found, f"Son {found}.", f"{_said(v, a, places)} son {found}"
    if m := re.fullmatch(
        r"cuant(?:os|as) (?:grados )?(.+?) (hay en|caben en|entran en|tiene|tienen|son|es|equivale a|equivalen a|mide|miden|pesa|pesan) (.+)", s
    ):
        target, verb_, rest = m.groups()
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
        found = _said(r, settings.ounces(ub, ua)[0], places)
        # What was asked about, said with its name: "un litro", "5 pulgadas".
        article = re.match(r"^(\S+)\s+", rest) if re.match(_ARTICLE, rest) else None
        said = _said(n, ua, places) if counted else f"{article.group(1)} {ua.name}" if article else ua.name
        if verb_.endswith(" en"):
            restated = f"En {said} {verb_.split()[0]} {found}"
        elif verb_ in ("son", "es"):
            restated = f"{_capital(said)} {verb(said) if counted else 'es'} {found}"
        else:
            restated = f"{_capital(said)} {verb_} {found}"  # "una libra tiene 453,6 gramos"
        return found, f"{verb(found).capitalize()} {found}.", restated
    m = re.fullmatch(
        rf"(?:cuanto (?:es|son)|a cuanto (?:equivale|equivalen)|convierte|pasa|cambia)?\s*{N}\s+(.+?)\s+(?:a|en)\s+(.+)",
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
            asked = _said(v, a, places)
            return found, f"{verb(found).capitalize()} {found}.", f"{asked} {verb(asked)} {found}"
    return None


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


def answer(text: str, settings: Settings):
    found = solve(text, settings)
    if not found:
        return None
    said, specific, restated = found
    return reply(said, text, restated, specific=specific)
