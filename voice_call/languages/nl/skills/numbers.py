"""How the Dutch skills read numbers and questions, with English's names
(voice_call/languages/en/skills/numbers.py).

Dutch writes a number in words as one ("driehonderdvijfenveertig",
"tweeëntwintig"), split here into its parts; a decimal comma ("3,5", "drie
komma vijf") and a thousands point ("1.000"); and halves into words
("anderhalf", "tweeënhalf")."""

from __future__ import annotations

import re

DECIMAL = ","
THOUSANDS = ""

_ONES = {
    "nul": 0, "een": 1, "één": 1, "twee": 2, "drie": 3, "vier": 4, "vijf": 5, "zes": 6, "zeven": 7, "acht": 8,
    "negen": 9, "tien": 10, "elf": 11, "twaalf": 12, "dertien": 13, "veertien": 14, "vijftien": 15, "zestien": 16,
    "zeventien": 17, "achttien": 18, "negentien": 19, "twintig": 20, "dertig": 30, "veertig": 40, "vijftig": 50,
    "zestig": 60, "zeventig": 70, "tachtig": 80, "negentig": 90,
}
_SCALES = {"honderd": 100, "duizend": 1000, "miljoen": 10**6, "miljard": 10**9}
_PARTS = sorted([*_ONES, *_SCALES, "en", "ën"], key=len, reverse=True)

FRACTIONS = {
    "half": 0.5, "een half": 0.5, "halve": 0.5, "een halve": 0.5, "anderhalf": 1.5, "anderhalve": 1.5,
    "kwart": 0.25, "een kwart": 0.25, "driekwart": 0.75, "drie kwart": 0.75, "derde": 1 / 3, "een derde": 1 / 3,
    "twee derde": 2 / 3, "een tiende": 0.1, "tiende": 0.1,
}

_WORD = rf"(?:{'|'.join(_PARTS)})+"
_FRACTION = "|".join(sorted((re.escape(f) for f in FRACTIONS), key=len, reverse=True))
NUM_RE = (
    r"-?\d{1,3}(?:\.\d{3})+(?:,\d+)?|-?\d+(?:[,.]\d+)?"
    rf"|(?<!\w)(?:(?:min|minus|negatief)\s+)?(?:{_FRACTION}|\w*(?:en|ën)half|{_WORD})(?:\s+(?:komma\s+)?(?:{_WORD}))*(?!\w)"
)
N = rf"({NUM_RE})"


def _split(word: str) -> list[str] | None:
    if not word:
        return []
    for part in _PARTS:
        if word.startswith(part):
            rest = _split(word[len(part):])
            if rest is not None:
                return [part, *rest]
    return None


def _whole(words: list[str]) -> int | None:
    parts = [p for w in words for p in (_split(w) or [None])]
    if not parts or None in parts or all(p in ("en", "ën") for p in parts):
        return None
    total = current = 0
    for part in parts:
        if part in ("en", "ën"):
            continue
        if part == "honderd":
            current = (current or 1) * 100
        elif part in _SCALES:
            total += (current or 1) * _SCALES[part]
            current = 0
        else:
            current += _ONES[part]
    return total + current


def parse_num(s):
    """A number written or said: "1.000", "3,5", "driehonderdvijfenveertig",
    "drie komma vijf", "tweeënhalf", "driekwart", "min twee"; None."""
    s = s.strip().lower()
    if not s:
        return None
    if re.fullmatch(r"-?\d{1,3}(?:\.\d{3})+(?:,\d+)?", s):
        return float(s.replace(".", "").replace(",", "."))
    if re.fullmatch(r"-?\d+(?:[,.]\d+)?", s):
        return float(s.replace(",", "."))
    negative = bool(re.match(r"(?:min|minus|negatief)\s+", s))
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
    # "tweeënhalf", "drie en een half": and a half.
    if m := re.fullmatch(r"(.+?)\s*(?:en|ën)\s*(?:een )?half", s):
        whole = _whole(m.group(1).split())
        return None if whole is None else sign * (whole + 0.5)
    if m := re.fullmatch(r"(.+?)\s+(kwart|derde|tiende)", s):
        count = _whole(m.group(1).split())
        return None if count is None else sign * count * FRACTIONS[m.group(2)]
    whole = _whole(s.split())
    return None if whole is None else sign * whole


_DEGREES = {"f": "Fahrenheit", "c": "Celsius", "k": "Kelvin"}
_CURRENCY = {"$": "dollar", "€": "euro", "£": "pond", "¥": "yen"}
_VULGAR = {"½": 0.5, "¼": 0.25, "¾": 0.75, "⅓": 1 / 3, "⅔": 2 / 3, "⅕": 0.2, "⅛": 0.125, "⅜": 0.375, "⅝": 0.625, "⅞": 0.875}
_SQUARE_UNITS = {"mm": "vierkante millimeter", "cm": "vierkante centimeter", "m": "vierkante meter", "km": "vierkante kilometer"}


def _decimal(value: float) -> str:
    return f"{round(value, 4):g}".replace(".", ",")


