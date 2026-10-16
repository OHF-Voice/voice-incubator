"""Commands: parsing, presets, slots, groups, and the store."""

import pytest

from adapt_ctc.config import ConfigError, parse_config
from voice_call.commands import adapt_config, match_slots, parse_command, parse_commands, parse_groups
from voice_call.engine import Satellite
from voice_call.store import Store, compile_store

LIGHTS = {
    "description": "turn on the lights",
    "require": [["turn on"], ["lights"]],
    "action": {"action": "light.turn_on", "target": "{{ target }}"},
}


def command(**changes):
    return parse_command("lights", {**LIGHTS, **changes})


def test_everywhere_by_default():
    behavior = Satellite("sat1", "Kitchen", "kitchen").available(command())
    assert behavior.does == "action"
    assert behavior.sequence() == [{"action": "light.turn_on", "target": "{{ target }}"}]


def test_scope_and_overrides_are_no_more():
    # Where a command is available is its command groups'; what it does
    # elsewhere is a copy of it, in a group of its own.
    with pytest.raises(ConfigError, match=r"commands\.lights\.scope: no longer used: .*command group"):
        command(scope={"areas": ["kitchen"]})
    with pytest.raises(ConfigError, match=r"commands\.lights\.overrides: no longer used: .*copy of the command"):
        command(overrides=[{"scope": {"areas": ["office"]}, "scene": "scene.office"}])


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ({"description": "", "examples": ["a"]}, "description"),
        ({"description": "x"}, "needs examples or keywords"),
        ({"description": "x", "examples": ["a"]}, "needs a script, scene, action or response"),
        ({"description": "x", "examples": ["a"], "script": "light.x"}, "script entity id"),
        ({"description": "x", "examples": ["a"], "script": "script.a", "scene": "scene.b"}, "give one of"),
        ({"description": "x", "examples": ["a"], "response": "ok", "bogus": 1}, "unknown key"),
        ({"description": "x", "require": ["a"], "number": "nope", "response": "ok"}, "preset"),
    ],
)
def test_command_errors(spec, message):
    with pytest.raises(ConfigError, match=message):
        parse_command("x", spec)


def test_ids_are_slugs():
    with pytest.raises(ConfigError, match="lowercase"):
        parse_command("Turn On", {**LIGHTS})


def test_number_presets_and_slot_names():
    brightness = parse_command("b", {"description": "set the brightness", "require": [["brightness"]], "number": "percentage", "response": "ok"})
    assert brightness.number_slot == "percentage"
    assert brightness.intent["number"]["max"] == 100
    assert match_slots(brightness, {"number": 40, "area": ["kitchen"]}) == {"percentage": 40, "area": ["kitchen"]}
    named = parse_command("b", {"description": "x", "require": [["fan"]], "number": {"preset": "count", "slot": "speed"}, "response": "ok"})
    assert match_slots(named, {"number": 3}) == {"speed": 3}


def test_duration_adds_its_units_and_one_slot():
    sprinklers = parse_command(
        "s", {"description": "run the sprinklers", "require": [["sprinklers"]], "number": "duration", "response": "ok"}
    )
    assert ["seconds", "second", "minutes", "minute", "hours", "hour"] in sprinklers.intents["s"]["require"]
    parse_config({"intents": sprinklers.intents})  # units are keywords of the intent
    slots = match_slots(sprinklers, {"hours": 1, "minutes": 30})
    assert slots == {
        "duration": {"hours": 1, "minutes": 30, "seconds": 0, "total_seconds": 5400, "text": "01:30:00"}
    }


def test_script_gets_the_variables_and_returns_its_response():
    night = parse_command("n", {"description": "say good night", "examples": ["good night"], "script": "script.good_night"})
    sequence = night.behavior.sequence()
    assert sequence[0]["action"] == "script.good_night"
    assert sequence[0]["data"]["slots"] == "{{ slots }}"
    assert sequence[-1] == {"stop": "done", "response_variable": "result"}
    scene = parse_command("m", {"description": "start movie time", "examples": ["movie time"], "scene": "scene.movie"})
    assert scene.behavior.sequence() == [{"action": "scene.turn_on", "target": {"entity_id": "scene.movie"}}]


