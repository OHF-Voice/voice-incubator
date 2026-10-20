"""Numbers as they are spoken, from ICU's rule-based number format.

The acoustic vocabulary has no digit pieces, so a number can only be heard as
words. ICU supplies the canonical spelling for any locale; English gets the
handful of everyday forms RBNF does not produce ("a hundred", "one hundred and
five", "seventy two and a half").

A duration's units are named in English ("minute"), and said in the
language's words (``unit_words``: "minuten"); some numbers are said only with
their unit (``unit_forms``: "a minute and a half", "eineinhalb Minuten").
"""

from __future__ import annotations

import unicodedata
from decimal import Decimal
from functools import lru_cache

import icu_rbnf

HALF = Decimal("0.5")

DURATION_UNITS = {"second": 1, "minute": 60, "hour": 3600}
"""Units a compound duration may be made of, in seconds: "one hour five
minutes and thirty seconds"."""

UNIT_WORDS: dict[str, dict[str, tuple[str, str]]] = {
    "de": {"second": ("sekunde", "sekunden"), "minute": ("minute", "minuten"), "hour": ("stunde", "stunden")},
    "nl": {"second": ("seconde", "seconden"), "minute": ("minuut", "minuten"), "hour": ("uur", "uren")},
    "fr": {"second": ("seconde", "secondes"), "minute": ("minute", "minutes"), "hour": ("heure", "heures")},
    "es": {"second": ("segundo", "segundos"), "minute": ("minuto", "minutos"), "hour": ("hora", "horas")},
    "it": {"second": ("secondo", "secondi"), "minute": ("minuto", "minuti"), "hour": ("ora", "ore")},
    # Slavic: one, five and more, two to four, and "for one" ("na minutę").
    "pl": {"second": ("sekunda", "sekund", "sekundy", "sekundę"), "minute": ("minuta", "minut", "minuty", "minutę"),
           "hour": ("godzina", "godzin", "godziny", "godzinę")},
    "ru": {"second": ("секунда", "секунд", "секунды", "секунду"), "minute": ("минута", "минут", "минуты", "минуту"),
           "hour": ("час", "часов", "часа")},
    "uk": {"second": ("секунда", "секунд", "секунди", "секунду"), "minute": ("хвилина", "хвилин", "хвилини", "хвилину"),
           "hour": ("година", "годин", "години", "годину")},
}
"""A duration unit's words, singular and plural first, by language (Slavic
has more: "2 minuty", "5 minut", "na minutę"); in English, the unit and its
plural."""


def value_grid(minimum: Decimal, maximum: Decimal, step: Decimal) -> list[Decimal]:
    """Every value from ``minimum`` to ``maximum`` inclusive, ``step`` apart."""
    if step <= 0:
        raise ValueError("step must be positive")
    if maximum < minimum:
        raise ValueError("max must not be below min")
    count = int((maximum - minimum) / step)
    return [minimum + step * i for i in range(count + 1)]


def on_grid(value: Decimal, minimum: Decimal, maximum: Decimal, step: Decimal) -> bool:
    return minimum <= value <= maximum and (value - minimum) % step == 0


def to_python(value: Decimal) -> int | float:
    return int(value) if value == value.to_integral_value() else float(value)


def _clean(text: str) -> str:
    """ICU's text as plain words: no syllable-hint format characters, hyphens
    as word breaks ("twenty-one" is two spoken words)."""
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    text = unicodedata.normalize("NFC", text).replace("-", " ").replace("­", "")
    text = text.replace("ʼ", "'").replace("’", "'")  # Ukrainian "пʼять", as models write it
    return " ".join(text.lower().split())


@lru_cache(maxsize=None)
def spellout(value: Decimal, language: str) -> str:
    number = to_python(value)
    return _clean(icu_rbnf.spellout(number, language))


@lru_cache(maxsize=None)
def spellout_ordinal(value: int, language: str) -> str:
    return _clean(icu_rbnf.spellout_ordinal(value, language))


def spoken_forms(value: Decimal, language: str) -> list[str]:
    """Every way of saying ``value`` this recognizer listens for."""
    forms = [spellout(value, language)]
    if _is_english(language):
        forms.extend(_english_forms(value, language))
    forms.extend(_before_a_noun(forms[0], language))
    return list(dict.fromkeys(forms))


