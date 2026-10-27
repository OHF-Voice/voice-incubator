"""Commands: parsing, presets, slots, where they run (every satellite or
some), and the store."""

import json

import pytest

from adapt_ctc.config import ConfigError, parse_config
from voice_call.commands import adapt_config, match_slots, parse_command, parse_commands
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
    # A command runs on every satellite or on some; what it does elsewhere is
    # a copy of it, on those.
    with pytest.raises(ConfigError, match=r"commands\.lights\.scope: no longer used: .*satellites list"):
        command(scope={"areas": ["kitchen"]})
    with pytest.raises(ConfigError, match=r"commands\.lights\.overrides: no longer used: .*copy of the command"):
        command(overrides=[{"scope": {"areas": ["office"]}, "scene": "scene.office"}])


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ({"description": "", "examples": ["a"]}, "description"),
        ({"description": "x"}, "needs examples or keywords"),
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


def test_the_scene_or_script_heard():
    """By its name: the command listens for a scene (or a script) and runs
    the one heard."""
    names = {"entity": {"count": 1, "domains": ["scene"]}, "area": {"count": 1}}
    cozy = parse_command("s", {"description": "activate a scene", "names": names, "combinations": [["entity"], ["entity", "area"]],
                               "optional": [["make it"]], "scene": "heard"})
    assert cozy.behavior.sequence() == [{"action": "scene.turn_on", "target": {"entity_id": "{{ slots.entity }}"}}]
    night = parse_command("n", {"description": "run a script", "names": {"entity": {"count": 1, "domains": ["script"]}},
                                "script": "heard"})
    assert night.behavior.sequence()[0]["action"] == "{{ slots.entity[0] }}"
    assert night.behavior.sequence()[0]["data"]["satellite"] == "{{ satellite }}"
    # Heard needs something to hear: a name of that kind.
    with pytest.raises(ConfigError, match="needs it to listen for a scene name"):
        parse_command("x", {"description": "x", "examples": ["x"], "scene": "heard"})
    with pytest.raises(ConfigError, match="needs it to listen for a script name"):
        parse_command("x", {"description": "x", "names": names, "script": "heard"})
    with pytest.raises(ConfigError, match="a scene entity id"):
        parse_command("x", {"description": "x", "examples": ["x"], "scene": "movie_time"})


def test_a_missing_scene_is_said_as_one():
    from voice_call.engine import _which  # pylint: disable=import-outside-toplevel
    from voice_call.languages.en import speech  # pylint: disable=import-outside-toplevel

    scene = parse_command("s", {"description": "activate a scene", "names": {"entity": {"count": 1, "domains": ["scene"]}},
                                "optional": [["activate"]], "scene": "heard"})
    device = parse_command("d", {"description": "turn on a device", "names": {"entity": {"count": 1}}, "require": [["turn on"]]})
    first = {"kind": "name", "type": "entity"}
    assert speech.missing(_which(first, scene)) == "but I didn't hear which scene"
    assert speech.missing(_which(first, device)) == "but I didn't hear which device"


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
        ({"description": "x", "examples": ["a"], "response": {"failed": "Oops."}}, "failed can't happen with nothing to do; use done"),
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


def saved(tmp_path, data):
    """A store whose commands file already holds ``data``."""
    (tmp_path / "commands.json").write_text(json.dumps(data))
    return Store(tmp_path)


