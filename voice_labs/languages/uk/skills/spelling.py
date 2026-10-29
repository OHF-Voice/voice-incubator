"""How Ukrainian asks for a spelling ("як пишеться необхідний", "продиктуй по
буквах слово кіт", "м'ята як пишеться") and says one: each letter, the ones
the voice would only sound out by name ("Бе", "Ве", "Йот"), the apostrophe
said (voice_labs/skills/spelling.py)."""

from __future__ import annotations

import re

from voice_labs.skills.common import Reply
from voice_labs.skills.spelling import Settings, key

from .numbers import apostrophes

EXAMPLES = ("як пишеться необхідний", "продиктуй по буквах слово м'ята")

ASK_RE = re.compile(
    r"^(?:слухай |скажи |підкажи |а )?(?:"
    r"(?:як (?:правильно )?(?:пишеться|пишуть|написати|писати)|(?:продиктуй|скажи|назви|прочитай)(?: мені)? по буквах"
    r"|по буквах)\s+(?:слово\s+)?(?P<asked>.+?)"
    r"|(?:продиктуй|скажи|назви)(?: мені)? (?:слово )?(?P<asked_too>.+?) по буквах"
    r"|(?:слово )?(?P<how_is>.+?),? як (?:правильно )?(?:пишеться|написати)"
    r")[?.!]*$",
    re.IGNORECASE,
)
WORD_RE = re.compile(r"[a-zа-яіїєґ][a-zа-яіїєґ'\-]*", re.IGNORECASE)

SAID = {" ": "пробіл", "-": "дефіс", "'": "апостроф"}
"""What isn't a letter or a digit, said."""

LETTERS = {"б": "Бе", "в": "Ве", "ж": "Же", "з": "Зе", "й": "Йот", "ч": "Че", "ь": "М'який знак"}
"""Letters said by name: the voice says the others' names from the letter
("К" is "ка"), these only their sound."""


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
    m = ASK_RE.match(apostrophes(text).strip())
    if not m:
        return None
    asked = (m.group("asked") or m.group("asked_too") or m.group("how_is")).strip(" \"«»'")
    spelled = settings.words.get(key(asked))
    if spelled is None:
        asked = " ".join(asked.lower().split())
        if not WORD_RE.fullmatch(asked):
            return None
        spelled = asked
    said = letters(spelled)
    if not said:
        return None
    word = spelled[:1].upper() + spelled[1:]
    return Reply(said, f"{said}.", f"{word} пишеться {said}.", f"{word}. {said}. {word}.")