PAUSE = {"description": "pause a timer", "require": [["pause"], ["timer"]], "timer": "pause"}


def test_responses_by_outcome():
    pause = parse_command("p", {**PAUSE, "response": {"done": "Paused.", "already": "{{ result.speech }}", "no_timers": ""}})
    behavior = pause.behavior
    assert behavior.response == {"done": "Paused.", "already": "{{ result.speech }}"}  # empty ones dropped
    assert behavior.response_for("done") == "Paused."
    assert behavior.response_for("not_found") is None
    # A plain response is the done one.
    plain = parse_command("p", {**PAUSE, "response": "Paused."}).behavior
    assert (plain.response_for("done"), plain.response_for("failed")) == ("Paused.", None)
    # A script may return outcomes of its own; a scene only succeeds or fails.
    script = parse_command("n", {"description": "x", "examples": ["a"], "script": "script.a", "response": {"no_one_home": "Nobody's in."}})
    assert script.behavior.response_for("no_one_home") == "Nobody's in."
    with pytest.raises(ConfigError, match="no_one_home can't happen with a scene; use done, failed"):
        parse_command("m", {"description": "x", "examples": ["a"], "scene": "scene.a", "response": {"no_one_home": "?"}})


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ({**PAUSE, "response": {"missing_duration": "How long?"}}, "missing_duration can't happen with timer: pause"),
        ({**PAUSE, "response": {"Done": "x"}}, "lowercase"),
        ({**PAUSE, "response": {"done": ["x"]}}, "template string"),
        ({**PAUSE, "response": ["x"]}, "templates by outcome"),
        ({"description": "x", "examples": ["a"], "response": {"failed": "Oops."}}, "needs a script, scene, action or response"),
    ],
)
def test_response_errors(spec, message):
    with pytest.raises(ConfigError, match=message):
        parse_command("x", spec)


def test_adapt_config_has_every_enabled_command():
    commands = parse_commands({"a": LIGHTS, "b": {**LIGHTS, "disabled": True}})
    config = adapt_config({"names": {"areas": {"kitchen": "Kitchen"}, "entities": {}}}, commands)
    assert list(config["intents"]) == ["a"]
    assert config["settings"]["examples_first"] is True
    assert config["names"] == {"areas": {"kitchen": "Kitchen"}}
    assert adapt_config({}, {}) is None


def test_store_validates_with_adapt_ctc():
    with pytest.raises(ConfigError, match="not in the shared list"):
        compile_store(
            {"commands": {"a": {**LIGHTS, "names": {"entity": {"count": 1, "ids": ["light.nope"]}}}}}
        )
    with pytest.raises(ConfigError, match="unknown top-level"):
        compile_store({"commandz": {}})


def test_store_edits_keep_comments(tmp_path):
    store = Store(tmp_path)
    (tmp_path / "commands.yaml").write_text(
        "# my commands\nnames: {}\nlists: {}\ncommands:\n"
        "  # the lights\n  lights:\n    description: turn on the lights\n"
        "    require: [[turn on], [lights]]\n    response: ok\n"
    )
    store.load()
    store.put_command("time", {"description": "tell the time", "examples": ["what time is it"], "response": "Noon."})
    store.put_name("areas", "kitchen", {"name": "Kitchen", "aliases": ["Cookhouse"]})
    text = (tmp_path / "commands.yaml").read_text()
    assert "# my commands" in text and "# the lights" in text
    assert "examples: [what time is it]" in text
    compiled = store.load()
    assert set(compiled.commands) == {"lights", "time"}
    assert compiled.data["names"]["areas"]["kitchen"]["aliases"] == ["Cookhouse"]
    # Renaming keeps its place; deleting removes it.
    store.put_command("lights_on", compiled.data["commands"]["lights"], old_id="lights")
    assert list(store.load().commands) == ["lights_on", "time"]
    store.delete_command("time")
    assert list(store.load().commands) == ["lights_on"]
    # An invalid edit is refused and the file is untouched.
    before = (tmp_path / "commands.yaml").read_text()
    with pytest.raises(ConfigError):
        store.put_command("broken", {"description": "x"})
    assert (tmp_path / "commands.yaml").read_text() == before


