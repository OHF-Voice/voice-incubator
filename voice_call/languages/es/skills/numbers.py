"""How the Spanish skills read numbers and questions, with English's names
(voice_call/languages/en/skills/numbers.py).

Spanish says a number in words as several ("trescientos cuarenta y cinco",
"dos mil veinticuatro"): its tens and units joined by "y" from thirty on
("treinta y cinco"), as one word below ("veinticinco", "dieciséis"); a
decimal comma ("3,5", "tres coma cinco") and a thousands point ("1.000");
a half as "y medio" ("dos y medio"). A question opens with "¿"; one typed
may leave out its accents ("cuantos"), so the grammars read a question both
ways (``loose``), or without them (``fold``)."""

from __future__ import annotations

import re
import unicodedata

DECIMAL = ","
"""8,047."""

THOUSANDS = ""
"""31536000: said whole, as Spanish writes no separator a voice reads."""


def fold(text: str) -> str:
    """Without accents: "cuántos" -> "cuantos", "año" -> "ano" (for matching
    only)."""
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


_LOOSE = str.maketrans({"á": "[aá]", "é": "[eé]", "í": "[ií]", "ó": "[oó]", "ú": "[uú]"})


def loose(pattern: str) -> str:
    """A grammar written with its accents, read with or without them:
    "cuántos" also matches "cuantos". (Its accented letters must be outside
    any [...].)"""
    return pattern.translate(_LOOSE)


