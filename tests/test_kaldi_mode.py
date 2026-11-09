"""Kaldi mode (voice_labs/kaldi_mode.py): what it hears, its grammar, its
pronunciations, and the decisions the engine makes of what it heard."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from voice_labs import kaldi_mode
from voice_labs.engine import Engine, Satellite
from voice_labs.kaldi_mode import Heard, KaldiMode, Sentences, words_of
from voice_labs.store import compile_store

ROOT = Path(__file__).parent.parent
CTC = ROOT / "models/speech-to-phrase/stt_en_parakeet_tdt_ctc_110m-int8"
WAV = Path(__file__).parent / "okay_nabu_turn_on_the_lights.wav"
OFFICE = Satellite("s", "Office", "office", "Office")
STORE = {
    "commands": {
        "lights_on": {"description": "turn on the lights", "examples": ["turn on the lights", "lights on"], "response": "On."},
        "unlock": {"description": "unlock the door", "require": [["unlock"]], "response": "Unlocked."},
        "add_item": {
            "description": "add to the shopping list", "examples": ["add milk to the shopping list"],
            "require": [["add"], ["shopping list"]], "optional": [["to"], ["the"]],
            "wildcard": {"slot": "item", "after": ["add"], "before": ["to"], "bounded": True},
            "response": "Added.",
        },
        "never_mind": {"from": "satellites/never_mind"},
    },
}


def _engine(store) -> Engine:
    if not CTC.is_dir():
        pytest.skip("no CTC model")
    engine = Engine(CTC, None, None, threads=2)
    engine.build(compile_store(store))
    return engine


@pytest.fixture(name="engine", scope="module")
def fixture_engine():
    return _engine(STORE)


def _sentences(engine: Engine) -> Sentences:
    recognizer = engine.recognizer
    return kaldi_mode.sentences(recognizer.config, lambda intent, text: engine._free_text(recognizer, intent, text))


def test_only_example_sentences_are_heard(engine):
    heard = _sentences(engine)
    said = {" ".join(s) for s in heard.all}
    assert {"turn on the lights", "lights on", "add milk to the shopping list", "never mind"} <= said
    # Keywords alone are not heard; free text with no value list, only as written.
    assert heard.notes["unlock"] == {"why": "examples"}
    assert heard.notes["add_item"] == {"why": "list", "list": "item"}


def test_a_value_list_named_as_the_slot_is_its_free_text(engine):
    store = {**STORE, "lists": {"item": {"coffee filters": ["coffee filters"], "eggs": ["eggs"]}}}
    listed = _engine(store)
    heard = _sentences(listed)
    said = {" ".join(s) for s in heard.by_intent["add_item"]}
    assert said == {"add coffee filters to the shopping list", "add eggs to the shopping list"}
    assert "add_item" not in heard.notes


def test_the_grammar():
    lines = kaldi_mode._grammar([("turn", "on"), ("turn", "off")], ([("um",)], [("please",)]))
    # One "turn" from the sentence's start: a shared-prefix tree.
    assert sum(line.startswith("2 ") and " turn turn " in line for line in lines) == 1
    # Epsilons are #0 in and nothing out, as Kaldi's grammars have them.
    assert all("#0 <eps>" in line for line in lines if "#0" in line)
    assert lines[-1] == "5"
    # Extras are their words' twins: heard, left out of what was said.
    assert any(line.startswith("0 0 <unk> <unk>") for line in lines)
    assert any(" __um __um " in line for line in lines) and any(" __please __please " in line for line in lines)


def test_pronunciations_are_checked():
    phones = {"h", "ˈɛ", "l", "oʊ"}
    assert kaldi_mode.validate(" h  ˈɛ l oʊ ", phones) == "h ˈɛ l oʊ"
    with pytest.raises(ValueError, match="x"):
        kaldi_mode.validate("h x", phones)
    with pytest.raises(ValueError):
        kaldi_mode.validate("  ", phones)


def _heard(text: str, margin: float, background: list[str] | None = None, answer: str | None = None) -> Heard:
    words = [(w, 0.0, 0.1) for w in text.split()]
    answer_words = [(w, 0.0, 0.1) for w in (answer if answer is not None else text).split()]
    return Heard(words=words, cost=margin * 100, frames=100, answer_words=answer_words, answer_cost=margin * 100,
                 background=0.0, background_words=["something"] if background is None else background, seconds=0.01)


class FakeKaldi:
    """Says what it is told to."""

    ready = True

    def __init__(self, heard: Heard) -> None:
        self.heard = heard

    def decode(self, audio):
        return self.heard


def _kaldi(engine: Engine, heard: Heard) -> Engine:
    engine.kaldi = FakeKaldi(heard)  # type: ignore[assignment]
    engine.recognizer_choice = "kaldi"
    return engine


def test_a_sentence_heard_is_matched_as_typed(engine):
    _kaldi(engine, _heard("turn on the lights", -0.2))
    try:
        decision = engine.recognize_command(np.zeros(16000, dtype=np.float32), OFFICE)
        assert decision.command.id == "lights_on" and decision.matched_by == "kaldi" and decision.text_source == "kaldi"
        assert decision.transcript == "turn on the lights"
        assert decision.sentence["recognizer"] == "kaldi" and decision.sentence["taken"]
        # A call: from just before Kaldi's split.
        decision, split = engine.recognize_call(np.zeros(48000, dtype=np.float32), None, 1.5, OFFICE)
        assert decision.command.id == "lights_on" and split.cut == 1.5
    finally:
        engine.recognizer_choice = "parakeet"


def test_too_far_from_the_background_is_not_understood(engine):
    _kaldi(engine, _heard("turn on the lights", 0.5))
    try:
        decision = engine.recognize_command(np.zeros(16000, dtype=np.float32), OFFICE)
        assert decision.command is None and decision.message and decision.transcript == ""
        assert decision.sentence["text"] == "turn on the lights" and not decision.sentence["taken"]
        # Not a word said, either: woken by mistake, most likely -- Never mind.
        _kaldi(engine, _heard("turn on the lights", 0.5, background=[]))
        decision = engine.recognize_command(np.zeros(16000, dtype=np.float32), OFFICE)
        assert decision.command.id == "never_mind" and decision.instead == {"why": "nothing_heard"}
    finally:
        engine.recognizer_choice = "parakeet"


def test_an_answer_is_heard_among_the_ways_of_answering(engine):
    _kaldi(engine, _heard("turn on the lights", -0.2, answer="yes"))
    try:
        decision = engine.recognize_answer(np.zeros(16000, dtype=np.float32), OFFICE)
        assert decision.command is None and decision.transcript == "yes"
    finally:
        engine.recognizer_choice = "parakeet"


def test_kaldi_hears_a_recorded_call(tmp_path):
    mode = KaldiMode(ROOT, tmp_path)
    if mode.available:
        pytest.skip(mode.available)
    import wave  # pylint: disable=import-outside-toplevel

    heard = Sentences({"lights": [*words_of("turn on the lights", "en"), *words_of("turn off the lights", "en")]})
    built = mode.build(heard, "en", {}, ["yes", "no"])
    try:
        assert built.sentences == 2 and not built.unspeakable
        with wave.open(str(WAV)) as recording:
            audio = np.frombuffer(recording.readframes(recording.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
        result = mode.decode(audio)
        assert result.text == "turn on the lights" and result.taken, result.to_json()
        assert result.background_words
        # The same again: nothing built anew.
        assert mode.build(heard, "en", {}, ["yes", "no"]) is built
    finally:
        mode.close()


class BrokenKaldi(FakeKaldi):
    def decode(self, audio):
        raise RuntimeError("the Kaldi decoder stopped")


def test_parakeet_hears_when_kaldi_fails(engine):
    engine.kaldi = BrokenKaldi(_heard("", 0.0))  # type: ignore[assignment]
    engine.recognizer_choice = "kaldi"
    try:
        decision = engine.recognize_command(np.zeros(16000, dtype=np.float32), OFFICE)
        assert decision.text_source != "kaldi" and decision.matched_by != "kaldi"
    finally:
        engine.recognizer_choice = "parakeet"
