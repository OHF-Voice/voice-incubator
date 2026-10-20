"""How Italian asks for a spelling ("come si scrive necessario", "fai lo
spelling di ritmo", "puoi fare lo spelling di perché") and says one, an
accent named with its letter ("E con l'accento acuto";
voice_call/skills/spelling.py)."""

from __future__ import annotations

import re

from voice_call.skills.common import Reply
from voice_call.skills.spelling import Settings, key

from .numbers import loose

EXAMPLES = ("come si scrive necessario", "fai lo spelling di ritmo")

ASK_RE = re.compile(
    loose(
        r"^(?:ehi |ok |senti |scusa )?(?:mi sai dire |sai dirmi |dimmi |mi dici |puoi dirmi |potresti dirmi )?(?:"
        r"come (?:si scrive|si scrivono|si compita|scrivo|scrivi|si fa lo spelling di|fai lo spelling di)"
        r"(?: correttamente)?\s+(?:la parola\s+|il nome\s+|il cognome\s+)?(?P<asked>.+?)"
        r"|(?:(?:mi )?(?:puoi|potresti|sai|riesci a) (?:fare|farmi|fammi)|fammi|fai|mi fai|fare)?\s*(?:lo )?spelling"
        r"(?: della parola| del nome| del cognome| di| del| della| dello)\s+(?P<asked_too>.+?)"
        r"|(?:compitami|compita|scandisci|spellami|spella)\s+(?:la parola\s+)?(?P<spell>.+?)"
        r"|(?:la parola )?(?P<how_is>.+?),? come si scrive"
        r")\s*[?.!]*$"
    ),
    re.IGNORECASE,
)
WORD_RE = re.compile(r"[a-zàèéìíòóùú][a-zàèéìíòóùú'\-]*", re.IGNORECASE)

SAID = {" ": "spazio", "-": "trattino", "'": "apostrofo"}
"""What isn't a letter or a digit, said."""

ACCENTED = {
    "à": "A con l'accento", "è": "E con l'accento grave", "é": "E con l'accento acuto", "ì": "I con l'accento",
    "í": "I con l'accento acuto", "ò": "O con l'accento", "ó": "O con l'accento acuto", "ù": "U con l'accento",
    "ú": "U con l'accento acuto", "ç": "C con la cediglia", "ñ": "N con la tilde", "ä": "A con la dieresi",
    "ë": "E con la dieresi", "ï": "I con la dieresi", "ö": "O con la dieresi", "ü": "U con la dieresi",
}
"""A letter with its accent, as Italian spells it out: "e" has two (the "è"
of "caffè", the "é" of "perché"), the others one."""


def letters(spelled: str) -> str:
    """Each letter or digit, capitalized, and a space, hyphen or apostrophe
    said -- with commas, so the voice says each."""
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
    asked = key(m.group("asked") or m.group("asked_too") or m.group("spell") or m.group("how_is"))
    spelled = settings.words.get(asked)
    if spelled is None:
        if not WORD_RE.fullmatch(asked):
            return None
        spelled = asked
    said = letters(spelled)
    if not said:
        return None
    word = spelled[:1].upper() + spelled[1:]
    return Reply(said, f"{said}.", f"{word} si scrive {said}.", f"{word}. {said}. {word}.")
