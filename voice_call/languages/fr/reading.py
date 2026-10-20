"""What the app reads in French itself, with English's names (see
voice_call/languages/en/reading.py for what each is)."""

from __future__ import annotations

import re

# ------------------------------------------------------------------ places

PLACE_BEFORE = ("dans", "à", "au", "aux", "en", "sur", "chez")
ARTICLES = ("le", "la", "les", "l'", "du", "de", "des")

# ------------------------------------------------------------------- music

MUSIC_GENERIC = {
    "de la musique", "musique", "la musique", "ma musique", "quelque chose", "n'importe quoi", "des chansons",
    "ça", "ceci", "cela", "cette chanson", "ce morceau", "un truc",
}

MUSIC_CUES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^(?:des |quelques )?(?:chansons|morceaux|titres|musique|quelque chose) (?:de|du|des|d') ?(?P<q>.+)$"), "artist"),
    (re.compile(r"^(?:l'artiste|le groupe|l'interprète|le chanteur|la chanteuse) (?P<q>.+)$"), "artist"),
    (re.compile(r"^(?:l'|un )?album (?P<q>.+)$"), "album"),
    (re.compile(r"^(?:la |le )?(?:chanson|morceau|titre) (?P<q>.+)$"), "track"),
    (re.compile(r"^(?:la |ma )?(?:playlist|liste de lecture) (?P<q>.+)$"), "playlist"),
    (re.compile(r"^(?:la )?(?:radio|station) (?P<q>.+)$"), "radio"),
]

MUSIC_LEADING = re.compile(r"^(?:un peu de |de la |du |des |un peu d')+")
MUSIC_TRAILING = re.compile(r"(?: s'il te plaît| s'il vous plaît| pour moi| sur music assistant)+$")

BY = "par"
"""Between a title and its artist: "so what par miles davis" ("de" is too
common to split on)."""

# ------------------------------------------------------------------- clock

ALARM_EXAMPLE = "sept heures et demie du matin"

_SMALL = {
    "zéro": 0, "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6, "sept": 7, "huit": 8,
    "neuf": 9, "dix": 10, "onze": 11, "douze": 12, "treize": 13, "quatorze": 14, "quinze": 15, "seize": 16,
    "vingt": 20, "trente": 30, "quarante": 40, "cinquante": 50,
}


def _numbers(text: str) -> str:
    """French number words as digits: "dix-sept" -> 17, "vingt et un" -> 21,
    "trente-cinq" -> 35."""
    words = re.findall(r"[a-zéèêàâîïôûç]+|\d+", text.replace("-", " "))
    out: list[str] = []
    index = 0
    while index < len(words):
        word = words[index]
        if word in _SMALL:
            value = _SMALL[word]
            rest = words[index + 1:index + 3]
            if value >= 20 and rest[:1] == ["et"] and len(rest) > 1 and _SMALL.get(rest[1]) == 1:
                value, index = value + 1, index + 2
            elif (value >= 20 and rest[:1] and 0 < _SMALL.get(rest[0], 0) < 10) or (
                value == 10 and rest[:1] and rest[0] in ("sept", "huit", "neuf")
            ):
                value, index = value + _SMALL[rest[0]], index + 1
            out.append(str(value))
        else:
            out.append(word)
        index += 1
    return " ".join(out)


# "huit heures moins le quart" is 7:45; "sept heures et demie" 7:30.
_SAID = [
    (re.compile(r"(\d+) heures? moins le quart"), lambda h: (h - 1, 45)),
    (re.compile(r"(\d+) heures? moins (\d+)"), lambda h, n: (h - 1, 60 - n)),
    (re.compile(r"(\d+) heures? et quart"), lambda h: (h, 15)),
    (re.compile(r"(\d+) heures? et demie"), lambda h: (h, 30)),
    (re.compile(r"(\d+) heures? (\d+)"), lambda h, m: (h, m)),
    (re.compile(r"(\d+) heures?"), lambda h: (h, 0)),
    (re.compile(r"(\d+) h (\d+)"), lambda h, m: (h, m)),
    (re.compile(r"(\d+) h\b"), lambda h: (h, 0)),
]


def clock(text: str) -> tuple[int, int, str | None] | None:
    """A time of day said or written: "sept heures et demie", "huit heures
    moins le quart", "dix-neuf heures trente", "19h30", "à 7 heures du soir",
    "midi"; "pm" in the afternoon or evening, "am" in the morning, else None."""
    s = text.lower().replace("’", "'")
    s = re.sub(r"(\d{1,2})\s*[h:]\s*(\d{2})", r"\1 h \2", s)
    s = re.sub(r"(\d{1,2})\s*h\b", r"\1 h", s)
    words = re.findall(r"[a-zéèêàâîïôûç']+|\d+", s)
    meridiem = None
    if any(w in ("soir", "l'après-midi", "après-midi", "nuit") for w in words) or "de l'après" in s:
        meridiem = "pm"
    elif any(w in ("matin",) for w in words):
        meridiem = "am"
    if "après" not in s and "midi" in words:
        return 12, 30 if "midi et demi" in s else 0, "pm"
    if "minuit" in words:
        return 0, 30 if "minuit et demi" in s else 0, "am"
    joined = _numbers(s)
    for pattern, time in _SAID:
        found = pattern.search(joined)
        if found:
            hour, minute = time(*(int(g) for g in found.groups()))
            return _valid(hour % 24, minute, meridiem)
    numbers = [int(t) for t in joined.split() if t.isdigit()]
    if not numbers:
        return None
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
