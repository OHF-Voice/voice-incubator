"""Questions no command or skill took: the question model's, with a stand-in
for the model."""

from __future__ import annotations

import pytest

from voice_call.questions import Questions, _first_sentence, classify, looks_like_a_question


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
    assert Questions().answer("who wrote the odyssey?") is None  # no LLM
    stub = StubAnswerer("**Homer** wrote The Odyssey. It is an epic poem.")
    asking = Questions(stub)
    answer = asking.answer("who wrote the odyssey?")
    assert answer.kind == "knowledge" and answer.text == "Homer wrote The Odyssey." and answer.seconds == 0.05
    # Not a question, or one about now: never asked.
    assert asking.answer("thank you") is None and asking.answer("what's on the news today") is None
    assert stub.asked == ["who wrote the odyssey?"]
