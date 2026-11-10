"""The corrected transcript: what a command's audio was heard as, written so
that it reads back as the same command -- the speech-to-text the app gives
Home Assistant's pipelines (voice_incubator/wyoming_server.py).

Its words are the speech-to-text the app uses: the TDT head's, which spells
well but doesn't know the home's names ("switch on an edge thread" for the
Nachtlicht), or an STT entity's. The CTC matcher found the command in the
audio with its names and numbers, each where it was said; so each is written
over the TDT words said there -- a name as the Names page has it, a number in
digits: "Switch on the Nachtlicht." That stands when it reads back, as the
app reads typed text (Engine.recognize_text), as the same command with the
same slots. Else the words as they are, if they do; else the command's own
words, names and numbers in the order they were heard ("switch on the
Nachtlicht"). An STT entity's words have no times to write over: they are
tried first, as they are, and then the TDT head's with the names written
over them. When none reads back -- or no command was heard -- it is the
words as heard.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from typing import Any

from adapt_ctc.numbers import to_python
from adapt_ctc.text import spoken_words

from . import languages
from .engine import Decision

NAME_KINDS = {"entity": "entities", "area": "areas", "floor": "floors"}
"""A name's type, as the Names page keeps it (actions.said_names)."""

Words = list[tuple[str, float, float]]
"""The TDT head's words, each with its start and end (seconds)."""

Names = dict[str, dict[str, list[str]]]
"""Each thing's names, by kind and id, as the Names page has them
(VoiceIncubatorApp.heard_names): its name first."""


def correct(
    decision: Decision,
    transcript: str,
    words: Words | None,
    frame_seconds: float,
    names: Names,
    read: Callable[[str], Decision],
    timed: Callable[[], Words] | None = None,
) -> tuple[str, dict[str, Any]]:
    """The transcript to give for ``decision`` -- the command heard in the
    audio, of which speech-to-text heard ``transcript`` (``words``, timed;
    None from an STT entity, which has no times: then ``timed`` gives the
    TDT head's, should they be needed) -- and how it was written: ``as``
    (``names``: written over; ``heard``: the words as heard; ``command``: the
    command's own) and what else was ``tried``. ``names``: every name of
    each thing (Names); ``read``: the command some text reads as."""
    heard = transcript.strip() or decision.transcript
    if decision.command is None:
        return heard, {"as": "heard"}
    tried = []
    for how, text in candidates(decision, heard, words, frame_seconds, names, timed):
        again = read(text)
        if same(decision, again):
            return text, {"as": how, **({"tried": tried} if tried else {})}
        tried.append({"as": how, "text": text, "command": again.command.id if again.command else None, "slots": again.slots})
    return heard, {"as": "heard", "tried": tried, "unread": True}


def candidates(
    decision: Decision, transcript: str, words: Words | None, frame_seconds: float,
    names: Names, timed: Callable[[], Words] | None = None,
) -> Iterator[tuple[str, str]]:
    """What ``decision``'s command may be written as, best first: the TDT
    words with its names and numbers written over them (only when the CTC
    head found it: theirs are the audio's times), the words as heard, and the
    command's own words -- an STT entity's words (no ``words``) before the
    TDT head's written over (``timed``, asked for only then). Two commands
    said together: the words as heard. Each once."""
    seen: set[str] = set()

    def fresh(text: str) -> bool:
        if not text.strip() or text in seen:
            return False
        seen.add(text)
        return True

    intent = decision.intent
    if decision.then is not None or intent is None:
        if fresh(transcript):
            yield "heard", transcript
        return
    # Timed in the audio: the CTC matcher's, or a sentence's put where it was said.
    ctc = decision.matched_by == "ctc" or bool((decision.sentence or {}).get("timed"))
    if words:
        written = written_over(words, intent.spans, frame_seconds, names) if ctc else ""
        if written != " ".join(word for word, _, _ in words) and fresh(written):
            yield "names", written
    if fresh(transcript):
        yield "heard", transcript
    if words is None and ctc and timed is not None:
        tdt = timed()
        if tdt:
            written = written_over(tdt, intent.spans, frame_seconds, names)
            if fresh(written):
                yield "names", written
    own = own_words(decision, names)
    if fresh(own):
        yield "command", own


