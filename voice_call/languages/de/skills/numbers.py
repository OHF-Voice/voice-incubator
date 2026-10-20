"""How the German skills read numbers and questions, with English's names
(voice_call/languages/en/skills/numbers.py).

German writes a number in words as one ("dreihundertfünfundvierzig",
"zweitausendvierundzwanzig"), split here into its parts; a decimal comma
("3,5", "drei Komma fünf") and a thousands point ("1.000"); and halves into
words ("dreieinhalb", "anderthalb")."""

from __future__ import annotations

import re

DECIMAL = ","
"""8,047."""

THOUSANDS = ""
"""31536000: said whole, as German writes no separator a voice reads."""

_ONES = {
    "null": 0, "eins": 1, "ein": 1, "eine": 1, "einen": 1, "einem": 1, "einer": 1, "zwei": 2, "zwo": 2,
    "drei": 3, "vier": 4, "fünf": 5, "sechs": 6, "sieben": 7, "acht": 8, "neun": 9, "zehn": 10, "elf": 11,
    "zwölf": 12, "dreizehn": 13, "vierzehn": 14, "fünfzehn": 15, "sechzehn": 16, "siebzehn": 17,
    "achtzehn": 18, "neunzehn": 19, "zwanzig": 20, "dreißig": 30, "dreissig": 30, "vierzig": 40,
    "fünfzig": 50, "sechzig": 60, "siebzig": 70, "achtzig": 80, "neunzig": 90,
}
_SCALES = {"hundert": 100, "tausend": 1000, "million": 10**6, "millionen": 10**6, "milliarde": 10**9, "milliarden": 10**9}
_PARTS = sorted([*_ONES, *_SCALES, "und"], key=len, reverse=True)

FRACTIONS = {
    "halb": 0.5, "ein halb": 0.5, "einhalb": 0.5, "eine halbe": 0.5, "halbe": 0.5, "anderthalb": 1.5,
    "eineinhalb": 1.5, "viertel": 0.25, "ein viertel": 0.25, "drei viertel": 0.75, "dreiviertel": 0.75,
    "drittel": 1 / 3, "ein drittel": 1 / 3, "zwei drittel": 2 / 3, "ein zehntel": 0.1, "zehntel": 0.1,
}
"""Parts said as words: "ein Viertel", "dreiviertel"."""

_WORD = rf"(?:{'|'.join(_PARTS)})+"
_FRACTION = "|".join(sorted((re.escape(f) for f in FRACTIONS), key=len, reverse=True))
NUM_RE = (
    r"-?\d{1,3}(?:\.\d{3})+(?:,\d+)?|-?\d+(?:[,.]\d+)?"
    rf"|(?<!\w)(?:(?:minus|negativ)\s+)?(?:{_FRACTION}|\w*einhalb|{_WORD})(?:\s+(?:komma\s+)?(?:{_WORD}))*(?!\w)"
)
N = rf"({NUM_RE})"
"""A number in a grammar, as a group."""


def _split(word: str) -> list[str] | None:
    """A number word in its parts: "dreihundertfünfundvierzig" -> drei,
    hundert, fünf, und, vierzig; None when it isn't one."""
    if not word:
        return []
    for part in _PARTS:
        if word.startswith(part):
            rest = _split(word[len(part):])
            if rest is not None:
                return [part, *rest]
    return None


def _whole(words: list[str]) -> int | None:
    """A whole number said in words, each maybe a compound."""
    parts = [p for w in words for p in (_split(w) or [None])]
    if not parts or None in parts or all(p == "und" for p in parts):
        return None
    total = current = 0
    for part in parts:
        if part == "und":
            continue
        if part == "hundert":
            current = (current or 1) * 100
        elif part in _SCALES:
            total += (current or 1) * _SCALES[part]
            current = 0
        else:
            current += _ONES[part]
    return total + current


