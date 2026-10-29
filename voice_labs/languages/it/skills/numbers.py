"""How the Italian skills read numbers and questions, with English's names
(voice_labs/languages/en/skills/numbers.py).

Italian writes a number in words as one ("trecentoquarantacinque",
"duemilaventisei"), a ten losing its vowel before "uno" and "otto"
("ventuno", "trentotto"), and "tre" taking an accent at the end
("trentatré"): split here into its parts. A decimal comma ("3,5", "tre
virgola cinque"), and thousands with a point or a space ("1.000", "1 000"),
read here and never written: a voice reads "1.000" badly. A half is "e
mezzo" ("due e mezzo"). A question typed may leave out its accents ("piu",
"e bisestile"), so the grammars read a question both ways (``loose``), or
without them (``fold``)."""

from __future__ import annotations

import re
import unicodedata

DECIMAL = ","
"""8,047."""

THOUSANDS = ""
"""31536000: said whole, as a voice misreads "31.536.000"."""


def fold(text: str) -> str:
    """Without accents: "più" -> "piu", "lunedì" -> "lunedi" (for matching
    only)."""
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


_LOOSE = str.maketrans({"à": "[aà]", "è": "[eèé]", "é": "[eèé]", "ì": "[iì]", "ò": "[oò]", "ù": "[uù]"})


def loose(pattern: str) -> str:
    """A grammar written with its accents, read with or without them: "più"
    also matches "piu", "è" "e" and "é". (Its accented letters must be
    outside any [...].)"""
    return pattern.translate(_LOOSE)


_ONES = {
    "zero": 0, "uno": 1, "una": 1, "un": 1, "due": 2, "tre": 3, "quattro": 4, "cinque": 5, "sei": 6, "sette": 7,
    "otto": 8, "nove": 9, "dieci": 10, "undici": 11, "dodici": 12, "tredici": 13, "quattordici": 14, "quindici": 15,
    "sedici": 16, "diciassette": 17, "diciotto": 18, "diciannove": 19, "venti": 20, "trenta": 30, "quaranta": 40,
    "cinquanta": 50, "sessanta": 60, "settanta": 70, "ottanta": 80, "novanta": 90,
}
_ELIDED = {"vent": 20, "trent": 30, "quarant": 40, "cinquant": 50, "sessant": 60, "settant": 70, "ottant": 80,
           "novant": 90, "cent": 100}
"""A ten, or "cento", before a vowel: "ventuno", "trentotto", "centottanta"."""

_HUNDRED = ("cento", "cent")
_THOUSAND = ("mille", "mila")
_SCALES = {"milione": 10**6, "milioni": 10**6, "miliardo": 10**9, "miliardi": 10**9}
_PARTS = sorted([*_ONES, *_ELIDED, "cento", *_THOUSAND, *_SCALES], key=len, reverse=True)

FRACTIONS = {
    "mezzo": 0.5, "mezza": 0.5, "un mezzo": 0.5, "metà": 0.5, "un quarto": 0.25, "tre quarti": 0.75,
    "un terzo": 1 / 3, "due terzi": 2 / 3, "un decimo": 0.1,
}
"""Parts said as words: "mezzo litro", "tre quarti"."""

_FOLDED_FRACTIONS = {fold(f): v for f, v in FRACTIONS.items()}


def _alternatives(words) -> str:
    return "|".join(sorted((re.escape(w) for w in set(words)), key=len, reverse=True))


# "tré" as written at the end of a number ("trentatré"): a word of its own
# here, not [eé], so ``loose`` can read the grammars it is in.
_WORD = rf"(?:{_alternatives([*_PARTS, 'tré'])})+"
_FRACTION = _alternatives(FRACTIONS)
_AND_PART = r"e\s+(?:mezzo|mezza|un quarto|tre quarti)"
NUM_RE = (
    r"-?\d{1,3}(?:\.\d{3})+(?:,\d+)?|-?\d+(?:[,.]\d+)?"
    rf"|(?<![\w'])(?:meno\s+)?(?:{_FRACTION}|{_WORD}(?:\s+{_WORD})*"
    rf"(?:\s+virgola(?:\s+{_WORD})+)?(?:\s+{_AND_PART})?)(?![\w'])"
)
N = rf"({NUM_RE})"
"""A number in a grammar, as a group."""


def _split(word: str) -> list[str] | None:
    """A number written as one word, in its parts: "trecentoquarantacinque"
    -> tre, cento, quaranta, cinque."""
    if not word:
        return []
    for part in _PARTS:
        if word.startswith(part):
            rest = _split(word[len(part):])
            if rest is not None:
                return [part, *rest]
    return None


