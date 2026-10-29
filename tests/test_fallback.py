"""The fallback: what commands may hear and back into slots, the checks on
it, and the chain -- with a stand-in for GLiNER."""

from __future__ import annotations

import pytest

from voice_labs.engine import Satellite, model_note
from voice_labs.fallback import (
    Arguments,
    Fallback,
    by_meaning,
    chosen,
    parse_duration,
)
from voice_labs.store import compile_store

STORE = {
    "names": {
        "areas": {"kitchen": {"name": "Kitchen"}, "living_room": {"name": "Living Room", "aliases": ["Lounge"]}},
        "floors": {"upstairs": {"name": "Upstairs"}},
        "entities": {"switch.coffee_maker": {"name": "Coffee Maker"}, "cover.garage_door": {"name": "Garage Door"}},
    },
    "lists": {"colors": {"red": ["red", "crimson"], "blue": "blue"}},
    "commands": {
        "lights_on": {
            "description": "turn on the lights",
            "require": [["turn on"], ["lights"]],
            "names": {"area": {"count": 1}, "floor": {"count": 1}},
            "combinations": [[], ["area"], ["floor"]],
            "action": {"action": "light.turn_on", "target": "{{ target }}"},
        },
        "device_on": {
            "description": "turn on a device",
            "require": [["turn on"]],
            "names": {"entity": {"count": 1, "domains": ["switch"]}},
            "action": {"action": "homeassistant.turn_on", "target": "{{ target }}"},
        },
        "light_color": {
            "description": "change the color of the lights",
            "require": [["lights"]],
            "lists": {"color": "colors"},
            "action": {"action": "light.turn_on", "target": "{{ target }}"},
        },
        "start_timer": {
            "description": "set a timer",
            "require": [["timer"], ["minutes", "minute"]],
            "number": "duration",
            "timer": "start",
        },
        "set_brightness": {
            "description": "set the brightness of the lights",
            "require": [["brightness", "percent"]],
            "number": "percentage",
            "action": {"action": "light.turn_on", "target": "{{ target }}"},
        },
        "add_item": {
            "description": "add something to the shopping list",
            "require": [["add"], ["list"]],
            "optional": [["to"]],
            "wildcard": {"slot": "item", "after": ["add"], "before": ["to"], "bounded": True},
            "action": {"action": "todo.add_item", "target": {"entity_id": "todo.shopping_list"}},
        },
        "what_time": {"description": "tell the time", "examples": ["what time is it"], "response": "Noon."},
        "set_alarm": {
            "description": "set an alarm",
            "require": [["alarm"]],
            "optional": [["for", "at"]],
            "wildcard": {"slot": "time", "after": ["for", "at"], "to_end": True},
            "timer": "alarm",
        },
    },
}
KITCHEN = Satellite("sat", "Kitchen", "kitchen", "Kitchen")


@pytest.fixture(name="compiled")
def fixture_compiled():
    return compile_store(STORE)


def chosen_for(store, meaning=(), llm=()):
    """The store with these commands chosen for the fallback steps."""
    commands = {}
    for command_id, spec in store["commands"].items():
        steps = [s for s, ids in (("meaning", meaning), ("llm", llm)) if command_id in ids]
        commands[command_id] = {**spec, **({"fallback": steps} if steps else {})}
    return compile_store({**store, "commands": commands})


def test_what_commands_hear(compiled):
    arguments = Arguments(compiled)
    assert set(arguments.parameters) == set(STORE["commands"])
    kinds = {c: {name: p.kind for name, p in ps.items()} for c, ps in arguments.parameters.items()}
    assert kinds["lights_on"] == {"area": "name", "floor": "name"} and arguments.required["lights_on"] == []
    assert arguments.parameters["lights_on"]["area"].choices == {"kitchen": "kitchen", "living room": "living_room", "lounge": "living_room"}
    assert arguments.parameters["device_on"]["entity"].choices == {"coffee maker": "switch.coffee_maker"}  # switches only
    assert set(arguments.parameters["light_color"]["color"].choices.values()) == {"red", "blue"}
    assert kinds["start_timer"] == {"duration": "duration"} and kinds["set_brightness"] == {"percentage": "number"}
    assert kinds["add_item"] == {"item": "text"} and arguments.required["add_item"] == ["item"]
    assert kinds["what_time"] == {}


