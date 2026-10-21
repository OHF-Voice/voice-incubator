"""The library: every command valid, ids unique, icons shipped -- in every
language (``language``) -- and English's versions."""

import json
import re
from pathlib import Path

import pytest

from adapt_ctc.config import ConfigError
from voice_call import languages
from voice_call.commands import parse_command
from voice_call.web import load_library

WEB = Path(__file__).resolve().parent.parent / "voice_call/web"


def shipped_icons() -> set[str]:
    text = (WEB / "icons.js").read_text()
    return set(json.loads(re.search(r"const ICONS = (\{.*?\});", text, re.S).group(1)))


@pytest.fixture(name="language", params=languages.codes())
def fixture_language(request):
    """Each language's library in turn."""
    yield languages.use(request.param)
    languages.use(languages.DEFAULT)


def test_every_library_command_is_valid(language):
    entries = load_library()
    assert entries and not [(e["id"], e["error"]) for e in entries if e.get("error")]


def test_ids_are_unique_across_files(language):
    # The library page marks a command added by its id; the Commands page
    # lists one by the file its id (or its family's, or a former one) is in.
    from voice_call.web import library_files  # pylint: disable=import-outside-toplevel

    ids = [e["id"] for e in load_library()]
    assert len(ids) == len(set(ids))
    every = []
    for _path, data in library_files():
        for family_id, family in data["commands"].items():
            every += [family_id, *(family.get("formerly") or [])]
            every += [f"{family_id}_{v}" for v in family.get("variants") or {}]
    assert len(every) == len(set(every))


def test_a_command_comes_in_versions():
    """Here (nothing named), a device by name, an area or floor by name: each
    its own command, the family's keys with its own on top."""
    entries = {e["id"]: e for e in load_library()}
    play = [e for e in entries.values() if e["family"] == "play_music"]
    assert [(e["variant"], e["title"]) for e in play] == [
        ("here", "Play music on this satellite"),
        ("device", "Play music on a different device"),
        ("place", "Play music in an area or floor"),
    ]
    assert all(e["family_title"] == "Play music" and e["spec"]["music"] == "play" for e in play)
    assert "names" not in entries["play_music_here"]["spec"]
    assert entries["play_music_device"]["spec"]["combinations"] == [["entity", "query"]]
    assert entries["play_music_place"]["spec"]["combinations"] == [["area", "query"], ["floor", "query"]]
    # A version's own keys replace the family's: a device's intent and
    # response, with none of the by-type list.
    device = entries["open_covers_device"]["spec"]
    assert device["intent"] == "HassTurnOn" and "lists" not in device
    assert entries["open_covers_here"]["spec"]["intent"]["anywhere"] is True
    # By type, from the language's own list (Pack.lists).
    assert entries["open_covers_place"]["spec"]["lists"]["device_class"] == {"list": "cover_classes"}
    assert "shutter" in languages.current().lists["cover_classes"]
    # Example sentences name nothing, so they are the version that names
    # nothing's: never one that needs a device, area or floor.
    for entry in entries.values():
        if entry["variant"] in ("device", "place"):
            assert not entry["spec"].get("examples"), entry["id"]
        assert not {"needs", "variants", "formerly"} & set(entry["spec"]), entry["id"]
    # One without versions is its own.
    assert entries["what_time"]["variant"] is None and entries["what_time"]["family"] == "what_time"


def test_a_variant_must_be_one_of_the_three():
    from voice_call.web import library_versions  # pylint: disable=import-outside-toplevel

    with pytest.raises(ConfigError, match="no variant room"):
        library_versions("x", {"examples": ["x"], "variants": {"room": {}}})
    # A null one removes the family's key.
    [(command_id, variant, spec)] = library_versions("x", {"examples": ["x"], "response": "ok", "variants": {"place": {"examples": None}}})
    assert (command_id, variant, spec) == ("x_place", "place", {"response": "ok"})


