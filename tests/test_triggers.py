"""Home Assistant's sentence triggers (voice_incubator/triggers.py): read and
grouped by automation, heard as example sentences or on the transcript,
ahead of the app's own commands, and run by Home Assistant's own agent."""

from __future__ import annotations

import asyncio
import dataclasses
import zlib
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from adapt_ctc.config import ConfigError
from adapt_ctc.text import spoken_words
from voice_incubator import actions, languages, triggers
from voice_incubator.engine import Engine, Satellite
from voice_incubator.fallback import label_of
from voice_incubator.hass import HassError
from voice_incubator.store import Store, compile_store

CTC = Path(__file__).parent.parent / "models/speech-to-phrase/stt_en_parakeet_tdt_ctc_110m-int8"
OFFICE = Satellite("s", "Office", "office", "Office", device_id="satellite-device")
KITCHEN = Satellite("k", "Kitchen", "kitchen", "Kitchen")


@pytest.fixture(autouse=True)
def english():
    languages.use("en")


def trigger(key, title, *sentences, **more):
    return triggers.Trigger(key, title, sentences, **more)


GOOD_NIGHT = trigger("automation:1", "Good night", "good night [everyone]", "(it's|it is) bed time", automation="automation.good_night", automation_id="1")
PLAY = trigger("automation:2", "Play an album", "play {album} by {artist}")


# ------------------------------------------------------------------ reading


class Hass:
    """conversation/sentences/list, get_states, automation/config and
    blueprint/substitute, as Home Assistant answers them."""

    def __init__(self, listed, automations):
        self.listed = listed
        self.automations = automations

    async def call(self, message, timeout=30.0):
        kind = message["type"]
        if kind == "conversation/sentences/list":
            return {"trigger_sentences": self.listed}
        if kind == "get_states":
            return [
                {"entity_id": e, "state": a["state"], "attributes": {"friendly_name": a["name"], **({"id": a["id"]} if a.get("id") else {})}}
                for e, a in self.automations.items()
            ] + [{"entity_id": "light.desk", "state": "on", "attributes": {}}]
        if kind == "automation/config":
            config = self.automations[message["entity_id"]].get("config")
            if config is None:
                raise HassError("not_found: Entity not found")
            return {"config": config}
        if kind == "blueprint/substitute":
            assert message["domain"] == "automation" and message["path"] == "sleep.yaml"
            return {"substituted_config": {"triggers": [{"trigger": "conversation", "command": [f"{message['input']['word']} time"]}]}}
        raise HassError(f"unknown_command: {kind}")


def test_triggers_are_grouped_by_automation():
    hass = Hass(
        # The editor open on Party and on Lights out lists theirs again; a
        # template entity's is no automation's.
        ["good night", "bed time", "party time", "party time", "lights out", "nap time", "from a template"],
        {
            "automation.good_night": {"state": "on", "name": "Good night", "id": "1", "config": {"description": "Turns off  the lights\nat bedtime", "triggers": [
                {"trigger": "conversation", "command": ["good night", "bed time"]},
                {"trigger": "state", "entity_id": "sun.sun"},
            ]}},
            "automation.party": {"state": "on", "name": "Party", "config": {"trigger": {"platform": "conversation", "command": "party time"}}},
            "automation.off": {"state": "off", "name": "Off", "config": {"triggers": [{"trigger": "conversation", "command": "lights out"}]}},
            "automation.nap": {"state": "on", "name": "Nap", "id": "3", "config": {"description": "A nap", "use_blueprint": {"path": "sleep.yaml", "input": {"word": "nap"}}}},
        },
    )
    found, automations = asyncio.run(triggers.read(hass))
    assert found == [
        trigger("automation:1", "Good night", "good night", "bed time", automation="automation.good_night", automation_id="1",
                about="Turns off the lights at bedtime"),
        trigger("automation:automation.party", "Party", "party time", automation="automation.party"),
        trigger("automation:3", "Nap", "nap time", automation="automation.nap", automation_id="3", about="A nap"),
        trigger("sentence:from a template", "from a template", "from a template"),
    ]
    assert automations == ["automation.good_night", "automation.party", "automation.off", "automation.nap"]
    assert asyncio.run(triggers.read(Hass([], hass.automations))) == ([], automations)


def test_an_automations_sentences():
    assert triggers.sentences_in({"triggers": [
        {"trigger": "conversation", "command": ["a", "b"]},
        {"trigger": "conversation", "command": "c", "enabled": False},
        {"platform": "conversation", "command": "d"},
        {"trigger": "time", "at": "07:00"},
    ]}) == ["a", "b", "d"]
    assert triggers.sentences_in({}) == []


