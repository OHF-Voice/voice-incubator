"""The library's cover, lock and light commands, run in Home Assistant's own
template engine and script runner: what they act on and what they say."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import (
    area_registry as ar,
    config_validation as cv,
    entity_registry as er,
    floor_registry as fr,
)
from homeassistant.helpers.script import Script
from homeassistant.helpers.template import Template

from tests.common import async_mock_service

LIBRARY = Path(__file__).resolve().parent.parent / "voice_call" / "library"


def command(file: str, command_id: str) -> dict[str, Any]:
    return yaml.safe_load((LIBRARY / f"{file}.yaml").read_text())["commands"][command_id]


async def _home(hass: HomeAssistant) -> SimpleNamespace:
    upstairs = fr.async_get(hass).async_create("First Floor")
    areas = ar.async_get(hass)
    bedroom = areas.async_create("Bedroom", floor_id=upstairs.floor_id)
    office = areas.async_create("Office", floor_id=upstairs.floor_id)
    kitchen = areas.async_create("Kitchen")
    garage = areas.async_create("Garage")
    registry = er.async_get(hass)

    def add(entity_id: str, area, state: str, **attributes) -> None:
        domain, object_id = entity_id.split(".", 1)
        entry = registry.async_get_or_create(domain, "test", object_id, suggested_object_id=object_id)
        if area is not None:
            registry.async_update_entity(entry.entity_id, area_id=area.id)
        hass.states.async_set(entry.entity_id, state, attributes)

    add("cover.bedroom_blinds", bedroom, "open", device_class="blind", friendly_name="Bedroom Blinds")
    add("cover.office_blinds", office, "closed", device_class="blind", friendly_name="Office Blinds")
    add("cover.garage_door", garage, "closed", device_class="garage", friendly_name="Garage Door")
    add("light.kitchen_ceiling", kitchen, "on", friendly_name="Kitchen Ceiling")
    add("light.kitchen_lamp", kitchen, "on", friendly_name="Kitchen Lamp")
    add("light.bedroom_lamp", bedroom, "off", friendly_name="Bedroom Lamp")
    add("lock.front_door", None, "locked", friendly_name="Front Door")
    add("lock.back_door", None, "unlocked", friendly_name="Back Door")
    add("binary_sensor.side_door_lock", None, "off", device_class="lock", friendly_name="Side Door Lock")
    add("binary_sensor.front_window", None, "on", device_class="window", friendly_name="Front Window")
    return SimpleNamespace(
        bedroom=bedroom.id, office=office.id, kitchen=kitchen.id, garage=garage.id, upstairs=upstairs.floor_id
    )


def _variables(slots: dict[str, Any], area: str | None = None, area_name: str | None = None) -> dict[str, Any]:
    """What the app gives a command (voice_call/actions.py)."""
    return {
        "slots": slots,
        "satellite": {"id": "vpe", "name": "VPE", "area_id": area, "area_name": area_name},
        "speaker": {"name": None, "score": None},
        "target": {},
        "command": {"id": "c", "description": "", "text": ""},
    }


def _say(hass: HomeAssistant, spec: dict[str, Any], values: dict[str, Any], returned: dict | None = None) -> str:
    """The response for how it went, as the app picks it."""
    returned = returned or {}
    outcome = returned.get("outcome") or "done"
    response = spec["response"]
    template = response.get(outcome) if isinstance(response, dict) else response
    result = {**returned, "outcome": outcome}
    return Template(template, hass).async_render({**values, "result": result}, parse_result=False).strip()


async def _run(hass: HomeAssistant, spec: dict[str, Any], values: dict[str, Any]) -> dict:
    action = spec["action"] if isinstance(spec["action"], list) else [spec["action"]]
    script = Script(hass, cv.SCRIPT_SCHEMA(action), "test", "test")
    run = await script.async_run(run_variables=values, context=Context())
    return run.service_response or {}


@pytest.mark.parametrize("which", ["open", "close"])
async def test_covers_by_type(hass: HomeAssistant, which: str) -> None:
    home = await _home(hass)
    spec = command("home", f"{which}_covers")
    calls = async_mock_service(hass, "cover", f"{which}_cover")
    verb = "Opening" if which == "open" else "Closing"

    # In the satellite's area.
    values = _variables({"device_class": "blind"}, home.bedroom, "Bedroom")
    returned = await _run(hass, spec, values)
    assert calls[-1].data["entity_id"] == ["cover.bedroom_blinds"]
    assert _say(hass, spec, values, returned) == f"{verb} the blind."

    # On a floor.
    values = _variables({"device_class": "blind", "floor": [home.upstairs]}, home.kitchen, "Kitchen")
    returned = await _run(hass, spec, values)
    assert sorted(calls[-1].data["entity_id"]) == ["cover.bedroom_blinds", "cover.office_blinds"]
    assert _say(hass, spec, values, returned) == f"{verb} the blinds on the First Floor."

    # None of the type here: every one in the home.
    values = _variables({"device_class": "garage"}, home.kitchen, "Kitchen")
    returned = await _run(hass, spec, values)
    assert calls[-1].data["entity_id"] == ["cover.garage_door"]
    assert _say(hass, spec, values, returned) == f"{verb} the garage door."

    # None where it was asked for.
    count = len(calls)
    values = _variables({"device_class": "shade", "area": [home.bedroom]}, home.kitchen, "Kitchen")
    returned = await _run(hass, spec, values)
    assert len(calls) == count and returned["outcome"] == "not_found"
    assert _say(hass, spec, values, returned) == "There aren't any shades in the Bedroom."


async def test_covers_and_locks_by_name(hass: HomeAssistant) -> None:
    await _home(hass)
    opened = async_mock_service(hass, "cover", "open_cover")
    unlocked = async_mock_service(hass, "lock", "unlock")
    locked = async_mock_service(hass, "lock", "lock")

    spec = command("home", "open_cover")
    values = {**_variables({"entity": ["cover.garage_door"]}), "target": {"entity_id": ["cover.garage_door"]}}
    await _run(hass, spec, values)
    assert opened[-1].data["entity_id"] == ["cover.garage_door"]
    assert _say(hass, spec, values) == "Opening Garage Door."

    spec = command("home", "unlock")
    values = {**_variables({"entity": ["lock.front_door"]}), "target": {"entity_id": ["lock.front_door"]}}
    await _run(hass, spec, values)
    assert unlocked[-1].data["entity_id"] == ["lock.front_door"]
    assert _say(hass, spec, values) == "Unlocking Front Door."

    await _run(hass, command("home", "lock_all"), _variables({}))
    assert locked[-1].data["entity_id"] == "all"


async def test_are_any_lights_on(hass: HomeAssistant) -> None:
    home = await _home(hass)
    spec = command("basic", "any_lights_on")
    assert _say(hass, spec, _variables({}, home.kitchen, "Kitchen")) == (
        "Yes, Kitchen Ceiling and Kitchen Lamp are on in the Kitchen."
    )
    assert _say(hass, spec, _variables({"area": [home.bedroom]}, home.kitchen, "Kitchen")) == (
        "No, the lights are off in the Bedroom."
    )
    assert _say(hass, spec, _variables({"floor": [home.upstairs]})) == "No, the lights are off on the First Floor."
    hass.states.async_set("light.bedroom_lamp", "on", {"friendly_name": "Bedroom Lamp"})
    assert _say(hass, spec, _variables({})) == (
        "Yes, Kitchen Ceiling, Kitchen Lamp and Bedroom Lamp are on."
    )


async def test_is_it_open(hass: HomeAssistant) -> None:
    home = await _home(hass)
    spec = command("home", "is_cover_open")
    assert _say(hass, spec, _variables({"entity": ["cover.garage_door"], "asked": "open"})) == (
        "No, Garage Door is closed."
    )
    assert _say(hass, spec, _variables({"entity": ["cover.garage_door"], "asked": "closed"})) == (
        "Yes, Garage Door is closed."
    )

    spec = command("home", "are_covers_open")
    values = _variables({"device_class": "blind", "floor": [home.upstairs], "asked": "open"}, home.kitchen)
    assert _say(hass, spec, values) == "Yes, Bedroom Blinds is open."
    values = _variables({"device_class": "blind", "area": [home.office], "asked": "closed"}, home.kitchen)
    assert _say(hass, spec, values) == "Yes, the blind is closed in the Office."
    values = _variables({"device_class": "garage", "asked": "open"}, home.kitchen)
    assert _say(hass, spec, values) == "No, the garage door is closed."
    values = _variables({"device_class": "shade", "asked": "open"}, home.kitchen)
    assert _say(hass, spec, values) == "There aren't any shades."


async def test_is_it_locked(hass: HomeAssistant) -> None:
    await _home(hass)
    spec = command("home", "is_locked")
    assert _say(hass, spec, _variables({"entity": ["lock.back_door"], "asked": "locked"})) == (
        "No, Back Door is unlocked."
    )
    # A binary sensor of the lock class: on is unlocked.
    assert _say(hass, spec, _variables({"entity": ["binary_sensor.side_door_lock"], "asked": "locked"})) == (
        "Yes, Side Door Lock is locked."
    )

    spec = command("home", "all_locked")
    assert _say(hass, spec, _variables({"asked": "locked"})) == "No, Back Door is unlocked."
    hass.states.async_set("lock.back_door", "locked", {"friendly_name": "Back Door"})
    assert _say(hass, spec, _variables({"asked": "locked"})) == "Yes, all 3 are locked."
    assert _say(hass, spec, _variables({})) == "Yes, all 3 are locked."  # "is everything locked"
    hass.states.async_set("lock.back_door", "jammed", {"friendly_name": "Back Door"})
    hass.states.async_set("binary_sensor.side_door_lock", "on", {"device_class": "lock", "friendly_name": "Side Door Lock"})
    assert _say(hass, spec, _variables({"asked": "unlocked"})) == (
        "Yes, Back Door is jammed and Side Door Lock is unlocked."
    )