def test_every_icon_is_shipped(language):
    icons = shipped_icons()
    used = {e["spec"]["icon"] for e in load_library() if e["spec"].get("icon")}
    used |= {e["group_icon"] for e in load_library() if e["group_icon"]}
    used |= {e["family_icon"] for e in load_library() if e["family_icon"]}
    # Every icon the page names, the kinds' defaults among them.
    page = set(re.findall(r'"(mdi:[a-z0-9-]+)"', (WEB / "app.js").read_text()))
    assert used and not (used | page) - icons
    assert all(e["spec"].get("icon") for e in load_library())


def test_commands_are_listed_by_library_section():
    from voice_call.web import library_of, library_sections  # pylint: disable=import-outside-toplevel

    sections, _ids = library_sections()
    assert [s["file"] for s in sections] == ["basic", "home", "music", "timers"] and sections[0]["title"] == "Basics"
    # Added from the library (library: music), else by id -- a numbered or
    # copied one too; made from scratch: none (Custom).
    assert library_of("anything", {"library": "music"}) == "music"
    assert [library_of(i, {}) for i in ("lights_on", "lights_on_2", "lights_on_copy", "pause_timer_copy_3", "my_lights")] == [
        "basic", "basic", "basic", "timers", None]
    # A version, its family, and what a family was before it had versions.
    assert [library_of(i, {}) for i in ("play_music_device_2", "turn_on_place", "turn_on", "device_off", "open_cover")] == [
        "music", "basic", "basic", "basic", "home"]


def test_icons_are_material_design_names():
    base = {"description": "x", "examples": ["x"], "response": "ok"}
    assert parse_command("x", {**base, "icon": "mdi:lightbulb"})
    with pytest.raises(ConfigError, match="Material Design Icon"):
        parse_command("x", {**base, "icon": "lightbulb"})


def test_every_response_template_renders(language):
    """Each library response, at each level, renders with Home Assistant's
    template functions (stubbed): a typo shows here, not in a call."""
    import jinja2  # pylint: disable=import-outside-toplevel

    from voice_call.commands import SPOKEN, parse_command  # pylint: disable=import-outside-toplevel

    class States:
        """states("light.x"), and states.light: Home Assistant's both."""

        light = ()
        lock = ()

        def __call__(self, entity_id):
            return "on"

    env = jinja2.Environment(undefined=jinja2.ChainableUndefined)
    env.globals.update(
        now=__import__("datetime").datetime.now, states=States(), state_attr=lambda e, a: 21 if a == "temperature" else "Ceiling",
        is_state=lambda e, s: False, area_entities=lambda a: ["light.ceiling", "light.lamp", "switch.kettle"],
        area_name=lambda a: "Kitchen", floor_name=lambda f: "Upstairs", floor_areas=lambda f: ["kitchen"],
        area_id=lambda e: "kitchen",
    )
    env.filters.setdefault("round", lambda value, n=0: round(float(value), n))
    env.tests["match"] = lambda value, pattern: __import__("re").match(pattern, str(value)) is not None
    variables = {
        "slots": {"entity": ["light.ceiling"], "area": ["kitchen"], "item": "milk", "reminder": "call Sam",
                  "percentage": 30, "temperature": 21, "color": "red", "device_class": "blind", "asked": "open"},
        "satellite": {"area_id": "kitchen", "area_name": "Kitchen"},
        "place": {"here": False, "said": "in the Kitchen", "areas": ["kitchen"]},
        "command": {"id": "c", "description": "do it", "text": "do it"},
        "result": {"outcome": "done", "speech": "", "count": 2, "names": ["Ceiling", "Lamp"]},
        "exposed": ["light.ceiling", "light.lamp"],
    }
    lights = None
    for entry in load_library():
        spec = {k: v for k, v in entry["spec"].items() if k != "needs"}
        behavior = parse_command(entry["id"], spec).behavior
        for level in SPOKEN:
            template = behavior.response_for("done", level)
            if template:
                said = env.from_string(template).render(**variables)
                if entry["id"] == "turn_on_here" and level == "verbose":
                    lights = said
    # What the intent acted on, by name.
    assert lights is not None and "Ceiling" in lights and "Lamp" in lights, lights