def _before_a_noun(spelled: str, language: str) -> list[str]:
    """``spelled`` as said before a noun, where that changes its last word:
    Spanish "veintiún grados", "treinta y una horas"; French "vingt et une
    minutes". ICU spells the counting form ("veintiuno", "vingt et un").
    Not one alone: "un", "una", "une" are articles too."""
    words = spelled.split()
    base = _base(language)
    if base == "es" and words and (words[-1] == "veintiuno" or (len(words) > 1 and words[-1] == "uno")):
        endings = ("veintiún", "veintiuna") if words[-1] == "veintiuno" else ("un", "una")
        return [" ".join([*words[:-1], ending]) for ending in endings]
    if base == "fr" and len(words) > 1 and words[-1] == "un":
        return [" ".join([*words[:-1], "une"])]
    if base == "it" and words and len(words[-1]) > 3 and words[-1].endswith("uno"):
        return [" ".join([*words[:-1], words[-1][:-1]])]  # "ventun minuti"
    if words and words[-1] in _FEMININE.get(base, {}):
        # "Одну минуту", "две минуты", "dwie minuty", "двадцять одну хвилину".
        return [" ".join([*words[:-1], form]) for form in _FEMININE[base][words[-1]]]
    return []


_FEMININE = {
    "pl": {"jeden": ("jedna", "jedną"), "dwa": ("dwie",)},
    "ru": {"один": ("одна", "одну"), "два": ("две",)},
    "uk": {"один": ("одна", "одну"), "два": ("дві",)},
}
"""A number's last word before a feminine noun, by language: Slavic
minutes, seconds and (but Russian's "час") hours are feminine."""


HALVES = range(2, 31)
"""Counts said with their unit and a half after it: "dos minutos y medio",
"deux heures et demie"."""


def unit_words(unit: str, language: str) -> tuple[str, str]:
    """The words ``unit`` ("minute") is said with: singular, plural."""
    return UNIT_WORDS.get(_base(language), {}).get(unit) or (unit, unit + "s")


