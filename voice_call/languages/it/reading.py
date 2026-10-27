"""What the app reads in Italian itself, with English's names (see
voice_call/languages/en/reading.py for what each is)."""

from __future__ import annotations

import re

# ------------------------------------------------------------------ places

PLACE_BEFORE = ("in", "nel", "nella", "nello", "nell'", "nei", "negli", "nelle", "a", "al", "alla", "allo", "all'",
                "su", "sul", "sulla", "sull'", "da", "per")
ARTICLES = ("il", "lo", "la", "l'", "i", "gli", "le")

# ------------------------------------------------------------------- music

MUSIC_GENERIC = {
    "musica", "la musica", "un po' di musica", "un po di musica", "della musica", "la mia musica", "qualcosa",
    "qualsiasi cosa", "delle canzoni", "qualche canzone", "questo", "questa", "questa canzone", "questo brano",
}

MUSIC_CUES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^(?:qualche |delle |dei )?(?:canzoni|brani|pezzi|musica|qualcosa) (?:di|dei|degli|delle) (?P<q>.+)$"), "artist"),
    (re.compile(r"^(?:l'artista|il gruppo|la band|il cantante|la cantante) (?P<q>.+)$"), "artist"),
    (re.compile(r"^(?:l'|un |il )?(?:album|disco) (?P<q>.+)$"), "album"),
    (re.compile(r"^(?:la |una )?canzone (?P<q>.+)$"), "track"),
    (re.compile(r"^(?:il |un )?(?:brano|pezzo) (?P<q>.+)$"), "track"),
    (re.compile(r"^(?:la |la mia |una )?playlist (?P<q>.+)$"), "playlist"),
    (re.compile(r"^(?:la )?(?:radio|stazione) (?P<q>.+)$"), "radio"),
]

MUSIC_LEADING = re.compile(r"^(?:(?:un )?po'? di |della |del |dei |delle |degli |dello )+")
"""Before what to play: "un po' di", "del" -- or "po' di" alone, its "un" a
filler the free text lost."""
MUSIC_TRAILING = re.compile(r"(?: per favore| per piacere| per me| su music assistant)+$")

BY = "di"
AND = ("e", "ed")
"""What joins items said together ("apples and bananas"): a word of its own."""
"""Between a title and its artist: "kind of blue di miles davis"."""

# ------------------------------------------------------------------- clock

ALARM_EXAMPLE = "le sette e mezza di mattina"

_UNITS = ["zero", "uno", "due", "tre", "quattro", "cinque", "sei", "sette", "otto", "nove"]
_TEENS = ["dieci", "undici", "dodici", "tredici", "quattordici", "quindici", "sedici", "diciassette", "diciotto",
          "diciannove"]
_TENS = {20: "venti", 30: "trenta", 40: "quaranta", 50: "cinquanta"}


def _table() -> dict[str, int]:
    """Number words to 59: "ventuno", "ventotto", "trentatré"."""
    words = {w: n for n, w in enumerate(_UNITS)} | {w: 10 + n for n, w in enumerate(_TEENS)}
    words |= {"una": 1, "tré": 3}
    for value, ten in _TENS.items():
        words[ten] = value
        for unit in range(1, 10):
            stem = ten[:-1] if unit in (1, 8) else ten  # ventuno, ventotto
            words[stem + _UNITS[unit]] = value + unit
        words[ten + "tré"] = value + 3
    return words


_WORDS = _table()


def _numbers(text: str) -> str:
    """Italian number words as digits: "diciannove" -> 19, "trentacinque" ->
    35, "l'una" -> 1."""
    text = re.sub(r"\bl'una\b", "1", text)
    return " ".join(str(_WORDS[w]) if w in _WORDS else w for w in re.findall(r"[a-zàèéìòù']+|\d+", text))


# "Le otto meno un quarto" is 7:45; "le sette e mezza" 7:30.
_SAID = [
    (re.compile(r"(\d+) meno un quarto"), lambda h: (h - 1, 45)),
    (re.compile(r"(\d+) meno (\d+)"), lambda h, n: (h - 1, 60 - n)),
    (re.compile(r"(\d+) e un quarto"), lambda h: (h, 15)),
    (re.compile(r"(\d+) e tre quarti"), lambda h: (h, 45)),
    (re.compile(r"(\d+) e mezz[ao]"), lambda h: (h, 30)),
    (re.compile(r"(\d+) e (\d+)"), lambda h, m: (h, m)),
    (re.compile(r"(\d+) (\d{2})\b"), lambda h, m: (h, m)),
    (re.compile(r"(\d+)"), lambda h: (h, 0)),
]


def clock(text: str) -> tuple[int, int, str | None] | None:
    """A time of day said or written: "le sette e mezza", "le otto meno un
    quarto", "le diciannove e trenta", "19:30", "alle 7 di sera",
    "mezzogiorno"; "pm" in the afternoon or evening, "am" in the morning,
    else None."""
    s = text.lower().replace("’", "'")
    s = re.sub(r"(\d{1,2})\s*[:.]\s*(\d{2})", r"\1 \2", s)
    if "mezzogiorno" in s:
        return 12, 30 if re.search(r"mezzogiorno e mezz[ao]", s) else 0, "pm"
    if "mezzanotte" in s:
        return 0, 30 if re.search(r"mezzanotte e mezz[ao]", s) else 0, "am"
    joined = _numbers(s)
    for pattern, time in _SAID:
        found = pattern.search(joined)
        if found:
            hour, minute = time(*(int(g) for g in found.groups()))
            return _valid(hour % 24, minute, _meridiem(s, hour % 24))
    return None


def _meridiem(said: str, hour: int) -> str | None:
    if re.search(r"\b(?:di|della|nella) sera\b|\b(?:del|di|nel) pomeriggio\b", said):
        return "pm"
    if re.search(r"\b(?:di|della|nella) notte\b", said):
        return "pm" if hour >= 7 else "am"  # "le undici di notte", "le tre di notte"
    if re.search(r"\b(?:di|della|nella) mattina\b|\b(?:del|di|nel) mattino\b", said):
        return "am"
    return None


def _valid(hour: int, minute: int, meridiem: str | None) -> tuple[int, int, str | None] | None:
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    if hour > 12:
        meridiem = None  # 24-hour
    return hour, minute, meridiem
