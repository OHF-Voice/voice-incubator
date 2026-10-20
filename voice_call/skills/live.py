"""Whether a question is about now or here -- the weather, news, scores,
prices, what's open. Not a skill: it keeps such questions away from the
skills (a number in "who won the 2024 final" is not arithmetic), which leave
them to the conversation agent, or else the Unknown command. How is the app's
language's (its pack's ``skills/live.py``)."""

from __future__ import annotations

from .. import languages


def is_live(q: str) -> bool:
    return languages.grammar("live").is_live(q)
