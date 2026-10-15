"""Numbers as they are spoken, from ICU's rule-based number format.

The acoustic vocabulary has no digit pieces, so a number can only be heard as
words. ICU supplies the canonical spelling for any locale; English gets the
handful of everyday forms RBNF does not produce ("a hundred", "one hundred and
five", "seventy two and a half").
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


def unit_forms(unit: str, language: str) -> list[tuple[str, Decimal]]:
    """Numbers said only together with their unit: "a minute" (1), "an hour
    and a half" (1.5), "half a minute" (0.5). Each is ``(phrase, value)``."""
    if not _is_english(language):
        return []
    one, one_and_a_half = Decimal(1), Decimal("1.5")
    forms = [(f"one {unit} and a half", one_and_a_half)]
    for article in ("a", "an"):
        forms.append((f"{article} {unit}", one))
        forms.append((f"{article} {unit} and a half", one_and_a_half))
        forms.append((f"half {article} {unit}", HALF))
    return forms


def _is_english(language: str) -> bool:
    return language.split("_")[0].split("-")[0].lower() == "en"


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
