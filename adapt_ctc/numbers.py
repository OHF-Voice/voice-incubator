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
}
"""A duration unit's words, singular and plural, by language; in English,
the unit and its plural."""


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
    return list(dict.fromkeys(forms))


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
        return [
            (f"eine {single}", one),
            (f"einer {single}", one),
            (f"eineinhalb {plural}", one_and_a_half),
            (f"anderthalb {plural}", one_and_a_half),
            (f"eine halbe {single}", HALF),
            (f"einer halben {single}", HALF),
        ]
    if _base(language) == "nl":
        # Uur is a het-word ("een half uur"), minuut and seconde de-words
        # ("een halve minuut"); a kwartier is a quarter of an hour.
        single, _plural = unit_words(unit, language)
        half = "half" if unit == "hour" else "halve"
        forms = [(f"een {single}", one), (f"één {single}", one), (f"ander{half} {single}", one_and_a_half),
                 (f"een {half} {single}", HALF)]
        if unit == "hour":
            forms += [("een kwartier", Decimal("0.25")), ("drie kwartier", Decimal("0.75"))]
        return forms
    if _base(language) == "fr":
        # All feminine: "une minute", "une heure et demie", "une demi heure".
        single, _plural = unit_words(unit, language)
        forms = [(f"une {single}", one), (f"une {single} et demie", one_and_a_half), (f"une demi {single}", HALF),
                 (f"demi {single}", HALF)]
        if unit == "hour":
            forms += [("un quart d'heure", Decimal("0.25")), ("trois quarts d'heure", Decimal("0.75"))]
        return forms
    if _base(language) == "es":
        # "Un minuto", "una hora", "una hora y media", "media hora".
        single, _plural = unit_words(unit, language)
        a, half = ("una", "media") if unit == "hour" else ("un", "medio")
        forms = [(f"{a} {single}", one), (f"{a} {single} y {half}", one_and_a_half), (f"{single} y {half}", one_and_a_half),
                 (f"{half} {single}", HALF)]
        if unit == "hour":
            forms += [("un cuarto de hora", Decimal("0.25")), ("tres cuartos de hora", Decimal("0.75"))]
        return forms
    if not _is_english(language):
        return []
    forms = [(f"one {unit} and a half", one_and_a_half)]
    for article in ("a", "an"):
        forms.append((f"{article} {unit}", one))
        forms.append((f"{article} {unit} and a half", one_and_a_half))
        forms.append((f"half {article} {unit}", HALF))
    return forms


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