def test_arguments_become_slots(compiled):
    tools = Arguments(compiled)
    commands = compiled.commands
    assert tools.slots(commands["lights_on"], {"area": "lounge"}, "lights on in the lounge") == ({"area": ["living_room"]}, [])
    # A name the sentence does not say is dropped: a small model makes them up.
    assert tools.slots(commands["lights_on"], {"area": "Kitchen"}, "kill the lights") == ({}, [])
    # Not a name it may have: dropped when some combination goes without it, a
    # problem when every one needs it.
    assert tools.slots(commands["lights_on"], {"area": "Garage"}, "garage lights") == ({}, [])
    # Slots that are no combination of it.
    assert tools.slots(commands["lights_on"], {"area": "Kitchen", "floor": "Upstairs"}, "kitchen lights upstairs")[1] == [
        "area and floor don't go together"
    ]
    slots, problems = tools.slots(commands["device_on"], {"entity": "Garage Door"}, "turn on the garage door")
    assert problems and "no entity" in problems[0]
    assert tools.slots(commands["light_color"], {"color": "crimson"}, "make it crimson")[0] == {"color": "red"}
    slots, problems = tools.slots(commands["start_timer"], {"duration": "an hour and a half"}, "an hour and a half")
    assert slots["duration"]["total_seconds"] == 5400 and not problems
    assert tools.slots(commands["set_brightness"], {"percentage": "30%"}, "thirty percent")[0] == {"percentage": 30}
    assert tools.slots(commands["add_item"], {"item": "oat milk"}, "we need oat milk")[0] == {"item": "oat milk"}
    slots, problems = tools.slots(commands["add_item"], {"item": "bananas"}, "we need milk")
    assert problems == ["no item"]  # made up, and required
    # A number or duration the sentence does not say is made up.
    assert tools.slots(commands["set_brightness"], {"percentage": 80}, "make it brighter") == ({}, ["no percentage"])
    timer, problems = tools.slots(commands["start_timer"], {"duration": "5 minutes"}, "start the coffee maker")
    assert problems == ["no duration"]
    timer, problems = tools.slots(commands["start_timer"], {"duration": "30 minutes"}, "give me half an hour")
    assert timer["duration"]["total_seconds"] == 1800 and not problems
    # A clock time is written, not said; its am or pm only when the sentence says one.
    morning = "set an alarm for quarter past six in the morning"
    assert tools.slots(commands["set_alarm"], {"time": "6:15 am"}, morning) == ({"time": "6:15 am"}, [])
    assert tools.slots(commands["set_alarm"], {"time": "6:00 am"}, "wake me up at six") == ({"time": "6:00"}, [])


def test_durations():
    assert parse_duration("10 minutes") == 600
    assert parse_duration("1 hour and 30 minutes") == 5400
    assert parse_duration("an hour and a half") == 5400
    assert parse_duration("five minutes") == 300
    assert parse_duration("00:10:00") == 600 and parse_duration(90) == 90
    assert parse_duration("soon") is None


def test_by_meaning(compiled):
    commands = compiled.commands
    assert not commands["what_time"].takes_something
    assert commands["lights_on"].takes_something and () in commands["lights_on"].combinations  # or an area, or a floor
    assert commands["add_item"].takes_something and commands["start_timer"].takes_something
    near = {"intent": "what_time", "similarity": 0.86, "next": "'tell me a joke'", "next_similarity": 0.7}
    assert by_meaning(near, commands["what_time"])[0]
    assert not by_meaning({**near, "similarity": 0.75}, commands["what_time"])[0]
    assert not by_meaning({**near, "next_similarity": 0.83}, commands["what_time"])[0]
    # It picks the command; what was said in it is the model's to hear.
    assert by_meaning({**near, "intent": "add_item"}, commands["add_item"])[0]


