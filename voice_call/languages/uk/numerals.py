"""Ukrainian numbers in words, for what the app says and reads: cardinals
by gender ("одна хвилина", "дві години", "двадцять одну секунду"), ordinals
by gender and case ("сьома", "о сьомій", "двадцять п'ятого грудня"), a
noun's form after a count (``plural``: 1 хвилина, 2 хвилини, 5 хвилин), and
numbers read back from words in any case ("п'ятнадцяти", "сорока").

The pack's Piper voice (Tetiana) reads a written number as a masculine
cardinal, so "21 хвилина" comes out "двадцять один хвилина"; it reads
"7:30" as "сім дві крапки тридцять", a decimal comma not at all ("37,8" is
"тридцять сім вісім"), and numbers from a thousand up wrongly ("1000000" is
"один тисяча"). What the app says writes those in words (``spoken``)."""

from __future__ import annotations

import re

_ONES = {
    "m": ["нуль", "один", "два", "три", "чотири", "п'ять", "шість", "сім", "вісім", "дев'ять"],
    "f": ["нуль", "одна", "дві", "три", "чотири", "п'ять", "шість", "сім", "вісім", "дев'ять"],
    "n": ["нуль", "одне", "два", "три", "чотири", "п'ять", "шість", "сім", "вісім", "дев'ять"],
}
_TEENS = ["десять", "одинадцять", "дванадцять", "тринадцять", "чотирнадцять", "п'ятнадцять", "шістнадцять",
          "сімнадцять", "вісімнадцять", "дев'ятнадцять"]
_TENS = ["", "", "двадцять", "тридцять", "сорок", "п'ятдесят", "шістдесят", "сімдесят", "вісімдесят", "дев'яносто"]
_HUNDREDS = ["", "сто", "двісті", "триста", "чотириста", "п'ятсот", "шістсот", "сімсот", "вісімсот", "дев'ятсот"]
_SCALES = (
    (10**12, ("трильйон", "трильйони", "трильйонів"), "m"),
    (10**9, ("мільярд", "мільярди", "мільярдів"), "m"),
    (10**6, ("мільйон", "мільйони", "мільйонів"), "m"),
    (1000, ("тисяча", "тисячі", "тисяч"), "f"),
)


def plural(count: float, one: str, few: str, many: str, part: str | None = None) -> str:
    """The form of a noun after ``count``: ``one`` after 1, 21, 101 (not 11);
    ``few`` after 2-4, 22-24 (not 12-14); ``many`` after the rest; ``part``
    (the genitive singular, ``few`` unless given) after a fraction: "2,5
    хвилини", "8,047 кілометра"."""
    if count != int(count):
        return part or few
    n = abs(int(count))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def _below_thousand(n: int, gender: str, accusative: bool) -> list[str]:
    words = []
    hundreds, rest = divmod(n, 100)
    if hundreds:
        words.append(_HUNDREDS[hundreds])
    if 10 <= rest < 20:
        words.append(_TEENS[rest - 10])
    else:
        tens, ones = divmod(rest, 10)
        if tens:
            words.append(_TENS[tens])
        if ones:
            word = _ONES[gender][ones]
            words.append("одну" if accusative and word == "одна" else word)
    return words


def cardinal(n: int, gender: str = "m", accusative: bool = False) -> str:
    """``n`` in words, its last word agreeing with a noun of ``gender`` (m,
    f, n): "двадцять одна", "дві тисячі двадцять чотири"; ``accusative``,
    after "на" or "через": "одну", "двадцять одну"."""
    if n < 0:
        return f"мінус {cardinal(-n, gender, accusative)}"
    if n == 0:
        return "нуль"
    words: list[str] = []
    for scale, forms, scale_gender in _SCALES:
        count, n = divmod(n, scale)
        if count:
            if not (count == 1 and scale == 1000):  # "тисяча", not "одна тисяча"
                words += _below_thousand(count, scale_gender, False)
            words.append(plural(count, *forms))
    return " ".join(words + _below_thousand(n, gender, accusative))


def counted(n: int, forms: tuple[str, str, str], gender: str, accusative: bool = False) -> str:
    """A count and its noun, in words: "одна хвилина", "дві години", "п'ять
    секунд"; ``accusative``: "одну хвилину"."""
    one = forms[0]
    if accusative and gender == "f":
        one = re.sub(r"а$", "у", re.sub(r"я$", "ю", one))
    return f"{cardinal(n, gender, accusative)} {plural(n, one, forms[1], forms[2])}"


