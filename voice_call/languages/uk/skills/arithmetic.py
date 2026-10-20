"""How Ukrainian asks a sum ("скільки буде дванадцять помножити на вісім", "15
відсотків від 80", "квадратний корінь із 144", "середнє з чотирьох, восьми і
дванадцяти", "п'ять у кубі") and says its answer (voice_call/skills/
arithmetic.py).

Numbers after "з", "від" or "із" are in the genitive ("зі ста сорока
чотирьох"), read as any other. Said back, a sum "буде" its answer:
"Дванадцять помножити на вісім буде 96" ("дорівнює" would want the
dative)."""

from __future__ import annotations

import math
import re
from functools import partial

from voice_call.skills import common
from voice_call.skills.arithmetic import Settings
from voice_call.skills.common import DIGITS, reply

from ..numerals import ordinal
from .numbers import FRACTIONS, N, clean, counted, parse_num, said

EXAMPLES = ("скільки буде дванадцять помножити на вісім", "скільки буде 15 відсотків від 80", "квадратний корінь із 144")

POWERS = {ordinal(n, "loc"): n for n in range(2, 21)}
"""The power said by its ordinal: "у третьому степені"."""

_OPERATORS = (
    (r"\b(?:помножити|помножене|помножений|множити|помножимо|перемножити) на\b|\bразів по\b|(?<=\d) [xх] (?=\d)", "*"),
    (r"\b(?:поділити|поділене|поділений|ділити|розділити|розділене|поділимо) на\b", "/"),
    (r"\b(?:додати|плюс)\b", "+"),
    (r"(?<=\S) (?:мінус|відняти) (?=\S)", " - "),
)
_ASKED = re.compile(
    r"^(?:скільки (?:буде|це|становить|дорівнює|вийде|виходить)|скільки|чому (?:дорівнює|дорівнюють)|що буде|"
    r"який результат|яка відповідь)\s+"
)


def restated(q: str) -> str:
    """What is asked, as asked, without "скільки буде": "дванадцять помножити
    на вісім"."""
    s = _ASKED.sub("", clean(q))
    # "дванадцять помножити на вісім, скільки буде": asked after.
    return re.sub(r"^(.+?),? (?:скільки (?:це|буде)|скільки|чому дорівнює)$", r"\1", s).strip()


def asked(q: str) -> str:
    """What is asked, its operators as signs: "дванадцять * вісім"."""
    s = restated(q)
    for pattern, sign in _OPERATORS:
        s = re.sub(pattern, sign if sign.startswith(" ") else f" {sign} ", s)
    # "12 на 8": a product.
    s = re.sub(rf"^{N} на {N}$", r"\1 * \2", s)
    return " ".join(s.split())


def _items(text: str) -> list[str]:
    """A list's numbers: "чотирьох, восьми і дванадцяти"."""
    return [p.strip() for p in re.split(r",|\s+(?:і|та|й)\s+", text) if p.strip()]