def unit_forms(unit: str, language: str) -> list[tuple[str, Decimal]]:
    """Numbers said only together with their unit: "a minute" (1), "an hour
    and a half" (1.5), "half a minute" (0.5). Each is ``(phrase, value)``."""
    one, one_and_a_half = Decimal(1), Decimal("1.5")
    if _base(language) == "de":
        # Stunde, Minute, Sekunde: all feminine. "Eine Minute", "in einer
        # Stunde", "eineinhalb Minuten", "eine halbe Stunde".
        single, plural = unit_words(unit, language)
        forms = [
            (f"eine {single}", one),
            (f"einer {single}", one),
            (f"eineinhalb {plural}", one_and_a_half),
            (f"anderthalb {plural}", one_and_a_half),
            (f"eine halbe {single}", HALF),
            (f"einer halben {single}", HALF),
        ]
        # "Zweieinhalb Minuten", "zwei und eine halbe Stunde".
        for count in map(Decimal, HALVES):
            said = spellout(count, language)
            forms += [(f"{said}einhalb {plural}", count + HALF), (f"{said} und eine halbe {single}", count + HALF)]
        return forms
    if _base(language) == "nl":
        # Uur is a het-word ("een half uur"), minuut and seconde de-words
        # ("een halve minuut"); a kwartier is a quarter of an hour.
        single, _plural = unit_words(unit, language)
        half = "half" if unit == "hour" else "halve"
        forms = [(f"een {single}", one), (f"één {single}", one), (f"ander{half} {single}", one_and_a_half),
                 (f"een {half} {single}", HALF)]
        if unit == "hour":
            forms += [("een kwartier", Decimal("0.25")), ("drie kwartier", Decimal("0.75"))]
        # "Tweeënhalve minuut", "vierenhalf uur", "twee en een halve minuut";
        # said quickly, its "en" all but gone and "halve" heard as "half":
        # "vier half uur", "twee half minuut".
        for count in map(Decimal, HALVES):
            said = spellout(count, language)
            joined = f"{said}{'ën' if said.endswith('e') else 'en'}{half}"
            forms += [(f"{joined} {single}", count + HALF)]
            forms += [(f"{said}{between}{word} {single}", count + HALF)
                      for between in (" en een ", " een ", " ") for word in dict.fromkeys((half, "half"))]
        return forms
    if _base(language) == "fr":
        # All feminine: "une minute", "une heure et demie", "une demi heure";
        # after "de", elided: "un minuteur d'une heure".
        single, plural = unit_words(unit, language)
        forms = [(f"{a} {single}", one) for a in ("une", "d'une")]
        forms += [(f"{a} {single} et demie", one_and_a_half) for a in ("une", "d'une")]
        forms += [(f"{a} {single}", HALF) for a in ("une demi", "d'une demi", "demi")]
        if unit == "hour":
            forms += [("un quart d'heure", Decimal("0.25")), ("d'un quart d'heure", Decimal("0.25")),
                      ("trois quarts d'heure", Decimal("0.75"))]
        # "Deux minutes et demie", "vingt et une heures et demie".
        forms += [(f"{said} {plural} et demie", count + HALF)
                  for count in map(Decimal, HALVES) for said in _feminine(spoken_forms(count, language))]
        return forms
    if _base(language) == "es":
        # "Un minuto", "una hora", "una hora y media", "media hora".
        single, plural = unit_words(unit, language)
        a, half = ("una", "media") if unit == "hour" else ("un", "medio")
        forms = [(f"{a} {single}", one), (f"{a} {single} y {half}", one_and_a_half), (f"{single} y {half}", one_and_a_half),
                 (f"{half} {single}", HALF)]
        if unit == "hour":
            forms += [("un cuarto de hora", Decimal("0.25")), ("tres cuartos de hora", Decimal("0.75"))]
        # "Dos minutos y medio", "dos horas y media", "veintiún minutos y medio".
        said = _feminine if unit == "hour" else _masculine
        forms += [(f"{form} {plural} y {half}", count + HALF)
                  for count in map(Decimal, HALVES) for form in said(spoken_forms(count, language))]
        return forms
    if _base(language) == "it":
        # Ora is feminine and elides: "un'ora", "un'ora e mezza", "mezz'ora";
        # minuto and secondo masculine: "un minuto e mezzo", "mezzo minuto".
        single, plural = unit_words(unit, language)
        if unit == "hour":
            forms = [("un'ora", one), ("un ora", one), ("un'ora e mezza", one_and_a_half), ("un ora e mezza", one_and_a_half),
                     ("mezz'ora", HALF), ("mezza ora", HALF), ("un quarto d'ora", Decimal("0.25")),
                     ("tre quarti d'ora", Decimal("0.75"))]
            half = "mezza"
        else:
            forms = [(f"un {single}", one), (f"un {single} e mezzo", one_and_a_half), (f"mezzo {single}", HALF)]
            half = "mezzo"
        # "Due minuti e mezzo", "due ore e mezza", "ventun minuti e mezzo".
        forms += [(f"{said} {plural} e {half}", count + HALF)
                  for count in map(Decimal, HALVES) for said in spoken_forms(count, language)]
        return forms
    if _base(language) in _SLAVIC_UNITS:
        return _slavic_unit_forms(unit, language)
    if not _is_english(language):
        return []
    forms = [(f"one {unit} and a half", one_and_a_half)]
    for article in ("a", "an"):
        forms.append((f"{article} {unit}", one))
        forms.append((f"{article} {unit} and a half", one_and_a_half))
        forms.append((f"half {article} {unit}", HALF))
    return forms


