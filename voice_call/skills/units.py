"""Unit conversions: "how many cups are in a quart", "5 miles in
kilometers", "100 Fahrenheit in Celsius". Volumes are US customary."""

from __future__ import annotations

import re

from .common import N, Skill, clean, fmt, parse_num, reply

# unit -> (dimension, size in base unit)
UNITS = {
    # length (m)
    "millimeter": ("len", 0.001), "centimeter": ("len", 0.01), "meter": ("len", 1), "kilometer": ("len", 1000),
    "inch": ("len", 0.0254), "foot": ("len", 0.3048), "yard": ("len", 0.9144), "mile": ("len", 1609.344),
    # area (m^2)
    "square foot": ("area", 0.09290304), "square yard": ("area", 0.83612736), "square meter": ("area", 1),
    "square inch": ("area", 0.00064516), "acre": ("area", 4046.8564224), "square mile": ("area", 2589988.110336),
    "hectare": ("area", 10000), "square kilometer": ("area", 1e6),
    # volume (mL), US customary
    "milliliter": ("vol", 1), "liter": ("vol", 1000), "teaspoon": ("vol", 4.92892159375), "tablespoon": ("vol", 14.78676478125),
    "fluid ounce": ("vol", 29.5735295625), "cup": ("vol", 236.5882365), "pint": ("vol", 473.176473),
    "quart": ("vol", 946.352946), "gallon": ("vol", 3785.411784),
    # mass (g)
    "milligram": ("mass", 0.001), "gram": ("mass", 1), "kilogram": ("mass", 1000), "ounce": ("mass", 28.349523125),
    "pound": ("mass", 453.59237), "ton": ("mass", 907184.74), "metric ton": ("mass", 1e6), "stone": ("mass", 6350.29318),
    # time (s)
    "millisecond": ("time", 0.001), "second": ("time", 1), "minute": ("time", 60), "hour": ("time", 3600), "day": ("time", 86400),
    "week": ("time", 604800), "fortnight": ("time", 1209600), "year": ("time", 31536000), "decade": ("time", 315360000),
    "century": ("time", 3153600000),
    # data (bytes)
    "byte": ("data", 1), "kilobyte": ("data", 1000), "megabyte": ("data", 1e6), "gigabyte": ("data", 1e9), "terabyte": ("data", 1e12),
}
ALIASES = {"mm": "millimeter", "cm": "centimeter", "m": "meter", "km": "kilometer", "feet": "foot", "ft": "foot", "in": "inch",
           "inches": "inch", "yd": "yard", "mi": "mile", "ml": "milliliter", "l": "liter", "litre": "liter", "tsp": "teaspoon",
           "tbsp": "tablespoon", "oz": "ounce", "fl oz": "fluid ounce", "lb": "pound", "lbs": "pound", "g": "gram", "kg": "kilogram",
           "kilo": "kilogram", "kilos": "kilogram", "mg": "milligram", "sec": "second", "secs": "second", "min": "minute",
           "mins": "minute", "hr": "hour", "hrs": "hour", "centuries": "century", "square feet": "square foot",
           "tonne": "metric ton", "tonnes": "metric ton",
           # As a transcript may write them.
           "quartz": "quart"}


def unit(s):
    s = s.strip().lower()
    s = re.sub(r"^(a|an|one|the|us|u\.s\.|american|imperial)\s+", "", s)
    s = re.sub(r"^(us|u\.s\.)\s+", "", s)
    s = re.sub(r"\s+(in us cooking|in cooking|us)$", "", s)
    s = s.replace("metre", "meter").replace("litre", "liter")
    if s in ALIASES:
        return ALIASES[s]
    for cand in (s, re.sub(r"ies$", "y", s), re.sub(r"es$", "", s), re.sub(r"s$", "", s)):
        if cand in UNITS:
            return cand
        if cand in ALIASES:
            return ALIASES[cand]
    return None


