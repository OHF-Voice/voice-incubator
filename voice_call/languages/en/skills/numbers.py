"""How the English skills read numbers and questions: numbers said in words
("ninety eight point six", "twenty twenty four"), a question normalized
("12 × 8", "50°F", "$80" in words) and cleaned up for the grammars, and an
answer as a sentence ("It's 96.").

Every language's skills/numbers.py has these names; voice_call/skills uses
``normalize``, ``sentence``, ``DECIMAL`` and ``THOUSANDS``, its grammars the
rest."""

from __future__ import annotations

import calendar
import re

DECIMAL = "."
"""Between a number's whole part and its fraction, as said: 8.047."""

THOUSANDS = ","
"""Between a whole number's thousands (from 10,000), as said: 31,536,000."""

_UNITS = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
          "sixteen seventeen eighteen nineteen").split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()
WORD_NUM = {w: i for i, w in enumerate(_UNITS)} | {w: 10 * i for i, w in enumerate(_TENS) if w != "_"}
SCALES = {"hundred": 100, "thousand": 1000, "million": 10**6}
FRACTIONS = {"half": 0.5, "a half": 0.5, "one half": 0.5, "quarter": 0.25, "a quarter": 0.25, "third": 1 / 3,
             "a third": 1 / 3, "one third": 1 / 3, "three quarters": 0.75, "two thirds": 2 / 3, "a tenth": 0.1}
