"""How Italian asks a sum ("quanto fa dodici per otto", "il 15 per cento di
80", "la radice quadrata di 144", "la media di quattro, otto e dodici",
"cinque alla terza") and says its answer (voice_labs/skills/arithmetic.py).

"Per" multiplies ("dodici per otto", "moltiplicato per") and "diviso" (or
"fratto") divides; "per cento" is a percentage."""

from __future__ import annotations

import math
import re
from functools import partial

from voice_labs.skills import common
from voice_labs.skills.arithmetic import Settings
from voice_labs.skills.common import DIGITS, reply

from .numbers import FRACTIONS, N, clean, loose, parse_num

EXAMPLES = ("quanto fa dodici per otto", "quanto fa il 15 per cento di 80", "la radice quadrata di 144")

POWERS = {"seconda": 2, "terza": 3, "quarta": 4, "quinta": 5, "sesta": 6, "settima": 7, "ottava": 8, "nona": 9,
          "decima": 10}
"""The power said by its ordinal: "cinque alla terza"."""


def asked(q):
    """What is asked, without "quanto fa": "dodici per otto"."""
    s = clean(q)
    s = re.sub(loose(
        r"^(?:quanto (?:fa|fanno|è|vale|valgono|risulta|viene|farebbe)|quant'è|qual è il risultato (?:di|del|della)"
        r"|qual è|cos'è|che cos'è|cosa fa|che risultato dà)\s+"
    ), "", s)
    # "dodici per otto, quanto fa": asked after.
    s = re.sub(loose(r"^(.+?),? (?:quanto (?:fa|fanno|è)|fa|fanno)$"), r"\1", s)
    s = re.sub(r"\bmoltiplicat[oa] per\b|\bx\b|\bvolte\b", "per", s)
    s = re.sub(r"\bdivis[oa](?: per)?\b|\bfratto\b", "diviso", s)
    s = re.sub(r"(\d)\s*/\s*(\d)", r"\1 diviso \2", s)
    s = re.sub(r"\bpi[uù]\b", "più", s)
    s = re.sub(r"\bmet[aà]\b", "metà", s)
    s = re.sub(r"\bper ?cento\b", "per cento", s)
    return s.strip()


def _items(text: str) -> list[str]:
    """A list's numbers: "quattro, otto e dodici" -> quattro, otto, dodici
    ("due e mezzo" is one)."""
    return [p for p in re.split(r",\s*(?:e\s+)?|\s+e\s+(?!mezz[oa]\b|un quarto\b|tre quarti\b)", text) if p]


def solve(q, places=DIGITS):
    s = asked(q)
    fmt = partial(common.fmt, places=places)

    def match(pattern):
        return re.fullmatch(loose(pattern), s)

    if m := match(rf"(?:la )?radice cubica (?:di )?{N}"):
        v = parse_num(m.group(1))
        return None if v is None else fmt(round(math.copysign(abs(v) ** (1 / 3), v), 6))
    if m := match(rf"(?:la )?radice(?: quadrata)? (?:di )?{N}"):
        v = parse_num(m.group(1))
        return None if v is None or v < 0 else fmt(math.sqrt(v))
    if m := match(rf"(?:il )?fattoriale (?:di )?{N}|{N} fattoriale"):
        v = parse_num(m.group(1) or m.group(2))
        return fmt(math.factorial(int(v))) if v is not None and v.is_integer() and 0 <= v <= 20 else None
    if m := match(rf"{N} al quadrato|(?:il )?quadrato (?:di )?{N}"):
        v = parse_num(m.group(1) or m.group(2))
        return None if v is None else fmt(v**2)
    if m := match(rf"{N} al cubo|(?:il )?cubo (?:di )?{N}"):
        v = parse_num(m.group(1) or m.group(2))
        return None if v is None else fmt(v**3)
    if m := match(rf"{N} (?:elevato )?(?:alla|a) ({'|'.join(POWERS)})(?: potenza)?"):
        v = parse_num(m.group(1))
        return None if v is None else fmt(v ** POWERS[m.group(2)])
    if m := match(rf"{N} (?:elevato )?(?:alla|a)(?: potenza)?(?: di)? {N}"):
        v, e = parse_num(m.group(1)), parse_num(m.group(2))
        return None if v is None or e is None or e > 64 else fmt(v**e)
    if m := match(rf"(?:il |l')?{N} per cento (?:di|del|della|dello|dell') ?{N}"):
        a, b = parse_num(m.group(1)), parse_num(m.group(2))
        return None if a is None or b is None else fmt(round(a * b / 100, 6))
    if m := match(rf"(?:la )?metà (?:di |del |della |dell')?{N}"):
        b = parse_num(m.group(1))
        return None if b is None else fmt(round(b / 2, 6))
    if m := match(rf"(un quarto|un terzo|due terzi|tre quarti|un decimo|un mezzo) (?:di )?{N}"):
        b = parse_num(m.group(2))
        return None if b is None else fmt(round(FRACTIONS[m.group(1)] * b, 6))
    if m := match(r"(?:la )?media(?: aritmetica)? (?:di|dei|delle|degli|tra|fra) (.+)"):
        vals = [parse_num(p) for p in _items(m.group(1))]
        if len(vals) >= 2 and None not in vals:
            return fmt(round(sum(vals) / len(vals), 6))
        return None
    if m := match(r"(?:la )?somma (?:di|dei|delle|degli|tra|fra) (.+)"):
        vals = [parse_num(p) for p in _items(m.group(1))]
        return fmt(sum(vals)) if len(vals) >= 2 and None not in vals else None
    if m := match(r"(?:il )?prodotto (?:di|dei|delle|degli|tra|fra) (.+)"):
        vals = [parse_num(p) for p in _items(m.group(1))]
        return fmt(math.prod(vals)) if len(vals) >= 2 and None not in vals else None
    if "per cento" in s:
        return None
    # Chains of + - * /.
    ops = {"più": "+", "meno": "-", "per": "*", "diviso": "/"}
    tokens = re.split(r"\s+(più|meno|per|diviso)\s+", s)
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


MONEY = re.compile(r"\b(euro|dollar[oi]|sterlin[ae]|centesim[oi]|yen)\b", re.IGNORECASE)
_MONEY = {"euro": ("euro", "euro"), "dollar": ("dollaro", "dollari"), "sterlin": ("sterlina", "sterline"),
          "centesim": ("centesimo", "centesimi"), "yen": ("yen", "yen")}


def _money(word: str) -> str:
    """"dollari" -> "dollar": the money's own."""
    word = word.lower()
    return next((k for k in _MONEY if word.startswith(k)), word)


def answer(text: str, settings: Settings):
    """With money in it ("il 15 % di 80 €"), in that money: "Fa 12 euro."."""
    money = {_money(m) for m in MONEY.findall(text)}
    if len(money) > 1:
        return None  # a conversion: not arithmetic
    plain = MONEY.sub(" ", text) if money else text
    found = solve(plain, settings.digits)
    if found is not None and money:
        single, plural = _MONEY[money.pop()]
        found = f"{found} {single if re.fullmatch(r'-?1', found) else plural}"
    expression = " ".join(asked(plain).split())
    return reply(found, text, f"{expression} fa {found}" if expression else None,
                 specific=f"Fa {found}." if found else None)
