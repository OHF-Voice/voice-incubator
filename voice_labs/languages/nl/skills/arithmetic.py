"""How Dutch asks a sum ("wat is twaalf keer acht", "15 procent van 80", "de
wortel van 144", "het gemiddelde van vier, acht en twaalf", "vijf tot de
macht drie") and says its answer (voice_labs/skills/arithmetic.py)."""

from __future__ import annotations

import math
import re
from functools import partial

from voice_labs.skills import common
from voice_labs.skills.arithmetic import Settings
from voice_labs.skills.common import DIGITS, reply

from .numbers import FRACTIONS, N, clean, parse_num

EXAMPLES = ("wat is twaalf keer acht", "hoeveel is 15 procent van 80", "de wortel van 144")

POWERS = {"tweede": 2, "derde": 3, "vierde": 4, "vijfde": 5}


def asked(q):
    """What is asked, without "wat is": "twaalf keer acht"."""
    s = clean(q)
    s = re.sub(r"^(?:wat|hoeveel) (?:is|zijn|maakt|geeft) ", "", s)
    s = re.sub(r"^(?:wat|hoeveel) (.+?) (?:is|zijn)$", r"\1", s)
    s = re.sub(r"\bmaal\b|\bvermenigvuldigd met\b|\bx\b", "keer", s)
    s = re.sub(r"\b(?:gedeeld|delen) door\b", "gedeeld door", s)
    return re.sub(r"^(?:de )?som (?:van)\b", "som van", s).strip()


def solve(q, places=DIGITS):
    s = asked(q)
    fmt = partial(common.fmt, places=places)

    if m := re.fullmatch(rf"(?:de )?(?:vierkants)?wortel (?:van|uit) {N}", s):
        v = parse_num(m.group(1))
        return None if v is None or v < 0 else fmt(math.sqrt(v))
    if m := re.fullmatch(rf"(?:de )?derdemachtswortel (?:van|uit) {N}", s):
        v = parse_num(m.group(1))
        return None if v is None else fmt(round(math.copysign(abs(v) ** (1 / 3), v), 6))
    if m := re.fullmatch(rf"{N} faculteit|(?:de )?faculteit van {N}", s):
        v = parse_num(m.group(1) or m.group(2))
        return fmt(math.factorial(int(v))) if v is not None and v.is_integer() and 0 <= v <= 20 else None
    if m := re.fullmatch(rf"{N} (?:in het )?kwadraat", s):
        v = parse_num(m.group(1))
        return None if v is None else fmt(v**2)
    if m := re.fullmatch(rf"{N} tot de macht {N}", s):
        v, e = parse_num(m.group(1)), parse_num(m.group(2))
        return None if v is None or e is None or e > 64 else fmt(v**e)
    if m := re.fullmatch(rf"{N} tot de (\w+)(?: macht)?", s):
        v, e = parse_num(m.group(1)), POWERS.get(m.group(2))
        return None if v is None or e is None else fmt(v**e)
    if m := re.fullmatch(rf"{N} procent (?:van) {N}", s):
        a, b = parse_num(m.group(1)), parse_num(m.group(2))
        return None if a is None or b is None else fmt(round(a * b / 100, 6))
    if m := re.fullmatch(rf"(?:de )?helft van {N}", s):
        b = parse_num(m.group(1))
        return None if b is None else fmt(round(b / 2, 6))
    if m := re.fullmatch(rf"(een kwart|een derde|twee derde|driekwart|drie kwart|een tiende) van {N}", s):
        b = parse_num(m.group(2))
        return None if b is None else fmt(round(FRACTIONS[m.group(1)] * b, 6))
    if m := re.fullmatch(r"(?:het )?gemiddelde (?:van) (.+)", s):
        parts = [p for p in re.split(r",?\s+en\s+|,\s*", m.group(1)) if p]
        vals = [parse_num(p) for p in parts]
        if len(vals) >= 2 and None not in vals:
            return fmt(round(sum(vals) / len(vals), 6))
        return None
    if m := re.fullmatch(r"som van (.+)", s):
        vals = [parse_num(p) for p in re.split(r",?\s+en\s+|,\s*", m.group(1)) if p]
        return fmt(sum(vals)) if len(vals) >= 2 and None not in vals else None
    ops = {"plus": "+", "min": "-", "keer": "*", "gedeeld door": "/"}
    tokens = re.split(r"\s+(plus|min|keer|gedeeld door)\s+", s)
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


MONEY = re.compile(r"\b(euro|dollar|cent|pond|yen)\b", re.IGNORECASE)


def answer(text: str, settings: Settings):
    """With money in it ("15 % van 80 euro"), in that money: "Dat is 12 euro."."""
    money = {m.lower() for m in MONEY.findall(text)}
    if len(money) > 1:
        return None  # a conversion: not arithmetic
    plain = MONEY.sub(" ", text) if money else text
    found = solve(plain, settings.digits)
    if found is not None and money:
        found = f"{found} {money.pop()}"
    expression = " ".join(asked(plain).split())
    return reply(found, text, f"{expression} is {found}" if expression else None, specific=f"Dat is {found}." if found else None)
