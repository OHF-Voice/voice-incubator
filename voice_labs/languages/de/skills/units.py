"""How German names units ("Tasse", "Tassen", "eine amerikanische Tasse"),
which it knows built in, and how it asks a conversion ("wie viele Tassen sind
in einem Liter", "was sind 5 Meilen in Kilometern", "100 Grad Fahrenheit in
Celsius") and says one (voice_labs/skills/units.py).

A "Pfund" is 500 grams, as German uses it; the US and UK pound is a
"Pound". Teaspoons, tablespoons and a cup are metric (5, 15 and 250 ml) as
built in -- German recipes' -- the larger volumes US customary."""

from __future__ import annotations

import re

from voice_labs.skills.common import fmt, reply
from voice_labs.skills.units import Settings, _scale, _u

from .numbers import N, clean, parse_num

EXAMPLES = ("wie viele Gramm hat ein Pfund", "was sind 5 Meilen in Kilometern", "100 Grad Fahrenheit in Celsius")


def plural_of(name: str) -> str:
    """"Tasse" -> "Tassen"; "Meter", "Gramm", "Zoll" stay."""
    return name + "n" if name.endswith("e") else name


def key(text: str) -> str:
    """A unit's name as looked up: lowercase, "US-Tasse" as "us tasse",
    "Quadrat Meter" as "quadratmeter", no periods."""
    s = text.lower().replace(".", "").replace("-", " ")
    s = re.sub(r"\bquadrat\s+", "quadrat", s)
    return " ".join(s.split())


UNITS = (
    _u("Millimeter", "length", 0.001, "mm"),
    _u("Zentimeter", "length", 0.01, "cm"),
    _u("Meter", "length", 1, "m"),
    _u("Kilometer", "length", 1000, "km"),
    _u("Zoll", "length", 0.0254, "in", "Inch", "Inches"),
    _u("Fuß", "length", 0.3048, "ft", "Fuss", "Feet"),
    _u("Yard", "length", 0.9144, "yd", plural="Yards"),
    _u("Meile", "length", 1609.344, "mi"),
    _u("Seemeile", "length", 1852, "sm"),
    _u("Quadratzoll", "area", 0.00064516),
    _u("Quadratfuß", "area", 0.09290304),
    _u("Quadratmeter", "area", 1, "qm", "m2"),
    _u("Quadratkilometer", "area", 1e6, "km2"),
    _u("Quadratmeile", "area", 2589988.110336),
    _u("Acre", "area", 4046.8564224, plural="Acres"),
    _u("Hektar", "area", 10000, "ha"),
    _u("Milliliter", "volume", 1, "ml"),
    _u("Liter", "volume", 1000, "l"),
    _u("Teelöffel", "volume", 5, "tl"),
    _u("Esslöffel", "volume", 15, "el"),
    _u("Flüssigunze", "volume", 29.5735295625, "fl oz"),
    _u("Tasse", "volume", 250, "Cup", "Cups"),
    _u("Pint", "volume", 473.176473, plural="Pints"),
    _u("Quart", "volume", 946.352946, plural="Quarts"),
    _u("Gallone", "volume", 3785.411784),
    _u("Milligramm", "weight", 0.001, "mg"),
    _u("Gramm", "weight", 1, "g"),
    _u("Kilogramm", "weight", 1000, "kg", "Kilo"),
    _u("Unze", "weight", 28.349523125, "oz"),
    _u("Pfund", "weight", 500),
    _u("Pound", "weight", 453.59237, "lb", "lbs", plural="Pounds"),
    _u("Stone", "weight", 6350.29318, plural="Stones"),
    _u("Zentner", "weight", 50000),
    _u("Tonne", "weight", 1e6, "t"),
    _u("Millisekunde", "time", 0.001, "ms"),
    _u("Sekunde", "time", 1, "sek", "s"),
    _u("Minute", "time", 60, "min"),
    _u("Stunde", "time", 3600, "std", "h"),
    _u("Tag", "time", 86400, plural="Tage"),
    _u("Woche", "time", 604800),
    _u("Jahr", "time", 31536000, plural="Jahre"),
    _u("Jahrzehnt", "time", 315360000, plural="Jahrzehnte"),
    _u("Jahrhundert", "time", 3153600000, plural="Jahrhunderte"),
    _u("Byte", "data", 1, plural="Bytes"),
    _u("Kilobyte", "data", 1000, "kb", plural="Kilobytes"),
    _u("Megabyte", "data", 1e6, "mb", plural="Megabytes"),
    _u("Gigabyte", "data", 1e9, "gb", plural="Gigabytes"),
    _u("Terabyte", "data", 1e12, "tb", plural="Terabytes"),
    _scale("Celsius", 0, 100, "c"),
    _scale("Fahrenheit", 32, 212, "f"),
    _scale("Kelvin", 273.15, 373.15, "k"),
)
"""The built-in units."""

_US = {"teelöffel": 4.92892159375, "esslöffel": 14.78676478125, "flüssigunze": 29.5735295625, "tasse": 236.5882365,
       "pint": 473.176473, "quart": 946.352946, "gallone": 3785.411784}
VOLUMES = {
    "metric": _US | {"teelöffel": 5, "esslöffel": 15, "tasse": 250},
    "us": _US,
    "imperial": {"teelöffel": 5, "esslöffel": 15, "flüssigunze": 28.4130625, "tasse": 250, "pint": 568.26125,
                 "quart": 1136.5225, "gallone": 4546.09},
}
"""The volumes' sizes, in milliliters, by measures: metric spoons and cup
with US customary larger ones (as built in), US customary, or imperial."""