def normalize(text: str) -> str:
    """A question as typed, or written by a speech-to-text, in the words the
    grammars read: "12 × 8" -> "12 keer 8", "50°F" -> "50 graden Fahrenheit",
    "1½ kopje" -> "1,5 kopje", "10 m²" -> "10 vierkante meter", "5²" -> "5
    kwadraat", "√144" -> "wortel van 144", "80 €" -> "80 euro"."""
    s = text
    s = s.translate(str.maketrans({"’": "'", "‘": "'", "‛": "'", "`": "'", "´": "'", "“": '"', "”": '"', "„": '"'}))
    s = s.replace('"', " ")
    s = re.sub(r"(?<![A-Za-zÀ-ÿ])'(?!s\b)|'(?![A-Za-zÀ-ÿ])", " ", s)
    s = re.sub(r"[    ]", " ", s)
    s = re.sub(r"[−‒–—]", "-", s)
    s = s.replace("℉", "°F").replace("℃", "°C")
    s = re.sub(r"\s*°\s*([FCKfck])\b", lambda m: f" graden {_DEGREES[m.group(1).lower()]}", s)
    s = re.sub(r"\s*°", " graden", s)
    s = re.sub(r"\b(mm|cm|km|m)²", lambda m: _SQUARE_UNITS[m.group(1)], s)
    s = re.sub(r"\s*²", " kwadraat", s)
    s = re.sub(r"\s*³", " tot de macht 3", s)
    vulgar = "".join(_VULGAR)
    s = re.sub(rf"(\d+)\s*([{vulgar}])", lambda m: _decimal(int(m.group(1)) + _VULGAR[m.group(2)]), s)
    s = re.sub(rf"[{vulgar}]", lambda m: f" {_decimal(_VULGAR[m.group(0)])} ", s)
    s = re.sub(r"\b(\d+)\s+(\d+)/(\d+)\b", lambda m: _decimal(int(m.group(1)) + int(m.group(2)) / int(m.group(3))), s)
    s = re.sub(r"(\d)\s*[x×✕✖]\s*(\d)", r"\1 keer \2", s)
    s = re.sub(r"\s*[×✕✖·∙⋅]\s*", " keer ", s)
    s = re.sub(r"\s*÷\s*", " gedeeld door ", s)
    s = re.sub(r"√\s*", "wortel van ", s)
    s = re.sub(r"∛\s*", "derdemachtswortel van ", s)
    s = re.sub(r"\s*=\s*\??\s*$", "", s)
    symbols = re.escape("".join(_CURRENCY))
    s = re.sub(rf"([{symbols}])\s*(\d[\d.,]*)", lambda m: f"{m.group(2)} {_CURRENCY[m.group(1)]}", s)
    s = re.sub(rf"(\d)\s*([{symbols}])", lambda m: f"{m.group(1)} {_CURRENCY[m.group(2)]}", s)
    s = re.sub(r"(\d)([A-Za-zÀ-ÿ]+)\b", r"\1 \2", s)
    s = re.sub(r"\s+([?.!])", r"\1", s)
    return re.sub(r"\s+", " ", s).strip()


def clean(q):
    """A question as the grammars read it: lowercase, no lead-in ("hé, kun je
    me vertellen"), no closing punctuation or "alsjeblieft", symbols as
    words."""
    s = q.lower().strip()
    s = re.sub(r"^(hé|hey|ok|oké|nou|eh|zeg)[,\s]+", "", s)
    s = re.sub(r"^(?:kun je me vertellen,? |kunt u me vertellen,? |kun je |kunt u |kan je |weet je,? |zeg eens,? )?(?:bereken |reken uit,? )?", "", s)
    s = re.sub(r"[?!.]+$", "", s).strip()
    s = re.sub(r",?\s+(?:alsjeblieft|alstublieft)$", "", s)
    s = re.sub(r"\s*[?!]+\s+|\.\s+(?=\D)", " ", s)
    s = s.replace("×", " keer ").replace("÷", " gedeeld door ").replace("%", " procent")
    s = re.sub(r"(\d)\s*\*\s*(\d)", r"\1 keer \2", s)
    # Not a date's slashes: "25/12/2026".
    s = re.sub(r"(?<![\d/])(\d+)\s*/\s*(\d+)(?![\d/])", r"\1 gedeeld door \2", s)
    s = re.sub(r"(\d)\s*\+\s*(\d)", r"\1 plus \2", s)
    s = re.sub(r"(\d)\s+-\s+(\d)", r"\1 min \2", s)
    s = re.sub(r"(\d)\s*\^\s*(\d)", r"\1 tot de macht \2", s)
    return re.sub(r"\s+", " ", s)


def sentence(answer: str | None) -> str | None:
    """"96" -> "Dat is 96."; "Ja" -> "Ja."."""
    if answer is None:
        return None
    answer = answer.strip()
    if answer in ("Ja", "Nee"):
        return answer + "."
    if re.match(r"-?\d", answer):
        return f"Dat is {answer}."
    return answer if answer.endswith(".") else answer + "."
