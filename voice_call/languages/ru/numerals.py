"""Russian numbers in words, where what is said needs them, and the plural
rule. Piper reads a number written in digits as a masculine count -- "21
минута" as "двадцать один минута", "1 января" as "один января" -- so a count
before a feminine noun ending in one or two, and a date's day and year, are
written out ("двадцать одна минута", "первое января две тысячи двадцать
седьмого года")."""

from __future__ import annotations

_ONES = ("", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять")
_TEENS = ("десять", "одиннадцать", "двенадцать", "тринадцать", "четырнадцать", "пятнадцать", "шестнадцать",
          "семнадцать", "восемнадцать", "девятнадцать")
_TENS = ("", "", "двадцать", "тридцать", "сорок", "пятьдесят", "шестьдесят", "семьдесят", "восемьдесят", "девяносто")
_HUNDREDS = ("", "сто", "двести", "триста", "четыреста", "пятьсот", "шестьсот", "семьсот", "восемьсот", "девятьсот")
_SCALES = ((10**9, ("миллиард", "миллиарда", "миллиардов"), "m"), (10**6, ("миллион", "миллиона", "миллионов"), "m"),
           (1000, ("тысяча", "тысячи", "тысяч"), "f"))


def plural(n: float, one: str, few: str, many: str) -> str:
    """The noun after a count: 1, 21 "минута"; 2-4, 22 "минуты"; 5-20, 25
    "минут". A fraction takes ``few`` (the genitive singular): "1,5 минуты"."""
    if not float(n).is_integer():
        return few
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def _hundreds(n: int, gender: str, case: str) -> list[str]:
    """1 to 999, the last word agreeing: "одна", "одну", "две"."""
    words = [_HUNDREDS[n // 100]] if n >= 100 else []
    rest = n % 100
    if 10 <= rest < 20:
        return [*words, _TEENS[rest - 10]]
    if rest >= 20:
        words.append(_TENS[rest // 10])
    unit = rest % 10
    if unit == 1:
        words.append({"f": "одну" if case == "acc" else "одна", "n": "одно"}.get(gender, "один"))
    elif unit == 2:
        words.append("две" if gender == "f" else "два")
    elif unit:
        words.append(_ONES[unit])
    return [w for w in words if w]


def cardinal(n: int, gender: str = "m", case: str = "nom") -> str:
    """``n`` in words, as a count of a ``gender`` noun ("m", "f", "n") in the
    nominative or (``case`` "acc") the accusative: "двадцать одна",
    "двадцать одну", "две тысячи двадцать четыре"."""
    if n == 0:
        return "ноль"
    if n < 0:
        return "минус " + cardinal(-n, gender, case)
    words: list[str] = []
    for size, names, scale_gender in _SCALES:
        count, n = divmod(n, size)
        if count:
            # "Тысяча девятьсот", not "одна тысяча девятьсот".
            said = [] if count == 1 and size == 1000 else _hundreds(count, scale_gender, case)
            name = plural(count, *names)
            if name == "тысяча" and case == "acc":
                name = "тысячу"
            words += [*said, name]
    if n:
        words += _hundreds(n, gender, case)
    return " ".join(words)


_ORDINAL_ONES = ("", "перв", "втор", "трет", "четвёрт", "пят", "шест", "седьм", "восьм", "девят")
_ORDINAL_TEENS = ("десят", "одиннадцат", "двенадцат", "тринадцат", "четырнадцат", "пятнадцат", "шестнадцат",
                  "семнадцат", "восемнадцат", "девятнадцат")
_ORDINAL_TENS = ("", "", "двадцат", "тридцат", "сороков", "пятидесят", "шестидесят", "семидесят", "восьмидесят",
                 "девяност")
_ORDINAL_HUNDREDS = ("", "сот", "двухсот", "трёхсот", "четырёхсот", "пятисот", "шестисот", "семисот", "восьмисот",
                     "девятисот")
_THOUSANDTHS = ("", "тысячн", "двухтысячн", "трёхтысячн", "четырёхтысячн", "пятитысячн", "шеститысячн",
                "семитысячн", "восьмитысячн", "девятитысячн")
_STRESSED = {"втор", "шест", "седьм", "восьм", "сороков"}
"""Ordinals stressed on the ending: "второй", not "вторый"."""

_ENDINGS = {
    ("m", "nom"): "ый", ("m", "acc"): "ый", ("m", "gen"): "ого", ("m", "prep"): "ом",
    ("n", "nom"): "ое", ("n", "acc"): "ое", ("n", "gen"): "ого", ("n", "prep"): "ом",
    ("f", "nom"): "ая", ("f", "acc"): "ую", ("f", "gen"): "ой", ("f", "prep"): "ой",
}
_THIRD = {
    ("m", "nom"): "третий", ("m", "acc"): "третий", ("m", "gen"): "третьего", ("m", "prep"): "третьем",
    ("n", "nom"): "третье", ("n", "acc"): "третье", ("n", "gen"): "третьего", ("n", "prep"): "третьем",
    ("f", "nom"): "третья", ("f", "acc"): "третью", ("f", "gen"): "третьей", ("f", "prep"): "третьей",
}


def _ordinal_word(stem: str, gender: str, case: str) -> str:
    if stem == "трет":
        return _THIRD[(gender, case)]
    ending = _ENDINGS[(gender, case)]
    if ending == "ый" and stem in _STRESSED:
        ending = "ой"
    return stem + ending


def ordinal(n: int, gender: str = "n", case: str = "nom") -> str:
    """``n`` as an ordinal in words, its last word declined: "первое"
    (января), "двадцать девятое", "две тысячи двадцать седьмого" (года),
    "двухтысячного"."""
    if n <= 0:
        return str(n)
    if n % 1000 == 0 and n < 10**4:
        return _ordinal_word(_THOUSANDTHS[n // 1000], gender, case)
    if n % 100 == 0:
        stem, before = _ORDINAL_HUNDREDS[n % 1000 // 100], n - n % 1000
    elif n % 100 < 20 and n % 100 >= 10:
        stem, before = _ORDINAL_TEENS[n % 100 - 10], n - n % 100
    elif n % 10 == 0:
        stem, before = _ORDINAL_TENS[n % 100 // 10], n - n % 100
    else:
        stem, before = _ORDINAL_ONES[n % 10], n - n % 10
    word = _ordinal_word(stem, gender, case)
    return f"{cardinal(before)} {word}" if before else word
