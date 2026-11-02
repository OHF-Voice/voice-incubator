"""Two commands said together (voice_labs/compound.py): the words that join
them, and how what both say is said as one sentence."""

from __future__ import annotations

JOINERS = ("and then", "and", "then")
"""Where one command ends and the next begins; the longest first."""

ARTICLES = {"the", "a", "an", "my", "your"}

UNITS = {"second", "seconds", "minute", "minutes", "hour", "hours", "percent", "degree", "degrees"}
NUMBERS = {
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
    "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty", "thirty",
    "forty", "fifty", "sixty", "seventy", "eighty", "ninety", "hundred", "thousand", "half", "quarter",
}


def both(first: str, second: str) -> str:
    """Two sentences as one: "Turned off the lights." and "Turned off the
    TV." are "Turned off the lights and the TV."; "Turned on the lights." and
    "Turned off the TV." are "Turned on the lights and turned off the TV."."""
    a, b = first.strip(), second.strip()
    if a == b:
        return a
    if _sentences(a) != 1 or _sentences(b) != 1:
        return f"{a} {b}"
    end = b[-1] if b[-1] in ".!?" else "."
    a, b = a.rstrip(".!?"), b.rstrip(".!?")
    aw, bw = a.split(), b.split()
    common = 0
    while common < min(len(aw), len(bw)) - 1 and aw[common].lower() == bw[common].lower():
        common += 1
    # What the verb acts on keeps its article: "... the lights and the TV".
    while common and aw[common - 1].lower() in ARTICLES:
        common -= 1
    # Only the whole verb said once: what is left starts with what it acts
    # on (an article, a name) -- "turned on ... and off the TV" isn't English.
    if common and (bw[common].lower() in ARTICLES or bw[common][:1].isupper()):
        return f"{a} and {' '.join(bw[common:])}{end}"
    return f"{a} and {_lowered(bw)}{end}"


def within_an_amount(before: list[str], after: list[str]) -> bool:
    """Whether "and" joins the parts of one amount, not two commands: "an
    hour and a half", "1 hour and 30 seconds", "two hundred and fifty"."""
    if not before or not after or not (before[-1] in UNITS or _number(before[-1])):
        return False
    first = after[0]
    return _number(first) or (first in ("a", "an") and len(after) > 1 and (after[1] in UNITS or after[1] in ("half", "quarter")))


def _number(word: str) -> bool:
    return word.isdigit() or word in NUMBERS


def alone(description: str) -> str:
    """A command that asks first, said with another: neither is done."""
    return f"Ask me to {description} on its own."


def _sentences(text: str) -> int:
    return sum(1 for i, c in enumerate(text) if c in ".!?" and (i == len(text) - 1 or text[i + 1] == " "))


def _lowered(words: list[str]) -> str:
    """A sentence carried on after "and": its first word lowercase, unless
    it is a name ("TV", "Desk Lamp")."""
    first = words[0]
    name = first[1:] != first[1:].lower() or (len(words) > 1 and words[1][:1].isupper())
    return " ".join([first if name else first[0].lower() + first[1:], *words[1:]])
