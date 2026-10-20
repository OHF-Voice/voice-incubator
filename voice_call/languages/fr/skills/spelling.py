"""How French asks for a spelling ("comment s'écrit nécessaire", "épelle
rythme", "comment on écrit ça") and says one, its accents named ("E accent
aigu", "C cédille"; voice_call/skills/spelling.py)."""

from __future__ import annotations

import re

from voice_call.skills.common import Reply
from voice_call.skills.spelling import Settings, key

EXAMPLES = ("comment s'écrit nécessaire", "épelle rythme")

ASK_RE = re.compile(
    r"^(?:hé |hey |ok )?(?:"
    r"(?:comment (?:s'écrit|s'épelle|on écrit|on épelle|écrit[- ]on|épelle[- ]t[- ]on|tu écris|tu épelles|écris[- ]tu|épelles[- ]tu)"
    r"|épelle(?:[- ]moi)?|épelez(?:[- ]moi)?)"
    r"\s+(?:le mot\s+)?(?P<asked>.+?)"
    r"|(?:peux[- ]tu|pouvez[- ]vous|tu peux|vous pouvez) (?:m'|me )?épeler (?:le mot\s+)?(?P<asked_too>.+?)"
    r"|comment (?:le mot\s+)?(?P<how_is>.+?) s'écrit(?:[- ]il)?"
    r")\s*[?.!]*$",
    re.IGNORECASE,
)
WORD_RE = re.compile(r"[a-zà-ÿœæ][a-zà-ÿœæ'\-]*", re.IGNORECASE)

SAID = {" ": "espace", "-": "trait d'union", "'": "apostrophe"}
"""What isn't a letter or a digit, said."""

ACCENTED = {
    "é": "E accent aigu", "è": "E accent grave", "ê": "E accent circonflexe", "ë": "E tréma",
    "à": "A accent grave", "â": "A accent circonflexe", "î": "I accent circonflexe", "ï": "I tréma",
    "ô": "O accent circonflexe", "ù": "U accent grave", "û": "U accent circonflexe", "ü": "U tréma",
    "ç": "C cédille", "ÿ": "Y tréma", "œ": "O, E", "æ": "A, E",
}
"""A letter with its accent, as French spells it out."""


def letters(spelled: str) -> str:
    out: list[str] = []
    for c in spelled.strip():
        if c.lower() in ACCENTED:
            out.append(ACCENTED[c.lower()])
        elif c.isalnum():
            out.append(c.upper())
        elif c in SAID and out and out[-1] not in SAID.values():
            out.append(SAID[c])
    while out and out[-1] in SAID.values():
        out.pop()
    return ", ".join(out)


def answer(text: str, settings: Settings) -> Reply | None:
    m = ASK_RE.match(text.strip().replace("’", "'"))
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
    return Reply(said, f"{said}.", f"{word} s'écrit {said}.", f"{word}. {said}. {word}.")
