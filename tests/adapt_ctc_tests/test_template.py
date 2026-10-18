"""Example sentences as templates: a small part of hassil's syntax."""

import re

import pytest

from adapt_ctc.config import ConfigError, parse_config
from adapt_ctc.template import MAX_SENTENCES, TemplateError, covering, expand, is_template


def test_alternatives_and_optional_parts():
    assert expand("(turn|switch) on the light[s]") == [
        "turn on the lights", "turn on the light", "switch on the lights", "switch on the light"]
    # Inside a word, at its start, nested, or a choice of none.
    assert expand("[re]start the (timer|clock)") == [
        "restart the timer", "restart the clock", "start the timer", "start the clock"]
    assert expand("(turn (on|off)|switch it off) the fan") == [
        "turn on the fan", "turn off the fan", "switch it off the fan"]
    assert expand("[please] stop") == ["please stop", "stop"]
    assert expand("(a|)b") == ["ab", "b"]
    # Spaced as words, punctuation where a part was left out, no repeats.
    assert expand("what's [the] weather [like]?") == [
        "what's the weather like?", "what's the weather?", "what's weather like?", "what's weather?"]
    assert expand("(light|light)") == ["light"]
    assert expand("never mind") == ["never mind"] and not is_template("never mind")
    assert is_template("light[s]") and is_template("(a|b)")


def test_a_few_say_every_alternative():
    # For meaning: the i-th takes every choice's i-th, leaving an optional
    # part out last.
    assert covering("(turn|switch) on [the] light[s]") == ["turn on the lights", "switch on light"]
    assert covering("(a|b|c) [d]") == ["a d", "b", "c"]
    assert covering("never mind") == ["never mind"]


@pytest.mark.parametrize(
    ("template", "message"),
    [
        ("turn on (the light", "'(' at 9 is never closed"),
        ("turn on the light]", "']' at 18 closes nothing"),
        ("(a|b]", "'(' at 1 is closed by ']'"),
        ("on|off", "'|' outside ( ) or [ ]"),
        ("turn on the {area} lights", "slots and rules aren't supported"),
        ("play <music>", "slots and rules aren't supported"),
        ("(a|b) " * 10, f"stands for 1024 sentences, at most {MAX_SENTENCES}"),
    ],
)
def test_what_cant_be_a_template(template, message):
    with pytest.raises(TemplateError, match=re.escape(message)):
        expand(template)


def test_an_intent_has_every_sentence():
    config = parse_config({"language": "en", "intents": {"Lights": {"examples": ["(turn|switch) on [the] light[s]", "lights please"]}}})
    intent = config.intents["Lights"]
    assert len(intent.examples) == 9 and "switch on light" in intent.examples
    assert intent.templates == ("(turn|switch) on [the] light[s]", "lights please")
    assert intent.meaning_examples == ("turn on the lights", "switch on light", "lights please")
    with pytest.raises(ConfigError, match=r"intents\.Lights\.examples: 'turn on \(the': '\(' at 9 is never closed"):
        parse_config({"language": "en", "intents": {"Lights": {"examples": ["turn on (the"]}}})
    # A sentence two intents' templates both stand for, and from which.
    with pytest.raises(ConfigError, match=r"'turn on the lights' \(from '\(switch\|turn\) on the lights'\) is also an example of A"):
        parse_config({"language": "en", "intents": {
            "A": {"examples": ["turn on the light[s]"]}, "B": {"examples": ["(switch|turn) on the lights"]}}})
