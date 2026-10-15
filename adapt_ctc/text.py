"""Written phrases to the words that would be heard.

Configuration text is written for people: "EcoBee", "Zone 2", "Kid's Room",
"TV & Sound". The acoustic model hears none of that punctuation and has no
digit pieces, so each phrase is reduced to one or more spoken spellings.
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal

from .numbers import spellout, spellout_ordinal

_APOSTROPHES = "’ʼ‘′`´"
_DIGITS = re.compile(r"(\d+(?:\.\d+)?)(st|nd|rd|th)?\b", re.IGNORECASE)
_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])")
_SYMBOLS = {"&": " and ", "%": " percent ", "+": " plus ", "@": " at "}


def spoken_words(text: str, language: str) -> list[list[str]]:
    """Spoken spellings of ``text``, each as a list of lowercase words.

    The first spelling is the canonical one. A possessive or contraction also
    yields the spelling without its apostrophe, since the model writes "kids
    room" about as often as "kid's room".
    """
    return [
        [part for word in spelling for part in word]
        for spelling in _spellings(text, language)
    ]


def sentence_key(text: str, language: str) -> str:
    """A sentence reduced for exact comparison with a transcript: spoken
    words, without case, punctuation, apostrophes or spaces. The transducer
    writes "per cent" as often as "percent", and "what's" as "whats"."""
    spellings = spoken_words(text, language)
    if not spellings:
        return ""
    return "".join(word.replace("'", "") for word in spellings[0])


def name_units(text: str, language: str) -> list[list[str]]:
    """Spellings of a name as units: one per word as written, with the parts
    of a compound joined ("EcoBee" and "Eco-Bee" are one unit, "ecobee").

    Names match with word breaks anywhere, so splitting a compound buys
    nothing; keeping written words apart lets names that start alike
    ("Kitchen Lamp", "Kitchen Fan") share the start of their lattices.
    """
    return [["".join(word) for word in spelling] for spelling in _spellings(text, language)]


def _spellings(text: str, language: str) -> list[list[list[str]]]:
    """Spellings as written words, each a list of its spoken parts."""
    text = unicodedata.normalize("NFC", text)
    for apostrophe in _APOSTROPHES:
        text = text.replace(apostrophe, "'")
    for symbol, words in _SYMBOLS.items():
        text = text.replace(symbol, words)
    text = _DIGITS.sub(lambda m: f" {_spell_digits(m, language)} ", text)

    words = []
    for written in text.split():
        # "EcoBee" -> "Eco Bee"; "Eco-Bee" -> "Eco Bee".
        written = _CAMEL.sub(" ", written)
        cleaned = "".join(ch if ch.isalnum() or ch == "'" else " " for ch in written)
        parts = [part.strip("'") for part in cleaned.lower().split()]
        parts = [part for part in parts if part]
        if parts:
            words.append(parts)
    if not words:
        return []

    spellings = [words]
    if any("'" in part for word in words for part in word):
        spellings.append([[part.replace("'", "") for part in word] for word in words])
    return spellings


def _spell_digits(match: re.Match[str], language: str) -> str:
    number, suffix = match.group(1), match.group(2)
    if suffix and "." not in number:
        return spellout_ordinal(int(number), language)
    return spellout(Decimal(number), language)
