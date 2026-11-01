"""What the app reads in Ukrainian itself, with English's names (see
voice_labs/languages/en/reading.py for what each is).

Ukrainian has no articles; what may stand between "у" or "на" and a place
is a possessive ("у моїй спальні"). A time of day names its hour by an
ordinal, in whatever case the sentence puts it ("сьома", "на сьому", "о
сьомій", "до сьомої"), or by a cardinal ("на сім тридцять"); half and
quarter hours count towards the next ("пів на восьму" is 7:30, "за чверть
восьма" 7:45)."""

from __future__ import annotations

import re

from .numerals import NUMBER_WORDS as _NUMBER_WORDS
from .numerals import ORDINAL_ENDINGS as _ORDINAL_ENDINGS
from .numerals import ordinal

# ------------------------------------------------------------------ places

PLACE_BEFORE = ("в", "у", "на", "до", "біля")
ARTICLES = ("мій", "моя", "моє", "мою", "моїй", "моєму", "моїм", "наш", "наша", "нашу", "нашій", "нашому")
"""Not articles: possessives, which may stand where they would ("у моїй
спальні")."""

# ------------------------------------------------------------------- music

MUSIC_GENERIC = {
    "музика", "музику", "музики", "якусь музику", "трохи музики", "щось", "що-небудь", "що небудь", "що завгодно",
    "будь-що", "щось гарне", "пісні", "якісь пісні", "пісню", "цю пісню", "цю", "це", "її", "його", "мою музику",
    "музику далі",
}

MUSIC_CUES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^(?:щось |музику |пісні |треки |композиції )?(?:від|гурту|групи|виконавця|виконавиці|співака|співачки) (?P<q>.+)$"),
     "artist"),
    (re.compile(r"^(?:виконавця|виконавицю|гурт|групу|артиста|артистку|співака|співачку) (?P<q>.+)$"), "artist"),
    (re.compile(r"^(?:музику|пісні|треки) (?P<q>.+)$"), "artist"),
    (re.compile(r"^(?:альбом|платівку) (?P<q>.+)$"), "album"),
    (re.compile(r"^(?:пісню|трек|композицію|сингл) (?P<q>.+)$"), "track"),
    (re.compile(r"^(?:мій |мою )?(?:плейлист|список відтворення|добірку|підбірку) (?P<q>.+)$"), "playlist"),
    (re.compile(r"^(?:радіостанцію|радіо|станцію|хвилю) (?P<q>.+)$"), "radio"),
]

MUSIC_LEADING = re.compile(r"^(?:мені |нам |трохи |якусь |якийсь |якісь |будь ласка )+")
MUSIC_TRAILING = re.compile(r"(?: будь ласка| мені| для мене| для нас| (?:у|в) music assistant)+$")

BY = "від"
"""Between a title and its artist, as said: "so what від miles davis"."""
AND = ("і", "й", "та")
"""What joins items said together ("apples and bananas"): a word of its own."""
PLURALS = (("и", ""), ("і", ""), ("и", "а"), ("і", "а"), ("і", "я"), ("а", "о"))
"""A plural's ending, and the singular's in its place, for telling an item said
from one on a to-do list (actions.same_item): "банани" is "банан", "груші"
"груша", "яблука" "яблуко". A word has one only when it is longer than the
ending by three letters."""

# ----------------------------------------------------------------- answers
# What the app asked -- the conversation agent's question, a command's
# yes-or-no one -- answered (voice_labs/follow_up.py).

ANSWER_ENDS = {
    "скасуй", "скасувати", "скасування", "неважливо", "забудь", "стоп", "досить", "нічого",
    "ні дякую", "це все", "більше нічого", "бувай", "до побачення",
}
"""An answer that is only one of these ends the conversation instead of
going on with it: "Щось іще?" -- "ні дякую". Lowercase, without punctuation."""

ANSWER_YES = {
    "так", "авжеж", "звісно", "звичайно", "давай", "гаразд", "добре", "окей", "ок", "точно",
    "вірно", "зроби це", "будь ласка", "так будь ласка",
}
"""Yes to a command's question (``confirm``): an answer with one of these in
it, and none of ANSWER_NO."""

ANSWER_NO = {
    "ні", "не", "не треба", "ніколи", "зачекай", "почекай", "залиш", "залиш їх", "але", "крім",
}
"""No to it: an answer with one of these in it (or that only ends it), and
none of ANSWER_YES -- with both, it isn't clear."""

YES_SAID = (
    "так", "авжеж", "звісно", "звичайно", "давай", "гаразд", "добре", "точно", "зроби це",
)
NO_SAID = (
    "ні", "не треба", "ніколи", "не зараз", "зачекай", "почекай", "залиш їх",
)
"""Yes and no said whole, without words that only doubt it ("but", "not"):
what an answer with neither, or both, is told apart by, by meaning."""

# --------------------------------------------------------------- pronouns
# Said in place of what was last spoken of (voice_labs/context.py).

PRONOUNS = {"його", "її", "їх", "це"}
"""Each, said where a device's name would be ("вимкни його"), stands for the
devices last spoken of: the sentence is heard again with their names in its
place. Lowercase; a word of its own (one joined to its verb isn't)."""

# ------------------------------------------------------------------- clock

ALARM_EXAMPLE = "сьому тридцять ранку"


def _ordinals() -> dict[str, int]:
    """An hour's ordinal in any case and gender -> the hour: "сьома",
    "сьому", "сьомій", "сьомої", "сьомого", "двадцять першій"."""
    table: dict[str, int] = {}
    for hour in range(1, 25):
        for form in _ORDINAL_ENDINGS:
            table.setdefault(ordinal(hour, form), hour)
    for form in _ORDINAL_ENDINGS:
        table.setdefault(ordinal(0, form), 0)
    return table