def test_store_edits(tmp_path):
    store = saved(tmp_path, {"commands": {"lights": {"description": "turn on the lights", "require": [["turn on"], ["lights"]], "response": "ok"}}})
    store.put_command("time", {"description": "tell the time", "examples": ["what time is it"], "response": "Noon."})
    store.put_name("areas", "kitchen", {"name": "Kitchen", "aliases": ["Cookhouse"]})
    on_disk = json.loads((tmp_path / "commands.json").read_text())
    assert on_disk["commands"]["time"]["examples"] == ["what time is it"]
    assert on_disk == store.document()
    compiled = store.load()
    assert set(compiled.commands) == {"lights", "time"}
    assert compiled.data["names"]["areas"]["kitchen"]["aliases"] == ["Cookhouse"]
    # A new store reads what this one saved.
    assert set(Store(tmp_path).load().commands) == {"lights", "time"}
    # Renaming keeps its place; deleting removes it.
    store.put_command("lights_on", compiled.data["commands"]["lights"], old_id="lights")
    assert list(store.load().commands) == ["lights_on", "time"]
    store.delete_command("time")
    assert list(store.load().commands) == ["lights_on"]
    # An invalid edit is refused and the file is untouched.
    before = (tmp_path / "commands.json").read_text()
    with pytest.raises(ConfigError):
        store.put_command("broken", {"description": "x"})
    assert (tmp_path / "commands.json").read_text() == before
    assert list(store.load().commands) == ["lights_on"]
    # What it hands out is a copy.
    store.document()["commands"].clear()
    assert list(store.load().commands) == ["lights_on"]


def test_yaml_from_before_is_copied_once(tmp_path):
    yaml_text = (
        "# my commands\nnames: {}\nlists: {}\ncommands:\n  off:\n    description: turn off\n"
        "    require: [[turn off]]\n    exclude: [on]\n    response: ok\n"
    )
    (tmp_path / "commands.yaml").write_text(yaml_text)
    (tmp_path / "settings.yaml").write_text("keep_audio: true\nend_of_speech: {silence_seconds: 0.5}\n")
    store = Store(tmp_path)
    compiled = store.load()
    assert compiled.commands["off"].intent["exclude"] == ["on"]  # YAML 1.2: on and off stay words
    store.load_extras()
    assert store.settings["keep_audio"] is True and store.settings["end_of_speech"] == {"silence_seconds": 0.5}
    assert json.loads((tmp_path / "commands.json").read_text())["commands"]["off"]["exclude"] == ["on"]
    assert json.loads((tmp_path / "settings.json").read_text())["keep_audio"] is True
    # The YAML stays as it was, and is not read again.
    store.put_command("time", {"description": "tell the time", "examples": ["what time is it"], "response": "Noon."})
    assert (tmp_path / "commands.yaml").read_text() == yaml_text
    (tmp_path / "commands.yaml").write_text("commands: {}\n")
    assert set(Store(tmp_path).load().commands) == {"off", "time"}
    # One that doesn't parse says which.
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "commands.yaml").write_text("commands: [\n")
    with pytest.raises(ConfigError, match="commands.yaml"):
        Store(tmp_path / "other").load()


def test_every_satellite_or_some():
    everywhere = command()
    picked = command(satellites=["sat1", "sat3", "sat1"])
    elsewhere = command(satellites=["sat2"])
    assert everywhere.satellites is None and picked.satellites == ("sat1", "sat3")
    kitchen = Satellite("sat1", "Kitchen", "kitchen")
    assert kitchen.available(everywhere) is not None and kitchen.available(picked) is not None
    assert kitchen.available(elsewhere) is None and kitchen.why_unavailable(elsewhere) == "not on this satellite"
    # Those picked for it go first.
    assert kitchen.first([elsewhere, everywhere, picked]) == [picked, everywhere]
    # On none: nowhere (typed on the web page, with no satellite, too).
    assert Satellite("sat1", "Kids").available(command(satellites=[])) is None
    assert Satellite("", "web").available(picked) is None
    with pytest.raises(ConfigError, match="satellites' keys"):
        command(satellites="sat1")
    with pytest.raises(ConfigError, match="satellite: no longer used: satellites"):
        command(satellite="sat1")


def test_an_item_on_a_list():
    remove = command(on_list={"item": "todo.shopping_list"})
    assert remove.behavior.on_list == {"item": "todo.shopping_list"}
    said = command(action=None, on_list={"item": "todo.shopping_list"}, response={"done": "ok", "not_on_list": "No."})
    assert said.behavior.outcomes() == ("done", "failed", "not_on_list")
    with pytest.raises(ConfigError, match="on_list.item: a to-do list"):
        command(on_list={"item": "light.kitchen"})


