"""How Polish names units ("szklanka", "szklanki", "szklanek", "w
szklance"), which it knows built in, and how it asks a conversion ("ile
gramów ma funt?", "ile szklanek jest w litrze?", "ile kilometrów to 5
mil?", "100 stopni Fahrenheita na Celsjusza") and says one
(voice_labs/skills/units.py).

A unit is asked in any case ("ile metrów w kilometrze"): the built-in ones
know theirs (``FORMS``). A number's unit takes its form by the number: "1
kilometr", "2 kilometry", "5 kilometrów", "8,047 kilometra"; before a
feminine unit, one and two are written out ("jedna szklanka", "dwie
szklanki"), as a voice would read the digits in the masculine. Teaspoons,
tablespoons and a glass (szklanka) are metric (5, 15 and 250 ml) as built
in, the larger volumes US customary; Poland's dekagram ("deko") is 10 g.
Units are looked up, and questions read, folded (numbers.fold)."""

from __future__ import annotations

import re

from voice_labs.skills.common import fmt, reply
from voice_labs.skills.units import Settings, _scale, _u

from .numbers import N, clean, fold, parse_num, plural, spell

EXAMPLES = ("ile gramów ma funt", "ile kilometrów to 5 mil", "100 stopni Fahrenheita na Celsjusza")


def key(text: str) -> str:
    """A unit's name as looked up: lowercase, folded, without periods:
    "Kilometrów" -> "kilometrow"."""
    return " ".join(fold(text.lower()).replace(".", "").replace("-", " ").split())


# name: (two to four, five and more, a part of one, feminine), and other cases to look up.
_DECLINED = {
    "milimetr": ("milimetry milimetrów milimetra", "milimetrze milimetrach"),
    "centymetr": ("centymetry centymetrów centymetra", "centymetrze centymetrach"),
    "metr": ("metry metrów metra", "metrze metrach"),
    "kilometr": ("kilometry kilometrów kilometra", "kilometrze kilometrach"),
    "cal": ("cale cali cala", "calu calach calów"),
    "stopa": ("stopy stóp stopy f", "stopę stopie stopach"),
    "jard": ("jardy jardów jarda", "jardzie jardach"),
    "mila": ("mile mil mili f", "milę milach"),
    "mila morska": ("mile_morskie mil_morskich mili_morskiej f", "milę_morską milach_morskich"),
    "cal kwadratowy": ("cale_kwadratowe cali_kwadratowych cala_kwadratowego", "calu_kwadratowym"),
    "stopa kwadratowa": ("stopy_kwadratowe stóp_kwadratowych stopy_kwadratowej f", "stopę_kwadratową stopie_kwadratowej"),
    "metr kwadratowy": ("metry_kwadratowe metrów_kwadratowych metra_kwadratowego", "metrze_kwadratowym"),
    "kilometr kwadratowy": ("kilometry_kwadratowe kilometrów_kwadratowych kilometra_kwadratowego", "kilometrze_kwadratowym"),
    "mila kwadratowa": ("mile_kwadratowe mil_kwadratowych mili_kwadratowej f", "milę_kwadratową"),
    "ar": ("ary arów ara", "arze arach"),
    "akr": ("akry akrów akra", "akrze akrach"),
    "hektar": ("hektary hektarów hektara", "hektarze hektarach"),
    "mililitr": ("mililitry mililitrów mililitra", "mililitrze mililitrach"),
    "litr": ("litry litrów litra", "litrze litrach"),
    "łyżeczka": ("łyżeczki łyżeczek łyżeczki f", "łyżeczkę łyżeczce łyżeczkach"),
    "łyżka": ("łyżki łyżek łyżki f", "łyżkę łyżce łyżkach"),
    "uncja płynu": ("uncje_płynu uncji_płynu uncji_płynu f", "uncję_płynu uncjach_płynu"),
    "szklanka": ("szklanki szklanek szklanki f", "szklankę szklance szklankach"),
    "pinta": ("pinty pint pinty f", "pintę pincie pintach"),
    "kwarta": ("kwarty kwart kwarty f", "kwartę kwarcie kwartach"),
    "galon": ("galony galonów galona", "galonie galonach"),
    "miligram": ("miligramy miligramów miligrama", "miligramie miligramach"),
    "gram": ("gramy gramów grama", "gramie gramach"),
    "dekagram": ("dekagramy dekagramów dekagrama", "dekagramie dekagramach"),
    "kilogram": ("kilogramy kilogramów kilograma", "kilogramie kilogramach"),
    "uncja": ("uncje uncji uncji f", "uncję uncjach"),
    "funt": ("funty funtów funta", "funcie funtach"),
    "kamień": ("kamienie kamieni kamienia", "kamieniu kamieniach"),
    "tona": ("tony ton tony f", "tonę tonie tonach"),
    "milisekunda": ("milisekundy milisekund milisekundy f", "milisekundę milisekundzie"),
    "sekunda": ("sekundy sekund sekundy f", "sekundę sekundzie sekundach"),
    "minuta": ("minuty minut minuty f", "minutę minucie minutach"),
    "godzina": ("godziny godzin godziny f", "godzinę godzinie godzinach"),
    "dzień": ("dni dni dnia", "dniu dniach"),
    "tydzień": ("tygodnie tygodni tygodnia", "tygodniu tygodniach"),
    "rok": ("lata lat roku", "latach"),
    "dekada": ("dekady dekad dekady f", "dekadę dekadzie"),
    "wiek": ("wieki wieków wieku", "wiekach"),
    "bajt": ("bajty bajtów bajta", "bajcie bajtach"),
    "kilobajt": ("kilobajty kilobajtów kilobajta", "kilobajcie kilobajtach"),
    "megabajt": ("megabajty megabajtów megabajta", "megabajcie megabajtach"),
    "gigabajt": ("gigabajty gigabajtów gigabajta", "gigabajcie gigabajtach"),
    "terabajt": ("terabajty terabajtów terabajta", "terabajcie terabajtach"),
    "kelwin": ("kelwiny kelwinów kelwina", "kelwinach"),
}