def proper(use: Any, names: Names) -> str | None:
    """How a name or number the command was heard with is written: the name
    as the Names page has it -- the one of the thing's names that was said
    ("Side Door" after "garage", not its whole house "Garage Side Door") --
    else None (written as it was said); the number in digits, but None for
    one said with its unit ("a minute and a half"), written as said."""
    meaning = use.meaning
    if use.role == "number":
        return None if meaning.unit or meaning.value is None else str(to_python(meaning.value))
    kind = NAME_KINDS.get(meaning.name_type or "")
    if kind is None:
        return None
    language = languages.current().code
    for name in (names.get(kind) or {}).get(str(meaning.name_id)) or []:
        if any(" ".join(spelling) == meaning.phrase for spelling in spoken_words(name, language)):
            return name
    return None


def _word(text: str) -> str:
    return re.sub(r"^[^\w']+|[^\w']+$", "", text).lower()


def written_over(words: Words, spans: list[Any], frame_seconds: float, names: Names) -> str:
    """The TDT ``words`` with each name and number of ``spans`` (the CTC
    matcher's, timed in frames of ``frame_seconds``) written over the words
    said in its time -- or, where the TDT head heard none, put in where it
    was said. The words' own punctuation stays.

    A word is the span's that it starts in -- of all the spans, a filler's
    or a keyword's too ("the" before a name) -- else the one it starts
    nearest, within a frame: the two heads may put it a frame apart, and the
    TDT head's ends are no guide (a short word lasts a frame, the last runs
    on into the silence). A word of the name heard next to its words, left
    out by that, is its too ("side" in time, "door" beside it)."""
    out = [word for word, _, _ in words]
    timed = sorted(spans, key=lambda u: u.hit.start)
    owner: list[Any] = []
    for word, start, end in words:
        best, nearest = None, frame_seconds + 1e-6
        for use in timed:
            low, high = use.hit.start * frame_seconds, (use.hit.end + 1) * frame_seconds
            # Past its end counts half a frame more: a word starting where
            # one span ends is the next's.
            away = low - start if start < low else 0.0 if start < high - 1e-6 else start - high + frame_seconds / 2
            if away < nearest:
                best, nearest = use, away
        if best is not None and start < best.hit.start * frame_seconds and best.role in ("name", "number"):
            # Starting before a name, it is the name's when it is one of its
            # words, or runs on into it ("Neno"): not "the" just before it.
            if _word(word) not in best.meaning.phrase.split() and end <= best.hit.start * frame_seconds + frame_seconds:
                best = None
        owner.append(best)
    changes: list[tuple[int, int, str]] = []
    for use in (u for u in timed if u.role in ("name", "number")):
        text = proper(use, names) or (use.meaning.phrase if use.role == "name" else None)
        if text is None:
            continue
        inside = [i for i, o in enumerate(owner) if o is use]
        if inside:
            first, last = inside[0], inside[-1]
            said = set(use.meaning.phrase.split()) | {_word(w) for w in text.split()}
            while first > 0 and owner[first - 1] is None and _word(out[first - 1]) in said:
                first -= 1
            while last + 1 < len(out) and owner[last + 1] is None and _word(out[last + 1]) in said:
                last += 1
            owner[first : last + 1] = [use] * (last + 1 - first)
            changes.append((first, last, text))
        else:
            said_at = use.hit.start * frame_seconds
            at = next((i for i, (_, s, _) in enumerate(words) if s >= said_at), len(words))
            changes.append((at, at - 1, text))
    for first, last, text in sorted(changes, key=lambda c: (c[0], c[1]), reverse=True):
        if last < first:
            out[first:first] = [text]
            continue
        lead = re.match(r"^[^\w']*", out[first]).group(0)
        tail = re.search(r"[^\w']*$", out[last]).group(0)
        out[first : last + 1] = [lead + text + tail]
    return " ".join(out)


def own_words(decision: Decision, names: Names) -> str:
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
    (free text alike but for case and punctuation), and the same after it --
    the text read leaving no more words unexplained than the audio did (a
    name written twice still runs: "Unlock Garage Garage Side Door door")?"""
    if heard.command is None or read.command is None or heard.command.id != read.command.id:
        return False
    if _slots(heard.slots) != _slots(read.slots):
        return False
    if heard.intent is not None and read.intent is not None and len(read.intent.unexplained) > len(heard.intent.unexplained):
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