class FakeEngine:
    def __init__(self, compiled):
        self.compiled = compiled
        self.dynamic = {"running_timers": []}


def far():
    return {"intent": "what_time", "similarity": 0.4, "next": None, "next_similarity": None}


def near():
    return {"intent": "what_time", "similarity": 0.9, "next": None, "next_similarity": 0.5}


def ranked():
    return [("add_item", 0.7), ("what_time", 0.5), ("lights_on", 0.4)]


class StubGliner:
    """GLiNER's place: says the probabilities and entities it is told to, and
    remembers the labels and entity types it was given."""

    model = "stub-gliner"
    name = "Stub GLiNER"

    def __init__(self, scores, entities=None, none=0.1):
        self.scores, self.entities, self.none = scores, entities or {}, none
        self.threshold = 0.3
        self.ready = False
        self.signature = None
        self.labels = self.types = None

    def load(self, signature=None):
        self.ready, self.signature = True, signature

    def pick(self, text, labels, entities):
        self.labels, self.types = labels, entities
        probabilities = {label: self.scores.get(label, 0.01) for label in labels}
        probabilities[labels[-1]] = self.none
        return {"classification": probabilities, "entities": self.entities}, 0.02

    def hear(self, text, entities):
        self.labels, self.types = None, entities
        return {"entities": self.entities}, 0.01


def gliner_fallback(stub, chosen_ids, store=STORE):
    compiled = chosen_for(store, llm=chosen_ids)
    fallback = Fallback(model=stub, top_k=8)
    fallback.prepare(FakeEngine(compiled), wait=True)
    return fallback, compiled


def everything():
    """Every command, closest first: what an embedding would say."""
    return [("add_item", 0.8), ("start_timer", 0.7), ("set_brightness", 0.6), ("lights_on", 0.5),
            ("device_on", 0.4), ("light_color", 0.3), ("what_time", 0.2), ("set_alarm", 0.1)]


def test_the_chain():
    compiled = chosen_for(STORE, meaning=["what_time"], llm=["add_item", "what_time", "lights_on"])
    stub = StubGliner({"add something to the shopping list": 0.8}, {"item": [("milk", 0.97, 8, 12)]})
    fallback = Fallback(embedding_accept=True, model=stub, top_k=8)
    fallback.prepare(FakeEngine(compiled), wait=True)
    assert fallback.status == "ready" and stub.signature == fallback.signature == "stub-gliner"

    found = fallback.decide("we need milk", far, compiled, KITCHEN, everything)
    assert found.command.id == "add_item" and found.by == "gliner" and found.slots == {"item": "milk"}
    assert found.trace["meaning"]["why"] == "not close enough"

    # Close in meaning and nothing to say: GLiNER isn't asked.
    stub.labels = "not asked"
    found = fallback.decide("have you got the time", near, compiled, KITCHEN, everything)
    assert found.command.id == "what_time" and found.by == "meaning" and stub.labels == "not asked"

    # It picks something this satellite may not run: not run, said why.
    compiled.commands["add_item"].satellites = ("elsewhere",)
    found = fallback.decide("we need milk", far, compiled, KITCHEN, everything)
    assert found.command is None and found.trace["llm"]["command"] == "add_item" and found.trace["llm"]["problems"]
    compiled.commands["add_item"].satellites = None

    # It says it is not a command: nothing.
    stub.none = 0.9
    found = fallback.decide("what's the capital of france", far, compiled, KITCHEN, everything)
    assert found.command is None and found.trace["llm"]["said"] == "not a command"
    assert model_note(found.trace) == "GLiNER: not a command"


