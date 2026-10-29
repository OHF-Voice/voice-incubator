"""How Polish asks a sum ("ile to jest dwanaście razy osiem?", "15 procent z
80", "pierwiastek z 144", "średnia z czterech, ośmiu i dwunastu", "dwa do
potęgi dziesiątej", "pomnóż dwanaście przez osiem") and says its answer
(voice_labs/skills/arithmetic.py).

"Razy" multiplies and "przez" divides ("sto przez siedem"); "i" joins a
list ("cztery, osiem i dwanaście"), but a half too ("dwa i pół"). Numbers
come in any case ("średnia z czterech i ośmiu"). The question is read
folded (numbers.fold) and said back as it was asked: "Dwanaście razy
osiem to 96."."""

from __future__ import annotations

import math
import re
from functools import partial

from voice_labs.skills import common
from voice_labs.skills.arithmetic import Settings
from voice_labs.skills.common import DIGITS, reply

from .numbers import FRACTIONS, N, clean, count, fold, parse_num

EXAMPLES = ("ile to jest dwanaście razy osiem", "ile to jest 15 procent z 80", "pierwiastek z 144")

POWERS = {"drugiej": 2, "trzeciej": 3, "czwartej": 4, "piatej": 5, "szostej": 6, "siodmej": 7, "osmej": 8,
          "dziewiatej": 9, "dziesiatej": 10}
"""The power said by its ordinal (folded): "do potęgi trzeciej"."""


def _sub(pattern: str, repl: str, s: str) -> str:
    """``re.sub`` matched on ``s`` folded, replacing in ``s`` itself (fold is
    letter for letter): what is said back keeps its diacritics. ``\\1`` in
    ``repl`` is the group as written."""
    out, last = [], 0
    for m in re.finditer(pattern, fold(s)):
        out.append(s[last:m.start()])
        out.append(re.sub(r"\\(\d)", lambda g, m=m: s[m.start(int(g.group(1))):m.end(int(g.group(1)))], repl))
        last = m.end()
    out.append(s[last:])
    return "".join(out)


def asked(q):
    """What is asked, without "ile to jest": "dwanaście razy osiem"."""
    s = clean(q)
    s = _sub(r"^(?:a |no )?(?:ile (?:to )?(?:jest|wynosi|bedzie|daje|by bylo)|ile to|ile|jaki jest wynik|wynik|co daje"
             r"|(?:jaka|jaki|ile) (?:jest|wynosi|bedzie)) ", "", s)
    # "dwanaście razy osiem, ile to jest": asked after.
    s = _sub(r"^(.+?),? (?:ile (?:to )?(?:jest|wynosi|bedzie|daje)|ile to)$", r"\1", s)
    # Told to: "pomnóż dwanaście przez osiem" is "dwanaście razy osiem".
    s = _sub(rf"^pomnoz {N} (?:przez|razy) {N}$", r"\1 razy \2", s)
    s = _sub(rf"^podziel {N} (?:przez|na) {N}$", r"\1 przez \2", s)
    # "Dodaj trzy do dziesięciu", "odejmij trzy od dziesięciu": in digits, as
    # "dziesięciu" said back would be in the wrong case.
    if m := re.fullmatch(rf"(dodaj|odejmij) {N} (?:do|od) {N}", fold(s)):
        a, b = parse_num(m.group(2)), parse_num(m.group(3))
        if a is not None and b is not None:
            s = f"{common.fmt(a)} plus {common.fmt(b)}" if m.group(1) == "dodaj" else f"{common.fmt(b)} minus {common.fmt(a)}"
    s = _sub(r"\b(?:pomnozone|pomnozyc|mnozone) przez\b|\bx\b", "razy", s)
    s = _sub(r"\b(?:podzielone|podzielic|dzielone) przez\b|\bdzielic przez\b", "przez", s)
    s = _sub(r"\bdodac\b", "plus", s)
    s = _sub(r"\bodjac\b", "minus", s)
    return " ".join(s.split())


def _items(text: str) -> list[str]:
    """A list's numbers: "czterech, ośmiu i dwunastu" -> czterech, ośmiu,
    dwunastu; "dwa i pół i trzy" -> dwa i pół, trzy."""
    words = text.replace(",", " , ").split()
    items, current = [], []
    for index, word in enumerate(words):
        half = word == "i" and index + 1 < len(words) and fold(words[index + 1]) in ("pol", "cwierc")
        if word in (",", "oraz") or (word == "i" and not half):
            if current:
                items.append(" ".join(current))
            current = []
        else:
            current.append(word)
    if current:
        items.append(" ".join(current))
    return items


