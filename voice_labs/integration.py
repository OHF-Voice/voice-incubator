"""The Voice Labs integration in Home Assistant, from the app's side.

The integration (OHF-Voice/voice-labs, installed with HACS) gives each
satellite a device of its own -- with its own area -- and a voice_timer_list
entity for its timers. The app:

* sets the integration up if it is installed but not yet configured (its config
  flow asks nothing);
* registers every satellite it knows (``voice_labs/satellites/sync``) and
  learns its device and timer list;
* follows each timer list (``voice_timer_list/item/subscribe``): which timers are
  running, for matching timer commands and for recognizing their names, and
  which finish, to ring the satellite;
* asks which areas are on do not disturb (``voice_labs/do_not_disturb``: a
  switch for each satellite's area) before it sends something there.

All of it is renewed on every reconnection to Home Assistant.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .hass import HassError, HomeAssistant

_LOGGER = logging.getLogger(__name__)

DOMAIN = "voice_labs"
RUNNING = ("active", "paused")

NOT_INSTALLED = (
    "The Voice Labs integration is not installed in Home Assistant. Install it "
    "with HACS (github.com/OHF-Voice/voice-labs) and restart Home Assistant to give "
    "satellites devices, areas and timers."
)


@dataclass
class SatelliteLink:
    """What Home Assistant knows a satellite as."""

    key: str
    device_id: str | None = None
    timer_list: str | None = None
    name: str | None = None
    media_player: str | None = None
    """Its Sendspin client's player in Music Assistant, found by MAC."""


