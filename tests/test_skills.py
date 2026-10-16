"""Skills: questions answered without an LLM, each on or off for the home or
a satellite."""

from __future__ import annotations

import importlib

import pytest

from voice_call import skills
from voice_call.skills import SKILLS, Answer, enabled
from voice_call.skills.live import LIVE_REPLY


@pytest.mark.parametrize(
    ("question", "said", "skill"),
    [
        ("How do you spell accommodate?", "Accommodate is spelled A, C, C, O, M, M, O, D, A, T, E.", "spelling"),
        ("What is twelve times eight?", "It's 96.", "arithmetic"),
        ("what's 15 percent of 80", "It's 12.", "arithmetic"),
        ("what is the square root of 144", "It's 12.", "arithmetic"),
        ("convert 100 fahrenheit to celsius", "It's 37.8 degrees Celsius.", "units"),
        ("how many cups are in a quart", "It's 4.", "units"),
        ("what is 5 miles in kilometers", "It's 8.047 kilometers.", "units"),
        # An ounce beside a volume is a fluid ounce.
        ("how many ounces are in a cup", "It's 8.", "units"),
        ("what day is christmas", "It's December 25.", "dates"),
        ("Is 2024 a leap year?", "Yes.", "dates"),
        ("What is the last month of the year?", "It's December.", "dates"),
        ("what time is 45 minutes after 3 pm", "It's 3:45 PM.", "clock"),
        # As a transcript writes them.
        ("Is twenty twenty four a leap year?", "Yes.", "dates"),
        ("What is ninety minutes after two PM?", "It's 3:30 PM.", "clock"),
        ("What is the average of four? Eight and twelve?", "It's 8.", "arithmetic"),
        ("How should you spell rhythm?", "Rhythm is spelled R, H, Y, T, H, M.", "spelling"),
        ("What is zero degrees Celsius in Fahrenheit?", "It's 32 degrees Fahrenheit.", "units"),
    ],
)
def test_exact_answers(question, said, skill):
    assert skills.answer(question) == Answer("exact", said, skill)
    assert skills.answer(question, last=True) is None  # asked before the command fallback
    # Turned off: not answered, by it or another.
    assert skills.answer(question, frozenset(SKILLS) - {skill}) is None


@pytest.mark.parametrize(
    "question",
    ["what's the weather like tomorrow", "who won the game last night?", "what are the top news headlines",
     "how many euros is a dollar", "what time does Cosco close?", "how much is 1 bitcoin in dollars"],
)
def test_live_questions(question):
    # Not for the other skills (a number in it is not arithmetic)...
    assert skills.answer(question) is None
    # ...but the live notice, after the command fallback.
    assert skills.answer(question, last=True) == Answer("live", LIVE_REPLY, "live")
    assert skills.answer(question, frozenset(SKILLS) - {"live"}, last=True) is None


def test_spelling_comes_first():
    # "Weather" reads as live; spelling it is not.
    assert skills.answer("how do you spell weather").skill == "spelling"


def test_not_questions():
    for text in ("thank you", "turn on the lights", "we need milk", "what's the capital of France", ""):
        assert skills.answer(text) is None and skills.answer(text, last=True) is None


def test_on_and_off():
    assert enabled() == frozenset(SKILLS)
    # The home's setting, then the satellite's own.
    assert enabled({"units": False}) == frozenset(SKILLS) - {"units"}
    assert enabled({"units": False}, {"units": True, "spelling": False}) == frozenset(SKILLS) - {"spelling"}
    # Only true and false count; unknown skills are ignored.
    assert enabled({"units": "no", "nope": False}, None) == frozenset(SKILLS)


def test_each_skill_is_its_own_module():
    for skill_id, skill in SKILLS.items():
        module = importlib.import_module(f"voice_call.skills.{skill_id}")
        assert module.SKILL is skill and skill.answer.__module__ == module.__name__
        assert skill.name and skill.description and skill.examples and skill.icon.startswith("mdi:")
        # Each says its own examples are its own.
        for example in skill.examples:
            found = skills.answer(example, last=skill.last)
            assert found is not None and found.skill == skill_id, example


def test_days_until(monkeypatch):
    import datetime as dt

    from voice_call.skills import dates

    class Today(dt.date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 15)

    monkeypatch.setattr(dates.dt, "date", Today)
    assert skills.answer("how many days until Christmas").text == "It's 71 days."
    assert skills.answer("how many days until october 16th").text == "It's 1 day."
    assert skills.answer("how many weeks until halloween").text == "It's 2 weeks."
    assert skills.answer("how many days until valentine's day").text == "It's 122 days."
