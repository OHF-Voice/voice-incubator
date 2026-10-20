"""Unit conversions: "how many cups are in a quart", "5 miles in
kilometers", "100 Fahrenheit in Celsius".

The units are set on its page (``UNITS`` unless changed): each a name, its
plural, others it goes by, what it measures, and its size in that measure's
base unit (``MEASURES``) -- a temperature scale, where water freezes and
boils on it. Volumes are US customary as built in."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from . import common
from .common import DIGITS, N, Skill, clean, fmt, options, parse_num, reply, rounded

MEASURES = {
    "length": "meter",
    "area": "square meter",
    "volume": "milliliter",
    "weight": "gram",
    "time": "second",
    "data": "byte",
    "temperature": None,
}
"""What a unit measures, and the unit its size is in (a temperature scale
has none: where water freezes and boils on it instead)."""


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


@dataclass(frozen=True)
class Unit:
    name: str
    measures: str
    size: float = 1.0
    """In the measure's base unit (``MEASURES``)."""
    plural: str = ""
    """Empty: ``plural_of`` its name."""
    also: tuple[str, ...] = ()
    """Other names: abbreviations, as a speech-to-text writes it."""
    freezes: float = 0.0
    """A temperature scale: where water freezes on it, and boils."""
    boils: float = 100.0

    @property
    def plural_name(self) -> str:
        if self.measures == "temperature":
            return self.name
        return self.plural or plural_of(self.name)

    def said(self, value: float) -> str:
        return self.name if value == 1 else self.plural_name

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name, "measures": self.measures, "also": list(self.also)}
        if self.measures == "temperature":
            out |= {"freezes": self.freezes, "boils": self.boils}
        else:
            out |= {"size": self.size, "plural": self.plural, "plural_of": plural_of(self.name)}
        return out

    def stored(self) -> dict[str, Any]:
        out: dict[str, Any] = {"measures": self.measures}
        if self.measures == "temperature":
            out |= {"freezes": _plain(self.freezes), "boils": _plain(self.boils)}
        else:
            out["size"] = _plain(self.size)
            if self.plural:
                out["plural"] = self.plural
        if self.also:
            out["also"] = list(self.also)
        return out


def _plain(number: float) -> float | int:
    """1852.0 as 1852, for settings.yaml."""
    return int(number) if float(number).is_integer() else number


def _u(name: str, measures: str, size: float, *also: str, plural: str = "") -> Unit:
    return Unit(name, measures, size, plural, also)


def _scale(name: str, freezes: float, boils: float, *also: str) -> Unit:
    return Unit(name, "temperature", also=also, freezes=freezes, boils=boils)


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


def _number(value: Any, what: str) -> float:
    if isinstance(value, bool) or value is None or value == "":
        raise ValueError(f"{what}: a number")
    try:
        number = float(value)
    except (TypeError, ValueError) as err:
        raise ValueError(f"{what}: a number") from err
    if not math.isfinite(number):
        raise ValueError(f"{what}: a number")
    return number


def _unit(name: Any, value: Any) -> Unit:
    """One unit as stored: ``{measures, size, plural, also}``, or for a
    temperature scale ``{measures, freezes, boils, also}``."""
    name = " ".join(str(name or "").split())
    if not name:
        raise ValueError("A unit needs a name")
    value = options(value, ("measures", "size", "plural", "also", "freezes", "boils"))
    measures = value.get("measures")
    if measures not in MEASURES:
        raise ValueError(f"{name}: it measures one of {', '.join(MEASURES)}")
    also = value.get("also") or []
    if isinstance(also, str):
        also = also.split(",")
    if not isinstance(also, list):
        raise ValueError(f"{name}: also, a list of names")
    also = tuple(dict.fromkeys(a for a in (" ".join(str(x).split()) for x in also) if a))
    if measures == "temperature":
        freezes = _number(value.get("freezes"), f"{name}: where water freezes")
        boils = _number(value.get("boils"), f"{name}: where water boils")
        if freezes == boils:
            raise ValueError(f"{name}: water can't freeze and boil at the same temperature")
        return Unit(name, measures, also=also, freezes=freezes, boils=boils)
    size = _number(value.get("size"), f"{name}: its size")
    if size <= 0:
        raise ValueError(f"{name}: its size, a number above 0")
    return Unit(name, measures, size, " ".join(str(value.get("plural") or "").split()), also)