# ---------------------------------------------------------------- preparing


def test_sentences_are_examples_or_matched_on_the_transcript():
    good_night, play = triggers.prepare([GOOD_NIGHT, PLAY])
    assert good_night.examples == ("good night everyone", "good night", "it's bed time", "it is bed time")
    assert good_night.transcript == () and good_night.description == "say “good night”"
    assert play.examples == () and play.transcript == ("play {album} by {artist}",)
    # Hints say the first sentence, each slot by its name.
    assert play.sentence == "play album by artist" and play.description == "say “play album by artist”"
    [game] = triggers.prepare([trigger("sentence:g", "Game info", "how is the {sport} game going in {the_city}")])
    assert game.description == "say “how is the sport game going in the city”"
    assert good_night.free == () and play.free == (("album", "artist"),)
    [put_on] = triggers.prepare([trigger("sentence:p", "p", "put on [the album] {album} [by {artist}]")])
    assert put_on.free == (("album", "artist"), ("album",))
    # Too many sentences for examples; one that can't be read.
    many = "(a|b|c|d|e|f|g|h) (a|b|c|d|e|f|g|h) (a|b|c|d|e|f|g|h) (one|two)"
    [odd] = triggers.prepare([trigger("sentence:x", "x", many, "(broken", "everyone (sleep;now)")])
    assert odd.transcript == (many,) and len(odd.problems) == 1 and "(broken" in odd.problems[0]
    assert odd.examples == ("everyone sleep now", "everyone now sleep")


def test_command_ids():
    assert GOOD_NIGHT.command_id == "trigger_1"
    assert trigger("automation:automation.party", "Party").command_id == "trigger_automation_party"
    assert trigger("sentence:it's bed time", "x").command_id == "trigger_it_s_bed_time"


CYCLE = trigger("automation:3", "Dishwasher", "start the {program} cycle", "run the dishwasher on {program} for {who}")
PROGRAMS = {"program": {"eco": ["eco", "economy"], "pots": ["pots and pans", "heavy"]}}


def test_a_slot_named_as_a_value_list_takes_it():
    [plain] = triggers.prepare([CYCLE])
    assert plain.examples == () and plain.lists == () and plain.free == (("program",), ("program", "who"))
    [cycle] = triggers.prepare([CYCLE], lists=PROGRAMS)
    # Heard in the audio, as each of its phrases; Home Assistant gets the value.
    assert cycle.examples == ("start the eco cycle", "start the economy cycle", "start the pots and pans cycle", "start the heavy cycle")
    assert cycle.lists == ("program",) and cycle.transcript == ("run the dishwasher on {program} for {who}",)
    assert cycle.free == (("who",),)
    assert triggers.heard_as(cycle, "start the economy cycle") == ("start the eco cycle", {"program": "eco"})
    assert triggers.heard_as(cycle, "start the eco cycle") == ("start the eco cycle", {"program": "eco"})
    assert triggers.heard_as(GOOD_NIGHT, "good night") == ("good night", {})
    # With free text too: on the transcript, one of its phrases there.
    compiled = triggers.compile_triggers([cycle], {}, (), PROGRAMS)
    found = triggers.match(compiled.heard, "Run the dishwasher on economy for the whole family.")
    assert found == [("trigger_3", {"program": "eco", "who": "the whole family"})]
    assert triggers.match(compiled.heard, "run the dishwasher on turbo for me") == []
    said = triggers.said_with(cycle, "Run the dishwasher on economy for the whole family.", found[0][1])
    assert said == "run the dishwasher on eco for the whole family"
    # Without one, what was said, as Home Assistant hears it.
    assert triggers.said_with(plain, "start the turbo cycle", {"program": "turbo"}) == "start the turbo cycle"
    # Prepared again only where a list is named.
    assert triggers.with_lists([GOOD_NIGHT, CYCLE], PROGRAMS)[0] is GOOD_NIGHT


def test_free_text_is_matched_as_home_assistant_does():
    compiled = triggers.compile_triggers(triggers.prepare([GOOD_NIGHT, PLAY]), {}, ())
    assert triggers.match(compiled.heard, "Play Abbey Road by the Beatles.") == [("trigger_2", {"album": "Abbey Road", "artist": "the Beatles"})]
    assert triggers.match(compiled.heard, "play something") == []
    assert triggers.match(None, "anything") == []


# ---------------------------------------------------------------- compiling

