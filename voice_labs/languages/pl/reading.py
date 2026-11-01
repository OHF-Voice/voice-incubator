"""What the app reads in Polish itself, with English's names (see
voice_labs/languages/en/reading.py for what each is).

Polish has no articles: what comes between a preposition and a place is a
possessive ("w moim pokoju"). A time of day is said by its hour's ordinal,
in the case its preposition takes ("o siódmej", "na siódmą", "siódma
trzydzieści"), with a half and quarters before the next hour ("wpół do
ósmej" is 7:30, "za kwadrans ósma" 7:45); read folded, so typed without
diacritics too."""

from __future__ import annotations

import re

from .skills.numbers import fold, parse_num, read_ordinal

# ------------------------------------------------------------------ places

PLACE_BEFORE = ("w", "we", "na", "do", "przy", "u")
ARTICLES = ("mój", "moja", "moje", "moją", "mojej", "moim", "mojego")

# ------------------------------------------------------------------- music

MUSIC_GENERIC = {
    "muzyka", "muzykę", "muzyki", "muzyczkę", "jakąś muzykę", "trochę muzyki", "coś", "cokolwiek", "coś fajnego",
    "piosenki", "jakieś piosenki", "moją muzykę", "to", "tę piosenkę", "tą piosenkę", "ten utwór", "tę", "ją", "go",
    "dalej", "coś do posłuchania",
}

MUSIC_CUES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^(?:coś |trochę |jakieś )?(?:piosenki|piosenek|muzykę|muzyki|utwory|utworów|kawałki|coś)"
                r" (?:od|zespołu|grupy|artysty|wykonawcy) (?P<q>.+)$"), "artist"),
    (re.compile(r"^(?:artystę|artysty|wykonawcę|wykonawcy|zespół|zespołu|grupę|grupy|kapelę) (?P<q>.+)$"), "artist"),
    (re.compile(r"^(?:album|albumu|płytę|płyty|krążek) (?P<q>.+)$"), "album"),
    (re.compile(r"^(?:piosenkę|piosenki|utwór|utworu|kawałek|singiel) (?P<q>.+)$"), "track"),
    # "playlistę jazz", "moją playlistę z jazzem", "listę odtwarzania jazz"
    (re.compile(r"^(?:moją |mojej )?(?:playlistę|playlisty|playlista|listę odtwarzania|listy odtwarzania)(?: z)? (?P<q>.+)$"),
     "playlist"),
    (re.compile(r"^(?:radio|stację|stacji|stację radiową|rozgłośnię) (?P<q>.+)$"), "radio"),
]

MUSIC_LEADING = re.compile(r"^(?:mi |nam |trochę |jakąś |jakieś |coś z )+")
MUSIC_TRAILING = re.compile(r"(?: proszę| dla mnie| dla nas| na music assistant| w music assistant)+$")

BY = "od"
AND = ("i", "oraz")
"""What joins items said together ("apples and bananas"): a word of its own."""
PLURALS = (("y", ""), ("i", ""), ("i", "a"), ("y", "a"), ("a", "o"))
"""A plural's ending, and the singular's in its place, for telling an item said
from one on a to-do list (actions.same_item): "banany" is "banan", "gruszki"
"gruszka", "jabłka" "jabłko". A word has one only when it is longer than the
ending by three letters."""

# ----------------------------------------------------------------- answers
# What the app asked -- the conversation agent's question, a command's
# yes-or-no one -- answered (voice_labs/follow_up.py).

ANSWER_ENDS = {
    "anuluj", "nieważne", "niewazne", "zapomnij", "stop", "przestań", "nic", "nie dziękuję",
    "nie dzięki", "to wszystko", "nic więcej", "koniec", "do widzenia",
}
"""An answer that is only one of these ends the conversation instead of
going on with it: "Coś jeszcze?" -- "nie dziękuję". Lowercase, without punctuation."""

ANSWER_YES = {
    "tak", "jasne", "pewnie", "okej", "ok", "dobrze", "zrób to", "śmiało", "oczywiście",
    "zgadza się", "proszę", "tak proszę", "dawaj",
}
"""Yes to a command's question (``confirm``): an answer with one of these in
it, and none of ANSWER_NO."""

ANSWER_NO = {
    "nie", "nigdy", "czekaj", "poczekaj", "zostaw", "zostaw je", "ale", "oprócz", "poza",
}
"""No to it: an answer with one of these in it (or that only ends it), and
none of ANSWER_YES -- with both, it isn't clear."""

YES_SAID = (
    "tak", "jasne", "pewnie", "dobrze", "zrób to", "śmiało", "oczywiście", "zgadza się",
)
NO_SAID = (
    "nie", "nigdy", "nie teraz", "czekaj", "poczekaj", "zostaw je",
)
"""Yes and no said whole, without words that only doubt it ("but", "not"):
what an answer with neither, or both, is told apart by, by meaning."""

