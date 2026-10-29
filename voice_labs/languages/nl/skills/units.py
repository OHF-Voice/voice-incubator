"""How Dutch names units ("kopje", "kopjes", "een Amerikaans kopje"), which it
knows built in, and how it asks a conversion ("hoeveel kopjes gaan er in een
liter", "wat is 5 mijl in kilometer", "100 graden Fahrenheit in Celsius") and
says one (voice_labs/skills/units.py).

A "pond" is 500 grams and an "ons" 100, as Dutch uses them; the US and UK
pound and ounce are "pound" and "ounce". Spoons and a cup are metric (5, 15
and 250 ml) as built in, the larger volumes US customary."""

from __future__ import annotations

import re

from voice_labs.skills.common import fmt, reply
from voice_labs.skills.units import Settings, _scale, _u

from .numbers import N, clean, parse_num

EXAMPLES = ("hoeveel gram is een pond", "wat is 5 mijl in kilometer", "100 graden Fahrenheit in Celsius")


def plural_of(name: str) -> str:
    """Units keep their name after a number: "5 meter", "5 mijl", "5 gram"."""
    return name


def key(text: str) -> str:
    return " ".join(text.lower().replace(".", "").replace("-", " ").split())


UNITS = (
    _u("millimeter", "length", 0.001, "mm"),
    _u("centimeter", "length", 0.01, "cm"),
    _u("meter", "length", 1, "m"),
    _u("kilometer", "length", 1000, "km"),
    _u("inch", "length", 0.0254, "in", "duim", plural="inch"),
    _u("voet", "length", 0.3048, "ft", "feet", "foot"),
    _u("yard", "length", 0.9144, "yd", plural="yards"),
    _u("mijl", "length", 1609.344, "mi", "mijlen"),
    _u("zeemijl", "length", 1852, "zeemijlen"),
    _u("vierkante inch", "area", 0.00064516),
    _u("vierkante voet", "area", 0.09290304),
    _u("vierkante meter", "area", 1, "m2"),
    _u("vierkante kilometer", "area", 1e6, "km2"),
    _u("vierkante mijl", "area", 2589988.110336),
    _u("acre", "area", 4046.8564224, plural="acres"),
    _u("hectare", "area", 10000, "ha", "hectares"),
    _u("milliliter", "volume", 1, "ml"),
    _u("liter", "volume", 1000, "l"),
    _u("theelepel", "volume", 5, "tl", plural="theelepels"),
    _u("eetlepel", "volume", 15, "el", plural="eetlepels"),
    _u("fluid ounce", "volume", 29.5735295625, "fl oz", plural="fluid ounces"),
    _u("kopje", "volume", 250, "cup", "cups", "kop", plural="kopjes"),
    _u("pint", "volume", 473.176473, plural="pints"),
    _u("quart", "volume", 946.352946, plural="quarts"),
    _u("gallon", "volume", 3785.411784, plural="gallons"),
    _u("milligram", "weight", 0.001, "mg"),
    _u("gram", "weight", 1, "g"),
    _u("kilogram", "weight", 1000, "kg", "kilo", "kilo's"),
    _u("ons", "weight", 100),
    _u("pond", "weight", 500),
    _u("ounce", "weight", 28.349523125, "oz", plural="ounces"),
    _u("pound", "weight", 453.59237, "lb", "lbs", plural="pounds"),
    _u("stone", "weight", 6350.29318),
    _u("ton", "weight", 1e6, "t", "tonne"),
    _u("milliseconde", "time", 0.001, "ms", plural="milliseconden"),
    _u("seconde", "time", 1, "sec", "s", plural="seconden"),
    _u("minuut", "time", 60, "min", plural="minuten"),
    _u("uur", "time", 3600, "u", "uren"),
    _u("dag", "time", 86400, plural="dagen"),
    _u("week", "time", 604800, plural="weken"),
    _u("jaar", "time", 31536000, "jaren"),
    _u("decennium", "time", 315360000, plural="decennia"),
    _u("eeuw", "time", 3153600000, plural="eeuwen"),
    _u("byte", "data", 1, plural="bytes"),
    _u("kilobyte", "data", 1000, "kb", plural="kilobytes"),
    _u("megabyte", "data", 1e6, "mb", plural="megabytes"),
    _u("gigabyte", "data", 1e9, "gb", plural="gigabytes"),
    _u("terabyte", "data", 1e12, "tb", plural="terabytes"),
    _scale("Celsius", 0, 100, "c"),
    _scale("Fahrenheit", 32, 212, "f"),
    _scale("Kelvin", 273.15, 373.15, "k"),
)