STORE = {
    "commands": {
        "lights_off": {"description": "turn off the lights", "require": [["turn off"], ["lights"]], "response": "Off."},
        "night": {"description": "wish good night", "examples": ["good night [all]"], "response": "Sleep well."},
        "bed": {"description": "go to bed", "examples": ["it's bed time"], "response": "Bed."},
        "bed_kitchen": {"description": "go to bed", "examples": ["it's bed time"], "area": "kitchen", "response": "Kitchen bed."},
    },
}


def test_triggers_go_first():
    compiled = compile_store(STORE, triggers.prepare([GOOD_NIGHT, PLAY]))
    assert list(compiled.commands)[:2] == ["trigger_1", "trigger_2"]
    assert compiled.commands["trigger_1"].behavior.does == "trigger" and compiled.commands["trigger_1"].title == "Good night"
    intents = compiled.config["intents"]
    # Their sentences are theirs: a template keeps the rest, written out.
    assert intents["trigger_1"]["examples"] == ["good night everyone", "good night", "it's bed time", "it is bed time"]
    assert intents["night"]["examples"] == ["good night all"]
    assert "bed" not in intents and "bed_kitchen" not in intents
    assert compiled.twins["trigger_1"] == ("night", "bed", "bed_kitchen")
    # Free text: matched on the transcript, nothing to listen for.
    assert "trigger_2" not in intents and compiled.heard_triggers is not None
    assert set(compiled.triggers) == {"trigger_1", "trigger_2"} and compiled.trigger_error is None


def test_settings_turn_them_off():
    data = {**STORE, "triggers": {"automation:1": {"off": ["kitchen"]}, "automation:2": {"disabled": True}}}
    compiled = compile_store(data, triggers.prepare([GOOD_NIGHT, PLAY]))
    assert compiled.commands["trigger_1"].off == ("kitchen",)
    assert compiled.commands["trigger_2"].disabled and compiled.heard_triggers is None
    with pytest.raises(ConfigError, match="triggers.automation:1"):
        compile_store({**STORE, "triggers": {"automation:1": {"loud": True}}}, triggers.prepare([GOOD_NIGHT]))


def test_meaning_and_the_model_may_run_them():
    data = {**STORE, "triggers": {"automation:1": {"fallback": ["meaning", "llm"]}, "automation:2": {"fallback": ["meaning", "llm"]}}}
    compiled = compile_store(data, triggers.prepare([GOOD_NIGHT, dataclasses.replace(PLAY, about="Plays an album")]))
    # Meaning for one with sentences of its own; the model for free text.
    good_night, play = compiled.commands["trigger_1"], compiled.commands["trigger_2"]
    assert good_night.fallback == ("meaning",) and play.fallback == ("llm",)
    with pytest.raises(ConfigError, match=r"triggers.automation:1.fallback"):
        compile_store({**STORE, "triggers": {"automation:1": {"fallback": ["loud"]}}}, triggers.prepare([GOOD_NIGHT]))
    # Known to them by the automation's description, else its first
    # sentence (GLiNER makes little of a name: "Game info"), else its name.
    assert label_of(play) == "Plays an album" and label_of(good_night) == "good night"
    assert dataclasses.replace(compiled.triggers["trigger_1"], sentence="").label == "Good night"
    assert good_night.combinations == ((),) and play.combinations == (("album", "artist"),)
    # What Home Assistant is sent: its sentence meaning found closest, else its first ...
    found = compiled.triggers
    assert triggers.sentence_for(good_night, found["trigger_1"], {}, "it's bed time") == "it's bed time"
    assert triggers.sentence_for(good_night, found["trigger_1"], {}, good_night.description) == "good night everyone"
    # ... or its template said with what the model heard, as Home Assistant hears it back.
    heard = {"album": "Kind of Blue", "artist": "Miles Davis"}
    assert triggers.sentence_for(play, found["trigger_2"], heard) == "play Kind of Blue by Miles Davis"
    assert triggers.sentence_for(play, found["trigger_2"], {"album": "Kind of Blue"}) is None
    # Meaning knows it by its label and its templates, free text said by name.
    assert triggers.meaning_texts(found, compiled.commands, compiled.config["intents"]) == {"trigger_2": ["Plays an album", "play album by artist"]}


