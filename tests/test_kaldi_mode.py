"""Kaldi mode (voice_incubator/kaldi_mode.py): what it hears, its grammar, its
pronunciations, and the decisions the engine makes of what it heard."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from voice_incubator import kaldi_mode
from voice_incubator.engine import Engine, Satellite
from voice_incubator.kaldi_mode import CALL, COMMAND, Heard, KaldiMode, Sentences, words_of
from voice_incubator.store import compile_store

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
    return kaldi_mode.sentences(recognizer.config, lambda intent, text: engine._free_text(recognizer, intent, text),
                                engine.compiled.commands.values())


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
    # A call's: its wake phrase first, the twins of its words.
    lines = kaldi_mode._grammar([("turn", "on")], ([], []), [("okay", "nabu")])
    assert any(line.startswith("0 ") and " __wake_okay __wake_okay " in line for line in lines)
    assert not any(line.startswith("0 1 #0") for line in lines)  # not without it


def test_pronunciations_are_checked():
    phones = {"h", "ˈɛ", "l", "oʊ"}
    assert kaldi_mode.validate(" h  ˈɛ l oʊ ", phones) == "h ˈɛ l oʊ"
    with pytest.raises(ValueError, match="x"):
        kaldi_mode.validate("h x", phones)
    with pytest.raises(ValueError):
        kaldi_mode.validate("  ", phones)


def _heard(text: str, margin: float, background: list[str] | None = None, answer: str | None = None) -> Heard:
    def said(words: str) -> list:
        return [(w, 0.3 * at, 0.3 * at + 0.3) for at, w in enumerate(words.split())]

    # The wake phrase's words and the extras' are their twins: not said.
    call = said(f"__wake_okay __wake_nabu {text} __please")
    background = ["something"] if background is None else background
    return Heard({
        "home": (margin * 100, 100, said(text)), "answer": (margin * 100, 100, said(answer if answer is not None else text)),
        "call": (margin * 100, 100, call), "background": (0.0, 100, said(" ".join(background))),
        "call_background": (0.0, 100, said(" ".join(background))),
    }, 0.01)


class FakeKaldi:
    """Says what it is told to."""

    ready = True

    def __init__(self, heard: Heard) -> None:
        self.heard = heard

    def decode(self, audio, graphs):
        # Only the graphs asked for, as the decoder answers: reading another
        # is a KeyError.
        return Heard({tag: self.heard.results[tag] for tag in graphs}, self.heard.seconds, self.heard.max_cost)


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
        # A call: decoded whole, after its wake phrase.
        decision, split = engine.recognize_call(np.zeros(48000, dtype=np.float32), None, 1.5, OFFICE)
        assert decision.command.id == "lights_on" and split.cut == 1.5
        assert decision.sentence["graph"] == "call" and decision.transcript == "turn on the lights"
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


def test_kaldi_hears_a_recorded_call(tmp_path, monkeypatch):
    # A data folder given relative to where the app started (--data-dir
    # var/test-home): Kaldi's scripts run from inside the graphs' folders.
    monkeypatch.chdir(tmp_path)
    mode = KaldiMode(ROOT, Path("var/kaldi"))
    if mode.available:
        pytest.skip(mode.available)
    import wave  # pylint: disable=import-outside-toplevel

    heard = Sentences({"lights": [*words_of("turn on the lights", "en"), *words_of("turn off the lights", "en")]})
    wake = ([("okay", "nabu")], {"nabu": ["n ˈæ b u"]})
    built = mode.build(heard, "en", {}, ["yes", "no"], *wake)
    try:
        assert built.sentences == 2 and not built.unspeakable
        with wave.open(str(WAV)) as recording:
            audio = np.frombuffer(recording.readframes(recording.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
        # The call whole, after its wake phrase -- and, without it, after noise.
        result = mode.decode(audio, CALL)
        assert set(result.results) == set(CALL)
        assert result.text("call") == "turn on the lights" and result.taken("call"), result.to_json("call")
        # As it is said: fed in the pieces a satellite sends, the same (but
        # for the dither each decode draws anew).
        pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
        stream = mode.stream(CALL)
        for at in range(0, len(pcm), 1023):
            stream.feed(pcm[at : at + 1023])
        assert stream.samples == len(audio)
        streamed = stream.finish()
        assert streamed.streamed and streamed.to_json("call")["streamed"]
        assert streamed.text("call") == "turn on the lights" and abs(streamed.margin("call") - result.margin("call")) < 0.05
        result = mode.decode(audio, COMMAND)
        assert set(result.results) == set(COMMAND)
        assert result.text("home") == "turn on the lights"
        assert result.background_words()
        # The same again: nothing built anew.
        assert mode.build(heard, "en", {}, ["yes", "no"], *wake) is built
    finally:
        mode.close()


def test_a_call_being_said_finishes_on_the_graphs_it_started_on(tmp_path):
    import gc  # pylint: disable=import-outside-toplevel
    import wave  # pylint: disable=import-outside-toplevel

    mode = KaldiMode(ROOT, tmp_path / "kaldi")
    if mode.available:
        pytest.skip(mode.available)
    wake = ([("okay", "nabu")], {"nabu": ["n ˈæ b u"]})
    lights = Sentences({"lights": [*words_of("turn on the lights", "en"), *words_of("turn off the lights", "en")]})
    mode.build(lights, "en", {}, ["yes", "no"], *wake)
    try:
        with wave.open(str(WAV)) as recording:
            pcm = recording.readframes(recording.getnframes())
        half = len(pcm) // 2 & ~1
        said, forgotten = mode.stream(CALL), mode.stream(CALL)
        said.feed(pcm[:half])
        forgotten.feed(pcm[:half])
        old = mode._decoder  # pylint: disable=protected-access
        # A save meanwhile: the graphs are built again, without the lights.
        mode.build(Sentences({"fan": words_of("turn on the fan", "en")}), "en", {}, ["yes", "no"], *wake)
        assert mode._decoder is not old and old.running  # pylint: disable=protected-access
        said.feed(pcm[half:])
        assert said.finish().text("call") == "turn on the lights"
        assert old.running  # the other call isn't over yet
        del forgotten
        gc.collect()
        assert not old.running
        # New calls are heard by the new graphs.
        stream = mode.stream(CALL)
        stream.feed(pcm)
        assert stream.finish().text("call") != "turn on the lights"
    finally:
        mode.close()


class FakeStream:
    def __init__(self, heard: Heard, samples: int) -> None:
        self.heard, self.samples, self.done, self.cancelled = heard, samples, False, False

    def finish(self):
        self.done = True
        return Heard(self.heard.results, 0.001, self.heard.max_cost, streamed=True)

    def cancel(self):
        self.done = self.cancelled = True


def test_a_call_decoded_as_it_was_said_is_heard_from_its_stream(engine):
    _kaldi(engine, _heard("turn on the lights", -0.2))
    try:
        audio = np.zeros(48000, dtype=np.float32)
        stream = FakeStream(_heard("turn on the lights", -0.2), len(audio))
        decision, _ = engine.recognize_call(audio, None, 1.5, OFFICE, kaldi_stream=stream)
        assert decision.command.id == "lights_on" and decision.sentence["streamed"] and not stream.cancelled
        # Someone else's speech cut the call short: what was fed is too much,
        # so the call is decoded whole.
        stream = FakeStream(_heard("turn off the lights", -0.2), len(audio) + 1600)
        decision, _ = engine.recognize_call(audio, None, 1.5, OFFICE, kaldi_stream=stream)
        assert decision.transcript == "turn on the lights" and "streamed" not in decision.sentence and stream.cancelled
        # Parakeet hears when Kaldi isn't chosen: the stream is let go.
        engine.recognizer_choice = "parakeet"
        stream = FakeStream(_heard("turn on the lights", -0.2), len(audio))
        engine.recognize_call(audio, None, 1.5, OFFICE, kaldi_stream=stream)
        assert stream.cancelled
    finally:
        engine.recognizer_choice = "parakeet"


class BrokenKaldi(FakeKaldi):
    def decode(self, audio, graphs):
        raise RuntimeError("the Kaldi decoder stopped")


def test_parakeet_hears_when_kaldi_fails(engine):
    engine.kaldi = BrokenKaldi(_heard("", 0.0))  # type: ignore[assignment]
    engine.recognizer_choice = "kaldi"
    try:
        decision = engine.recognize_command(np.zeros(16000, dtype=np.float32), OFFICE)
        assert decision.text_source != "kaldi" and decision.matched_by != "kaldi"
    finally:
        engine.recognizer_choice = "parakeet"


# ------------------------------------------------------- sentence templates

TEMPLATED = {
    "names": {
        "entities": {"light.desk": {"name": "Desk Lamp"}, "lock.front_door": {"name": "Front Door"}},
        "areas": {"kitchen": {"name": "Kitchen"}, "office": {"name": "Office"}},
    },
    "commands": {
        "light_on": {
            "description": "turn on a light", "require": [["turn on", "turn ... on"]], "optional": [["the"], ["in"], ["light", "lights"]],
            "names": {"entity": {"count": 1, "domains": ["light"]}, "area": {"count": 1}},
            "combinations": [["entity"], ["area"]],
            "sentences": ["turn on [the] {entity}", "turn on the lights in [the] {area}"],
            "action": {"action": "light.turn_on"},
        },
        "brightness": {
            "description": "set a light's brightness", "require": [["brightness"]], "optional": [["set"], ["the"], ["to"], ["percent"]],
            "names": {"entity": {"count": 1, "domains": ["light"]}}, "number": "percentage",
            "sentences": ["set [the] {entity} brightness to {percentage} percent"],
            "action": {"action": "light.turn_on"},
        },
    },
}


def test_templates_are_kaldi_s_alone():
    """Parakeet reads nothing of them: the recognizer's config is the same
    with them as without."""
    without = {**TEMPLATED, "commands": {k: {kk: vv for kk, vv in v.items() if kk != "sentences"} for k, v in TEMPLATED["commands"].items()}}
    assert compile_store(TEMPLATED).config == compile_store(without).config


def test_template_slots_are_their_names_and_numbers():
    engine = _engine(TEMPLATED)
    heard = engine.kaldi_sentences = _sentences(engine)
    said = {" ".join(s) for s in heard.all}
    on_entity = next(s for s in heard.by_intent["light_on"])
    token = next(w for w in on_entity if w.startswith(kaldi_mode.SLOT_TOKEN))
    # The light's name only: a lock isn't a light.
    assert heard.slots[token] == [("desk", "lamp")]
    assert any(w.startswith(kaldi_mode.SLOT_TOKEN) for s in heard.by_intent["light_on:area"] for w in s)
    numbers = next(heard.slots[w] for s in heard.by_intent["brightness"] for w in s if w.startswith(kaldi_mode.SLOT_TOKEN) and len(heard.slots[w]) > 50)
    assert ("fifty",) in numbers and ("one", "hundred") in numbers
    assert not any("{" in s for s in said)
    # Each reads back as its own command.
    engine._misread(engine.recognizer, heard)
    assert heard.misread == {}


def test_a_template_read_as_another_command_is_said():
    store = {**TEMPLATED, "commands": {**TEMPLATED["commands"], "brightness": {
        **TEMPLATED["commands"]["brightness"], "sentences": ["turn on [the] {entity} to {percentage} percent"]}}}
    engine = _engine(store)
    heard = _sentences(engine)
    engine._misread(engine.recognizer, heard)
    # Without "brightness" it isn't Set the brightness: Kaldi would hear it, and it wouldn't run.
    [misread] = heard.misread["brightness"]
    assert misread["sentence"] == "turn on the desk lamp to fifty percent" and misread["as"] != "brightness"


def test_a_template_s_slots_must_be_a_way_to_say_it():
    from adapt_ctc.config import ConfigError  # pylint: disable=import-outside-toplevel

    for bad, message in ((["turn on {floor}"], "not a slot"), (["turn on {entity} in {area}"], "isn't a way to say it"),
                         (["turn on (the {entity}"], "never closed")):
        store = {**TEMPLATED, "commands": {"light_on": {**TEMPLATED["commands"]["light_on"], "sentences": bad}}}
        with pytest.raises(ConfigError, match=message):
            compile_store(store)


def test_a_slot_is_a_tree_where_it_is_said():
    lines = kaldi_mode._grammar([("turn", "on", "zzslota"), ("turn", "on", "zzslota", "please")], ([], []),
                                slots={"zzslota": [("kitchen", "light"), ("lamp",)]})
    words = [line.split()[2] for line in lines if len(line.split()) > 3]
    assert words.count("kitchen") == 1 and words.count("lamp") == 1 and "zzslota" not in words


def test_a_followed_command_keeps_its_own_sentences(monkeypatch):
    from voice_incubator import library  # pylint: disable=import-outside-toplevel

    monkeypatch.setattr(library, "base", lambda reference, *_: {"description": "d", "require": [["a"]], "response": "ok",
                                                                 "sentences": ["the library's"]})
    out = library.resolve("x", {"from": "f/x", "sentences": ["mine", "the library's"]})
    assert out["sentences"] == ["the library's", "mine"]
    assert library.settings({"from": "f/x", "sentences": ["the library's", "mine"]}, {"sentences": ["the library's"]}) == {
        "from": "f/x", "sentences": ["mine"]}


def test_every_library_template_reads_back_as_its_command():
    """The English library's sentences (languages/en/sentences.yaml), each
    with names of every kind and a value for every free text: what Kaldi
    would hear is read as the command it is a sentence of."""
    from voice_incubator import languages  # pylint: disable=import-outside-toplevel
    from voice_incubator.library import _library  # pylint: disable=import-outside-toplevel

    languages.use("en")
    entries = _library("en")
    assert not [e["id"] for e in entries if e.get("error")]
    entities = {"light.desk_lamp": "Desk Lamp", "switch.kettle": "Kettle", "fan.ceiling_fan": "Ceiling Fan",
                "media_player.living_room_speaker": "Living Room Speaker", "climate.ecobee": "Ecobee",
                "cover.garage_door": "Garage Door", "lock.front_door": "Front Door", "binary_sensor.back_door": "Back Door",
                "sensor.outside_temperature": "Outside Temperature", "scene.movie_night": "Movie Night",
                "script.good_night": "Good Night"}
    store = {
        "names": {"entities": {k: {"name": v} for k, v in entities.items()},
                  "areas": {"kitchen": {"name": "Kitchen"}, "office": {"name": "Office"}},
                  "floors": {"upstairs": {"name": "Upstairs"}}},
        "lists": {slot: {value: [value]} for slot, value in (("item", "milk"), ("query", "radiohead"), ("message", "dinner is ready"),
                                                             ("reminder", "to call mom"), ("timer", "pizza"), ("time", "seven"))},
        "commands": {e["id"]: {"from": f"{e['file']}/{e['id']}"} for e in entries},
    }
    engine = _engine(store)
    heard = _sentences(engine)
    engine._misread(engine.recognizer, heard)
    assert sum(len(t) for t in heard.templates.values()) > 200
    assert heard.misread == {}


def test_kaldi_is_built_after_a_save_not_during_it():
    """A save returns at once; Kaldi's graphs are built after, in the
    background, once more for every change made meanwhile -- not once each."""
    import asyncio  # pylint: disable=import-outside-toplevel
    import threading  # pylint: disable=import-outside-toplevel
    import time  # pylint: disable=import-outside-toplevel
    from types import SimpleNamespace  # pylint: disable=import-outside-toplevel

    from voice_incubator.server import VoiceIncubatorApp  # pylint: disable=import-outside-toplevel

    builds = []
    started = threading.Event()

    def build_kaldi():
        builds.append(time.monotonic())
        started.set()
        time.sleep(0.2)

    async def run():
        app = SimpleNamespace(engine=SimpleNamespace(kaldi=object(), recognizer_choice="kaldi", build_kaldi=build_kaldi),
                              _kaldi_dirty=False, _kaldi_task=None, kaldi_building=False)
        app._spawn = asyncio.ensure_future
        app._kaldi_builds = lambda: VoiceIncubatorApp._kaldi_builds(app)
        VoiceIncubatorApp.kaldi_outdated(app)
        await asyncio.to_thread(started.wait, 2)
        assert app.kaldi_building
        for _ in range(3):  # three saves while it builds
            VoiceIncubatorApp.kaldi_outdated(app)
        await app._kaldi_task
        assert len(builds) == 2 and not app.kaldi_building
        app.engine.recognizer_choice = "parakeet"
        VoiceIncubatorApp.kaldi_outdated(app)
        assert app._kaldi_task.done()

    asyncio.run(run())


def test_home_assistants_audio_is_fed_to_kaldi_as_it_comes():
    from voice_incubator.wyoming_server import Hearing  # pylint: disable=import-outside-toplevel

    class Fed:
        def __init__(self):
            self.pcm, self.cancelled = b"", False

        def feed(self, pcm):
            self.pcm += pcm

        def cancel(self):
            self.cancelled = True

    hearing = Hearing({}, None, None, kaldi=Fed())
    hearing.feed(b"\x01\x00\x02\x00")
    hearing.feed(b"\x03\x00")
    assert hearing.kaldi.pcm == bytes(hearing.pcm) == b"\x01\x00\x02\x00\x03\x00"
    hearing.cancel()  # the client left
    assert hearing.kaldi.cancelled



def test_kaldi_hears_which_by_the_homes_own_slots():
    from voice_incubator.kaldi_mode import answer_sentences  # pylint: disable=import-outside-toplevel

    heard = Sentences(slots={"zzslota": [("five", "minutes")], "zzslotb": [("pizza",)]},
                      slot_names={"zzslota": "duration", "zzslotb": "timer"})
    said = answer_sentences(["[the] {duration} [timer|one]", "[the] {time} alarm", "(both|all) [of them]"], heard)
    assert "the zzslota one" in said and "zzslota" in said and "both of them" in said
    assert not any("time" in s.split() for s in said)  # no command hears a time here
