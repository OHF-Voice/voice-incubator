"""How the Russian skills read numbers and questions, with English's names
(voice_call/languages/en/skills/numbers.py).

Russian says a number in words as several ("сто сорок четыре", "две тысячи
двадцать четыре"), each in the case the sentence asks for ("из ста сорока
четырёх", "двадцати") -- read the same whatever the case; a decimal comma
("3,5", "три целых пять десятых"), a half as "с половиной" ("два с
половиной"), "полтора" for one and a half. The model writes "ё" as "е", and
so are questions read (``fold``)."""

from __future__ import annotations

import re

DECIMAL = ","
"""8,047."""

THOUSANDS = ""
"""31536000: said whole, as a voice reads a number written with spaces digit
group by digit group."""


def fold(text: str) -> str:
    """Lowercase, "ё" as "е" (for matching only)."""
    return text.lower().replace("ё", "е")


def _forms(value: int, *words: str) -> dict[str, int]:
    return dict.fromkeys(words, value)


_ONES = {
    **_forms(0, "ноль", "нуль", "ноля", "нуля", "нолю", "нулю", "нолем", "нулем"),
    **_forms(1, "один", "одна", "одно", "одного", "одной", "одному", "одним", "одном", "одну", "одною"),
    **_forms(2, "два", "две", "двух", "двум", "двумя"),
    **_forms(3, "три", "трех", "трем", "тремя"),
    **_forms(4, "четыре", "четырех", "четырем", "четырьмя"),
    **_forms(5, "пять", "пяти", "пятью"),
    **_forms(6, "шесть", "шести", "шестью"),
    **_forms(7, "семь", "семи", "семью"),
    **_forms(8, "восемь", "восьми", "восемью", "восьмью"),
    **_forms(9, "девять", "девяти", "девятью"),
}
_TEENS_AND_TENS = {
    **{f"{stem}{end}": value for value, stem in (
        (10, "десят"), (11, "одиннадцат"), (12, "двенадцат"), (13, "тринадцат"), (14, "четырнадцат"),
        (15, "пятнадцат"), (16, "шестнадцат"), (17, "семнадцат"), (18, "восемнадцат"), (19, "девятнадцат"),
        (20, "двадцат"), (30, "тридцат"),
    ) for end in ("ь", "и", "ью")},
    **_forms(40, "сорок", "сорока"),
    **_forms(50, "пятьдесят", "пятидесяти", "пятьюдесятью"),
    **_forms(60, "шестьдесят", "шестидесяти", "шестьюдесятью"),
    **_forms(70, "семьдесят", "семидесяти", "семьюдесятью"),
    **_forms(80, "восемьдесят", "восьмидесяти", "восемьюдесятью"),
    **_forms(90, "девяносто", "девяноста"),
}
_HUNDREDS = {
    **_forms(100, "сто", "ста"),
    **_forms(200, "двести", "двухсот", "двумстам", "двумястами", "двухстах"),
    **_forms(300, "триста", "трехсот", "тремстам", "тремястами", "трехстах"),
    **_forms(400, "четыреста", "четырехсот", "четыремстам", "четырьмястами", "четырехстах"),
    **_forms(500, "пятьсот", "пятисот", "пятистам", "пятьюстами", "пятистах"),
    **_forms(600, "шестьсот", "шестисот", "шестистам", "шестьюстами", "шестистах"),
    **_forms(700, "семьсот", "семисот", "семистам", "семьюстами", "семистах"),
    **_forms(800, "восемьсот", "восьмисот", "восьмистам", "восемьюстами", "восьмистах"),
    **_forms(900, "девятьсот", "девятисот", "девятистам", "девятьюстами", "девятистах"),
}
_SCALES = {
    **_forms(1000, "тысяча", "тысячи", "тысячу", "тысяч", "тысячей", "тысячам", "тысячами", "тысячах", "тысяче"),
    **_forms(10**6, "миллион", "миллиона", "миллиону", "миллионом", "миллионе", "миллионы", "миллионов",
             "миллионам", "миллионами", "миллионах"),
    **_forms(10**9, "миллиард", "миллиарда", "миллиарду", "миллиардом", "миллиарде", "миллиарды", "миллиардов",
             "миллиардам", "миллиардами", "миллиардах"),
}
_VALUES = {**_ONES, **_TEENS_AND_TENS, **_HUNDREDS}

FRACTIONS = {
    "половина": 0.5, "половину": 0.5, "половины": 0.5, "половиной": 0.5, "пол": 0.5,
    "четверть": 0.25, "четверти": 0.25, "треть": 1 / 3, "трети": 1 / 3,
    "полтора": 1.5, "полторы": 1.5, "полутора": 1.5,
    "три четверти": 0.75, "две трети": 2 / 3, "одна треть": 1 / 3, "одна четверть": 0.25,
}
"""Parts said as words: "четверть", "три четверти", "полтора"."""