# ----------------------------------------------------------------- ordinals

_ORDINAL_STEMS = {
    0: "нульов", 1: "перш", 2: "друг", 3: "трет", 4: "четверт", 5: "п'ят", 6: "шост", 7: "сьом", 8: "восьм",
    9: "дев'ят", 10: "десят", 11: "одинадцят", 12: "дванадцят", 13: "тринадцят", 14: "чотирнадцят",
    15: "п'ятнадцят", 16: "шістнадцят", 17: "сімнадцят", 18: "вісімнадцят", 19: "дев'ятнадцят", 20: "двадцят",
    30: "тридцят", 40: "сороков", 50: "п'ятдесят", 60: "шістдесят", 70: "сімдесят", 80: "вісімдесят",
    90: "дев'яност", 100: "сот", 200: "двохсот", 300: "трьохсот", 400: "чотирьохсот", 500: "п'ятисот",
    600: "шестисот", 700: "семисот", 800: "восьмисот", 900: "дев'ятисот",
}
_THOUSANDTH = {1: "", 2: "двох", 3: "трьох", 4: "чотирьох", 5: "п'яти", 6: "шести", 7: "семи", 8: "восьми",
               9: "дев'яти", 10: "десяти"}

ORDINAL_ENDINGS = {
    # hard stem, soft stem ("трет-")
    "m": ("ий", "ій"), "n": ("е", "є"), "f": ("а", "я"), "f_acc": ("у", "ю"), "f_loc": ("ій", "ій"),
    "gen": ("ого", "ього"), "f_gen": ("ої", "ьої"), "loc": ("ому", "ьому"),
}
"""An ordinal's ending by form: masculine, neuter and feminine nominative
("сьомий", "сьоме", "сьома"), feminine accusative and locative ("на сьому",
"о сьомій"), genitive ("сьомого", "сьомої"), masculine locative ("на
сьомому")."""


def ordinal(n: int, form: str = "m") -> str:
    """``n`` as an ordinal in ``form`` (``ORDINAL_ENDINGS``): "двадцять
    п'ятого", "дві тисячі двадцять шостого", "сьомій"."""
    hard, soft = ORDINAL_ENDINGS[form]
    if n >= 1000 and n % 1000 == 0 and n // 1000 in _THOUSANDTH:
        return f"{_THOUSANDTH[n // 1000]}тисячн{hard}"
    head = cardinal(n - n % 1000) if n >= 1000 else ""
    rest = n % 1000
    if rest and rest % 100 == 0:
        stem = _ORDINAL_STEMS[rest]
    else:
        if rest >= 100:
            head = f"{head} {_HUNDREDS[rest // 100]}".strip()
        last = rest % 100
        if last < 20 or last % 10 == 0:
            stem = _ORDINAL_STEMS[last]
        else:
            head = f"{head} {_TENS[last // 10]}".strip()
            stem = _ORDINAL_STEMS[last % 10]
    word = stem + (soft if stem == "трет" else hard)
    return f"{head} {word}".strip()


# ---------------------------------------------------------- what is said

_NUMBER = re.compile(r"(?<![\w,])-?\d{1,3}(?:[\u00a0\u202f]\d{3})+(?:,\d+)?(?![\w,])|(?<![\w,])-?\d+(?:,\d+)?(?![\w])")


def decimal(text: str, gender: str = "m") -> str:
    """A number as written ("8,047", "31 536 000", "-5") in words: "вісім
    кома нуль сорок сім", "тридцять один мільйон п'ятсот тридцять шість
    тисяч"."""
    text = re.sub(r"[\u00a0\u202f]", "", text.strip())
    negative = text.startswith("-")
    whole, _, fraction = text.lstrip("-").partition(",")
    said = cardinal(int(whole), gender)
    if fraction:
        zeros = len(fraction) - len(fraction.lstrip("0"))
        rest = fraction.lstrip("0")
        said += " кома" + " нуль" * zeros + (f" {cardinal(int(rest))}" if rest else "")
    return f"мінус {said}" if negative else said