_US = {"theelepel": 4.92892159375, "eetlepel": 14.78676478125, "fluid ounce": 29.5735295625, "kopje": 236.5882365,
       "pint": 473.176473, "quart": 946.352946, "gallon": 3785.411784}
VOLUMES = {
    "metric": _US | {"theelepel": 5, "eetlepel": 15, "kopje": 250},
    "us": _US,
    "imperial": {"theelepel": 5, "eetlepel": 15, "fluid ounce": 28.4130625, "kopje": 250, "pint": 568.26125,
                 "quart": 1136.5225, "gallon": 4546.09},
}
VOLUME_NAMES = {"metric": "Metric spoons and cup (US otherwise)", "us": "US customary", "imperial": "Imperial (UK)"}
DEFAULT_VOLUMES = "metric"

QUALIFIED = {
    **dict.fromkeys(["us", "amerikaans", "amerikaanse"], "us"),
    **dict.fromkeys(["uk", "brits", "britse", "engels", "engelse", "imperiaal", "imperiale"], "imperial"),
    **dict.fromkeys(["metrisch", "metrische"], "metric"),
}

OUNCE, FLUID_OUNCE = "ounce", "fluid ounce"

_ARTICLE = r"^(?:een|de|het)\s+"


def read_unit(text: str) -> tuple[list[str], str, str | None]:
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
    """A name as said, and as its plural might be: "kopjes", "liters", "kilo's"."""
    return (name, re.sub(r"'?s$", "", name), re.sub(r"en$", "", name), re.sub(r"n$", "", name))


def temperature_pattern(scales: str) -> re.Pattern:
    return re.compile(
        rf"(?:wat is|hoeveel is|hoeveel zijn|reken|converteer|zet)?\s*{N} (?:graden )?({scales})"
        rf"\s+(?:in|naar|om naar|om in)\s+(?:graden )?({scales})(?:\s+om)?"
    )


def solve(q, settings):
    places = settings.digits
    s = clean(q)
    temp = settings.temperature.fullmatch(s) if settings.temperature is not None else None
    if temp:
        v = parse_num(temp.group(1))
        a, b = settings.names[temp.group(2)], settings.names[temp.group(3)]
        if v is None or a == b:
            return None
        found = f"{fmt(settings.convert(v, a, b), places)} graden {b.name}"
        return found, f"Dat is {found}.", f"{fmt(v, places)} graden {a.name} is {found}"
    if m := re.fullmatch(
        r"hoeveel (?:graden )?(.+?) (gaan er in|gaan in|zitten er in|zitten in|passen er in|past er in|is|zijn|heeft|hebben) (.+)", s
    ):
        target, verb, rest = m.groups()
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
        found = f"{fmt(r, places)} graden {unit.name}" if unit.measures == "temperature" else f"{fmt(r, places)} {unit.said(r)}"
        article = re.match(r"^(\S+)\s+", rest) if re.match(_ARTICLE, rest) else None
        said = f"{fmt(n, places)} {ua.said(n)}" if counted else f"{article.group(1)} {ua.name}" if article else ua.name
        if "in" in verb.split():
            restated = f"In {said} {verb.split()[0]} {found}"
        else:
            restated = f"{said[:1].upper()}{said[1:]} {verb} {found}"
        return found, f"Dat is {found}.", restated
    m = re.fullmatch(
        rf"(?:wat is|hoeveel is|hoeveel zijn|reken|converteer|zet)?\s*{N}\s+(.+?)\s+(?:in|naar|om naar|om in)\s+(.+?)(?:\s+om)?",
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
                found = f"{fmt(r, places)} graden {b.name}"
                return found, f"Dat is {found}.", f"{fmt(v, places)} graden {a.name} is {found}"
            found = f"{fmt(r, places)} {b.said(r)}"
            return found, f"Dat is {found}.", f"{fmt(v, places)} {a.said(v)} is {found}"
    return None


def answer(text: str, settings: Settings):
    found = solve(text, settings)
    if not found:
        return None
    said, specific, restated = found
    return reply(said, text, restated, specific=specific)
