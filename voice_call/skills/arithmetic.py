"""Arithmetic: "what's twelve times eight", "15 percent of 80", "the square
root of 144", "the average of four, eight and twelve"."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from functools import partial
from typing import Any

from . import common
from .common import DIGITS, FRACTIONS, N, ORDINAL_POW, Skill, clean, options, parse_num, reply


@dataclass(frozen=True)
class Settings(common.Settings):
    digits: int = DIGITS
    """Significant digits in an answer that isn't whole: 14.29 (4), 14.3 (3)."""

    @classmethod
    def load(cls, stored: Any) -> Settings:
        return cls(common.digits(options(stored, ("digits",)).get("digits")))

    def to_json(self) -> dict[str, Any]:
        return {"digits": self.digits}

    @classmethod
    def store(cls, edited: Any) -> Any:
        settings = cls.load(edited)
        return {"digits": settings.digits} if settings != cls() else None


def asked(q):
    """What is asked, without "what's": "twelve times eight"."""
    s = clean(q)
    s = re.sub(r"^(what|what's|whats|how much) (is|are)? ?", "", s)
    s = re.sub(r"^(what's|whats) ", "", s)
    s = re.sub(r"\bmultiplied by\b|\bx\b", "times", s)
    s = re.sub(r"\bover\b", "divided by", s)
    return re.sub(r"\bthe (sum|total) of\b", "sum of", s).strip()


def solve(q, places=DIGITS):
    s = asked(q)
    fmt = partial(common.fmt, places=places)

    if m := re.fullmatch(rf"the square root of {N}", s) or re.fullmatch(rf"square root of {N}", s):
        v = parse_num(m.group(1))
        return None if v is None or v < 0 else fmt(math.sqrt(v))
    if m := re.fullmatch(rf"(?:the )?cube root of {N}", s):
        v = parse_num(m.group(1))
        return None if v is None else fmt(round(math.copysign(abs(v) ** (1 / 3), v), 6))
    if m := re.fullmatch(rf"{N} factorial|(?:the )?factorial of {N}", s):
        v = parse_num(m.group(1) or m.group(2))
        return fmt(math.factorial(int(v))) if v is not None and v.is_integer() and 0 <= v <= 20 else None
    if m := re.fullmatch(rf"{N} (squared|cubed)", s):
        v = parse_num(m.group(1))
        return None if v is None else fmt(v ** ORDINAL_POW[m.group(2)])
    if m := re.fullmatch(rf"{N} (?:to the|raised to the) (?:power of )?(\w+)(?: power)?", s):
        v = parse_num(m.group(1))
        e = ORDINAL_POW.get(m.group(2)) or parse_num(re.sub(r"(st|nd|rd|th)$", "", m.group(2)) or "")
        return None if v is None or e is None or e > 64 else fmt(v ** e)
    if m := re.fullmatch(rf"{N} percent of {N}", s):
        a, b = parse_num(m.group(1)), parse_num(m.group(2))
        return None if a is None or b is None else fmt(round(a * b / 100, 6))
    if m := re.fullmatch(rf"(half|a half|one half|a quarter|quarter|a third|one third|two thirds|three quarters|a tenth) of {N}", s):
        b = parse_num(m.group(2))
        return None if b is None else fmt(round(FRACTIONS[m.group(1)] * b, 6))
    if m := re.fullmatch(r"(?:the )?(?:average|mean) of (.+)", s):
        parts = [p for p in re.split(r",?\s+and\s+|,\s*|\s+", m.group(1)) if p and p != "and"]
        vals = [parse_num(p) for p in parts]
        if len(vals) >= 2 and None not in vals:
            return fmt(round(sum(vals) / len(vals), 6))
        return None
    if m := re.fullmatch(r"sum of (.+)", s):
        vals = [parse_num(p) for p in re.split(r",?\s+and\s+|,\s*", m.group(1)) if p]
        return fmt(sum(vals)) if len(vals) >= 2 and None not in vals else None
    # chains of + - * /
    ops = {"plus": "+", "add": "+", "minus": "-", "less": "-", "take away": "-", "times": "*", "divided by": "/"}
    tokens = re.split(r"\s+(plus|minus|times|divided by|take away)\s+", s)
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


MONEY = re.compile(r"\b(dollars?|euros?|pounds?|yen|cents?|bucks?)\b", re.I)


DEFAULT = Settings()


def answer(text: str, settings: Settings = DEFAULT):
    """With money in it ("15% of $80"), in that money: "It's 12 dollars."."""
    money = {m.lower().rstrip("s") for m in MONEY.findall(text)}
    if len(money) > 1:
        return None  # a conversion: not arithmetic
    plain = MONEY.sub(" ", text) if money else text
    found = solve(plain, settings.digits)
    if found is not None and money:
        unit = money.pop()
        found = f"{found} {unit}{'' if unit == 'yen' or found == '1' else 's'}"
    expression = " ".join(asked(plain).split())
    return reply(found, text, f"{expression} is {found}" if expression else None)


SKILL = Skill(
    id="arithmetic",
    name="Math",
    description="Adds, subtracts, multiplies and divides; percentages, fractions, powers, roots, averages.",
    examples=("what's twelve times eight", "what is 15 percent of 80", "the square root of 144"),
    icon="mdi:calculator-variant-outline",
    answer=answer,
    settings=Settings,
)
