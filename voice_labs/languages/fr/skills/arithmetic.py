"""How French asks a sum ("combien font douze fois huit", "15 pour cent de
80", "la racine carrée de 144", "la moyenne de quatre, huit et douze", "cinq
puissance trois") and says its answer (voice_labs/skills/arithmetic.py)."""

from __future__ import annotations

import math
import re
from functools import partial

from voice_labs.skills import common
from voice_labs.skills.arithmetic import Settings
from voice_labs.skills.common import DIGITS, reply

from .numbers import FRACTIONS, N, clean, parse_num

EXAMPLES = ("combien font douze fois huit", "combien font 15 pour cent de 80", "la racine carrée de 144")


def asked(q):
    """What is asked, without "combien font": "douze fois huit"."""
    s = clean(q)
    s = re.sub(r"^(?:combien|que|qu'est ce que|ça) (?:font|fait|égale|égalent|donne|donnent) ", "", s)
    s = re.sub(r"^(?:c'est combien|ça fait combien|combien ça fait|quel est le résultat de|calcule)\s*,?\s+", "", s)
    s = re.sub(r"^(?:quel|quelle) est ", "", s)
    s = re.sub(r",?\s+(?:ça )?(?:font|fait|égale|égalent|donne) (?:combien|quoi)$", "", s)
    s = re.sub(r"\bmultiplié par\b|\bx\b", "fois", s)
    s = re.sub(r"\bdivisé par\b|\bdivisée par\b", "divisé par", s)
    s = re.sub(r"\bpourcent\b", "pour cent", s)
    return re.sub(r"^(?:la )?somme (?:de|des)\b", "somme de", s).strip()


def solve(q, places=DIGITS):
    s = asked(q)
    fmt = partial(common.fmt, places=places)

    if m := re.fullmatch(rf"(?:la )?racine(?: carrée)? (?:de|d') ?{N}", s):
        v = parse_num(m.group(1))
        return None if v is None or v < 0 else fmt(math.sqrt(v))
    if m := re.fullmatch(rf"(?:la )?racine cubique (?:de|d') ?{N}", s):
        v = parse_num(m.group(1))
        return None if v is None else fmt(round(math.copysign(abs(v) ** (1 / 3), v), 6))
    if m := re.fullmatch(rf"{N} factorielle|(?:la )?factorielle (?:de |d')?{N}", s):
        v = parse_num(m.group(1) or m.group(2))
        return fmt(math.factorial(int(v))) if v is not None and v.is_integer() and 0 <= v <= 20 else None
    if m := re.fullmatch(rf"{N} au carré", s):
        v = parse_num(m.group(1))
        return None if v is None else fmt(v**2)
    if m := re.fullmatch(rf"{N} au cube", s):
        v = parse_num(m.group(1))
        return None if v is None else fmt(v**3)
    if m := re.fullmatch(rf"{N} (?:à la )?puissance {N}", s):
        v, e = parse_num(m.group(1)), parse_num(m.group(2))
        return None if v is None or e is None or e > 64 else fmt(v**e)
    if m := re.fullmatch(rf"{N} pour cent (?:de|d') ?{N}", s):
        a, b = parse_num(m.group(1)), parse_num(m.group(2))
        return None if a is None or b is None else fmt(round(a * b / 100, 6))
    if m := re.fullmatch(rf"(?:la )?moitié (?:de|d') ?{N}", s):
        b = parse_num(m.group(1))
        return None if b is None else fmt(round(b / 2, 6))
    if m := re.fullmatch(rf"(un quart|un tiers|deux tiers|trois quarts|un dixième) (?:de|d') ?{N}", s):
        b = parse_num(m.group(2))
        return None if b is None else fmt(round(FRACTIONS[m.group(1)] * b, 6))
    if m := re.fullmatch(r"(?:la )?moyenne (?:de|des) (.+)", s):
        parts = [p for p in re.split(r",?\s+et\s+|,\s*", m.group(1)) if p]
        vals = [parse_num(p) for p in parts]
        if len(vals) >= 2 and None not in vals:
            return fmt(round(sum(vals) / len(vals), 6))
        return None
    if m := re.fullmatch(r"somme de (.+)", s):
        vals = [parse_num(p) for p in re.split(r",?\s+et\s+|,\s*", m.group(1)) if p]
        return fmt(sum(vals)) if len(vals) >= 2 and None not in vals else None
    ops = {"plus": "+", "moins": "-", "fois": "*", "divisé par": "/"}
    tokens = re.split(r"\s+(plus|moins|fois|divisé par)\s+", s)
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


MONEY = re.compile(r"\b(euros?|dollars?|centimes?|yens?)\b", re.IGNORECASE)


def answer(text: str, settings: Settings):
    """With money in it ("15 % de 80 €"), in that money: "Ça fait 12 euros."."""
    money = {m.lower() for m in MONEY.findall(text)}
    if len({m.rstrip("s") for m in money}) > 1:
        return None  # a conversion: not arithmetic
    plain = MONEY.sub(" ", text) if money else text
    found = solve(plain, settings.digits)
    if found is not None and money:
        unit = max(money).rstrip("s")
        found = f"{found} {unit}{'' if abs(float(found.replace(',', '.'))) < 2 else 's'}"
    expression = " ".join(asked(plain).split())
    return reply(found, text, f"{expression}, ça fait {found}" if expression else None, specific=f"Ça fait {found}." if found else None)
