"""Nothing matched in the audio: the keyword matcher on the TDT or STT
transcript, before skills and the fallback (the real CTC model, with
evidence made from text)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from adapt_ctc.text import spoken_words
from voice_labs.engine import Engine, Satellite, trimmed, within
from voice_labs.store import compile_store

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


def test_a_doubtful_match_is_heard_again(engine):
    """A match that costs as much as a word it left unexplained runs only
    when the transcript agrees; the transcript's own command runs instead,
    and with none, nothing does."""
    store = {"commands": {
        "resume_music": {"description": "resume the music", "require": [["resume"]], "optional": [["the"], ["music"]], "response": "Music."},
        "resume_timer": {"description": "resume a timer", "require": [["resume"], ["timer"]], "optional": [["the"]],
                         "lists": {"timer_name": {"values": {"chicken": "chicken"}}}, "combinations": [["timer_name"]],
                         "response": "Timer."},
        "cancel_timer": {"description": "cancel a timer", "require": [["cancel"], ["timer"]], "response": "Cancelled."},
    }}
    engine.build(compile_store(store))
    try:
        # "timer" misheard: resuming the music, "chicken" left over.
        stt = CloudStt("Resume the chicken timer.")
        decision = decide(engine, "resume the chicken pder", stt)
        assert decision.command.id == "resume_timer" and decision.slots == {"timer_name": "chicken"}
        assert decision.doubt["command"] == "resume_music" and decision.doubt["outcome"] == "replaced"
        assert decision.doubt["why"] == "cost"
        assert decision.matched_by == "transcript" and stt.asked == 1
        # The transcript agrees, surely: it runs.
        decision = decide(engine, "resume the chicken pder", CloudStt("Resume the music."))
        assert decision.command.id == "resume_music" and decision.doubt["outcome"] == "agrees"
        # As unsure in the transcript: that says nothing, and nothing runs.
        decision = decide(engine, "resume the chicken pder", CloudStt("Resume the chicken pder."))
        assert decision.command is None and decision.doubt["outcome"] == "unconfirmed"
        assert decision.doubt["transcript_cost"] >= 4
        # The transcript says nothing, or nothing it can run: nothing runs.
        for heard in ("", "Bake the chicken."):
            decision = decide(engine, "resume the chicken pder", CloudStt(heard))
            assert decision.command is None and decision.doubt["outcome"] == "unconfirmed", heard
        # A match that leaves nothing out isn't asked about.
        stt = CloudStt("Something else entirely.")
        decision = decide(engine, "resume the music", stt)
        assert decision.command.id == "resume_music" and decision.doubt is None and stt.asked == 0
        # Typed text is its own transcript.
        decision = engine.recognize_text("resume the chicken pder", OFFICE)
        assert decision.command.id == "resume_music" and decision.doubt is None
    finally:
        engine.build(compile_store(STORE))


def test_a_question_a_command_half_explains_is_a_skills(engine):
    """"What day is Christmas" is the date's "what day", "is Christmas" left
    over, in the audio and the transcript alike: not the date, but the dates
    skill's question."""
    store = {"commands": {"what_date": {
        "description": "tell the date", "examples": ["what day is it [today]"], "require": [["date", "what day"]],
        "optional": [["what is", "what's"], ["today"]], "response": "Today."},
        # "is" is another command's keyword: left over, it is doubtful.
        "is_open": {"description": "say if a door is open", "require": [["is"], ["open"]], "response": "No."}}}
    engine.build(compile_store(store))
    try:
        decision = decide(engine, "what day is christmas", CloudStt("What day is Christmas?"))
        assert decision.command is None and decision.doubt["outcome"] == "unconfirmed"
        assert decision.answer and decision.answer["skill"] == "dates"
        assert engine.recognize_text("what day is it today", OFFICE).command.id == "what_date"
    finally:
        engine.build(compile_store(STORE))


def test_a_name_in_the_transcript_beats_none_in_the_audio(engine):
    """A match that named nothing, with a word left over, gives way to a
    version of it the transcript names a device for."""
    color = {"description": "change the color of the lights", "optional": [["to"], ["the"]],
             "lists": {"color": {"values": {"green": "green", "red": "red"}}}, "response": "Done."}
    store = {"names": {"entities": {"light.bed": {"name": "Bed Light"}}}, "commands": {
        "color_here": {**color, "require": [["set"], ["light", "lights"]]},
        "color_device": {**color, "require": [["set"]], "names": {"entity": {"count": 1}}},
    }}
    engine.build(compile_store(store))
    try:
        # "bed" heard as "and": here, unless the transcript names the light.
        decision = decide(engine, "set and light to green", CloudStt("Set bed light to green."))
        assert decision.command.id == "color_device" and decision.slots == {"entity": ["light.bed"], "color": "green"}
        assert decision.doubt["why"] == "unnamed" and decision.doubt["outcome"] == "named"
        decision = decide(engine, "set and light to green", CloudStt("Set the light to green."))
        assert decision.command.id == "color_here" and decision.doubt["outcome"] == "kept"
        # Every word explained: no name was missed, and it isn't asked.
        stt = CloudStt("Set bed light to green.")
        decision = decide(engine, "set the light to green", stt)
        assert decision.command.id == "color_here" and decision.doubt is None and stt.asked == 0
    finally:
        engine.build(compile_store(STORE))


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