ORDINAL_POW = {"square": 2, "squared": 2, "second": 2, "cube": 3, "cubed": 3, "third": 3, "fourth": 4, "fifth": 5,
               "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10}

NUM_RE = r"-?\d[\d,]*(?:\.\d+)?|(?:(?:a|an|zero|oh|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|and|half|quarter|negative|minus|point)\b[\s-]*)+"
N = rf"({NUM_RE})"
"""A number in a grammar, as a group."""


def parse_num(s):
    s = s.strip().lower().replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        pass
    neg = s.startswith(("negative ", "minus "))
    if neg:
        s = s.split(" ", 1)[1]
    if m := re.fullmatch(r"(.*?)\s*\bpoint\s+(.+)", s):
        # "ninety eight point six", "point five": digits after the point.
        whole = parse_num(m.group(1)) if m.group(1) else 0
        digits = [w if w.isdigit() else str(WORD_NUM.get(w, 0 if w == "oh" else -1)) for w in m.group(2).split()]
        if whole is None or any(not d.isdigit() for d in digits):
            return None
        return (-1 if neg else 1) * (whole + float("0." + "".join(digits)))
    # "one and a half"; a transcript may drop the "and".
    m = re.fullmatch(r"(.+?)\s+(?:and\s+)?(a half|a quarter|three quarters)", s)
    if m:
        a = parse_num(m.group(1))
        return None if a is None else (-1 if neg else 1) * (a + FRACTIONS[m.group(2)])
    if s in FRACTIONS:
        return FRACTIONS[s]
    year = _spoken_year(s)
    if year is not None:
        return year
    total = cur = 0
    seen = False
    for w in re.split(r"[\s-]+", s):
        if w in ("and",):
            continue
        if w in ("a", "an"):
            cur = cur or 1
            seen = True
        elif w in WORD_NUM:
            cur += WORD_NUM[w]
            seen = True
        elif w == "hundred":
            cur = (cur or 1) * 100
            seen = True
        elif w in SCALES:
            total += (cur or 1) * SCALES[w]
            cur = 0
            seen = True
        else:
            return None
    if not seen:
        return None
    return (-1 if neg else 1) * (total + cur)


def _spoken_year(s):
    """"twenty twenty four" -> 2024, "nineteen eighty four" -> 1984, "twenty
    oh five" -> 2005: a year said in pairs, as a transcript writes it."""
    words = re.split(r"[\s-]+", s)
    if len(words) < 2 or words[0] not in WORD_NUM or not 10 <= WORD_NUM[words[0]] <= 20:
        return None
    rest = words[1:]
    if rest[0] == "oh" and len(rest) == 2 and rest[1] in WORD_NUM and WORD_NUM[rest[1]] < 10:
        return WORD_NUM[words[0]] * 100 + WORD_NUM[rest[1]]
    if rest[0] not in WORD_NUM or WORD_NUM[rest[0]] < 10:
        return None
    low = WORD_NUM[rest[0]] + (WORD_NUM[rest[1]] if len(rest) == 2 and WORD_NUM.get(rest[1], 10) < 10 else 0)
    if len(rest) > 2 or (len(rest) == 2 and WORD_NUM.get(rest[1], 10) >= 10) or WORD_NUM[rest[0]] % 10 and len(rest) == 2:
        return None
    return WORD_NUM[words[0]] * 100 + low


_DEGREES = {"f": "fahrenheit", "c": "celsius", "k": "kelvin"}
_CURRENCY = {"$": "dollars", "€": "euros", "£": "pounds", "¥": "yen"}
_VULGAR = {"½": 0.5, "¼": 0.25, "¾": 0.75, "⅓": 1 / 3, "⅔": 2 / 3, "⅕": 0.2, "⅛": 0.125, "⅜": 0.375, "⅝": 0.625, "⅞": 0.875}
_FRACTION_WORDS = {0.5: "a half", 0.25: "a quarter", 0.75: "three quarters", 1 / 3: "a third", 2 / 3: "two thirds"}
_SQUARE_UNITS = {"mm": "millimeters", "cm": "centimeters", "m": "meters", "km": "kilometers", "in": "inches",
                 "ft": "feet", "yd": "yards", "mi": "miles"}
_OPERATORS = r"(?:plus|minus|times|divided|over|x|and|to)\b"


def normalize(text: str) -> str:
    """A question as typed, or written out by a speech-to-text that formats
    what it hears, in the words the skills read: "What’s" -> "What's",
    "50°F" -> "50 degrees fahrenheit", "−5" -> "-5", "12 × 8" -> "12 times
    8", "1½ cups" -> "1.5 cups", "½ cup" -> "a half cup", "5²" -> "5
    squared", "10 m²" -> "10 square meters", "√144" -> "square root of 144",
    "$80" -> "80 dollars", "100km" -> "100 km"."""
    s = text
    # Quotes and apostrophes: an apostrophe in a word stays, quotes go.
    s = s.translate(str.maketrans({"’": "'", "‘": "'", "‛": "'", "`": "'", "´": "'", "“": '"', "”": '"', "„": '"'}))
    s = s.replace('"', " ")
    s = re.sub(r"(?<![A-Za-z])'|'(?![A-Za-z])", " ", s)
    s = re.sub(r"[\u00a0\u2007\u2009\u202f]", " ", s)
    # A minus, or a dash used as one.
    s = re.sub(r"[\u2212\u2012\u2013\u2014]", "-", s)
    # Degrees.
    s = s.replace("℉", "°F").replace("℃", "°C")
    s = re.sub(r"\s*°\s*([FCKfck])\b", lambda m: f" degrees {_DEGREES[m.group(1).lower()]}", s)
    s = re.sub(r"\s*°", " degrees", s)
    # Squares and cubes: of a unit, or of a number.
    s = re.sub(r"\b(mm|cm|km|m|in|ft|yd|mi)²", lambda m: f"square {_SQUARE_UNITS[m.group(1)]}", s)
    s = re.sub(r"\s*²", " squared", s)
    s = re.sub(r"\s*³", " cubed", s)
    # Fractions: 1½ and 1 1/2 are numbers; ½ alone is said.
    vulgar = "".join(_VULGAR)
    s = re.sub(rf"(\d+)\s*([{vulgar}])", lambda m: _decimal(int(m.group(1)) + _VULGAR[m.group(2)]), s)
    s = re.sub(rf"[{vulgar}]", lambda m: f" {_said_fraction(_VULGAR[m.group(0)])} ", s)
    s = re.sub(r"\b(\d+)\s+(\d+)/(\d+)\b", lambda m: _decimal(int(m.group(1)) + int(m.group(2)) / int(m.group(3))), s)
    # 1/2 before a word (not an operator): "1/2 cup", "1/2 of 10".
    s = re.sub(
        rf"(?<![\d/.])\b(\d+)/(\d+)\b(?![/\d])(?=\s+(?!{_OPERATORS})[a-z])",
        lambda m: f"{_said_fraction(int(m.group(1)) / int(m.group(2)))}" if int(m.group(2)) else m.group(0),
        s,
        flags=re.I,
    )
    # Operators.
    s = re.sub(r"(\d)\s*[x×✕✖]\s*(\d)", r"\1 times \2", s)
    s = re.sub(r"\s*[×✕✖·∙⋅]\s*", " times ", s)
    s = re.sub(r"\s*÷\s*", " divided by ", s)
    s = re.sub(r"√\s*", "square root of ", s)
    s = re.sub(r"∛\s*", "cube root of ", s)
    s = re.sub(r"\s*=\s*\??\s*$", "", s)
    # Money: "$80" -> "80 dollars", "80 €" -> "80 euros".
    symbols = re.escape("".join(_CURRENCY))
    s = re.sub(rf"(?:US)?([{symbols}])\s*(\d[\d,]*(?:\.\d+)?)", lambda m: f"{m.group(2)} {_CURRENCY[m.group(1)]}", s)
    s = re.sub(rf"(\d)\s*([{symbols}])", lambda m: f"{m.group(1)} {_CURRENCY[m.group(2)]}", s)
    # A unit written on its number: "100km", "5lbs", "50F" (not "4th", "3pm").
    s = re.sub(r"(\d)(?!(?:st|nd|rd|th|am|pm)\b)([A-Za-z]+)\b", r"\1 \2", s)
    s = re.sub(r"\s+([?.!,])", r"\1", s)
    return re.sub(r"\s+", " ", s).strip()


def _decimal(value: float) -> str:
    return f"{round(value, 4):g}"


def _said_fraction(value: float) -> str:
    for known, said in _FRACTION_WORDS.items():
        if abs(value - known) < 1e-9:
            return said
    return _decimal(value)


def clean(q):
    """A question as the grammars read it: lowercase, no lead-in ("hey, can you
    tell me"), no closing punctuation, symbols as words."""
    s = q.lower().strip()
    s = re.sub(r"^(hey|ok|okay|so|um|uh)[,\s]+", "", s)
    s = re.sub(r"^(can you |could you |please |would you )?(tell me |calculate |compute |work out |figure out )?", "", s)
    s = re.sub(r"[?!.]+$", "", s).strip()
    # A transcript breaks a spoken question up: "the average of four? eight and twelve".
    s = re.sub(r"\s*[?!]+\s+|\.\s+", " ", s)
    s = s.replace("×", " times ").replace("÷", " divided by ").replace("%", " percent")
    s = re.sub(r"(\d)\s*\*\s*(\d)", r"\1 times \2", s)
    # Not a date's slashes: "25/12/2026".
    s = re.sub(r"(?<![\d/])(\d+)\s*/\s*(\d+)(?![\d/])", r"\1 divided by \2", s)
    s = re.sub(r"(\d)\s*\+\s*(\d)", r"\1 plus \2", s)
    s = re.sub(r"(\d)\s+-\s+(\d)", r"\1 minus \2", s)
    s = re.sub(r"(\d)\s*\^\s*(\d)", r"\1 to the power of \2", s)
    return re.sub(r"\s+", " ", s)


def sentence(answer: str | None) -> str | None:
    """"96" -> "It's 96."; "Yes" -> "Yes."."""
    if answer is None:
        return None
    answer = answer.strip()
    if answer in ("Yes", "No"):
        return answer + "."
    first = answer.split(" ")[0].rstrip(",")
    if answer[:1].isdigit() or answer[:1] == "-" or first in calendar.month_name or first in calendar.day_name:
        return f"It's {answer}."
    return answer if answer.endswith(".") else answer + "."
