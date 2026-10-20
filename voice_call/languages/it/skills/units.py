"""How Italian names units ("tazza", "tazze", "una tazza americana"), which it
knows built in, and how it asks a conversion ("quanti grammi ci sono in un
etto", "quanto fa 5 miglia in chilometri", "100 gradi Fahrenheit in
Celsius") and says one (voice_call/skills/units.py).

A plural changes the last vowel ("metro", "metri"; "tazza", "tazze";
"oncia", "once"), the words before a "di" or "da" agreeing ("metri quadrati",
"cucchiaini da tè"); a few are irregular ("miglio", "miglia") and given
theirs. An "etto" is 100 grams, as Italy weighs at the market; a "libbra" the
US and UK pound. A unit's measures come after it ("una tazza americana", "una
pinta inglese"), or before. Spoons and a cup are metric (5, 15 and 250 ml)
as built in, the larger volumes US customary. Units are looked up, and
questions read, without their accents."""

from __future__ import annotations

import itertools
import re

from voice_call.skills.common import fmt, reply
from voice_call.skills.units import Settings, _scale, _u

from .numbers import FRACTIONS, N, clean, fold, parse_num, verb

EXAMPLES = ("quanti grammi ci sono in un etto", "quanto fa 5 miglia in chilometri", "100 gradi Fahrenheit in Celsius")

_LINKS = ("di", "da", "a", "per")
"""Where a unit's name stops agreeing: "cucchiaini da tè"."""


def _plural(word: str) -> str:
    """"metro" -> "metri", "tazza" -> "tazze", "pollice" -> "pollici",
    "cucchiaio" -> "cucchiai", "oncia" -> "once"; "byte", "yard" stay."""
    if not word or word[-1:] not in "aeio":
        return word
    if word.endswith("io"):
        return word[:-1]
    if re.search(r"[^aeiou][cg]ia$", word):
        return word[:-2] + "e"
    if word.endswith(("ca", "ga")):
        return word[:-1] + "he"
    if word.endswith("a"):
        return word[:-1] + "e"
    if word.endswith(("o", "e")):
        return word[:-1] + "i"
    return word


def plural_of(name: str) -> str:
    """"tazza" -> "tazze", "metro quadrato" -> "metri quadrati", "oncia
    liquida" -> "once liquide", "cucchiaino da tè" -> "cucchiaini da tè"."""
    words = name.split()
    for index, word in enumerate(words):
        if word in _LINKS:
            break
        words[index] = _plural(word)
    return " ".join(words)


def key(text: str) -> str:
    """A unit's name as looked up: lowercase, without accents, periods or
    hyphens: "Cucchiaino da tè" -> "cucchiaino da te"."""
    return " ".join(fold(text.lower().replace("’", "'")).replace(".", " ").replace("-", " ").split())