def solve(q, places=DIGITS):
    s = asked(q)
    fmt = partial(common.fmt, places=places)

    def match(pattern):
        return re.fullmatch(pattern, s)

    if m := match(rf"(?:кубічний корінь|корінь кубічний|корінь третього степеня) (?:з|із|зі) {N}"):
        v = parse_num(m.group(1))
        return None if v is None else fmt(round(math.copysign(abs(v) ** (1 / 3), v), 6))
    if m := match(rf"(?:квадратний корінь|корінь квадратний|корінь) (?:з|із|зі) {N}"):
        v = parse_num(m.group(1))
        return None if v is None or v < 0 else fmt(math.sqrt(v))
    if m := match(rf"факторіал (?:числа )?{N}|{N} факторіал"):
        v = parse_num(m.group(1) or m.group(2))
        return fmt(math.factorial(int(v))) if v is not None and v.is_integer() and 0 <= v <= 20 else None
    if m := match(rf"{N} (?:у|в) квадраті|квадрат (?:числа )?{N}"):
        v = parse_num(m.group(1) or m.group(2))
        return None if v is None else fmt(v**2)
    if m := match(rf"{N} (?:у|в) кубі|куб (?:числа )?{N}"):
        v = parse_num(m.group(1) or m.group(2))
        return None if v is None else fmt(v**3)
    if m := match(rf"{N} (?:у|в) (\S+) степені"):
        v, e = parse_num(m.group(1)), POWERS.get(m.group(2))
        if v is not None and e is not None:
            return fmt(v**e)
    if m := match(rf"{N} (?:у|в) степені {N}"):
        v, e = parse_num(m.group(1)), parse_num(m.group(2))
        return None if v is None or e is None or e > 64 else fmt(v**e)
    if m := match(rf"{N} (?:відсотків|відсотки|відсоток|відсотка) (?:від|з|із) {N}"):
        a, b = parse_num(m.group(1)), parse_num(m.group(2))
        return None if a is None or b is None else fmt(round(a * b / 100, 6))
    if m := match(rf"(половина|половину|чверть|третина|третину|дві третини|три чверті|десята частина|десяту частину) (?:від |з |із )?{N}"):
        b = parse_num(m.group(2))
        return None if b is None else fmt(round(FRACTIONS[m.group(1)] * b, 6))
    if m := match(r"середнє(?: арифметичне| значення)?(?: (?:з|із|для|чисел))? (.+)"):
        vals = [parse_num(p) for p in _items(m.group(1))]
        if len(vals) >= 2 and None not in vals:
            return fmt(round(sum(vals) / len(vals), 6))
        return None
    if m := match(r"сума(?: чисел)? (.+)"):
        vals = [parse_num(p) for p in _items(m.group(1))]
        return fmt(sum(vals)) if len(vals) >= 2 and None not in vals else None
    if re.search(r"відсот", s):
        return None
    # Chains of + - * /.
    tokens = re.split(r"\s+([-+*/])\s+", s)
    if len(tokens) >= 3 and len(tokens) % 2 == 1:
        vals = [parse_num(t) for t in tokens[0::2]]
        if None in vals:
            return None
        expr = "".join(f"{v!r}{o}" for v, o in zip(vals, tokens[1::2])) + repr(vals[-1])
        try:
            return fmt(round(eval(expr, {"__builtins__": {}}), 6))  # expr only has numbers and + - * /
        except ZeroDivisionError:
            return None
    return None


MONEY = re.compile(r"(?<![\w'])(гривн\w*|гривен\w*|грн|долар\w*|євро|цент\w*|копій\w*|копійк\w*|злот\w*|єн[аиу]?)(?![\w'])",
                   re.IGNORECASE)
_MONEY = {
    "гривн": (("гривня", "гривні", "гривень", "гривні"), "f"),
    "грн": (("гривня", "гривні", "гривень", "гривні"), "f"),
    "гривен": (("гривня", "гривні", "гривень", "гривні"), "f"),
    "долар": (("долар", "долари", "доларів", "долара"), "m"),
    "євро": (("євро", "євро", "євро", "євро"), "n"),
    "цент": (("цент", "центи", "центів", "цента"), "m"),
    "копій": (("копійка", "копійки", "копійок", "копійки"), "f"),
    "злот": (("злотий", "злоті", "злотих", "злотого"), "m"),
    "єн": (("єна", "єни", "єн", "єни"), "f"),
}


def _money(word: str) -> str:
    """"доларів" -> "долар": the money's own."""
    word = word.lower()
    return next((k for k in sorted(_MONEY, key=len, reverse=True) if word.startswith(k)), word)


def answer(text: str, settings: Settings):
    """With money in it ("15 % від 80 ₴"), in that money: "Це 12 гривень."."""
    money = {_money(m) for m in MONEY.findall(text)}
    if len(money) > 1:
        return None  # a conversion: not arithmetic
    plain = MONEY.sub(" ", text) if money else text
    found = solve(plain, settings.digits)
    if found is None:
        return None
    spoken = said(found)
    if money:
        forms, gender = _MONEY[money.pop()]
        found, spoken = counted(found, forms, gender)
    expression = said(restated(plain))
    return reply(found, text, f"{expression} буде {spoken}" if expression else None,
                 brief=f"{spoken[:1].upper()}{spoken[1:]}.", specific=f"Це {spoken}.")