_SLAVIC_UNITS = {
    # One said by the unit alone ("na minutę"), a half, one and a half, and a
    # count and a half ("dwie i pół minuty").
    "pl": {"one": "{acc}", "half": ("pół {few}",), "one_and_a_half": ("półtorej {few}",),
           "and_a_half": ("{n} i pół {few}",), "quarters": (("kwadrans", "0.25"), ("trzy kwadranse", "0.75"))},
    "ru": {"one": "{acc}", "half": ("пол{few}", "пол {few}"), "one_and_a_half": ("полторы {few}", "полтора {few}"),
           "and_a_half": ("{n} с половиной {few}", "{n} с половиной {many}"),
           "quarters": (("четверть часа", "0.25"), ("три четверти часа", "0.75"))},
    "uk": {"one": "{acc}", "half": ("пів{few}", "пів {few}"), "one_and_a_half": ("півтори {few}",),
           "and_a_half": ("{n} з половиною {few}", "{n} з половиною {many}"),
           "quarters": (("чверть години", "0.25"), ("три чверті години", "0.75"))},
}
"""How Polish, Russian and Ukrainian say a duration by its unit: ``{acc}``
the unit as in "for one" (минуту; Russian's hour is "час"), ``{few}`` its
form after two to four and in "half", ``{many}`` after five."""


def _slavic_unit_forms(unit: str, language: str) -> list[tuple[str, Decimal]]:
    """"Na minutę", "полчаса", "півтори години", "dwie i pół minuty",
    "четверть часа"."""
    rules = _SLAVIC_UNITS[_base(language)]
    words = unit_words(unit, language)
    said = {"one": words[0], "many": words[1], "few": words[2], "acc": words[3] if len(words) > 3 else words[0]}
    forms = [(rules["one"].format(**said), Decimal(1))]
    forms += [(form.format(**said), HALF) for form in rules["half"]]
    forms += [(form.format(**said), Decimal("1.5")) for form in rules["one_and_a_half"]]
    forms += [(form.format(n=n, **said), count + HALF)
              for count in map(Decimal, HALVES) for n in spoken_forms(count, language)
              for form in rules["and_a_half"]]
    if unit == "hour":
        forms += [(form, Decimal(value)) for form, value in rules["quarters"]]
    return list(dict.fromkeys(forms))


def _feminine(forms: list[str]) -> list[str]:
    """The forms before a feminine noun: "veintiuna", "vingt et une"."""
    return [f for f in forms if not f.endswith((" un", " uno", "veintiuno", "veintiún"))] or forms


def _masculine(forms: list[str]) -> list[str]:
    """The forms before a masculine noun: "veintiún", "treinta y un"."""
    return [f for f in forms if not f.endswith((" una", " uno", "veintiuno", "veintiuna"))] or forms


def _base(language: str) -> str:
    """"de" of "de", "de-CH", "de_DE"."""
    return language.split("_")[0].split("-")[0].lower()


def _is_english(language: str) -> bool:
    return _base(language) == "en"


def _english_forms(value: Decimal, language: str) -> list[str]:
    negative = value < 0
    magnitude = -value if negative else value
    whole = magnitude.to_integral_value(rounding="ROUND_FLOOR")
    fraction = magnitude - whole

    if fraction == 0:
        bodies = _english_integer_forms(whole, language)
    elif fraction == HALF:
        bodies = []
        if whole == 0:
            bodies.extend(["half", "a half", "one half", "point five"])
        else:
            for integer in _english_integer_forms(whole, language):
                bodies.append(f"{integer} and a half")
                bodies.append(f"{integer} point five")
    else:
        return []

    signs = ["minus", "negative"] if negative else [""]
    return [f"{sign} {body}".strip() for sign in signs for body in bodies]


def _english_integer_forms(whole: Decimal, language: str) -> list[str]:
    base = spellout(whole, language)
    forms = [base]
    words = base.split()

    # British "and": "one hundred and five", "two thousand and twelve".
    for index in range(len(words) - 1, -1, -1):
        if words[index] in {"hundred", "thousand"} and index + 1 < len(words):
            forms.append(" ".join(words[: index + 1] + ["and"] + words[index + 1 :]))
            break

    # Counted in hundreds: "twenty seven hundred", "nineteen hundred fifty".
    hundreds, rest = divmod(int(whole), 100)
    if 11 <= hundreds <= 99 and hundreds % 10:
        counted = f"{spellout(Decimal(hundreds), language)} hundred"
        forms.append(f"{counted} {spellout(Decimal(rest), language)}" if rest else counted)

    # "a hundred", "a thousand and five".
    for form in list(forms):
        parts = form.split()
        if len(parts) >= 2 and parts[0] == "one" and parts[1] in {"hundred", "thousand"}:
            forms.append(" ".join(["a"] + parts[1:]))
    return forms
