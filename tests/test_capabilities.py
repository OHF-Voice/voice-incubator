"""What a command's target can do: checked against Home Assistant's states."""

from __future__ import annotations

import pytest
from adapt_ctc.config import ConfigError

from voice_labs import capabilities
from voice_labs.commands import parse_command
from voice_labs.hass import Catalog, Entity

FAN_MODES = ["on_low", "on_high", "auto_low", "auto_high", "off"]


def entity(entity_id, area=None, **attributes):
    return Entity(entity_id, entity_id.split(".")[1].replace("_", " ").title(), area_id=area, attributes=attributes)


def test_what_a_device_can():
    plain = entity("light.plain", supported_color_modes=["onoff"])
    white = entity("light.white", supported_color_modes=["color_temp"])
    color = entity("light.color", supported_color_modes=["rgbw"])
    assert [capabilities.can(e, "brightness") for e in (plain, white, color)] == [False, True, True]
    assert [capabilities.can(e, "color") for e in (plain, white, color)] == [False, False, True]
    # Home Assistant makes a white of a color light's colors.
    assert [capabilities.can(e, "color_temp") for e in (plain, white, color)] == [False, True, True]
    # By supported_features: a garage door opens, a blind is set.
    door = entity("cover.door", supported_features=3)
    blind = entity("cover.blind", supported_features=15)
    assert [capabilities.can(e, "position") for e in (door, blind)] == [False, True]
    assert capabilities.can(entity("valve.water", supported_features=4), "position") is True
    # Another kind of device can't; one with no state isn't known.
    assert capabilities.can(entity("switch.fan"), "color") is False
    assert capabilities.can(Entity("light.x", "X"), "color") is None
    assert capabilities.can(None, "color") is None


def test_a_value_said_is_the_devices_own():
    hvac = entity("climate.hvac", supported_features=8, fan_modes=FAN_MODES, hvac_modes=["off", "heat", "cool"])
    assert capabilities.can(hvac, "fan_mode") is True
    assert capabilities.can(hvac, "hvac_mode", "heat") is True
    assert capabilities.can(hvac, "hvac_mode", "dry") is False
    # However it is written; else every one with all its words.
    assert capabilities.own_value(hvac, "fan_mode", "On Low") == "on_low"
    assert capabilities.choices(hvac, "fan_mode", "low") == ["on_low", "auto_low"]
    assert capabilities.choices(hvac, "fan_mode", "circulate") == []
    # Of several, the one most like what it is set to now: the rest of the
    # setting stays.
    assert capabilities.own_value(hvac, "fan_mode", "low", "auto_high") == "auto_low"
    assert capabilities.own_value(hvac, "fan_mode", "auto", "on_low") == "auto_low"
    assert capabilities.own_value(hvac, "fan_mode", "on", "auto_high") == "on_high"
    assert capabilities.own_value(hvac, "fan_mode", "low") == "on_low"  # the first it lists
    # A thermostat without a fan setting has none of its modes.
    assert capabilities.can(entity("climate.old", supported_features=1, fan_modes=["auto"]), "fan_mode") is False


def test_check_the_devices_named_or_whats_there():
    catalog = Catalog(entities={e.entity_id: e for e in (
        entity("light.plain", "kitchen", supported_color_modes=["onoff"]),
        entity("light.color", "kitchen", supported_color_modes=["hs"]),
        entity("light.hidden", "kitchen", supported_color_modes=["hs"]),
        entity("light.office", "office", supported_color_modes=["onoff"]),
        entity("cover.blind", "kitchen", supported_features=15, device_class="blind"),
        entity("cover.door", "kitchen", supported_features=3, device_class="garage"),
    )})
    catalog.exposed = {e for e in catalog.entities if e != "light.hidden"}
    color = {"color": None}
    # Named: the ones that can stay; with none, nothing can.
    some = capabilities.check(color, {"entity": ["light.plain", "light.color"]}, catalog, None)
    assert (some.entities, some.cannot, some.slots["entity"]) == (None, ["light.plain"], ["light.color"])
    none = capabilities.check(color, {"entity": ["light.plain"]}, catalog, None)
    assert none.nothing and none.slots["entity"] == ["light.plain"]
    assert capabilities.check(color, {"entity": ["light.color"]}, catalog, None).cannot == []
    # A place: what is exposed there, each that can by name.
    kitchen = capabilities.check(color, {}, catalog, ["kitchen"])
    assert (kitchen.entities, kitchen.cannot) == (["light.color"], ["light.plain"])
    office = capabilities.check(color, {}, catalog, ["office"])
    assert office.nothing and office.cannot == ["light.office"]
    # Nothing of the kind there: as it is (the intent says so).
    nothing = capabilities.check(color, {}, catalog, ["bedroom"])
    assert (nothing.entities, nothing.cannot, nothing.found) == (None, [], 0)
    # What the intent picks by: the blinds, not the garage door.
    blinds = capabilities.check({"position": None}, {}, catalog, ["kitchen"], {"domain": "cover", "device_class": "blind"})
    assert (blinds.entities, blinds.cannot) == (None, [])
    doors = capabilities.check({"position": None}, {}, catalog, ["kitchen"], {"device_class": "garage"})
    assert doors.nothing and doors.cannot == ["cover.door"]


def test_a_command_says_what_it_needs():
    base = {"description": "set the fan", "examples": ["set the fan"], "lists": {"mode": {"list": "fan_modes"}},
            "action": [{"action": "climate.set_fan_mode"}]}
    assert parse_command("c", {**base, "supports": "fan_mode"}).behavior.supports == {"fan_mode": None}
    assert parse_command("c", {**base, "supports": {"fan_mode": "mode"}}).behavior.supports == {"fan_mode": "mode"}
    assert parse_command("c", {**base, "supports": ["color", "brightness"]}).behavior.supports == {"color": None, "brightness": None}
    assert "not_supported" in parse_command("c", {**base, "action": None, "response": "ok", "supports": "color"}).behavior.outcomes()
    with pytest.raises(ConfigError, match="'speed' is not one of"):
        parse_command("c", {**base, "supports": "speed"})
    with pytest.raises(ConfigError, match="has no values"):
        parse_command("c", {**base, "supports": {"color": "mode"}})
    with pytest.raises(ConfigError, match="'speed' is not a slot it listens for"):
        parse_command("c", {**base, "supports": {"fan_mode": "speed"}})
