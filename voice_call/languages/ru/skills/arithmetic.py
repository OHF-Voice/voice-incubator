"""How Russian asks a sum ("сколько будет двенадцать умножить на восемь?",
"пятнадцать процентов от восьмидесяти", "квадратный корень из ста сорока
четырёх", "среднее чисел четыре, восемь и двенадцать", "два в десятой
степени") and says its answer (voice_call/skills/arithmetic.py).

"На" multiplies on its own ("двенадцать на восемь") and in "умножить на",
"разделить на" divides; a number is read in whatever case it is said in
("от восьмидесяти"). The sum is said back as asked: "Двенадцать умножить на
восемь будет 96"."""

from __future__ import annotations

import math
import re
from functools import partial

from voice_call.skills import common
from voice_call.skills.arithmetic import Settings
from voice_call.skills.common import DIGITS, reply

from ..numerals import plural
from .numbers import FRACTIONS, N, clean, parse_num

EXAMPLES = ("сколько будет двенадцать умножить на восемь", "пятнадцать процентов от восьмидесяти",
            "квадратный корень из ста сорока четырех")

POWERS = {
    "вторую": 2, "второй": 2, "третью": 3, "третьей": 3, "четвертую": 4, "четвертой": 4, "пятую": 5, "пятой": 5,
    "шестую": 6, "шестой": 6, "седьмую": 7, "седьмой": 7, "восьмую": 8, "восьмой": 8, "девятую": 9, "девятой": 9,
    "десятую": 10, "десятой": 10,
}
"""The power said by its ordinal: "в третьей степени"."""

_LEAD = re.compile(
    r"^(?:сколько (?:будет|это будет|это|получится|выйдет)|сколько|чему (?:равно|равен|равна|равны|будет равно)"
    r"|какой результат|каков результат|найди|найдите) "
)
_TAIL = re.compile(r",? (?:сколько (?:будет|это)|будет|равно|это сколько|получится)$")
_OPS = [
    (r"\b(?:умножить|умноженное|умножь|помножить|помноженное) на\b|\bраз(?:а)? по\b|\bx\b", "умн"),
    (r"\b(?:разделить|разделенное|поделить|поделенное|делить|деленное|раздели|подели) на\b", "дел"),
    (r"\bприбавить\b|\bплюс\b|\bсложить с\b", "плюс"),
    (r"\bотнять\b|\bвычесть\b|\bминус\b", "минус"),
]


def asked(q):
    """What is asked, without "сколько будет": "двенадцать умножить на
    восемь"."""
    s = clean(q)
    s = _LEAD.sub("", s)
    s = _TAIL.sub("", s)
    return s.strip()


def _expression(s: str) -> str:
    """The operators as one word each: "двенадцать умн восемь"; "на"
    between two numbers multiplies."""
    for pattern, word in _OPS:
        s = re.sub(pattern, word, s)
    return re.sub(rf"(?<=\w) на (?={N})", " умн ", s)


def _items(text: str) -> list[str]:
    """A list's numbers: "четырех, восьми и двенадцати"."""
    return [p.strip() for p in re.split(r",|\s+и\s+", text) if p.strip()]