UNITS = (
    _u("millimetro", "length", 0.001, "mm"),
    _u("centimetro", "length", 0.01, "cm"),
    _u("decimetro", "length", 0.1, "dm"),
    _u("metro", "length", 1, "m"),
    _u("chilometro", "length", 1000, "km", "kilometro", "kilometri"),
    _u("pollice", "length", 0.0254, "inch", "inches"),
    _u("piede", "length", 0.3048, "ft", "foot", "feet"),
    _u("iarda", "length", 0.9144, "yd", "yard", "yards"),
    _u("miglio", "length", 1609.344, "mi", "mile", "miles", plural="miglia"),
    _u("miglio nautico", "length", 1852, "miglio marino", "miglia marine", plural="miglia nautiche"),
    _u("millimetro quadrato", "area", 1e-6, "mm2", "millimetro quadro", "millimetri quadri"),
    _u("centimetro quadrato", "area", 1e-4, "cm2", "centimetro quadro", "centimetri quadri"),
    _u("pollice quadrato", "area", 0.00064516),
    _u("piede quadrato", "area", 0.09290304),
    _u("metro quadrato", "area", 1, "m2", "mq", "metro quadro", "metri quadri"),
    _u("chilometro quadrato", "area", 1e6, "km2", "chilometro quadro", "chilometri quadri"),
    _u("miglio quadrato", "area", 2589988.110336, plural="miglia quadrate"),
    _u("acro", "area", 4046.8564224),
    _u("ettaro", "area", 10000, "ha"),
    _u("millilitro", "volume", 1, "ml"),
    _u("centilitro", "volume", 10, "cl"),
    _u("decilitro", "volume", 100, "dl"),
    _u("litro", "volume", 1000, "l", "lt"),
    _u("cucchiaino", "volume", 5, "cucchiaino da tè", "cucchiaini da tè", "cucchiaino da caffè",
       "cucchiaini da caffè", "tsp"),
    _u("cucchiaio", "volume", 15, "cucchiaio da tavola", "cucchiai da tavola", "cucchiaio da minestra",
       "cucchiai da minestra", "tbsp"),
    _u("oncia liquida", "volume", 29.5735295625, "fl oz", "oncia fluida", "once fluide"),
    _u("tazza", "volume", 250, "cup", "cups"),
    _u("pinta", "volume", 473.176473, "pint", "pints"),
    _u("quarto", "volume", 946.352946, "quart", "quarts", "quarto di gallone", "quarti di gallone"),
    _u("gallone", "volume", 3785.411784, "gallon", "gallons"),
    _u("milligrammo", "weight", 0.001, "mg"),
    _u("grammo", "weight", 1, "g", "gr"),
    _u("etto", "weight", 100, "hg", "ettogrammo", "ettogrammi"),
    _u("chilogrammo", "weight", 1000, "kg", "chilo", "chili", "kilo", "kili", "kilogrammo", "kilogrammi"),
    _u("libbra", "weight", 453.59237, "lb", "lbs", "pound", "pounds"),
    _u("oncia", "weight", 28.349523125, "oz"),
    _u("quintale", "weight", 100000),
    _u("tonnellata", "weight", 1e6, "t"),
    _u("millisecondo", "time", 0.001, "ms"),
    _u("secondo", "time", 1, "s", "sec"),
    _u("minuto", "time", 60, "min"),
    _u("ora", "time", 3600, "h"),
    _u("giorno", "time", 86400, "giornata", "giornate"),
    _u("settimana", "time", 604800),
    _u("anno", "time", 31536000),
    _u("decennio", "time", 315360000),
    _u("secolo", "time", 3153600000),
    _u("byte", "data", 1, plural="byte"),
    _u("kilobyte", "data", 1000, "kb", plural="kilobyte"),
    _u("megabyte", "data", 1e6, "mb", plural="megabyte"),
    _u("gigabyte", "data", 1e9, "gb", plural="gigabyte"),
    _u("terabyte", "data", 1e12, "tb", plural="terabyte"),
    _scale("Celsius", 0, 100, "c", "centigrado", "centigradi"),
    _scale("Fahrenheit", 32, 212, "f"),
    _scale("Kelvin", 273.15, 373.15, "k"),
)
"""The built-in units."""

_US = {"cucchiaino": 4.92892159375, "cucchiaio": 14.78676478125, "oncia liquida": 29.5735295625,
       "tazza": 236.5882365, "pinta": 473.176473, "quarto": 946.352946, "gallone": 3785.411784}
VOLUMES = {
    "metric": _US | {"cucchiaino": 5, "cucchiaio": 15, "tazza": 250},
    "us": _US,
    "imperial": {"cucchiaino": 5, "cucchiaio": 15, "oncia liquida": 28.4130625, "tazza": 250, "pinta": 568.26125,
                 "quarto": 1136.5225, "gallone": 4546.09},
}
"""The volumes' sizes, in milliliters, by measures: metric spoons and cup
with US customary larger ones (as built in), US customary, or imperial."""

VOLUME_NAMES = {"metric": "Metric spoons and cup (US otherwise)", "us": "US customary", "imperial": "Imperial (UK)"}

DEFAULT_VOLUMES = "metric"

QUALIFIED = {
    **dict.fromkeys(["us", "usa", "americano", "americana", "americani", "americane", "statunitense",
                     "statunitensi"], "us"),
    **dict.fromkeys(["uk", "imperiale", "imperiali", "britannico", "britannica", "britannici", "britanniche",
                     "inglese", "inglesi"], "imperial"),
    **dict.fromkeys(["metrico", "metrica", "metrici", "metriche"], "metric"),
}
"""Said after a volume ("una tazza americana"), or before it: its measures,
whichever are set."""

