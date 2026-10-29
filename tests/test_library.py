"""The library: every command valid, ids unique, icons shipped -- in every
language (``language``) -- and English's versions."""

import json
import re
from pathlib import Path

import pytest

from adapt_ctc.config import ConfigError
from voice_labs import languages
from voice_labs.commands import parse_command
from voice_labs.web import load_library

WEB = Path(__file__).resolve().parent.parent / "voice_labs/web"


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
    # What to set after adding it is text: "a: b" in it, unquoted, is a mapping.
    assert not [(e["id"], n) for e in entries for n in e["needs"] if not isinstance(n, str)]


def test_the_homes_own(language):
    """The weather, thermostat, shopping list and to-do list commands use the
    home's (Settings) where they aren't given their own."""
    entries = {e["id"]: e for e in load_library()}
    homes = {"weather": "weather", "weather_day": "weather", "weather_week": "weather", "will_it_rain": "weather",
             "thermostat": "climate", "set_thermostat": "climate", "thermostat_fan": "climate",
             "set_thermostat_fan": "climate", "thermostat_mode": "climate", "set_thermostat_mode": "climate",
             "shopping_list_add": "shopping_list", "shopping_list_remove": "shopping_list",
             "shopping_list_check": "shopping_list", "shopping_list_items": "shopping_list", "todo_add": "todo_list",
             "todo_add_mine": "todo_list"}
    found = {i: entries[i]["spec"] for i in homes if i in entries}
    assert {"weather", "shopping_list_add", "todo_add"} <= set(found)
    assert {i: s.get("home") for i, s in found.items()} == {i: homes[i] for i in found}
    assert not [i for i, s in found.items() if (s.get("slots") or {}).get("entity")]


def test_ids_are_unique_across_files(language):
    # The library page marks a command added by its id; the Commands page
    # lists one by the file its id (or its family's, or a former one) is in.
    from voice_labs.web import library_files  # pylint: disable=import-outside-toplevel

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
    from voice_labs.web import library_versions  # pylint: disable=import-outside-toplevel

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
    from voice_labs.web import library_of, library_sections  # pylint: disable=import-outside-toplevel

    sections, _ids = library_sections()
    # By title, as the Home page and the library list them.
    assert [s["title"] for s in sections] == [
        "Climate", "Covers and locks", "Date and time", "Lights and switches", "Lists", "Music",
        "Reminders and calendar", "Satellites", "Scenes and scripts", "Sensors", "Timers and alarms", "Weather"]
    assert [s["file"] for s in sections] == [
        "climate", "home", "time", "lights", "todo", "music", "reminders", "satellites", "scenes", "sensors", "timers", "weather"]
    # Added from the library (library: music), else by id -- a numbered or
    # copied one too; made from scratch: none (Custom).
    assert library_of("anything", {"library": "music"}) == "music"
    assert [library_of(i, {}) for i in ("lights_on", "lights_on_2", "lights_on_copy", "pause_timer_copy_3", "my_lights")] == [
        "lights", "lights", "lights", "timers", None]
    # A version, its family, and what a family was before it had versions.
    assert [library_of(i, {}) for i in ("play_music_device_2", "turn_on_place", "turn_on", "device_off", "open_cover")] == [
        "music", "lights", "lights", "lights", "home"]



def test_every_language_files_its_commands_as_english_does():
    """A command is in the same section in every language, and a section has
    the same title and icon: the Home page and the library group them alike."""
    from voice_labs.library import library_files  # pylint: disable=import-outside-toplevel

    def sections(code):
        files = library_files(code)
        return ({path.stem: (data["title"], data["icon"]) for path, data in files},
                {command_id: path.stem for path, data in files for command_id in data["commands"]})

    titles, files = sections("en")
    for code in languages.codes():
        theirs, their_files = sections(code)
        assert {stem: titles[stem] for stem in theirs} == theirs, code
        assert {c: files[c] for c in their_files} == their_files, code


