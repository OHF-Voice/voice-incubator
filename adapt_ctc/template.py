"""Example sentences written as templates: a small part of Home Assistant's
hassil syntax.

``(a|b)`` is one of the alternatives, ``[a]`` may be left out (``[a|b]``: one
of them, or neither), nested, anywhere -- between words or inside one::

    (turn|switch) on [the] light[s]
    [re]start the (timer|clock)

A template stands for every sentence it can be (``expand``), each matched
word for word like an example written out. For meaning, a few of them that
say every alternative at least once (``covering``) stand for all: the others
are near copies, and each costs an embedding.

Slots (``{area}``) and rules (``<name>``) are not supported: an example hears
nothing, and what is heard comes from keywords.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MAX_SENTENCES = 512
"""The most sentences one template may stand for. Each is searched for in the
CTC evidence on every call: about 0.06 ms a sentence on a desktop, a few
times that on a Raspberry Pi."""

MAX_COVERING = 8
"""The most sentences that stand for a template's meaning."""


class TemplateError(ValueError):
    """A template that can't be read, or stands for too many sentences."""


@dataclass
class _Choice:
    """``(a|b)``, or ``[a|b]`` (``optional``: neither is a choice too)."""

    options: list[list["_Part"]] = field(default_factory=list)
    optional: bool = False


_Part = str | _Choice


def is_template(text: str) -> bool:
    """Whether ``text`` uses template syntax (else it is one sentence)."""
    return any(c in text for c in "()[]|")


def parse(text: str) -> list[_Part]:
    """The template as a sequence of text and choices. Raises TemplateError."""
    for c in "{}<>":
        if c in text:
            raise TemplateError(
                f"{c!r}: slots and rules aren't supported in examples; what is heard comes from keywords"
            )
    sequence, end = _sequence(text, 0, None)
    if end < len(text):
        raise TemplateError(f"{text[end]!r} at {end + 1} closes nothing")
    return sequence


def _sequence(text: str, at: int, closing: str | None) -> tuple[list[_Part], int]:
    """Parts up to ``|`` or ``closing`` (or the end, at the top)."""
    parts: list[_Part] = []
    literal = []
    while at < len(text):
        c = text[at]
        if c in "([":
            if literal:
                parts.append("".join(literal))
                literal = []
            choice, at = _choice(text, at)
            parts.append(choice)
            continue
        if c in ")]|":
            if closing is None:
                what = "'|' outside ( ) or [ ]" if c == "|" else f"{c!r} at {at + 1} closes nothing"
                raise TemplateError(what)
            break
        literal.append(c)
        at += 1
    if literal:
        parts.append("".join(literal))
    return parts, at


def _choice(text: str, start: int) -> tuple[_Choice, int]:
    """``(…|…)`` or ``[…|…]`` from ``start``; returns it and where it ends."""
    opening = text[start]
    closing = ")" if opening == "(" else "]"
    choice = _Choice(optional=opening == "[")
    at = start + 1
    while True:
        option, at = _sequence(text, at, closing)
        choice.options.append(option)
        if at >= len(text):
            raise TemplateError(f"{opening!r} at {start + 1} is never closed")
        if text[at] == "|":
            at += 1
            continue
        if text[at] != closing:
            raise TemplateError(f"{opening!r} at {start + 1} is closed by {text[at]!r}")
        return choice, at + 1


def count(sequence: list[_Part]) -> int:
    """How many sentences the sequence stands for, before dropping repeats."""
    total = 1
    for part in sequence:
        if isinstance(part, _Choice):
            total *= sum(count(option) for option in part.options) + (1 if part.optional else 0)
    return total


def expand(text: str) -> list[str]:
    """Every sentence ``text`` stands for, in order, spaced as words. Raises
    TemplateError."""
    sequence = parse(text)
    total = count(sequence)
    if total > MAX_SENTENCES:
        raise TemplateError(f"stands for {total} sentences, at most {MAX_SENTENCES}")
    return _tidy(_all(sequence))


def covering(text: str) -> list[str]:
    """A few of the sentences ``text`` stands for that say each alternative
    at least once: the i-th takes the i-th alternative of every choice (the
    last, where a choice has fewer; the one without, last, for an optional
    choice). Raises TemplateError."""
    sequence = parse(text)
    return _tidy(_render(sequence, i) for i in range(min(MAX_COVERING, max(1, _widest(sequence)))))


def _all(sequence: list[_Part]) -> list[str]:
    out = [""]
    for part in sequence:
        if isinstance(part, str):
            out = [s + part for s in out]
        else:
            endings = [e for option in part.options for e in _all(option)] + ([""] if part.optional else [])
            out = [s + e for s in out for e in endings]
    return out


def _render(sequence: list[_Part], i: int) -> str:
    out = []
    for part in sequence:
        if isinstance(part, str):
            out.append(part)
        else:
            options = part.options + ([[]] if part.optional else [])
            out.append(_render(options[min(i, len(options) - 1)], i))
    return "".join(out)


def _widest(sequence: list[_Part]) -> int:
    """The most alternatives any choice in it has (an optional one counts
    leaving it out)."""
    widest = 0
    for part in sequence:
        if isinstance(part, _Choice):
            widest = max(widest, len(part.options) + (1 if part.optional else 0))
            widest = max([widest, *(_widest(option) for option in part.options)])
    return widest


def _tidy(sentences) -> list[str]:
    """Spaced as words -- "what's the weather?" with [like] left out too --
    without repeats or empty ones."""
    spaced = (re.sub(r"\s+([.,!?;:])", r"\1", " ".join(s.split())) for s in sentences)
    return [s for s in dict.fromkeys(spaced) if s]