FORMS: dict[str, tuple[str, str, str, bool]] = {}
"""A built-in unit's forms by the number before it (``key`` of its name):
two to four, five and more, a part of one; and whether it is feminine."""
_LEMMAS: dict[str, str] = {}
"""Every case of a built-in unit (``key``) -> its name's ``key``."""
for _name, (_counted, _other) in _DECLINED.items():
    _words = _counted.split()
    FORMS[key(_name)] = (*(w.replace("_", " ") for w in _words[:3]), len(_words) > 3)
    for _form in [*_words[:3], *_other.split()]:
        _LEMMAS.setdefault(key(_form.replace("_", " ")), key(_name))


def plural_of(name: str) -> str:
    """As five and more are said ("kilometrów", "szklanek"): a built-in
    unit's own; another's guessed by its ending."""
    known = FORMS.get(key(name))
    if known:
        return known[1]
    words = name.split()
    head = words[0] if words else name
    if head.endswith(("ja", "ia")):
        head = head[:-1] + "i"
    elif head.endswith(("a", "o", "e")):
        head = head[:-1]
    else:
        head += "ów"
    return " ".join([head, *words[1:]])


UNITS = (
    _u("milimetr", "length", 0.001, "mm"),
    _u("centymetr", "length", 0.01, "cm"),
    _u("metr", "length", 1, "m"),
    _u("kilometr", "length", 1000, "km"),
    _u("cal", "length", 0.0254, "in", "inch", "inches", "cale"),
    _u("stopa", "length", 0.3048, "ft", "feet", "foot"),
    _u("jard", "length", 0.9144, "yd"),
    _u("mila", "length", 1609.344, "mi"),
    _u("mila morska", "length", 1852, "węzeł"),
    _u("cal kwadratowy", "area", 0.00064516),
    _u("stopa kwadratowa", "area", 0.09290304),
    _u("metr kwadratowy", "area", 1, "m2"),
    _u("kilometr kwadratowy", "area", 1e6, "km2"),
    _u("mila kwadratowa", "area", 2589988.110336),
    _u("ar", "area", 100),
    _u("akr", "area", 4046.8564224),
    _u("hektar", "area", 10000, "ha"),
    _u("mililitr", "volume", 1, "ml"),
    _u("litr", "volume", 1000, "l"),
    _u("łyżeczka", "volume", 5, "łyżeczka do herbaty"),
    _u("łyżka", "volume", 15, "łyżka stołowa"),
    _u("uncja płynu", "volume", 29.5735295625, "fl oz", "uncja płynowa"),
    _u("szklanka", "volume", 250, "cup", "cups"),
    _u("pinta", "volume", 473.176473, "pint", "pints"),
    _u("kwarta", "volume", 946.352946, "quart", "quarts"),
    _u("galon", "volume", 3785.411784, "gallon", "gallons"),
    _u("miligram", "weight", 0.001, "mg"),
    _u("gram", "weight", 1, "g"),
    _u("dekagram", "weight", 10, "dag", "dkg", "deko", "deka"),
    _u("kilogram", "weight", 1000, "kg", "kilo"),
    _u("uncja", "weight", 28.349523125, "oz"),
    _u("funt", "weight", 453.59237, "lb", "lbs"),
    _u("kamień", "weight", 6350.29318, "stone"),
    _u("tona", "weight", 1e6, "t"),
    _u("milisekunda", "time", 0.001, "ms"),
    _u("sekunda", "time", 1, "s", "sek"),
    _u("minuta", "time", 60, "min"),
    _u("godzina", "time", 3600, "h", "godz"),
    _u("dzień", "time", 86400, "doba", "doby", "dób"),
    _u("tydzień", "time", 604800),
    _u("rok", "time", 31536000),
    _u("dekada", "time", 315360000),
    _u("wiek", "time", 3153600000, "stulecie", "stulecia", "stuleci"),
    _u("bajt", "data", 1, "b"),
    _u("kilobajt", "data", 1000, "kb"),
    _u("megabajt", "data", 1e6, "mb"),
    _u("gigabajt", "data", 1e9, "gb"),
    _u("terabajt", "data", 1e12, "tb"),
    _scale("Celsjusz", 0, 100, "c", "celsjusza", "celsius", "celsiusa"),
    _scale("Fahrenheit", 32, 212, "f", "fahrenheita", "farenhajt", "farenhajta"),
    _scale("Kelwin", 273.15, 373.15, "k", "kelvin", "kelvina", "kelwina", "kelwinów", "kelwiny", "kelwinach"),
)
"""The built-in units."""

