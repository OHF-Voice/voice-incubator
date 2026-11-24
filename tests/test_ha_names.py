"""HA-following names use the ordinary store and preserve manual ownership."""

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import make_mocked_request

from adapt_ctc.config import ConfigError
from voice_incubator import actions, ha_names, languages, library
from voice_incubator.hass import Area, Catalog, Entity, Floor
from voice_incubator.server import VoiceIncubatorApp
from voice_incubator.store import Device, Store
from voice_incubator.web import Api


@pytest.fixture
def store(tmp_path):
    languages.use("en")
    store = Store(tmp_path)
    store.catalog = Catalog(
        areas={"office": Area("office", "Office", ["Study"], "ground"), "kitchen": Area("kitchen", "Kitchen")},
        floors={"ground": Floor("ground", "Ground Floor")},
        entities={
            "light.desk": Entity("light.desk", "Office Desk Lamp", ["Office Task Light"], "office"),
            "light.private": Entity("light.private", "Private Lamp", area_id="office"),
            "weather.home": Entity("weather.home", "Forecast"),
        },
        exposed={"light.desk", "weather.home"},
    )
    return store


def enable(store):
    return store.update(lambda d: d.update(ha_names={"enabled": True}))


def test_sync_is_opt_in_and_normal_saves_keep_it_derived(store):
    assert store.load().devices == {}
    compiled = enable(store)
    assert compiled.devices == {"light.desk": Device(None, ("Desk Lamp", "Task Light"), ("Office Desk Lamp", "Office Task Light"))}
    assert compiled.data["names"]["areas"]["office"] == {"name": "Office", "aliases": ["Study"]}
    assert "ground" in compiled.data["names"]["floors"]
    assert store.document()["names"] == {}
    saved = store.put_command("hello", {"description": "say hello", "examples": ["hello"], "response": "Hello"})
    assert saved.devices == compiled.devices
    assert Store(store.data_dir, catalog=store.catalog).load().devices == compiled.devices


@pytest.mark.asyncio
async def test_auto_add_uses_synced_light_capabilities_even_when_only_state_changes(store):
    catalog = store.catalog
    desk = catalog.entities["light.desk"]
    private = replace(catalog.entities["light.private"], attributes={"supported_color_modes": ["rgb"]})
    catalog.entities["light.private"] = private
    applied = []

    async def apply(compiled):
        applied.append(set(compiled.commands))

    app = SimpleNamespace(store=store, hass=SimpleNamespace(catalog=catalog), engine=object(), apply=apply)
    original = set(enable(store).commands)
    assert "turn_on_device" not in original
    store.update(lambda d: d["ha_names"].update(auto_add_commands=True))
    on_off = {"turn_on_device", "turn_off_device", "any_lights_on_device"}
    assert set(store.load().commands) == original | on_off  # no known capability on the exposed light
    assert store.document()["commands"]["turn_on_device"] == {"from": "lights/turn_on_device"}

    brightness = {"set_brightness_device", "brighter_device", "dimmer_device"}
    for mode, extra in [("brightness", brightness), ("rgb", brightness | {"light_color_device", "light_temperature_device"})]:
        app.hass.catalog = replace(catalog, entities={**catalog.entities, "light.desk": replace(desk, attributes={"supported_color_modes": [mode]})})
        await VoiceIncubatorApp._follow_ha_names(app)
        assert applied[-1] == original | on_off | extra

    saved = store.document()["commands"]
    store.update(lambda d: d["ha_names"].update(enabled=False))
    assert store.document()["commands"] == saved
    assert not store.load().devices


def test_auto_add_preserves_customization_disabling_and_deletion(store):
    catalog = store.catalog
    catalog.entities["cover.shade"] = Entity("cover.shade", "Shade", attributes={"supported_features": 7})
    catalog.exposed = {"cover.shade"}
    disabled = {"from": "home/open_covers_device", "disabled": True, "title": "My open command", "area": "office"}
    store.put_command("open_my_way", disabled)
    store.put_command("my_close", {"from": "home/close_covers_device"})
    custom = {**library.base("home/close_covers_device"), "title": "My close command"}
    store.put_command("local_close", custom, old_id="my_close")
    store.put_command("position", {"from": "home/cover_position_device"})
    store.delete_command("position")
    original = store.document()["commands"]

    store.update(lambda d: d.update(ha_names={**d.get("ha_names", {}), "enabled": True, "auto_add_commands": True}))
    assert store.document()["commands"] == {**original, "are_covers_open_device": {"from": "home/are_covers_open_device"}}
    store.delete_command("are_covers_open_device")
    restarted = Store(store.data_dir, catalog=catalog)
    restarted.update(lambda d: d["ha_names"].update(auto_add_commands=False))
    restarted.update(lambda d: d["ha_names"].update(auto_add_commands=True))
    assert restarted.document()["commands"] == original