def test_a_pick_that_takes_none_of_what_was_heard_gives_way():
    """"I need cookies to be on the shopping list" was closest to what's on
    the list, which takes no item; GLiNER heard an item, which adding takes."""
    store = {**STORE, "commands": {**STORE["commands"], "list_items": {
        "description": "say what's on the shopping list", "examples": ["what's on the shopping list"], "response": "Milk."}}}
    stub = StubGliner({"say what's on the shopping list": 0.44, "add something to the shopping list": 0.39},
                      {"item": [("cookies", 0.99, 7, 14)]}, none=0.04)
    fallback, compiled = gliner_fallback(stub, ["add_item", "list_items"], store)

    def closest():
        return [("list_items", 0.82), *everything()]

    found = fallback.decide("I need cookies to be on the shopping list", far, compiled, KITCHEN, closest)
    assert found.command.id == "add_item" and found.slots == {"item": "cookies"}
    assert found.trace["llm"]["instead_of"] == {"command": "list_items", "why": "it takes none of what was heard"}
    # Nothing heard: the pick stands (and runs only if meaning puts it first).
    stub.entities = {}
    found = fallback.decide("what do we need", far, compiled, KITCHEN, closest)
    assert found.command is None and found.trace["llm"]["refused"]["command"] == "list_items"


def test_only_chosen_commands():
    # Nothing chosen: neither step runs anything, and GLiNER is not loaded.
    compiled = compile_store(STORE)
    assert chosen(compiled, "llm") == [] and chosen(compiled, "meaning") == []
    stub = StubGliner({"tell the time": 0.9})
    fallback = Fallback(embedding_accept=True, model=stub, top_k=8)
    fallback.prepare(FakeEngine(compiled), wait=True)
    assert fallback.status == "no commands chosen for it" and not stub.ready
    assert fallback.decide("have you got the time", near, compiled, KITCHEN, everything).command is None

    # Close in meaning, but not chosen for meaning; likeliest to GLiNER, but
    # not chosen for it either, and the chosen ones are unlikely.
    compiled = chosen_for(STORE, meaning=["lights_on"], llm=["lights_on", "add_item"])
    fallback = Fallback(embedding_accept=True, model=stub, top_k=8)
    fallback.prepare(FakeEngine(compiled), wait=True)
    found = fallback.decide("have you got the time", near, compiled, KITCHEN, everything)
    assert found.command is None and found.trace["meaning"]["why"] == "not chosen for meaning"
    assert found.trace["llm"]["said"] == "not a command" and found.trace["llm"]["surer"] == ["what_time"]


def test_gliner_picks_among_the_closest():
    stub = StubGliner({"add something to the shopping list": 0.8}, {"item": [("milk", 0.97, 8, 12)]})
    fallback, compiled = gliner_fallback(stub, ["add_item"])
    assert fallback.status == "ready" and stub.ready
    found = fallback.decide("we need milk", far, compiled, KITCHEN, everything)
    assert found.command.id == "add_item" and found.by == "gliner" and found.slots == {"item": "milk"}
    # The chosen one among others to tell it from, the closest 8 -- those that
    # hear nothing too.
    assert stub.labels[:-1] == [
        "add something to the shopping list", "set a timer", "set the brightness of the lights", "turn on the lights",
        "turn on a device", "change the color of the lights", "tell the time", "set an alarm",
    ]
    assert stub.labels[-1] == "something else: a question, small talk or thanks"
    assert stub.types["item"] == "something to buy, or a task to do, as said" and "duration" in stub.types
    trace = found.trace["llm"]
    assert trace["by"] == "GLiNER" and trace["candidates"][0] == "add_item" and trace["scores"]["add_item"] == 0.8


