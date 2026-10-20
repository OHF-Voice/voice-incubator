"""Spelling: "how do you spell necessary" -- and, set on its page, how a word
or a phrase of yours is spelled: "colour" for "color", a name the
speech-to-text writes another way, "my last name". How it is asked and said
is the app's language's (its pack's ``skills/spelling.py``)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .. import languages
from . import common
from .common import Reply, Skill, options


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


def answer(text: str, settings: Settings | None = None) -> Reply | None:
    return languages.grammar("spelling").answer(text, settings or Settings())


SKILL = Skill(
    id="spelling",
    name="Spelling",
    description="Spells a word, letter by letter.",
    icon="mdi:alphabetical-variant",
    answer=answer,
    settings=Settings,
    # First, and even when it reads as live: "how do you spell weather" is not
    # about the weather.
    live_too=True,
)