_PARTS = {"десят": 10, "сот": 100, "тысячн": 1000, "десятитысячн": 10000}
"""A decimal's part by its word: "пять десятых", "двадцать пять сотых"."""


def _alternatives(words) -> str:
    return "|".join(sorted((re.escape(w) for w in set(words)), key=len, reverse=True))


_WORD = _alternatives([*_VALUES, *_SCALES])
_WORDS = rf"(?:{_WORD})(?:\s+(?:{_WORD}))*"
_PART = r"(?:десятитысячн|десят|сот|тысячн)(?:ых|ая|ой|ую)"
_FRACTION = _alternatives(FRACTIONS)
NUM_RE = (
    r"-?\d+(?:[,.]\d+)?"
    rf"|(?<!\w)(?:минус\s+)?(?:{_WORDS}(?:\s+(?:(?:целых|целая|целой|запятая)\s+{_WORDS}(?:\s+{_PART})?"
    rf"|и\s+{_WORDS}\s+{_PART}|с\s+половиной))?|{_FRACTION})(?!\w)"
)
N = rf"({NUM_RE})"
"""A number in a grammar, as a group."""


def _whole(words: list[str]) -> int | None:
    """A whole number said in words (folded), in any case: "сто сорок
    четыре", "ста сорока четырех", "две тысячи двадцать четыре"."""
    total = current = 0
    seen = False
    for word in words:
        if word in _VALUES:
            current += _VALUES[word]
        elif word in _SCALES:
            total += (current or 1) * _SCALES[word]
            current = 0
        else:
            return None
        seen = True
    return total + current if seen else None


def parse_num(s):
    """A number written or said: "3,5", "1 000", "сто сорок четыре",
    "ста сорока четырех", "три целых пять десятых", "два с половиной",
    "полтора", "три четверти", "минус два"; None."""
    s = " ".join(fold(s.strip()).split())
    if not s:
        return None
    if re.fullmatch(r"-?\d+(?:[,.]\d+)?", s):
        return float(s.replace(",", "."))
    negative = s.startswith("минус ")
    if negative:
        s = s[len("минус "):]
    sign = -1 if negative else 1
    if s in FRACTIONS:
        return sign * FRACTIONS[s]
    if m := re.fullmatch(rf"(.+?)\s+(?:целых|целая|целой|и)\s+(.+?)\s+({_PART})", s):
        whole, part = _whole(m.group(1).split()), _whole(m.group(2).split())
        size = next(v for k, v in sorted(_PARTS.items(), key=lambda kv: -len(kv[0])) if m.group(3).startswith(k))
        if whole is None or part is None:
            return None
        return sign * (whole + part / size)
    if m := re.fullmatch(r"(.+?)\s+(?:целых|целая|целой|запятая)\s+(.+)", s):
        whole, words = _whole(m.group(1).split()), m.group(2).split()
        zeros = 0
        while zeros < len(words) - 1 and words[zeros] in ("ноль", "нуль"):
            zeros += 1
        rest = _whole(words[zeros:])
        if whole is None or rest is None:
            return None
        return sign * (whole + float(f"0.{'0' * zeros}{rest}"))
    if m := re.fullmatch(r"(.+?)\s+с\s+половиной", s):
        whole = _whole(m.group(1).split())
        return None if whole is None else sign * (whole + 0.5)
    whole = _whole(s.split())
    return None if whole is None else sign * whole


_DEGREES = {"f": "Фаренгейта", "c": "Цельсия", "k": "Кельвина"}
_CURRENCY = {"$": "долларов", "€": "евро", "£": "фунтов стерлингов", "¥": "иен", "₽": "рублей"}
_VULGAR = {"½": 0.5, "¼": 0.25, "¾": 0.75, "⅓": 1 / 3, "⅔": 2 / 3, "⅕": 0.2, "⅛": 0.125, "⅜": 0.375, "⅝": 0.625, "⅞": 0.875}
_SQUARE_UNITS = {"мм": "квадратных миллиметров", "см": "квадратных сантиметров", "м": "квадратных метров",
                 "км": "квадратных километров"}


def _decimal(value: float) -> str:
    return f"{round(value, 4):g}".replace(".", ",")