def test_the_homes_value_lists_and_the_languages():
    compiled = compile_store({**STORE, "lists": PROGRAMS}, triggers.prepare([CYCLE]))
    assert compiled.config["intents"]["trigger_3"]["examples"][:2] == ["start the eco cycle", "start the economy cycle"]
    # Meaning sends one of its sentences, with the value.
    cycle = compiled.commands["trigger_3"]
    assert triggers.sentence_for(cycle, compiled.triggers["trigger_3"], {}, "start the heavy cycle") == "start the pots cycle"
    # A built-in one too, unless it is turned off.
    fan = trigger("automation:4", "Fan", "set the fan to {fan_modes}")
    compiled = compile_store(STORE, triggers.prepare([fan]))
    assert compiled.triggers["trigger_4"].lists == ("fan_modes",) and "set the fan to always on" in compiled.triggers["trigger_4"].examples
    compiled = compile_store({**STORE, "lists": {"fan_modes": False}}, triggers.prepare([fan]))
    assert compiled.triggers["trigger_4"].lists == () and compiled.triggers["trigger_4"].transcript == ("set the fan to {fan_modes}",)


def test_the_store_keeps_their_choice_for_meaning(tmp_path):
    store = Store(tmp_path)
    store.put_command("lights_off", STORE["commands"]["lights_off"])
    store.triggers = triggers.prepare([GOOD_NIGHT, PLAY])
    compiled = store.put_fallback({"meaning": ["lights_off"]}, {"meaning": ["automation:1"]})
    assert compiled.commands["trigger_1"].fallback == ("meaning",) and compiled.commands["lights_off"].fallback == ("meaning",)
    assert store.document()["triggers"] == {"automation:1": {"fallback": ["meaning"]}}
    # Exactly those: the rest are not.
    compiled = store.put_fallback({"meaning": []}, {"meaning": []})
    assert "triggers" not in store.document() and compiled.commands["trigger_1"].fallback == ()
    # The model's apart from meaning's.
    store.put_fallback({}, {"meaning": ["automation:1"]})
    compiled = store.put_fallback({}, {"llm": ["automation:2"]})
    assert store.document()["triggers"] == {"automation:1": {"fallback": ["meaning"]}, "automation:2": {"fallback": ["llm"]}}
    assert compiled.commands["trigger_2"].fallback == ("llm",)


def test_ids_clear_of_the_commands():
    compiled = compile_store({"commands": {"trigger_1": STORE["commands"]["lights_off"]}}, triggers.prepare([GOOD_NIGHT]))
    assert compiled.triggers["trigger_1_2"].key == "automation:1"


def test_a_trigger_that_cant_be_heard_leaves_the_commands_be(monkeypatch):
    def broken(*args, **kwargs):
        raise ConfigError("broken")

    found = triggers.prepare([GOOD_NIGHT])
    real = compile_store.__globals__["adapt_config"]
    monkeypatch.setitem(compile_store.__globals__, "adapt_config", lambda data, commands: broken() if "trigger_1" in commands else real(data, commands))
    compiled = compile_store(STORE, found)
    assert "trigger_1" not in compiled.commands and compiled.trigger_error == "broken"
    assert "night" in compiled.config["intents"]


def test_the_store_keeps_their_settings(tmp_path):
    store = Store(tmp_path)
    store.put_command("lights_off", STORE["commands"]["lights_off"])
    store.triggers = triggers.prepare([GOOD_NIGHT])
    assert store.put_trigger("automation:1", True).commands["trigger_1"].disabled
    store.put_off("kitchen", [], True, ["automation:1"])
    assert store.document()["triggers"] == {"automation:1": {"disabled": True, "off": ["kitchen"]}}
    store.put_trigger("automation:1", False)
    compiled = store.put_off("kitchen", [], False, ["automation:1"])
    assert "triggers" not in store.document() and not compiled.commands["trigger_1"].disabled


# ---------------------------------------------------------------- hearing


@pytest.fixture(name="engine", scope="module")
def fixture_engine():
    if not CTC.is_dir():
        pytest.skip("no CTC model")
    languages.use("en")
    engine = Engine(CTC, None, None, threads=2)
    data = {**STORE, "triggers": {"automation:1": {"off": ["office"]}}}
    engine.build(compile_store(data, triggers.prepare([GOOD_NIGHT, PLAY])))
    return engine


class Tdt:
    """The TDT head's place: hears what it is told to."""

    source = "TDT"

    def __init__(self, text):
        self.text = text

    def transcribe(self, audio):
        return self.text

    def transcribe_words(self, audio):
        return self.text, None


def decide(engine, heard, satellite, tdt):
    log_probs, _ = engine.model.vocab.text_log_probs(spoken_words(heard, "en")[0])
    return engine.decide(log_probs, engine.model.frame_seconds, np.zeros(16000, dtype=np.float32), satellite, tdt)