VOLUME_NAMES = {"metric": "Metric spoons and cup (US otherwise)", "us": "US customary", "imperial": "Imperial (UK)"}

DEFAULT_VOLUMES = "metric"

QUALIFIED = {
    **dict.fromkeys(["us", "amerikanische", "amerikanischen", "amerikanischer", "amerikanisches"], "us"),
    **dict.fromkeys(["uk", "britische", "britischen", "britischer", "britisches", "englische", "englischen",
                     "englischer", "englisches", "imperiale", "imperialen", "imperialer"], "imperial"),
    **dict.fromkeys(["metrische", "metrischen", "metrischer", "metrisches"], "metric"),
}
"""Said before a volume ("eine amerikanische Tasse"): its measures, whichever are set."""

OUNCE, FLUID_OUNCE = "unze", "flüssigunze"

_ARTICLE = r"^(?:ein|eine|einen|einem|einer|eines|der|die|das|den|dem|des)\s+"


def read_unit(text: str) -> tuple[list[str], str, str | None]:
    """A unit as said ("einer amerikanischen Tasse", "US-Pints"): the names
    to look up -- the last one bare -- and its measures, if said."""
    s = key(text)
    tries = [s]
    s = re.sub(_ARTICLE, "", s)
    tries.append(s)
    qualified = re.match(rf"^({'|'.join(QUALIFIED)})\s+", s)
    measures = QUALIFIED[qualified.group(1)] if qualified else None
    s = s[qualified.end():] if qualified else s
    tries.append(s)
    return tries, s, measures


def forms(name: str) -> tuple[str, ...]:
    """A name looked up as said, and as its endings might be: "Metern",
    "Litern", "Tassen", "Liters"."""
    return (name, re.sub(r"n$", "", name), re.sub(r"en$", "", name), re.sub(r"s$", "", name), re.sub(r"e$", "", name))


def temperature_pattern(scales: str) -> re.Pattern:
    """A conversion between temperature scales: "100 Grad Fahrenheit in
    Celsius", "was sind 20 Grad Celsius in Fahrenheit"."""
    return re.compile(
        rf"(?:was (?:sind|ist)|wie viel (?:sind|ist)|rechne|konvertiere|wandle)?\s*{N} (?:grad )?({scales})"
        rf"\s+(?:in|nach|zu|als)\s+(?:grad )?({scales})(?:\s+um)?"
    )


def solve(q, settings):
    """The answer, its sentence and the conversion said whole ("5 Meilen sind
    8,047 Kilometer"); None."""
    places = settings.digits
    s = clean(q)
    temp = settings.temperature.fullmatch(s) if settings.temperature is not None else None
    if temp:
        v = parse_num(temp.group(1))
        a, b = settings.names[temp.group(2)], settings.names[temp.group(3)]
        if v is None or a == b:
            return None
        found = f"{fmt(settings.convert(v, a, b), places)} Grad {b.name}"
        return found, f"Das sind {found}.", f"{fmt(v, places)} Grad {a.name} sind {found}"
    if m := re.fullmatch(
        r"wie viele? (?:grad )?(.+?) (sind|ist|hat|haben|ergeben|ergibt|passen|gibt es) (in |auf )?(.+)", s
    ):
        target, verb, preposition, rest = m.groups()
        n = 1.0
        counted = False
        mm = re.fullmatch(rf"{N}\s*(.+)", rest)
        if mm and parse_num(mm.group(1)) is not None and settings.unit(mm.group(2)) and not re.match(_ARTICLE, rest):
            n, rest, counted = parse_num(mm.group(1)), mm.group(2), True
        ua, ub = settings.unit(rest), settings.unit(target)
        if not (ua and ub):
            return None
        # What was asked about, said with its name: "einem Liter", "5 Zoll".
        article = re.match(r"^(\S+)\s+", rest) if re.match(_ARTICLE, rest) else None
        said = f"{fmt(n, places)} {ua.said(n)}" if counted else f"{article.group(1)} {ua.name}" if article else ua.name
        r = settings.convert(n, ua, ub)
        if r is None:
            return None
        unit = settings.ounces(ub, ua)[0]
        if unit.measures == "temperature":
            found = f"{fmt(r, places)} Grad {unit.name}"
        else:
            found = f"{fmt(r, places)} {unit.said(r)}"
        if preposition:
            restated = f"{preposition.strip().capitalize()} {said} {'ist' if r == 1 else 'sind'} {found}"
        else:
            restated = f"{said[:1].upper()}{said[1:]} {verb} {found}"
        return found, f"Das sind {found}.", restated
    m = re.fullmatch(
        rf"(?:was (?:sind|ist)|wie viel (?:sind|ist)|rechne|konvertiere|wandle)?\s*{N}\s+(.+?)\s+(?:in|nach|zu|als)\s+(.+?)(?:\s+um)?",
        s,
    )
    if m:
        v, ua, ub = parse_num(m.group(1)), settings.unit(m.group(2)), settings.unit(m.group(3))
        if v is not None and ua and ub:
            r = settings.convert(v, ua, ub)
            if r is None:
                return None
            a, b = settings.ounces(ua, ub)
            if a.measures == "temperature":
                found = f"{fmt(r, places)} Grad {b.name}"
                return found, f"Das sind {found}.", f"{fmt(v, places)} Grad {a.name} sind {found}"
            found = f"{fmt(r, places)} {b.said(r)}"
            return found, f"Das sind {found}.", f"{fmt(v, places)} {a.said(v)} {'ist' if v == 1 else 'sind'} {found}"
    return None


def answer(text: str, settings: Settings):
    found = solve(text, settings)
    if not found:
        return None
    said, specific, restated = found
    return reply(said, text, restated, specific=specific)