def test_gliner_runs_only_a_chosen_command_it_is_sure_of():
    # It picks among the chosen ones; the others tell them apart. A sentence
    # for another leaves the chosen ones unsure.
    picks_timer = StubGliner({"set a timer": 0.7, "add something to the shopping list": 0.2})
    fallback, compiled = gliner_fallback(picks_timer, ["add_item"])
    found = fallback.decide("give me ten minutes", far, compiled, KITCHEN, everything)
    assert found.command is None and found.trace["llm"]["surer"] == ["start_timer"]
    assert model_note(found.trace) == "GLiNER: not sure enough (0.20, needs 0.30); start_timer likelier, not chosen for it"
    # Sure enough of a chosen one, though another is likelier: it runs.
    second = StubGliner({"turn on a device": 0.38, "add something to the shopping list": 0.35},
                        {"item": [("cookies", 0.99, 8, 15)]})
    fallback, compiled = gliner_fallback(second, ["add_item"])
    found = fallback.decide("we need cookies on the list for shopping", far, compiled, KITCHEN, everything)
    assert found.command.id == "add_item" and found.slots == {"item": "cookies"}
    assert found.trace["llm"]["surer"] == ["device_on"]

    unsure = StubGliner({"add something to the shopping list": 0.2}, {"item": [("milk", 0.97, 8, 12)]})
    fallback, compiled = gliner_fallback(unsure, ["add_item"])
    found = fallback.decide("milk", far, compiled, KITCHEN, everything)
    assert found.command is None and found.trace["llm"]["said"] == "not sure enough (0.20, needs 0.30)"

    not_a_command = StubGliner({"add something to the shopping list": 0.4}, none=0.6)
    fallback, compiled = gliner_fallback(not_a_command, ["add_item"])
    found = fallback.decide("thank you", far, compiled, KITCHEN, everything)
    assert found.command is None and model_note(found.trace) == "GLiNER: not a command"

    # Sure, but what it needs was not found: not run, and hinted from it.
    nothing_said = StubGliner({"add something to the shopping list": 0.9})
    fallback, compiled = gliner_fallback(nothing_said, ["add_item"])
    found = fallback.decide("we're out", far, compiled, KITCHEN, everything)
    assert found.command is None and found.trace["llm"]["command"] == "add_item" and found.trace["llm"]["problems"] == ["no item"]


def test_gliner_arguments():
    # A number as said, a name without its article.
    dim = StubGliner({"set the brightness of the lights": 0.9}, {"percentage": [("thirty percent", 0.9, 0, 14)]})
    fallback, compiled = gliner_fallback(dim, ["set_brightness"])
    found = fallback.decide("thirty percent please", far, compiled, KITCHEN, everything)
    assert found.command.id == "set_brightness" and found.slots == {"percentage": 30}
    coffee = StubGliner({"turn on a device": 0.9}, {"device": [("the coffee maker", 0.9, 6, 22)]})
    fallback, compiled = gliner_fallback(coffee, ["device_on"])
    found = fallback.decide("start the coffee maker", far, compiled, KITCHEN, everything)
    assert found.command.id == "device_on" and found.slots == {"entity": ["switch.coffee_maker"]}


def test_gliner_skips():
    # Its commands are not among the closest: not asked.
    stub = StubGliner({})
    fallback, compiled = gliner_fallback(stub, ["set_alarm"])
    found = fallback.decide("we need milk", far, compiled, KITCHEN, lambda: everything()[:5])
    assert found.trace["llm"]["skipped"] == "none of its commands is close in meaning" and stub.labels is None


def test_gliner_without_meaning_is_given_its_commands():
    # Without an embedding model nothing is closest: the commands chosen for
    # it first, then the others, to top_k -- not the first by id, which left
    # out every chosen one sorted after them.
    stub = StubGliner({"set an alarm": 0.9})
    compiled = chosen_for(STORE, llm=["set_alarm"])
    fallback = Fallback(model=stub, top_k=3)
    fallback.prepare(FakeEngine(compiled), wait=True)
    found = fallback.decide("wake me at seven", None, compiled, KITCHEN, None)
    assert "skipped" not in found.trace["llm"]
    assert stub.labels == ["set an alarm", "add something to the shopping list", "turn on a device",
                           "something else: a question, small talk or thanks"]


