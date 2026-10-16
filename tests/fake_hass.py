"""A stand-in for Home Assistant's websocket API, for tests and UI work.

It answers what voice_call asks for -- auth, the registries, get_states,
render_template, execute_script, fire_event, event subscriptions, the
Voice Call integration's API (satellites, voice_timer_list timers), and Music
Assistant's (players, search, play_media, get_queue, transfer_queue) -- and records the
scripts it was asked to run, the services called and the events fired. Timers run only when a test finishes them (``finish_timer``). Templates are
rendered with jinja2 and stubs for ``now()``, ``states()`` and
``state_attr()``; that is close enough to check variables arrive, not a
replacement for Home Assistant's own template engine.

    .venv/bin/python -m tests.fake_hass --port 8124
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import itertools
import json
import logging
from typing import Any

import jinja2
import websockets

TOKEN = "fake-token"

FLOORS = [
    {"floor_id": "ground", "name": "Ground Floor", "aliases": ["downstairs"], "level": 0},
    {"floor_id": "first", "name": "First Floor", "aliases": ["upstairs"], "level": 1},
]
AREAS = [
    {"area_id": "kitchen", "name": "Kitchen", "aliases": [], "floor_id": "ground"},
    {"area_id": "living_room", "name": "Living Room", "aliases": ["Den", "Lounge"], "floor_id": "ground"},
    {"area_id": "office", "name": "Office", "aliases": [], "floor_id": "first"},
    {"area_id": "bedroom", "name": "Bedroom", "aliases": ["Master Bedroom"], "floor_id": "first"},
]
ENTITIES = [
    ("light.kitchen_ceiling", "Kitchen Ceiling", "kitchen", []),
    ("light.desk_lamp", "Desk Lamp", "office", ["Lamp"]),
    ("light.bedroom_lamp", "Bedroom Lamp", "bedroom", []),
    ("climate.ecobee", "EcoBee", "living_room", ["Thermostat"]),
    ("media_player.tv", "TV", "living_room", ["Television"]),
    ("cover.garage_door", "Garage Door", None, []),
    ("lock.front_door", "Front Door", None, []),
    ("scene.movie_time", "Movie Time", "living_room", []),
    ("script.good_night", "Good Night", None, []),
    ("sensor.outside_temperature", "Outside Temperature", None, []),
    ("weather.home", "Home", None, []),
    ("todo.shopping_list", "Shopping List", None, []),
]
STATES = {
    "weather.home": ("sunny", {"temperature": 21.4}),
    "sensor.outside_temperature": ("12.5", {}),
}
# Music Assistant's players: entity id -> (unique id, name, area, device's
# (manufacturer, model)). The first is the fake satellite's Sendspin player:
# Music Assistant's id for it is its own, and its device's model is the
# satellite's ESPHome name.
MUSIC_PLAYERS = {
    "media_player.fake_vpe": ("up54cb3d39", "Fake VPE", None, ("ESPHome", "fake-vpe")),
    "media_player.kitchen_speaker": ("kitchen-speaker-uuid", "Kitchen Speaker", "kitchen", ("Sonos", "One")),
}
SENDSPIN_PLAYERS = {"aabbccddeeff": "media_player.fake_vpe"}
"""Satellite -> its Sendspin player, as the Voice Call integration finds it."""
# The fake satellite's ESPHome device and its media player (where TTS speaks).
ESPHOME_DEVICE = {"id": "esphome-fake-vpe", "mac": "aa:bb:cc:dd:ee:ff", "media_player": "media_player.fake_vpe_media_player"}
STT_ENGINES = [{"engine_id": "stt.fake_whisper", "supported_languages": ["en-US", "en-GB", "de-DE", "fr-FR"]}]
TTS_ENGINES = [{"engine_id": "tts.fake_cloud", "supported_languages": ["en-US", "de-DE"]}]


def _for_language(providers: list[dict], language: str | None) -> list[dict]:
    """As Home Assistant answers: with a language, only what matches it."""
    if not language:
        return providers
    base = language.split("-")[0].lower()
    return [
        {**p, "supported_languages": [c for c in p["supported_languages"] if c.split("-")[0].lower() == base]}
        for p in providers
    ]
TTS_VOICES = {"tts.fake_cloud": [{"voice_id": "JennyNeural", "name": "Jenny"}, {"voice_id": "GuyNeural", "name": "Guy"}]}
MUSIC_LIBRARY = {
    "artists": [{"media_type": "artist", "uri": "library://artist/1", "name": "Miles Davis"}],
    "albums": [
        {"media_type": "album", "uri": "library://album/1", "name": "Kind of Blue", "artists": [{"name": "Miles Davis"}]},
    ],
    "tracks": [
        {"media_type": "track", "uri": "library://track/1", "name": "So What", "artists": [{"name": "Miles Davis"}]},
        {"media_type": "track", "uri": "library://track/2", "name": "Stand by Me", "artists": [{"name": "Ben E. King"}]},
    ],
    "playlists": [{"media_type": "playlist", "uri": "library://playlist/1", "name": "Chill"}],
    "radio": [{"media_type": "radio", "uri": "library://radio/1", "name": "KEXP"}],
}


class FakeHass:
    def __init__(self) -> None:
        self.scripts: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.script_response: Any = None
        self.fail_scripts = False
        self.integration = True
        """Whether the Voice Call integration is installed and set up."""
        self.services: list[dict[str, Any]] = []
        self.satellites: dict[str, dict[str, Any]] = {}
        self.device_areas: dict[str, str | None] = {}
        self.timers: dict[str, dict[str, dict[str, Any]]] = {}
        """timer list entity -> timer id -> timer."""
        self._timer_ids = itertools.count(1)
        self._timer_subscribers: dict[str, list] = {}
        self._clients: set = set()
        self.states: dict[str, tuple[str, dict[str, Any]]] = dict(STATES)
        for entity_id, (_, name, _, _) in MUSIC_PLAYERS.items():
            self.states[entity_id] = ("idle", {"friendly_name": name, "volume_level": 0.5, "is_volume_muted": False})
        self.stt_text = "what time is it"
        """What the STT entity hears (over REST, see serve_rest)."""
        self.stt_requests: list[dict[str, Any]] = []
        self.voice_requests: list[dict[str, Any]] = []
        self.on_speak: Any = None
        """Called with tts.speak's data: a test makes the satellite announce."""
        self.broken_search = False
        """music_assistant.search fails as Home Assistant's does when Music
        Assistant leaves an album's ``favorite`` empty."""
        self.queues: dict[str, dict[str, Any]] = {}
        """Music player -> the item it plays (Music Assistant's queue, in short)."""
        self._env = jinja2.Environment(undefined=jinja2.ChainableUndefined)
        self._env.globals.update(
            now=datetime.datetime.now,
            states=lambda entity_id: self.states.get(entity_id, ("unknown", {}))[0],
            state_attr=lambda entity_id, attr: self.states.get(entity_id, ("", {}))[1].get(attr),
        )
        self._env.filters.setdefault("round", lambda value, n=0: round(float(value), n))

    def render(self, template: str, variables: dict[str, Any]) -> str:
        return self._env.from_string(template).render(**variables)

    async def handle(self, socket) -> None:
        await socket.send(json.dumps({"type": "auth_required", "ha_version": "fake"}))
        auth = json.loads(await socket.recv())
        if auth.get("access_token") != TOKEN:
            await socket.send(json.dumps({"type": "auth_invalid", "message": "bad token"}))
            return
        await socket.send(json.dumps({"type": "auth_ok", "ha_version": "fake"}))
        self._clients.add(socket)
        try:
            async for raw in socket:
                message = json.loads(raw)
                await self._answer(socket, message)
        finally:
            self._clients.discard(socket)

    async def _answer(self, socket, message: dict[str, Any]) -> None:
        msg_id, kind = message["id"], message["type"]

        async def result(value: Any = None, success: bool = True, error: str = "") -> None:
            out: dict[str, Any] = {"id": msg_id, "type": "result", "success": success}
            if success:
                out["result"] = value
            else:
                out["error"] = {"code": "failed", "message": error}
            await socket.send(json.dumps(out))

        if kind in ("subscribe_events", "unsubscribe_events"):
            await result()
        elif kind == "config/floor_registry/list":
            await result(FLOORS)
        elif kind == "config/area_registry/list":
            await result(AREAS)
        elif kind == "config/device_registry/list":
            await result(
                [
                    {"id": f"device-{e}", "area_id": None, "manufacturer": m[0], "model": m[1], "connections": []}
                    for e, (_, _, _, m) in MUSIC_PLAYERS.items()
                ]
                + [
                    {
                        "id": ESPHOME_DEVICE["id"],
                        "area_id": None,
                        "manufacturer": "Espressif",
                        "model": "esp32-s3-devkitc-1",
                        "connections": [["mac", ESPHOME_DEVICE["mac"]]],
                    }
                ]
            )
        elif kind == "config/entity_registry/list":
            await result(
                [
                    {"entity_id": e, "name": None, "original_name": n, "area_id": a, "device_id": None, "platform": "demo", "unique_id": e}
                    for e, n, a, _ in ENTITIES
                ]
                + [
                    {"entity_id": e, "name": None, "original_name": n, "area_id": a, "device_id": f"device-{e}", "platform": "music_assistant", "unique_id": u}
                    for e, (u, n, a, _) in MUSIC_PLAYERS.items()
                ]
                + [
                    {
                        "entity_id": ESPHOME_DEVICE["media_player"],
                        "name": None,
                        "original_name": "Media Player",
                        "area_id": None,
                        "device_id": ESPHOME_DEVICE["id"],
                        "platform": "esphome",
                        "unique_id": "aabbccddeeff-media_player",
                    }
                ]
            )
        elif kind == "config/entity_registry/get_entries":
            wanted = set(message.get("entity_ids") or [])
            await result({e: {"entity_id": e, "aliases": al} for e, _, _, al in ENTITIES if e in wanted})
        elif kind == "get_states":
            await result(
                [
                    {
                        "entity_id": e,
                        "state": self.states.get(e, ("on", {}))[0],
                        "attributes": {"friendly_name": n, **self.states.get(e, ("", {}))[1]},
                    }
                    for e, n, _, _ in ENTITIES
                ]
                + [
                    {"entity_id": e, "state": self.states[e][0], "attributes": self.states[e][1]}
                    for e in MUSIC_PLAYERS
                ]
            )
        elif kind == "render_template":
            await result()
            try:
                rendered = self.render(message["template"], message.get("variables") or {})
                event = {"result": rendered, "listeners": {}}
            except jinja2.TemplateError as err:
                event = {"error": str(err), "level": "ERROR"}
            await socket.send(json.dumps({"id": msg_id, "type": "event", "event": event}))
        elif kind == "execute_script":
            self.scripts.append({"sequence": message["sequence"], "variables": message.get("variables")})
            if self.fail_scripts:
                await result(success=False, error="Action light.turn_on failed")
            else:
                await result({"context": {"id": "x"}, "response": self.script_response})
        elif kind == "fire_event":
            self.events.append({"event_type": message["event_type"], "data": message.get("event_data")})
            await result({"context": {"id": "x"}})
        elif kind == "config_entries/get":
            entries = {
                "voice_call": [{"entry_id": "vc", "domain": "voice_call", "state": "loaded"}] if self.integration else [],
                "music_assistant": [{"entry_id": "ma", "domain": "music_assistant", "state": "loaded"}],
            }
            await result(entries.get(message.get("domain"), []))
        elif kind == "voice_call/satellites/sync":
            if not self.integration:
                await result(success=False, error="unknown command")
                return
            out = []
            for item in message["satellites"]:
                satellite = self.satellites.setdefault(
                    item["id"],
                    {
                        "id": item["id"],
                        "device_id": f"device-{item['id']}",
                        "timer_list": f"voice_timer_list.{item['id']}_timers",
                    },
                )
                satellite["name"] = item.get("name")
                self.timers.setdefault(satellite["timer_list"], {})
                out.append(
                    {
                        **satellite,
                        "device_name": item.get("name"),
                        "area_id": self.device_areas.get(satellite["device_id"]),
                        "media_player": SENDSPIN_PLAYERS.get(item["id"]),
                    }
                )
            await result({"satellites": out})
        elif kind == "config/device_registry/update":
            self.device_areas[message["device_id"]] = message.get("area_id")
            await result({})
        elif kind == "voice_timer_list/item/subscribe":
            entity_id = message["entity_id"]
            self._timer_subscribers.setdefault(entity_id, []).append((socket, msg_id))
            await result()
            await self._push(entity_id, {"type": "timers", "timers": list(self.timers.get(entity_id, {}).values())})
        elif kind == "voice_timer_list/item/list":
            await result({"timers": list(self.timers.get(message["entity_id"], {}).values())})
        elif kind == "stt/engine/list":
            await result({"providers": _for_language(STT_ENGINES, message.get("language"))})
        elif kind == "tts/engine/list":
            await result({"providers": _for_language(TTS_ENGINES, message.get("language"))})
        elif kind == "tts/engine/voices":
            self.voice_requests.append(message)
            await result({"voices": TTS_VOICES.get(message["engine_id"], [])})
        elif kind == "call_service":
            self.services.append(message)
            if self.broken_search and message["domain"] == "music_assistant" and message["service"] == "search":
                await result(success=False, error="invalid_format: expected bool at 'albums[0].favorite'")
                return
            response = await self._call_service(message)
            await result({"context": {"id": "x"}, **({"response": response} if message.get("return_response") else {})})
        else:
            await result(success=False, error=f"unknown command {kind}")


    async def _push(self, entity_id: str, event: dict[str, Any]) -> None:
        for socket, msg_id in list(self._timer_subscribers.get(entity_id, [])):
            try:
                await socket.send(json.dumps({"id": msg_id, "type": "event", "event": event}))
            except websockets.ConnectionClosed:
                self._timer_subscribers[entity_id].remove((socket, msg_id))

    async def _call_service(self, message: dict[str, Any]) -> Any:
        if message["domain"] == "tts" and message["service"] == "speak":
            if self.on_speak is not None:
                self.on_speak(message.get("service_data") or {})
            return None
        if message["domain"] in ("music_assistant", "media_player"):
            return self._music(message)
        if message["domain"] != "voice_timer_list":
            return None
        entity_id = message["target"]["entity_id"]
        data = message.get("service_data") or {}
        timers = self.timers.setdefault(entity_id, {})
        service = message["service"]
        if service == "create_timer":
            timer_id = str(next(self._timer_ids))
            seconds = float(data["duration"])
            timers[timer_id] = {
                "timer_id": timer_id,
                "name": data.get("name"),
                "status": "active",
                "created_duration": seconds,
                "total_duration": seconds,
                "created_at": datetime.datetime.now().isoformat(),
                "remaining": seconds,
            }
            await self._push(entity_id, {"type": "change", "event_type": "created", "timer": timers[timer_id]})
            return {entity_id: {"timer_id": timer_id}}
        timer = timers[data["timer_id"]]
        status, event = {
            "cancel_timer": ("cancelled", "cancelled"),
            "pause_timer": ("paused", "paused"),
            "unpause_timer": ("active", "unpaused"),
            "finish_timer": ("finished", "finished"),
        }.get(service, (timer["status"], "time_changed"))
        if service in ("add_time", "subtract_time"):
            sign = 1 if service == "add_time" else -1
            timer["remaining"] += sign * float(data["duration"])
        timer["status"] = status
        await self._push(entity_id, {"type": "change", "event_type": event, "timer": timer})
        return None

    def _music(self, message: dict[str, Any]) -> Any:
        """Music Assistant, in short: search the small library, play an item
        (the players start playing it), and the media player controls."""
        service = message["service"]
        data = message.get("service_data") or {}
        def matching(text: str, types: list[str]) -> dict[str, list[dict]]:
            words = [w for w in text.lower().replace(" - ", " ").split() if len(w) > 2]
            return {
                group: [
                    item
                    for item in found
                    if item["media_type"] in types
                    and any(w in f"{item['name']} {' '.join(a['name'] for a in item.get('artists', []))}".lower() for w in words)
                ]
                for group, found in MUSIC_LIBRARY.items()
            }

        everything = ["artist", "album", "track", "playlist", "radio"]
        if service == "search":
            return matching(f"{data.get('artist') or ''} {data['name']}", data.get("media_type") or everything)
        if service == "search_media":
            # Music Assistant's media browser: "Artist - Name" titles, its URIs.
            entity_id = message["target"]["entity_id"]
            types = data.get("media_filter_classes") or everything
            found = [i for group in matching(data["search_query"], types).values() for i in group]
            return {
                entity_id: {
                    "result": [
                        {
                            "title": f"{i['artists'][0]['name']} - {i['name']}" if i.get("artists") else i["name"],
                            "media_class": "music" if i["media_type"] == "radio" else i["media_type"],
                            "media_content_type": "music",
                            "media_content_id": i["uri"],
                        }
                        for i in found
                    ]
                }
            }
        ids = (message.get("target") or {}).get("entity_id") or []
        ids = [ids] if isinstance(ids, str) else ids
        if service == "get_queue":
            return {e: {"current_item": {"media_item": self.queues[e]}} for e in ids if e in self.queues}
        if service == "transfer_queue":
            # The queue, and what plays, goes to the target; the source stops.
            source = data["source_player"]
            state, attrs = self.states.get(source, ("idle", {}))
            for entity_id in ids:
                if source in self.queues:
                    self.queues[entity_id] = self.queues[source]
                target_attrs = dict(self.states.get(entity_id, ("idle", {}))[1])
                target_attrs.update(media_title=attrs.get("media_title"), media_artist=attrs.get("media_artist"))
                self.states[entity_id] = ("playing" if data.get("auto_play", True) else state, target_attrs)
            self.queues.pop(source, None)
            self.states[source] = ("idle", {k: v for k, v in attrs.items() if not k.startswith("media_")})
            return None
        for entity_id in ids:
            state, attrs = self.states.get(entity_id, ("idle", {}))
            attrs = dict(attrs)
            if service == "play_media":
                item = next(
                    (i for group in MUSIC_LIBRARY.values() for i in group if i["uri"] == data["media_id"]), None
                ) or {"name": data["media_id"], "uri": data["media_id"]}
                self.queues[entity_id] = item
                artists = item.get("artists") or []
                attrs.update(media_title=item["name"], media_artist=artists[0]["name"] if artists else None)
                state = "playing"
            elif service in ("media_pause", "media_play", "media_stop"):
                state = {"media_pause": "paused", "media_play": "playing", "media_stop": "idle"}[service]
            elif service == "volume_set":
                attrs["volume_level"] = data["volume_level"]
            elif service == "volume_mute":
                attrs["is_volume_muted"] = data["is_volume_muted"]
            self.states[entity_id] = (state, attrs)
        return None

    async def finish_timer(self, entity_id: str, timer_id: str) -> None:
        """What Home Assistant does when a timer's time is up."""
        await self._call_service(
            {
                "domain": "voice_timer_list",
                "service": "finish_timer",
                "target": {"entity_id": entity_id},
                "service_data": {"timer_id": timer_id},
            }
        )