def spoken(text: str) -> str:
    """A sentence with its numbers as the voice should read them: a decimal
    or a number from a thousand up in words ("8,047" -> "вісім кома нуль
    сорок сім"); others, which it reads right, as they are."""

    def said(match: re.Match[str]) -> str:
        number = match.group(0)
        plain = re.sub(r"[\u00a0\u202f]", "", number)
        if "," in plain or abs(int(plain)) >= 1000:
            return decimal(number)
        return number

    return _NUMBER.sub(said, text)


# ----------------------------------------------------------- read back

def _forms() -> dict[str, int]:
    """Every case of the basic numbers -> its value: "п'ять", "п'яти",
    "п'ятьох", "п'ятьма"; "сорок", "сорока"; "двохсот"."""
    table: dict[str, int] = {}

    def add(value: int, *words: str) -> None:
        for word in words:
            table.setdefault(word, value)

    add(0, "нуль", "нуля", "нулю", "нулем", "ноль")
    add(1, "один", "одна", "одне", "одно", "одну", "одного", "однієї", "одної", "одному", "одній", "одним", "однією",
        "одною")
    add(2, "два", "дві", "двох", "двом", "двома")
    add(3, "три", "трьох", "трьом", "трьома")
    add(4, "чотири", "чотирьох", "чотирьом", "чотирма")
    # 5-20 and 30: -ть in the nominative, -ти/-тьох in the others.
    for value, word in ((5, "п'ять"), (6, "шість"), (7, "сім"), (8, "вісім"), (9, "дев'ять"), (10, "десять"),
                        *((10 + i, w) for i, w in enumerate(_TEENS) if i), (20, "двадцять"), (30, "тридцять"),
                        (50, "п'ятдесят"), (60, "шістдесят"), (70, "сімдесят"), (80, "вісімдесят")):
        add(value, word)
    for value, stem in ((5, "п'ят"), (6, "шест"), (7, "сем"), (8, "восьм"), (9, "дев'ят"), (10, "десят"),
                        *((10 + i, w[:-1]) for i, w in enumerate(_TEENS) if i), (20, "двадцят"), (30, "тридцят"),
                        (50, "п'ятдесят"), (60, "шістдесят"), (70, "сімдесят"), (80, "вісімдесят")):
        add(value, stem + "и", stem + "ьох", stem + "ьом", stem + "ьма", stem + "ьома")
    add(6, "шістьох", "шістьом", "шістьма")
    add(7, "сімох", "сімом", "сьома", "сімома")
    add(8, "вісьмох", "вісьмом", "вісьма", "вісьмома")
    add(40, "сорок", "сорока")
    add(90, "дев'яносто", "дев'яноста")
    add(100, "сто", "ста", "стам", "стами")
    for value, (nominative, genitive) in enumerate(
        [("двісті", "двохсот"), ("триста", "трьохсот"), ("чотириста", "чотирьохсот"), ("п'ятсот", "п'ятисот"),
         ("шістсот", "шестисот"), ("сімсот", "семисот"), ("вісімсот", "восьмисот"), ("дев'ятсот", "дев'ятисот")],
        start=2,
    ):
        add(value * 100, nominative, genitive)
    return table


NUMBER_WORDS = _forms()
"""A number's word, in any case -> its value."""

SCALE_WORDS = {
    **dict.fromkeys(["тисяча", "тисячі", "тисяч", "тисячу", "тисячею", "тисячам", "тисячах"], 1000),
    **dict.fromkeys(["мільйон", "мільйони", "мільйонів", "мільйона", "мільйоні", "мільйоном"], 10**6),
    **dict.fromkeys(["мільярд", "мільярди", "мільярдів", "мільярда"], 10**9),
}


def whole(words: list[str]) -> int | None:
    """A whole number said in words, in any case: "триста сорок п'ять",
    "п'ятнадцяти", "двох тисяч двадцяти чотирьох"; None."""
    total = current = 0
    seen = False
    previous = None
    for word in words:
        if word in NUMBER_WORDS:
            value = NUMBER_WORDS[word]
            # "сто двадцять п'ять": each smaller than what came before.
            if previous is not None and previous <= value:
                return None
            current += value
            previous = value
        elif word in SCALE_WORDS:
            total += (current or 1) * SCALE_WORDS[word]
            current, previous = 0, None
        else:
            return None
        seen = True
    return total + current if seen else None