def test_gliner_picks_a_command_that_hears_nothing_too():
    # One label among the others, and "not a command". Hearing nothing in it,
    # it runs only when meaning puts it first too -- ahead of the everyday
    # requests that are no command.
    stub = StubGliner({"tell the time": 0.8})
    fallback, compiled = gliner_fallback(stub, ["what_time"])

    def time_first():
        return [("what_time", 0.9), *[c for c in everything() if c[0] != "what_time"]]

    def time_near():
        return {"intent": "what_time", "similarity": 0.8, "next": "'tell me a joke'", "next_similarity": 0.5}

    found = fallback.decide("have you got the time", time_near, compiled, KITCHEN, time_first)
    assert found.command.id == "what_time" and found.by == "gliner" and found.slots == {}
    assert "tell the time" in stub.labels and stub.labels[-1] == "something else: a question, small talk or thanks"
    found = fallback.decide("have you got the time", lights_near, compiled, KITCHEN, everything)
    assert found.command is None and "not what meaning puts first" in found.trace["llm"]["refused"]["why"]

    def thanks_near():
        return {"intent": "what_time", "similarity": 0.6, "next": "'thank you'", "next_similarity": 0.96}

    found = fallback.decide("thank you", thanks_near, compiled, KITCHEN, time_first)
    assert found.command is None and found.trace["llm"]["refused"]["why"] == (
        "closer in meaning to 'thank you', which is no command, and nothing was heard in it")
    stub.none = 0.9
    found = fallback.decide("thank you", time_near, compiled, KITCHEN, time_first)
    assert found.command is None and found.trace["llm"]["said"] == "not a command"


def lights_near():
    return {"intent": "lights_on", "similarity": 0.88, "next": "'tell me a joke'", "next_similarity": 0.7}


def test_meaning_picks_the_command_and_gliner_hears_what_was_said():
    compiled = chosen_for(STORE, meaning=["lights_on"])
    heard_a_room = StubGliner({}, {"room": [("the lounge", 0.9, 13, 23)]})
    fallback = Fallback(embedding_accept=True, model=heard_a_room, top_k=8)
    fallback.prepare(FakeEngine(compiled), wait=True)
    found = fallback.decide("lights up in the lounge", lights_near, compiled, KITCHEN, everything)
    assert found.command.id == "lights_on" and found.by == "meaning" and found.slots == {"area": ["living_room"]}
    assert found.trace["meaning"]["heard_by"] == "GLiNER" and found.trace["meaning"]["combination"] == ["area"]
    assert heard_a_room.labels is None and set(heard_a_room.types) == {"room", "floor"}  # its slots only
    # Nothing heard: the combination of nothing, the satellite's area.
    fallback.model.entities = {}
    found = fallback.decide("lights up", lights_near, compiled, KITCHEN, everything)
    assert found.command.id == "lights_on" and found.slots == {}
    # The same words as a room and as a floor: the surer one, if it has it.
    fallback.model.entities = {"room": [("upstairs", 0.6, 10, 18)], "floor": [("upstairs", 0.8, 10, 18)]}
    found = fallback.decide("lights up upstairs", lights_near, compiled, KITCHEN, everything)
    assert found.slots == {"floor": ["upstairs"]}
    fallback.model.entities = {"room": [("lounge", 0.9, 14, 20)], "floor": [("lounge", 0.95, 14, 20)]}
    found = fallback.decide("lights up, the lounge", lights_near, compiled, KITCHEN, everything)
    assert found.slots == {"area": ["living_room"]}  # no floor of that name
    # A room and a floor: no combination of it, so nothing runs.
    fallback.model.entities = {"room": [("kitchen", 0.9, 13, 20)], "floor": [("upstairs", 0.9, 21, 29)]}
    found = fallback.decide("lights up in kitchen upstairs", lights_near, compiled, KITCHEN, everything)
    assert found.command is None and found.trace["meaning"]["problems"] == ["area and floor don't go together"]
    # Without a model to hear them, a command that hears something isn't run by meaning.
    fallback = Fallback(embedding_accept=True, model=None)
    fallback.prepare(FakeEngine(compiled), wait=True)
    found = fallback.decide("lights up in the lounge", lights_near, compiled, KITCHEN, everything)
    assert found.command is None and "no model to hear them" in found.trace["meaning"]["why"]


def test_gliner_hearing_nothing_runs_only_what_meaning_puts_first():
    stub = StubGliner({"turn on the lights": 0.8})
    fallback, compiled = gliner_fallback(stub, ["lights_on"])
    found = fallback.decide("some light please", far, compiled, KITCHEN, everything)
    assert found.command is None and "not what meaning puts first" in found.trace["llm"]["refused"]["why"]

    def lights_first():
        return [("lights_on", 0.9), *[c for c in everything() if c[0] != "lights_on"]]

    found = fallback.decide("some light please", lights_near, compiled, KITCHEN, lights_first)
    assert found.command.id == "lights_on" and found.slots == {} and found.trace["llm"]["combination"] == []


