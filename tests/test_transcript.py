"""Nothing matched in the audio: the keyword matcher on the TDT or STT
transcript, before skills and the fallback (the real CTC model, with
evidence made from text)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from adapt_ctc.text import spoken_words
from voice_call.engine import Engine, Satellite
from voice_call.store import compile_store

CTC = Path(__file__).parent.parent / "models/speech-to-phrase/stt_en_parakeet_tdt_ctc_110m-int8"
STORE = {
    "names": {"entities": {"lock.front_door": {"name": "Front Door"}}},
    "commands": {
        "unlock": {
            "description": "unlock a door",
            "require": [["unlock"]],
            "names": {"entity": {"count": 1, "domains": ["lock"]}},
            "action": {"action": "lock.unlock", "target": "{{ target }}"},
        }
    },
}
OFFICE = Satellite("s", "Office", "office", "Office")


class CloudStt:
    """An STT entity's place: says what it is told to."""

    source = "stt.home_assistant_cloud"

    def __init__(self, text: str) -> None:
        self.text = text
        self.asked = 0

    def transcribe(self, audio):
        self.asked += 1
        return self.text

    def transcribe_words(self, audio):
        return self.text, None


@pytest.fixture(name="engine", scope="module")
def fixture_engine():
    if not CTC.is_dir():
        pytest.skip("no CTC model")
    engine = Engine(CTC, None, None, threads=2)
    engine.build(compile_store(STORE))
    return engine


def decide(engine, heard, stt):
    log_probs, _ = engine.model.vocab.text_log_probs(spoken_words(heard, "en")[0])
    return engine.decide(log_probs, engine.model.frame_seconds, np.zeros(16000, dtype=np.float32), OFFICE, stt)


def test_the_transcript_is_matched_when_the_audio_was_not(engine):
    # The CTC head heard "front door" too poorly; Home Assistant Cloud did not.
    decision = decide(engine, "unlock fun dough", CloudStt("Unlock front door."))
    assert decision.command.id == "unlock" and decision.matched_by == "transcript"
    assert decision.slots == {"entity": ["lock.front_door"]}
    assert decision.transcript_pass["text"] == "Unlock front door." and decision.transcript_pass["command"] == "unlock"
    # Matched in the audio: no second pass.
    decision = decide(engine, "unlock the front door", CloudStt("Unlock the front door."))
    assert decision.matched_by == "ctc" and decision.transcript_pass is None
    # Keywords and names only: a short example with words around it is no
    # example said ("grab bananas next time you shop" is not "next").
    store = {**STORE, "commands": {**STORE["commands"], "next_track": {
        "description": "skip to the next song", "examples": ["next"], "require": [["skip"]], "response": "Skipped."}}}
    engine.build(compile_store(store))
    decision = decide(engine, "blah", CloudStt("Grab bananas next time you shop."))
    assert decision.command is None and decision.transcript_pass["command"] is None
    engine.build(compile_store(STORE))
    # Nothing in either: on to the rest, the pass recorded.
    decision = decide(engine, "blah blah", CloudStt("What's the capital of France?"))
    assert decision.command is None and decision.transcript_pass["command"] is None


def test_typed_text_takes_an_example_only_typed_whole(engine):
    """The Try page: an example typed word for word wins, as one said does;
    a short one with words around it is no example ("grab bananas *next*
    time you shop")."""
    store = {**STORE, "commands": {**STORE["commands"], "next_track": {
        "description": "skip to the next song", "examples": ["next", "play the next song"], "require": [["skip"]],
        "response": "Skipped."}}}
    engine.build(compile_store(store))
    try:
        assert engine.recognize_text("next", OFFICE).command.id == "next_track"
        decision = engine.recognize_text("Play the next song.", OFFICE)
        assert decision.command.id == "next_track" and decision.intent.example == "play the next song"
        assert engine.recognize_text("grab bananas next time you shop", OFFICE).command is None
        assert engine.recognize_text("skip this one", OFFICE).command.id == "next_track"  # its keywords
    finally:
        engine.build(compile_store(STORE))
