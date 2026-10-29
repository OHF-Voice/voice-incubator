"""How English names units ("cup", "cups", "a US cup"), which it knows built
in, and how it asks a conversion ("how many cups are in a quart", "5 miles
in kilometers", "100 Fahrenheit in Celsius") and says one
(voice_labs/skills/units.py)."""

from __future__ import annotations

import re

from voice_labs.skills.common import fmt, reply
from voice_labs.skills.units import Settings, _scale, _u

from .numbers import N, clean, parse_num

EXAMPLES = ("how many cups are in a quart", "what is 5 miles in kilometers", "100 Fahrenheit in Celsius")


def plural_of(name: str) -> str:
    """"cup" -> "cups", "inch" -> "inches", "century" -> "centuries"."""
    if re.search(r"(s|x|z|ch|sh)$", name):
        return name + "es"
    if re.search(r"[^aeiou]y$", name):
        return name[:-1] + "ies"
    return name + "s"


def key(text: str) -> str:
    """A unit's name as looked up: lowercase, "metre" and "litre" as
    "meter" and "liter", no periods."""
    s = text.lower().replace("metre", "meter").replace("litre", "liter").replace(".", "")
    return " ".join(s.split())


UNITS = (
    _u("millimeter", "length", 0.001, "mm"),
    _u("centimeter", "length", 0.01, "cm"),
    _u("meter", "length", 1, "m"),
    _u("kilometer", "length", 1000, "km"),
    _u("inch", "length", 0.0254, "in"),
    _u("foot", "length", 0.3048, "ft", plural="feet"),
    _u("yard", "length", 0.9144, "yd"),
    _u("mile", "length", 1609.344, "mi"),
    _u("square inch", "area", 0.00064516),
    _u("square foot", "area", 0.09290304, plural="square feet"),
    _u("square yard", "area", 0.83612736),
    _u("square meter", "area", 1),
    _u("square kilometer", "area", 1e6),
    _u("square mile", "area", 2589988.110336),
    _u("acre", "area", 4046.8564224),
    _u("hectare", "area", 10000),
    _u("milliliter", "volume", 1, "ml"),
    _u("liter", "volume", 1000, "l"),
    _u("teaspoon", "volume", 4.92892159375, "tsp"),
    _u("tablespoon", "volume", 14.78676478125, "tbsp"),
    _u("fluid ounce", "volume", 29.5735295625, "fl oz"),
    _u("cup", "volume", 236.5882365),
    _u("pint", "volume", 473.176473),
    # "Quartz": as a speech-to-text may write it.
    _u("quart", "volume", 946.352946, "quartz"),
    _u("gallon", "volume", 3785.411784),
    _u("milligram", "weight", 0.001, "mg"),
    _u("gram", "weight", 1, "g"),
    _u("kilogram", "weight", 1000, "kg", "kilo"),
    _u("ounce", "weight", 28.349523125, "oz"),
    _u("pound", "weight", 453.59237, "lb", "lbs"),
    _u("stone", "weight", 6350.29318),
    _u("ton", "weight", 907184.74),
    _u("metric ton", "weight", 1e6, "tonne"),
    _u("millisecond", "time", 0.001),
    _u("second", "time", 1, "sec"),
    _u("minute", "time", 60, "min"),
    _u("hour", "time", 3600, "hr"),
    _u("day", "time", 86400),
    _u("week", "time", 604800),
    _u("fortnight", "time", 1209600),
    _u("year", "time", 31536000),
    _u("decade", "time", 315360000),
    _u("century", "time", 3153600000),
    _u("byte", "data", 1),
    _u("kilobyte", "data", 1000),
    _u("megabyte", "data", 1e6),
    _u("gigabyte", "data", 1e9),
    _u("terabyte", "data", 1e12),
    _scale("Celsius", 0, 100, "centigrade", "c"),
    _scale("Fahrenheit", 32, 212, "f"),
    _scale("kelvin", 273.15, 373.15, "k"),
)
"""The built-in units."""

VOLUMES = {
    "us": {key(u.name): u.size for u in UNITS if u.measures == "volume" and u.name not in ("milliliter", "liter")},
    "imperial": {
        "teaspoon": 5,
        "tablespoon": 15,
        "fluid ounce": 28.4130625,
        "cup": 250,
        "pint": 568.26125,
        "quart": 1136.5225,
        "gallon": 4546.09,
    },
}
"""The volumes' sizes, in milliliters, by measures: US customary (as built
in) or imperial -- the UK fluid ounce, pint, quart and gallon, and the
metric cup and spoons UK recipes use."""

VOLUME_NAMES = {"us": "US customary", "imperial": "Imperial (UK)"}