def test_a_name_in_several_rooms():
    """A device's names in its area are heard from a satellite there -- ahead
    of another's whole house name -- and elsewhere with its area said
    (unless not: with_area); its whole house names from every satellite. A
    name heard nowhere from here, or one two devices have there, runs
    nothing, and the hint says why; one only part of its area has none."""
    from voice_labs.engine import Engine, Satellite  # pylint: disable=import-outside-toplevel
    from voice_labs.store import compile_store  # pylint: disable=import-outside-toplevel

    ctc = Path(__file__).resolve().parent.parent / "models/speech-to-phrase/stt_en_parakeet_tdt_ctc_110m-int8"
    if not ctc.is_dir():
        pytest.skip("no CTC model")
    entries = [e for e in load_library() if e["file"] == "lights"]
    store = {
        "names": {
            "entities": {
                "light.office_lamp": {"name": "Lamp", "house": ["Office Lamp"]}, "light.bedroom_lamp": {"name": "Lamp", "house": []},
                "light.office_overhead": {"name": "Overhead Light", "house": [], "with_area": False},
                "light.porch": {"name": "Porch Light"}, "light.desk": {"name": "Desk Light", "implicit": False},
                "light.reading": {"name": "Reading Light"}, "light.bedroom_reading": {"name": "Reading Light"},
                "light.night": {},
            },
            "areas": {"office": "Office", "bedroom": "Bedroom", "kitchen": "Kitchen"},
        },
        "lists": {name: values for e in entries for name, values in e["lists"].items()},
        "commands": {e["id"]: e["spec"] for e in entries},
    }
    engine = Engine(ctc, None, None, threads=2)
    engine.build(compile_store(store))
    engine.entity_area = {"light.office_lamp": "office", "light.bedroom_lamp": "bedroom", "light.office_overhead": "office",
                          "light.porch": None, "light.desk": "office", "light.reading": "office",
                          "light.bedroom_reading": "bedroom", "light.night": "office"}.get

    def heard(text, area):
        decision = engine.recognize_text(text, Satellite("s", area.title(), area, area.title()))
        return decision.slots.get("entity") if decision.command else decision.message

    # Its area's name there; elsewhere, with the area said, or its whole house name.
    assert heard("turn on the lamp", "office") == ["light.office_lamp"]
    assert heard("turn on the lamp", "bedroom") == ["light.bedroom_lamp"]
    assert heard("turn on the lamp in the bedroom", "kitchen") == ["light.bedroom_lamp"]
    assert heard("turn on the office lamp", "kitchen") == ["light.office_lamp"]
    assert heard("turn on the lamp", "kitchen") == (
        "It looks like you're trying to turn on a device, but there's no Lamp here, only in the Office and in the Bedroom.")
    assert heard("turn on the lamp in the kitchen", "office").endswith("but there's no Lamp in the Kitchen.")
    # Not heard elsewhere, even with its area said.
    assert heard("turn on the overhead light", "office") == ["light.office_overhead"]
    assert heard("turn on the overhead light", "kitchen").endswith("but there's no Overhead Light here, only in the Office.")
    assert heard("turn on the overhead light in the office", "kitchen").endswith("but there's no Overhead Light here, only in the Office.")
    # The same names in the whole house (left out): from anywhere.
    assert heard("turn on the porch light", "kitchen") == ["light.porch"]
    assert heard("turn on the desk light", "kitchen") == ["light.desk"]
    # Two devices' whole house name: the area's own goes first there; elsewhere, neither.
    assert heard("turn on the reading light", "office") == ["light.reading"]
    assert heard("turn on the reading light", "kitchen").endswith("but more than one device is called Reading Light.")
    # Only part of its area: no name to hear.
    assert heard("turn on the night light", "office") != ["light.night"]

def test_icons_are_material_design_names():
    base = {"description": "x", "examples": ["x"], "response": "ok"}
    assert parse_command("x", {**base, "icon": "mdi:lightbulb"})
    with pytest.raises(ConfigError, match="Material Design Icon"):
        parse_command("x", {**base, "icon": "lightbulb"})


def test_every_response_template_renders(language):
    """Each library response, at each level, renders with Home Assistant's
    template functions (stubbed): a typo shows here, not in a call."""
    import jinja2  # pylint: disable=import-outside-toplevel

    from voice_labs.commands import SPOKEN, parse_command  # pylint: disable=import-outside-toplevel

    class States:
        """states("light.x"), and states.light: Home Assistant's both."""

        light = ()
        lock = ()

        def __call__(self, entity_id):
            return "on"

    env = jinja2.Environment(undefined=jinja2.ChainableUndefined)
    import datetime as dt  # pylint: disable=import-outside-toplevel

    env.globals.update(
        now=dt.datetime.now, timedelta=dt.timedelta, as_datetime=dt.datetime.fromisoformat,
        states=States(),
        state_attr=lambda e, a: 21 if a.endswith("temperature") or a.startswith("target_temp") else "auto" if a == "fan_mode" else "Ceiling",
        is_state=lambda e, s: False, area_entities=lambda a: ["light.ceiling", "light.lamp", "switch.kettle"],
        area_name=lambda a: "Kitchen", floor_name=lambda f: "Upstairs", floor_areas=lambda f: ["kitchen"],
        area_id=lambda e: "kitchen",
    )
    env.filters.setdefault("round", lambda value, n=0: round(float(value), n))
    env.filters["as_local"] = lambda value: value.astimezone()
    env.tests["match"] = lambda value, pattern: __import__("re").match(pattern, str(value)) is not None
    variables = {
        "slots": {"entity": ["light.ceiling"], "area": ["kitchen"], "item": "milk", "reminder": "call Sam",
                  "percentage": 30, "temperature": 21, "color": "red", "device_class": "blind", "asked": "open",
                  "day": "saturday", "what": "rain"},
        "satellite": {"area_id": "kitchen", "area_name": "Kitchen"},
        "place": {"here": False, "said": "in the Kitchen", "areas": ["kitchen"]},
        "command": {"id": "c", "description": "do it", "text": "do it"},
        "result": {"outcome": "done", "speech": "", "count": 2, "names": ["Ceiling", "Lamp"], "days": [
            {"datetime": (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=n)).isoformat(), "condition": "rainy",
             "temperature": 15, "templow": 8, "precipitation_probability": 60} for n in range(7)]},
        "exposed": ["light.ceiling", "light.lamp"],
        "names": {"light.ceiling": "Ceiling Light"},
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
    ("pause the music", "pause_music_here", {"kind": "music"}),
    ("pause the tv", "pause_music_here", {"kind": "tv"}),
    ("pause", "pause_music_here", {}),
    ("pause the music on the mark 2", "pause_music_device", {"entity": ["media_player.mark_ii"]}),
    ("pause the music in the kitchen", "pause_music_place", {"area": ["kitchen"], "kind": "music"}),
    ("turn the tv down in the kitchen", "volume_down_place", {"area": ["kitchen"], "kind": "tv"}),
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
    from voice_labs.engine import Engine, Satellite  # pylint: disable=import-outside-toplevel
    from voice_labs.store import compile_store  # pylint: disable=import-outside-toplevel

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