def solve(q, places=DIGITS):
    s = asked(q)
    fmt = partial(common.fmt, places=places)

    def match(pattern):
        return re.fullmatch(pattern, s)

    if m := match(rf"(?:кубический|кубичный) корень (?:из )?{N}"):
        v = parse_num(m.group(1))
        return None if v is None else fmt(round(math.copysign(abs(v) ** (1 / 3), v), 6))
    if m := match(rf"(?:квадратный )?корень (?:квадратный )?(?:из )?{N}"):
        v = parse_num(m.group(1))
        return None if v is None or v < 0 else fmt(math.sqrt(v))
    if m := match(rf"факториал (?:числа )?{N}|{N} факториал"):
        v = parse_num(m.group(1) or m.group(2))
        return fmt(math.factorial(int(v))) if v is not None and v.is_integer() and 0 <= v <= 20 else None
    if m := match(rf"{N} в квадрате|квадрат (?:числа )?{N}"):
        v = parse_num(m.group(1) or m.group(2))
        return None if v is None else fmt(v**2)
    if m := match(rf"{N} в кубе|куб (?:числа )?{N}"):
        v = parse_num(m.group(1) or m.group(2))
        return None if v is None else fmt(v**3)
    if m := match(rf"(?:возвести |возведи )?{N} в (\w+) степен[ьи]"):
        v, e = parse_num(m.group(1)), POWERS.get(m.group(2))
        if v is not None and e is not None:
            return fmt(v**e)
    if m := match(rf"(?:возвести |возведи )?{N} в степен[ьи] {N}|{N} в {N}(?:-?[йюои]+)? степен[ьи]"):
        v = parse_num(m.group(1) or m.group(3))
        e = parse_num(m.group(2) or m.group(4))
        return None if v is None or e is None or e > 64 else fmt(v**e)
    if m := match(rf"{N} (?:процент\w*) (?:от )?{N}"):
        a, b = parse_num(m.group(1)), parse_num(m.group(2))
        return None if a is None or b is None else fmt(round(a * b / 100, 6))
    if m := match(rf"(половин\w*|четверть|треть|две трети|три четверти|одна треть|одна четверть) (?:от )?{N}"):
        part = FRACTIONS.get(m.group(1), 0.5 if m.group(1).startswith("половин") else None)
        b = parse_num(m.group(2))
        return None if b is None or part is None else fmt(round(part * b, 6))
    if m := match(r"(?:среднее арифметическое|среднее значение|среднее) (?:от |из |для )?(?:чисел )?(.+)"):
        vals = [parse_num(p) for p in _items(m.group(1))]
        if len(vals) >= 2 and None not in vals:
            return fmt(round(sum(vals) / len(vals), 6))
        return None
    if m := match(r"сумма (?:чисел )?(.+)"):
        vals = [parse_num(p) for p in _items(m.group(1))]
        return fmt(sum(vals)) if len(vals) >= 2 and None not in vals else None
    if "процент" in s:
        return None
    # Chains of + - * /.
    ops = {"плюс": "+", "минус": "-", "умн": "*", "дел": "/"}
    tokens = re.split(r"\s+(плюс|минус|умн|дел)\s+", _expression(s))
    if len(tokens) >= 3 and len(tokens) % 2 == 1:
        vals = [parse_num(t) for t in tokens[0::2]]
        if None in vals:
            return None
        expr = "".join(f"{v!r}{ops[o]}" for v, o in zip(vals, tokens[1::2])) + repr(vals[-1])
        try:
            return fmt(round(eval(expr, {"__builtins__": {}}), 6))  # expr only has numbers and + - * /
        except ZeroDivisionError:
            return None
    return None


MONEY = re.compile(r"\b(евро|доллар\w*|рубл\w*|центов|цента|цент|копе\w*|иен\w*|йен\w*|юан\w*)\b", re.IGNORECASE)
_MONEY = {"евро": ("евро", "евро", "евро"), "доллар": ("доллар", "доллара", "долларов"),
          "рубл": ("рубль", "рубля", "рублей"), "цент": ("цент", "цента", "центов"),
          "копе": ("копейка", "копейки", "копеек"), "иен": ("иена", "иены", "иен"), "йен": ("иена", "иены", "иен"),
          "юан": ("юань", "юаня", "юаней")}


def _money(word: str) -> str:
    """"долларов" -> "доллар": the money's own."""
    word = word.lower()
    return next((k for k in _MONEY if word.startswith(k)), word)


def answer(text: str, settings: Settings):
    """With money in it ("15 % от 80 €"), in that money: "Это 12 евро."."""
    money = {_money(m) for m in MONEY.findall(text)}
    if len(money) > 1:
        return None  # a conversion: not arithmetic
    plain = MONEY.sub(" ", text) if money else text
    found = solve(plain, settings.digits)
    if found is not None and money:
        value = float(found.replace(",", "."))
        found = f"{found} {plural(value, *_MONEY[money.pop()])}"
    expression = " ".join(asked(plain).split())
    if not expression or found is None:
        return None
    # A sum is what it "будет"; a thing of a number is said with a dash:
    # "квадратный корень из 144 — 12".
    chain = len(re.split(r"\s+(?:плюс|минус|умн|дел)\s+", _expression(expression))) > 1
    restated = f"{expression} будет {found}" if chain else f"{expression} — {found}"
    return reply(found, text, restated)
