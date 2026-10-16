"""The fallback: commands as LLM tools and back, the checks on what it says,
and the chain -- with a stand-in for the model."""

from __future__ import annotations

import pytest

from voice_call.engine import Satellite
from voice_call.fallback import Fallback, Tools, by_meaning, needs_heard_slots, parse_duration, parse_hammer
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
    },
}
KITCHEN = Satellite("sat", "Kitchen", "kitchen", "Kitchen")


@pytest.fixture(name="compiled")
def fixture_compiled():
    return compile_store(STORE)


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


def test_parsing():
    assert parse_hammer('```\n[{"name": "lights_on", "arguments": {"area": "Kitchen"}}]\n```') == [("lights_on", {"area": "Kitchen"})]
    assert parse_hammer("```\n[{'name': 'x', 'arguments': {'on': true}}]\n```") == [("x", {"on": True})]
    assert parse_hammer('[{"type": "function", "function": {"name": "y", "arguments": "{\\"a\\": 1}"}}]') == [("y", {"a": 1})]
    assert parse_hammer("```\n[]\n```") == [] and parse_hammer("I don't know") == []
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


class StubHammer:
    """Says what it is told to, and remembers what it was given."""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.ready = False
        self.signature = None
        self.given = None

    def load(self, tools, signature):
        self.ready, self.signature = True, signature

    def call(self, text, tools=None):
        self.given = tools
        return parse_hammer(self.reply), self.reply, 0.01


class FakeEngine:
    def __init__(self, compiled):
        self.compiled = compiled
        self.dynamic = {"running_timers": []}


def test_the_chain(compiled):
    hammer = StubHammer('```\n[{"name": "add_item", "arguments": {"item": "milk"}}]\n```')
    fallback = Fallback(embedding_accept=True, hammer=hammer, top_k=2)
    fallback.prepare(FakeEngine(compiled), wait=True)
    assert fallback.status == "ready" and hammer.signature == fallback.signature

    def far():
        return {"intent": "what_time", "similarity": 0.4, "next": None, "next_similarity": None}

    def ranked():
        return [("add_item", 0.7), ("what_time", 0.5), ("lights_on", 0.4)]

    found = fallback.decide("we need milk", far, compiled, KITCHEN, ranked)
    assert found.command.id == "add_item" and found.by == "llm" and found.slots == {"item": "milk"}
    assert [spec["function"]["name"] for spec in hammer.given] == ["add_item", "what_time"]
    assert found.trace["meaning"]["why"] == "not close enough"

    # Close in meaning and nothing to say: no LLM needed.
    def near():
        return {"intent": "what_time", "similarity": 0.9, "next": None, "next_similarity": 0.5}

    hammer.given = "not asked"
    found = fallback.decide("have you got the time", near, compiled, KITCHEN, ranked)
    assert found.command.id == "what_time" and found.by == "meaning" and hammer.given == "not asked"

    # The LLM picks something this satellite may not run: not run, said why.
    limited = Satellite("sat", "Kids", "kitchen", command_groups=frozenset({"kids"}))
    found = fallback.decide("we need milk", far, compiled, limited, ranked)
    assert found.command is None and "command groups" in found.trace["llm"]["problems"][0]

    # It calls nothing: nothing.
    hammer.reply = "```\n[]\n```"
    assert fallback.decide("what's the capital of france", far, compiled, KITCHEN, ranked).command is None