def test_a_trigger_beats_a_command_saying_the_same(engine):
    decision = engine.recognize_text("good night", KITCHEN)
    assert decision.command.id == "trigger_1" and decision.slots == {"sentence": "good night"}
    # Even an area's own copy: the trigger goes first.
    assert engine.recognize_text("it's bed time", KITCHEN).command.id == "trigger_1"
    # Turned off in the office: the command there.
    assert engine.recognize_text("good night", OFFICE).command.id == "night"
    assert engine.recognize_text("it's bed time", OFFICE).command.id == "bed"
    # Only the trigger says it: not heard in the office.
    decision = engine.recognize_text("good night everyone", OFFICE)
    assert decision.command is None and decision.skipped == [{"command": "trigger_1", "why": "turned off in this area"}]


def test_free_text_is_spelled_by_the_tdt_head(engine):
    decision = decide(engine, "play abby road by the beetles", OFFICE, Tdt("Play Abbey Road by The Beatles."))
    assert decision.command.id == "trigger_2" and decision.matched_by == "trigger"
    assert decision.slots == {"album": "Abbey Road", "artist": "The Beatles", "sentence": "Play Abbey Road by The Beatles."}
    # The CTC head's transcript missed it; the TDT head's, after nothing matched, didn't.
    decision = decide(engine, "lay abby road my the beetles", OFFICE, Tdt("Play Abbey Road by The Beatles."))
    assert decision.command.id == "trigger_2" and decision.slots["sentence"] == "Play Abbey Road by The Beatles."
    assert engine.recognize_text("play Abbey Road by the Beatles", OFFICE).slots["album"] == "Abbey Road"


def test_home_assistant_gets_a_value_lists_value():
    if not CTC.is_dir():
        pytest.skip("no CTC model")
    engine = Engine(CTC, None, None, threads=2)
    engine.build(compile_store({**STORE, "lists": PROGRAMS}, triggers.prepare([CYCLE])))
    # Heard in the audio, as an example ...
    decision = engine.recognize_text("start the economy cycle", KITCHEN)
    assert decision.command.id == "trigger_3" and decision.slots == {"program": "eco", "sentence": "start the eco cycle"}
    # ... or on the transcript, with free text.
    decision = decide(engine, "run the dishwasher on economy for the family", KITCHEN, Tdt("Run the dishwasher on economy for the family."))
    assert decision.command.id == "trigger_3" and decision.matched_by == "trigger"
    assert decision.slots == {"program": "eco", "who": "the family", "sentence": "run the dishwasher on eco for the family"}


def test_meaning_sends_a_sentence_of_its_own():
    """Chosen for meaning, a trigger close to what was said is sent its own
    sentence -- "kill the party" is none of its, and runs nothing there."""
    from voice_incubator.fallback import Fallback  # pylint: disable=import-outside-toplevel

    if not CTC.is_dir():
        pytest.skip("no CTC model")
    engine = Engine(CTC, None, None, threads=2)
    data = {**STORE, "triggers": {"automation:1": {"fallback": ["meaning"]}}}
    engine.build(compile_store(data, triggers.prepare([GOOD_NIGHT])))
    engine.fallback = Fallback(embedding_accept=True, model=None)
    engine.fallback.prepare(engine, wait=True)
    engine.embedder = object()  # what the meaning step needs; the stand-in below answers
    near = {"intent": "trigger_1", "text": "it's bed time", "similarity": 0.9, "next": None, "next_similarity": 0.5}
    engine.recognizer.closest_by_meaning = lambda texts, group=None: near
    decision = engine.recognize_text("time to hit the hay", KITCHEN)
    assert decision.command.id == "trigger_1" and decision.matched_by == "meaning"
    assert decision.slots == {"sentence": "it's bed time"}
    # Closest to its description: its first sentence.
    near["text"] = engine.compiled.commands["trigger_1"].description
    assert engine.recognize_text("time to hit the hay", KITCHEN).slots == {"sentence": "good night everyone"}


class Words:
    """An embedder's place: a sentence's words, as a unit vector."""

    def embed(self, texts, document=False):
        vectors = np.zeros((len(texts), 256), dtype=np.float32)
        for row, text in enumerate(texts):
            for word in str(text).lower().split():
                vectors[row, zlib.crc32(word.encode()) % 256] += 1.0
        return vectors / np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-6)