_US = {"lyzeczka": 4.92892159375, "lyzka": 14.78676478125, "uncja plynu": 29.5735295625, "szklanka": 236.5882365,
       "pinta": 473.176473, "kwarta": 946.352946, "galon": 3785.411784}
VOLUMES = {
    "metric": _US | {"lyzeczka": 5, "lyzka": 15, "szklanka": 250},
    "us": _US,
    "imperial": {"lyzeczka": 5, "lyzka": 15, "uncja plynu": 28.4130625, "szklanka": 250, "pinta": 568.26125,
                 "kwarta": 1136.5225, "galon": 4546.09},
}
"""The volumes' sizes, in milliliters, by measures: metric spoons and glass
with US customary larger ones (as built in), US customary, or imperial."""

VOLUME_NAMES = {"metric": "Metric spoons and cup (US otherwise)", "us": "US customary", "imperial": "Imperial (UK)"}

DEFAULT_VOLUMES = "metric"


def _qualifiers(stems: str, endings: str, measures: str) -> dict[str, str]:
    return {stem + ending: measures for stem in stems.split() for ending in endings.split()}


QUALIFIED = {
    "us": "us", "usa": "us", "uk": "imperial",
    **_qualifiers("amerykansk", "i a ie iego iej im ich imi", "us"),
    **_qualifiers("brytyjsk angielsk", "i a ie iego iej im ich imi", "imperial"),
    **_qualifiers("imperialn", "y a e ego ej ym ych ymi", "imperial"),
    **_qualifiers("metryczn", "y a e ego ej ym ych ymi", "metric"),
}
"""Said after a volume ("szklanka amerykańska", folded), or before it: its
measures, whichever are set."""

OUNCE, FLUID_OUNCE = "uncja", "uncja plynu"

_ONE = r"^(?:jeden|jedna|jedno|jednego|jednej|jednym|jedna|jednemu)\s+"
_QUALIFIERS = "|".join(sorted(QUALIFIED, key=len, reverse=True))


def read_unit(text: str) -> tuple[list[str], str, str | None]:
    """A unit as said ("szklanka amerykańska", "w brytyjskich galonach"):
    the names to look up -- the last one bare -- and its measures, if
    said."""
    s = key(text)
    tries = [s]
    s = re.sub(_ONE, "", s)
    s = re.sub(r"^(?:stopni|stopnie|stopniach|stopnia|stopien|stopniami)\s+", "", s)
    tries.append(s)
    measures = None
    if qualified := re.search(rf"\s+({_QUALIFIERS})$", s):
        measures, s = QUALIFIED[qualified.group(1)], s[: qualified.start()]
    elif qualified := re.match(rf"^({_QUALIFIERS})\s+", s):
        measures, s = QUALIFIED[qualified.group(1)], s[qualified.end():]
    tries.append(s)
    return tries, s, measures


def forms(name: str) -> tuple[str, ...]:
    """A name looked up as said, and as the unit it is a case of: "metrów
    kwadratowych" -> "metr kwadratowy", "litrze" -> "litr"; another unit's
    by its ending."""
    stem = re.sub(r"(?:ami|ach|om|ow|ie|y|i|e|a|u)$", "", name)
    return tuple(dict.fromkeys([name, _LEMMAS.get(name, name), stem, stem + "a"]))