def _whole(words: list[str]) -> int | None:
    """A whole number said in words (folded): "trecentoquarantacinque",
    "duemila ventisei", "un milione"."""
    parts: list[str] = []
    for word in words:
        split = _split(word)
        if not split:
            return None
        parts += split
    total = current = 0
    for index, part in enumerate(parts):
        following = parts[index + 1] if index + 1 < len(parts) else ""
        if part in _ELIDED and not following.startswith(("u", "o")):
            return None  # "vent" alone, "ventdue"
        if part in _HUNDRED:
            current = (current or 1) * 100
        elif part == "mille":
            total += (current or 1) * 1000
            current = 0
        elif part == "mila":
            if current < 2:
                return None  # "mila" counts thousands from two: "duemila"
            total += current * 1000
            current = 0
        elif part in _SCALES:
            total += (current or 1) * _SCALES[part]
            current = 0
        else:
            current += _ONES.get(part, _ELIDED.get(part, 0))
    return total + current


def parse_num(s):
    """A number written or said: "1.000", "1 000", "3,5", "trecentoquaranta-
    cinque", "tre virgola cinque", "due e mezzo", "tre quarti", "meno due";
    None."""
    s = fold(s.strip().lower())
    if not s:
        return None
    if re.fullmatch(r"-?\d{1,3}(?:[. ]\d{3})+(?:,\d+)?", s):
        return float(re.sub(r"[. ]", "", s).replace(",", "."))
    if re.fullmatch(r"-?\d+(?:[,.]\d+)?", s):
        return float(s.replace(",", "."))
    negative = s.startswith("meno ")
    if negative:
        s = s.split(None, 1)[1]
    sign = -1 if negative else 1
    s = " ".join(s.replace("-", " ").split())
    if s in _FOLDED_FRACTIONS:
        return sign * _FOLDED_FRACTIONS[s]
    if m := re.fullmatch(r"(.+?)\s+virgola\s+(.+)", s):
        whole, words = _whole(m.group(1).split()), m.group(2).split()
        # "tre virgola cinque", "tre virgola venticinque", "tre virgola zero cinque".
        zeros = 0
        while zeros < len(words) - 1 and words[zeros] == "zero":
            zeros += 1
        rest = _whole(words[zeros:])
        if whole is None or rest is None:
            return None
        return sign * (whole + float(f"0.{'0' * zeros}{rest}"))
    # "due e mezzo", "tre e un quarto", "un'ora e tre quarti".
    if m := re.fullmatch(r"(.+?)\s+e\s+(mezzo|mezza|un quarto|tre quarti)", s):
        whole = parse_num(m.group(1))
        return None if whole is None else sign * (whole + _FOLDED_FRACTIONS[m.group(2)])
    if m := re.fullmatch(r"(.+?)\s+(quarti|terzi|decimi)", s):
        count = _whole(m.group(1).split())
        part = {"quarti": 0.25, "terzi": 1 / 3, "decimi": 0.1}[m.group(2)]
        return None if count is None else sign * count * part
    whole = _whole(s.split())
    return None if whole is None else sign * whole


_DEGREES = {"f": "Fahrenheit", "c": "Celsius", "k": "Kelvin"}
_CURRENCY = {"$": "dollari", "€": "euro", "£": "sterline", "¥": "yen"}
_VULGAR = {"½": 0.5, "¼": 0.25, "¾": 0.75, "⅓": 1 / 3, "⅔": 2 / 3, "⅕": 0.2, "⅛": 0.125, "⅜": 0.375, "⅝": 0.625, "⅞": 0.875}
_SQUARE_UNITS = {"mm": "millimetri quadrati", "cm": "centimetri quadrati", "m": "metri quadrati",
                 "km": "chilometri quadrati"}
_MONTH_START = r"(?:gen|feb|mar|apr|mag|giu|lug|ago|set|ott|nov|dic)[a-z]*\b"


def _decimal(value: float) -> str:
    return f"{round(value, 4):g}".replace(".", ",")


