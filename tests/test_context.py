"""What was last spoken of, for "it": kept by area for a while, and a
pronoun heard again as its names."""

from __future__ import annotations

import pytest

from voice_labs import languages
from voice_labs.context import Context, substitute


@pytest.fixture(autouse=True)
def english():
    languages.use("en")


def test_kept_by_area_for_a_while():
    context = Context(keep_seconds=300)
    context.remember("kitchen", ["lock.front_door", "lock.front_door", "light.porch"])
    assert context.referred("kitchen") == ["lock.front_door", "light.porch"]
    assert context.referred("office") == []
    context.remember("kitchen", [])  # named nothing: what was is kept
    assert context.referred("kitchen") == ["lock.front_door", "light.porch"]
    context.remember("kitchen", ["light.porch"])
    assert context.referred("kitchen") == ["light.porch"]
    assert context.referred("kitchen", now=context._referred["kitchen"].at + 301) == []  # pylint: disable=protected-access
    assert context.referred("kitchen") == []


@pytest.mark.parametrize(
    ("text", "names", "heard"),
    [
        ("Lock it.", ["front door"], "lock front door"),
        ("Turn them off", ["porch light", "lamp"], "turn porch light and lamp off"),
        ("is that locked?", ["front door"], "is front door locked"),
        ("What time is it?", ["front door"], "what time is front door"),
        ("Turn on the lights", ["front door"], None),
        ("Lock it", [], None),
    ],
)
def test_a_pronoun_heard_as_the_names(text, names, heard):
    assert substitute(text, names) == heard


def test_every_language_has_pronouns():
    for code in languages.codes():
        pronouns = languages.load(code).reading.PRONOUNS
        assert pronouns and all(p == p.lower() and " " not in p for p in pronouns), code
