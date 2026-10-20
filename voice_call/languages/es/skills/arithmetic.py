"""How Spanish asks a sum ("¿cuánto es doce por ocho?", "el 15 por ciento de
80", "la raíz cuadrada de 144", "la media de cuatro, ocho y doce", "cinco
elevado a tres") and says its answer (voice_call/skills/arithmetic.py).

"Por" multiplies and "entre" divides; "y" joins a list ("cuatro, ocho y
doce") but also tens and units ("treinta y cinco"), and is read as the
second only between the two."""

from __future__ import annotations

import math
import re
from functools import partial

from voice_call.skills import common
from voice_call.skills.arithmetic import Settings
from voice_call.skills.common import DIGITS, reply

from .numbers import FRACTIONS, N, clean, fold, loose, parse_num, verb

EXAMPLES = ("cuánto es doce por ocho", "cuánto es el 15 por ciento de 80", "la raíz cuadrada de 144")

POWERS = {"segunda": 2, "tercera": 3, "cuarta": 4, "quinta": 5, "sexta": 6}
"""The power said by its ordinal: "elevado a la tercera"."""

_TENS = {"treinta", "cuarenta", "cincuenta", "sesenta", "setenta", "ochenta", "noventa"}
_UNITS = {"uno", "una", "un", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve"}


def asked(q):
    """What is asked, without "cuánto es": "doce por ocho"."""
    s = clean(q)
    s = re.sub(loose(r"^(?:cuánto|cuanto|qué|cuál) (?:es|son|da|dan|sale|salen|hace|hacen|sería|serían|vale|resulta) "), "", s)
    # "doce por ocho, cuánto es": asked after.
    s = re.sub(loose(r"^(.+?),? (?:cuánto|qué) (?:es|son|da|dan|sale|salen)$"), r"\1", s)
    s = re.sub(loose(r"\bmultiplicado por\b|\bx\b|\bveces\b"), "por", s)
    s = re.sub(loose(r"\b(?:dividido|partido) (?:por|entre)\b"), "entre", s)
    s = re.sub(r"\bmas\b", "más", s)
    return s.strip()


def _items(text: str) -> list[str]:
    """A list's numbers: "cuatro, ocho y doce" -> cuatro, ocho, doce; "treinta
    y cinco y dos" -> treinta y cinco, dos."""
    words = text.replace(",", " , ").split()
    items, current = [], []
    for index, word in enumerate(words):
        joins = (word == "y" and current and fold(current[-1]) in _TENS
                 and index + 1 < len(words) and fold(words[index + 1]) in _UNITS)
        if word == "," or (word == "y" and not joins):
            if current:
                items.append(" ".join(current))
            current = []
        else:
            current.append(word)
    if current:
        items.append(" ".join(current))
    return items


def solve(q, places=DIGITS):
    s = asked(q)
    fmt = partial(common.fmt, places=places)

    def match(pattern):
        return re.fullmatch(loose(pattern), s)

    if m := match(rf"(?:la )?raíz cúbica de {N}"):
        v = parse_num(m.group(1))
        return None if v is None else fmt(round(math.copysign(abs(v) ** (1 / 3), v), 6))
    if m := match(rf"(?:la )?raíz(?: cuadrada)? de {N}"):
        v = parse_num(m.group(1))
        return None if v is None or v < 0 else fmt(math.sqrt(v))
    if m := match(rf"(?:el )?factorial de {N}|{N} factorial"):
        v = parse_num(m.group(1) or m.group(2))
        return fmt(math.factorial(int(v))) if v is not None and v.is_integer() and 0 <= v <= 20 else None
    if m := match(rf"{N} al cuadrado|(?:el )?cuadrado de {N}"):
        v = parse_num(m.group(1) or m.group(2))
        return None if v is None else fmt(v**2)
    if m := match(rf"{N} al cubo|(?:el )?cubo de {N}"):
        v = parse_num(m.group(1) or m.group(2))
        return None if v is None else fmt(v**3)
    if m := match(rf"{N} elevado a la (\w+)(?: potencia)?"):
        v, e = parse_num(m.group(1)), POWERS.get(m.group(2))
        if v is not None and e is not None:
            return fmt(v**e)
    if m := match(rf"{N} elevado a(?: la potencia(?: de)?)? {N}"):
        v, e = parse_num(m.group(1)), parse_num(m.group(2))
        return None if v is None or e is None or e > 64 else fmt(v**e)
    if m := match(rf"(?:el )?{N} por ciento de {N}"):
        a, b = parse_num(m.group(1)), parse_num(m.group(2))
        return None if a is None or b is None else fmt(round(a * b / 100, 6))
    if m := match(rf"(?:la )?mitad de {N}"):
        b = parse_num(m.group(1))
        return None if b is None else fmt(round(b / 2, 6))
    if m := match(rf"(un cuarto|un tercio|dos tercios|tres cuartos|un décimo|un medio) de {N}"):
        b = parse_num(m.group(2))
        return None if b is None else fmt(round(FRACTIONS.get(m.group(1), parse_num(m.group(1)) or 0) * b, 6))
    if m := match(r"(?:la |el )?(?:media|promedio|media aritmética) de (.+)"):
        vals = [parse_num(p) for p in _items(m.group(1))]
        if len(vals) >= 2 and None not in vals:
            return fmt(round(sum(vals) / len(vals), 6))
        return None
    if m := match(r"(?:la )?suma de (.+)"):
        vals = [parse_num(p) for p in _items(m.group(1))]
        return fmt(sum(vals)) if len(vals) >= 2 and None not in vals else None
    if "por ciento" in s:
        return None
    # Chains of + - * /.
    ops = {"más": "+", "menos": "-", "por": "*", "entre": "/"}
    tokens = re.split(r"\s+(más|menos|por|entre)\s+", s)
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


MONEY = re.compile(loose(r"\b(euros?|dólar|dólares|céntimos?|yenes|yen|pesos?)\b"), re.IGNORECASE)
_MONEY = {"euro": ("euro", "euros"), "dolar": ("dólar", "dólares"), "centimo": ("céntimo", "céntimos"),
          "yen": ("yen", "yenes"), "peso": ("peso", "pesos")}


def _money(word: str) -> str:
    """"dólares" -> "dolar": the money's own."""
    word = fold(word.lower())
    return next((k for k in _MONEY if word in (k, k + "s", k + "es")), word)


def answer(text: str, settings: Settings):
    """With money in it ("el 15 % de 80 €"), in that money: "Son 12 euros."."""
    money = {_money(m) for m in MONEY.findall(text)}
    if len(money) > 1:
        return None  # a conversion: not arithmetic
    plain = MONEY.sub(" ", text) if money else text
    found = solve(plain, settings.digits)
    if found is not None and money:
        single, plural = _MONEY[money.pop()]
        found = f"{found} {single if verb(found) == 'es' else plural}"
    expression = " ".join(asked(plain).split())
    # A thing of a number is one ("la raíz cuadrada de 144 es 12"), a sum as
    # many as it comes to ("doce por ocho son 96", "seis entre seis es 1").
    said = "es" if re.match(loose(r"(?:la|el) |(?:un|una) (?:cuarto|tercio|medio|décimo) "), expression) else verb(found or "")
    return reply(found, text, f"{expression} {said} {found}" if expression else None,
                 specific=f"{said.capitalize()} {found}." if found else None)
