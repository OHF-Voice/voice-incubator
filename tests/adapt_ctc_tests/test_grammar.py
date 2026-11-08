"""A home's sentences as one word graph (adapt_ctc/grammar.py)."""

import numpy as np
import pytest

from adapt_ctc.config import parse_config
from adapt_ctc.grammar import (
    ANY, PhraseSet, Word, WordGraph, _grammar, build, compile_graph, decode, keyword_phrases, path_cost,
)
from adapt_ctc.matcher import Recognizer

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


def words(text: str) -> list[Word]:
    return [Word(w) for w in text.split()]


def test_the_path_cost_is_how_far_from_what_was_heard():
    vocab = toy_vocab()
    log_probs, _ = vocab.text_log_probs("turn on the kitchen lights".split())
    assert path_cost(log_probs, vocab, words("turn on the kitchen lights")) == pytest.approx(0.0, abs=1e-3)
    assert path_cost(log_probs, vocab, words("turn on the bedroom lights")) > 10
    # A word left out is heard as anything, at a cost -- free in the wake word.
    assert path_cost(log_probs, vocab, words("turn on the lights")) > 0.5
    assert path_cost(log_probs, vocab, words("the kitchen lights"), free=12) == pytest.approx(0.0, abs=1e-3)


SEARCHES = [True, False] if _grammar is not None else [False]


@pytest.mark.parametrize("native", SEARCHES)
def test_the_search_hears_the_sentence_said(native):
    vocab = toy_vocab()
    g = compile_graph(build(parse_config(HOME), intents=["TurnOnLights", "SetBrightness"]), vocab)
    assert g.num_arcs and "kitchen" not in g.unspeakable  # the toy pieces spell few numbers
    for said in ["turn on the kitchen lights", "turn the den lights on", "set the brightness to fifty percent"]:
        log_probs = vocab.text_log_probs(said.split())[0]
        heard = decode(g, log_probs, vocab, native=native)
        assert heard is not None and heard.text == said
        # Each word from where it begins to where the next does.
        starts = [start for start, _ in heard.frames]
        assert starts == sorted(starts) and heard.frames[-1][1] == len(log_probs) - 1
        assert all(end + 1 == start for (_, end), (start, _) in zip(heard.frames, heard.frames[1:]))
    # Nothing heard, nothing said.
    assert decode(g, np.zeros((0, vocab.size), dtype=np.float32), vocab, native=native) is None


def frames(vocab, pieces: list[str]) -> np.ndarray:
    """A frame for each piece, sure of it."""
    ids = {p: i for i, p in vocab.id2piece.items()}
    out = np.full((len(pieces), vocab.size), np.log(0.01 / vocab.size), dtype=np.float32)
    for t, piece in enumerate(pieces):
        out[t, ids[piece]] = np.log(0.99)
    return out


@pytest.mark.parametrize("native", SEARCHES)
def test_a_piece_twice_needs_a_blank_between(native):
    vocab = toy_vocab()
    g = WordGraph()
    end, middle = g.state(), g.state()
    g.finals.add(end)
    g.add(0, end, Word("a"))
    g.add(0, middle, Word("a"))
    g.add(middle, end, Word("a"))
    tokens = compile_graph(g, vocab)
    once = decode(tokens, frames(vocab, ["<blk>", "▁a", "▁a", "<blk>"]), vocab, native=native)
    twice = decode(tokens, frames(vocab, ["<blk>", "▁a", "<blk>", "▁a", "<blk>"]), vocab, native=native)
    assert [w.text for w in once.words] == ["a"] and [w.text for w in twice.words] == ["a", "a"]


@pytest.mark.skipif(_grammar is None, reason="needs the native search")
@pytest.mark.parametrize(("beam", "max_active"), [(16.0, 4000), (4.0, 8)])
def test_the_native_search_is_the_python_one(beam, max_active):
    vocab = toy_vocab()
    g = compile_graph(build(parse_config(HOME), wake=["okay nabu"]), vocab)
    rng = np.random.default_rng(0)
    for said in ["okay nabu turn on the kitchen lights", "set the brightness to fifteen percent",
                 "turn on the bedroom lights", "play music"]:
        log_probs, _ = vocab.text_log_probs(said.split())
        noisy = log_probs + rng.normal(0, 2.0, log_probs.shape).astype(np.float32)
        noisy -= np.logaddexp.reduce(noisy, axis=1, keepdims=True)
        native = decode(g, noisy, vocab, beam, max_active)
        python = decode(g, noisy, vocab, beam, max_active, native=False)
        assert (native is None) == (python is None)
        if native is not None:
            assert native.words == python.words and native.score == pytest.approx(python.score, abs=1e-3)
            assert native.frames == python.frames


@pytest.mark.skipif(_grammar is None, reason="needs the native search")
def test_the_recognizer_hears_its_config_as_sentences():
    vocab = toy_vocab()
    recognizer = Recognizer(parse_config({**HOME, "settings": {"grammar": True}}), vocab=vocab)
    heard, cost = recognizer.hear_sentence(vocab.text_log_probs("turn on the kitchen lights".split())[0])
    assert heard.text == "turn on the kitchen lights" and cost == pytest.approx(0.0, abs=1e-3)
    _, cost = recognizer.hear_sentence(vocab.text_log_probs("turn on the bedroom lights".split())[0])
    assert cost > recognizer.config.settings.grammar_max_cost
    assert Recognizer(parse_config(HOME), vocab=vocab).grammar is None  # not asked for