QUALIFIED = {"us": "us", "american": "us", "imperial": "imperial", "british": "imperial", "uk": "imperial"}
"""Said before a volume ("an imperial pint"): its measures, whichever are set."""


DEFAULT_VOLUMES = "us"
"""The volumes' measures built in."""

OUNCE, FLUID_OUNCE = "ounce", "fluid ounce"
"""An ounce beside a volume is a fluid ounce (``Settings.ounces``)."""


def read_unit(text: str) -> tuple[list[str], str, str | None]:
    """A unit as said ("an imperial pint", "US quarts in cooking"): the
    names to look up, in order -- the last one bare -- and the measures it
    was said with (``QUALIFIED``), if any."""
    s = key(text)
    tries = [s]
    s = re.sub(r"^(?:a|an|one|the)\s+", "", s)
    tries.append(s)
    qualified = re.match(rf"^({'|'.join(QUALIFIED)})\s+", s)
    measures = QUALIFIED[qualified.group(1)] if qualified else None
    s = s[qualified.end():] if qualified else s
    if re.search(r"\s+(?:in us cooking|us)$", s):
        measures = "us"
    s = re.sub(r"\s+(?:in us cooking|in cooking|us)$", "", s)
    tries.append(s)
    return tries, s, measures


def forms(name: str) -> tuple[str, ...]:
    """A name looked up as said, and as its singular might be."""
    return (name, re.sub(r"ies$", "y", name), re.sub(r"es$", "", name), re.sub(r"s$", "", name))


def temperature_pattern(scales: str) -> re.Pattern:
    """A conversion between temperature scales, as said: "fifty degrees F in
    Celsius"; "thirty CNF": "in" heard as "n". ``scales``: an alternation."""
    return re.compile(
        rf"(?:what(?:'s| is)|convert|how much is|how hot is|how cold is)?\s*{N} (?:degrees? )?({scales})"
        rf"(?:\s+(?:in|to|into|as|and)\s+|(?<=\b[a-z])n\s*)(?:degrees? )?({scales})"
    )


def solve(q, settings):
    """The answer, and it said with the question ("there are 4 cups in a
    quart"); None."""
    places = settings.digits
    s = clean(q)
    s = re.sub(r"\s+(in us cooking|in cooking)$", "", s)
    temp = settings.temperature.fullmatch(s) if settings.temperature is not None else None
    if temp:
        v = parse_num(temp.group(1))
        a, b = settings.names[temp.group(2)], settings.names[temp.group(3)]
        if v is None or a == b:
            return None
        found = f"{fmt(settings.convert(v, a, b), places)} degrees {b.name}"
        return found, f"{fmt(v, places)} degrees {a.name} is {found}"
    m = (re.fullmatch(r"how many (.+?) (?:are )?(?:there )?(?:in|per|make up|make|in a single) (.+)", s)
         or re.fullmatch(r"how many (.+?) (?:is|are) (?:in )?(.+)", s))
    if m:
        b, rest = m.group(1), m.group(2)
        said = rest
        n = 1.0
        # "one and a half quarts", "2 quarts"; else "a quart", "one quart".
        mm = re.fullmatch(rf"{N}\s*(.+)", rest)
        if mm and parse_num(mm.group(1)) is not None and settings.unit(mm.group(2)):
            n, rest = parse_num(mm.group(1)), mm.group(2)
        else:
            rest = re.sub(r"^(?:a|an|one) ", "", rest)
        ua, ub = settings.unit(rest), settings.unit(b)
        if ua and ub and ua.measures != "temperature":
            r = settings.convert(n, ua, ub)
            if r is None:
                return None
            unit = settings.ounces(ub, ua)[0]
            return fmt(r, places), f"there {'is' if r == 1 else 'are'} {fmt(r, places)} {unit.said(r)} in {said}"
        return None
    m = re.fullmatch(rf"(?:what(?:'s| is)|convert|how much is|how many [a-z ]+ is)\s*{N}\s+(.+?)\s+(?:in|to|into|as)\s+(.+)", s)
    if m:
        v, ua, ub = parse_num(m.group(1)), settings.unit(m.group(2)), settings.unit(m.group(3))
        if v is not None and ua and ub:
            r = settings.convert(v, ua, ub)
            if r is None:
                return None
            a, b = settings.ounces(ua, ub)
            if a.measures == "temperature":
                found = f"{fmt(r, places)} degrees {b.name}"
                return found, f"{fmt(v, places)} degrees {a.name} is {found}"
            found = f"{fmt(r, places)} {b.said(r)}"
            return found, f"{fmt(v, places)} {a.said(v)} is {found}"
    return None


def answer(text: str, settings: Settings):
    found = solve(text, settings)
    return reply(found[0], text, found[1]) if found else None
