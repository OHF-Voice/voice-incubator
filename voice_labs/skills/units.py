"""Unit conversions: "how many cups are in a quart", "5 miles in
kilometers", "100 Fahrenheit in Celsius".

The units are set on its page (the language's ``UNITS`` unless changed):
each a name, its plural, others it goes by, what it measures, and its size
in that measure's base unit (``MEASURES``) -- a temperature scale, where
water freezes and boils on it. Volumes come in the language's measures
(``VOLUMES``: US customary or imperial). How units are named and looked up,
and how a conversion is asked and said, are the app's language's (its
pack's ``skills/units.py``)."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, replace
from types import ModuleType
from typing import Any

from .. import languages
from . import common
from .common import DIGITS, Reply, Skill, options, rounded


def _words() -> ModuleType:
    """The app's language's: ``UNITS``, ``VOLUMES``, ``key``, ``plural_of``,
    ``read_unit`` ..."""
    return languages.grammar("units")


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
        return self.plural or _words().plural_of(self.name)

    def said(self, value: float) -> str:
        return self.name if value == 1 else self.plural_name

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name, "measures": self.measures, "also": list(self.also)}
        if self.measures == "temperature":
            out |= {"freezes": self.freezes, "boils": self.boils}
        else:
            out |= {"size": self.size, "plural": self.plural, "plural_of": _words().plural_of(self.name)}
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
    """1852.0 as 1852, for settings.json."""
    return int(number) if float(number).is_integer() else number


def _u(name: str, measures: str, size: float, *also: str, plural: str = "") -> Unit:
    return Unit(name, measures, size, plural, also)


def _scale(name: str, freezes: float, boils: float, *also: str) -> Unit:
    return Unit(name, "temperature", also=also, freezes=freezes, boils=boils)


def built_in(volumes: str | None = None) -> tuple[Unit, ...]:
    """The language's built-in units, their volumes by ``volumes``
    (``VOLUMES``; None: its own)."""
    words = _words()
    sizes = words.VOLUMES[volumes or words.DEFAULT_VOLUMES]
    return tuple(replace(u, size=sizes[words.key(u.name)]) if words.key(u.name) in sizes else u for u in words.UNITS)


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


def _volumes(value: Any) -> str:
    words = _words()
    if value in (None, ""):
        return words.DEFAULT_VOLUMES
    if value not in words.VOLUMES:
        raise ValueError(f"volumes: one of {', '.join(words.VOLUMES)}")
    return value


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
    units: tuple[Unit, ...] = field(default_factory=built_in)
    digits: int = DIGITS
    """Significant digits in an answer that isn't whole."""
    volumes: str = field(default_factory=lambda: _words().DEFAULT_VOLUMES)
    """The volumes' measures the built-in units have (``VOLUMES``)."""
    names: dict[str, Unit] = field(init=False, repr=False, compare=False)
    """Each name a unit goes by (``key``): its name, plural, others."""
    temperature: re.Pattern | None = field(init=False, repr=False, compare=False)
    """A conversion between temperature scales, as said."""

    def __post_init__(self) -> None:
        words = _words()
        names: dict[str, Unit] = {}
        for unit in self.units:
            for name in dict.fromkeys(words.key(n) for n in (unit.name, unit.plural_name, *unit.also)):
                other = names.setdefault(name, unit)
                if other is not unit:
                    raise ValueError(f"“{name}” is both {other.name} and {unit.name}")
        object.__setattr__(self, "names", names)
        scales = sorted((n for n, u in names.items() if u.measures == "temperature"), key=len, reverse=True)
        pattern = words.temperature_pattern("|".join(re.escape(n) for n in scales)) if scales else None
        object.__setattr__(self, "temperature", pattern)

    @classmethod
    def load(cls, stored: Any) -> Settings:
        """Stored: ``volumes`` (``VOLUMES``: the built-in units' measures),
        ``units`` by name -- a unit, or false for a built-in one taken out --
        and ``digits``."""
        stored = options(stored, ("volumes", "units", "digits"))
        volumes = _volumes(stored.get("volumes"))
        changes = stored.get("units") or {}
        if not isinstance(changes, dict):
            raise ValueError("units: expected {name: unit}")
        key = _words().key
        units = {key(u.name): u for u in built_in(volumes)}
        for name, value in changes.items():
            if value is False:
                units.pop(key(str(name)), None)
            else:
                unit = _unit(name, value)
                units[key(unit.name)] = unit
        return cls(tuple(units.values()), common.digits(stored.get("digits")), volumes)

    def to_json(self) -> dict[str, Any]:
        words = _words()
        key = words.key
        base = {key(u.name): u for u in built_in(self.volumes)}
        here = {key(u.name) for u in self.units}
        return {
            "units": [u.to_json() | {"built_in": base.get(key(u.name)) == u} for u in self.units],
            "removed": [u.to_json() | {"built_in": True} for k, u in base.items() if k not in here],
            "digits": self.digits,
            "measures": MEASURES,
            "volumes": self.volumes,
            "presets": {name: {"name": words.VOLUME_NAMES[name], "sizes": sizes} for name, sizes in words.VOLUMES.items()},
        }

    @classmethod
    def store(cls, edited: Any) -> Any:
        """The page sends every unit (``units``: ``[{name, measures, size,
        plural, also}]``) and ``digits``: stored, the built-in ones left out
        (false), changed, and added."""
        edited = options(edited, ("volumes", "units", "digits", "removed", "measures", "presets"))
        volumes = _volumes(edited.get("volumes"))
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
        key = _words().key
        given: dict[str, Unit] = {}
        for unit in units:
            if given.setdefault(key(unit.name), unit) is not unit:
                raise ValueError(f"{unit.name} is given twice")
        settings = cls(tuple(units), common.digits(edited.get("digits")), volumes)
        base = {key(u.name): u for u in built_in(volumes)}
        changes: dict[str, Any] = {u.name: False for k, u in base.items() if k not in given}
        changes |= {u.name: u.stored() for k, u in given.items() if base.get(k) != u}
        out: dict[str, Any] = {}
        if volumes != _words().DEFAULT_VOLUMES:
            out["volumes"] = volumes
        if changes:
            out["units"] = changes
        if settings.digits != DIGITS:
            out["digits"] = settings.digits
        return out or None

    def unit(self, text: str) -> Unit | None:
        """The unit ``text`` names: "a cup", "US quarts", "kilos" (the
        language's ``read_unit`` and ``forms``). A volume said with its
        measures ("an imperial pint", "a US cup") is theirs, whichever are
        set."""
        words = _words()
        tries, bare, measures = words.read_unit(text)
        for name in dict.fromkeys(tries):
            for candidate in words.forms(name):
                if candidate in self.names:
                    unit = self.names[candidate]
                    sizes = words.VOLUMES.get(measures) or {}
                    if name == bare and measures and unit.measures == "volume" and words.key(unit.name) in sizes:
                        unit = replace(unit, size=sizes[words.key(unit.name)])
                    return unit
        return None

    def ounces(self, a: Unit, b: Unit) -> tuple[Unit, Unit]:
        """An ounce beside a volume is a fluid ounce: "how many ounces are in a
        cup"."""
        words = _words()
        fluid = self.names.get(words.FLUID_OUNCE)
        if fluid is not None and a.measures != b.measures:
            if words.key(a.name) == words.OUNCE and b.measures == "volume":
                return fluid, b
            if words.key(b.name) == words.OUNCE and a.measures == "volume":
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


def answer(text: str, settings: Settings | None = None) -> Reply | None:
    return _words().answer(text, settings or Settings())


SKILL = Skill(
    id="units",
    name="Unit conversions",
    description="Converts lengths, areas, volumes (US or imperial measures), weights, temperatures, times and data sizes.",
    icon="mdi:scale-balance",
    answer=answer,
    settings=Settings,
)


def __getattr__(name: str) -> Any:
    """``UNITS``, ``VOLUMES``, ``VOLUME_NAMES``, ``QUALIFIED``: the app's
    language's."""
    if name in ("UNITS", "VOLUMES", "VOLUME_NAMES", "QUALIFIED"):
        return getattr(_words(), name)
    raise AttributeError(name)
