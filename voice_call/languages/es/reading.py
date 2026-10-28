"""What the app reads in Spanish itself, with English's names (see
voice_call/languages/en/reading.py for what each is)."""

from __future__ import annotations

import re
import unicodedata

# ------------------------------------------------------------------ places

PLACE_BEFORE = ("en", "a", "al", "del", "por")
ARTICLES = ("el", "la", "los", "las")

# ------------------------------------------------------------------- music

MUSIC_GENERIC = {
    "música", "musica", "algo de música", "un poco de música", "algo", "lo que sea", "cualquier cosa", "canciones",
    "unas canciones", "la música", "mi música", "esto", "eso", "esta", "esta canción", "esa canción",
    "la canción", "lo", "algo bonito",
}

MUSIC_CUES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^(?:algo |unas |un poco )?(?:de )?(?:canciones|música|musica|temas|éxitos|algo) de (?P<q>.+)$"), "artist"),
    (re.compile(r"^(?:al |a la |el |la )?(?:artista|grupo|cantante|banda) (?P<q>.+)$"), "artist"),
    (re.compile(r"^(?:el )?(?:álbum|album|disco) (?P<q>.+)$"), "album"),
    (re.compile(r"^(?:la )?(?:canción|cancion|tema) (?P<q>.+)$"), "track"),
    # "la lista de reproducción jazz", "mi lista de jazz", "la playlist de jazz"
    (re.compile(r"^(?:la |mi )?(?:lista de reproducción|lista de reproduccion|lista|playlist)(?: de)? (?P<q>.+)$"), "playlist"),
    (re.compile(r"^(?:la )?(?:emisora|estación de radio|estacion de radio|radio)(?: de)? (?P<q>.+)$"), "radio"),
]

MUSIC_LEADING = re.compile(r"^(?:me |nos |un poco de |algo de )+")
MUSIC_TRAILING = re.compile(r"(?: por favor| para mí| para mi| para nosotros| en music assistant)+$")

BY = "de"
AND = ("y", "e")
"""What joins items said together ("apples and bananas"): a word of its own."""
PLURALS = (("es", ""), ("s", ""))
"""A plural's ending, and the singular's in its place, for telling an item said
from one on a to-do list (actions.same_item): "manzanas" is "manzana",
"limones" "limon". A word has one only when it is longer than the ending by
three letters."""

# ------------------------------------------------------------------- clock

ALARM_EXAMPLE = "las siete y media de la mañana"

_SMALL = {
    "cero": 0, "una": 1, "uno": 1, "un": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7,
    "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15,
    "dieciseis": 16, "diecisiete": 17, "dieciocho": 18, "diecinueve": 19, "veinte": 20, "veintiuno": 21,
    "veintiuna": 21, "veintiun": 21, "veintidos": 22, "veintitres": 23, "veinticuatro": 24, "veinticinco": 25,
    "veintiseis": 26, "veintisiete": 27, "veintiocho": 28, "veintinueve": 29,
}
_TENS = {"treinta": 30, "cuarenta": 40, "cincuenta": 50}
_UNITS = {"uno", "una", "un", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve"}


def _fold(text: str) -> str:
    """Without accents: "dieciséis" -> "dieciseis"."""
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def _numbers(words: list[str]) -> list[str]:
    """Number words as digits ("veinticinco" -> "25", "treinta y cinco" ->
    "35"), other words kept. "Treinta y cinco" is one number -- no hour is
    thirty -- but "veinte y cinco" is 20:05."""
    out: list[str] = []
    index = 0
    while index < len(words):
        word = words[index]
        if word in _TENS:
            value = _TENS[word]
            if index + 2 < len(words) and words[index + 1] == "y" and words[index + 2] in _UNITS:
                value += _SMALL[words[index + 2]]
                index += 2
            out.append(str(value))
        elif word in _SMALL:
            out.append(str(_SMALL[word]))
        else:
            out.append(word)
        index += 1
    return out


# "las ocho menos cuarto" is 7:45, "las ocho menos diez" 7:50, "las siete y
# media" 7:30, "las siete y cuarto" 7:15; "las 19 y 30", "las diecinueve
# horas y treinta minutos", "las siete treinta". First that fits wins.
_SAID = [
    (re.compile(r"(\d+) (?:horas? )?menos cuarto"), lambda h: (h - 1, 45)),
    (re.compile(r"(\d+) (?:horas? )?menos (\d+)"), lambda h, n: (h - 1, 60 - n)),
    (re.compile(r"(\d+) (?:horas? )?y media"), lambda h: (h, 30)),
    (re.compile(r"(\d+) (?:horas? )?y cuarto"), lambda h: (h, 15)),
    (re.compile(r"(\d+) (?:horas? )?(?:y )?(\d+)"), lambda h, m: (h, m)),
    (re.compile(r"(\d+)"), lambda h: (h, 0)),
]


def clock(text: str) -> tuple[int, int, str | None] | None:
    """A time of day said or written: "las siete y media", "las ocho menos
    cuarto", "a las 7", "las diecinueve treinta", "19:30", "mediodía",
    "medianoche"; "am" for "de la mañana" or "de la madrugada", "pm" for
    "de la tarde" or "de la noche", else None (the next time it comes)."""
    text = re.sub(r"(\d{1,2})[:.h](\d{2})\b", r"\1 y \2", _fold(text.lower()))
    words = re.findall(r"[a-zñ]+|\d+", text)
    joined = " ".join(words)
    meridiem = None
    if re.search(r"\b(?:de|por) la (?:tarde|noche)\b|\besta (?:tarde|noche)\b|\bdel mediodia\b", joined):
        meridiem = "pm"
    elif re.search(r"\b(?:de|por) la (?:manana|madrugada)\b|\bmadrugada\b", joined):
        meridiem = "am"
    if "medianoche" in words:
        return 0, 0, "am"
    tokens = _numbers(words)
    numbers = [int(t) for t in tokens if t.isdigit()]
    if not numbers:
        return (12, 0, "pm") if "mediodia" in words else None
    joined = " ".join(tokens)
    for pattern, time in _SAID:
        found = pattern.search(joined)
        if found:
            hour, minute = time(*(int(g) for g in found.groups()))
            hour %= 24
            # "las doce de la noche" is midnight; "las dos de la noche", 2 am.
            if meridiem == "pm" and re.search(r"\bnoche\b", joined) and (hour == 12 or hour < 5):
                hour, meridiem = hour % 12, "am"
            return _valid(hour, minute, meridiem)
    return None


def _valid(hour: int, minute: int, meridiem: str | None) -> tuple[int, int, str | None] | None:
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    if hour > 12:
        meridiem = None  # 24-hour
    return hour, minute, meridiem