ROUTES = [
    ("play jazz", "play_music_here", {"query": "jazz"}),
    ("play she paints me blue on the mark 2", "play_music_device", {"entity": ["media_player.mark_ii"], "query": "she paints me blue"}),
    ("play jazz in the kitchen", "play_music_place", {"area": ["kitchen"], "query": "jazz"}),
    ("play jazz upstairs", "play_music_place", {"floor": ["upstairs"], "query": "jazz"}),
    ("pause the music", "pause_music_here", {}),
    ("pause the music on the mark 2", "pause_music_device", {"entity": ["media_player.mark_ii"]}),
    ("pause the music in the kitchen", "pause_music_place", {"area": ["kitchen"]}),
    ("set the volume on the mark 2 to 30 percent", "set_volume_device", {"entity": ["media_player.mark_ii"], "percentage": 30}),
    ("what's playing in the kitchen", "whats_playing_place", {"area": ["kitchen"]}),
    ("move the music to the mark 2", "move_music_device", {"entity": ["media_player.mark_ii"]}),
    ("turn on the lights", "turn_on_here", {}),
    ("turn on the lights in the kitchen", "turn_on_place", {"area": ["kitchen"]}),
    ("turn on the desk lamp", "turn_on_device", {"entity": ["light.desk_lamp"]}),
    ("is the desk lamp on", "any_lights_on_device", {"entity": ["light.desk_lamp"], "asked": "on"}),
    ("are any lights on upstairs", "any_lights_on_place", {"floor": ["upstairs"]}),
    ("turn the desk lamp blue", "light_color_device", {"entity": ["light.desk_lamp"], "color": "blue"}),
    ("set the ecobee to 70 degrees", "set_temperature_device", {"entity": ["climate.ecobee"], "temperature": 70}),
    ("open the blinds", "open_covers_here", {"device_class": "blind"}),
    ("open the skylight", "open_covers_device", {"entity": ["cover.skylight"]}),
    ("close the shades upstairs", "close_covers_place", {"floor": ["upstairs"], "device_class": "shade"}),
    ("is the skylight open", "are_covers_open_device", {"entity": ["cover.skylight"], "asked": "open"}),
]


def test_each_version_takes_its_own_sentences():
    """Every version added at once: nothing named goes to the one for the
    satellite, a device named to the device's, an area or floor to the
    place's."""
    from voice_call.engine import Engine, Satellite  # pylint: disable=import-outside-toplevel
    from voice_call.store import compile_store  # pylint: disable=import-outside-toplevel

    ctc = Path(__file__).resolve().parent.parent / "models/speech-to-phrase/stt_en_parakeet_tdt_ctc_110m-int8"
    if not ctc.is_dir():
        pytest.skip("no CTC model")
    entries = [e for e in load_library() if e["file"] != "timers"]
    store = {
        "names": {
            "entities": {
                "media_player.mark_ii": {"name": "Mark 2"},
                "light.desk_lamp": {"name": "Desk Lamp"},
                "climate.ecobee": {"name": "Ecobee"},
                "cover.skylight": {"name": "Skylight"},
                "lock.front_door": {"name": "Front Door"},
            },
            "areas": {"kitchen": {"name": "Kitchen"}, "office": {"name": "Office"}},
            "floors": {"upstairs": {"name": "Upstairs"}},
        },
        "lists": {name: values for e in entries for name, values in e["lists"].items()},
        "commands": {e["id"]: e["spec"] for e in entries},
    }
    engine = Engine(ctc, None, None, threads=2)
    engine.build(compile_store(store))
    office = Satellite("s", "Office", "office", "Office")
    heard = []
    for text, _command, _slots in ROUTES:
        decision = engine.recognize_text(text, office)
        heard.append((text, decision.command.id if decision.command else decision.message, decision.slots))
    assert heard == ROUTES
