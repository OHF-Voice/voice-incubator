"""How the French skills read numbers and questions, with English's names
(voice_call/languages/en/skills/numbers.py).

French says a number in parts, often hyphened ("trois cent quarante-cinq",
"quatre-vingt-dix-neuf", "soixante et onze"; Belgian and Swiss "septante",
"nonante"); a decimal comma ("3,5", "trois virgule cinq"); and thousands
with a space ("1 000"), read here and never written: a speech-to-text
reads "1 000" digit by digit."""

from __future__ import annotations

import re

DECIMAL = ","
THOUSANDS = ""

_ONES = {
    "zéro": 0, "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6, "sept": 7, "huit": 8,
    "neuf": 9, "dix": 10, "onze": 11, "douze": 12, "treize": 13, "quatorze": 14, "quinze": 15, "seize": 16,
    "vingt": 20, "vingts": 20, "trente": 30, "quarante": 40, "cinquante": 50, "soixante": 60,
    "septante": 70, "huitante": 80, "octante": 80, "nonante": 90,
}
_SCALES = {"mille": 1000, "million": 10**6, "millions": 10**6, "milliard": 10**9, "milliards": 10**9}
_HUNDREDS = ("cent", "cents")

FRACTIONS = {
    "demi": 0.5, "un demi": 0.5, "une demie": 0.5, "la moitié": 0.5, "un quart": 0.25, "quart": 0.25,
    "trois quarts": 0.75, "un tiers": 1 / 3, "tiers": 1 / 3, "deux tiers": 2 / 3, "un dixième": 0.1,
}

_WORDS = sorted([*_ONES, *_SCALES, *_HUNDREDS], key=len, reverse=True)
_WORD = rf"(?:{'|'.join(_WORDS)})"
_FRACTION = "|".join(sorted((re.escape(f) for f in FRACTIONS), key=len, reverse=True))
NUM_RE = (
    r"-?\d{1,3}(?:[  ]\d{3})+(?:,\d+)?|-?\d+(?:[,.]\d+)?"
    rf"|(?<![\w-])(?:moins\s+)?(?:{_FRACTION}|{_WORD}(?:[\s-]+(?:et[\s-]+)?{_WORD})*"
    rf"(?:\s+virgule(?:\s+{_WORD})+)?(?:\s+et\s+(?:demi|demie|quart))?)(?![\w-])"
)
N = rf"({NUM_RE})"


def _whole(words: list[str]) -> int | None:
    """"trois cent quarante cinq" -> 345, "quatre vingt dix neuf" -> 99,
    "soixante et onze" -> 71, "deux mille vingt six" -> 2026."""
    words = [w for w in words if w != "et"]
    if not words or any(w not in _ONES and w not in _SCALES and w not in _HUNDREDS for w in words):
        return None
    total = current = 0
    for index, word in enumerate(words):
        if word in ("vingt", "vingts") and index and words[index - 1] == "quatre":
            current += 76  # quatre-vingts: 80, its 4 already counted
        elif word in _HUNDREDS:
            current = (current or 1) * 100
        elif word in _SCALES:
            total += (current or 1) * _SCALES[word]
            current = 0
        else:
            current += _ONES[word]
    return total + current


def parse_num(s):
    """A number written or said: "1 000", "3,5", "trois cent quarante-cinq",
    "trois virgule cinq", "deux et demi", "trois quarts", "moins deux"; None."""
    s = s.strip().lower()
    if not s:
        return None
    if re.fullmatch(r"-?\d{1,3}(?:[  ]\d{3})+(?:,\d+)?", s):
        return float(re.sub(r"[  ]", "", s).replace(",", "."))
    if re.fullmatch(r"-?\d+(?:[,.]\d+)?", s):
        return float(s.replace(",", "."))
    negative = s.startswith("moins ")
    if negative:
        s = s.split(None, 1)[1]
    sign = -1 if negative else 1
    s = " ".join(s.replace("-", " ").split())
    if s in FRACTIONS:
        return sign * FRACTIONS[s]
    if m := re.fullmatch(r"(.+?)\s+virgule\s+(.+)", s):
        whole = _whole(m.group(1).split())
        decimals = _whole(m.group(2).split())
        if whole is None or decimals is None:
            return None
        # "trois virgule cinq", "trois virgule vingt-cinq", "trois virgule zéro cinq".
        zeros = len(re.match(r"(?:zéro\s+)*", m.group(2)).group(0).split())
        return sign * (whole + float("0." + "0" * zeros + str(decimals)))
    # "deux et demi", "une heure et quart": and a half, and a quarter.
    if m := re.fullmatch(r"(.+?)\s+et\s+(demi|demie|quart)", s):
        whole = _whole(m.group(1).split())
        return None if whole is None else sign * (whole + (0.25 if m.group(2) == "quart" else 0.5))
    if m := re.fullmatch(r"(.+?)\s+(quarts|tiers|dixièmes)", s):
        count = _whole(m.group(1).split())
        part = {"quarts": 0.25, "tiers": 1 / 3, "dixièmes": 0.1}[m.group(2)]
        return None if count is None else sign * count * part
    whole = _whole(s.split())
    return None if whole is None else sign * whole


_DEGREES = {"f": "Fahrenheit", "c": "Celsius", "k": "Kelvin"}
_CURRENCY = {"$": "dollars", "€": "euros", "£": "livres", "¥": "yens"}
_VULGAR = {"½": 0.5, "¼": 0.25, "¾": 0.75, "⅓": 1 / 3, "⅔": 2 / 3, "⅕": 0.2, "⅛": 0.125, "⅜": 0.375, "⅝": 0.625, "⅞": 0.875}
_SQUARE_UNITS = {"mm": "millimètres carrés", "cm": "centimètres carrés", "m": "mètres carrés", "km": "kilomètres carrés"}


