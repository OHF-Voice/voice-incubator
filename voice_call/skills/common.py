"""What the skills share (not a skill itself): what a skill is, spoken
numbers, a question cleaned up for the grammars, and an answer said as a
sentence."""

from __future__ import annotations

import calendar
import re
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Skill:
    """A kind of question answered without an LLM. ``answer`` parses the whole
    question *and* computes the answer, or returns None: a partial parse falls
    through rather than guessing."""

    id: str
    name: str
    description: str
    examples: tuple[str, ...]
    icon: str
    answer: Callable[[str], str | None]
    """The question as transcribed -> what is said, or None."""
    kind: str = "exact"
    """exact | live: what the answer is, for the call's trace."""
    last: bool = False
    """Asked after the command fallback (meaning, the LLM) has found nothing,
    not before."""
    live_too: bool = False
    """Asked even when the question is about now or here (live.is_live)."""

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "examples": list(self.examples),
            "icon": self.icon,
            "kind": self.kind,
            "last": self.last,
        }

_UNITS = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
          "sixteen seventeen eighteen nineteen").split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()
WORD_NUM = {w: i for i, w in enumerate(_UNITS)} | {w: 10 * i for i, w in enumerate(_TENS) if w != "_"}
SCALES = {"hundred": 100, "thousand": 1000, "million": 10**6}
FRACTIONS = {"half": 0.5, "a half": 0.5, "one half": 0.5, "quarter": 0.25, "a quarter": 0.25, "third": 1 / 3,
             "a third": 1 / 3, "one third": 1 / 3, "three quarters": 0.75, "two thirds": 2 / 3, "a tenth": 0.1}
ORDINAL_POW = {"square": 2, "squared": 2, "second": 2, "cube": 3, "cubed": 3, "third": 3, "fourth": 4, "fifth": 5,
               "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10}

NUM_RE = r"-?\d[\d,]*(?:\.\d+)?|(?:(?:a|an|zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|and|half|quarter|negative|minus)[\s-]*)+"
N = rf"({NUM_RE})"
"""A number in a grammar, as a group."""


def parse_num(s):
    s = s.strip().lower().replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        pass
    neg = s.startswith(("negative ", "minus "))
    if neg:
        s = s.split(" ", 1)[1]
    m = re.fullmatch(r"(.+?)\s+and\s+(a half|a quarter|three quarters)", s)
    if m:
        a = parse_num(m.group(1))
        return None if a is None else (-1 if neg else 1) * (a + FRACTIONS[m.group(2)])
    if s in FRACTIONS:
        return FRACTIONS[s]
    year = _spoken_year(s)
    if year is not None:
        return year
    total = cur = 0
    seen = False
    for w in re.split(r"[\s-]+", s):
        if w in ("and",):
            continue
        if w in ("a", "an"):
            cur = cur or 1
            seen = True
        elif w in WORD_NUM:
            cur += WORD_NUM[w]
            seen = True
        elif w == "hundred":
            cur = (cur or 1) * 100
            seen = True
        elif w in SCALES:
            total += (cur or 1) * SCALES[w]
            cur = 0
            seen = True
        else:
            return None
    if not seen:
        return None
    return (-1 if neg else 1) * (total + cur)


def _spoken_year(s):
    """"twenty twenty four" -> 2024, "nineteen eighty four" -> 1984, "twenty
    oh five" -> 2005: a year said in pairs, as a transcript writes it."""
    words = re.split(r"[\s-]+", s)
    if len(words) < 2 or words[0] not in WORD_NUM or not 10 <= WORD_NUM[words[0]] <= 20:
        return None
    rest = words[1:]
    if rest[0] == "oh" and len(rest) == 2 and rest[1] in WORD_NUM and WORD_NUM[rest[1]] < 10:
        return WORD_NUM[words[0]] * 100 + WORD_NUM[rest[1]]
    if rest[0] not in WORD_NUM or WORD_NUM[rest[0]] < 10:
        return None
    low = WORD_NUM[rest[0]] + (WORD_NUM[rest[1]] if len(rest) == 2 and WORD_NUM.get(rest[1], 10) < 10 else 0)
    if len(rest) > 2 or (len(rest) == 2 and WORD_NUM.get(rest[1], 10) >= 10) or WORD_NUM[rest[0]] % 10 and len(rest) == 2:
        return None
    return WORD_NUM[words[0]] * 100 + low


def fmt(x):
    if isinstance(x, float) and x.is_integer():
        x = int(x)
    if isinstance(x, float):
        return f"{x:.4g}" if abs(x) < 1e6 else f"{x:,.0f}"
    return f"{x:,}" if abs(x) >= 10000 else str(x)


def clean(q):
    """A question as the grammars read it: lowercase, no lead-in ("hey, can you
    tell me"), no closing punctuation, symbols as words."""
    s = q.lower().strip()
    s = re.sub(r"^(hey|ok|okay|so|um|uh)[,\s]+", "", s)
    s = re.sub(r"^(can you |could you |please |would you )?(tell me |calculate |compute |work out |figure out )?", "", s)
    s = re.sub(r"[?!.]+$", "", s).strip()
    # A transcript breaks a spoken question up: "the average of four? eight and twelve".
    s = re.sub(r"\s*[?!]+\s+|\.\s+", " ", s)
    s = s.replace("×", " times ").replace("÷", " divided by ").replace("%", " percent")
    s = re.sub(r"(\d)\s*\*\s*(\d)", r"\1 times \2", s)
    s = re.sub(r"(\d)\s*/\s*(\d)", r"\1 divided by \2", s)
    s = re.sub(r"(\d)\s*\+\s*(\d)", r"\1 plus \2", s)
    s = re.sub(r"(\d)\s+-\s+(\d)", r"\1 minus \2", s)
    s = re.sub(r"(\d)\s*\^\s*(\d)", r"\1 to the power of \2", s)
    return re.sub(r"\s+", " ", s)


def sentence(answer: str | None) -> str | None:
    """"96" -> "It's 96."; "Yes" -> "Yes."."""
    if answer is None:
        return None
    answer = answer.strip()
    if answer in ("Yes", "No"):
        return answer + "."
    if answer[:1].isdigit() or answer[:1] == "-" or answer.split(" ")[0] in calendar.month_name or answer in calendar.day_name:
        return f"It's {answer}."
    return answer if answer.endswith(".") else answer + "."
