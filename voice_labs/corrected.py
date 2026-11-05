"""The corrected transcript: what a command's audio was heard as, written so
that it reads back as the same command -- the speech-to-text the app gives
Home Assistant's pipelines (voice_labs/wyoming_server.py).

Its words are the TDT head's, which spells well but doesn't know the home's
names ("switch on an edge thread" for the Nachtlicht). The CTC matcher found
the command in the audio with its names and numbers, each where it was said;
so each is written over the TDT words said there -- a name as the Names page
has it, a number in digits: "Switch on the Nachtlicht." That stands when it
reads back, as the app reads typed text (Engine.recognize_text), as the same
command with the same slots. Else the TDT words as they are, if they do; else
the command's own words, names and numbers in the order they were heard
("switch on the Nachtlicht"). When none does -- or no command was heard --
it is the TDT words: what was said, as well as it was heard.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from adapt_ctc.numbers import to_python

from .engine import WORD_SECONDS, Decision

NAME_KINDS = {"entity": "entities", "area": "areas", "floor": "floors"}
"""A name's type, as the Names page keeps it (actions.said_names)."""

Words = list[tuple[str, float, float]]
"""The TDT head's words, each with its start and end (seconds)."""


def correct(
    decision: Decision,
    transcript: str,
    words: Words,
    frame_seconds: float,
    names: dict[str, dict[str, str]],
    read: Callable[[str], Decision],
) -> tuple[str, dict[str, Any]]:
    """The transcript to give for ``decision`` -- the command heard in the
    audio, of which the TDT head heard ``transcript`` (``words``, timed) --
    and how it was written: ``as`` (``names``: written over; ``heard``: the
    TDT words; ``command``: the command's own) and what else was ``tried``.
    ``names``: what things are called (said_names); ``read``: the command
    some text reads as."""
    heard = transcript.strip() or decision.transcript
    if decision.command is None:
        return heard, {"as": "heard"}
    tried = []
    for how, text in candidates(decision, heard, words, frame_seconds, names):
        again = read(text)
        if same(decision, again):
            return text, {"as": how, **({"tried": tried} if tried else {})}
        tried.append({"as": how, "text": text, "command": again.command.id if again.command else None, "slots": again.slots})
    return heard, {"as": "heard", "tried": tried, "unread": True}


def candidates(
    decision: Decision, transcript: str, words: Words, frame_seconds: float, names: dict[str, dict[str, str]]
) -> list[tuple[str, str]]:
    """What ``decision``'s command may be written as, best first: the TDT
    words with its names and numbers written over them (only when the CTC
    head found it: theirs are the audio's times), the TDT words, and the
    command's own words. Two commands said together: the TDT words."""
    out = []
    intent = decision.intent
    if decision.then is None and intent is not None:
        if decision.matched_by == "ctc" and words:
            written = written_over(words, intent.spans, frame_seconds, names)
            if written != " ".join(word for word, _, _ in words):
                out.append(("names", written))
        out.append(("heard", transcript))
        out.append(("command", own_words(decision, names)))
    else:
        out.append(("heard", transcript))
    seen: set[str] = set()
    return [(how, text) for how, text in out if text.strip() and not (text in seen or seen.add(text))]


def proper(use: Any, names: dict[str, dict[str, str]]) -> str | None:
    """How a name or number the command was heard with is written: the name
    as the Names page has it (else as it was said), the number in digits --
    None for one said with its unit ("a minute and a half"), which is
    written as said."""
    meaning = use.meaning
    if use.role == "number":
        return None if meaning.unit or meaning.value is None else str(to_python(meaning.value))
    kind = NAME_KINDS.get(meaning.name_type or "")
    return (names.get(kind) or {}).get(str(meaning.name_id)) if kind else None


def written_over(words: Words, spans: list[Any], frame_seconds: float, names: dict[str, dict[str, str]]) -> str:
    """The TDT ``words`` with each name and number of ``spans`` (the CTC
    matcher's, timed in frames of ``frame_seconds``) written over the words
    said in its time -- a word is in it when its middle is (engine.within),
    which takes a frame's difference in where the two heads put it -- or,
    where the TDT head heard none, put in where it was said. The words' own
    punctuation stays."""
    out = [word for word, _, _ in words]
    taken = [False] * len(words)
    changes: list[tuple[int, int, str]] = []
    for use in sorted((u for u in spans if u.role in ("name", "number")), key=lambda u: u.hit.start):
        text = proper(use, names) or (use.meaning.phrase if use.role == "name" else None)
        if text is None:
            continue
        start, end = use.hit.start * frame_seconds, (use.hit.end + 1) * frame_seconds
        inside = [
            i for i, (_, s, e) in enumerate(words)
            if not taken[i] and start <= (s + min(e, s + WORD_SECONDS)) / 2 <= end
        ]
        if inside:
            first, last = inside[0], inside[-1]
            taken[first : last + 1] = [True] * (last + 1 - first)
            changes.append((first, last, text))
        else:
            said = use.hit.start * frame_seconds
            at = next((i for i, (_, s, e) in enumerate(words) if (s + e) / 2 > said), len(words))
            changes.append((at, at - 1, text))
    for first, last, text in sorted(changes, reverse=True):
        if last < first:
            out[first:first] = [text]
            continue
        lead = re.match(r"^[^\w']*", out[first]).group(0)
        tail = re.search(r"[^\w']*$", out[last]).group(0)
        out[first : last + 1] = [lead + text + tail]
    return " ".join(out)


def own_words(decision: Decision, names: dict[str, dict[str, str]]) -> str:
    """The command as its own words: what it was heard with -- keywords,
    names, numbers and free text -- in the order they were heard."""
    assert decision.intent is not None
    slot = ((decision.command.intent if decision.command is not None else {}).get("wildcard") or {}).get("slot")
    parts = []
    for use in sorted(decision.intent.spans, key=lambda u: u.hit.start):
        if use.role == "wildcard":
            parts.append(str(decision.slots.get(slot) or use.meaning.phrase) if slot else use.meaning.phrase)
        elif use.role in ("name", "number"):
            parts.append(proper(use, names) or use.meaning.phrase)
        else:
            parts.append(use.meaning.phrase)
    return " ".join(p for p in parts if p)


def same(heard: Decision, read: Decision) -> bool:
    """Do two decisions run the same: the same command, with the same slots
    (free text alike but for case and punctuation), and the same after it?"""
    if heard.command is None or read.command is None or heard.command.id != read.command.id:
        return False
    if _slots(heard.slots) != _slots(read.slots):
        return False
    if heard.then is None or read.then is None:
        return heard.then is None and read.then is None
    return same(heard.then, read.then)


def _slots(slots: dict[str, Any]) -> dict[str, Any]:
    def value(v: Any) -> Any:
        if isinstance(v, (list, tuple)):
            return sorted(str(value(x)) for x in v)
        if isinstance(v, bool) or v is None:
            return v
        if isinstance(v, (int, float)):
            return float(v)
        return " ".join(re.sub(r"[^\w\s']", " ", str(v).lower()).split())

    return {k: value(v) for k, v in slots.items()}