def test_a_room_said_must_be_taken():
    compiled = compile_store(STORE)
    tools = Arguments(compiled)
    commands = compiled.commands
    assert tools.places_left(commands["what_time"], {}, "what time is it in the kitchen") == ["area Kitchen"]
    assert tools.places_left(commands["what_time"], {}, "what time is it upstairs") == ["floor Upstairs"]
    assert tools.places_left(commands["lights_on"], {"area": ["living_room"]}, "lights on in the lounge") == []
    assert tools.places_left(commands["lights_on"], {}, "lights on in the lounge") == ["area Living Room"]
    # Free text may say a room: "office supplies", "the kitchen sink".
    assert tools.places_left(commands["add_item"], {"item": "kitchen roll"}, "add kitchen roll to the list") == []
    assert tools.places_left(commands["what_time"], {}, "what time is it") == []
    # A device's own name may say a room.
    store = {**STORE, "names": {**STORE["names"], "entities": {"fan.kitchen_fan": {"name": "Kitchen Fan"}}}}
    store["commands"] = {**STORE["commands"], "device_on": {**STORE["commands"]["device_on"], "names": {"entity": {"count": 1}}}}
    fans = Arguments(compile_store(store))
    device_on = compile_store(store).commands["device_on"]
    assert fans.places_left(device_on, {"entity": ["fan.kitchen_fan"]}, "turn on the kitchen fan") == []
    assert fans.places_left(device_on, {"entity": ["fan.kitchen_fan"]}, "turn on the kitchen fan upstairs") == ["floor Upstairs"]

    # Meaning picked a command that hears nothing; the sentence names a room.
    compiled = chosen_for(STORE, meaning=["what_time"])
    fallback = Fallback(embedding_accept=True, model=None)
    fallback.prepare(FakeEngine(compiled), wait=True)
    found = fallback.decide("what's the time in the kitchen", near, compiled, KITCHEN, ranked)
    assert found.command is None and found.trace["meaning"]["problems"] == ["heard area Kitchen, which it doesn't take"]
    assert model_note(found.trace) == "meaning picked what_time, not run: heard area Kitchen, which it doesn't take"
    assert fallback.decide("what's the time", near, compiled, KITCHEN, ranked).command.id == "what_time"

    # GLiNER picked one that takes no room.
    stub = StubGliner({"turn on a device": 0.9}, {"device": [("coffee maker", 0.9, 8, 20)]})
    fallback, compiled = gliner_fallback(stub, ["device_on"])
    found = fallback.decide("start the coffee maker in the kitchen", far, compiled, KITCHEN, everything)
    assert found.command is None and found.trace["llm"]["problems"] == ["heard area Kitchen, which it doesn't take"]


VERSIONS = {
    "names": {
        "areas": {"kitchen": {"name": "Kitchen"}},
        "entities": {"media_player.mark_ii": {"name": "Mark 2"}},
    },
    "commands": {
        "lights_off_here": {
            "description": "turn off the lights",
            "require": [["turn off"], ["lights"]],
            "action": {"action": "light.turn_off", "target": "{{ target }}"},
        },
        "lights_off_place": {
            "description": "turn off the lights",
            "require": [["turn off"], ["lights"]],
            "names": {"area": {"count": 1}},
            "action": {"action": "light.turn_off", "target": "{{ target }}"},
        },
        "play_here": {
            "description": "play music",
            "require": [["play"]],
            "wildcard": {"slot": "query", "after": ["play"], "to_end": True},
            "music": "play",
        },
        "play_device": {
            "description": "play music",
            "require": [["play"]],
            "optional": [["on"]],
            "wildcard": {"slot": "query", "after": ["play"], "to_end": True},
            "names": {"entity": {"count": 1, "domains": ["media_player"]}},
            "combinations": [["entity", "query"]],
            "music": "play",
        },
        "what_time": {"description": "tell the time", "examples": ["what time is it"], "response": "Noon."},
    },
}


