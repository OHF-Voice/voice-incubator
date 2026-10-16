"""Home Assistant over one long-lived websocket.

Everything the app needs from Home Assistant goes over this connection:

* the area, floor and entity registries, as suggestions for names and
  targets (names are never taken from them automatically);
* ``execute_script``, which runs a command's action sequence with the matched
  slots, the satellite and the speaker as variables;
* ``render_template``, for spoken responses, with the same variables;
* ``fire_event``, so automations can react to every command.

As an app the Supervisor proxies the connection (``ws://supervisor/core/
websocket``) with the token it supplies in ``SUPERVISOR_TOKEN``; standalone it
is a long-lived access token against Home Assistant's own URL. The connection
reconnects for as long as the app runs, and event subscriptions are renewed
each time.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import logging
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import websockets

_LOGGER = logging.getLogger(__name__)

EventCallback = Callable[[dict], Awaitable[None] | None]

# Entities a voice command may target from the UI. Commands written in YAML
# may name anything.
VOICE_DOMAINS = (
    "light",
    "switch",
    "fan",
    "cover",
    "lock",
    "climate",
    "media_player",
    "scene",
    "script",
    "input_boolean",
    "valve",
    "vacuum",
    "weather",
    "todo",
    "shopping_list",
)

CONNECT_WAIT = 5.0
"""Seconds a command waits for a dropped connection to come back."""

REGISTRY_EVENTS = (
    "area_registry_updated",
    "floor_registry_updated",
    "entity_registry_updated",
    "device_registry_updated",
)


class HassError(RuntimeError):
    """Home Assistant answered a command with an error."""


@dataclass
class Area:
    area_id: str
    name: str
    aliases: list[str] = field(default_factory=list)
    floor_id: str | None = None


@dataclass
class Floor:
    floor_id: str
    name: str
    aliases: list[str] = field(default_factory=list)
    level: int | None = None


@dataclass
class Entity:
    entity_id: str
    name: str
    aliases: list[str] = field(default_factory=list)
    area_id: str | None = None
    platform: str | None = None
    """The integration that provides it (``music_assistant`` ...)."""

    unique_id: str | None = None
    device_id: str | None = None

    @property
    def domain(self) -> str:
        return self.entity_id.split(".", 1)[0]


@dataclass
class Catalog:
    """Home Assistant's areas, floors and voice-domain entities."""

    areas: dict[str, Area] = field(default_factory=dict)
    floors: dict[str, Floor] = field(default_factory=dict)
    entities: dict[str, Entity] = field(default_factory=dict)
    device_areas: dict[str, str | None] = field(default_factory=dict)
    """Device id -> area id, for every device (satellites' included)."""
    device_models: dict[str, tuple[str | None, str | None]] = field(default_factory=dict)
    """Device id -> (manufacturer, model)."""
    device_macs: dict[str, list[str]] = field(default_factory=dict)
    """MAC (aa:bb:..) -> the devices with it (an ESPHome device, ...)."""

    def device_media_player(self, mac: str) -> str | None:
        """The ESPHome media player of the device with this MAC: where Home
        Assistant's TTS speaks on a satellite."""
        wanted = ":".join(mac.replace(":", "").lower()[i : i + 2] for i in range(0, 12, 2))
        devices = set(self.device_macs.get(wanted, []))
        return next(
            (
                e.entity_id
                for e in self.entities.values()
                if e.domain == "media_player" and e.platform == "esphome" and e.device_id in devices
            ),
            None,
        )

    def music_players(self) -> list[Entity]:
        """Music Assistant's players (its media_player entities)."""
        return [
            e for e in self.entities.values() if e.platform == "music_assistant" and e.domain == "media_player"
        ]

    def music_player_for(self, mac: str, node: str | None = None) -> str | None:
        """A guess at the Music Assistant player of a Sendspin client with this
        MAC (``aabbccddeeff``), for when the Voice Call integration cannot say:
        some versions name it ``up`` + the MAC; else the one player whose
        device is ESPHome's model ``node`` (the device's name), when only one
        is. Several satellites flashed from one YAML share a name, so then
        nothing is guessed."""
        players = self.music_players()
        wanted = "up" + mac.replace(":", "").lower()
        exact = next((e.entity_id for e in players if e.unique_id == wanted), None)
        if exact or not node:
            return exact
        same = [e.entity_id for e in players if self.device_models.get(e.device_id or "") == ("ESPHome", node)]
        return same[0] if len(same) == 1 else None

    def area_info(self, area_id: str | None) -> dict[str, Any]:
        """``area_id``, ``area_name``, ``floor_id`` and ``floor_name`` for a
        satellite, with None for what is unknown."""
        area = self.areas.get(area_id) if area_id else None
        floor = self.floors.get(area.floor_id) if area and area.floor_id else None
        return {
            "area_id": area_id,
            "area_name": area.name if area else None,
            "floor_id": floor.floor_id if floor else None,
            "floor_name": floor.name if floor else None,
        }