def test_on_and_off_stay_words(tmp_path):
    store = Store(tmp_path)
    store.save_text(
        "commands:\n  off:\n    description: turn off\n    require: [[turn off]]\n"
        "    exclude: [on]\n    response: ok\n"
    )
    compiled = store.load()
    assert compiled.commands["off"].intent["exclude"] == ["on"]


def test_command_groups_limit_a_satellite():
    lights = command(groups=["kids", "downstairs"])
    other = command()
    assert lights.groups == ("kids", "downstairs") and other.groups == ()
    kids = Satellite("sat1", "Kids", "kids_room", command_groups=frozenset({"kids"}))
    anywhere = Satellite("sat2", "Kitchen", "kitchen")
    assert kids.available(lights) is not None and kids.available(other) is None
    assert kids.why_unavailable(other) == "not in this satellite's command groups"
    # Not limited: groups change nothing.
    assert anywhere.available(lights) is not None and anywhere.available(other) is not None
    assert kids.as_dict()["command_groups"] == ["kids"] and anywhere.as_dict()["command_groups"] is None


def test_fallback_steps_are_chosen():
    assert command().fallback == ()
    assert command(fallback=["llm", "meaning"]).fallback == ("meaning", "llm")
    assert command(fallback="llm").fallback == ("llm",)
    with pytest.raises(ConfigError, match="'guess' is not one of meaning, llm"):
        command(fallback=["guess"])


def test_choosing_commands_for_the_fallback(tmp_path):
    store = Store(tmp_path)
    store.save_text(
        "commands:\n"
        "  lights:\n    description: turn on the lights\n    require: [[lights]]\n    response: ok\n"
        "  fan:\n    description: turn on the fan\n    require: [[fan]]\n    response: ok\n    fallback: [meaning]\n"
    )
    compiled = store.put_fallback({"llm": ["lights", "fan"]})
    assert compiled.commands["lights"].fallback == ("llm",)
    assert compiled.commands["fan"].fallback == ("meaning", "llm")  # meaning left as it was
    compiled = store.put_fallback({"meaning": [], "llm": ["fan"]})
    assert compiled.commands["lights"].fallback == () and compiled.commands["fan"].fallback == ("llm",)
    assert "fallback" not in store.read_text().split("fan:")[0]  # not left empty
    with pytest.raises(ConfigError, match="No command 'nope'"):
        store.put_fallback({"llm": ["nope"]})


def test_command_group_errors():
    with pytest.raises(ConfigError, match="group ids"):
        command(groups=["Kids Room"])
    with pytest.raises(ConfigError, match="no group 'kids'"):
        compile_store({"commands": {"lights": {**LIGHTS, "groups": ["kids"]}}})
    with pytest.raises(ConfigError, match="unknown key"):
        parse_groups({"kids": {"name": "Kids", "commands": []}})
    groups = parse_groups({"kids_room": None, "night": {"name": "Night", "description": "quiet"}})
    assert groups == {"kids_room": {"name": "Kids room", "description": ""}, "night": {"name": "Night", "description": "quiet"}}
    compiled = compile_store({"groups": {"kids": {"name": "Kids"}}, "commands": {"lights": {**LIGHTS, "groups": ["kids"]}}})
    assert compiled.groups == {"kids": {"name": "Kids", "description": ""}}


