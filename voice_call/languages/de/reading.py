"""What the app reads in German itself, with English's names (see
voice_call/languages/en/reading.py for what each is)."""

from __future__ import annotations

import re

# ------------------------------------------------------------------ places

PLACE_BEFORE = ("in", "im", "ins", "auf", "am", "an", "zu", "zum", "zur", "nach", "bei", "beim")
ARTICLES = ("der", "die", "das", "dem", "den", "des")

# ------------------------------------------------------------------- music

MUSIC_GENERIC = {
    "musik", "etwas musik", "irgendwas", "irgendetwas", "etwas", "was", "lieder", "ein paar lieder",
    "die musik", "meine musik", "das", "dies", "das hier", "dieses lied", "das lied", "diesen song",
    "den song", "es",
}

MUSIC_CUES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^(?:etwas |ein paar |irgendwas )?(?:lieder|songs|musik|titel|was|etwas) von (?P<q>.+)$"), "artist"),
    (re.compile(r"^(?:den |dem |die )?(?:künstler|künstlerin|interpreten|interpret|band) (?P<q>.+)$"), "artist"),
    (re.compile(r"^(?:das )?album (?P<q>.+)$"), "album"),
    (re.compile(r"^(?:das )?(?P<q>.+) album$"), "album"),
    (re.compile(r"^(?:das |den )?(?:lied|song|titel|track|stück) (?P<q>.+)$"), "track"),
    (re.compile(r"^(?:die |meine )?(?:playlist|wiedergabeliste) (?P<q>.+)$"), "playlist"),
    # "die Jazz-Playlist", "meine Jazzplaylist"
    (re.compile(r"^(?:die |meine )?(?P<q>.+?)[ -]?(?:playlist|wiedergabeliste)$"), "playlist"),
    (re.compile(r"^(?:den )?(?:radiosender|sender) (?P<q>.+)$"), "radio"),
    (re.compile(r"^(?:den )?(?P<q>.+?)[ -]?(?:radiosender|sender)$"), "radio"),
]

MUSIC_LEADING = re.compile(r"^(?:mir |uns |etwas |ein bisschen )+")
# "spiel Jazz ab"
MUSIC_TRAILING = re.compile(r"(?: bitte| für mich| für uns| ab| auf music assistant)+$")

BY = "von"
AND = ("und",)
"""What joins items said together ("apples and bananas"): a word of its own."""
PLURALS = (("en", ""), ("n", ""), ("e", ""))
"""A plural's ending, and the singular's in its place, for telling an item said
from one on a to-do list (actions.same_item): "Birnen" is "Birne", "Brote"
"Brot". A word has one only when it is longer than the ending by three
letters."""

# ------------------------------------------------------------------- clock

ALARM_EXAMPLE = "halb acht morgens"

_SMALL = {
    "null": 0, "ein": 1, "eins": 1, "zwei": 2, "zwo": 2, "drei": 3, "vier": 4, "fünf": 5,
    "sechs": 6, "sieben": 7, "acht": 8, "neun": 9, "zehn": 10, "elf": 11, "zwölf": 12, "dreizehn": 13,
    "vierzehn": 14, "fünfzehn": 15, "sechzehn": 16, "siebzehn": 17, "achtzehn": 18, "neunzehn": 19,
}
_TENS = {"zwanzig": 20, "dreißig": 30, "dreissig": 30, "vierzig": 40, "fünfzig": 50}
_COMPOUND = re.compile(r"^(ein|zwei|drei|vier|fünf|sechs|sieben|acht|neun)und(zwanzig|dreißig|dreissig|vierzig|fünfzig)$")


def _number(word: str) -> int | None:
    """"7", "sieben", "fünfundzwanzig"."""
    if word.isdigit():
        return int(word)
    if word in _SMALL:
        return _SMALL[word]
    if word in _TENS:
        return _TENS[word]
    found = _COMPOUND.match(word)
    return _SMALL[found.group(1)] + _TENS[found.group(2)] if found else None


# "fünf nach halb sieben" is 6:35; "halb sieben" is 6:30; "dreiviertel
# sieben" (as some say it) and "viertel vor sieben" are 6:45. First that
# fits wins, so the longer come first.
_SAID = [
    (re.compile(r"(\d+) (?:minuten? )?nach halb (\d+)"), lambda n, h: (h - 1, 30 + n)),
    (re.compile(r"(\d+) (?:minuten? )?vor halb (\d+)"), lambda n, h: (h - 1, 30 - n)),
    (re.compile(r"viertel nach (\d+)"), lambda h: (h, 15)),
    (re.compile(r"viertel vor (\d+)"), lambda h: (h - 1, 45)),
    (re.compile(r"dreiviertel (\d+)"), lambda h: (h - 1, 45)),
    (re.compile(r"halb (\d+)"), lambda h: (h - 1, 30)),
    (re.compile(r"(\d+) (?:minuten? )?nach (\d+)"), lambda n, h: (h, n)),
    (re.compile(r"(\d+) (?:minuten? )?vor (\d+)"), lambda n, h: (h - 1, 60 - n)),
    (re.compile(r"(\d+) uhr (\d+)"), lambda h, m: (h, m)),
    (re.compile(r"(\d+) uhr"), lambda h: (h, 0)),
]


def clock(text: str) -> tuple[int, int, str | None] | None:
    """A time of day said or written: "halb acht", "viertel nach sieben",
    "sieben Uhr dreißig", "19.30 Uhr", "um 7 abends", "Mittag"; "pm" for the
    evening, "am" for the morning, else None (the next time it comes)."""
    text = re.sub(r"(\d{1,2})[:.](\d{2})", r"\1 uhr \2", text.lower())
    words = re.findall(r"[a-zäöüß]+|\d+", text)
    meridiem = None
    if any(w in ("abends", "abend", "nachmittags", "nachmittag", "nachts", "nacht") for w in words):
        meridiem = "pm"
    elif any(w in ("morgens", "früh", "frühmorgens", "vormittags", "vormittag") for w in words):
        meridiem = "am"
    if "mittag" in words or "mittags" in words:
        return 12, 0, "pm"
    if "mitternacht" in words:
        return 0, 0, "am"
    tokens = [str(n) if (n := _number(w)) is not None else w for w in words]
    numbers = [int(t) for t in tokens if t.isdigit()]
    if not numbers:
        return None
    joined = " ".join(tokens)
    for pattern, time in _SAID:
        found = pattern.search(joined)
        if found:
            hour, minute = time(*(int(g) for g in found.groups()))
            return _valid(hour % 24, minute, meridiem)
    first = numbers[0]
    if first >= 100 and len(numbers) == 1:
        return _valid(first // 100, first % 100, meridiem)
    return _valid(first, numbers[1] if len(numbers) > 1 else 0, meridiem)


def _valid(hour: int, minute: int, meridiem: str | None) -> tuple[int, int, str | None] | None:
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    if hour > 12:
        meridiem = None  # 24-hour
    return hour, minute, meridiem