def temperature_pattern(scales: str) -> re.Pattern:
    """A conversion between temperature scales (read folded): "100 stopni
    Fahrenheita na Celsjusza", "ile to jest 20 stopni Celsjusza w stopniach
    Fahrenheita"."""
    degrees = r"(?:stopni|stopnie|stopien|stopnia|stopniach|stopniami)"
    return re.compile(
        rf"(?:ile (?:to )?(?:jest |wynosi |bedzie )?|przelicz |zamien |konwertuj )?{N} (?:{degrees} )?({scales})"
        rf"\s+(?:na|w|do)\s+(?:{degrees} )?({scales})"
    )


_SCALE_OF = {"celsjusz": "Celsjusza", "fahrenheit": "Fahrenheita"}


def _said(value: float, unit, places: int) -> str:
    """"8,047 kilometra", "4 szklanki", "jedna szklanka", "dwie mile",
    "37,8 stopnia Celsjusza"."""
    number = fmt(value, places)
    whole = "," not in number
    if unit.measures == "temperature" and key(unit.name) in _SCALE_OF:
        degrees = plural(value if not whole else int(value), "stopień", "stopnie", "stopni", "stopnia")
        return f"{number} {degrees} {_SCALE_OF[key(unit.name)]}"
    known = FORMS.get(key(unit.name))
    if known is None:
        return f"{number} {unit.name if value == 1 else unit.plural_name}"
    few, many, part, feminine = known
    noun = plural(value if not whole else int(value), unit.name, few, many, part)
    if whole and feminine and abs(value) < 10**6 and (int(value) == 1 or (int(value) % 10 == 2 and int(value) % 100 != 12)):
        number = spell(int(value), feminine=True)
    return f"{number} {noun}"


def _are(value: float, places: int) -> str:
    """"jest" with one, five and more or a part ("jest 5 szklanek", "jest
    0,5 szklanki"), "są" with two to four ("są 4 szklanki")."""
    whole = "," not in fmt(value, places)
    return "są" if whole and plural(int(value), "", "few", "") == "few" else "jest"


def solve(q, settings):
    """The answer, its sentence and the conversion said whole ("5 mil to
    8,047 kilometra"); None."""
    places = settings.digits
    said_text = clean(q)
    s = fold(said_text)
    temp = settings.temperature.fullmatch(s) if settings.temperature is not None else None
    if temp:
        v = parse_num(temp.group(1))
        a, b = settings.names[temp.group(2)], settings.names[temp.group(3)]
        if v is None or a == b:
            return None
        found = _said(settings.convert(v, a, b), b, places)
        return found, f"To {found}.", f"{_said(v, a, places)} to {found}"
    if m := re.fullmatch(
        r"ile (?:stopni )?(.+?) (ma|maja|liczy|licza|jest w|sa w|miesci sie w|w|to|jest|sa|wynosi|wynosza|wazy|waza) (.+)", s
    ):
        target, verb, rest = m.groups()
        asked = said_text[m.start(3):m.end(3)]
        n = 1.0
        mm = re.fullmatch(rf"{N}\s*(.+)", rest)
        if mm and parse_num(mm.group(1)) is not None and settings.unit(mm.group(2)):
            n, rest = parse_num(mm.group(1)), mm.group(2)
        ua, ub = settings.unit(rest), settings.unit(target)
        if not (ua and ub):
            return None
        r = settings.convert(n, ua, ub)
        if r is None:
            return None
        found = _said(r, settings.ounces(ub, ua)[0], places)
        if verb.endswith("w") or verb == "w":
            restated = f"W {asked} {_are(r, places)} {found}"
        elif verb in ("to", "jest", "sa", "wynosi", "wynosza"):
            restated = f"{_capital(asked)} to {found}"
        else:
            verb_said = {"maja": "mają", "licza": "liczą", "wazy": "waży", "waza": "ważą"}.get(verb, verb)
            restated = f"{_capital(asked)} {verb_said} {found}"
        return found, f"To {found}.", restated
    m = re.fullmatch(
        rf"(?:ile (?:to )?(?:jest |wynosi |bedzie )?|przelicz |zamien |konwertuj )?{N}\s+(.+?)\s+(?:na|w|do)\s+(.+)", s
    )
    if m:
        v, ua, ub = parse_num(m.group(1)), settings.unit(m.group(2)), settings.unit(m.group(3))
        if v is not None and ua and ub:
            r = settings.convert(v, ua, ub)
            if r is None:
                return None
            a, b = settings.ounces(ua, ub)
            found = _said(r, b, places)
            return found, f"To {found}.", f"{_said(v, a, places)} to {found}"
    return None


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


def answer(text: str, settings: Settings):
    found = solve(text, settings)
    if not found:
        return None
    said, specific, restated = found
    return reply(said, text, restated, specific=specific)