def solve(q, places=DIGITS):
    s = fold(asked(q))
    fmt = partial(common.fmt, places=places)

    def match(pattern):
        return re.fullmatch(pattern, s)

    if m := match(rf"pierwiastek (?:szescienny|trzeciego stopnia) (?:z|ze) {N}"):
        v = parse_num(m.group(1))
        return None if v is None else fmt(round(math.copysign(abs(v) ** (1 / 3), v), 6))
    if m := match(rf"pierwiastek(?: kwadratowy| drugiego stopnia)? (?:z|ze) (?:liczby )?{N}"):
        v = parse_num(m.group(1))
        return None if v is None or v < 0 else fmt(math.sqrt(v))
    if m := match(rf"silnia (?:z |ze )?(?:liczby )?{N}|{N} silnia"):
        v = parse_num(m.group(1) or m.group(2))
        return fmt(math.factorial(int(v))) if v is not None and v.is_integer() and 0 <= v <= 20 else None
    if m := match(rf"{N} do kwadratu|kwadrat (?:liczby )?{N}"):
        v = parse_num(m.group(1) or m.group(2))
        return None if v is None else fmt(v**2)
    if m := match(rf"{N} do szescianu|szescian (?:liczby )?{N}"):
        v = parse_num(m.group(1) or m.group(2))
        return None if v is None else fmt(v**3)
    if m := match(rf"{N} do (?:potegi )?(\w+)(?: potegi)?"):
        v, e = parse_num(m.group(1)), POWERS.get(m.group(2))
        if v is not None and e is not None:
            return fmt(v**e)
    if m := match(rf"{N} do potegi {N}"):
        v, e = parse_num(m.group(1)), parse_num(m.group(2))
        return None if v is None or e is None or e > 64 else fmt(v**e)
    if m := match(rf"{N} procent (?:z|ze|od) {N}"):
        a, b = parse_num(m.group(1)), parse_num(m.group(2))
        return None if a is None or b is None else fmt(round(a * b / 100, 6))
    if m := match(rf"(?:polowa|pol) (?:z |ze )?{N}"):
        b = parse_num(m.group(1))
        return None if b is None else fmt(round(b / 2, 6))
    if m := match(rf"(jedna trzecia|dwie trzecie|jedna czwarta|trzy czwarte|cwierc|jedna piata|jedna dziesiata) (?:z|ze) {N}"):
        b = parse_num(m.group(2))
        return None if b is None else fmt(round(FRACTIONS[m.group(1)] * b, 6))
    if m := match(r"srednia(?: arytmetyczna)? (?:z |ze |liczb )?(.+)"):
        vals = [parse_num(p) for p in _items(m.group(1))]
        if len(vals) >= 2 and None not in vals:
            return fmt(round(sum(vals) / len(vals), 6))
        return None
    if m := match(r"suma (?:z |ze |liczb )?(.+)"):
        vals = [parse_num(p) for p in _items(m.group(1))]
        return fmt(sum(vals)) if len(vals) >= 2 and None not in vals else None
    if "procent" in s:
        return None
    # Chains of + - * /.
    ops = {"plus": "+", "minus": "-", "razy": "*", "przez": "/"}
    tokens = re.split(r"\s+(plus|minus|razy|przez)\s+", s)
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


MONEY = re.compile(r"(?<!\w)(złot\w*|zl|zł|euro|dolar\w*|funt\w*|jen\w*|grosz\w*)(?!\w)", re.IGNORECASE)
_MONEY = {"zlot": ("złoty", "złote", "złotych", "złotego"), "euro": ("euro", "euro", "euro", "euro"),
          "dolar": ("dolar", "dolary", "dolarów", "dolara"), "funt": ("funt", "funty", "funtów", "funta"),
          "jen": ("jen", "jeny", "jenów", "jena"), "grosz": ("grosz", "grosze", "groszy", "grosza")}


def _money(word: str) -> str:
    """"złotych" -> "zlot": the money's own."""
    word = fold(word.lower())
    return "zlot" if word in ("zl",) else next((k for k in _MONEY if word.startswith(k)), word)


def answer(text: str, settings: Settings):
    """With money in it ("15 % z 80 zł"), in that money: "To 12 złotych."."""
    money = {_money(m) for m in MONEY.findall(text)}
    if len(money) > 1:
        return None  # a conversion: not arithmetic
    plain = MONEY.sub(" ", text) if money else text
    found = solve(plain, settings.digits)
    if found is not None and money:
        value = float(found.replace(",", "."))
        noun = count(value, *_MONEY[money.pop()]).split(" ", 1)[1]
        found = f"{found} {noun}"
    expression = " ".join(asked(plain).split())
    return reply(found, text, f"{expression} to {found}" if expression and found else None,
                 specific=f"To {found}." if found else None)
