"""Skills: questions answered without an LLM, each on or off for the home or
a satellite."""

from __future__ import annotations

import importlib

import pytest

from voice_call import skills
from voice_call.skills import SKILLS, Answer, enabled
from voice_call.skills.common import normalize
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


@pytest.mark.parametrize(
    ("question", "said", "skill"),
    [
        # As typed, or as a speech-to-text that formats what it hears writes it.
        ("What is 50°F in Celsius?", "It's 10 degrees Celsius.", "units"),
        ("What is 50 °F in °C?", "It's 10 degrees Celsius.", "units"),
        ("What is 50℉ in Celsius?", "It's 10 degrees Celsius.", "units"),
        ("What is −5°C in Fahrenheit?", "It's 23 degrees Fahrenheit.", "units"),
        ("What is 50F in C?", "It's 10 degrees Celsius.", "units"),
        ("What is 300K in Celsius?", "It's 26.9 degrees Celsius.", "units"),
        ("What’s 12 × 8?", "It's 96.", "arithmetic"),
        ("What is 12·8?", "It's 96.", "arithmetic"),
        ("What is 12x8?", "It's 96.", "arithmetic"),
        ("What is 10 – 3?", "It's 7.", "arithmetic"),
        ("What is 5 + 3 =", "It's 8.", "arithmetic"),
        ("What is 15% of $80?", "It's 12 dollars.", "arithmetic"),
        ("What's $5 plus $3?", "It's 8 dollars.", "arithmetic"),
        ("What is ½ of 10?", "It's 5.", "arithmetic"),
        ("What is 1/2 of 10?", "It's 5.", "arithmetic"),
        ("What is 5²?", "It's 25.", "arithmetic"),
        ("What is √144?", "It's 12.", "arithmetic"),
        ("How many tablespoons are in ¼ cup?", "It's 4.", "units"),
        ("How many cups in 1½ quarts?", "It's 6.", "units"),
        ("How many cups in 1 1/2 quarts?", "It's 6.", "units"),
        ("How many square feet are in 10 m²?", "It's 107.6.", "units"),
        ("Convert 100km to miles", "It's 62.14 miles.", "units"),
        ("What is 5lbs in kilograms?", "It's 2.268 kilograms.", "units"),
        ("What's 3.5 kg in pounds?", "It's 7.716 pounds.", "units"),  # pounds, not money
        ("How do you spell “necessary”?", "Necessary is spelled N, E, C, E, S, S, A, R, Y.", "spelling"),
        ("How do you spell ‘rhythm’?", "Rhythm is spelled R, H, Y, T, H, M.", "spelling"),
        ("When is April Fools’ Day?", "It's April 1.", "dates"),
        ("What day of the week is July 4th, 2026?", "It's Saturday.", "dates"),
        ("What time is 45 minutes after 3 p.m.?", "It's 3:45 PM.", "clock"),
    ],
)
def test_written_out(question, said, skill):
    assert skills.answer(question) == Answer("exact", said, skill)


@pytest.mark.parametrize(
    ("question", "said"),
    [
        # As the speech models transcribe them, "in" merged or misheard.
        ("What is fifty degrees FN Celsius?", "It's 10 degrees Celsius."),
        ("What's thirty CNF?", "It's 86 degrees Fahrenheit."),
        ("What's thirty CN F.", "It's 86 degrees Fahrenheit."),
        ("What is fifty? Degrees F and degrees C.", "It's 10 degrees Celsius."),
        ("What is ninety eight point six degrees F in Celsius?", "It's 37 degrees Celsius."),
        ("How many cups in one and a half quartz?", "It's 6."),
        ("How many cups in one a half quarts?", "It's 6."),
        ("What's three point five kG in pounds?", "It's 7.716 pounds."),
    ],
)
def test_transcribed(question, said):
    assert skills.answer(question).text == said


def test_normalize():
    assert normalize("What’s 50°F in °C?") == "What's 50 degrees fahrenheit in degrees celsius?"
    assert normalize("½ cup, 1½ cups, 2 1/4 cups, 1/3 cup") == "a half cup, 1.5 cups, 2.25 cups, a third cup"
    assert normalize("what is 1/2") == "what is 1/2"  # a division, left to arithmetic
    assert normalize("80 € in $") == "80 euros in $"
    assert normalize("the 4th at 3pm") == "the 4th at 3pm"  # ordinals and times stay as they are


def test_money_is_live_when_converted():
    assert skills.answer("how many euros is a dollar", last=True) is not None
    assert skills.answer("what is 80 € in dollars", last=True) is not None
    assert skills.answer("what is 5 pounds in dollars", last=True) is not None
    assert skills.answer("what's $5 plus $3", last=True) is None
    assert skills.answer("what's 3.5 kg in pounds", last=True) is None


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
