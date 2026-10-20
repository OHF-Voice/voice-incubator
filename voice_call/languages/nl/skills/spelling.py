"""How Dutch asks for a spelling ("hoe spel je noodzakelijk", "spel ritme",
"hoe wordt dat geschreven") and says one (voice_call/skills/spelling.py)."""

from __future__ import annotations

import re

from voice_call.skills.common import Reply
from voice_call.skills.spelling import Settings, key

EXAMPLES = ("hoe spel je noodzakelijk", "spel ritme")

ASK_RE = re.compile(
    r"^(?:hé |hey |ok |oké )?(?:"
    r"(?:hoe (?:spel|schrijf) (?:je|ik|u)|spel(?: me)?)"
    r"\s+(?:het woord\s+)?(?P<asked>.+?)"
    r"|(?:kun je|kunt u|kan je) (?:me )?(?:het woord\s+)?(?P<asked_too>.+?) spellen"
    r"|hoe wordt (?:het woord\s+)?(?P<how_is>.+?) (?:gespeld|geschreven)"
    r")[?.!]*$",
    re.IGNORECASE,
)
WORD_RE = re.compile(r"[a-zà-ÿ][a-zà-ÿ'\-]*", re.IGNORECASE)

SAID = {" ": "spatie", "-": "koppelteken", "'": "apostrof"}
"""What isn't a letter or a digit, said."""


def letters(spelled: str) -> str:
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
    return Reply(said, f"{said}.", f"{word} spel je {said}.", f"{word}. {said}. {word}.")