class HomeAssistant:
    def __init__(self, url: str, token: str, websocket_url: str | None = None) -> None:
        self.url = url.rstrip("/")
        self.token = token
        if websocket_url is None:
            scheme = "wss" if self.url.startswith("https://") else "ws"
            websocket_url = f"{scheme}://{self.url.split('://', 1)[-1]}/api/websocket"
        self.websocket_url = websocket_url
        self.connected = asyncio.Event()
        self.catalog = Catalog()
        self._socket: Any = None
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}
        self._subscriptions: dict[int, Callable[[dict], None]] = {}
        self._event_callbacks: dict[str, list[EventCallback]] = {}
        self._task: asyncio.Task | None = None
        self._refresh_task: asyncio.Task | None = None
        self._catalog_listeners: list[Callable[[], Awaitable[None] | None]] = []
        self._connect_listeners: list[Callable[[], Awaitable[None] | None]] = []

    # ------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        self._task = asyncio.ensure_future(self._run())

    async def stop(self) -> None:
        if self._socket is not None:
            with contextlib.suppress(Exception):
                await self._socket.close()
        for task in (self._task, self._refresh_task):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._task = self._refresh_task = None

    async def wait_connected(self, timeout: float | None = None) -> bool:
        try:
            await asyncio.wait_for(self.connected.wait(), timeout)
        except (TimeoutError, asyncio.TimeoutError):
            return False
        return True

    def on_event(self, event_type: str, callback: EventCallback) -> None:
        """Call ``callback(event)`` for every ``event_type``, across reconnects."""
        self._event_callbacks.setdefault(event_type, []).append(callback)

    def on_catalog_change(self, callback: Callable[[], Awaitable[None] | None]) -> None:
        self._catalog_listeners.append(callback)

    def on_connect(self, callback: Callable[[], Awaitable[None] | None]) -> None:
        """Call ``callback`` after every (re)connection, once the catalog is
        current: subscriptions die with a connection and must be renewed."""
        self._connect_listeners.append(callback)

    async def _run(self, reconnect_seconds: float = 5.0) -> None:
        while True:
            try:
                await self._connect_once()
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - the connection must never die
                _LOGGER.warning("Home Assistant connection lost: %s", err)
            self.connected.clear()
            self._socket = None
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(ConnectionError("Home Assistant disconnected"))
            self._pending.clear()
            self._subscriptions.clear()
            await asyncio.sleep(reconnect_seconds)

    async def _connect_once(self) -> None:
        async with websockets.connect(
            self.websocket_url, max_size=None, ping_interval=20
        ) as socket:
            greeting = json.loads(await socket.recv())
            if greeting.get("type") != "auth_required":
                raise HassError(f"Unexpected greeting: {greeting}")
            await socket.send(json.dumps({"type": "auth", "access_token": self.token}))
            auth = json.loads(await socket.recv())
            if auth.get("type") != "auth_ok":
                raise HassError(f"Authentication failed: {auth.get('message', auth)}")
            self._socket = socket
            _LOGGER.info("Connected to Home Assistant %s", auth.get("ha_version", ""))

            reader = asyncio.ensure_future(self._read(socket))
            try:
                for event_type in set(self._event_callbacks) | set(REGISTRY_EVENTS):
                    await self._subscribe_event(event_type)
                await self.refresh_catalog()
                self.connected.set()
                for listener in self._connect_listeners:
                    result = listener()
                    if asyncio.iscoroutine(result):
                        asyncio.ensure_future(_logged(result))
                await reader
            finally:
                reader.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await reader

    async def _read(self, socket) -> None:
        async for raw in socket:
            message = json.loads(raw)
            messages = message if isinstance(message, list) else [message]
            for item in messages:
                self._dispatch(item)

    def _dispatch(self, message: dict) -> None:
        msg_id = message.get("id")
        if message.get("type") == "event":
            handler = self._subscriptions.get(msg_id)
            if handler is not None:
                handler(message.get("event", {}))
            return
        future = self._pending.pop(msg_id, None)
        if future is None or future.done():
            return
        if message.get("success", True):
            future.set_result(message.get("result"))
        else:
            error = message.get("error") or {}
            future.set_exception(HassError(f"{error.get('code')}: {error.get('message')}"))

    # ------------------------------------------------------------- commands

    async def call(self, message: dict, timeout: float = 30.0) -> Any:
        """Send one command and return its result."""
        # Someone is waiting on the satellite: a reconnect gets a moment, not
        # the whole timeout.
        if self._socket is None and not await self.wait_connected(min(timeout, CONNECT_WAIT)):
            raise ConnectionError("Not connected to Home Assistant")
        msg_id = next(self._ids)
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = future
        await self._socket.send(json.dumps({**message, "id": msg_id}))
        try:
            return await asyncio.wait_for(future, timeout)
        finally:
            self._pending.pop(msg_id, None)

    async def _subscribe(self, message: dict, handler: Callable[[dict], None]) -> int:
        msg_id = next(self._ids)
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = future
        # Registered before sending: the first event can follow the result
        # in the same read.
        self._subscriptions[msg_id] = handler
        await self._socket.send(json.dumps({**message, "id": msg_id}))
        try:
            await asyncio.wait_for(future, 30)
        except BaseException:
            self._subscriptions.pop(msg_id, None)
            raise
        return msg_id

    async def subscribe(self, message: dict, handler: Callable[[dict], None]) -> int:
        """Start a subscription; ``handler`` gets each event. Lasts until
        ``unsubscribe`` or the connection drops (see ``on_connect``)."""
        if self._socket is None and not await self.wait_connected(CONNECT_WAIT):
            raise ConnectionError("Not connected to Home Assistant")
        return await self._subscribe(message, handler)

    async def unsubscribe(self, msg_id: int) -> None:
        await self._unsubscribe(msg_id)

    async def _unsubscribe(self, msg_id: int) -> None:
        self._subscriptions.pop(msg_id, None)
        with contextlib.suppress(Exception):
            await self.call({"type": "unsubscribe_events", "subscription": msg_id}, 5)

    async def _subscribe_event(self, event_type: str) -> None:
        def handler(event: dict) -> None:
            if event_type in REGISTRY_EVENTS:
                self._schedule_refresh()
            for callback in self._event_callbacks.get(event_type, ()):
                result = callback(event)
                if asyncio.iscoroutine(result):
                    asyncio.ensure_future(result)

        await self._subscribe({"type": "subscribe_events", "event_type": event_type}, handler)

    async def render_template(
        self, template: str, variables: dict | None = None, timeout: float = 10.0
    ) -> str:
        """Render ``template`` once, with ``variables``."""
        result = await self.render_value(template, variables, timeout)
        return "" if result is None else str(result)

    async def render_value(
        self, template: str, variables: dict | None = None, timeout: float = 10.0
    ) -> Any:
        """Render ``template`` once, as Home Assistant parses the result: a
        list or a mapping when it looks like one, else the text."""
        if self._socket is None and not await self.wait_connected(min(timeout, CONNECT_WAIT)):
            raise ConnectionError("Not connected to Home Assistant")
        rendered: asyncio.Future = asyncio.get_running_loop().create_future()

        def handler(event: dict) -> None:
            if rendered.done():
                return
            if "error" in event:
                # Warnings (an undefined attribute) come the same way, ahead of
                # the result; only an error means there will be none.
                if event.get("level", "ERROR") == "ERROR":
                    rendered.set_exception(HassError(str(event["error"])))
                else:
                    _LOGGER.debug("Template %s: %s", event.get("level"), event["error"])
            else:
                rendered.set_result(event.get("result"))

        msg_id = await self._subscribe(
            {
                "type": "render_template",
                "template": template,
                "variables": variables or {},
                "report_errors": True,
                "strict": False,
                "timeout": timeout,
            },
            handler,
        )
        try:
            return await asyncio.wait_for(rendered, timeout)
        finally:
            await self._unsubscribe(msg_id)

    async def execute_script(
        self, sequence: list[dict], variables: dict | None = None, timeout: float = 60.0
    ) -> Any:
        """Run an action sequence; returns the response it stops with, if any."""
        result = await self.call(
            {"type": "execute_script", "sequence": sequence, "variables": variables or {}},
            timeout,
        )
        return (result or {}).get("response")

    async def call_service(
        self,
        domain: str,
        service: str,
        data: dict | None = None,
        target: dict | None = None,
        return_response: bool = False,
    ) -> Any:
        """Call a service; its response when ``return_response``."""
        message: dict[str, Any] = {
            "type": "call_service",
            "domain": domain,
            "service": service,
            "service_data": data or {},
        }
        if target:
            message["target"] = target
        if return_response:
            message["return_response"] = True
        result = await self.call(message)
        return (result or {}).get("response") if return_response else None

    async def update_device_area(self, device_id: str, area_id: str | None) -> None:
        await self.call({"type": "config/device_registry/update", "device_id": device_id, "area_id": area_id})
        self.catalog.device_areas[device_id] = area_id

    async def config_entries(self, domain: str) -> list[dict]:
        return await self.call({"type": "config_entries/get", "domain": domain}) or []

    async def start_config_flow(self, handler: str) -> dict:
        """Start a config flow over the REST API (there is no websocket command
        for it). Raises HassError when the integration is not installed."""

        def post() -> dict:
            request = urllib.request.Request(
                f"{self.url}/api/config/config_entries/flow",
                data=json.dumps({"handler": handler, "show_advanced_options": False}).encode(),
                headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    return json.load(response)
            except urllib.error.HTTPError as err:
                raise HassError(f"{err.code}: {err.read().decode(errors='replace')[:200]}") from err

        return await asyncio.to_thread(post)

    async def fire_event(self, event_type: str, data: dict) -> None:
        await self.call({"type": "fire_event", "event_type": event_type, "event_data": data})

    # --------------------------------------------------------- speech, text

    async def speech_to_text(
        self, entity_id: str, pcm: bytes, language: str, rate: int = 16000, timeout: float = 30.0
    ) -> str:
        """Transcribe 16-bit mono PCM with an STT entity (``/api/stt``: there
        is no websocket command outside a pipeline). Raises HassError."""
        import aiohttp  # pylint: disable=import-outside-toplevel

        headers = {
            "Authorization": f"Bearer {self.token}",
            "X-Speech-Content": (
                f"format=wav; codec=pcm; sample_rate={rate}; bit_rate=16; channel=1; language={language}"
            ),
        }
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as http:
                async with http.post(f"{self.url}/api/stt/{entity_id}", data=pcm, headers=headers) as response:
                    if response.status != 200:
                        raise HassError(f"{entity_id}: {response.status} {(await response.text())[:200]}")
                    result = await response.json()
        except aiohttp.ClientError as err:
            raise HassError(f"{entity_id}: {err}") from err
        if result.get("result") != "success":
            raise HassError(f"{entity_id} could not transcribe the audio")
        return str(result.get("text") or "")

    async def stt_engines(self, language: str | None) -> list[dict]:
        """STT entities and the languages they support that match ``language``
        (best first); every language they support when it is None."""
        message: dict[str, Any] = {"type": "stt/engine/list"}
        if language:
            message["language"] = language
        result = await self.call(message)
        return [p for p in (result or {}).get("providers", []) if str(p.get("engine_id", "")).startswith("stt.")]

    async def tts_engines(self, language: str | None) -> list[dict]:
        message: dict[str, Any] = {"type": "tts/engine/list"}
        if language:
            message["language"] = language
        result = await self.call(message)
        return [p for p in (result or {}).get("providers", []) if str(p.get("engine_id", "")).startswith("tts.")]

    async def tts_voices(self, engine_id: str, language: str) -> list[dict]:
        result = await self.call({"type": "tts/engine/voices", "engine_id": engine_id, "language": language})
        return list((result or {}).get("voices") or [])

    # -------------------------------------------------------------- catalog

    def _schedule_refresh(self, delay: float = 1.0) -> None:
        """Registry edits come in bursts; refresh once they settle."""
        if self._refresh_task is not None and not self._refresh_task.done():
            self._refresh_task.cancel()

        async def later() -> None:
            await asyncio.sleep(delay)
            try:
                await self.refresh_catalog()
            except (HassError, ConnectionError, OSError, TimeoutError) as err:
                _LOGGER.warning("Could not refresh Home Assistant registries: %s", err)

        self._refresh_task = asyncio.ensure_future(later())

    async def refresh_catalog(self) -> Catalog:
        """Re-read areas, floors and voice-domain entities."""
        floors = await self.call({"type": "config/floor_registry/list"})
        areas = await self.call({"type": "config/area_registry/list"})
        devices = await self.call({"type": "config/device_registry/list"})
        registry = await self.call({"type": "config/entity_registry/list"})
        states = await self.call({"type": "get_states"})

        device_area = {d["id"]: d.get("area_id") for d in devices or []}
        catalog_device_areas = dict(device_area)
        device_models = {d["id"]: (d.get("manufacturer"), d.get("model")) for d in devices or []}
        device_macs: dict[str, list[str]] = {}
        for device in devices or []:
            for kind, value in device.get("connections") or []:
                if kind == "mac":
                    device_macs.setdefault(str(value).lower(), []).append(device["id"])
        friendly = {
            s["entity_id"]: s.get("attributes", {}).get("friendly_name") or s["entity_id"]
            for s in states or []
        }
        catalog = Catalog(device_areas=catalog_device_areas, device_models=device_models, device_macs=device_macs)
        for floor in floors or []:
            catalog.floors[floor["floor_id"]] = Floor(
                floor["floor_id"], floor["name"], list(floor.get("aliases") or []), floor.get("level")
            )
        for area in areas or []:
            catalog.areas[area["area_id"]] = Area(
                area["area_id"], area["name"], list(area.get("aliases") or []), area.get("floor_id")
            )
        wanted = [
            entry
            for entry in registry or []
            if entry["entity_id"].split(".", 1)[0] in VOICE_DOMAINS
            and not entry.get("disabled_by")
            and not entry.get("hidden_by")
        ]
        aliases: dict[str, list[str]] = {}
        if wanted:
            # Aliases are only in the extended entries.
            extended = await self.call(
                {
                    "type": "config/entity_registry/get_entries",
                    "entity_ids": [entry["entity_id"] for entry in wanted],
                }
            )
            for entity_id, entry in (extended or {}).items():
                if entry:
                    aliases[entity_id] = [a for a in entry.get("aliases") or [] if a]
        for entry in wanted:
            entity_id = entry["entity_id"]
            catalog.entities[entity_id] = Entity(
                entity_id,
                entry.get("name") or friendly.get(entity_id) or entry.get("original_name") or entity_id,
                aliases.get(entity_id, []),
                entry.get("area_id") or device_area.get(entry.get("device_id")),
                entry.get("platform"),
                entry.get("unique_id"),
                entry.get("device_id"),
            )
        # Entities without a registry entry (YAML-defined) still have states.
        for entity_id, name in friendly.items():
            if entity_id.split(".", 1)[0] in VOICE_DOMAINS and entity_id not in catalog.entities:
                if not any(e["entity_id"] == entity_id for e in registry or []):
                    catalog.entities[entity_id] = Entity(entity_id, name)
        self.catalog = catalog
        _LOGGER.info(
            "Home Assistant: %d floor(s), %d area(s), %d voice entities",
            len(catalog.floors),
            len(catalog.areas),
            len(catalog.entities),
        )
        for listener in self._catalog_listeners:
            result = listener()
            if asyncio.iscoroutine(result):
                await result
        return catalog


async def _logged(coroutine) -> None:
    try:
        await coroutine
    except Exception:  # noqa: BLE001 - a listener must not take the connection down
        _LOGGER.exception("Home Assistant connection listener failed")