def versions_near():
    return {"intent": "lights_off_here", "similarity": 0.88, "next": "'tell me a joke'", "next_similarity": 0.7}


def versions_ranked():
    return [("lights_off_here", 0.88), ("lights_off_place", 0.88), ("play_here", 0.5), ("play_device", 0.5), ("what_time", 0.2)]


def test_meaning_picks_the_command_and_what_was_heard_picks_its_version():
    compiled = chosen_for(VERSIONS, meaning=["lights_off_here", "lights_off_place"])
    stub = StubGliner({})
    fallback = Fallback(embedding_accept=True, model=stub, top_k=8)
    fallback.prepare(FakeEngine(compiled), wait=True)
    found = fallback.decide("kill the lights", versions_near, compiled, KITCHEN, versions_ranked)
    assert found.command.id == "lights_off_here" and found.slots == {} and found.by == "meaning"
    assert set(stub.types) == {"room"}  # what either version hears
    stub.entities = {"room": [("the kitchen", 0.9, 16, 27)]}
    found = fallback.decide("kill the lights in the kitchen", versions_near, compiled, KITCHEN, versions_ranked)
    assert found.command.id == "lights_off_place" and found.slots == {"area": ["kitchen"]}
    assert found.trace["meaning"]["command"] == "lights_off_place" and found.trace["meaning"]["combination"] == ["area"]
    # Only the version for here chosen: the room said is left out, so nothing runs.
    compiled = chosen_for(VERSIONS, meaning=["lights_off_here"])
    fallback.prepare(FakeEngine(compiled), wait=True)
    found = fallback.decide("kill the lights in the kitchen", versions_near, compiled, KITCHEN, versions_ranked)
    assert found.command is None and found.trace["meaning"]["problems"] == ["heard area Kitchen, which it doesn't take"]


def test_gliner_knows_a_command_by_one_label_and_hears_its_version():
    stub = StubGliner({"play music": 0.8}, {"music": [("jazz", 0.95, 5, 9)], "device": [("the mark 2", 0.9, 13, 23)]})
    fallback, compiled = gliner_fallback(stub, ["play_here", "play_device"], VERSIONS)

    def play_first():
        return [("play_here", 0.81), ("play_device", 0.8), ("what_time", 0.3), ("lights_off_here", 0.21), ("lights_off_place", 0.2)]

    found = fallback.decide("play jazz on the mark 2", far, compiled, KITCHEN, play_first)
    assert found.command.id == "play_device" and found.slots == {"entity": ["media_player.mark_ii"], "query": "jazz"}
    assert stub.labels == ["play music", "tell the time", "turn off the lights", "something else: a question, small talk or thanks"]
    assert found.trace["llm"]["candidates"] == ["play_here", "what_time", "lights_off_here"]
    stub.entities = {"music": [("jazz", 0.95, 5, 9)]}
    found = fallback.decide("play jazz", far, compiled, KITCHEN, play_first)
    assert found.command.id == "play_here" and found.slots == {"query": "jazz"}


def test_a_step_turned_off_says_so():
    # Chosen for a step that is turned off (Settings): the trace says why it
    # did not run, instead of nothing.
    compiled = chosen_for(STORE, meaning=["what_time"], llm=["what_time"])
    fallback = Fallback(embedding_accept=False, model=None)
    fallback.status = "turned off on the Settings page"
    fallback.prepare(FakeEngine(compiled), wait=True)
    found = fallback.decide("have you got the time", near, compiled, KITCHEN, ranked)
    assert found.command is None
    assert found.trace == {
        "meaning": {"skipped": "turned off on the Settings page"},
        "llm": {"by": "GLiNER", "skipped": "turned off on the Settings page"},
    }
    # Nothing chosen for them: nothing said.
    compiled = compile_store(STORE)
    fallback.prepare(FakeEngine(compiled), wait=True)
    assert fallback.decide("have you got the time", near, compiled, KITCHEN, ranked).trace == {}