def test_auto_add_matches_named_device_domains_and_capabilities(store):
    catalog = store.catalog
    catalog.entities.update({
        "cover.shade": Entity("cover.shade", "Shade", attributes={"supported_features": 3}),
        "cover.private": Entity("cover.private", "Private Shade", attributes={"supported_features": 7}),
        "lock.front": Entity("lock.front", "Front Door"),
        "sensor.temperature": Entity("sensor.temperature", "Outside Temperature"),
    })
    catalog.exposed = {"cover.shade", "cover.private", "lock.front", "sensor.temperature"}
    original = set(store.load().commands)
    compiled = store.update(lambda d: d.update(ha_names={
        "enabled": True, "auto_add_commands": True, "excluded": {"entities": ["cover.private"]},
    }))
    expected = {
        "open_covers_device", "close_covers_device", "are_covers_open_device",
        "lock", "unlock", "is_locked", "sensor_value",
    }
    assert set(compiled.commands) - original == expected
    assert store.document()["commands"]["unlock"] == {"from": "home/unlock"}

    catalog.entities["cover.shade"] = replace(catalog.entities["cover.shade"], attributes={"supported_features": 7})
    assert set(store.update(lambda _: None).commands) - original == expected | {"cover_position_device"}


def test_only_areas_with_supported_assist_exposure_are_imported_or_followed(store):
    catalog = store.catalog
    catalog.areas["empty"] = Area("empty", "Empty")
    catalog.entities["weather.home"] = replace(catalog.entities["weather.home"], area_id="kitchen")
    catalog.entities["light.private"] = replace(catalog.entities["light.private"], area_id="kitchen")
    imported = Store(store.data_dir / "import", catalog=catalog)
    imported.update(lambda d: ha_names.import_once(d, catalog, ["light.desk"]))
    assert set(imported.document()["names"]["areas"]) == {"office"}

    assert set(enable(store).data["names"]["areas"]) == {"office"}
    store.put_name("areas", "empty", {"name": "Local Empty"})
    catalog.exposed.add("light.private")
    catalog.entities["light.desk"] = replace(catalog.entities["light.desk"], area_id=None)
    compiled = store.load()
    assert set(compiled.data["names"]["areas"]) == {"empty", "kitchen"}
    assert compiled.devices["light.desk"] == Device(None, (), ("Office Desk Lamp", "Office Task Light"))

    catalog.exposed.remove("light.private")
    assert set(store.load().data["names"]["areas"]) == {"empty"}
    assert set(imported.load().data["names"]["areas"]) == {"office"}


@pytest.mark.asyncio
async def test_follow_preview_warns_about_scoped_clashes_before_enabling(store):
    catalog = store.catalog
    catalog.entities["light.other_office"] = Entity("light.other_office", "Corner Lamp", ["Desk Lamp"], "office")
    catalog.entities["light.other_room"] = Entity("light.other_room", "Desk Lamp", area_id="kitchen")
    catalog.exposed.update({"light.other_office", "light.other_room"})
    catalog.entities["light.private"] = replace(catalog.entities["light.private"], area_id="kitchen")
    store.put_name("entities", "light.private", {"name": "Task Light", "house": [], "area": "office"})
    connected = asyncio.Event()
    connected.set()
    api = Api(SimpleNamespace(store=store, store_error=None, engine=None, trigger_reader=SimpleNamespace(error=None),
                              hass=SimpleNamespace(catalog=catalog, connected=connected)))
    request = make_mocked_request("GET", "/api/store")
    data = json.loads((await api.get_store(request)).text)
    assert data["error"] is None and not data["ha_names"]["enabled"]
    assert set(data["devices"]) == {"light.private"}
    preview = {e["id"]: e["clashes"] for e in data["ha_names"]["preview"]}
    assert {c["name"]: (c["with"], c["area"], c["area_name"]) for c in preview["light.desk"]} == {
        "Desk Lamp": (["light.other_office"], "office", "Office"),
        "Task Light": (["light.private"], "office", "Office"),
        "Office Desk Lamp": (["light.other_office"], None, None),
    }
    assert preview["light.other_room"] == []
    assert "light.private" not in preview


def test_rename_move_room_removal_and_exposure_follow_the_catalog(store):
    enable(store)
    entity = store.catalog.entities["light.desk"]
    store.catalog.entities["light.desk"] = replace(entity, name="Reading Lamp", aliases=["Book Light"], area_id="kitchen")
    device = store.load().devices["light.desk"]
    assert device.here == ("Reading Lamp", "Book Light")
    assert device.house == ("Kitchen Reading Lamp", "Kitchen Book Light")
    assert "Office Desk Lamp" not in device.phrases
    store.catalog.areas["kitchen"] = Area("kitchen", "Galley")
    assert store.load().devices["light.desk"].house == ("Galley Reading Lamp", "Galley Book Light")
    store.catalog.entities["light.desk"] = replace(store.catalog.entities["light.desk"], area_id=None)
    assert store.load().devices["light.desk"] == Device(None, (), ("Reading Lamp", "Book Light"))
    store.catalog.exposed = set()
    assert store.load().devices == {}
    store.catalog.exposed = {"light.desk"}
    del store.catalog.entities["light.desk"]
    assert store.load().devices == {}