class Integration:
    def __init__(
        self,
        hass: HomeAssistant,
        on_finished: Callable[[str, dict[str, Any]], Awaitable[None] | None],
        on_timers_changed: Callable[[], None],
    ) -> None:
        self.hass = hass
        self.on_finished = on_finished
        self.on_timers_changed = on_timers_changed
        self.status = "connecting"
        """``connecting``, ``ready``, or why not: shown in the UI."""

        self.links: dict[str, SatelliteLink] = {}
        self.timers: dict[str, dict[str, dict[str, Any]]] = {}
        """Satellite key -> timer id -> timer, as voice_timer_list reports them."""

        self._wanted: dict[str, str] = {}
        self._subscriptions: dict[str, int] = {}
        self._sync_task: asyncio.Task | None = None
        self._said_no_quiet = False
        hass.on_connect(self._on_connect)
        # A player Music Assistant adds later (a satellite's Sendspin client
        # found after it registered) shows up as a registry change.
        hass.on_catalog_change(self._catalog_changed)

    @property
    def ready(self) -> bool:
        return self.status == "ready"

    # --------------------------------------------------------------- setup

    async def _on_connect(self) -> None:
        # Subscriptions died with the old connection.
        self._subscriptions.clear()
        await self.ensure()
        await self.sync()

    async def ensure(self) -> None:
        """Set the integration up if it is installed but not configured."""
        try:
            entries = await self.hass.config_entries(DOMAIN)
            if not any(e.get("state") == "loaded" for e in entries):
                if entries:
                    self.status = f"The Voice Labs integration is {entries[0].get('state')}."
                    return
                result = await self.hass.start_config_flow(DOMAIN)
                _LOGGER.info("Set up the Voice Labs integration: %s", result.get("type"))
            self.status = "ready"
        except HassError as err:
            self.status = NOT_INSTALLED
            _LOGGER.warning("%s (%s)", NOT_INSTALLED, err)
        except (ConnectionError, OSError, TimeoutError) as err:
            self.status = f"Could not reach Home Assistant: {err}"

    # ----------------------------------------------------------- satellites

    def want(self, key: str, name: str) -> None:
        """Register this satellite (soon: several connecting at once make one sync)."""
        if self._wanted.get(key) == name and key in self.links:
            return
        self._wanted[key] = name
        if self._sync_task is None or self._sync_task.done():
            self._sync_task = asyncio.ensure_future(self._sync_soon())

    def _catalog_changed(self) -> None:
        if self.ready and self._wanted and (self._sync_task is None or self._sync_task.done()):
            self._sync_task = asyncio.ensure_future(self._sync_soon())

    async def _sync_soon(self) -> None:
        await asyncio.sleep(0.5)
        await self.sync()

    async def sync(self) -> None:
        if not self.ready or not self._wanted:
            return
        try:
            result = await self.hass.call(
                {
                    "type": "voice_labs/satellites/sync",
                    "satellites": [{"id": k, "name": n} for k, n in self._wanted.items()],
                }
            )
        except HassError as err:
            # Installed and configured but not loaded yet (or an older version).
            self.status = f"Voice Labs could not register satellites: {err}"
            _LOGGER.warning(self.status)
            return
        except (ConnectionError, OSError, TimeoutError) as err:
            _LOGGER.warning("Could not register satellites: %s", err)
            return
        for item in result.get("satellites", []):
            key = item["id"]
            link = self.links.setdefault(key, SatelliteLink(key))
            link.device_id = item.get("device_id")
            link.timer_list = item.get("timer_list")
            link.name = item.get("device_name")
            link.media_player = item.get("media_player")
            if item.get("area_id") is not None or link.device_id not in self.hass.catalog.device_areas:
                self.hass.catalog.device_areas[link.device_id] = item.get("area_id")
            if link.timer_list and link.timer_list not in self._subscriptions:
                await self._follow(key, link.timer_list)

    # --------------------------------------------------------------- timers

    async def _follow(self, key: str, timer_list: str) -> None:
        def handler(event: dict[str, Any]) -> None:
            timers = self.timers.setdefault(key, {})
            if event.get("type") == "timers":
                timers.clear()
                timers.update({t["timer_id"]: t for t in event.get("timers", [])})
            elif event.get("type") == "change":
                timer = event["timer"]
                if event.get("event_type") == "removed":
                    timers.pop(timer["timer_id"], None)
                else:
                    timers[timer["timer_id"]] = timer
                if event.get("event_type") == "finished":
                    result = self.on_finished(key, timer)
                    if asyncio.iscoroutine(result):
                        asyncio.ensure_future(result)
            self.on_timers_changed()

        try:
            self._subscriptions[timer_list] = await self.hass.subscribe(
                {"type": "voice_timer_list/item/subscribe", "entity_id": timer_list}, handler
            )
        except (HassError, ConnectionError, OSError, TimeoutError) as err:
            _LOGGER.warning("Could not follow %s: %s", timer_list, err)

    def running(self, key: str) -> list[dict[str, Any]]:
        """This satellite's active and paused timers, oldest first."""
        timers = [t for t in self.timers.get(key, {}).values() if t.get("status") in RUNNING]
        return sorted(timers, key=lambda t: t.get("created_at") or "")

    def running_names(self) -> list[str]:
        """Names of every running timer, on any satellite."""
        return sorted(
            {
                t["name"]
                for timers in self.timers.values()
                for t in timers.values()
                if t.get("status") in RUNNING and t.get("name")
            }
        )

    # ------------------------------------------------------ do not disturb

    async def do_not_disturb(self) -> dict[str, dict[str, Any]]:
        """Each satellite's area's do not disturb switch, by area id: its
        ``entity_id`` and whether it is ``on``. Asked each time, so an
        automation's change counts at once; none when it can't be asked (an
        integration from before it had them: said once)."""
        if not self.ready:
            return {}
        try:
            result = await self.hass.call({"type": "voice_labs/do_not_disturb"}, timeout=5)
        except HassError as err:
            if not self._said_no_quiet:
                self._said_no_quiet = True
                _LOGGER.warning("No do not disturb (update the Voice Labs integration): %s", err)
            return {}
        except (ConnectionError, OSError, TimeoutError) as err:
            _LOGGER.warning("Could not ask which areas are on do not disturb: %s", err)
            return {}
        return {str(k): v for k, v in (result.get("areas") or {}).items() if isinstance(v, dict)}

    async def quiet_areas(self) -> set[str]:
        """The areas whose do not disturb is on."""
        return {area for area, switch in (await self.do_not_disturb()).items() if switch.get("on")}

    async def fresh_timers(self, key: str) -> list[dict[str, Any]]:
        """The satellite's timers straight from Home Assistant."""
        link = self.links.get(key)
        if link is None or not link.timer_list:
            return []
        result = await self.hass.call(
            {"type": "voice_timer_list/item/list", "entity_id": link.timer_list}
        )
        timers = result.get("timers", [])
        self.timers[key] = {t["timer_id"]: t for t in timers}
        return [t for t in timers if t.get("status") in RUNNING]
