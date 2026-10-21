"""The Voice Call integration: satellites as devices, and their timer lists."""

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.setup import async_setup_component

from tests.common import MockConfigEntry, async_fire_time_changed
from tests.typing import WebSocketGenerator

MAC = "d83bdaa3d5f4"


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain="voice_call", title="Voice Call")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _sync(client, satellites: list[dict]) -> list[dict]:
    await client.send_json_auto_id({"type": "voice_call/satellites/sync", "satellites": satellites})
    msg = await client.receive_json()
    assert msg["success"], msg
    return msg["result"]["satellites"]


async def test_config_flow_creates_one_entry(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init("voice_call", context={"source": "user"})
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    result = await hass.config_entries.flow.async_init("voice_call", context={"source": "user"})
    assert result["type"] == "abort"


async def test_satellite_starts_in_its_esphome_devices_area(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    devices = dr.async_get(hass)
    esphome = MockConfigEntry(domain="esphome", title="Voice Call VPE")
    esphome.add_to_hass(hass)
    existing = devices.async_get_or_create(
        config_entry_id=esphome.entry_id,
        connections={(dr.CONNECTION_NETWORK_MAC, "d8:3b:da:a3:d5:f4")},
        name="Voice Call VPE",
    )
    devices.async_update_device(existing.id, area_id="kitchen")

    await _setup(hass)
    client = await hass_ws_client(hass)
    [satellite] = await _sync(client, [{"id": MAC, "name": "voice-call-vpe"}])

    # Its own device, in the ESPHome device's area to begin with.
    assert satellite["device_id"] != existing.id
    assert satellite["area_id"] == "kitchen"
    assert satellite["device_name"] == "voice-call-vpe"
    assert satellite["timer_list"] == "voice_timer_list.voice_call_vpe_timers"
    device = devices.async_get(satellite["device_id"])
    assert ("voice_call", MAC) in device.identifiers
    entity = er.async_get(hass).async_get(satellite["timer_list"])
    assert entity.device_id == satellite["device_id"]
    assert hass.states.get(satellite["timer_list"]).state == "0"

    # From then on the areas are separate.
    devices.async_update_device(existing.id, area_id="office")
    [satellite] = await _sync(client, [{"id": MAC, "name": "voice-call-vpe"}])
    assert satellite["area_id"] == "kitchen"
    devices.async_update_device(satellite["device_id"], area_id="bedroom")
    [satellite] = await _sync(client, [{"id": MAC, "name": "voice-call-vpe"}])
    assert satellite["area_id"] == "bedroom"


async def test_satellite_without_esphome_gets_its_own_device(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    await _setup(hass)
    client = await hass_ws_client(hass)
    [satellite] = await _sync(client, [{"id": "manual", "name": "Desk"}])
    device = dr.async_get(hass).async_get(satellite["device_id"])
    assert device.name == "Desk" and not device.connections
    assert satellite["timer_list"] == "voice_timer_list.desk_timers"


async def test_satellites_survive_a_restart(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    entry = await _setup(hass)
    client = await hass_ws_client(hass)
    await _sync(client, [{"id": MAC, "name": "Kitchen VPE"}])
    await hass.async_block_till_done()
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await client.send_json_auto_id({"type": "voice_call/satellites/list"})
    msg = await client.receive_json()
    assert [s["timer_list"] for s in msg["result"]["satellites"]] == [
        "voice_timer_list.kitchen_vpe_timers"
    ]
    assert hass.states.get("voice_timer_list.kitchen_vpe_timers") is not None


async def test_timers_run_and_finish(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, freezer: FrozenDateTimeFactory
) -> None:
    await _setup(hass)
    client = await hass_ws_client(hass)
    [satellite] = await _sync(client, [{"id": MAC, "name": "Kitchen VPE"}])
    entity_id = satellite["timer_list"]

    await client.send_json_auto_id({"type": "voice_timer_list/item/subscribe", "entity_id": entity_id})
    assert (await client.receive_json())["success"]
    snapshot = await client.receive_json()
    assert snapshot["event"] == {"type": "timers", "timers": []}

    response = await hass.services.async_call(
        "voice_timer_list",
        "create_timer",
        {"entity_id": entity_id, "name": "pizza", "duration": {"minutes": 5}},
        blocking=True,
        return_response=True,
    )
    timer_id = response[entity_id]["timer_id"]
    created = await client.receive_json()
    assert created["event"]["event_type"] == "created"
    assert created["event"]["timer"]["name"] == "pizza"
    assert hass.states.get(entity_id).state == "1"

    await client.send_json_auto_id({"type": "voice_timer_list/item/list", "entity_id": entity_id})
    listed = await client.receive_json()
    [timer] = listed["result"]["timers"]
    assert timer["timer_id"] == timer_id and timer["created_duration"] == 300

    freezer.tick(timedelta(minutes=5, seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    finished = await client.receive_json()
    assert finished["event"]["event_type"] == "finished"
    assert hass.states.get(entity_id).state == "0"


async def test_finished_trigger(hass: HomeAssistant, hass_ws_client: WebSocketGenerator) -> None:
    await _setup(hass)
    client = await hass_ws_client(hass)
    [satellite] = await _sync(client, [{"id": MAC, "name": "Kitchen VPE"}])
    entity_id = satellite["timer_list"]
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "triggers": {"trigger": "voice_call.timer_finished", "target": {"entity_id": entity_id}},
                "actions": {"event": "timer_done", "event_data": {"name": "{{ trigger.timer.name }}"}},
            }
        },
    )
    seen = []
    hass.bus.async_listen("timer_done", seen.append)
    response = await hass.services.async_call(
        "voice_timer_list",
        "create_timer",
        {"entity_id": entity_id, "name": "tea", "duration": {"seconds": 1}},
        blocking=True,
        return_response=True,
    )
    await hass.services.async_call(
        "voice_timer_list",
        "finish_timer",
        {"entity_id": entity_id, "timer_id": response[entity_id]["timer_id"]},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert [e.data["name"] for e in seen] == ["tea"]


async def test_sync_needs_the_entry(hass: HomeAssistant, hass_ws_client: WebSocketGenerator) -> None:
    assert await async_setup_component(hass, "voice_call", {})
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "voice_call/satellites/sync", "satellites": []})
    msg = await client.receive_json()
    assert not msg["success"] and msg["error"]["code"] == "not_loaded"


async def test_removing_the_device_forgets_the_satellite(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    entry = await _setup(hass)
    client = await hass_ws_client(hass)
    [satellite] = await _sync(client, [{"id": MAC, "name": "Kitchen VPE"}])
    assert await async_setup_component(hass, "config", {})
    await client.send_json_auto_id(
        {
            "type": "config/device_registry/remove_config_entry",
            "config_entry_id": entry.entry_id,
            "device_id": satellite["device_id"],
        }
    )
    assert (await client.receive_json())["success"]
    assert entry.runtime_data.all() == []


async def test_satellite_comes_with_its_music_assistant_player(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """Music Assistant's player ids are its own; its player data has the MAC.
    The Sendspin client is a protocol player, played through the player that
    wraps it, which has the entity."""
    from types import SimpleNamespace

    from homeassistant.config_entries import ConfigEntryState

    music = MockConfigEntry(domain="music_assistant", title="Music Assistant")
    music.add_to_hass(hass)
    music.mock_state(hass, ConfigEntryState.LOADED)
    sendspin = SimpleNamespace(
        player_id="sendspin-1", device_info=SimpleNamespace(mac_address="D8:3B:DA:A3:D5:F4"), output_protocols=[]
    )
    wrapper = SimpleNamespace(
        player_id="up54cb3d39",
        device_info=SimpleNamespace(mac_address=None),
        output_protocols=[SimpleNamespace(output_protocol_id="native"), SimpleNamespace(output_protocol_id="sendspin-1")],
    )
    other = SimpleNamespace(player_id="up11111111", device_info=SimpleNamespace(mac_address="20:f8:3b:09:00:87"), output_protocols=[])
    music.runtime_data = SimpleNamespace(mass=SimpleNamespace(players=[other, sendspin, wrapper]))
    er.async_get(hass).async_get_or_create(
        "media_player", "music_assistant", "up54cb3d39", suggested_object_id="voice_mike", config_entry=music
    )

    await _setup(hass)
    client = await hass_ws_client(hass)
    [satellite, manual] = await _sync(client, [{"id": MAC, "name": "voice-call-vpe"}, {"id": "manual", "name": "Desk"}])
    assert satellite["media_player"] == "media_player.voice_mike"
    assert manual["media_player"] is None


async def _intent(client, **message) -> dict:
    await client.send_json_auto_id({"type": "voice_call/intent/handle", **message})
    msg = await client.receive_json()
    assert msg["success"], msg
    return msg["result"]


async def test_intents_act_on_what_is_exposed(hass: HomeAssistant, hass_ws_client: WebSocketGenerator) -> None:
    """An intent runs as Assist's: only on exposed entities; one exposed but
    unavailable is said apart; and why nothing matched comes back."""
    from homeassistant.components.homeassistant.exposed_entities import (  # pylint: disable=import-outside-toplevel
        async_expose_entity,
    )
    from homeassistant.helpers import area_registry as ar  # pylint: disable=import-outside-toplevel

    from tests.common import async_mock_service  # pylint: disable=import-outside-toplevel

    assert await async_setup_component(hass, "homeassistant", {})
    await _setup(hass)
    areas = ar.async_get(hass)
    kitchen = areas.async_create("Kitchen")
    office = areas.async_create("Office")
    hall = areas.async_create("Hall")
    entities = er.async_get(hass)
    for name, area, state, exposed in (
        ("ceiling", kitchen, "off", True),
        ("lamp", kitchen, "off", False),
        ("strip", kitchen, "unavailable", True),
        ("desk", office, "off", False),
    ):
        entry = entities.async_get_or_create("light", "test", name, suggested_object_id=name)
        entities.async_update_entity(entry.entity_id, area_id=area.id)
        hass.states.async_set(entry.entity_id, state, {"friendly_name": name.title()})
        async_expose_entity(hass, "conversation", entry.entity_id, exposed)
    turned_on = async_mock_service(hass, "light", "turn_on")
    from homeassistant.components.light import intent as light_intent  # pylint: disable=import-outside-toplevel

    await light_intent.async_setup_intents(hass)
    client = await hass_ws_client(hass)

    # Exposed ones only, by area id; the unavailable one said apart.
    result = await _intent(client, intent="HassTurnOn", slots={"domain": ["light"], "area": kitchen.id})
    acted = sorted(t["id"] for t in result["response"]["data"]["success"] if t["type"] == "entity")
    assert acted == ["light.ceiling", "light.strip"]
    assert [t["id"] for t in result["unavailable"]] == ["light.strip"]
    called = {e for c in turned_on for e in ([c.data["entity_id"]] if isinstance(c.data["entity_id"], str) else c.data["entity_id"])}
    assert "light.lamp" not in called and "light.ceiling" in called

    # A slot as a template of the command's variables.
    turned_on.clear()
    result = await _intent(
        client, intent="HassLightSet", slots={"brightness": "{{ slots.percentage }}", "name": "light.ceiling"},
        variables={"slots": {"percentage": 40}},
    )
    assert "error" not in result and turned_on[-1].data["brightness_pct"] == 40

    # Why not: nothing exposed there; nothing of that kind there; no such intent.
    result = await _intent(client, intent="HassTurnOn", slots={"domain": ["light"], "area": office.id})
    assert result["error"] == {"code": "no_match", "reason": "assistant", "name": None}
    result = await _intent(client, intent="HassTurnOn", slots={"domain": ["light"], "area": hall.id})
    assert result["error"]["code"] == "no_match" and result["error"]["reason"] == "area"
    result = await _intent(client, intent="HassNoSuchThing")
    assert result["error"]["code"] == "unknown_intent"

    # A device the app names: exposed or not.
    result = await _intent(client, intent="HassTurnOn", slots={"name": "light.desk"}, assistant=None)
    assert [t["id"] for t in result["response"]["data"]["success"]] == ["light.desk"]

    # What is exposed to Assist.
    await client.send_json_auto_id({"type": "voice_call/exposed"})
    msg = await client.receive_json()
    assert msg["success"] and msg["result"]["entities"] == ["light.ceiling", "light.strip"]
