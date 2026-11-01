"""What the app reads in Dutch itself, with English's names (see
voice_labs/languages/en/reading.py for what each is)."""

from __future__ import annotations

import re

# ------------------------------------------------------------------ places

PLACE_BEFORE = ("in", "op", "bij", "naar", "aan")
ARTICLES = ("de", "het", "den")

# ------------------------------------------------------------------- music

MUSIC_GENERIC = {
    "muziek", "wat muziek", "iets", "wat", "liedjes", "nummers", "de muziek", "mijn muziek", "dit", "dat",
    "dit nummer", "dat nummer", "het", "iets leuks",
}

MUSIC_CUES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^(?:wat |een paar )?(?:liedjes|nummers|muziek|iets) van (?P<q>.+)$"), "artist"),
    (re.compile(r"^(?:de )?(?:artiest|band|zanger|zangeres) (?P<q>.+)$"), "artist"),
    (re.compile(r"^(?:het )?album (?P<q>.+)$"), "album"),
    (re.compile(r"^(?:het )?(?P<q>.+) album$"), "album"),
    (re.compile(r"^(?:het |de )?(?:nummer|liedje|lied|track) (?P<q>.+)$"), "track"),
    (re.compile(r"^(?:de |mijn )?(?:afspeellijst|playlist) (?P<q>.+)$"), "playlist"),
    # "mijn jazz-afspeellijst", "de jazzplaylist"
    (re.compile(r"^(?:de |mijn )?(?P<q>.+?)[ -]?(?:afspeellijst|playlist)$"), "playlist"),
    (re.compile(r"^(?:de )?(?:radiozender|zender) (?P<q>.+)$"), "radio"),
    (re.compile(r"^(?:de )?(?P<q>.+?)[ -]?(?:radiozender|zender)$"), "radio"),
]

MUSIC_LEADING = re.compile(r"^(?:wat |een beetje |iets )+")
# "speel jazz af"
MUSIC_TRAILING = re.compile(r"(?: alsjeblieft| alstublieft| af| voor mij| op music assistant)+$")

BY = "van"
AND = ("en",)
"""What joins items said together ("apples and bananas"): a word of its own."""
PLURALS = (("en", ""), ("s", ""))
"""A plural's ending, and the singular's in its place, for telling an item said
from one on a to-do list (actions.same_item): "appels" is "appel", "bananen"
"banaan". A word has one only when it is longer than the ending by three
letters."""

# ----------------------------------------------------------------- answers
# What the app asked -- the conversation agent's question, a command's
# yes-or-no one -- answered (voice_labs/follow_up.py).

ANSWER_ENDS = {
    "annuleer", "annuleren", "laat maar", "vergeet het", "stop", "niets", "nee dank je",
    "nee bedankt", "dat was het", "dat is alles", "verder niets", "klaar", "doei",
}
"""An answer that is only one of these ends the conversation instead of
going on with it: "Verder nog iets?" -- "nee bedankt". Lowercase, without punctuation."""

ANSWER_YES = {
    "ja", "jazeker", "zeker", "oké", "ok", "prima", "goed", "doe maar", "doe het", "ga je gang",
    "graag", "natuurlijk", "klopt", "ja graag",
}
"""Yes to a command's question (``confirm``): an answer with one of these in
it, and none of ANSWER_NO."""

ANSWER_NO = {
    "nee", "neen", "niet", "nooit", "liever niet", "wacht", "laat maar staan", "laat ze", "houd ze",
    "hou ze", "maar", "behalve",
}
"""No to it: an answer with one of these in it (or that only ends it), and
none of ANSWER_YES -- with both, it isn't clear."""

YES_SAID = (
    "ja", "zeker", "oké", "prima", "doe maar", "doe het", "ga je gang", "graag", "natuurlijk",
    "klopt",
)
NO_SAID = (
    "nee", "liever niet", "nooit", "niet nu", "wacht", "laat maar staan", "laat ze", "hou ze",
)
"""Yes and no said whole, without words that only doubt it ("but", "not"):
what an answer with neither, or both, is told apart by, by meaning."""

# ------------------------------------------------------------------- clock

ALARM_EXAMPLE = "half acht 's ochtends"

_SMALL = {
    "nul": 0, "een": 1, "één": 1, "twee": 2, "drie": 3, "vier": 4, "vijf": 5, "zes": 6, "zeven": 7, "acht": 8,
    "negen": 9, "tien": 10, "elf": 11, "twaalf": 12, "dertien": 13, "veertien": 14, "vijftien": 15, "zestien": 16,
    "zeventien": 17, "achttien": 18, "negentien": 19,
}
_TENS = {"twintig": 20, "dertig": 30, "veertig": 40, "vijftig": 50}
_COMPOUND = re.compile(r"^(een|twee|drie|vier|vijf|zes|zeven|acht|negen)(?:en|ën)(twintig|dertig|veertig|vijftig)$")


def _number(word: str) -> int | None:
    """"7", "zeven", "vijfentwintig"."""
    if word.isdigit():
        return int(word)
    if word in _SMALL:
        return _SMALL[word]
    if word in _TENS:
        return _TENS[word]
    found = _COMPOUND.match(word)
    return _SMALL[found.group(1)] + _TENS[found.group(2)] if found else None


# "tien over half acht" is 7:40; "half acht" 7:30; "kwart voor acht" 7:45.
_SAID = [
    (re.compile(r"(\d+) (?:minuten )?over half (\d+)"), lambda n, h: (h - 1, 30 + n)),
    (re.compile(r"(\d+) (?:minuten )?voor half (\d+)"), lambda n, h: (h - 1, 30 - n)),
    (re.compile(r"kwart over (\d+)"), lambda h: (h, 15)),
    (re.compile(r"kwart voor (\d+)"), lambda h: (h - 1, 45)),
    (re.compile(r"half (\d+)"), lambda h: (h - 1, 30)),
    (re.compile(r"(\d+) (?:minuten )?over (\d+)"), lambda n, h: (h, n)),
    (re.compile(r"(\d+) (?:minuten )?voor (\d+)"), lambda n, h: (h - 1, 60 - n)),
    (re.compile(r"(\d+) uur (\d+)"), lambda h, m: (h, m)),
    (re.compile(r"(\d+) uur"), lambda h: (h, 0)),
]


def clock(text: str) -> tuple[int, int, str | None] | None:
    """A time of day said or written: "half acht", "kwart over zeven", "zeven
    uur dertig", "19.30 uur", "om 7 uur 's avonds"; "pm" in the afternoon,
    evening or night, "am" in the morning, else None."""
    text = re.sub(r"(\d{1,2})[:.](\d{2})", r"\1 uur \2", text.lower())
    # "halfzeven", "kwartover": written as one.
    text = re.sub(r"\b(half|kwart)(?=[a-z])", r"\1 ", text)
    text = re.sub(r"\b(kwart )(over|voor)(?=[a-z])", r"\1\2 ", text)
    words = re.findall(r"[a-zéë]+|\d+", text)
    meridiem = None
    if any(w in ("middags", "avonds", "nachts", "middag", "avond", "nacht", "vanavond") for w in words):
        meridiem = "pm"
    elif any(w in ("ochtends", "morgens", "ochtend", "vroeg") for w in words):
        meridiem = "am"
    if "middernacht" in words:
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