OUNCE, FLUID_OUNCE = "oncia", "oncia liquida"

_ARTICLE = r"^(?:un|uno|una|il|lo|la|i|gli|le)\s+|^(?:un|l)'\s*"
_QUALIFIERS = "|".join(sorted(QUALIFIED, key=len, reverse=True))


def read_unit(text: str) -> tuple[list[str], str, str | None]:
    """A unit as said ("una tazza americana", "pinte inglesi", "gradi
    Celsius"): the names to look up -- the last one bare -- and its measures,
    if said."""
    s = key(text)
    tries = [s]
    s = re.sub(_ARTICLE, "", s)
    s = re.sub(r"^grad[oi]\s+", "", s)
    tries.append(s)
    measures = None
    if qualified := re.search(rf"\s+({_QUALIFIERS})$", s):
        measures, s = QUALIFIED[qualified.group(1)], s[: qualified.start()]
    elif qualified := re.match(rf"^({_QUALIFIERS})\s+", s):
        measures, s = QUALIFIED[qualified.group(1)], s[qualified.end():]
    tries.append(s)
    return tries, s, measures


_SINGULAR = (("i", "o"), ("i", "e"), ("i", "io"), ("ce", "cia"), ("ge", "gia"), ("he", "a"), ("e", "a"))
"""A plural's ending, and what its singular's might be."""


def _singulars(word: str) -> list[str]:
    return [word] + [word[: -len(plural)] + single for plural, single in _SINGULAR
                     if word.endswith(plural) and len(word) > len(plural) + 1]


def forms(name: str) -> tuple[str, ...]:
    """A name looked up as said, and as its singular might be: "metri
    quadrati" -> "metro quadrato", "once liquide" -> "oncia liquida",
    "cucchiai da tavola" -> "cucchiaio da tavola"."""
    words = name.split()
    choices: list[list[str]] = []
    linked = False
    for word in words:
        linked = linked or word in _LINKS
        choices.append([word] if linked else _singulars(word))
    return tuple(dict.fromkeys([name, *(" ".join(c) for c in itertools.product(*choices))]))


_CONVERT = (
    r"(?:quanto (?:fa|fanno|sono|e|pesa|pesano|misura|misurano|vale|valgono)"
    r"|a quanto (?:corrisponde|corrispondono|equivale|equivalgono)"
    r"|quant[ie] (?:sono|fanno)|converti(?:mi)?|convertire|trasforma(?:mi)?|trasformare)?"
)


def temperature_pattern(scales: str) -> re.Pattern:
    """A conversion between temperature scales: "100 gradi Fahrenheit in
    Celsius", "quanto fanno 20 gradi Celsius in Fahrenheit"."""
    return re.compile(
        rf"{_CONVERT}\s*{N} (?:gradi |grado )?({scales})"
        rf"\s+(?:in|a)\s+(?:gradi )?({scales})(?:,? quanto (?:fa|fanno|sono))?"
    )


def _said(value: float, unit, places: int) -> str:
    """"8,047 chilometri", "37,8 gradi Celsius"."""
    if unit.measures == "temperature":
        return f"{fmt(value, places)} {'grado' if value == 1 else 'gradi'} {unit.name}"
    return f"{fmt(value, places)} {unit.said(value)}"


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


_IN = ("ci sono in", "ci stanno in", "stanno in", "entrano in", "c'e in", "in")
_ASKED = re.compile(
    r"quant[ie] (?:gradi )?(.+?) (ci sono in|ci stanno in|stanno in|entrano in|c'e in|in|ha|hanno|fa|fanno|sono|e"
    r"|equivale a|equivalgono a|corrisponde a|corrispondono a|pesa|pesano|misura|misurano|contiene|contengono"
    r"|vale|valgono) (.+)"
)
_FRACTIONS = {fold(f) for f in FRACTIONS}
_PLURAL_VERB = {"ha": "hanno", "pesa": "pesano", "misura": "misurano", "contiene": "contengono", "vale": "valgono"}