def test_store_edits_command_groups(tmp_path):
    store = Store(tmp_path)
    store.save_text(
        "commands:\n"
        "  lights:\n    description: turn on the lights\n    require: [[turn on], [lights]]\n    response: ok\n"
        "  time:\n    description: tell the time\n    examples: [what time is it]\n    response: Noon.\n"
    )
    store.put_group("kids", {"name": "Kids", "description": ""}, ["time"])
    compiled = store.load()
    assert compiled.groups["kids"]["name"] == "Kids"
    assert compiled.commands["time"].groups == ("kids",) and compiled.commands["lights"].groups == ()
    # Exactly these commands; a rename alone keeps the members.
    store.put_group("kids", {"name": "Kids", "description": ""}, ["lights"])
    store.put_group("night", {"name": "Night"}, ["lights", "time"])
    store.put_group("kids", {"name": "Children", "description": "upstairs"})
    compiled = store.load()
    assert compiled.commands["lights"].groups == ("kids", "night") and compiled.commands["time"].groups == ("night",)
    assert compiled.groups["kids"] == {"name": "Children", "description": "upstairs"}
    with pytest.raises(ConfigError, match="No command 'nope'"):
        store.put_group("kids", {"name": "Kids"}, ["nope"])
    # Deleting a group takes every command out of it.
    store.put_group("night", None)
    store.put_group("kids", None)
    compiled = store.load()
    assert compiled.groups == {} and all(not c.groups for c in compiled.commands.values())
    assert "groups" not in (tmp_path / "commands.yaml").read_text()


def test_store_duplicates_a_command(tmp_path):
    store = Store(tmp_path)
    store.save_text(
        "groups:\n  home: {name: Home}\n  kids: {name: Kids}\n"
        "commands:\n"
        "  lights:\n    description: turn on the lights\n    require: [[turn on], [lights]]\n"
        "    response: ok  # said\n    groups: [home, kids]\n"
        "  time:\n    description: tell the time\n    examples: [what time is it]\n    response: Noon.\n"
    )
    # In the original's groups, right after it.
    compiled = store.duplicate_command("lights", "lights_copy", "Lights, again")
    assert list(compiled.commands) == ["lights", "lights_copy", "time"]
    assert compiled.commands["lights_copy"].groups == ("home", "kids") and compiled.commands["lights_copy"].title == "Lights, again"
    # Only in one group, taking the original's place there.
    compiled = store.duplicate_command("lights", "kids_lights", group="kids", replace=True)
    assert compiled.commands["kids_lights"].groups == ("kids",) and compiled.commands["lights"].groups == ("home",)
    # In a group made for it; the original keeps its own.
    compiled = store.duplicate_command("time", "night_time", new_group=("night", "Night"))
    assert compiled.groups["night"]["name"] == "Night" and compiled.commands["night_time"].groups == ("night",)
    assert compiled.commands["time"].groups == ()
    assert "# said" in (tmp_path / "commands.yaml").read_text()
    with pytest.raises(ConfigError, match="already a command 'time'"):
        store.duplicate_command("lights", "time")
    with pytest.raises(ConfigError, match="No command group 'nope'"):
        store.duplicate_command("lights", "x", group="nope")
    with pytest.raises(FileNotFoundError):
        store.duplicate_command("nope", "x")


