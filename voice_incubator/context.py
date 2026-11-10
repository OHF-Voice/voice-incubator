"""What was last spoken of, for "it": "is the front door locked?" ... "lock
it".

Each area keeps what its last command acted on or asked about by name --
the devices in its ``entity`` slot (``remember``) -- for as long as its
conversation with the agent is kept (agent.KEEP_SECONDS, five minutes),
until another command names devices. A satellite in no area keeps its own,
as its conversation is (agent.conversation_key).

A sentence with one of the language's pronouns in it (``PRONOUNS``: "it",
"them", "that") is heard again with a device's name in its place, as it is
said from the satellite's area (``substitute``): "lock it" is heard as "lock
front door". Each device spoken of is a reading of its own, as the gazetteer
matcher's plural follow-ups are (``hear_again``): a reading is a command that
acts on its device, or none; the first device's command is the one, with
every device whose reading is that command too -- "lock them" after the
front door and the porch light locks the door. It is taken in place of what
the sentence matched itself: "turn it up" after "is the desk lamp on?" is
the lamp's brightness, not the music's volume. Anything else -- "what time
is it" -- stays as it was heard, and a pronoun with nothing here to refer to
goes on as any other sentence: to the conversation agent, when nothing
matches, which keeps its own conversation and may know what was meant.

Only devices said by name are kept, as the gazetteer matcher's follow-ups
reach only what the previous command named: "turn off the kitchen lights"
then "turn them on" is the satellite's own area's lights, not the kitchen's.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from . import languages

MOST = 5
"""The most devices spoken of that a pronoun is heard as, one at a time."""


@dataclass
class Referred:
    """What a command acted on or asked about, by name."""

    entities: list[str]
    at: float = field(default_factory=time.monotonic)


class Context:
    """What each area last spoke of, by conversation key."""

    def __init__(self, keep_seconds: float) -> None:
        self.keep_seconds = keep_seconds
        self._referred: dict[str, Referred] = {}

    def remember(self, key: str, entities: list[str]) -> None:
        """A command in conversation ``key`` acted on or asked about these."""
        if entities:
            self._referred[key] = Referred(list(dict.fromkeys(entities)))

    def referred(self, key: str, now: float | None = None) -> list[str]:
        """What conversation ``key`` last spoke of, while it is kept."""
        found = self._referred.get(key)
        if found is None:
            return []
        if (time.monotonic() if now is None else now) - found.at > self.keep_seconds:
            del self._referred[key]
            return []
        return list(found.entities)

    def forget(self, key: str | None = None) -> None:
        """Nothing spoken of (in ``key``; else anywhere)."""
        if key is None:
            self._referred.clear()
        else:
            self._referred.pop(key, None)


def substitute(text: str, names: list[str]) -> str | None:
    """``text`` with its first pronoun (the language's ``PRONOUNS``) in the
    names' place -- joined, when there are several -- or None when it has
    none."""
    if not names:
        return None
    say = languages.current()
    pronouns = say.reading.PRONOUNS
    words = re.sub(r"[^\w\s']", " ", text.lower()).split()
    for i, word in enumerate(words):
        if word in pronouns:
            return " ".join([*words[:i], say.speech.join(names), *words[i + 1 :]])
    return None


def hear_again(
    text: str, referred: list[str], said_as: dict[str, str], recognize: Callable[[str], Any]
) -> tuple[Any, list[str]] | None:
    """``text`` heard again as what was spoken of (``referred``, by entity;
    ``said_as``: each one's name as it is said here), a device at a time
    (``recognize``: the engine's recognize_text): the first device's command,
    acting on every device whose reading is that command, and what was
    heard for them -- or None, with no pronoun or no such command."""
    if substitute(text, ["x"]) is None:
        return None
    readings = []
    for entity in referred[:MOST]:
        name = said_as.get(entity)
        heard = substitute(text, [name]) if name else None
        if heard is None:
            continue
        again = recognize(heard)
        if again.command is not None and entity in (again.slots.get("entity") or []):
            readings.append((entity, heard, again))
    if not readings:
        return None
    command = readings[0][2].command.id
    same = [r for r in readings if r[2].command.id == command]
    decision = same[0][2]
    decision.slots = {**decision.slots, "entity": [entity for entity, _, _ in same]}
    return decision, [heard for _, heard, _ in same]