def test_the_homes_own():
    weather = command(action=None, home="weather", response={"done": "ok", "not_set": "Set it up."})
    assert weather.behavior.home == "weather"
    assert weather.behavior.outcomes() == ("done", "failed", "not_set")
    with pytest.raises(ConfigError, match="home: one of weather"):
        command(home="calendar")


def test_fallback_steps_are_chosen():
    assert command().fallback == ()
    assert command(fallback=["llm", "meaning"]).fallback == ("meaning", "llm")
    assert command(fallback="llm").fallback == ("llm",)
    with pytest.raises(ConfigError, match="'guess' is not one of meaning, llm"):
        command(fallback=["guess"])


def test_choosing_commands_for_the_fallback(tmp_path):
    store = saved(tmp_path, {"commands": {
        "lights": {"description": "turn on the lights", "require": [["lights"]], "response": "ok"},
        "fan": {"description": "turn on the fan", "require": [["fan"]], "response": "ok", "fallback": ["meaning"]},
    }})
    compiled = store.put_fallback({"llm": ["lights", "fan"]})
    assert compiled.commands["lights"].fallback == ("llm",)
    assert compiled.commands["fan"].fallback == ("meaning", "llm")  # meaning left as it was
    compiled = store.put_fallback({"meaning": [], "llm": ["fan"]})
    assert compiled.commands["lights"].fallback == () and compiled.commands["fan"].fallback == ("llm",)
    assert "fallback" not in store.document()["commands"]["lights"]  # not left empty
    with pytest.raises(ConfigError, match="No command 'nope'"):
        store.put_fallback({"llm": ["nope"]})


def test_command_groups_are_gone():
    with pytest.raises(ConfigError, match="command groups are gone"):
        compile_store({"commands": {"lights": {**LIGHTS, "groups": ["kids"]}}})
    with pytest.raises(ConfigError, match="unknown top-level key"):
        compile_store({"groups": {"kids": {"name": "Kids"}}, "commands": {"lights": LIGHTS}})


def test_store_duplicates_a_command(tmp_path):
    store = saved(tmp_path, {"commands": {
        "lights": {"description": "turn on the lights", "require": [["turn on"], ["lights"]], "response": "ok"},
        "time": {"description": "tell the time", "examples": ["what time is it"], "response": "Noon."},
    }})
    # The Home's, right after it.
    compiled = store.duplicate_command("lights", "lights_copy", "Lights, again")
    assert list(compiled.commands) == ["lights", "lights_copy", "time"]
    assert compiled.commands["lights_copy"].title == "Lights, again"
    # Where it runs comes along.
    store.put_command("kids_time", {**store.document()["commands"]["time"], "satellites": ["sat1"]})
    compiled = store.duplicate_command("kids_time", "time_again")
    assert compiled.commands["time_again"].satellites == ("sat1",)
    assert list(store.document()["commands"]) == ["lights", "lights_copy", "time", "kids_time", "time_again"]
    with pytest.raises(ConfigError, match="already a command 'time'"):
        store.duplicate_command("lights", "time")
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
    assert [closest_text(texts, level) for level in ("silent", "beep", "brief", "specific", "verbose")] == ["b", "b", "b", "b", "v"]
    # Silent is 0: the numbers 1 to 4 keep their levels.
    assert parse_command("c", {**base, "verbosity": 0}).behavior.verbosity == "silent"


def test_a_command_may_do_nothing():
    # "Never mind": nothing to do, and by default nothing said, not even the
    # acknowledgement sound. With a response it only responds, and says it.
    nothing = parse_command("never_mind", {"description": "cancel what was asked", "examples": ["never mind"]}).behavior
    assert nothing.does == "nothing" and nothing.level == "silent" and not nothing.question
    assert nothing.sequence() == [] and nothing.outcomes() == ("done",)
    said = parse_command("never_mind", {"description": "x", "examples": ["never mind"], "response": "OK."}).behavior
    assert said.does == "respond" and said.question


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ({"verbosity": "loud"}, "one of silent, beep, brief, specific, verbose"),
        ({"verbosity": 5}, "one of silent"),
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