def test_verbosity_and_responses_by_level():
    from voice_call.commands import closest_text  # pylint: disable=import-outside-toplevel

    base = {"description": "x", "examples": ["x"], "action": {"action": "light.turn_on"}}
    command = parse_command("c", {**base, "verbosity": 1, "response": {"done": {"brief": "On.", "verbose": "All on."}}})
    assert command.behavior.verbosity == "beep" and command.behavior.texts("done") == {"brief": "On.", "verbose": "All on."}
    assert command.behavior.response_for("done", "specific") == "On."  # less said first
    assert parse_command("c", {**base, "response": "On."}).behavior.level == "specific"
    music = parse_command("m", {"description": "x", "examples": ["x"], "music": "pause"}).behavior
    assert music.level == "beep" and not music.question
    assert parse_command("q", {"description": "x", "examples": ["x"], "response": "Hi."}).behavior.question
    texts = {"brief": "b", "verbose": "v"}
    assert [closest_text(texts, level) for level in ("beep", "brief", "specific", "verbose")] == ["b", "b", "b", "v"]


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ({"verbosity": "loud"}, "one of beep, brief, specific, verbose"),
        ({"verbosity": 5}, "one of beep"),
        ({"response": {"done": {"loud": "x"}}}, "by level, one of brief, specific, verbose"),
        ({"response": {"failed": {"brief": "x"}}}, "by level only when done"),
    ],
)
def test_verbosity_errors(spec, message):
    with pytest.raises(ConfigError, match=message):
        parse_command("c", {"description": "x", "examples": ["x"], "action": {"action": "light.turn_on"}, **spec})


def test_combinations_are_intents_of_their_own():
    from voice_call.commands import command_of  # pylint: disable=import-outside-toplevel

    spec = {
        "description": "turn off the lights", "examples": ["lights off"], "require": [["off"], ["lights"]],
        "names": {"area": {"count": 1}, "floor": {"count": 1}}, "combinations": [[], ["area"], ["floor"]],
        "action": {"action": "light.turn_off"},
    }
    command = parse_command("lights_off", spec)
    assert list(command.intents) == ["lights_off", "lights_off:area", "lights_off:floor"]
    assert command.takes_nothing and command.takes_something and command.heard_slots == ("area", "floor")
    # The first has the examples and the description; each hears exactly its slots.
    assert command.intents["lights_off"]["examples"] == ["lights off"] and "names" not in command.intents["lights_off"]
    assert "examples" not in command.intents["lights_off:area"] and "description" not in command.intents["lights_off:area"]
    assert set(command.intents["lights_off:area"]["names"]) == {"area"}
    assert command.combination_for({"floor"}) == ("floor",) and command.combination_for({"area", "floor"}) is None
    assert command_of("lights_off:floor") == "lights_off" and command_of("lights_off") == "lights_off"
    parse_config({"intents": command.intents})
    # Without combinations: everything it hears, together.
    timer = parse_command("t", {"description": "x", "require": [["timer"]], "number": "duration", "timer": "start",
                                "wildcard": {"slot": "timer", "before": ["timer"]}})
    assert timer.combinations == (("duration", "timer"),) and timer.intents["t"]["wildcard"]["required"]
    # A combination without the number doesn't need its units said.
    cancel = parse_command("c", {"description": "x", "require": [["cancel"], ["timer"]], "number": "duration",
                                 "combinations": [[], ["duration"]], "timer": "cancel"})
    assert ["seconds", "second", "minutes", "minute", "hours", "hour"] not in cancel.intents["c"]["require"]
    assert "number" in cancel.intents["c:duration"] and "number" not in cancel.intents["c"]


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ({"names": {"area": {"count": 1, "optional": True}}}, "never optional"),
        ({"names": {"area": {"count": 1}}, "combinations": [["floor"]]}, "'floor' is not a slot it listens for"),
        ({"names": {"area": {"count": 1}}, "combinations": [[]]}, "'area' is in no combination"),
        ({"names": {"area": {"count": 1}}, "combinations": [["area"], ["area"]]}, "listed twice"),
        ({"wildcard": {"slot": "x", "after": ["do"], "required": True}}, "never optional"),
        ({"combinations": "area"}, "a list of slot lists"),
    ],
)
def test_combination_errors(spec, message):
    with pytest.raises(ConfigError, match=message):
        parse_command("c", {"description": "x", "require": [["do"]], "action": {"action": "light.turn_on"}, **spec})
