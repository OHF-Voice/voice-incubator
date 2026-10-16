"""The fallback: commands as LLM tools and back, the checks on what it says,
and the chain -- with a stand-in for the model."""

from __future__ import annotations

import pytest

from voice_call.engine import Satellite, llm_note
from voice_call.fallback import (
    Fallback,
    Tools,
    by_meaning,
    chosen,
    needs_heard_slots,
    parse_calls,
    parse_duration,
    parse_hammer,
)
from voice_call.store import compile_store

STORE = {
    "names": {
        "areas": {"kitchen": {"name": "Kitchen"}, "living_room": {"name": "Living Room", "aliases": ["Lounge"]}},
        "entities": {"switch.coffee_maker": {"name": "Coffee Maker"}, "cover.garage_door": {"name": "Garage Door"}},
    },
    "lists": {"colors": {"red": ["red", "crimson"], "blue": "blue"}},
    "commands": {
        "lights_on": {
            "description": "turn on the lights",
            "require": [["turn on"], ["lights"]],
            "names": {"area": {"count": 1, "optional": True}},
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
            "wildcard": {"slot": "item", "after": ["add"], "before": ["to"], "bounded": True, "required": True},
            "action": {"action": "todo.add_item", "target": {"entity_id": "todo.shopping_list"}},
        },
        "what_time": {"description": "tell the time", "examples": ["what time is it"], "response": "Noon."},
        "set_alarm": {
            "description": "set an alarm",
            "require": [["alarm"]],
            "optional": [["for", "at"]],
            "wildcard": {"slot": "time", "after": ["for", "at"], "to_end": True, "required": True},
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


def test_commands_become_tools(compiled):
    tools = Tools(compiled)
    by_name = {spec["function"]["name"]: spec["function"] for spec in tools.specs}
    assert set(by_name) == set(STORE["commands"])
    area = by_name["lights_on"]["parameters"]["properties"]["area"]
    assert area["enum"] == ["Kitchen", "Living Room"]
    assert by_name["lights_on"]["parameters"]["required"] == []
    entity = by_name["device_on"]["parameters"]["properties"]["entity"]
    assert entity["enum"] == ["Coffee Maker"]  # switches only
    assert by_name["light_color"]["parameters"]["properties"]["color"]["enum"] == ["blue", "red"]
    assert by_name["start_timer"]["parameters"]["properties"]["duration"]["type"] == "string"
    assert by_name["set_brightness"]["parameters"]["properties"]["percentage"] == {"type": "number", "minimum": 0, "maximum": 100}
    assert by_name["add_item"]["parameters"]["required"] == ["item"]
    # How it is said: its examples, else its keywords.
    assert 'Said like: "what time is it"' in by_name["what_time"]["description"]
    assert 'Said like: "turn on lights"' in by_name["lights_on"]["description"]
    assert [s["function"]["name"] for s in tools.subset(["what_time", "lights_on", "nope"])] == ["what_time", "lights_on"]


def test_arguments_become_slots(compiled):
    tools = Tools(compiled)
    commands = compiled.commands
    assert tools.slots(commands["lights_on"], {"area": "lounge"}, "lights on in the lounge") == ({"area": ["living_room"]}, [])
    # A name the sentence does not say is dropped: a small model makes them up.
    assert tools.slots(commands["lights_on"], {"area": "Kitchen"}, "kill the lights") == ({}, [])
    # Not a name it may have: dropped when optional, a problem when required.
    assert tools.slots(commands["lights_on"], {"area": "Garage"}, "garage lights") == ({}, [])
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


def test_parsing():
    assert parse_hammer('```\n[{"name": "lights_on", "arguments": {"area": "Kitchen"}}]\n```') == [("lights_on", {"area": "Kitchen"})]
    assert parse_hammer("```\n[{'name': 'x', 'arguments': {'on': true}}]\n```") == [("x", {"on": True})]
    assert parse_hammer('[{"type": "function", "function": {"name": "y", "arguments": "{\\"a\\": 1}"}}]') == [("y", {"a": 1})]
    assert parse_hammer("```\n[]\n```") == [] and parse_hammer("I don't know") == []
    # Granite: <tool_call> blocks, the last one cut at its closing tag (a stop
    # sequence), or the objects alone when the tags are not printed.
    granite = '<tool_call>\n{"name": "lights_on", "arguments": {"area": "Kitchen"}}\n'
    assert parse_calls(granite) == [("lights_on", {"area": "Kitchen"})]
    assert parse_calls(granite + "</tool_call>\n<tool_call>\n{\"name\": \"x\", \"arguments\": {}}") == [
        ("lights_on", {"area": "Kitchen"}), ("x", {})
    ]
    assert parse_calls('{"name": "a", "arguments": {}}\n{"name": "b", "arguments": {}}') == [("a", {}), ("b", {})]
    assert parse_calls("```\n[]\n```") == [] and parse_calls("I can't do that.") == []
    assert parse_duration("10 minutes") == 600
    assert parse_duration("1 hour and 30 minutes") == 5400
    assert parse_duration("an hour and a half") == 5400
    assert parse_duration("five minutes") == 300
    assert parse_duration("00:10:00") == 600 and parse_duration(90) == 90
    assert parse_duration("soon") is None


def test_by_meaning(compiled):
    commands = compiled.commands
    assert not needs_heard_slots(commands["what_time"]) and not needs_heard_slots(commands["lights_on"])
    assert needs_heard_slots(commands["add_item"]) and needs_heard_slots(commands["start_timer"])
    near = {"intent": "what_time", "similarity": 0.86, "next": "'tell me a joke'", "next_similarity": 0.7}
    assert by_meaning(near, commands["what_time"])[0]
    assert not by_meaning({**near, "similarity": 0.75}, commands["what_time"])[0]
    assert not by_meaning({**near, "next_similarity": 0.83}, commands["what_time"])[0]
    assert by_meaning({**near, "intent": "add_item"}, commands["add_item"])[1]["why"] == "needs something said"


class StubLLM:
    """Says what it is told to, and remembers what it was given."""

    name = "stub"
    model = "stub"
    way_out = True

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.ready = False
        self.signature = None
        self.loaded = None
        self.given = None

    def load(self, tools, signature):
        self.ready, self.signature, self.loaded = True, signature, tools

    def call(self, text, tools=None):
        self.given = tools
        return parse_calls(self.reply), self.reply, 0.01


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


def test_the_chain():
    compiled = chosen_for(STORE, meaning=["what_time"], llm=["add_item", "what_time", "lights_on"])
    llm = StubLLM('```\n[{"name": "add_item", "arguments": {"item": "milk"}}]\n```')
    fallback = Fallback(embedding_accept=True, llm=llm, top_k=2)
    fallback.prepare(FakeEngine(compiled), wait=True)
    assert fallback.status == "ready" and llm.signature == fallback.signature and fallback.closest_only

    found = fallback.decide("we need milk", far, compiled, KITCHEN, ranked)
    assert found.command.id == "add_item" and found.by == "llm" and found.slots == {"item": "milk"}
    assert [spec["function"]["name"] for spec in llm.given] == ["add_item", "what_time", "not_a_command"]
    assert found.trace["meaning"]["why"] == "not close enough"

    # Close in meaning and nothing to say: no LLM needed.
    llm.given = "not asked"
    found = fallback.decide("have you got the time", near, compiled, KITCHEN, ranked)
    assert found.command.id == "what_time" and found.by == "meaning" and llm.given == "not asked"

    # The LLM picks something this satellite may not run: not run, said why.
    limited = Satellite("sat", "Kids", "kitchen", command_groups=frozenset({"kids"}))
    found = fallback.decide("we need milk", far, compiled, limited, ranked)
    assert found.command is None and found.trace["llm"]["skipped"] == "none of its commands is available here"

    # It calls nothing, or says it is not a command: nothing.
    llm.reply = "```\n[]\n```"
    assert fallback.decide("what's the capital of france", far, compiled, KITCHEN, ranked).command is None
    llm.reply = '<tool_call>\n{"name": "not_a_command", "arguments": {}}\n'
    found = fallback.decide("what's the capital of france", far, compiled, KITCHEN, ranked)
    assert found.command is None and found.trace["llm"]["said"] == "not a command"
    assert llm_note(found.trace) == "LLM: not a command"


def test_only_chosen_commands():
    # Nothing chosen: neither step runs anything, and the LLM is not loaded.
    compiled = compile_store(STORE)
    assert chosen(compiled, "llm") == [] and chosen(compiled, "meaning") == []
    llm = StubLLM('<tool_call>\n{"name": "what_time", "arguments": {}}\n')
    fallback = Fallback(embedding_accept=True, llm=llm, top_k=8)
    fallback.prepare(FakeEngine(compiled), wait=True)
    assert fallback.status == "no commands chosen for it" and not llm.ready
    assert fallback.decide("have you got the time", near, compiled, KITCHEN, ranked).command is None

    # Close in meaning, but not chosen for meaning.
    compiled = chosen_for(STORE, meaning=["lights_on"], llm=["lights_on", "add_item"])
    fallback = Fallback(embedding_accept=True, llm=llm, top_k=8)
    fallback.prepare(FakeEngine(compiled), wait=True)
    found = fallback.decide("have you got the time", near, compiled, KITCHEN, ranked)
    assert found.command is None and found.trace["meaning"]["why"] == "not chosen for meaning"
    # No more than top_k chosen: all of them, in a fixed order, read once (no
    # tools given per call); a command not chosen is not run.
    assert not fallback.closest_only
    assert [s["function"]["name"] for s in llm.loaded] == ["add_item", "lights_on", "not_a_command"]
    assert llm.given is None
    assert found.trace["llm"]["problems"] == ["no command what_time"]

    def lights_close():
        return [("lights_on", 0.8), ("add_item", 0.5)]

    llm.reply = '<tool_call>\n{"name": "lights_on", "arguments": {}}\n'
    found = fallback.decide("can i get some light", far, compiled, KITCHEN, lights_close)
    assert found.command.id == "lights_on" and found.by == "llm"
    # Every chosen command whatever the satellite (one prefix); availability after.
    limited = Satellite("sat", "Kids", "kitchen", command_groups=frozenset({"kids"}))
    found = fallback.decide("can i get some light", far, compiled, limited, lights_close)
    assert found.command is None and "command groups" in found.trace["llm"]["problems"][0]


def test_picks_must_be_close_in_meaning():
    compiled = chosen_for(STORE, llm=["lights_on", "add_item"])
    llm = StubLLM('<tool_call>\n{"name": "lights_on", "arguments": {}}\n')
    fallback = Fallback(llm=llm, top_k=8)
    fallback.prepare(FakeEngine(compiled), wait=True)

    def at(similarity):
        return lambda: [("lights_on", similarity), ("add_item", similarity)]

    # Nothing taken from the sentence: 0.70.
    assert fallback.decide("some light", far, compiled, KITCHEN, at(0.72)).command.id == "lights_on"
    found = fallback.decide("thank you", far, compiled, KITCHEN, at(0.68))
    assert found.command is None and found.trace["llm"]["refused"]["why"] == "not close in meaning"
    assert "command" not in found.trace["llm"]  # so no "it looks like you're trying to..." from it
    # Said in the call's summary.
    assert llm_note(found.trace) == "LLM picked lights_on, not close enough in meaning to run (0.68, needs 0.70)"
    # Something taken from it: 0.65.
    llm.reply = '<tool_call>\n{"name": "add_item", "arguments": {"item": "milk"}}\n'
    assert fallback.decide("we need milk", far, compiled, KITCHEN, at(0.68)).command.id == "add_item"
    assert fallback.decide("milk is a drink", far, compiled, KITCHEN, at(0.6)).command is None
    # No embedding model: no floor.
    assert fallback.decide("milk is a drink", far, compiled, KITCHEN, None).command.id == "add_item"


class StubGliner:
    """GLiNER's place: says the probabilities and entities it is told to, and
    remembers the labels and entity types it was given."""

    kind = "gliner"
    way_out = False
    model = "stub-gliner"
    name = "Stub GLiNER"

    def __init__(self, scores, entities=None, none=0.1):
        self.scores, self.entities, self.none = scores, entities or {}, none
        self.threshold = 0.3
        self.ready = False
        self.signature = None
        self.labels = self.types = None

    def load(self, specs=None, signature=None):
        self.ready, self.signature = True, signature

    def pick(self, text, labels, entities):
        self.labels, self.types = labels, entities
        probabilities = {label: self.scores.get(label, 0.01) for label in labels}
        probabilities[labels[-1]] = self.none
        return {"classification": probabilities, "entities": self.entities}, 0.02


def gliner_fallback(stub, chosen_ids, store=STORE):
    compiled = chosen_for(store, llm=chosen_ids)
    fallback = Fallback(llm=stub, top_k=8)
    fallback.prepare(FakeEngine(compiled), wait=True)
    return fallback, compiled


def everything():
    """Every command, closest first: what an embedding would say."""
    return [("add_item", 0.8), ("start_timer", 0.7), ("set_brightness", 0.6), ("lights_on", 0.5),
            ("device_on", 0.4), ("light_color", 0.3), ("what_time", 0.2), ("set_alarm", 0.1)]


def test_gliner_picks_among_the_closest_that_need_something_said():
    stub = StubGliner({"add something to the shopping list": 0.8}, {"item": [("milk", 0.97, 8, 12)]})
    fallback, compiled = gliner_fallback(stub, ["add_item"])
    assert fallback.status == "ready" and stub.ready
    found = fallback.decide("we need milk", far, compiled, KITCHEN, everything)
    assert found.command.id == "add_item" and found.by == "gliner" and found.slots == {"item": "milk"}
    # The chosen one among others to tell it from -- but not those that need
    # nothing said (meaning's): lights_on, what_time.
    assert stub.labels[:5] == [
        "add something to the shopping list", "set a timer", "set the brightness of the lights", "turn on a device",
        "change the color of the lights",
    ]
    assert stub.labels[-1] == "something else: a question, small talk or thanks"
    assert stub.types["item"] == "something to buy, or a task to do, as said" and "duration" in stub.types
    trace = found.trace["llm"]
    assert trace["by"] == "GLiNER" and trace["candidates"][0] == "add_item" and trace["scores"]["add_item"] == 0.8


def test_gliner_runs_only_a_chosen_command_it_is_sure_of():
    picks_timer = StubGliner({"set a timer": 0.7, "add something to the shopping list": 0.5})
    fallback, compiled = gliner_fallback(picks_timer, ["add_item"])
    found = fallback.decide("give me ten minutes", far, compiled, KITCHEN, everything)
    assert found.command is None and found.trace["llm"]["refused"] == {"command": "start_timer", "why": "not chosen for it"}
    assert llm_note(found.trace) == "GLiNER picked start_timer, not run: not chosen for it"

    unsure = StubGliner({"add something to the shopping list": 0.2}, {"item": [("milk", 0.97, 8, 12)]})
    fallback, compiled = gliner_fallback(unsure, ["add_item"])
    found = fallback.decide("milk", far, compiled, KITCHEN, everything)
    assert found.command is None and found.trace["llm"]["said"] == "not sure enough (0.20, needs 0.30)"

    not_a_command = StubGliner({"add something to the shopping list": 0.4}, none=0.6)
    fallback, compiled = gliner_fallback(not_a_command, ["add_item"])
    found = fallback.decide("thank you", far, compiled, KITCHEN, everything)
    assert found.command is None and llm_note(found.trace) == "GLiNER: not a command"

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
    # Only commands that need nothing said: meaning's, not GLiNER's.
    fallback, compiled = gliner_fallback(stub, ["lights_on"])
    found = fallback.decide("some light please", far, compiled, KITCHEN, everything)
    assert "meaning takes those" in found.trace["llm"]["skipped"]