def test_local_voice_controls_do_not_freeze_ha_names_or_rooms(store):
    enable(store)
    store.update(lambda d: d["ha_names"].update(overrides={"light.desk": {
        "implicit": False, "with_area": False, "whole_home": False, "aliases": {"en": ["Reading Light"]},
    }}))
    entity = store.catalog.entities["light.desk"]
    store.catalog.entities["light.desk"] = replace(entity, name="Kitchen Corner Lamp", aliases=[], area_id="kitchen")
    device = store.load().devices["light.desk"]
    assert device == Device(None, ("Corner Lamp", "Reading Light"), (), implicit=False, with_area=False)
    voiced = actions.voice_catalog(store.catalog, store.load().devices)
    assert actions.implicit_devices(voiced) == {}
    with pytest.raises(ConfigError, match="take control"):
        store.put_name("entities", "light.desk", {"name": "Frozen"})


@pytest.mark.parametrize("area", ["office", None])
def test_take_control_disconnects_names_and_room_even_from_no_area(store, area):
    store.catalog.entities["light.desk"] = replace(store.catalog.entities["light.desk"], area_id=area)
    before = enable(store).devices["light.desk"]
    store.update(lambda d: ha_names.take_control(d, store.catalog, "entities", "light.desk"))
    store.catalog.entities["light.desk"] = replace(store.catalog.entities["light.desk"], name="New Lamp", area_id="kitchen")
    store.catalog.exposed = set()
    restarted = Store(store.data_dir, catalog=store.catalog)
    device = restarted.load().devices["light.desk"]
    assert device == replace(before, area=area or "")
    voiced = actions.voice_catalog(store.catalog, restarted.load().devices)
    assert voiced.entities["light.desk"].area_id == area
    restarted.put_name("entities", "light.desk", None)
    store.catalog.exposed = {"light.desk"}
    assert restarted.load().devices == {}  # ownership removal does not silently reattach


@pytest.mark.parametrize("source", ["excluded directly", "taken then removed", "still manual"])
def test_resume_following_one_device(store, source):
    enable(store)
    store.put_name("entities", "light.private", {"name": "Private Local", "area": "office"})
    store.update(lambda d: ha_names.exclude(d, "entities", "light.private"))
    store.update(lambda d: d["ha_names"].update(overrides={"light.desk": {"whole_home": False}}))
    if source != "excluded directly":
        store.update(lambda d: ha_names.take_control(d, store.catalog, "entities", "light.desk"))
    if source != "still manual":
        store.put_name("entities", "light.desk", None)
    entity = store.catalog.entities["light.desk"]
    store.catalog.entities["light.desk"] = replace(entity, name="Reading Lamp", aliases=[], area_id="kitchen")

    def resume(confirm=False):
        return store.update(lambda d: ha_names.resume_following(d, store.catalog, "light.desk", confirm))

    before = store.document()
    store.catalog.exposed.remove("light.desk")
    with pytest.raises(ConfigError, match="exposed to Assist"):
        resume(True)
    assert store.document() == before
    store.catalog.exposed.add("light.desk")
    if source == "still manual":
        with pytest.raises(ConfigError, match="Confirm replacing"):
            resume()
        assert store.document() == before

    resumed = resume(source == "still manual")
    assert resumed.devices == {
        "light.desk": Device(None, ("Reading Lamp",), ()),
        "light.private": Device("office", ("Private Local",), ("Private Local",)),
    }
    assert actions.voice_catalog(store.catalog, resumed.devices).entities["light.desk"].area_id == "kitchen"
    saved = Store(store.data_dir, catalog=store.catalog)
    assert saved.load().devices == resumed.devices
    assert saved.document()["ha_names"]["excluded"]["entities"] == ["light.private"]


def test_manual_entries_win_and_exclusions_survive_restart(store):
    store.put_name("entities", "light.desk", {"name": "My Lamp", "area": "kitchen"})
    assert enable(store).devices["light.desk"].here == ("My Lamp",)
    store.catalog.exposed.add("light.private")
    assert "light.private" in store.load().devices
    store.put_name("entities", "light.private", None)
    restarted = Store(store.data_dir, catalog=store.catalog)
    assert set(restarted.load().devices) == {"light.desk"}
    restarted.update(lambda d: d["ha_names"].update(enabled=False))
    assert restarted.load().devices["light.desk"].area == "kitchen"


