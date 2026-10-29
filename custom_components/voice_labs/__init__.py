"""Voice Labs: the voice_labs app's satellites, and their timers.

The app registers each satellite it serves over the websocket API
(``voice_labs/satellites/sync``). Each becomes a device -- the satellite's
ESPHome device when it has one, matched by MAC, so both share one area -- with a
``voice_timer_list`` entity holding its timers. The app starts, matches and
rings those timers; automations can follow them with the ``voice_labs.timer_*``
triggers.

Each satellite's player in Music Assistant -- its Sendspin client's, found by
MAC in Music Assistant's own player data -- comes back with it.

``voice_timer_list`` is the timer_list entity platform (not yet merged into
Home Assistant), renamed so it cannot clash with it, and provided by this
integration.

The app runs Home Assistant's intents through it (``voice_labs/intent/handle``)
as Assist does -- only on entities exposed to Assist, unless the app names
the device itself -- on behalf of a satellite, and reads which entities are
exposed (``voice_labs/exposed``) for the questions it answers itself.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import TemplateError
from homeassistant.helpers import config_validation as cv, intent, template
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .satellites import Satellites, VoiceLabsConfigEntry
from .timer_list import async_setup_timer_lists
from .timer_list_const import DATA_COMPONENT

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the timer list entity component and the websocket API."""
    await async_setup_timer_lists(hass, config)
    websocket_api.async_register_command(hass, websocket_sync_satellites)
    websocket_api.async_register_command(hass, websocket_list_satellites)
    websocket_api.async_register_command(hass, websocket_handle_intent)
    websocket_api.async_register_command(hass, websocket_exposed)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: VoiceLabsConfigEntry) -> bool:
    """Set up the satellites and their timer lists."""
    satellites = Satellites(hass, entry)
    await satellites.async_load()
    entry.runtime_data = satellites
    # Loads this integration's voice_timer_list platform for the entry.
    return await hass.data[DATA_COMPONENT].async_setup_entry(entry)


async def async_unload_entry(hass: HomeAssistant, entry: VoiceLabsConfigEntry) -> bool:
    """Unload the timer lists. Their timers are lost."""
    return await hass.data[DATA_COMPONENT].async_unload_entry(entry)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: VoiceLabsConfigEntry, device: DeviceEntry
) -> bool:
    """Forget a satellite. Its ESPHome device, if it shares one, stays."""
    for domain, satellite_id in device.identifiers:
        if domain == DOMAIN:
            entry.runtime_data.async_remove(satellite_id)
    return True


@callback
def _satellites(hass: HomeAssistant) -> Satellites | None:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    return entries[0].runtime_data if entries else None


@websocket_api.websocket_command(
    {
        vol.Required("type"): "voice_labs/satellites/sync",
        vol.Required("satellites"): [
            {vol.Required("id"): cv.string, vol.Optional("name"): cv.string}
        ],
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_sync_satellites(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Register satellites; each gets a device and a timer list."""
    satellites = _satellites(hass)
    if satellites is None:
        connection.send_error(msg["id"], "not_loaded", "Voice Labs is not set up")
        return
    connection.send_result(msg["id"], {"satellites": await satellites.async_sync(msg["satellites"])})


@websocket_api.websocket_command({vol.Required("type"): "voice_labs/satellites/list"})
@websocket_api.require_admin
@callback
def websocket_list_satellites(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Every satellite registered so far."""
    satellites = _satellites(hass)
    if satellites is None:
        connection.send_error(msg["id"], "not_loaded", "Voice Labs is not set up")
        return
    connection.send_result(
        msg["id"], {"satellites": [satellites.describe(s) for s in satellites.all()]}
    )


ASSISTANT = "conversation"
"""What intents run as: Assist, so only what is exposed to it is acted on."""


@websocket_api.websocket_command(
    {
        vol.Required("type"): "voice_labs/intent/handle",
        vol.Required("intent"): cv.string,
        vol.Optional("slots", default={}): {cv.string: object},
        vol.Optional("variables", default={}): dict,
        vol.Optional("language"): cv.string,
        vol.Optional("device_id"): cv.string,
        vol.Optional("text"): cv.string,
        vol.Optional("assistant", default=ASSISTANT): vol.Any(cv.string, None),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_handle_intent(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Run an intent as Assist would: only on entities exposed to it (no
    ``assistant``: on any, for a device the app names), for the satellite
    whose device is ``device_id``. Slot values may be templates,
    rendered with ``variables`` (the command's). Why nothing matched comes
    back as ``error`` (``no_match`` and its reason: ``assistant`` when
    nothing that fits is exposed); the entities it matched that are
    unavailable -- which their services skip -- as ``unavailable``."""
    slots: dict[str, Any] = {}
    try:
        for key, value in msg["slots"].items():
            if isinstance(value, str) and template.is_template_string(value):
                value = template.Template(value, hass).async_render(msg["variables"], parse_result=True)
            if value not in (None, "", []):
                slots[key] = {"value": value}
    except TemplateError as err:
        connection.send_result(msg["id"], {"error": {"code": "template", "message": str(err)}})
        return
    try:
        response = await intent.async_handle(
            hass,
            DOMAIN,
            msg["intent"],
            slots,
            msg.get("text"),
            connection.context(msg),
            language=msg.get("language"),
            assistant=msg["assistant"],
            device_id=msg.get("device_id"),
        )
    except intent.MatchFailedError as err:
        reason = err.result.no_match_reason
        connection.send_result(
            msg["id"],
            {
                "error": {
                    "code": "no_match",
                    "reason": reason.name.lower() if reason is not None else None,
                    "name": err.result.no_match_name,
                }
            },
        )
        return
    except intent.UnknownIntent as err:
        connection.send_result(msg["id"], {"error": {"code": "unknown_intent", "message": str(err)}})
        return
    except intent.InvalidSlotInfo as err:
        connection.send_result(msg["id"], {"error": {"code": "invalid_slots", "message": str(err)}})
        return
    except intent.IntentHandleError as err:
        connection.send_result(msg["id"], {"error": {"code": "failed", "message": str(err)}})
        return
    result = response.as_dict()
    unavailable = [
        target
        for target in result.get("data", {}).get("success", [])
        if target.get("type") == "entity"
        and (state := hass.states.get(target["id"])) is not None
        and state.state == STATE_UNAVAILABLE
    ]
    connection.send_result(msg["id"], {"response": result, "unavailable": unavailable})


@websocket_api.websocket_command({vol.Required("type"): "voice_labs/exposed"})
@websocket_api.require_admin
@callback
def websocket_exposed(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Every entity exposed to Assist, as Assist decides it (new ones by the
    "expose new entities" setting)."""
    from homeassistant.components.homeassistant.exposed_entities import (  # pylint: disable=import-outside-toplevel
        async_should_expose,
    )

    connection.send_result(
        msg["id"],
        {"entities": sorted(e for e in hass.states.async_entity_ids() if async_should_expose(hass, ASSISTANT, e))},
    )
