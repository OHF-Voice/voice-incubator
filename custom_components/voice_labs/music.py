"""A satellite's player in Music Assistant: its Sendspin client's.

Music Assistant names its players with ids of its own (``up54cb3d39``), and
Home Assistant's devices for them carry no MAC. Music Assistant's own player
data does: the Music Assistant integration's client holds every player with
its device's identifiers, so a satellite's MAC finds its player there, and the
entity registry the player's entity.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import format_mac

MUSIC_ASSISTANT = "music_assistant"


@callback
def music_player_for(hass: HomeAssistant, mac: str) -> str | None:
    """The media_player entity of the Music Assistant player with this MAC."""
    wanted = format_mac(mac)
    registry = er.async_get(hass)
    for entry in hass.config_entries.async_entries(MUSIC_ASSISTANT):
        if entry.state is not ConfigEntryState.LOADED:
            continue
        mass = getattr(getattr(entry, "runtime_data", None), "mass", None)
        if mass is None:
            continue
        players = list(mass.players)
        found = [p for p in players if _mac(p) == wanted]
        ids = {p.player_id for p in found}
        # A protocol player (the Sendspin client itself) plays through the
        # player that wraps it; that one has the entity.
        found = [
            p
            for p in players
            if p.player_id not in ids and any(o.output_protocol_id in ids for o in getattr(p, "output_protocols", None) or [])
        ] + found
        for player in found:
            entity_id = registry.async_get_entity_id("media_player", MUSIC_ASSISTANT, player.player_id)
            if entity_id is not None:
                return entity_id
    return None


def _mac(player: Any) -> str | None:
    mac = getattr(getattr(player, "device_info", None), "mac_address", None)
    return format_mac(mac) if mac else None
