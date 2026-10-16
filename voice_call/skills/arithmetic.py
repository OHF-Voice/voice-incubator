"""Arithmetic: "what's twelve times eight", "15 percent of 80", "the square
root of 144", "the average of four, eight and twelve"."""

from __future__ import annotations

import math
import re

from .common import FRACTIONS, N, ORDINAL_POW, Skill, clean, fmt, parse_num, sentence


def solve(q):
    s = clean(q)
    s = re.sub(r"^(what|what's|whats|how much) (is|are)? ?", "", s)
    s = re.sub(r"^(what's|whats) ", "", s)
    s = re.sub(r"\bmultiplied by\b|\bx\b", "times", s)
    s = re.sub(r"\bover\b", "divided by", s)
    s = re.sub(r"\bthe (sum|total) of\b", "sum of", s).strip()

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


def answer(text: str) -> str | None:
    """With money in it ("15% of $80"), in that money: "It's 12 dollars."."""
    money = {m.lower().rstrip("s") for m in MONEY.findall(text)}
    if len(money) > 1:
        return None  # a conversion: not arithmetic
    found = solve(MONEY.sub(" ", text) if money else text)
    if found is None or not money:
        return sentence(found)
    unit = money.pop()
    plural = "" if unit == "yen" or found == "1" else "s"
    return sentence(f"{found} {unit}{plural}")


SKILL = Skill(
    id="arithmetic",
    name="Math",
    description="Adds, subtracts, multiplies and divides; percentages, fractions, powers, roots, averages.",
    examples=("what's twelve times eight", "what is 15 percent of 80", "the square root of 144"),
    icon="mdi:calculator-variant-outline",
    answer=answer,
)
