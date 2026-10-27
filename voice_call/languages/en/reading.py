"""What the app reads in English itself: text the matcher hands on as said --
a place at the end of free text, what to play, an alarm's time.

Each language's pack has a reading module with these names (the tests hold
every pack to English's). The matcher's own words -- filler, name cues,
numbers -- are its config (the pack's ``adapt``), not this.
"""

from __future__ import annotations

import re

# ------------------------------------------------------------------ places
# Free text ends before a place said after it: "jazz in the kitchen".

PLACE_BEFORE = ("in", "on", "at", "to")
"""A word that may come before a place."""

ARTICLES = ("the",)
"""Articles: after PLACE_BEFORE, before a place; text of nothing but these
is no text."""

# ------------------------------------------------------------------- music

MUSIC_GENERIC = {
    "music", "some music", "something", "anything", "songs", "some songs", "the music", "my music",
    "this", "that", "this one", "that one", "this song", "that song", "it",
}
"""Asked to play these, the player resumes instead; to play music like
these, like what is playing."""

MUSIC_CUES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^(?:some |any )?(?:songs|music|something|anything|stuff|tracks) (?:by|from) (?P<q>.+)$"), "artist"),
    (re.compile(r"^(?:the )?artist (?P<q>.+)$"), "artist"),
    (re.compile(r"^(?:the )?album (?P<q>.+)$"), "album"),
    (re.compile(r"^(?:the )?(?P<q>.+) album$"), "album"),
    (re.compile(r"^(?:the )?(?:song|track) (?P<q>.+)$"), "track"),
    (re.compile(r"^(?:the )?(?P<q>.+) (?:song|track)$"), "track"),
    (re.compile(r"^(?:the |my )?playlist (?P<q>.+)$"), "playlist"),
    (re.compile(r"^(?:the |my )?(?P<q>.+) playlist$"), "playlist"),
    (re.compile(r"^(?:the )?(?:radio station|station) (?P<q>.+)$"), "radio"),
    (re.compile(r"^(?:the )?(?P<q>.+) (?:radio station|station)$"), "radio"),
    (re.compile(r"^(?:the )?genre (?P<q>.+)$"), "genre"),
    (re.compile(r"^(?:the )?(?P<q>.+) genre$"), "genre"),
    (re.compile(r"^(?P<q>.+) music$"), "genre"),
]
"""What to play, with a cue to its type: "the album …", "songs by …", "my …
playlist", "… music" (a genre); group ``q`` is what is searched for."""

MUSIC_LEADING = re.compile(r"^(?:me |us |some )+")
MUSIC_TRAILING = re.compile(r"(?: please| for me| for us| on music assistant)+$")
"""Politeness around what to play (lowercase, without punctuation)."""

BY = "by"
AND = ("and",)
"""What joins items said together ("apples and bananas"): a word of its own."""
"""Between a title and its artist: "so what by miles davis"."""

# ------------------------------------------------------------------- clock

ALARM_EXAMPLE = "seven thirty in the morning"
"""An alarm's time, said, for the examples in the UI."""

_SMALL = {
    "zero": 0, "oh": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50}


def _number_words(words: list[str]) -> list[int | str]:
    """Number words to numbers ("twenty five" -> 25, "oh five" -> 0, 5),
    other words kept."""
    out: list[int | str] = []
    index = 0
    while index < len(words):
        word = words[index]
        if word.isdigit():
            out.append(int(word))
        elif word in _TENS:
            value = _TENS[word]
            if index + 1 < len(words) and words[index + 1] in _SMALL and 0 < _SMALL[words[index + 1]] < 10:
                value += _SMALL[words[index + 1]]
                index += 1
            out.append(value)
        elif word in _SMALL:
            out.append(_SMALL[word])
        else:
            out.append(word)
        index += 1
    return out


def clock(text: str) -> tuple[int, int, str | None] | None:
    """A time of day said or written: (hour, minute, "am" | "pm" | None) --
    "seven thirty", "6 am", "half past six", "noon", "19:30" -- or None."""
    text = text.lower().replace(".", "").replace("o'clock", " ").replace("oclock", " ")
    text = re.sub(r"(\d)(am|pm)\b", r"\1 \2", text)
    text = re.sub(r"(\d{1,2}):(\d{2})", r"\1 \2", text)
    words = re.findall(r"[a-z]+|\d+", text)
    meridiem = None
    if any(w in ("pm", "evening", "night", "afternoon", "tonight") for w in words):
        meridiem = "pm"
    elif any(w in ("am", "morning") for w in words):
        meridiem = "am"
    if "noon" in words or "midday" in words:
        return 12, 0, "pm"
    if "midnight" in words:
        return 0, 0, "am"
    tokens = _number_words(words)
    numbers = [t for t in tokens if isinstance(t, int)]
    if len(numbers) > 1 and numbers[0] == 0 and tokens[0] == 0:
        # "oh seven hundred", "zero seven thirty": the hour's leading zero.
        tokens, numbers = tokens[1:], numbers[1:]
    if not numbers:
        return None
    joined = " ".join(str(t) for t in tokens)
    # "half past seven", "quarter past seven", "quarter to eight", "ten to eight"
    for pattern, offset in (
        (r"half past (\d+)", 30),
        (r"quarter past (\d+)", 15),
        (r"quarter to (\d+)", -15),
        (r"(\d+) (?:minutes )?past (\d+)", None),
        (r"(\d+) (?:minutes )?to (\d+)", None),
    ):
        found = re.search(pattern, joined)
        if not found:
            continue
        if offset is not None:
            hour, minute = int(found.group(1)), offset
        else:
            amount, hour = int(found.group(1)), int(found.group(2))
            minute = amount if "past" in found.group(0) else -amount
        if minute < 0:
            hour, minute = hour - 1, 60 + minute
        return _valid(hour % 24 if hour >= 0 else 23, minute, meridiem)
    # "7 30", "seven thirty", "7 oh 5", "1930", "7"
    first = numbers[0]
    if first >= 100 and len(numbers) == 1:
        return _valid(first // 100, first % 100, meridiem)
    minute = 0
    rest = numbers[1:]
    if rest and rest[0] == 0 and len(rest) > 1:  # "seven oh five"
        minute = rest[1]
    elif rest:
        minute = rest[0]
    return _valid(first, minute, meridiem)


def _valid(hour: int, minute: int, meridiem: str | None) -> tuple[int, int, str | None] | None:
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    if hour > 12:
        meridiem = None  # 24-hour
    return hour, minute, meridiem
