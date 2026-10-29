"""How English asks for a spelling ("how do you spell necessary", "how is
it spelled") and says one (voice_labs/skills/spelling.py)."""

from __future__ import annotations

import re

from voice_labs.skills.common import Reply
from voice_labs.skills.spelling import Settings, key

EXAMPLES = ("how do you spell necessary", "spell rhythm")

ASK_RE = re.compile(
    r"^(?:hey |ok |okay )?(?:"
    r"(?:how (?:do|should|would|can) (?:you|i|we) spell|can you spell|spell|what is the spelling of|what's the spelling of)"
    r"\s+(?:the word\s+)?(?P<asked>.+?)(?: spelled)?"
    r"|how is (?:the word\s+)?(?P<how_is>.+?) spelled"
    r")[?.!]*$",
    re.IGNORECASE,
)
WORD_RE = re.compile(r"[a-z][a-z'\-]*", re.IGNORECASE)

SAID = {" ": "space", "-": "hyphen", "'": "apostrophe"}
"""What isn't a letter or a digit, said."""


def letters(spelled: str) -> str:
    """Each letter or digit, capitalized, and a space, hyphen or apostrophe
    said -- with commas, so the voice says each: "A-C-C" comes out as "AC"."""
    out: list[str] = []
    for c in spelled.strip():
        if c.isalnum():
            out.append(c.upper())
        elif c in SAID and out and out[-1] not in SAID.values():
            out.append(SAID[c])
    while out and out[-1] in SAID.values():
        out.pop()
    return ", ".join(out)


def answer(text: str, settings: Settings) -> Reply | None:
    m = ASK_RE.match(text.strip())
    if not m:
        return None
    asked = key(m.group("asked") or m.group("how_is"))
    spelled = settings.words.get(asked)
    if spelled is None:
        if not WORD_RE.fullmatch(asked):
            return None
        spelled = asked
    said = letters(spelled)
    if not said:
        return None
    word = spelled[:1].upper() + spelled[1:]
    return Reply(
        said,
        f"{said}.",
        f"{word} is spelled {said}.",
        # As in a spelling bee: the word, its letters, the word.
        f"{word}. {said}. {word}.",
    )
