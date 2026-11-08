"""A home's sentences as one word graph (adapt_ctc/grammar.py)."""

import shutil
from pathlib import Path

import pytest

from adapt_ctc.config import parse_config
from adapt_ctc.grammar import ANY, PhraseSet, Word, build, keyword_phrases, path_cost, write_fst

from .conftest import toy_vocab

HOME = {
    "language": "en",
    "filler": ["the", "a", "please"],
    "name_cues": ["the"],
    "numbers": {"detect": {"min": 0, "max": 200}},
    "names": {
        "areas": {"kitchen": "Kitchen", "living_room": {"name": "Living Room", "aliases": ["Den"]}},
        "entities": {"light.ceiling": "Ceiling Light", "lock.front_door": "Front Door"},
    },
    "intents": {
        "TurnOnLights": {
            "require": [["turn on", "turn ... on"], ["light", "lights"]],
            "optional": [["in"]],
            "names": {"area": {"count": 1, "optional": True}},
        },
        "Lock": {"require": [["lock"]], "names": {"entity": {"count": 1, "domains": ["lock"]}}},
        "SetBrightness": {
            "require": [["brightness"]],
            "optional": [["set"], ["to"], ["percent"]],
            "number": {"min": 0, "max": 100},
        },
        "StartTimer": {
            "require": [["timer"], ["minutes", "minute", "hours", "hour"]],
            "optional": [["set"], ["for"]],
            "number": {"min": 0.5, "max": 100, "step": 0.5, "units": ["minute", "hour"]},
        },
        "AddItem": {
            "require": [["add"], ["to my list"]],
            "wildcard": {"slot": "item", "after": ["add"], "required": True},
        },
        "Nevermind": {"examples": ["never mind", "forget (it|that)"]},
    },
}


def graph(**kwargs):
    return build(parse_config(HOME), any_cost=None, **kwargs)


def says(g, sentence: str) -> bool:
    return g.accepts(sentence.split()) is not None


def test_a_command_says_its_words_in_any_order():
    g = graph(intents=["TurnOnLights"])
    for sentence in [
        "turn on the lights", "turn on the kitchen lights", "turn on the lights in the den",
        "please turn on the living room light", "turn the kitchen lights on", "lights turn on",
    ]:
        assert says(g, sentence), sentence
    # The required words, and only the command's words.
    assert not says(g, "turn on the kitchen")
    assert not says(g, "turn off the lights")
    assert not says(g, "on the lights turn")  # a split phrase keeps its order


def test_names_are_the_ones_the_command_takes():
    g = graph(intents=["Lock"])
    assert says(g, "lock the front door")
    assert not says(g, "lock the ceiling light")  # not a lock
    assert not says(g, "lock")  # the name is required
    # A command that needs a name the home doesn't have has no sentences.
    home = {**HOME, "names": {"areas": HOME["names"]["areas"]}}
    g = build(parse_config(home), any_cost=None, intents=["Lock"])
    assert not says(g, "lock") and not says(g, "lock the front door")


def test_numbers_and_durations():
    g = graph(intents=["SetBrightness", "StartTimer"])
    assert says(g, "set the brightness to fifty percent")
    # Any number the config listens for, for the matcher to refuse.
    assert says(g, "set the brightness to one hundred fifty percent")
    assert says(g, "set a timer for ten minutes")
    assert says(g, "set a timer for an hour and a half")
    assert says(g, "start a five minute timer") is False  # "start" isn't its word
    assert says(g, "set a five minute timer")  # the singular before a noun
    assert not says(g, "set a timer")  # the number is required


def test_examples_as_written_and_no_free_text():
    g = graph(intents=["Nevermind", "AddItem"])
    assert says(g, "never mind") and says(g, "forget that") and says(g, "please forget it")
    assert not says(g, "forget")
    # Free text can't be listed: a command that needs it says nothing.
    assert not says(g, "add milk to my list") and not says(g, "add to my list")


def test_the_wake_phrase_comes_first_and_anything_around_it():
    g = build(parse_config(HOME), wake=["okay nabu"], intents=["Nevermind"], any_cost=None)
    assert says(g, "okay nabu never mind") and not says(g, "never mind")
    assert says(g, "okay nabu never mind thanks")  # the language's extras
    g = build(parse_config(HOME), wake=["okay nabu"], intents=["Nevermind"])
    assert says(g, "never mind")  # anything at all before, at a cost
    assert any(word == ANY for _, _, word, _ in g.arcs) and ANY not in g.words
    assert Word("nabu", "wake") in g.words and Word("nabu") not in g.words


def test_a_phrase_set_shares_its_starts_and_ends():
    phrases = [p for text in ["fifty percent", "sixty percent"] for p in keyword_phrases(text, "en")]
    shared = PhraseSet.of(phrases)
    # fifty or sixty, then one "percent".
    assert sum(len(arcs) for arcs in shared.children.values()) == 3
    assert len(shared.accepting) == 1
    # "Fifty" alone ends where "sixty" can't: they no longer share "percent".
    alone = PhraseSet.of([*phrases, *keyword_phrases("fifty", "en")])
    assert sum(len(arcs) for arcs in alone.children.values()) == 4


def test_the_path_cost_is_how_far_from_what_was_heard():
    vocab = toy_vocab()
    log_probs, _ = vocab.text_log_probs("turn on the kitchen lights".split())
    said = [(w, "strict") for w in "turn on the kitchen lights".split()]
    assert path_cost(log_probs, vocab, said) == pytest.approx(0.0, abs=1e-3)
    other = [(w, "strict") for w in "turn on the bedroom lights".split()]
    assert path_cost(log_probs, vocab, other) > 10
    # A word left out is heard as anything, at a cost -- free in the wake word.
    shorter = [(w, "strict") for w in "turn on the lights".split()]
    assert path_cost(log_probs, vocab, shorter) > 0.5
    assert path_cost(log_probs, vocab, said[2:], free=12) == pytest.approx(0.0, abs=1e-3)


TOOLS = Path(__file__).resolve().parents[2] / "models/wake-kaldi-tools/openfst/bin"


@pytest.mark.skipif(shutil.which("fstcompile") is None and not TOOLS.is_dir(), reason="needs OpenFst")
def test_the_graph_is_written_for_sherpa(tmp_path):
    fst = write_fst(graph(intents=["Nevermind"]), toy_vocab(), tmp_path, TOOLS if TOOLS.is_dir() else None)
    assert fst.exists() and fst.stat().st_size > 0
    words = [line.split("\t") for line in (tmp_path / "words.txt").read_text().splitlines()]
    assert {(text, mode) for _, text, mode in words} >= {("never", "strict"), ("mind", "strict")}