def parse_num(s):
    """A number written or said: "1.000", "3,5", "dreihundertfünfundvierzig",
    "drei Komma fünf", "dreieinhalb", "drei Viertel", "minus zwei"; None."""
    s = s.strip().lower()
    if not s:
        return None
    if re.fullmatch(r"-?\d{1,3}(?:\.\d{3})+(?:,\d+)?", s):
        return float(s.replace(".", "").replace(",", "."))
    if re.fullmatch(r"-?\d+(?:[,.]\d+)?", s):
        return float(s.replace(",", "."))
    negative = bool(re.match(r"(?:minus|negativ)\s+", s))
    if negative:
        s = s.split(None, 1)[1]
    sign = -1 if negative else 1
    if s in FRACTIONS:
        return sign * FRACTIONS[s]
    if m := re.fullmatch(r"(.+?)\s+komma\s+(.+)", s):
        whole = _whole(m.group(1).split())
        digits = [str(_ONES.get(w, w)) for w in m.group(2).split()]
        if whole is None or not all(d.isdigit() and len(d) == 1 for d in digits):
            return None
        return sign * (whole + float("0." + "".join(digits)))
    # "dreieinhalb", "zwei einhalb": and a half.
    if m := re.fullmatch(r"(.+?)\s*einhalb", s):
        whole = _whole(m.group(1).split())
        return None if whole is None else sign * (whole + 0.5)
    if m := re.fullmatch(r"(.+?)\s+(viertel|drittel|zehntel)", s):
        count = _whole(m.group(1).split())
        return None if count is None else sign * count * FRACTIONS[m.group(2)]
    whole = _whole(s.split())
    return None if whole is None else sign * whole


_DEGREES = {"f": "Fahrenheit", "c": "Celsius", "k": "Kelvin"}
_CURRENCY = {"$": "Dollar", "€": "Euro", "£": "britische Pfund", "¥": "Yen"}
_VULGAR = {"½": 0.5, "¼": 0.25, "¾": 0.75, "⅓": 1 / 3, "⅔": 2 / 3, "⅕": 0.2, "⅛": 0.125, "⅜": 0.375, "⅝": 0.625, "⅞": 0.875}
_SQUARE_UNITS = {"mm": "Quadratmillimeter", "cm": "Quadratzentimeter", "m": "Quadratmeter", "km": "Quadratkilometer"}


def _decimal(value: float) -> str:
    return f"{round(value, 4):g}".replace(".", ",")


def normalize(text: str) -> str:
    """A question as typed, or written by a speech-to-text that formats what
    it hears, in the words the grammars read: "12 × 8" -> "12 mal 8", "50°F"
    -> "50 Grad Fahrenheit", "1½ Tassen" -> "1,5 Tassen", "10 m²" -> "10
    Quadratmeter", "5²" -> "5 hoch 2", "√144" -> "Wurzel aus 144", "80 €" ->
    "80 Euro", "100km" -> "100 km"."""
    s = text
    s = s.translate(str.maketrans({"’": "'", "‘": "'", "‛": "'", "`": "'", "´": "'", "“": '"', "”": '"', "„": '"'}))
    s = s.replace('"', " ")
    s = re.sub(r"(?<![A-Za-zÄÖÜäöüß])'|'(?![A-Za-zÄÖÜäöüß])", " ", s)
    s = re.sub(r"[    ]", " ", s)
    s = re.sub(r"[−‒–—]", "-", s)
    s = s.replace("℉", "°F").replace("℃", "°C")
    s = re.sub(r"\s*°\s*([FCKfck])\b", lambda m: f" Grad {_DEGREES[m.group(1).lower()]}", s)
    s = re.sub(r"\s*°", " Grad", s)
    s = re.sub(r"\b(mm|cm|km|m)²", lambda m: _SQUARE_UNITS[m.group(1)], s)
    s = re.sub(r"\s*²", " hoch 2", s)
    s = re.sub(r"\s*³", " hoch 3", s)
    vulgar = "".join(_VULGAR)
    s = re.sub(rf"(\d+)\s*([{vulgar}])", lambda m: _decimal(int(m.group(1)) + _VULGAR[m.group(2)]), s)
    s = re.sub(rf"[{vulgar}]", lambda m: f" {_decimal(_VULGAR[m.group(0)])} ", s)
    s = re.sub(r"\b(\d+)\s+(\d+)/(\d+)\b", lambda m: _decimal(int(m.group(1)) + int(m.group(2)) / int(m.group(3))), s)
    s = re.sub(r"(\d)\s*[x×✕✖]\s*(\d)", r"\1 mal \2", s)
    s = re.sub(r"\s*[×✕✖·∙⋅]\s*", " mal ", s)
    s = re.sub(r"\s*÷\s*", " geteilt durch ", s)
    s = re.sub(r"√\s*", "Wurzel aus ", s)
    s = re.sub(r"∛\s*", "Kubikwurzel aus ", s)
    s = re.sub(r"\s*=\s*\??\s*$", "", s)
    symbols = re.escape("".join(_CURRENCY))
    s = re.sub(rf"(?:US)?([{symbols}])\s*(\d[\d.,]*)", lambda m: f"{m.group(2)} {_CURRENCY[m.group(1)]}", s)
    s = re.sub(rf"(\d)\s*([{symbols}])", lambda m: f"{m.group(1)} {_CURRENCY[m.group(2)]}", s)
    # A unit written on its number: "100km", "5kg" (not "4." or "3h"-like times).
    s = re.sub(r"(\d)([A-Za-zÄÖÜäöüß]+)\b", r"\1 \2", s)
    s = re.sub(r"\s+([?.!])", r"\1", s)
    return re.sub(r"\s+", " ", s).strip()


