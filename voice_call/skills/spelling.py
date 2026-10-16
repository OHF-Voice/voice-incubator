"""Spelling: "how do you spell necessary"."""

from __future__ import annotations

import re

from .common import Skill

SPELL_RE = re.compile(r"^(?:hey |ok |okay )?(?:how (?:do|should|would|can) (?:you|i|we) spell|how is|can you spell|spell|what is the spelling of|what's the spelling of)\s+(?:the word\s+)?\"?([a-z][a-z'\-]*)\"?(?: spelled)?[?.!]*$", re.I)


def answer(text: str) -> str | None:
    m = SPELL_RE.match(text.strip())
    if not m:
        return None
    word = m.group(1)
    # Commas, so the voice says each letter: "A-C-C" comes out as "AC".
    letters = ", ".join(c.upper() for c in word if c.isalpha())
    return f"{word.capitalize()} is spelled {letters}."


SKILL = Skill(
    id="spelling",
    name="Spelling",
    description="Spells a word, letter by letter.",
    examples=("how do you spell necessary", "spell rhythm"),
    icon="mdi:alphabetical-variant",
    answer=answer,
    # First, and even when it reads as live: "how do you spell weather" is not
    # about the weather.
    live_too=True,
)