def normalize(text: str) -> str:
    """A question as typed, or written by a speech-to-text, in the words the
    grammars read: "Сколько будет 12 × 8?" -> "Сколько будет 12 умножить на
    8?", "50°F" -> "50 градусов Фаренгейта", "1½ стакана" -> "1,5 стакана",
    "10 м²" -> "10 квадратных метров", "5²" -> "5 в квадрате", "√144" ->
    "квадратный корень из 144", "80 €" -> "80 евро", "1 000 000" (with
    narrow spaces) -> "1000000", "100км" -> "100 км"."""
    s = text
    s = s.translate(str.maketrans({"’": "'", "‘": "'", "‛": "'", "`": "'", "´": "'", "“": '"', "”": '"', "„": '"',
                                   "«": '"', "»": '"'}))
    s = s.replace('"', " ")
    s = re.sub(r"(?<=\d)[    ](?=\d{3}\b)", "", s)
    s = re.sub(r"[    ]", " ", s)
    s = re.sub(r"[−‒–—](?=\s*\d)", "-", s)
    s = s.replace("℉", "°F").replace("℃", "°C")
    s = re.sub(r"\s*°\s*([FCKfckСс])(?![\w])", lambda m: f" градусов {_DEGREES[m.group(1).lower().replace('с', 'c')]}", s)
    s = re.sub(r"\s*°", " градусов", s)
    s = re.sub(r"(мм|см|км|м)²", lambda m: _SQUARE_UNITS[m.group(1)], s)
    s = re.sub(r"\s*²", " в квадрате", s)
    s = re.sub(r"\s*³", " в кубе", s)
    vulgar = "".join(_VULGAR)
    s = re.sub(rf"(\d+)\s*([{vulgar}])", lambda m: _decimal(int(m.group(1)) + _VULGAR[m.group(2)]), s)
    s = re.sub(rf"[{vulgar}]", lambda m: f" {_decimal(_VULGAR[m.group(0)])} ", s)
    s = re.sub(r"(\d)\s*[x×✕✖хХ]\s*(\d)", r"\1 умножить на \2", s)
    s = re.sub(r"\s*[×✕✖·∙⋅]\s*", " умножить на ", s)
    s = re.sub(r"\s*÷\s*", " разделить на ", s)
    s = re.sub(r"√\s*", "квадратный корень из ", s)
    s = re.sub(r"∛\s*", "кубический корень из ", s)
    s = re.sub(r"\s*=\s*\??\s*$", "", s)
    symbols = re.escape("".join(_CURRENCY))
    s = re.sub(rf"([{symbols}])\s*(\d[\d.,]*)", lambda m: f"{m.group(2)} {_CURRENCY[m.group(1)]}", s)
    s = re.sub(rf"(\d)\s*([{symbols}])", lambda m: f"{m.group(1)} {_CURRENCY[m.group(2)]}", s)
    # A unit written on its number: "100км", "5кг" (not "5-го").
    s = re.sub(r"(\d)([A-Za-zА-Яа-яЁё]+)\b", r"\1 \2", s)
    s = re.sub(r"\s+([?.!,])", r"\1", s)
    return re.sub(r"\s+", " ", s).strip()


def clean(q):
    """A question as the grammars read it: lowercase, "ё" as "е", no lead-in
    ("скажи, пожалуйста"), no closing punctuation, symbols as words."""
    s = fold(q).strip()
    s = re.sub(r"^(?:слушай|скажи|скажите|подскажи|подскажите|а|ну|окей|ок|привет|так|эй)[,\s]+", "", s)
    s = re.sub(r"^(?:пожалуйста|будь добр|будьте добры)[,\s]+", "", s)
    s = re.sub(r"^(?:ты не знаешь|не знаешь|не подскажешь|не подскажете|можешь сказать|можешь подсказать|"
               r"ты знаешь|знаешь)[,\s]+", "", s)
    s = re.sub(r"^(?:посчитай|посчитайте|вычисли|вычислите|подсчитай)[,:\s]+", "", s)
    s = re.sub(r"[?!.]+$", "", s).strip()
    s = re.sub(r",?\s+пожалуйста$", "", s)
    s = re.sub(r"\s*[?!]+\s*|\.\s+(?=\D)", " ", s)
    s = s.replace("×", " умножить на ").replace("÷", " разделить на ").replace("%", " процентов")
    s = re.sub(r"(\d)\s*\*\s*(\d)", r"\1 умножить на \2", s)
    s = re.sub(r"(\d)\s*/\s*(\d)", r"\1 разделить на \2", s)
    s = re.sub(r"(\d)\s*\+\s*(\d)", r"\1 плюс \2", s)
    s = re.sub(r"(\d)\s+-\s+(\d)", r"\1 минус \2", s)
    s = re.sub(r"(\d)\s*\^\s*(\d)", r"\1 в степени \2", s)
    return re.sub(r"\s+", " ", s).strip()


def sentence(answer: str | None) -> str | None:
    """"96" -> "Это 96."; "Да" -> "Да."."""
    if answer is None:
        return None
    answer = answer.strip()
    if answer in ("Да", "Нет"):
        return answer + "."
    if re.match(r"-?\d", answer):
        return f"Это {answer}."
    answer = answer[:1].upper() + answer[1:]
    return answer if answer.endswith(".") else answer + "."
