"""People: who lives here, who is speaking, and what is theirs.

A person is someone the app knows by name: on the People page with things of
their own (``people.json``), with an enrolled voice (``speakers.json``), or
both. What can be theirs:

================  ==========================================================
``todo_list``     a to-do list (``todo.*``): "add … to my to-do list",
                  "remind me …"
``calendar``      a calendar (``calendar.*``): "what's on my calendar"
``playlist``      a Music Assistant playlist, by name: "play my playlist"
``tts``           the voice replies to them are said in: a text-to-speech
                  entity (or ``builtin``), with ``tts_language`` and
                  ``tts_voice``; else the satellite's
``wake_words``    wake words that are theirs (ids): "Okay Nabu (Mike)",
                  trained on their recordings
================  ==========================================================

**Who is speaking** (``speaking``): the person their voice is identified as;
else the one whose wake word started the call; else, in a home of one
person, them -- unless their voice was heard and it wasn't theirs. A home of
one person needs no voice enrolled, and then identifies none.

**"My"**: a command with ``mine: <thing>`` acts on the speaker's own
(``mine``) -- their to-do list in ``slots.entity``, their playlist in
``slots.query``. Where nobody has one of their own, it acts on the command's
own target, as it would without ``mine``; where someone does, the speaker
must be known (the outcome is ``no_speaker`` otherwise), and acts on the
command's own target when they have none.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

THINGS: dict[str, tuple[str | None, str]] = {
    "todo_list": ("todo", "entity"),
    "calendar": ("calendar", "entity"),
    "playlist": (None, "query"),
}
"""What can be someone's own: its Home Assistant domain (None: a name), and
the slot a command with ``mine`` gets it in."""
VOICE_KEYS = ("tts", "tts_language", "tts_voice")
KEYS = (*THINGS, *VOICE_KEYS, "wake_words")

BY = ("voice", "wake_word", "only_person")
"""How the speaker was known: their voice, the wake word (theirs), or being
the only person in the home."""

ENTITY_ID = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")


@dataclass
class Speaker:
    """Who spoke, as commands see it (``speaker``)."""

    name: str | None = None
    score: float | None = None
    """Their voice's score, when it was identified."""
    by: str | None = None
    """How they were known (BY); None when nobody was."""
    own: dict[str, Any] = field(default_factory=dict)
    """Their things (``people.json``)."""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "score": self.score,
            "by": self.by,
            **{thing: self.own.get(thing) for thing in THINGS},
        }


def names(people: dict[str, Any], voices: list[str] | tuple[str, ...] = ()) -> list[str]:
    """Everyone: with things of their own, or a voice, or both."""
    out: dict[str, str] = {}
    for name in [*people, *voices]:
        out.setdefault(name.lower(), name)
    return sorted(out.values(), key=str.lower)


def find(people: dict[str, Any], name: str | None) -> str | None:
    """``name``'s key in ``people``, case-insensitively."""
    if not name:
        return None
    return next((k for k in people if k.lower() == name.lower()), None)


def owner(people: dict[str, Any], word_id: str | None) -> str | None:
    """Whose wake word it is, if anyone's."""
    if not word_id:
        return None
    return next((name for name, own in people.items() if word_id in (own.get("wake_words") or ())), None)


def speaking(
    people: dict[str, Any],
    voices: list[str] | tuple[str, ...],
    identified: str | None = None,
    score: float | None = None,
    wake_owner: str | None = None,
    refused: bool = False,
) -> Speaker:
    """Who is speaking: the voice ``identified``; else whose wake word it
    was (``wake_owner``); else the only person, unless a voice was heard and
    matched nobody's (``refused``)."""
    everyone = names(people, voices)
    if identified:
        name, by = identified, "voice"
    elif wake_owner and any(n.lower() == wake_owner.lower() for n in everyone):
        name, by, score = next(n for n in everyone if n.lower() == wake_owner.lower()), "wake_word", None
    elif len(everyone) == 1 and not refused:
        name, by, score = everyone[0], "only_person", None
    else:
        return Speaker()
    key = find(people, name)
    return Speaker(name, score, by, dict(people[key]) if key is not None else {})


def mine(thing: str, speaker: dict[str, Any] | None, people: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    """For a command with ``mine: <thing>``: whether it can run (False: the
    speaker isn't known and must be), and the slots it gets."""
    if not any(isinstance(own, dict) and own.get(thing) for own in people.values()):
        return True, {}
    name = (speaker or {}).get("name")
    if not name:
        return False, {}
    key = find(people, name)
    value = people[key].get(thing) if key is not None else None
    if not value:
        return True, {}
    if THINGS[thing][1] == "entity":
        return True, {"entity": [value]}
    return True, {"query": value, "media_type": "playlist"}


def clean(spec: Any) -> dict[str, Any]:
    """A person's things as the People page sends them; raises ValueError."""
    if not isinstance(spec, dict):
        raise ValueError("A person's things are a mapping")
    unknown = sorted(set(spec) - set(KEYS))
    if unknown:
        raise ValueError(f"unknown: {', '.join(unknown)} (one of {', '.join(KEYS)})")
    out: dict[str, Any] = {}
    for key, value in spec.items():
        if key == "wake_words":
            if not isinstance(value, list):
                raise ValueError("wake_words: a list of wake words' ids")
            words = list(dict.fromkeys(str(w).strip() for w in value if str(w or "").strip()))
            if words:
                out[key] = words
            continue
        value = str(value or "").strip()
        if not value:
            continue
        domain = THINGS[key][0] if key in THINGS else None
        if domain and (not ENTITY_ID.match(value) or value.split(".")[0] != domain):
            raise ValueError(f"{key}: a {domain} entity, like {domain}.mine")
        out[key] = value
    return out


def person_name(text: Any) -> str | None:
    """A name as given ("mike" -> "Mike"); None when there is none."""
    name = re.sub(r"[^\w' -]+", "", str(text or "")).strip()
    return " ".join(word.capitalize() for word in name.split()) or None