def clean(q):
    """A question as the grammars read it: lowercase, no lead-in ("hey, kannst
    du mir sagen"), no closing punctuation or "bitte", symbols as words."""
    s = q.lower().strip()
    s = re.sub(r"^(hey|ok|okay|also|äh|ähm|sag mal)[,\s]+", "", s)
    s = re.sub(r"^(?:kannst du mir sagen,? |kannst du |könntest du |weißt du,? |sag mir,? |bitte )?(?:berechne |rechne )?", "", s)
    s = re.sub(r"[?!.]+$", "", s).strip()
    s = re.sub(r",?\s+bitte$", "", s)
    s = re.sub(r"\s*[?!]+\s+|\.\s+(?=\D)", " ", s)
    s = s.replace("×", " mal ").replace("÷", " geteilt durch ").replace("%", " prozent")
    s = re.sub(r"(\d)\s*\*\s*(\d)", r"\1 mal \2", s)
    # Not a date's slashes: "25/12/2026".
    s = re.sub(r"(?<![\d/])(\d+)\s*/\s*(\d+)(?![\d/])", r"\1 geteilt durch \2", s)
    s = re.sub(r"(\d)\s*\+\s*(\d)", r"\1 plus \2", s)
    s = re.sub(r"(\d)\s+-\s+(\d)", r"\1 minus \2", s)
    s = re.sub(r"(\d)\s*\^\s*(\d)", r"\1 hoch \2", s)
    return re.sub(r"\s+", " ", s)


NOUNS = {
    "prozent", "wurzel", "quadratwurzel", "kubikwurzel", "hälfte", "durchschnitt", "mittelwert", "summe", "quadrat",
    "fakultät", "potenz", "minute", "minuten", "stunde", "stunden", "uhr", "mittag", "mitternacht",
}
"""Nouns in what the skills say back, capitalized as German writes them."""


def nouns(text: str) -> str:
    """``text`` (as cleaned, lowercase) with its ``NOUNS`` capitalized: "15
    Prozent von 80"."""
    return re.sub(r"\w+", lambda m: m.group(0).capitalize() if m.group(0) in NOUNS else m.group(0), text)


def sentence(answer: str | None) -> str | None:
    """"96" -> "Das sind 96."; "1 Tag" -> "Das ist 1 Tag."; "Ja" -> "Ja."."""
    if answer is None:
        return None
    answer = answer.strip()
    if answer in ("Ja", "Nein"):
        return answer + "."
    if re.match(r"-?\d", answer):
        verb = "ist" if re.match(r"-?1(?![\d,])", answer) else "sind"
        return f"Das {verb} {answer}."
    return answer if answer.endswith(".") else answer + "."