class Gliner:
    """GLiNER's place: sure of the labels it is told, and hears what it is told."""

    model = name = "stub-gliner"
    threshold = 0.3

    def __init__(self, scores, entities):
        self.scores, self.entities = scores, entities
        self.ready, self.signature, self.labels = False, None, None

    def load(self, signature=None):
        self.ready, self.signature = True, signature

    def pick(self, text, labels, entities):
        self.labels = labels
        probabilities = {label: self.scores.get(label, 0.01) for label in labels}
        probabilities[labels[-1]] = 0.05
        return {"classification": probabilities, "entities": self.entities}, 0.01

    def hear(self, text, entities):
        return {"entities": self.entities}, 0.01


def test_the_model_runs_one_with_free_text():
    """Chosen for the model, a trigger with free text is among the commands
    it picks from (by meaning: its label and its templates), known by its
    automation's description; what it hears fills a template of its, which
    Home Assistant is sent -- when it hears that back as this trigger."""
    from voice_incubator.fallback import Fallback  # pylint: disable=import-outside-toplevel

    if not CTC.is_dir():
        pytest.skip("no CTC model")
    engine = Engine(CTC, None, None, threads=2)
    engine.embedder = Words()
    data = {**STORE, "triggers": {"automation:2": {"fallback": ["llm"]}}}
    engine.build(compile_store(data, triggers.prepare([GOOD_NIGHT, dataclasses.replace(PLAY, about="Plays an album")])))
    heard = {"album": [["Kind of Blue", 0.9, 7, 19]], "artist": [["Miles Davis", 0.9, 23, 34]]}
    gliner = Gliner({"Plays an album": 0.9}, heard)
    engine.fallback = Fallback(model=gliner, top_k=8)
    engine.fallback.prepare(engine, wait=True)
    decision = engine.recognize_text("put on kind of blue by miles davis", KITCHEN)
    assert "Plays an album" in gliner.labels
    assert decision.command.id == "trigger_2" and decision.matched_by == "gliner"
    assert decision.slots == {"album": "Kind of Blue", "artist": "Miles Davis", "sentence": "play Kind of Blue by Miles Davis"}
    # Heard only in part: no template of its takes it, and nothing runs.
    gliner.entities = {"album": heard["album"]}
    decision = engine.recognize_text("put on kind of blue by miles davis", KITCHEN)
    assert decision.command is None and decision.fallback["llm"]["problems"]


# ------------------------------------------------------------------ running


class Agent:
    def __init__(self, answer):
        self.answer = answer
        self.sent = []

    async def run_trigger(self, text, device_id, language):
        self.sent.append((text, device_id, language))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


class RunHass:
    def __init__(self):
        self.events = []

    async def call(self, message, timeout=30.0):
        assert message["type"] == "frontend/get_translations"
        return {"resources": {"component.conversation.conversation.agent.done": "Done"}}

    async def fire_event(self, event_type, data):
        self.events.append(data)

    async def render_template(self, template, variables=None, timeout=10.0):
        return template


def said(answer, speech_type="action_done"):
    return {"response": {"response_type": speech_type, "speech": {"plain": {"speech": answer}}, "data": {}}, "conversation_id": "c"}


def run(agent, dry_run=False):
    compiled = compile_store(STORE, triggers.prepare([GOOD_NIGHT]))
    command = compiled.commands["trigger_1"]
    decision = SimpleNamespace(
        command=command, behavior=command.behavior, slots={"sentence": "good night"}, transcript="good nite",
    )
    return asyncio.run(actions.run(RunHass(), decision, OFFICE, None, dry_run, agent=agent))


def test_home_assistant_runs_it_and_answers():
    agent = Agent(said("Sleep tight."))
    outcome = run(agent)
    assert agent.sent == [("good night", "satellite-device", "en")]
    assert outcome.name == "done" and outcome.speech == "Sleep tight." and outcome.to_json()["trigger"] == "good night"
    # No response of its own: the acknowledgement sound.
    outcome = run(Agent(said("Done")))
    assert outcome.name == "done" and outcome.speech == ""
    # Not sent on a dry run.
    agent = Agent(said("Sleep tight."))
    assert (run(agent, dry_run=True)).name == "done" and agent.sent == []


def test_when_home_assistant_cant():
    outcome = run(Agent(said("Sorry, I couldn't understand that", "error")))
    assert outcome.name == "failed" and outcome.speech == "Sorry, I couldn't understand that"
    outcome = run(Agent(HassError("timeout")))
    assert outcome.name == "failed" and outcome.error == "timeout"