def _decimal(value: float) -> str:
    return f"{round(value, 4):g}".replace(".", ",")


def normalize(text: str) -> str:
    """A question as typed, or written by a speech-to-text, in the words the
    grammars read: "12 × 8" -> "12 fois 8", "50°F" -> "50 degrés Fahrenheit",
    "1½ tasse" -> "1,5 tasse", "10 m²" -> "10 mètres carrés", "5²" -> "5 au
    carré", "√144" -> "racine carrée de 144", "80 €" -> "80 euros", "1 000"
    -> "1000"."""
    s = text
    s = s.translate(str.maketrans({"’": "'", "‘": "'", "‛": "'", "`": "'", "´": "'", "“": '"', "”": '"', "«": '"', "»": '"'}))
    s = s.replace('"', " ")
    s = re.sub(r"(?<![A-Za-zÀ-ÿ])'|'(?![A-Za-zÀ-ÿ])", " ", s)
    s = re.sub(r"(?<=\d)[  ](?=\d{3}\b)", "", s)
    s = re.sub(r"[  ]", " ", s)
    s = re.sub(r"[−‒–—]", "-", s)
    s = s.replace("℉", "°F").replace("℃", "°C")
    s = re.sub(r"\s*°\s*([FCKfck])\b", lambda m: f" degrés {_DEGREES[m.group(1).lower()]}", s)
    s = re.sub(r"\s*°", " degrés", s)
    s = re.sub(r"\b(mm|cm|km|m)²", lambda m: _SQUARE_UNITS[m.group(1)], s)
    s = re.sub(r"\s*²", " au carré", s)
    s = re.sub(r"\s*³", " au cube", s)
    vulgar = "".join(_VULGAR)
    s = re.sub(rf"(\d+)\s*([{vulgar}])", lambda m: _decimal(int(m.group(1)) + _VULGAR[m.group(2)]), s)
    s = re.sub(rf"[{vulgar}]", lambda m: f" {_decimal(_VULGAR[m.group(0)])} ", s)
    s = re.sub(r"\b(\d+)\s+(\d+)/(\d+)\b", lambda m: _decimal(int(m.group(1)) + int(m.group(2)) / int(m.group(3))), s)
    s = re.sub(r"(\d)\s*[x×✕✖]\s*(\d)", r"\1 fois \2", s)
    s = re.sub(r"\s*[×✕✖·∙⋅]\s*", " fois ", s)
    s = re.sub(r"\s*÷\s*", " divisé par ", s)
    s = re.sub(r"√\s*", "racine carrée de ", s)
    s = re.sub(r"∛\s*", "racine cubique de ", s)
    s = re.sub(r"\s*=\s*\??\s*$", "", s)
    symbols = re.escape("".join(_CURRENCY))
    s = re.sub(rf"([{symbols}])\s*(\d[\d.,]*)", lambda m: f"{m.group(2)} {_CURRENCY[m.group(1)]}", s)
    s = re.sub(rf"(\d)\s*([{symbols}])", lambda m: f"{m.group(1)} {_CURRENCY[m.group(2)]}", s)
    s = re.sub(r"(\d)([A-Za-zÀ-ÿ]+)\b", r"\1 \2", s)
    s = re.sub(r"\s+([?.!])", r"\1", s)
    return re.sub(r"\s+", " ", s).strip()


def clean(q):
    """A question as the grammars read it: lowercase, no hyphens between
    words ("quatre vingt", "qu'est ce que"), no lead-in ("dis-moi, est-ce
    que tu sais"), no closing punctuation or "s'il te plaît", symbols as
    words."""
    s = q.lower().strip()
    s = re.sub(r"(?<=[a-zà-ÿ])-(?=[a-zà-ÿ])", " ", s)
    s = re.sub(r"^(hé|hey|ok|bon|euh|alors)[,\s]+", "", s)
    s = re.sub(
        r"^(?:dis moi,? |dites moi,? |peux tu me dire,? |pouvez vous me dire,? |tu peux me dire,? "
        r"|est ce que tu sais,? |sais tu,? |tu sais,? )?(?:calcule,? |calculez,? )?",
        "", s,
    )
    s = re.sub(r"\s*[?!.]+$", "", s).strip()
    s = re.sub(r",?\s+s'il (?:te|vous) plaît$", "", s)
    s = re.sub(r"\s*[?!]+\s+|\.\s+(?=\D)", " ", s)
    s = s.replace("×", " fois ").replace("÷", " divisé par ").replace("%", " pour cent")
    s = re.sub(r"(\d)\s*\*\s*(\d)", r"\1 fois \2", s)
    # Not a date's slashes: "25/12/2026".
    s = re.sub(r"(?<![\d/])(\d+)\s*/\s*(\d+)(?![\d/])", r"\1 divisé par \2", s)
    s = re.sub(r"(\d)\s*\+\s*(\d)", r"\1 plus \2", s)
    s = re.sub(r"(\d)\s+-\s+(\d)", r"\1 moins \2", s)
    s = re.sub(r"(\d)\s*\^\s*(\d)", r"\1 puissance \2", s)
    return re.sub(r"\s+", " ", s)


def sentence(answer: str | None) -> str | None:
    """"96" -> "Ça fait 96."; "Oui" -> "Oui."."""
    if answer is None:
        return None
    answer = answer.strip()
    if answer in ("Oui", "Non"):
        return answer + "."
    if re.match(r"-?\d", answer):
        return f"Ça fait {answer}."
    return answer if answer.endswith(".") else answer + "."
