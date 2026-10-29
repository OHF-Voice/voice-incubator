"""How German asks for a spelling ("wie schreibt man notwendig", "buchstabiere
Rhythmus", "wie wird das geschrieben") and says one: "ß" as Eszett
(voice_labs/skills/spelling.py)."""

from __future__ import annotations

import re

from voice_labs.skills.common import Reply
from voice_labs.skills.spelling import Settings, key

EXAMPLES = ("wie schreibt man notwendig", "buchstabiere Rhythmus")

ASK_RE = re.compile(
    r"^(?:hey |ok |okay )?(?:"
    r"(?:wie (?:schreibt|buchstabiert) man|wie (?:schreibe|buchstabiere) ich|buchstabiere(?: mir)?|buchstabier(?: mir)?)"
    r"\s+(?:das wort\s+)?(?P<asked>.+?)"
    r"|(?:kannst du|könntest du) (?:mir )?(?:das wort\s+)?(?P<asked_too>.+?) buchstabieren"
    r"|wie wird (?:das wort\s+)?(?P<how_is>.+?) (?:geschrieben|buchstabiert)"
    r")[?.!]*$",
    re.IGNORECASE,
)
WORD_RE = re.compile(r"[a-zäöüß][a-zäöüß'\-]*", re.IGNORECASE)

SAID = {" ": "Leerzeichen", "-": "Bindestrich", "'": "Apostroph"}
"""What isn't a letter or a digit, said."""

LETTERS = {"ß": "Eszett"}
"""Letters said by name: "ß" has no capital to say."""


def letters(spelled: str) -> str:
    """Each letter or digit, capitalized, and a space, hyphen or apostrophe
    said -- with commas, so the voice says each."""
    out: list[str] = []
    for c in spelled.strip():
        if c in LETTERS:
            out.append(LETTERS[c])
        elif c.isalnum():
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
    asked = key(m.group("asked") or m.group("asked_too") or m.group("how_is"))
    spelled = settings.words.get(asked)
    if spelled is None:
        if not WORD_RE.fullmatch(asked):
            return None
        spelled = asked
    said = letters(spelled)
    if not said:
        return None
    word = spelled[:1].upper() + spelled[1:]
    return Reply(said, f"{said}.", f"{word} schreibt man {said}.", f"{word}. {said}. {word}.")