_ORDINALS = _ordinals()
_ORDINAL_WORDS = {w: v for w, v in _ORDINALS.items() if " " not in w}
_MINUTE_WORDS = {"хвилин", "хвилина", "хвилини", "хвилину", "хв"}
_HOUR_WORDS = {"година", "години", "годині", "годину", "годин", "год"}


def _tokens(words: list[str]) -> list[tuple[str, int | str]]:
    """Words as ("o", hour) for an ordinal, ("n", value) for a cardinal or
    digits, ("w", word) else: "двадцять першій" is one ordinal, "сорок п'ять"
    one cardinal."""
    out: list[tuple[str, int | str]] = []
    index = 0
    while index < len(words):
        word = words[index]
        following = words[index + 1] if index + 1 < len(words) else ""
        hour = _hour(word)
        if word.isdigit():
            out.append(("n", int(word)))
        elif hour is not None:
            out.append(("o", hour))
        elif word in _NUMBER_WORDS:
            value = _NUMBER_WORDS[word]
            if value in (20, 30, 40, 50) and following in _ORDINAL_WORDS and 1 <= _ORDINAL_WORDS[following] <= 9:
                out.append(("o", value + _ORDINAL_WORDS[following]))
                index += 1
            elif value in (20, 30, 40, 50) and following in _NUMBER_WORDS and 1 <= _NUMBER_WORDS[following] <= 9:
                out.append(("n", value + _NUMBER_WORDS[following]))
                index += 1
            else:
                out.append(("n", value))
        else:
            out.append(("w", word))
        index += 1
    return out


def _hour(word: str) -> int | None:
    """An hour's ordinal, also as a transcript may write it: joined to "о"
    ("осьомій"), or without its last "й" ("сьомі")."""
    for candidate in (word, word + "й") if word.endswith("і") else (word,):
        for joined in ("", "об", "о"):
            if candidate.startswith(joined) and candidate[len(joined) :] in _ORDINAL_WORDS:
                return _ORDINAL_WORDS[candidate[len(joined) :]]
    return None


def clock(text: str) -> tuple[int, int, str | None] | None:
    """A time of day said or written: "сьома тридцять", "на сьому", "о
    сьомій ранку", "пів на восьму", "за чверть восьма", "без п'яти вісім",
    "десять хвилин на восьму", "19:30", "опівночі", "полудень"; "am" for
    "ранку" or "ночі", "pm" for "дня" or "вечора", else None (the next time
    it comes)."""
    text = text.lower().replace("’", "'").replace("ʼ", "'")
    text = re.sub(r"(\d{1,2})[:.h](\d{2})\b", r"\1 \2", text)
    # "напів на восьму", "опів на восьму": "пів на восьму".
    words = ["пів" if w in ("напів", "опів") else w for w in re.findall(r"[а-яіїєґ']+|\d+", text)]
    meridiem = None
    if any(w in ("дня", "вдень", "пополудні", "пообіді", "вечора", "увечері", "ввечері", "вечір") for w in words) \
            or "після обіду" in " ".join(words):
        meridiem = "pm"
    elif any(w in ("ранку", "вранці", "зранку", "ранок", "ночі", "вночі", "уночі") for w in words):
        meridiem = "am"
    if any(w in ("опівночі", "північ", "півночі") for w in words):
        return 0, 0, "am"
    if any(w in ("опівдні", "полудень", "полудня", "південь") for w in words) and not any(
        w.isdigit() or w in _ORDINAL_WORDS for w in words
    ):
        return 12, 0, "pm"
    tokens = [t for t in _tokens(words) if t[1] not in _HOUR_WORDS]
    numbers = [t for t in tokens if t[0] in ("n", "o")]
    if not numbers:
        return None
    said = [str(t[1]) if t[0] == "w" else f"{t[0]}{t[1]}" for t in tokens]
    joined = " ".join(said)
    minutes = r"(?: (?:хвилин|хвилина|хвилини|хвилину|хв))?"
    patterns = [
        # "пів на восьму", "пів восьмої": 7:30.
        (r"\bпів (?:на )?[on](\d+)", lambda h: (h - 1, 30)),
        (r"\b(?:за|без) (?:чверть|чверті) [on](\d+)", lambda h: (h - 1, 45)),
        (r"\bчверть (?:на )?[on](\d+)", lambda h: (h - 1, 15)),
        (rf"\b(?:за|без) n(\d+){minutes} [on](\d+)", lambda n, h: (h - 1, 60 - n)),
        # "десять хвилин на восьму": 7:10.
        (rf"\bn(\d+){minutes} на o(\d+)", lambda n, h: (h - 1, n)),
        # "сьома тридцять", "о сьомій нуль п'ять", "сім годин тридцять хвилин".
        (r"[on](\d+) n0 n(\d)\b", lambda h, m: (h, m)),
        (rf"[on](\d+) n(\d+){minutes}", lambda h, m: (h, m)),
        (r"[on](\d+)", lambda h: (h, 0)),
    ]
    for pattern, time in patterns:
        found = re.search(pattern, joined)
        if found:
            hour, minute = time(*(int(g) for g in found.groups()))
            if hour > 24:
                return None  # "тридцять" alone is no hour
            hour %= 24
            # "дванадцята ночі" is midnight; "дванадцята дня" noon.
            if hour == 12 and meridiem == "am" and any(w in ("ночі", "вночі", "уночі") for w in words):
                hour = 0
            return _valid(hour, minute, meridiem)
    return None


def _valid(hour: int, minute: int, meridiem: str | None) -> tuple[int, int, str | None] | None:
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    if hour > 12:
        meridiem = None  # 24-hour
    return hour, minute, meridiem
