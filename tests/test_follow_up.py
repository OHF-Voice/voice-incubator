"""Answers to what the app asked: one that ends the conversation, and a
command's question answered yes or no -- by its words, else by meaning."""

from __future__ import annotations

import numpy as np
import pytest

from voice_labs import follow_up, languages
from voice_labs.agent import asks


@pytest.fixture(autouse=True)
def english():
    languages.use("en")


@pytest.mark.parametrize("text", ["Cancel.", "never mind", "No thanks!", "That's all."])
def test_an_answer_that_only_ends_it(text):
    assert follow_up.ends(text)


@pytest.mark.parametrize("text", ["cancel the pizza timer", "no", "who wrote the iliad"])
def test_an_answer_that_goes_on(text):
    assert not follow_up.ends(text)


@pytest.mark.parametrize(
    ("text", "said"),
    [
        ("Yes.", "yes"), ("Yeah, go ahead.", "yes"), ("Sure thing.", "yes"), ("Please do.", "yes"),
        ("No.", "no"), ("Nope.", "no"), ("Don't do that.", "no"), ("Leave them running.", "no"),
        ("Cancel.", "no"), ("Never mind.", "no"),
        # Both, or neither: not clear by its words.
        ("Okay, no.", None), ("Yes, but not the pizza one.", None), ("Absolutely not.", None),
        ("Make it so.", None), ("", None),
    ],
)
def test_yes_or_no_by_its_words(text, said):
    assert follow_up.yes_or_no(text) == said


class Embedder:
    """Answers placed by hand: "make it so" near "do it", "you bet" between."""

    PLACES = {"do it": [1.0, 0.0, 0.0], "don't": [0.0, 1.0, 0.0], "make it so": [0.95, 0.31, 0.0],
              "you bet": [0.7, 0.7, 0.14], "banana": [0.0, 0.0, 1.0]}

    def embed(self, texts, document=False):
        vectors = [np.array(self.PLACES.get(text, [0.0, 0.0, 1.0])) for text in texts]
        return np.stack([v / np.linalg.norm(v) for v in vectors])


def test_by_meaning_only_when_close_and_clear():
    embedder = Embedder()
    assert follow_up.by_meaning(embedder, "make it so") == "yes"
    assert follow_up.by_meaning(embedder, "you bet") is None  # as close to no
    assert follow_up.by_meaning(embedder, "banana") is None  # close to neither
    assert follow_up.by_meaning(embedder, "") is None


@pytest.mark.parametrize("code", languages.codes())
def test_every_language_can_be_answered(code):
    reading = languages.load(code).reading
    assert not reading.ANSWER_YES & reading.ANSWER_NO
    assert set(reading.YES_SAID) & reading.ANSWER_YES and set(reading.NO_SAID) & reading.ANSWER_NO
    for phrase in (*reading.ANSWER_ENDS, *reading.ANSWER_YES, *reading.ANSWER_NO):
        assert follow_up._words(phrase) == phrase, phrase  # pylint: disable=protected-access


@pytest.mark.parametrize(
    ("text", "asking"),
    [
        ("Anything else?", True), ("What are you passionate about? 🌟", True), ("Want me to? **", True),
        ('Is it on?")', True), ("你好吗？", True),
        ("Done. 😊", False), ("Why? Because.", False), ("Okay!", False), ("", False),
    ],
)
def test_an_answer_that_asks(text, asking):
    """A question mark last, but for what isn't words after it."""
    assert asks(text) is asking
