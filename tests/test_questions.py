"""Questions no command took: exact answers, the live notice, and the LLM's
-- with a stand-in for the model."""

from __future__ import annotations

import pytest

from voice_call.questions import Answer, LIVE_REPLY, Questions, _first_sentence, classify, looks_like_a_question, solve


@pytest.mark.parametrize(
    ("question", "said", "solver"),
    [
        ("How do you spell accommodate?", "Accommodate is spelled A, C, C, O, M, M, O, D, A, T, E.", "spelling"),
        ("What is twelve times eight?", "It's 96.", "arithmetic"),
        ("what's 15 percent of 80", "It's 12.", "arithmetic"),
        ("what is the square root of 144", "It's 12.", "arithmetic"),
        ("convert 100 fahrenheit to celsius", "It's 37.8 degrees Celsius.", "units"),
        ("how many cups are in a quart", "It's 4.", "units"),
        ("what is 5 miles in kilometers", "It's 8.047 kilometers.", "units"),
        ("what day is christmas", "It's December 25.", "calendar"),
        ("Is 2024 a leap year?", "Yes.", "calendar"),
        ("What is the last month of the year?", "It's December.", "calendar"),
        ("what time is 45 minutes after 3 pm", "It's 3:45 PM.", "calendar"),
        # As a transcript writes them.
        ("Is twenty twenty four a leap year?", "Yes.", "calendar"),
        ("What is ninety minutes after two PM?", "It's 3:30 PM.", "calendar"),
        ("What is the average of four? Eight and twelve?", "It's 8.", "arithmetic"),
        ("How should you spell rhythm?", "Rhythm is spelled R, H, Y, T, H, M.", "spelling"),
        ("What is zero degrees Celsius in Fahrenheit?", "It's 32 degrees Fahrenheit.", "units"),
    ],
)
def test_exact_answers(question, said, solver):
    answer = solve(question)
    assert answer is not None and answer.text == said and answer.solver == solver and answer.kind == "exact"


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("what's the capital of France", "knowledge"),
        ("Who wrote The Odyssey?", "knowledge"),
        ("in what year did Columbus reach the Americas", "knowledge"),
        ("what's the weather like tomorrow", "live"),
        ("who won the game last night?", "live"),
        ("what are the top news headlines", "live"),
        ("who's the prime minister of the UK", "live"),
        ("how many euros is a dollar", "live"),
        ("when's the next formula, one race?", "live"),
        ("what time does Cosco close?", "live"),
        ("did Manchester United win", "live"),
        ("thank you", None),
        ("turn on the lights", None),
        ("we need milk", None),
    ],
)
def test_what_kind_of_question(text, kind):
    assert solve(text) is None
    assert classify(text) == kind


def test_only_questions_are_asked():
    assert looks_like_a_question("Is it safe to eat raw cookie dough?")
    assert looks_like_a_question("Okay, define photosynthesis")
    assert looks_like_a_question("the meaning of life?")
    assert not looks_like_a_question("I'm cold")


class StubAnswerer:
    def __init__(self, reply: str) -> None:
        self.reply, self.asked = reply, []

    def answer(self, question):
        self.asked.append(question)
        return _first_sentence(self.reply), 0.05


def test_questions():
    plain = Questions()
    assert plain.exact("what is 7 times 6").text == "It's 42."
    assert plain.answer("what's on the news today") == Answer("live", LIVE_REPLY)
    assert plain.answer("who wrote the odyssey?") is None  # no LLM
    stub = StubAnswerer("**Homer** wrote The Odyssey. It is an epic poem.")
    asking = Questions(stub)
    answer = asking.answer("who wrote the odyssey?")
    assert answer.kind == "knowledge" and answer.text == "Homer wrote The Odyssey." and answer.seconds == 0.05
    assert asking.answer("thank you") is None and stub.asked == ["who wrote the odyssey?"]


def test_days_until(monkeypatch):
    import datetime as dt

    from voice_call import questions

    class Today(dt.date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 15)

    monkeypatch.setattr(questions.dt, "date", Today)
    assert solve("how many days until Christmas").text == "It's 71 days."
    assert solve("how many days until october 16th").text == "It's 1 day."
    assert solve("how many weeks until halloween").text == "It's 2 weeks."
    assert solve("how many days until valentine's day").text == "It's 122 days."
