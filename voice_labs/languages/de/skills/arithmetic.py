"""How German asks a sum ("was ist zwölf mal acht", "15 Prozent von 80", "die
Wurzel aus 144", "der Durchschnitt von vier, acht und zwölf", "fünf hoch
drei") and says its answer (voice_labs/skills/arithmetic.py)."""

from __future__ import annotations

import math
import re
from functools import partial

from voice_labs.skills import common
from voice_labs.skills.arithmetic import Settings
from voice_labs.skills.common import DIGITS, reply

from .numbers import FRACTIONS, N, clean, nouns, parse_num

EXAMPLES = ("was ist zwölf mal acht", "wie viel sind 15 Prozent von 80", "die Wurzel aus 144")

POWERS = {"quadrat": 2, "zweite": 2, "zweiten": 2, "dritte": 3, "dritten": 3, "vierte": 4, "vierten": 4, "fünfte": 5, "fünften": 5}
"""The power said by its ordinal: "zur dritten Potenz"."""


def asked(q):
    """What is asked, without "was ist": "zwölf mal acht"."""
    s = clean(q)
    s = re.sub(r"^(?:was|wie ?viel) (?:ist|sind|ergibt|ergeben|macht|machen|wäre|gibt) ", "", s)
    # "was 15 prozent von 80 sind": a question asked indirectly.
    s = re.sub(r"^(?:was|wie ?viel) (.+?) (?:ist|sind|ergibt|ergeben|macht|machen)$", r"\1", s)
    s = re.sub(r"^(?:was|wie ?viel) ", "", s)
    s = re.sub(r"\bmultipliziert mit\b|\bx\b|\bmal genommen\b", "mal", s)
    s = re.sub(r"\b(?:geteilt |dividiert )?durch\b", "geteilt durch", s)
    s = re.sub(r"\bweniger\b|\babzüglich\b", "minus", s)
    return re.sub(r"^(?:die )?(?:gesamt)?summe (?:von|aus)\b", "summe von", s).strip()


def solve(q, places=DIGITS):
    s = asked(q)
    fmt = partial(common.fmt, places=places)

    if m := re.fullmatch(rf"(?:die )?(?:quadrat)?wurzel (?:aus|von) {N}", s):
        v = parse_num(m.group(1))
        return None if v is None or v < 0 else fmt(math.sqrt(v))
    if m := re.fullmatch(rf"(?:die )?(?:kubikwurzel|dritte wurzel) (?:aus|von) {N}", s):
        v = parse_num(m.group(1))
        return None if v is None else fmt(round(math.copysign(abs(v) ** (1 / 3), v), 6))
    if m := re.fullmatch(rf"{N} fakultät|(?:die )?fakultät (?:von|aus) {N}", s):
        v = parse_num(m.group(1) or m.group(2))
        return fmt(math.factorial(int(v))) if v is not None and v.is_integer() and 0 <= v <= 20 else None
    if m := re.fullmatch(rf"{N} (?:zum|im) quadrat|{N} quadriert", s):
        v = parse_num(m.group(1) or m.group(2))
        return None if v is None else fmt(v**2)
    if m := re.fullmatch(rf"{N} hoch {N}", s):
        v, e = parse_num(m.group(1)), parse_num(m.group(2))
        return None if v is None or e is None or e > 64 else fmt(v**e)
    if m := re.fullmatch(rf"{N} (?:zur|in der|in die) (\w+) potenz", s):
        v, e = parse_num(m.group(1)), POWERS.get(m.group(2))
        return None if v is None or e is None else fmt(v**e)
    if m := re.fullmatch(rf"{N} prozent (?:von|aus) {N}", s):
        a, b = parse_num(m.group(1)), parse_num(m.group(2))
        return None if a is None or b is None else fmt(round(a * b / 100, 6))
    if m := re.fullmatch(rf"(?:die )?hälfte (?:von|aus) {N}", s):
        b = parse_num(m.group(1))
        return None if b is None else fmt(round(b / 2, 6))
    if m := re.fullmatch(rf"(ein halb|ein viertel|ein drittel|zwei drittel|drei viertel|dreiviertel|ein zehntel) (?:von|aus) {N}", s):
        b = parse_num(m.group(2))
        return None if b is None else fmt(round(FRACTIONS[m.group(1)] * b, 6))
    if m := re.fullmatch(r"(?:der |das )?(?:durchschnitt|mittelwert|mittel) (?:von|aus) (.+)", s):
        parts = [p for p in re.split(r",?\s+und\s+|,\s*", m.group(1)) if p]
        vals = [parse_num(p) for p in parts]
        if len(vals) >= 2 and None not in vals:
            return fmt(round(sum(vals) / len(vals), 6))
        return None
    if m := re.fullmatch(r"summe von (.+)", s):
        vals = [parse_num(p) for p in re.split(r",?\s+und\s+|,\s*", m.group(1)) if p]
        return fmt(sum(vals)) if len(vals) >= 2 and None not in vals else None
    # Chains of + - * /.
    ops = {"plus": "+", "minus": "-", "mal": "*", "geteilt durch": "/"}
    tokens = re.split(r"\s+(plus|minus|mal|geteilt durch)\s+", s)
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


MONEY = re.compile(r"\b(euro|dollar|cent|franken|yen)\b", re.IGNORECASE)


def answer(text: str, settings: Settings):
    """With money in it ("15 % von 80 Euro"), in that money: "Das ergibt 12 Euro."."""
    money = {m.lower() for m in MONEY.findall(text)}
    if len(money) > 1:
        return None  # a conversion: not arithmetic
    plain = MONEY.sub(" ", text) if money else text
    found = solve(plain, settings.digits)
    if found is not None and money:
        found = f"{found} {money.pop().capitalize()}"
    expression = nouns(" ".join(asked(plain).split()))
    return reply(found, text, f"{expression} ist {found}" if expression else None, specific=f"Das ergibt {found}." if found else None)