def test_import_once_is_selected_manual_and_unknown_exposure_is_not_all(store):
    store.catalog.exposed.add("light.private")
    commands = store.document()["commands"]
    store.update(lambda d: ha_names.import_once(d, store.catalog, ["light.desk"]))
    assert store.document()["commands"] == commands
    store.catalog.entities["light.desk"] = replace(store.catalog.entities["light.desk"], name="Renamed", area_id="kitchen")
    assert store.load().devices == {"light.desk": Device("office", ("Desk Lamp", "Task Light"), ("Office Desk Lamp", "Office Task Light"))}
    assert not store.document().get("ha_names", {}).get("enabled")
    store.catalog.exposed = None
    with pytest.raises(ConfigError, match="exposure"):
        store.update(lambda d: ha_names.import_once(d, store.catalog, ["light.desk"]))
    enable(store)
    assert set(store.load().devices) == {"light.desk"}  # the manually imported one only


def test_import_once_can_add_commands_for_selected_devices_only(store):
    catalog = store.catalog
    catalog.entities["cover.shade"] = Entity("cover.shade", "Shade", attributes={"supported_features": 7})
    catalog.exposed.add("cover.shade")
    store.put_command("my_open", {"from": "home/open_covers_device", "disabled": True})
    store.put_command("my_close", {"from": "home/close_covers_device"})
    store.delete_command("my_close")
    before = store.document()["commands"]

    compiled = store.update(lambda d: ha_names.import_once(d, catalog, ["cover.shade"], add_library_commands=True))

    assert set(compiled.devices) == {"cover.shade"}
    assert store.document()["commands"] == {
        **before,
        "are_covers_open_device": {"from": "home/are_covers_open_device"},
        "cover_position_device": {"from": "home/cover_position_device"},
    }
    settings = ha_names.config(store.document())
    assert not settings.get("enabled") and not settings.get("auto_add_commands")


def test_room_ownership_and_local_aliases_are_independent_of_language(store):
    enable(store)
    store.update(lambda d: ha_names.take_control(d, store.catalog, "areas", "office"))
    store.put_name("areas", "office", {"name": "Den"})
    assert store.load().devices["light.desk"].house[0] == "Den Desk Lamp"
    store.update(lambda d: d["ha_names"].update(overrides={"light.desk": {"aliases": {"en": ["Reading Light"]}}}))
    try:
        languages.use("de")
        compiled = store.load()
        assert compiled.devices["light.desk"].here == ("Desk Lamp", "Task Light")
        assert store.document()["ha_names"]["overrides"]["light.desk"]["aliases"]["en"] == ["Reading Light"]
    finally:
        languages.use("en")


def test_pinned_command_waits_for_withdrawn_name_without_blocking_the_rest(store):
    enable(store)
    pinned = {"description": "Use desk lamp", "require": [["wake"]], "response": "Ready",
              "names": {"entity": {"count": 1, "ids": ["light.desk"]}}}
    assert "pinned" in store.put_command("pinned", pinned).config["intents"]

    catalog = store.catalog
    # Cold start must work even without a first catalog.
    offline = Store(store.data_dir)
    assert offline.load().unavailable == {"pinned": "Waiting for named targets: light.desk"}

    catalog.exposed.clear()
    compiled = store.put_command("other", {"description": "Say hello", "examples": ["hello there"], "response": "Hello"})
    assert "other" in compiled.config["intents"] and "pinned" not in compiled.config["intents"]
    assert compiled.commands["pinned"].disabled
    with pytest.raises(ConfigError, match="not in the shared list"):
        store.put_command("typo", pinned)

    catalog.exposed.add("light.desk")
    assert "pinned" in store.load().config["intents"]
    stopped = store.update(lambda d: d["ha_names"].update(enabled=False))
    assert "pinned" not in stopped.config["intents"]
    assert store.document()["commands"]["pinned"] == pinned


def test_deleting_last_manual_name_does_not_fall_back_to_live_sync(store):
    store.put_name("entities", "light.desk", {"name": "Local Lamp", "area": "office"})
    enable(store)
    assert "light.desk" not in store.put_name("entities", "light.desk", None).devices
    assert store.document()["ha_names"]["excluded"]["entities"] == ["light.desk"]


@pytest.mark.parametrize("enabled,ids,message", [
    (False, [], "Select at least one"), (True, ["light.desk"], "Stop following"),
])
def test_import_rejects_unsuitable_submission_without_saving(store, enabled, ids, message):
    if enabled:
        enable(store)
    before = store.document()
    with pytest.raises(ConfigError, match=message):
        store.update(lambda d: ha_names.import_once(d, store.catalog, ids))
    assert Store(store.data_dir).document() == before