def _counted(rest: str, settings) -> tuple[float, str, str | None, bool]:
    """How many of a unit were asked about, the unit, how that is said back
    (None: the count and the unit's name, "5 miglia"), and whether it was a
    number: "5 miglia", "mezzo litro" (0.5), "un quarto di litro" (0.25),
    "un litro e mezzo" (1.5), "mezz'ora" -- or "un litro" (1)."""
    extra = 0.0
    said = rest
    rest = re.sub(r"^mezz'\s*", "mezza ", rest)  # "mezz'ora"
    if half := re.fullmatch(r"(.+?) e (mezzo|mezza)", rest):
        rest, extra = half.group(1), 0.5
    mm = re.fullmatch(rf"{N}\s+(?:di\s+|d')?(.+)", rest)
    # "un litro" is one litro, but "un quarto di litro" a quarter of one.
    if (mm and settings.unit(mm.group(2)) and (n := parse_num(mm.group(1))) is not None
            and (not re.match(_ARTICLE, rest) or fold(mm.group(1)) in _FRACTIONS)):
        digits = re.match(r"-?\d", mm.group(1)) is not None
        return n + extra, mm.group(2), None if digits and not extra else said, digits
    return 1.0 + extra, rest, said, False


def solve(q, settings):
    """The answer, its sentence and the conversion said whole ("5 miglia sono
    8,047 chilometri"); None."""
    places = settings.digits
    s = fold(clean(q))
    temp = settings.temperature.fullmatch(s) if settings.temperature is not None else None
    if temp:
        v = parse_num(temp.group(1))
        a, b = settings.names[temp.group(2)], settings.names[temp.group(3)]
        if v is None or a == b:
            return None
        found, asked = _said(settings.convert(v, a, b), b, places), _said(v, a, places)
        return found, f"{_capital(verb(found))} {found}.", f"{asked} {verb(asked)} {found}"
    m = _ASKED.fullmatch(s)
    if not m and (back := re.fullmatch(r"in (.+?),? quant[ie] (?:gradi )?(.+?) ci sono", s)):
        m, target, how, rest = back, back.group(2), "ci sono in", back.group(1)
    elif m:
        target, how, rest = m.groups()
    if m:
        n, rest, said, digits = _counted(rest, settings)
        ua, ub = settings.unit(rest), settings.unit(target)
        r = settings.convert(n, ua, ub) if ua and ub else None
        if r is not None:
            found = _said(r, settings.ounces(ub, ua)[0], places)
            # As asked: "5 miglia", "mezzo litro", "un etto".
            said = said or _said(n, ua, places)
            many = n > 1 or (digits and n != 1)
            if how in _IN:
                there = "c'è" if verb(found) == "è" else "ci sono"
                restated = f"In {said} {there} {found}"
            elif how in _PLURAL_VERB or how in _PLURAL_VERB.values():
                single = next(k for k, v in _PLURAL_VERB.items() if how in (k, v))
                restated = f"{_capital(said)} {_PLURAL_VERB[single] if many else single} {found}"
            else:
                restated = f"{_capital(said)} {'sono' if many else 'è'} {found}"
            return found, f"{_capital(verb(found))} {found}.", restated
    m = (re.fullmatch(rf"{_CONVERT}\s*{N}\s+(.+?)\s+(?:in|a)\s+(.+?)(?:,? quanto (?:fa|fanno|sono))?", s)
         or re.fullmatch(rf"{N}\s+(.+?),? quant[ie] (.+?) (?:sono|fanno|fa|e)", s))
    if m:
        v, ua, ub = parse_num(m.group(1)), settings.unit(m.group(2)), settings.unit(m.group(3))
        if v is not None and ua and ub:
            r = settings.convert(v, ua, ub)
            if r is None:
                return None
            a, b = settings.ounces(ua, ub)
            found = _said(r, b, places)
            # As asked: "5 miglia" (said with the unit's name), "una libbra".
            asked = _said(v, a, places) if re.match(r"-?\d", m.group(1)) else f"{m.group(1)} {m.group(2)}"
            many = v > 1 or (v != 1 and re.match(r"-?\d", m.group(1)) is not None)
            return found, f"{_capital(verb(found))} {found}.", f"{_capital(asked)} {'sono' if many else 'è'} {found}"
    return None


def answer(text: str, settings: Settings):
    found = solve(text, settings)
    if not found:
        return None
    said, specific, restated = found
    return reply(said, text, restated, specific=specific)
