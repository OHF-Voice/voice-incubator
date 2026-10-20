"""How Polish asks for a spelling ("jak się pisze żółw?", "przeliteruj
rzeczywistość", "jak napisać chrząszcz") and says one: a letter with a
diacritic by its name, "Zet z kropką", "O z kreską", "A z ogonkiem"
(voice_call/skills/spelling.py)."""

from __future__ import annotations

import re

from voice_call.skills.common import Reply
from voice_call.skills.spelling import Settings, key

EXAMPLES = ("jak się pisze żółw", "przeliteruj chrząszcz")

ASK_RE = re.compile(
    r"^(?:hej |ok |okej |no )?(?:"
    r"(?:jak (?:się )?(?:pisze|piszę|pisać|napisać|literuje|przeliterować)|przeliteruj(?: mi)?|przeliterujesz(?: mi)?)"
    r"\s+(?:słowo\s+|wyraz\s+)?(?P<asked>.+?)"
    r"|(?:czy )?(?:możesz|mógłbyś|mogłabyś) (?:mi )?przeliterować (?:słowo\s+|wyraz\s+)?(?P<asked_too>.+?)"
    r"|(?:słowo |wyraz )?(?P<how_is>.+?),? jak (?:się )?(?:to )?pisze"
    r")[?.!]*$",
    re.IGNORECASE,
)
WORD_RE = re.compile(r"[a-ząćęłńóśźż][a-ząćęłńóśźż'\-]*", re.IGNORECASE)

SAID = {" ": "spacja", "-": "myślnik", "'": "apostrof"}
"""What isn't a letter or a digit, said."""

LETTERS = {
    "ą": "A z ogonkiem", "ć": "C z kreską", "ę": "E z ogonkiem", "ł": "Ł", "ń": "N z kreską", "ó": "O z kreską",
    "ś": "S z kreską", "ź": "Zet z kreską", "ż": "Zet z kropką", "z": "Zet", "w": "Wu",
}
"""Letters said by name: a diacritic with its letter ("ł" has its own), and
the two a voice reads as a sound ("zy", "wy") rather than their names."""


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
    return Reply(said, f"{said}.", f"{word} pisze się: {said}.", f"{word}. {said}. {word}.")
