"""How Spanish asks for a spelling ("¿cómo se escribe necesario?", "deletrea
ritmo", "¿puedes deletrearme cigüeña?") and says one: "ñ" as eñe, an accent
as "con tilde" (voice_labs/skills/spelling.py)."""

from __future__ import annotations

import re

from voice_labs.skills.common import Reply
from voice_labs.skills.spelling import Settings, key

from .numbers import loose

EXAMPLES = ("cómo se escribe necesario", "deletrea ritmo")

ASK_RE = re.compile(
    loose(
        r"^(?:oye |hola |vale |ok )?(?:"
        r"(?:cómo se (?:escribe|deletrea)|cómo (?:escribo|deletreo)|deletrea(?:me)?|deletréame)"
        r"\s+(?:la palabra\s+)?(?P<asked>.+?)"
        r"|(?:me )?(?:puedes|podrías) deletrear(?:me)? (?:la palabra\s+)?(?P<asked_too>.+?)"
        r"|(?:la palabra )?(?P<how_is>.+?),? cómo se (?:escribe|deletrea)"
        r")[?.!]*$"
    ),
    re.IGNORECASE,
)
WORD_RE = re.compile(r"[a-záéíóúüñ][a-záéíóúüñ'\-]*", re.IGNORECASE)

SAID = {" ": "espacio", "-": "guion", "'": "apóstrofo"}
"""What isn't a letter or a digit, said."""

LETTERS = {
    "ñ": "Eñe", "á": "A con tilde", "é": "E con tilde", "í": "I con tilde", "ó": "O con tilde", "ú": "U con tilde",
    "ü": "U con diéresis",
}
"""Letters said by name: "ñ" has its own, an accent is said with its letter."""


def letters(spelled: str) -> str:
    """Each letter or digit, capitalized, and a space, hyphen or apostrophe
    said -- with commas, so the voice says each."""
    out: list[str] = []
    for c in spelled.strip():
        if c.lower() in LETTERS:
            out.append(LETTERS[c.lower()])
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
    return Reply(said, f"{said}.", f"{word} se escribe {said}.", f"{word}. {said}. {word}.")
