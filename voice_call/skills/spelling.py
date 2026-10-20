"""Spelling: "how do you spell necessary" -- and, set on its page, how a word
or a phrase of yours is spelled: "colour" for "color", a name the
speech-to-text writes another way, "my last name"."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from . import common
from .common import Reply, Skill, options

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


def key(text: str) -> str:
    """A word or phrase as looked up: lowercase, single spaces, no quotes."""
    text = re.sub(r"[\"“”‘’`]|(?<![a-z])'|'(?![a-z])", " ", text.lower())
    return " ".join(text.split())


@dataclass(frozen=True)
class Settings(common.Settings):
    words: dict[str, str] = field(default_factory=dict)
    """Asked to spell a word or phrase (``key``): how it's spelled."""

    @classmethod
    def load(cls, stored: Any) -> Settings:
        words = options(stored, ("words",)).get("words") or {}
        if not isinstance(words, dict):
            raise ValueError("words: expected {word: how it's spelled}")
        out: dict[str, str] = {}
        for heard, spelled in words.items():
            heard, spelled = key(str(heard or "")), " ".join(str(spelled or "").split())
            if not heard:
                raise ValueError("A word to spell is empty")
            if not any(c.isalnum() for c in spelled):
                raise ValueError(f"“{heard}”: how it's spelled needs a letter")
            if heard in out:
                raise ValueError(f"“{heard}” is given twice")
            out[heard] = spelled
        return cls(out)

    def to_json(self) -> dict[str, Any]:
        return {"words": dict(self.words)}

    @classmethod
    def store(cls, edited: Any) -> Any:
        settings = cls.load(edited)
        return {"words": dict(settings.words)} if settings.words else None


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


DEFAULT = Settings()


def answer(text: str, settings: Settings = DEFAULT) -> Reply | None:
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


SKILL = Skill(
    id="spelling",
    name="Spelling",
    description="Spells a word, letter by letter.",
    examples=("how do you spell necessary", "spell rhythm"),
    icon="mdi:alphabetical-variant",
    answer=answer,
    settings=Settings,
    # First, and even when it reads as live: "how do you spell weather" is not
    # about the weather.
    live_too=True,
)