_ONES = {
    "cero": 0, "uno": 1, "una": 1, "un": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7,
    "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15,
    "dieciséis": 16, "diecisiete": 17, "dieciocho": 18, "diecinueve": 19, "veinte": 20, "veintiuno": 21,
    "veintiuna": 21, "veintiún": 21, "veintidós": 22, "veintitrés": 23, "veinticuatro": 24, "veinticinco": 25,
    "veintiséis": 26, "veintisiete": 27, "veintiocho": 28, "veintinueve": 29,
}
_TENS = {"treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60, "setenta": 70, "ochenta": 80, "noventa": 90}
_HUNDREDS = {
    "cien": 100, "ciento": 100, "doscientos": 200, "doscientas": 200, "trescientos": 300, "trescientas": 300,
    "cuatrocientos": 400, "cuatrocientas": 400, "quinientos": 500, "quinientas": 500, "seiscientos": 600,
    "seiscientas": 600, "setecientos": 700, "setecientas": 700, "ochocientos": 800, "ochocientas": 800,
    "novecientos": 900, "novecientas": 900,
}
_SCALES = {"millón": 10**6, "millones": 10**6, "billón": 10**12, "billones": 10**12}
_UNITS = ("uno", "una", "un", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve")

_VALUES = {fold(w): v for table in (_ONES, _TENS, _HUNDREDS) for w, v in table.items()}
_SCALE_VALUES = {fold(w): v for w, v in _SCALES.items()}
_FOLDED_TENS = {fold(w) for w in _TENS}
_FOLDED_UNITS = {fold(w) for w in _UNITS}

FRACTIONS = {
    "medio": 0.5, "media": 0.5, "un medio": 0.5, "un cuarto": 0.25, "cuarto": 0.25, "tres cuartos": 0.75,
    "un tercio": 1 / 3, "tercio": 1 / 3, "dos tercios": 2 / 3, "un décimo": 0.1, "décimo": 0.1,
}
"""Parts said as words: "un cuarto", "tres cuartos"."""

_FOLDED_FRACTIONS = {fold(f): v for f, v in FRACTIONS.items()}


def _alternatives(words) -> str:
    every = {w for word in words for w in (word, fold(word))}
    return "|".join(sorted((re.escape(w) for w in every), key=len, reverse=True))


_WORD = _alternatives([*_ONES, *_HUNDREDS, *_SCALES, "mil"])
_TEN = _alternatives(_TENS)
_UNIT = _alternatives(_UNITS)
_TOKEN = rf"(?:(?:{_TEN})(?:\s+y\s+(?:{_UNIT}))?|{_WORD})"
_WORDS = rf"{_TOKEN}(?:\s+{_TOKEN})*"
_FRACTION = _alternatives(FRACTIONS)
NUM_RE = (
    r"-?\d{1,3}(?:\.\d{3})+(?:,\d+)?|-?\d+(?:[,.]\d+)?"
    rf"|(?<!\w)(?:(?:menos|negativo)\s+)?(?:{_WORDS}(?:\s+(?:coma\s+{_WORDS}|y\s+(?:medio|media|cuarto)))?|{_FRACTION})(?!\w)"
)
N = rf"({NUM_RE})"
"""A number in a grammar, as a group."""


def _whole(words: list[str]) -> int | None:
    """A whole number said in words (folded): "trescientos cuarenta y cinco",
    "dos mil veinticuatro", "un millón"."""
    total = current = 0
    seen = False
    for index, word in enumerate(words):
        if word == "y":
            # Only between tens and a unit: "treinta y cinco".
            before = words[index - 1] if index else ""
            after = words[index + 1] if index + 1 < len(words) else ""
            if before not in _FOLDED_TENS or after not in _FOLDED_UNITS:
                return None
            continue
        if word in _VALUES:
            current += _VALUES[word]
        elif word == "mil":
            current = (current or 1) * 1000
        elif word in _SCALE_VALUES:
            total += (current or 1) * _SCALE_VALUES[word]
            current = 0
        else:
            return None
        seen = True
    return total + current if seen else None


def parse_num(s):
    """A number written or said: "1.000", "3,5", "trescientos cuarenta y
    cinco", "tres coma cinco", "dos y medio", "tres cuartos", "menos dos";
    None."""
    s = fold(s.strip().lower())
    if not s:
        return None
    if re.fullmatch(r"-?\d{1,3}(?:\.\d{3})+(?:,\d+)?", s):
        return float(s.replace(".", "").replace(",", "."))
    if re.fullmatch(r"-?\d+(?:[,.]\d+)?", s):
        return float(s.replace(",", "."))
    negative = bool(re.match(r"(?:menos|negativo)\s+", s))
    if negative:
        s = s.split(None, 1)[1]
    sign = -1 if negative else 1
    if s in _FOLDED_FRACTIONS:
        return sign * _FOLDED_FRACTIONS[s]
    if m := re.fullmatch(r"(.+?)\s+coma\s+(.+)", s):
        whole, words = _whole(m.group(1).split()), m.group(2).split()
        zeros = 0
        while zeros < len(words) - 1 and words[zeros] == "cero":
            zeros += 1
        rest = _whole(words[zeros:])
        if whole is None or rest is None:
            return None
        return sign * (whole + float(f"0.{'0' * zeros}{rest}"))
    if m := re.fullmatch(r"(.+?)\s+y\s+(medio|media|cuarto)", s):
        whole = _whole(m.group(1).split())
        return None if whole is None else sign * (whole + (0.25 if m.group(2) == "cuarto" else 0.5))
    if m := re.fullmatch(r"(.+?)\s+(cuartos?|tercios?|decimos?)", s):
        count = _whole(m.group(1).split())
        part = {"cuarto": 0.25, "tercio": 1 / 3, "decimo": 0.1}[m.group(2).rstrip("s")]
        return None if count is None else sign * count * part
    whole = _whole(s.split())
    return None if whole is None else sign * whole


_DEGREES = {"f": "Fahrenheit", "c": "Celsius", "k": "Kelvin"}
_CURRENCY = {"$": "dólares", "€": "euros", "£": "libras", "¥": "yenes"}
_VULGAR = {"½": 0.5, "¼": 0.25, "¾": 0.75, "⅓": 1 / 3, "⅔": 2 / 3, "⅕": 0.2, "⅛": 0.125, "⅜": 0.375, "⅝": 0.625, "⅞": 0.875}
_SQUARE_UNITS = {"mm": "milímetros cuadrados", "cm": "centímetros cuadrados", "m": "metros cuadrados",
                 "km": "kilómetros cuadrados"}


def _decimal(value: float) -> str:
    return f"{round(value, 4):g}".replace(".", ",")


def normalize(text: str) -> str:
    """A question as typed, or written by a speech-to-text, in the words the
    grammars read: "¿Cuánto es 12 × 8?" -> "Cuánto es 12 por 8?", "50°F" ->
    "50 grados Fahrenheit", "1½ tazas" -> "1,5 tazas", "10 m²" -> "10 metros
    cuadrados", "5²" -> "5 al cuadrado", "√144" -> "raíz cuadrada de 144",
    "80 €" -> "80 euros", "100km" -> "100 km"."""
    s = text
    s = s.translate(str.maketrans({"’": "'", "‘": "'", "‛": "'", "`": "'", "´": "'", "“": '"', "”": '"', "„": '"',
                                   "«": '"', "»": '"', "¿": " ", "¡": " "}))
    s = s.replace('"', " ")
    s = re.sub(r"(?<![A-Za-zÀ-ÿ])'|'(?![A-Za-zÀ-ÿ])", " ", s)
    s = re.sub(r"[    ]", " ", s)
    s = re.sub(r"[−‒–—]", "-", s)
    s = s.replace("℉", "°F").replace("℃", "°C")
    s = re.sub(r"\s*°\s*([FCKfck])\b", lambda m: f" grados {_DEGREES[m.group(1).lower()]}", s)
    s = re.sub(r"\s*°", " grados", s)
    s = re.sub(r"\b(mm|cm|km|m)²", lambda m: _SQUARE_UNITS[m.group(1)], s)
    s = re.sub(r"\s*²", " al cuadrado", s)
    s = re.sub(r"\s*³", " al cubo", s)
    vulgar = "".join(_VULGAR)
    s = re.sub(rf"(\d+)\s*([{vulgar}])", lambda m: _decimal(int(m.group(1)) + _VULGAR[m.group(2)]), s)
    s = re.sub(rf"[{vulgar}]", lambda m: f" {_decimal(_VULGAR[m.group(0)])} ", s)
    s = re.sub(r"\b(\d+)\s+(\d+)/(\d+)\b", lambda m: _decimal(int(m.group(1)) + int(m.group(2)) / int(m.group(3))), s)
    s = re.sub(r"(\d)\s*[x×✕✖]\s*(\d)", r"\1 por \2", s)
    s = re.sub(r"\s*[×✕✖·∙⋅]\s*", " por ", s)
    s = re.sub(r"\s*÷\s*", " entre ", s)
    s = re.sub(r"√\s*", "raíz cuadrada de ", s)
    s = re.sub(r"∛\s*", "raíz cúbica de ", s)
    s = re.sub(r"\s*=\s*\??\s*$", "", s)
    symbols = re.escape("".join(_CURRENCY))
    s = re.sub(rf"(?:US)?([{symbols}])\s*(\d[\d.,]*)", lambda m: f"{m.group(2)} {_CURRENCY[m.group(1)]}", s)
    s = re.sub(rf"(\d)\s*([{symbols}])", lambda m: f"{m.group(1)} {_CURRENCY[m.group(2)]}", s)
    # A unit written on its number: "100km", "5kg".
    s = re.sub(r"(\d)([A-Za-zÀ-ÿ]+)\b", r"\1 \2", s)
    s = re.sub(r"\s+([?.!,])", r"\1", s)
    return re.sub(r"\s+", " ", s).strip()


def clean(q):
    """A question as the grammars read it: lowercase, no lead-in ("oye,
    ¿me puedes decir"), no closing punctuation or "por favor", symbols as
    words."""
    s = q.lower().strip()
    s = re.sub(r"^[¿¡\s]+", "", s)
    s = re.sub(loose(r"^(?:oye|hola|vale|ok|okey|eh|bueno|a ver|mira|perdona)[,\s]+"), "", s)
    s = re.sub(loose(
        r"^(?:me puedes decir,? |puedes decirme,? |me podrías decir,? |podrías decirme,? |sabes,? |dime,? "
        r"|por favor,? )?(?:calcula,? |calcúlame,? )?"
    ), "", s)
    s = re.sub(r"[?!.]+$", "", s).strip()
    s = re.sub(r",?\s+por favor$", "", s)
    s = re.sub(r"\s*[?!¿¡]+\s*|\.\s+(?=\D)", " ", s)
    s = s.replace("×", " por ").replace("÷", " entre ").replace("%", " por ciento")
    s = re.sub(r"(\d)\s*\*\s*(\d)", r"\1 por \2", s)
    s = re.sub(r"(\d)\s*/\s*(\d)", r"\1 entre \2", s)
    s = re.sub(r"(\d)\s*\+\s*(\d)", r"\1 más \2", s)
    s = re.sub(r"(\d)\s+-\s+(\d)", r"\1 menos \2", s)
    s = re.sub(r"(\d)\s*\^\s*(\d)", r"\1 elevado a \2", s)
    return re.sub(r"\s+", " ", s).strip()


def verb(answer: str) -> str:
    """"es" for one ("es 1", "es 1 kilómetro"), else "son" ("son 96")."""
    return "es" if re.match(r"-?1(?![\d,])", answer.strip()) else "son"


def sentence(answer: str | None) -> str | None:
    """"96" -> "Son 96."; "1 día" -> "Es 1 día."; "Sí" -> "Sí."."""
    if answer is None:
        return None
    answer = answer.strip()
    if answer in ("Sí", "No"):
        return answer + "."
    if re.match(r"-?\d", answer):
        return f"{verb(answer).capitalize()} {answer}."
    answer = answer[:1].upper() + answer[1:]
    return answer if answer.endswith(".") else answer + "."