async def serve(host: str = "127.0.0.1", port: int = 0) -> tuple[FakeHass, Any]:
    fake = FakeHass()
    server = await websockets.serve(fake.handle, host, port)
    return fake, server


async def serve_rest(fake: FakeHass, host: str = "127.0.0.1", port: int = 0):
    """The REST API beside the websocket one: ``/api/stt/<entity>``. Returns
    the runner and its port."""
    from aiohttp import web  # pylint: disable=import-outside-toplevel

    async def stt(request: web.Request) -> web.Response:
        if request.headers.get("Authorization") != f"Bearer {TOKEN}":
            return web.Response(status=401)
        audio = await request.read()
        fake.stt_requests.append(
            {"entity_id": request.match_info["entity_id"], "header": request.headers.get("X-Speech-Content"), "bytes": len(audio)}
        )
        return web.json_response({"text": fake.stt_text, "result": "success"})

    application = web.Application()
    application.add_routes([web.post("/api/stt/{entity_id}", stt)])
    runner = web.AppRunner(application)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    return runner, runner.addresses[0][1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8124)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)

    async def run() -> None:
        fake, server = await serve(port=args.port)
        print(f"Fake Home Assistant on ws://127.0.0.1:{args.port}/api/websocket, token {TOKEN!r}")
        try:
            await asyncio.Future()
        finally:
            server.close()

    asyncio.run(run())


if __name__ == "__main__":
    main()