# --------------------------------------------------------------- pronouns
# Said in place of what was last spoken of (voice_labs/context.py).

PRONOUNS = {"go", "ją", "je", "to", "ich"}
"""Each, said where a device's name would be ("zamknij je"), stands for the
devices last spoken of: the sentence is heard again with their names in its
place. Lowercase; a word of its own (one joined to its verb isn't)."""

# ------------------------------------------------------------------- clock

ALARM_EXAMPLE = "siódma trzydzieści rano"

def _numbers(words: list[str]) -> list[str]:
    """Hours said as ordinals ("siodmej", "dwudziestej pierwszej") and
    minutes as numbers ("czterdziesci piec") as digits; ordinals marked with
    "h" ("h7"), other words kept."""
    out: list[str] = []
    index = 0
    while index < len(words):
        for size in (2, 1):
            said = " ".join(words[index:index + size])
            if len(said.split()) < size:
                continue
            hour = read_ordinal(said)
            if hour is not None and hour <= 24:
                out.append(f"h{hour}")
                index += size
                break
            value = parse_num(said) if not said.isdigit() else None
            # Two words are one number only as tens and a unit: "czterdziesci piec".
            tens = parse_num(words[index]) if size == 2 else None
            if size == 2 and not (tens in (20, 30, 40, 50) and value is not None and 0 < value - tens < 10):
                value = None
            if value is not None and value == int(value):
                out.append(str(int(value)))
                index += size
                break
        else:
            out.append(words[index])
            index += 1
    return out


# "wpół do ósmej" is 7:30; "za kwadrans ósma" 7:45, "za dziesięć ósma"
# 7:50; "kwadrans po siódmej" 7:15, "dziesięć po siódmej" 7:10; "siódma
# trzydzieści", "siódma zero pięć", "19 30". First that fits wins.
_SAID = [
    (re.compile(r"w?pol do h(\d+)"), lambda h: (h - 1, 30)),
    (re.compile(r"za kwadrans h(\d+)"), lambda h: (h - 1, 45)),
    (re.compile(r"za (\d+) (?:minut\w* )?h(\d+)"), lambda n, h: (h - 1, 60 - n)),
    (re.compile(r"kwadrans po h(\d+)"), lambda h: (h, 15)),
    (re.compile(r"(\d+) (?:minut\w* )?po h(\d+)"), lambda n, h: (h, n)),
    (re.compile(r"h?(\d+) 0 (\d)\b"), lambda h, m: (h, m)),
    (re.compile(r"h?(\d+) (?:godzin\w* )?(\d+)"), lambda h, m: (h, m)),
    (re.compile(r"h?(\d+)"), lambda h: (h, 0)),
]


def clock(text: str) -> tuple[int, int, str | None] | None:
    """A time of day said or written: "siódma trzydzieści", "o siódmej",
    "na siódmą", "wpół do ósmej", "za kwadrans ósma", "dziewiętnasta
    trzydzieści", "7:30", "w południe", "o północy"; "am" for "rano", "pm"
    for "po południu" or "wieczorem" ("w nocy": small hours am, late ones
    pm), else None (the next time it comes)."""
    text = re.sub(r"(\d{1,2})[:.h](\d{2})\b", r"\1 \2", fold(text.lower()))
    words = re.findall(r"[a-z]+|\d+", text)
    joined = " ".join(words)
    meridiem = None
    if re.search(r"\b(?:po poludniu|popoludniu|wieczorem|wieczor|wieczora)\b", joined):
        meridiem = "pm"
    elif re.search(r"\b(?:rano|z rana|nad ranem|przed poludniem|rana)\b", joined):
        meridiem = "am"
    night = bool(re.search(r"\bw nocy\b", joined))
    tokens = _numbers(words)
    hours = [t for t in tokens if re.fullmatch(r"h?\d+", t)]
    if not hours:
        if re.search(r"\bpolnoc\w*\b", joined):
            return 0, 0, "am"
        if re.search(r"\bpoludni\w*\b", joined) and not re.search(r"\bpo poludniu\b", joined):
            return 12, 0, "pm"
        return None
    joined = " ".join(tokens)
    for pattern, time in _SAID:
        found = pattern.search(joined)
        if found:
            hour, minute = time(*(int(g) for g in found.groups()))
            hour %= 24
            if night:
                # "jedenasta w nocy" is 23:00; "druga w nocy", 2:00.
                meridiem = "pm" if 6 <= hour < 12 else "am"
            return _valid(hour, minute, meridiem)
    return None


def _valid(hour: int, minute: int, meridiem: str | None) -> tuple[int, int, str | None] | None:
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    if hour > 12:
        meridiem = None  # 24-hour
    return hour, minute, meridiem
