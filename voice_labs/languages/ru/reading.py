"""What the app reads in Russian itself, with English's names (see
voice_labs/languages/en/reading.py for what each is).

Russian has no articles: what may stand between a preposition and a place is
a possessive or "этот" ("в моей спальне"). A time of day is said on either
clock -- "в семь тридцать", "в 19:30", "в семь вечера" -- or by the hour it
leads to: "половина восьмого" (7:30), "четверть восьмого" (7:15), "без
четверти восемь" (7:45), "десять минут восьмого" (7:10)."""

from __future__ import annotations

import re

# ------------------------------------------------------------------ places

PLACE_BEFORE = ("в", "во", "на", "у", "к", "ко", "при")
ARTICLES = ("мой", "моя", "мою", "мое", "моё", "мои", "моей", "моем", "моём", "моих", "этой", "этом", "этот", "эту")

# ------------------------------------------------------------------- music

MUSIC_GENERIC = {
    "музыку", "музыка", "музыки", "какую-нибудь музыку", "какую нибудь музыку", "что-нибудь", "что нибудь",
    "что-то", "что то", "что угодно", "песни", "песню", "какую-нибудь песню", "какую нибудь песню", "мою музыку",
    "это", "эту", "эту песню", "ее", "её", "его", "что-нибудь хорошее", "что нибудь хорошее", "немного музыки",
    "музыку дальше",
}

MUSIC_CUES: list[tuple[re.Pattern, str]] = [
    # "песни группы Кино", "музыку Цоя" is a name in the genitive: left whole.
    (re.compile(r"^(?:какие-нибудь |какие нибудь |что-нибудь |что нибудь |что-то |что то )?(?:песни|песню|музыку|треки|хиты)"
                r" (?:группы|исполнителя|исполнительницы|певца|певицы|артиста|от) (?P<q>.+)$"), "artist"),
    (re.compile(r"^(?:что-нибудь |что нибудь |что-то |что то )(?:из|от|у) (?P<q>.+)$"), "artist"),
    (re.compile(r"^(?:группу|исполнителя|исполнительницу|певца|певицу|артиста) (?P<q>.+)$"), "artist"),
    (re.compile(r"^(?:альбом|пластинку) (?P<q>.+)$"), "album"),
    (re.compile(r"^(?:песню|трек|композицию|сингл) (?P<q>.+)$"), "track"),
    (re.compile(r"^(?:мой |свой )?(?:плейлист|плей-лист|плей лист|подборку)(?: под названием)? (?P<q>.+)$"), "playlist"),
    (re.compile(r"^(?:радиостанцию|радио|станцию)(?: под названием)? (?P<q>.+)$"), "radio"),
]

MUSIC_LEADING = re.compile(r"^(?:мне |нам |пожалуйста |немного |-?ка )+")
MUSIC_TRAILING = re.compile(r"(?: пожалуйста| для меня| для нас| в music assistant)+$")

BY = "в исполнении"
"""Between a title and its artist: "кукушка в исполнении кино"."""
AND = ("и",)
"""What joins items said together ("apples and bananas"): a word of its own."""
PLURALS = (("ы", ""), ("и", ""), ("и", "а"), ("ы", "а"), ("и", "о"))
"""A plural's ending, and the singular's in its place, for telling an item said
from one on a to-do list (actions.same_item): "бананы" is "банан", "груши"
"груша", "яблоки" "яблоко". A word has one only when it is longer than the
ending by three letters."""

# ----------------------------------------------------------------- answers
# What the app asked -- the conversation agent's question, a command's
# yes-or-no one -- answered (voice_labs/follow_up.py).

ANSWER_ENDS = {
    "отмена", "отменить", "отмени", "неважно", "забудь", "стоп", "хватит", "ничего", "нет спасибо",
    "это всё", "это все", "больше ничего", "пока",
}
"""An answer that is only one of these ends the conversation instead of
going on with it: "Что-нибудь ещё?" -- "нет спасибо". Lowercase, without punctuation."""

ANSWER_YES = {
    "да", "ага", "конечно", "давай", "ладно", "хорошо", "окей", "ок", "точно", "верно", "делай",
    "сделай это", "пожалуйста", "да пожалуйста",
}
"""Yes to a command's question (``confirm``): an answer with one of these in
it, and none of ANSWER_NO."""

ANSWER_NO = {
    "нет", "не", "не надо", "никогда", "подожди", "погоди", "оставь", "оставь их", "но", "кроме",
}
"""No to it: an answer with one of these in it (or that only ends it), and
none of ANSWER_YES -- with both, it isn't clear."""

YES_SAID = (
    "да", "конечно", "давай", "ладно", "хорошо", "точно", "верно", "сделай это",
)
NO_SAID = (
    "нет", "не надо", "никогда", "не сейчас", "подожди", "погоди", "оставь их",
)
"""Yes and no said whole, without words that only doubt it ("but", "not"):
what an answer with neither, or both, is told apart by, by meaning."""

# --------------------------------------------------------------- pronouns
# Said in place of what was last spoken of (voice_labs/context.py).

PRONOUNS = {"его", "её", "ее", "их", "это"}
"""Each, said where a device's name would be ("закрой её"), stands for the
devices last spoken of: the sentence is heard again with their names in its
place. Lowercase; a word of its own (one joined to its verb isn't)."""

# ------------------------------------------------------------------- clock

ALARM_EXAMPLE = "семь тридцать утра"