def _ounces(a, b):
    """An ounce beside a volume is a fluid ounce: "how many ounces are in a cup"."""
    if UNITS[a][0] != UNITS[b][0]:
        if a == "ounce" and UNITS[b][0] == "vol":
            return "fluid ounce", b
        if b == "ounce" and UNITS[a][0] == "vol":
            return a, "fluid ounce"
    return a, b


def convert(v, a, b):
    a, b = _ounces(a, b)
    if a == b or UNITS[a][0] != UNITS[b][0]:
        return None
    r = v * UNITS[a][1] / UNITS[b][1]
    return round(r, 4) if abs(r) < 100 else round(r, 1)


def plural(u, v):
    if v == 1:
        return u
    return {"foot": "feet", "inch": "inches", "century": "centuries", "square foot": "square feet"}.get(u, u + "s")


def solve(q):
    """The answer, and it said with the question ("there are 4 cups in a
    quart"); None."""
    s = clean(q)
    s = re.sub(r"\s+(in us cooking|in cooking)$", "", s)
    temp = re.fullmatch(rf"(?:what(?:'s| is)|convert|how much is|how hot is|how cold is)?\s*{N} (?:degrees? )?(celsius|centigrade|fahrenheit|kelvin|c|f|k)(?:\s+(?:in|to|into|as|and)\s+|(?<=\b[cfk])n\s*)(?:degrees? )?(celsius|centigrade|fahrenheit|kelvin|c|f|k)", s)
    if temp:
        v = parse_num(temp.group(1))
        a, b = temp.group(2)[0], temp.group(3)[0]
        if v is None or a == b:
            return None
        c = (v - 32) * 5 / 9 if a == "f" else v - 273.15 if a == "k" else v
        out = c * 9 / 5 + 32 if b == "f" else c + 273.15 if b == "k" else c
        names = {"f": "Fahrenheit", "c": "Celsius", "k": "kelvin"}
        found = f"{fmt(round(out, 1))} degrees {names[b]}"
        return found, f"{fmt(v)} degrees {names[a]} is {found}"
    m = (re.fullmatch(r"how many (.+?) (?:are )?(?:there )?(?:in|per|make up|make|in a single) (.+)", s)
         or re.fullmatch(r"how many (.+?) (?:is|are) (?:in )?(.+)", s))
    if m:
        b, rest = m.group(1), m.group(2)
        said = rest
        n = 1.0
        # "one and a half quarts", "2 quarts"; else "a quart", "one quart".
        mm = re.fullmatch(rf"{N}\s*(.+)", rest)
        if mm and parse_num(mm.group(1)) is not None and unit(mm.group(2)):
            n, rest = parse_num(mm.group(1)), mm.group(2)
        else:
            rest = re.sub(r"^(?:a|an|one) ", "", rest)
        ua, ub = unit(rest), unit(b)
        if ua and ub:
            r = convert(n, ua, ub)
            if r is None:
                return None
            return fmt(r), f"there {'is' if r == 1 else 'are'} {fmt(r)} {plural(_ounces(ub, ua)[0], r)} in {said}"
        return None
    m = re.fullmatch(rf"(?:what(?:'s| is)|convert|how much is|how many [a-z ]+ is)\s*{N}\s+(.+?)\s+(?:in|to|into|as)\s+(.+)", s)
    if m:
        v, ua, ub = parse_num(m.group(1)), unit(m.group(2)), unit(m.group(3))
        if v is not None and ua and ub:
            r = convert(v, ua, ub)
            if r is None:
                return None
            found = f"{fmt(r)} {plural(_ounces(ua, ub)[1], r)}"
            return found, f"{fmt(v)} {plural(_ounces(ua, ub)[0], v)} is {found}"
    return None


def answer(text: str) -> str | None:
    found = solve(text)
    return reply(found[0], text, found[1]) if found else None


SKILL = Skill(
    id="units",
    name="Unit conversions",
    description="Converts lengths, areas, volumes (US cooking measures), weights, temperatures, times and data sizes.",
    examples=("how many cups are in a quart", "what is 5 miles in kilometers", "100 Fahrenheit in Celsius"),
    icon="mdi:scale-balance",
    answer=answer,
)