def test_a_copy_on_a_satellite_says_the_same_sentences(engine):
    # Duplicated onto the kids' satellite: the same example sentences and
    # keywords, something else done there -- where its own goes first, though
    # it takes the Home's too.
    lights = {
        "description": "turn on the lights", "examples": ["lights please"], "require": [["lights"], ["on"]],
        "response": "Lights.",
    }
    store = {
        "commands": {
            "lights": lights,
            "kids_lights": {**lights, "satellites": ["k"], "response": "Night light."},
            "kids_time": {"description": "tell the time", "examples": ["what time is it"], "satellites": ["k"], "response": "Bedtime."},
            "time": {"description": "tell the time", "examples": ["what time is it"], "response": "Noon."},
        },
    }
    compiled = compile_store(store)
    assert compiled.twins == {"lights": ("kids_lights",), "kids_time": ("time",)}
    # Only examples, all shared: only a twin.
    assert "time" not in compiled.config["intents"] and "kids_lights" in compiled.config["intents"]
    engine.build(compiled)
    home = Satellite("h", "Kitchen", "kitchen")
    kids = Satellite("k", "Kids", "kids")
    try:
        for text, satellite, command in [
            ("lights please", home, "lights"), ("lights please", kids, "kids_lights"),
            ("turn the lights on", home, "lights"), ("turn the lights on", kids, "kids_lights"),
            ("what time is it", home, "time"), ("what time is it", kids, "kids_time"),
        ]:
            decision = engine.recognize_text(text, satellite)
            assert decision.command is not None and decision.command.id == command, (text, satellite.name)
            assert decision.behavior is decision.command.behavior
    finally:
        engine.build(compile_store(STORE))


def test_a_copy_says_the_same_templates(engine):
    # A template is every sentence it stands for: a copy's, all the first's,
    # makes it a twin.
    lights = {"description": "turn on the lights", "examples": ["(turn|switch) on the light[s]"], "response": "Lights."}
    store = {"commands": {"lights": lights, "kids_lights": {**lights, "satellites": ["k"], "response": "Night light."}}}
    compiled = compile_store(store)
    assert compiled.twins == {"lights": ("kids_lights",)} and "kids_lights" not in compiled.config["intents"]
    engine.build(compiled)
    kids = Satellite("k", "Kids", "kids")
    try:
        decision = engine.recognize_text("switch on the light", kids)
        assert decision.command.id == "kids_lights"
        assert engine.recognize_text("switch on the light", Satellite("h", "Kitchen", "kitchen")).command.id == "lights"
    finally:
        engine.build(compile_store(STORE))


def test_a_room_it_cant_take_is_why(engine):
    # Heard whole but for the room: the hint says so, though most of the
    # sentence was free text.
    store = {
        "names": {"areas": {"office": {"name": "Office"}}},
        "commands": {
            "play_music": {
                "description": "play music", "require": [["play"]], "response": "Playing.",
                "wildcard": {"slot": "query", "after": ["play"], "to_end": True, "max_words": 8},
            },
        },
    }
    engine.build(compile_store(store))
    try:
        decision = engine.recognize_text("play jazz in the office", OFFICE)
        assert decision.command is None
        assert decision.message == "It looks like you're trying to play music, but I can't pick a room for it."
        decision = engine.recognize_text("play songs about life on the moon", OFFICE)
        assert decision.command.id == "play_music" and decision.slots == {"query": "songs about life on the moon"}
        # Little of it heard, and not for a room: no hint.
        decision = engine.recognize_text("what a lovely day for a walk in the park", OFFICE)
        assert decision.command is None and decision.message == "Sorry, I didn't understand that."
    finally:
        engine.build(compile_store(STORE))


def test_free_text_keeps_off_its_edges(engine):
    """Free text from timed words leaves out, at either end, what the matcher
    read as no text: the intent's keywords and the wildcard's, fillers, name
    cues, and what says a place follows -- "pizza for", "jazz in", "um halb
    sieben" with "um" a frame late."""
    edges = engine._edge_words("unlock", {"after": ["named"], "before": ["timer"]})  # pylint: disable=protected-access
    assert {"unlock", "named", "timer", "the", "my", "in", "on"} <= edges and "front" not in edges
    assert trimmed(["the", "pizza", "for"], {"the", "for"}) == ["pizza"]
    assert trimmed(["eggs", "and", "bacon", "to"], {"and", "to"}) == ["eggs", "and", "bacon"]
    assert trimmed(["for"], {"for"}) == ["for"]  # never nothing


def test_a_last_word_running_into_silence_is_still_the_texts():
    """The transducer gives the last word an end at the end of the audio:
    its middle is taken half a second in, so "demie" stays in the time."""
    words = [("à", 0.88, 0.96), ("sept", 0.96, 1.04), ("heures", 1.2, 1.28), ("et", 1.44, 1.52), ("demie.", 1.6, 2.72)]
    assert within(words, 0.95, 2.0) == ["sept", "heures", "et", "demie"]
    assert within(words, 0.95, 1.5) == ["sept", "heures", "et"]