_ONES = {
    "ноль": 0, "нуль": 0, "один": 1, "одна": 1, "одну": 1, "одного": 1, "одной": 1, "одному": 1, "два": 2, "две": 2,
    "двух": 2, "двум": 2, "три": 3, "трех": 3, "трем": 3, "четыре": 4, "четырех": 4, "четырем": 4, "пять": 5,
    "пяти": 5, "шесть": 6, "шести": 6, "семь": 7, "семи": 7, "восемь": 8, "восьми": 8, "девять": 9, "девяти": 9,
    "десять": 10, "десяти": 10, "одиннадцать": 11, "одиннадцати": 11, "двенадцать": 12, "двенадцати": 12,
    "тринадцать": 13, "тринадцати": 13, "четырнадцать": 14, "четырнадцати": 14, "пятнадцать": 15, "пятнадцати": 15,
    "шестнадцать": 16, "шестнадцати": 16, "семнадцать": 17, "семнадцати": 17, "восемнадцать": 18,
    "восемнадцати": 18, "девятнадцать": 19, "девятнадцати": 19,
}
_TENS = {"двадцать": 20, "двадцати": 20, "тридцать": 30, "тридцати": 30, "сорок": 40, "сорока": 40,
         "пятьдесят": 50, "пятидесяти": 50}
_LEADS_TO = {
    "первого": 1, "второго": 2, "третьего": 3, "четвертого": 4, "пятого": 5, "шестого": 6, "седьмого": 7,
    "восьмого": 8, "девятого": 9, "десятого": 10, "одиннадцатого": 11, "двенадцатого": 12,
}
"""The hour a time leads to, as "половина восьмого" says it (7:30)."""


def _numbers(words: list[str]) -> list[str]:
    """Number words as digits ("двадцать пять" -> "25", "семи" -> "7"), an
    hour led to as "h<hour>" ("восьмого" -> "h8"), other words kept. Tens
    and a unit are one number ("двадцать пять"), but "семь тридцать" two."""
    out: list[str] = []
    index = 0
    while index < len(words):
        word = words[index]
        if word in _TENS:
            value = _TENS[word]
            following = words[index + 1] if index + 1 < len(words) else ""
            if following in _ONES and 0 < _ONES[following] < 10:
                value += _ONES[following]
                index += 1
            out.append(str(value))
        elif word in _ONES:
            out.append(str(_ONES[word]))
        elif word in _LEADS_TO:
            out.append(f"h{_LEADS_TO[word]}")
        elif word.startswith("пол") and word[3:] in _LEADS_TO:  # "полвосьмого"
            out += ["половина", f"h{_LEADS_TO[word[3:]]}"]
        else:
            out.append(word)
        index += 1
    return out


# What a time said comes to; the first that fits wins. "h8" is the hour a
# time leads to ("восьмого").
_SAID = [
    (re.compile(r"\b(?:половин[аеуы]?|пол) h(\d+)"), lambda h: (h - 1, 30)),
    (re.compile(r"\bчетверть h(\d+)"), lambda h: (h - 1, 15)),
    (re.compile(r"\bбез четверти (\d+)"), lambda h: (h - 1, 45)),
    (re.compile(r"\bбез (\d+) (?:минут[ы]? )?(\d+)"), lambda m, h: (h - 1, 60 - m)),
    (re.compile(r"(\d+) минут[ыа]? h(\d+)"), lambda m, h: (h - 1, m)),
    (re.compile(r"(\d+) (?:час[аов]* )?(?:0 )?(\d+)(?: минут[ыа]?)?"), lambda h, m: (h, m)),
    (re.compile(r"(\d+)"), lambda h: (h, 0)),
]


def clock(text: str) -> tuple[int, int, str | None] | None:
    """A time of day said or written: "семь тридцать", "в 7:30", "половина
    восьмого", "без четверти восемь", "в семь вечера", "в час дня",
    "полночь", "полдень"; "am" for "утра" or "ночи", "pm" for "дня" or
    "вечера", else None (the next time it comes)."""
    text = text.lower().replace("ё", "е")
    text = re.sub(r"(\d{1,2})[:.](\d{2})\b", r"\1 \2", text)
    words = re.findall(r"[а-я]+|\d+", text)
    meridiem = None
    if any(w in ("вечера", "вечером", "дня", "днем", "вечер") for w in words):
        meridiem = "pm"
    elif any(w in ("утра", "утром", "ночи", "ночью") for w in words):
        meridiem = "am"
    if any(w in ("полночь", "полуночи", "полночи") for w in words):
        return 0, 0, "am"
    if any(w in ("полдень", "полудня", "полдня") for w in words):
        return 12, 0, "pm"
    tokens = _numbers(words)
    # "В час", "в час дня": one o'clock, said without its number.
    if "час" in tokens and not any(t.isdigit() for t in tokens[: tokens.index("час")]):
        tokens[tokens.index("час")] = "1"
    numbers = [t for t in tokens if t.isdigit() or t.startswith("h")]
    if not numbers:
        return None
    joined = " ".join(tokens)
    for pattern, time in _SAID:
        found = pattern.search(joined)
        if found:
            hour, minute = time(*(int(g) for g in found.groups()))
            if hour == 0 and "h" in found.group(0):  # "половина первого": 12:30
                hour = 12
            # "Двенадцать ночи" is midnight; "двенадцать дня", noon.
            if hour == 12 and meridiem == "am" and "ночи" in words:
                hour = 0
            return _valid(hour, minute, meridiem)
    return None


def _valid(hour: int, minute: int, meridiem: str | None) -> tuple[int, int, str | None] | None:
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    if hour > 12:
        meridiem = None  # 24-hour
    return hour, minute, meridiem
