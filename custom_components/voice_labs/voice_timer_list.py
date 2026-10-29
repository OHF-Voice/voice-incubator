"""Each satellite's timers: a voice_timer_list entity on its device."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import (
    AddConfigEntryEntitiesCallback,
    async_get_current_platform,
)

from .satellites import Satellite, VoiceLabsConfigEntry
from .timer_list_local import InMemoryTimerListEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VoiceLabsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a timer list for every known satellite; new ones come with sync."""
    satellites = entry.runtime_data
    satellites.platform = async_get_current_platform()
    async_add_entities([VoiceTimerList(s) for s in satellites.all()])


class VoiceTimerList(InMemoryTimerListEntity):
    """A satellite's timers, held in memory: they do not survive a restart."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:timer-outline"

    def __init__(self, satellite: Satellite) -> None:
        super().__init__(
            name="Timers",
            unique_id=satellite.timer_list_unique_id,
            device_info=satellite.device_info(),
        )
