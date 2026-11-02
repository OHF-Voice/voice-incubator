"""Two commands said together: "turn on the lights and turn off the TV".

In a language that hears them (its ``compound`` module: English so far), a
sentence whose best match leaves a joining word unexplained ("and", "then",
"and then": ``JOINERS``) -- or that matched nothing, with one in it -- is
heard again as two clauses, split at the joining word: each matched on its
own stretch of the audio, as a command alone would be (typed, on its own
words). Only when the whole doesn't explain it, so "add apples and bananas
to the shopping list" and "play rock and roll" stay one.

A clause that is no command alone borrows from the other (``borrowings``):

- the first, the second's last words -- "turn off the kitchen | and bedroom
  lights": "turn off the kitchen lights";
- the second, the first's first words -- "turn off the lights | and the TV":
  "turn off the TV"; "turn on the lights in the kitchen | and the bedroom":
  "turn on the lights in the bedroom".

The shortest borrowing that is a command with nothing left over is taken.
A second that names nothing acts on what the first named, when it can:
"turn on the kitchen lights and set the brightness to 50" sets the kitchen's
(``target``); and a pronoun in the second is what the first acted on: "turn
on the TV and set its volume to 50%" (the server, voice_labs/context.py).
"And" within an amount -- "an hour and a half" -- joins no commands.

At most two clauses. Both are commands, or it is no command at all: what
was asked is never half done -- the sentence goes on as one that matched
nothing (to the conversation agent, which may well do both), never to the
fallback's meaning or model, which would pick one. A command that asks
first (``confirm``) isn't run with another: nothing is, and it says so.
Both run in order, and what each says is said as one sentence (the
language's ``both``): "Turned off the lights and the TV."
"""

from __future__ import annotations

from typing import Any

from . import languages


def joiners() -> tuple[str, ...]:
    """The app's language's joining words, the longest first; none: one
    sentence is one command."""
    module = languages.current().compound
    return tuple(module.JOINERS) if module is not None else ()


def joining_words() -> set[str]:
    """Each word of a joiner ("and", "then")."""
    return {word for joiner in joiners() for word in joiner.split()}


def leaves_joined(decision: Any, words: list[str]) -> bool:
    """Whether a decision is half of what was said: its match leaves a
    joining word unexplained -- or there is no match, and the sentence
    (``words``) has one between others."""
    joining = joining_words()
    if not joining:
        return False
    intent = getattr(decision, "intent", None)
    if getattr(decision, "command", None) is not None:
        return intent is not None and any(w.text.lower().strip(".,") in joining for w in intent.unexplained)
    return any(w.lower().strip(".,") in joining for w in words[1:-1])


def splits(words: list[str]) -> list[tuple[int, int]]:
    """Where ``words`` may be two clauses: each joiner's (start, end) word
    indexes, words on both sides; "and then" as one. Not one that joins the
    parts of an amount ("an hour and a half": the language's
    ``within_an_amount``)."""
    lowered = [w.lower().strip(".,") for w in words]
    module = languages.current().compound
    out = []
    i = 0
    while i < len(lowered):
        for joiner in joiners():
            parts = joiner.split()
            if lowered[i : i + len(parts)] == parts:
                amount = module is not None and module.within_an_amount(lowered[:i], lowered[i + len(parts) :])
                if 0 < i and i + len(parts) < len(lowered) and not amount:
                    out.append((i, i + len(parts)))
                i += len(parts) - 1
                break
        i += 1
    return out


def borrowings(first: list[str], second: list[str], which: str) -> list[str]:
    """What a clause that is no command alone may be heard as, shortest
    first: the ``first`` with the second's last words, or the ``second``
    with the first's first words."""
    if which == "first":
        return [" ".join(first + second[n:]) for n in range(len(second) - 1, 0, -1)]
    return [" ".join(first[:n] + second) for n in range(1, len(first))]


def target(slots: dict[str, Any], names: dict[str, Any], devices: dict[str, Any]) -> str | None:
    """What a command named, as said again after another that named
    nothing: its devices ("the TV"), else its area ("in the Kitchen"), else
    its floor; None when it named none. English's words so far."""
    if slots.get("entity"):
        said = [devices[e].phrases[0] if e in devices and devices[e].phrases else (names.get("entities", {}).get(e) or {}).get("name") for e in slots["entity"]]
        return " and ".join(f"the {s}" for s in said if s) or None
    for kind, before in (("area", "in the "), ("floor", "")):
        if slots.get(kind):
            said = [(names.get(f"{kind}s", {}).get(i) or {}).get("name") for i in slots[kind]]
            return " and ".join(f"{before}{s}" for s in said if s) or None
    return None


def both(first: str | None, second: str | None) -> str | None:
    """What two commands say, as one: None when both are silent; the one
    that says something; else the language's sentence for both (each
    already at its level of verbosity: "" is the acknowledgement sound)."""
    said = [s for s in (first, second) if s is not None]
    if not said:
        return None
    spoken = [s for s in said if s.strip()]
    if len(spoken) < 2:
        return spoken[0] if spoken else ""
    module = languages.current().compound
    return module.both(spoken[0], spoken[1]) if module is not None else f"{spoken[0]} {spoken[1]}"


def alone(description: str) -> str:
    """Said for a command that asks first, said with another."""
    module = languages.current().compound
    return module.alone(description) if module is not None else description