@dataclass(frozen=True)
class Settings(common.Settings):
    units: tuple[Unit, ...] = UNITS
    digits: int = DIGITS
    """Significant digits in an answer that isn't whole."""
    names: dict[str, Unit] = field(init=False, repr=False, compare=False)
    """Each name a unit goes by (``key``): its name, plural, others."""
    temperature: re.Pattern | None = field(init=False, repr=False, compare=False)
    """A conversion between temperature scales, as said."""

    def __post_init__(self) -> None:
        names: dict[str, Unit] = {}
        for unit in self.units:
            for name in dict.fromkeys(key(n) for n in (unit.name, unit.plural_name, *unit.also)):
                other = names.setdefault(name, unit)
                if other is not unit:
                    raise ValueError(f"“{name}” is both {other.name} and {unit.name}")
        object.__setattr__(self, "names", names)
        scales = sorted((n for n, u in names.items() if u.measures == "temperature"), key=len, reverse=True)
        pattern = None
        if scales:
            scale = "|".join(re.escape(n) for n in scales)
            # "fifty degrees F in Celsius"; "thirty CNF": "in" heard as "n".
            pattern = re.compile(
                rf"(?:what(?:'s| is)|convert|how much is|how hot is|how cold is)?\s*{N} (?:degrees? )?({scale})"
                rf"(?:\s+(?:in|to|into|as|and)\s+|(?<=\b[a-z])n\s*)(?:degrees? )?({scale})"
            )
        object.__setattr__(self, "temperature", pattern)

    @classmethod
    def load(cls, stored: Any) -> Settings:
        """Stored: ``units``, by name -- a unit, or false for a built-in one
        taken out -- and ``digits``."""
        stored = options(stored, ("units", "digits"))
        changes = stored.get("units") or {}
        if not isinstance(changes, dict):
            raise ValueError("units: expected {name: unit}")
        units = {key(u.name): u for u in UNITS}
        for name, value in changes.items():
            if value is False:
                units.pop(key(str(name)), None)
            else:
                unit = _unit(name, value)
                units[key(unit.name)] = unit
        return cls(tuple(units.values()), common.digits(stored.get("digits")))

    def to_json(self) -> dict[str, Any]:
        built_in = {key(u.name): u for u in UNITS}
        here = {key(u.name) for u in self.units}
        return {
            "units": [u.to_json() | {"built_in": built_in.get(key(u.name)) == u} for u in self.units],
            "removed": [u.to_json() | {"built_in": True} for k, u in built_in.items() if k not in here],
            "digits": self.digits,
            "measures": MEASURES,
        }

    @classmethod
    def store(cls, edited: Any) -> Any:
        """The page sends every unit (``units``: ``[{name, measures, size,
        plural, also}]``) and ``digits``: stored, the built-in ones left out
        (false), changed, and added."""
        edited = options(edited, ("units", "digits", "removed", "measures"))
        items = edited.get("units") or []
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            raise ValueError("units: expected a list of units")
        units = []
        for item in items:
            known = {k: item[k] for k in ("measures", "size", "plural", "also", "freezes", "boils") if k in item}
            if known.get("measures") == "temperature":
                known = {k: v for k, v in known.items() if k not in ("size", "plural")}
            else:
                known = {k: v for k, v in known.items() if k not in ("freezes", "boils")}
            units.append(_unit(item.get("name"), known))
        given: dict[str, Unit] = {}
        for unit in units:
            if given.setdefault(key(unit.name), unit) is not unit:
                raise ValueError(f"{unit.name} is given twice")
        settings = cls(tuple(units), common.digits(edited.get("digits")))
        built_in = {key(u.name): u for u in UNITS}
        changes: dict[str, Any] = {u.name: False for k, u in built_in.items() if k not in given}
        changes |= {u.name: u.stored() for k, u in given.items() if built_in.get(k) != u}
        out: dict[str, Any] = {}
        if changes:
            out["units"] = changes
        if settings.digits != DIGITS:
            out["digits"] = settings.digits
        return out or None

    def unit(self, text: str) -> Unit | None:
        """The unit ``text`` names: "a cup", "US quarts", "kilos"."""
        s = key(text)
        tries = [s]
        s = re.sub(r"^(?:a|an|one|the)\s+", "", s)
        tries.append(s)
        s = re.sub(r"^(?:us|american|imperial)\s+", "", s)
        s = re.sub(r"\s+(?:in us cooking|in cooking|us)$", "", s)
        tries.append(s)
        for name in dict.fromkeys(tries):
            for candidate in (name, re.sub(r"ies$", "y", name), re.sub(r"es$", "", name), re.sub(r"s$", "", name)):
                if candidate in self.names:
                    return self.names[candidate]
        return None

    def ounces(self, a: Unit, b: Unit) -> tuple[Unit, Unit]:
        """An ounce beside a volume is a fluid ounce: "how many ounces are in a
        cup"."""
        fluid = self.names.get("fluid ounce")
        if fluid is not None and a.measures != b.measures:
            if key(a.name) == "ounce" and b.measures == "volume":
                return fluid, b
            if key(b.name) == "ounce" and a.measures == "volume":
                return a, fluid
        return a, b

    def convert(self, v: float, a: Unit, b: Unit) -> float | None:
        a, b = self.ounces(a, b)
        if a == b or a.measures != b.measures:
            return None
        if a.measures == "temperature":
            celsius = (v - a.freezes) * 100 / (a.boils - a.freezes)
            return round(celsius * (b.boils - b.freezes) / 100 + b.freezes, 1)
        return rounded(v * a.size / b.size, self.digits)


def solve(q, settings=None):
    """The answer, and it said with the question ("there are 4 cups in a
    quart"); None."""
    settings = settings or DEFAULT
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


DEFAULT = Settings()


def answer(text: str, settings: Settings = DEFAULT):
    found = solve(text, settings)
    return reply(found[0], text, found[1]) if found else None


SKILL = Skill(
    id="units",
    name="Unit conversions",
    description="Converts lengths, areas, volumes (US cooking measures), weights, temperatures, times and data sizes.",
    examples=("how many cups are in a quart", "what is 5 miles in kilometers", "100 Fahrenheit in Celsius"),
    icon="mdi:scale-balance",
    answer=answer,
    settings=Settings,
)