def normalize(text: str) -> str:
    """A question as typed, or written by a speech-to-text, in the words the
    grammars read: "12 × 8" -> "12 per 8", "50°F" -> "50 gradi Fahrenheit",
    "1½ tazze" -> "1,5 tazze", "10 m²" -> "10 metri quadrati", "5²" -> "5 al
    quadrato", "√144" -> "radice quadrata di 144", "80 €" -> "80 euro", "1°
    maggio" -> "1 maggio", "e'" -> "è", "1 000" -> "1000"."""
    s = text
    s = s.translate(str.maketrans({"’": "'", "‘": "'", "‛": "'", "`": "'", "´": "'", "“": '"', "”": '"', "„": '"',
                                   "«": '"', "»": '"'}))
    s = s.replace('"', " ")
    # An accent typed as an apostrophe: "e'", "piu'", "perche'" (not "po'").
    s = re.sub(r"(?<![A-Za-z])([Ee])'(?=\s|$|[?.!,])", lambda m: "È" if m.group(1) == "E" else "è", s)
    s = re.sub(r"\b(?!po')([A-Za-z]+?)([aiou])'(?=\s|$|[?.!,])",
               lambda m: m.group(1) + {"a": "à", "i": "ì", "o": "ò", "u": "ù"}[m.group(2)], s)
    s = re.sub(r"(?<![A-Za-zÀ-ÿ])'|'(?![A-Za-zÀ-ÿ0-9])", " ", s)  # "all'8 dicembre" kept
    s = re.sub(r"(?<=\d)[    ](?=\d{3}\b)", "", s)
    s = re.sub(r"[    ]", " ", s)
    s = re.sub(r"[−‒–—]", "-", s)
    # "1° maggio", "1º maggio": a day, not degrees.
    s = re.sub(rf"\b(\d{{1,2}})\s*[°º]\s*(?={_MONTH_START})", r"\1 ", s, flags=re.IGNORECASE)
    s = s.replace("℉", "°F").replace("℃", "°C")
    s = re.sub(r"\s*°\s*([FCKfck])\b", lambda m: f" gradi {_DEGREES[m.group(1).lower()]}", s)
    s = re.sub(r"\s*°", " gradi", s)
    s = re.sub(r"\b(mm|cm|km|m)²", lambda m: _SQUARE_UNITS[m.group(1)], s)
    s = re.sub(r"\s*²", " al quadrato", s)
    s = re.sub(r"\s*³", " al cubo", s)
    vulgar = "".join(_VULGAR)
    s = re.sub(rf"(\d+)\s*([{vulgar}])", lambda m: _decimal(int(m.group(1)) + _VULGAR[m.group(2)]), s)
    s = re.sub(rf"[{vulgar}]", lambda m: f" {_decimal(_VULGAR[m.group(0)])} ", s)
    s = re.sub(r"\b(\d+)\s+(\d+)/(\d+)\b", lambda m: _decimal(int(m.group(1)) + int(m.group(2)) / int(m.group(3))), s)
    s = re.sub(r"(\d)\s*[x×✕✖]\s*(\d)", r"\1 per \2", s)
    s = re.sub(r"\s*[×✕✖·∙⋅]\s*", " per ", s)
    s = re.sub(r"\s*÷\s*", " diviso ", s)
    s = re.sub(r"√\s*", "radice quadrata di ", s)
    s = re.sub(r"∛\s*", "radice cubica di ", s)
    s = re.sub(r"\s*=\s*\??\s*$", "", s)
    symbols = re.escape("".join(_CURRENCY))
    s = re.sub(rf"(?:US)?([{symbols}])\s*(\d[\d.,]*)", lambda m: f"{m.group(2)} {_CURRENCY[m.group(1)]}", s)
    s = re.sub(rf"(\d)\s*([{symbols}])", lambda m: f"{m.group(1)} {_CURRENCY[m.group(2)]}", s)
    # A unit written on its number: "100km", "5kg".
    s = re.sub(r"(\d)([A-Za-zÀ-ÿ]+)\b", r"\1 \2", s)
    s = re.sub(r"\s+([?.!,])", r"\1", s)
    return re.sub(r"\s+", " ", s).strip()


def clean(q):
    """A question as the grammars read it: lowercase, no lead-in ("senti, mi
    sai dire"), no closing punctuation or "per favore", symbols as words.
    (A "/" between numbers is left: "25/12/2026" is a date; arithmetic reads
    it as "diviso".)"""
    s = q.lower().strip()
    s = re.sub(loose(r"^(?:ehi|ehm|ok|okay|allora|senti|scusa|ciao|beh|dunque|ascolta)[,\s]+"), "", s)
    s = re.sub(loose(
        r"^(?:mi sai dire|sai dirmi|mi puoi dire|puoi dirmi|potresti dirmi|mi potresti dire|mi dici|dimmi|sai"
        r"|per favore|per piacere)[,\s]+"
    ), "", s)
    s = re.sub(loose(r"^(?:calcola|calcolami|mi calcoli|puoi calcolare|potresti calcolare)[,:\s]+"), "", s)
    s = re.sub(r"[?!.]+$", "", s).strip()
    s = re.sub(r",?\s+(?:per favore|per piacere|per cortesia|grazie)$", "", s)
    s = re.sub(r"\s*[?!]+\s*|\.\s+(?=\D)", " ", s)
    s = s.replace("×", " per ").replace("÷", " diviso ").replace("%", " per cento")
    s = re.sub(r"(\d)\s*\*\s*(\d)", r"\1 per \2", s)
    s = re.sub(r"(\d)\s*\+\s*(\d)", r"\1 più \2", s)
    s = re.sub(r"(\d)\s+-\s+(\d)", r"\1 meno \2", s)
    s = re.sub(r"(\d)\s*\^\s*(\d)", r"\1 elevato alla \2", s)
    return re.sub(r"\s+", " ", s).strip()


def verb(answer: str) -> str:
    """"è" for one ("è 1 chilometro"), else "sono" ("sono 8,047 chilometri")."""
    return "è" if re.match(r"-?1(?![\d,])", answer.strip()) else "sono"


def sentence(answer: str | None) -> str | None:
    """"96" -> "Fa 96."; "Sì" -> "Sì."."""
    if answer is None:
        return None
    answer = answer.strip()
    if answer in ("Sì", "No"):
        return answer + "."
    if re.match(r"-?\d", answer):
        return f"Fa {answer}."
    answer = answer[:1].upper() + answer[1:]
    return answer if answer.endswith(".") else answer + "."
