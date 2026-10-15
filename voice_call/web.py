"""The web UI and its JSON API.

Served through Home Assistant's ingress when running as an app, so every URL
the page uses is relative, and only the ingress proxy may connect. Standalone
it listens on localhost unless told otherwise: the UI can run actions in Home
Assistant with an admin token.
"""

from __future__ import annotations

import copy
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aiohttp import web
from ruamel.yaml import YAML

from adapt_ctc.config import ConfigError

from . import music, timers
from .actions import FAILED, TIMERS_NEED_INTEGRATION
from .commands import DYNAMIC_LISTS, NUMBER_PRESETS, parse_command
from .hass import VOICE_DOMAINS, HassError
from .store import NAME_KINDS, compile_store, dump, plain

if TYPE_CHECKING:
    from .server import VoiceCallApp

_LOGGER = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "web"
LIBRARY_DIR = Path(__file__).parent / "library"
SAFE_ID = re.compile(r"[^a-z0-9_]+")


async def start_web(app: "VoiceCallApp") -> web.AppRunner:
    options = app.options
    allow = set(options.http_allow)

    @web.middleware
    async def only_allowed(request: web.Request, handler):
        if allow and request.remote not in allow:
            raise web.HTTPForbidden(text="Only Home Assistant's ingress may connect")
        return await handler(request)

    @web.middleware
    async def revalidate(request: web.Request, handler):
        # The page is a handful of small files: always check they're current,
        # so an app update never runs a stale script against a new API.
        response = await handler(request)
        if request.path.startswith("/static"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    @web.middleware
    async def errors(request: web.Request, handler):
        try:
            return await handler(request)
        except ConfigError as err:
            return web.json_response({"ok": False, "error": str(err)}, status=400)
        except FileNotFoundError as err:
            return web.json_response({"ok": False, "error": str(err)}, status=404)

    application = web.Application(middlewares=[only_allowed, revalidate, errors], client_max_size=16 * 1024**2)
    api = Api(app)
    application.add_routes(
        [
            web.get("/", api.index),
            web.static("/static", STATIC_DIR, append_version=True),
            web.get("/api/status", api.status),
            web.get("/api/store", api.get_store),
            web.get("/api/commands/{id}", api.get_command),
            web.put("/api/commands/{id}", api.put_command),
            web.delete("/api/commands/{id}", api.delete_command),
            web.post("/api/commands/check", api.check_command),
            web.get("/api/yaml", api.get_yaml),
            web.put("/api/yaml", api.put_yaml),
            web.post("/api/yaml/dump", api.yaml_dump),
            web.post("/api/yaml/parse", api.yaml_parse),
            web.put("/api/names/{kind}/{id}", api.put_name),
            web.delete("/api/names/{kind}/{id}", api.delete_name),
            web.put("/api/lists/{name}", api.put_list),
            web.delete("/api/lists/{name}", api.delete_list),
            web.get("/api/library", api.library),
            web.post("/api/library/add", api.library_add),
            web.get("/api/hass/catalog", api.catalog),
            web.get("/api/satellites", api.satellites),
            web.put("/api/satellites/{key}", api.put_satellite),
            web.post("/api/satellites/{key}/debug", api.debug_session),
            web.get("/api/settings", api.get_settings),
            web.get("/api/voices", api.voices),
            web.get("/api/voices/{entity_id}", api.tts_voices),
            web.put("/api/settings", api.put_settings),
            web.get("/api/calls", api.calls),
            web.get("/api/calls/{id}", api.call),
            web.delete("/api/calls/{id}", api.delete_call),
            web.get("/api/calls/{id}/audio/{name}", api.call_audio),
            web.post("/api/calls/{id}/replay", api.replay),
            web.post("/api/try", api.try_text),
        ]
    )
    runner = web.AppRunner(application, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, options.http_host, options.http_port)
    await site.start()
    _LOGGER.info("Web UI on http://%s:%d/", options.http_host, options.http_port)
    return runner


def load_library() -> list[dict[str, Any]]:
    """Example commands shipped with the app, by file."""
    yaml = YAML(typ="safe")
    entries = []
    for path in sorted(LIBRARY_DIR.glob("*.yaml")):
        data = plain(yaml.load(path.read_text("utf-8")) or {})
        file_lists = data.get("lists") or {}
        for command_id, spec in (data.get("commands") or {}).items():
            # The file's shared value lists this command uses: added with it.
            used = sorted(
                {
                    str(value["list"])
                    for value in (spec.get("lists") or {}).values()
                    if isinstance(value, dict) and value.get("list") in file_lists
                }
                | {str(value) for value in (spec.get("lists") or {}).values() if isinstance(value, str) and value in file_lists}
            )
            entry = {
                "file": path.stem,
                "group": data.get("title") or path.stem,
                "group_icon": data.get("icon"),
                "id": command_id,
                "title": spec.get("title") or command_id,
                "description": spec.get("description", ""),
                "spec": spec,
                "lists": {name: file_lists[name] for name in used},
                "needs": spec.get("needs") or [],
            }
            try:
                # adapt-ctc's own checks too: keywords, names, numbers.
                compile_store(
                    {
                        "lists": entry["lists"],
                        "commands": {command_id: {k: v for k, v in spec.items() if k != "needs"}},
                    }
                )
            except ConfigError as err:
                entry["error"] = str(err)
            entries.append(entry)
    return entries


class Api:
    def __init__(self, app: "VoiceCallApp") -> None:
        self.app = app

    async def index(self, request: web.Request) -> web.StreamResponse:
        return web.FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    # ---------------------------------------------------------------- state

    async def status(self, request: web.Request) -> web.Response:
        app = self.app
        engine = app.engine
        return web.json_response(
            {
                "hass": app.hass.connected.is_set(),
                "store_error": app.store_error,
                "unspeakable": engine.unspeakable if engine else [],
                "build_ms": round(engine.build_seconds * 1000) if engine else None,
                "tdt": bool(engine and engine.transcriber),
                "embedding": bool(engine and engine.embedder),
                "commands": len(engine.compiled.commands) if engine and engine.compiled else 0,
                "speakers": app.speakers is not None,
                "wake_ratio": bool(getattr(app.wake_ratio, "available", False)),
                "integration": app.integration.status,
                "settings": app.store.settings,
                "debug_sessions": {k: s.to_json() for k, s in app.calls.sessions.items()},
                "voice_domains": list(VOICE_DOMAINS),
                "number_presets": sorted(NUMBER_PRESETS),
                "dynamic_lists": DYNAMIC_LISTS,
                # What is said for each outcome when a command has no response for it.
                "outcomes": {
                    "failed": FAILED,
                    "timer_failed": TIMERS_NEED_INTEGRATION,
                    "timer": timers.outcome_examples(),
                    "music": music.EXAMPLES,
                },
            }
        )

    async def get_store(self, request: web.Request) -> web.Response:
        text = self.app.store.read_text()
        data: dict[str, Any] = {}
        error = self.app.store_error
        try:
            data = plain(self.app.store._parse(text))  # pylint: disable=protected-access
        except Exception as err:  # noqa: BLE001 - shown in the UI
            error = str(err)
        commands = data.get("commands") or {}
        details = {}
        for command_id, spec in commands.items():
            try:
                command = parse_command(command_id, spec)
                details[command_id] = {
                    "does": command.behavior.does,
                    "scope": command.scope.to_data(),
                    "overrides": len(command.overrides),
                }
            except ConfigError as err:
                details[command_id] = {"error": str(err)}
        return web.json_response(
            {
                "commands": commands,
                "details": details,
                "names": {kind: (data.get("names") or {}).get(kind) or {} for kind in NAME_KINDS},
                "lists": data.get("lists") or {},
                "error": error,
            }
        )

    async def _saved(self, compiled) -> web.Response:
        await self.app.apply(compiled)
        return web.json_response({"ok": True, "unspeakable": self.app.engine.unspeakable})

    async def get_command(self, request: web.Request) -> web.Response:
        command_id = request.match_info["id"]
        commands = plain(self.app.store._parse(self.app.store.read_text())).get("commands") or {}  # pylint: disable=protected-access
        if command_id not in commands:
            raise FileNotFoundError(f"No command {command_id}")
        return web.json_response(
            {"id": command_id, "spec": commands[command_id], "yaml": _dump(commands[command_id])}
        )

    async def put_command(self, request: web.Request) -> web.Response:
        body = await request.json()
        command_id = request.match_info["id"]
        if body.get("yaml") is not None:
            spec = _load(str(body["yaml"]))
            if not isinstance(spec, dict):
                raise ConfigError("A command is a mapping")
        else:
            spec = _from_form(body.get("spec") or {})
        spec = _clean(spec)
        parse_command(command_id, spec)
        compiled = self.app.store.put_command(command_id, spec, body.get("old_id"))
        return await self._saved(compiled)

    async def check_command(self, request: web.Request) -> web.Response:
        """Validate a command without saving it."""
        body = await request.json()
        spec = _clean(_from_form(body.get("spec") or {}))
        command = parse_command(str(body.get("id") or "draft"), spec)
        return web.json_response({"ok": True, "intent": command.intent, "yaml": _dump(spec)})

    async def yaml_dump(self, request: web.Request) -> web.Response:
        body = await request.json()
        value = body.get("value")
        return web.json_response({"text": "" if value in (None, "", [], {}) else _dump(value)})

    async def yaml_parse(self, request: web.Request) -> web.Response:
        body = await request.json()
        return web.json_response({"value": _load(str(body.get("text") or ""))})

    async def delete_command(self, request: web.Request) -> web.Response:
        return await self._saved(self.app.store.delete_command(request.match_info["id"]))

    async def get_yaml(self, request: web.Request) -> web.Response:
        return web.json_response({"text": self.app.store.read_text()})

    async def put_yaml(self, request: web.Request) -> web.Response:
        body = await request.json()
        try:
            compiled = self.app.store.save_text(str(body.get("text") or ""))
        except ConfigError:
            raise
        except Exception as err:  # noqa: BLE001 - YAML syntax errors
            raise ConfigError(str(err)) from err
        return await self._saved(compiled)

    async def put_name(self, request: web.Request) -> web.Response:
        body = _clean(await request.json())
        name = str(body.get("name") or "").strip()
        aliases = [str(a).strip() for a in body.get("aliases") or [] if str(a).strip()]
        if not name:
            raise ConfigError("A name needs a name")
        spec = {"name": name, **({"aliases": aliases} if aliases else {})}
        compiled = self.app.store.put_name(
            request.match_info["kind"], request.match_info["id"], spec
        )
        return await self._saved(compiled)

    async def delete_name(self, request: web.Request) -> web.Response:
        compiled = self.app.store.put_name(request.match_info["kind"], request.match_info["id"], None)
        return await self._saved(compiled)

    async def put_list(self, request: web.Request) -> web.Response:
        body = _clean(await request.json())
        compiled = self.app.store.put_list(request.match_info["name"], body.get("values") or {})
        return await self._saved(compiled)

    async def delete_list(self, request: web.Request) -> web.Response:
        return await self._saved(self.app.store.put_list(request.match_info["name"], None))

    # -------------------------------------------------------------- library

    async def library(self, request: web.Request) -> web.Response:
        return web.json_response({"entries": load_library()})

    async def library_add(self, request: web.Request) -> web.Response:
        body = await request.json()
        entry = next(
            (
                e
                for e in load_library()
                if e["file"] == body.get("file") and e["id"] == body.get("id")
            ),
            None,
        )
        if entry is None:
            raise FileNotFoundError("No such library command")
        spec = copy.deepcopy(entry["spec"])
        spec.pop("needs", None)
        spec.setdefault("title", entry["title"])
        # Shared lists it uses, unless yours already has one by that name.
        have = self.app.store.load().data.get("lists") or {}
        for name, values in entry["lists"].items():
            if name not in have:
                self.app.store.put_list(name, copy.deepcopy(values))
        existing = self.app.store.load().commands
        base = SAFE_ID.sub("_", str(body.get("new_id") or entry["id"]).lower()).strip("_") or "command"
        command_id, n = base, 2
        while command_id in existing:
            command_id, n = f"{base}_{n}", n + 1
        compiled = self.app.store.put_command(command_id, spec)
        await self.app.apply(compiled)
        return web.json_response({"ok": True, "id": command_id})

    # ------------------------------------------------------- home assistant

    async def catalog(self, request: web.Request) -> web.Response:
        """Search Home Assistant's areas, floors and voice-domain entities.
        Names and aliases come back as suggestions only."""
        kind = request.query.get("kind", "entity")
        query = request.query.get("q", "").strip().lower()
        domain = request.query.get("domain")
        catalog = self.app.hass.catalog
        results: list[dict[str, Any]] = []

        def matches(*texts: str | None) -> bool:
            return not query or any(query in (t or "").lower() for t in texts)

        if kind == "area":
            for area in catalog.areas.values():
                floor = catalog.floors.get(area.floor_id) if area.floor_id else None
                if matches(area.area_id, area.name, *area.aliases):
                    results.append(
                        {
                            "id": area.area_id,
                            "name": area.name,
                            "aliases": area.aliases,
                            "floor": floor.name if floor else None,
                        }
                    )
        elif kind == "floor":
            for floor in catalog.floors.values():
                if matches(floor.floor_id, floor.name, *floor.aliases):
                    results.append({"id": floor.floor_id, "name": floor.name, "aliases": floor.aliases})
        else:
            for entity in catalog.entities.values():
                if domain and entity.domain != domain:
                    continue
                area = catalog.areas.get(entity.area_id) if entity.area_id else None
                if matches(entity.entity_id, entity.name, *entity.aliases, area.name if area else None):
                    results.append(
                        {
                            "id": entity.entity_id,
                            "name": entity.name,
                            "aliases": entity.aliases,
                            "domain": entity.domain,
                            "area": area.name if area else None,
                            "area_id": entity.area_id,
                        }
                    )
        results.sort(key=lambda r: (r.get("name") or "").lower())
        return web.json_response({"results": results[:200], "connected": self.app.hass.connected.is_set()})

    # ----------------------------------------------------------- satellites

    async def satellites(self, request: web.Request) -> web.Response:
        app = self.app
        keys = list(dict.fromkeys([*app.store.satellites, *app.sessions]))
        out = []
        for key in keys:
            stored = app.store.satellite(key)
            session = app.sessions.get(key)
            satellite = app.satellite(key)
            out.append(
                {
                    "key": key,
                    "name": satellite.name,
                    "area": satellite.area_id,
                    "device_id": satellite.device_id,
                    "timer_list": satellite.timer_list,
                    "timers": app.integration.running(key),
                    "media_player": satellite.media_player,
                    # As stored (absent: as every satellite) and as it comes out.
                    "voice": {k: stored.get(k) for k in ("stt", "tts", "tts_voice", "speak_on") if k in stored},
                    "voice_used": satellite.voice.__dict__,
                    "device_media_player": satellite.device_media_player,
                    # Picked here, or else found: its Sendspin client's player.
                    "media_player_picked": bool(stored.get("media_player")),
                    "music_players": [
                        {"entity_id": e.entity_id, "name": e.name, "area": e.area_id}
                        for e in app.hass.catalog.music_players()
                    ],
                    "ringing": bool(session and session.ringing and not session.ringing.done()),
                    "area_name": satellite.area_name,
                    "floor_name": satellite.floor_name,
                    "esphome_name": stored.get("device_id") or (session.device_id if session else None),
                    "connected": bool(session and session.writer is not None),
                    "host": session.host if session else None,
                    "in_call": bool(session and session.call is not None),
                    "debug": app.calls.sessions[key].to_json() if key in app.calls.sessions else None,
                }
            )
        return web.json_response({"satellites": out, "integration": app.integration.status})

    async def put_satellite(self, request: web.Request) -> web.Response:
        key = request.match_info["key"]
        body = await request.json()
        entry = self.app.store.satellites.setdefault(key, {})
        if "name" in body:
            entry["name"] = str(body["name"] or "").strip() or key
        if "area" in body:
            area = str(body["area"]) if body["area"] else None
            link = self.app.integration.links.get(key)
            if link is not None and link.device_id:
                # The satellite's Home Assistant device holds its area.
                await self.app.hass.update_device_area(link.device_id, area)
            elif area:
                entry["area"] = area
            else:
                entry.pop("area", None)
        # STT and TTS of its own: an entity, "builtin", or empty for what every
        # satellite uses. speak_on: a media player for its responses.
        for key, domain in (("stt", "stt."), ("tts", "tts."), ("tts_voice", ""), ("speak_on", "media_player.")):
            if key in body:
                value = str(body[key] or "")
                if value and value != "builtin" and not value.startswith(domain):
                    raise ConfigError(f"{key}: expected a {domain.rstrip('.')} entity")
                if value:
                    entry[key] = value
                else:
                    entry.pop(key, None)
        if "media_player" in body:
            # Empty: the player found for its Sendspin client.
            if body["media_player"]:
                entry["media_player"] = str(body["media_player"])
            else:
                entry.pop("media_player", None)
        self.app.store.save_satellites()
        return web.json_response({"ok": True})

    async def debug_session(self, request: web.Request) -> web.Response:
        key = request.match_info["key"]
        body = await request.json()
        if body.get("active", True):
            session = self.app.calls.start_session(key, bool(body.get("dry_run", True)))
            return web.json_response({"ok": True, "session": session.to_json()})
        self.app.calls.stop_session(key)
        return web.json_response({"ok": True, "session": None})

    async def voices(self, request: web.Request) -> web.Response:
        """Home Assistant's STT and TTS entities that work in English, and the
        media players responses can be spoken on."""
        out: dict[str, Any] = {"stt": [], "tts": [], "error": None}
        try:
            out.update(await self.app.voices.engines())
        except (HassError, ConnectionError, OSError, TimeoutError) as err:
            out["error"] = str(err)
        out["media_players"] = sorted(
            (
                {"entity_id": e.entity_id, "name": e.name, "area": e.area_id}
                for e in self.app.hass.catalog.entities.values()
                if e.domain == "media_player"
            ),
            key=lambda m: m["name"].lower(),
        )
        return web.json_response(out)

    async def tts_voices(self, request: web.Request) -> web.Response:
        entity_id = request.match_info["entity_id"]
        if not entity_id.startswith("tts."):
            raise ConfigError("Expected a tts entity")
        try:
            voices = await self.app.voices.voices(entity_id)
        except (HassError, ConnectionError, OSError, TimeoutError) as err:
            return web.json_response({"voices": [], "error": str(err)})
        return web.json_response({"voices": [{"id": v.get("voice_id"), "name": v.get("name")} for v in voices]})

    async def get_settings(self, request: web.Request) -> web.Response:
        return web.json_response(self.app.store.settings)

    async def put_settings(self, request: web.Request) -> web.Response:
        body = await request.json()
        settings = self.app.store.settings
        if "keep_audio" in body:
            settings["keep_audio"] = bool(body["keep_audio"])
        if "max_saved_calls" in body:
            settings["max_saved_calls"] = max(1, int(body["max_saved_calls"]))
        # Home Assistant STT and TTS for every satellite: an entity, or none
        # for the built-in models.
        for key, domain in (("stt", "stt."), ("tts", "tts."), ("tts_voice", "")):
            if key in body:
                value = str(body[key] or "")
                if value and not value.startswith(domain):
                    raise ConfigError(f"{key}: expected a {domain.rstrip('.')} entity")
                if value:
                    settings[key] = value
                else:
                    settings.pop(key, None)
        if "tts" in body and not settings.get("tts"):
            settings.pop("tts_voice", None)
        self.app.store.save_settings()
        return web.json_response(settings)

    # ---------------------------------------------------------------- calls

    async def calls(self, request: web.Request) -> web.Response:
        return web.json_response({"calls": self.app.calls.list(request.query.get("satellite"))})

    async def call(self, request: web.Request) -> web.Response:
        record = self.app.calls.get(request.match_info["id"])
        if record is None:
            raise FileNotFoundError("No such call")
        return web.json_response(record.to_json(), dumps=_dumps)

    async def delete_call(self, request: web.Request) -> web.Response:
        self.app.calls.delete(request.match_info["id"])
        return web.json_response({"ok": True})

    async def call_audio(self, request: web.Request) -> web.StreamResponse:
        path = self.app.calls.audio_path(request.match_info["id"], request.match_info["name"])
        if path is None:
            raise FileNotFoundError("No such audio")
        return web.FileResponse(path, headers={"Content-Type": "audio/wav"})

    async def replay(self, request: web.Request) -> web.Response:
        body = await _json_or_empty(request)
        record = await self.app.replay(request.match_info["id"], bool(body.get("run_actions")))
        return web.json_response(record.to_json(), dumps=_dumps)

    async def try_text(self, request: web.Request) -> web.Response:
        body = await request.json()
        if not str(body.get("text") or "").strip():
            raise ConfigError("Type something to try")
        record = await self.app.try_text(
            str(body.get("text") or ""), body.get("satellite") or None, bool(body.get("run_actions"))
        )
        return web.json_response(record.to_json(), dumps=_dumps)


def _dump(value: Any) -> str:
    return dump(value)


def _load(text: str) -> Any:
    try:
        return plain(YAML().load(text)) if text.strip() else None
    except Exception as err:  # noqa: BLE001 - YAML syntax, shown in the UI
        raise ConfigError(f"YAML: {err}") from err


def _from_form(spec: dict[str, Any]) -> dict[str, Any]:
    """The editor sends actions as YAML text (``action_yaml``), for the
    command and each override."""
    spec = dict(spec)

    def take(target: dict[str, Any]) -> None:
        if "action_yaml" in target:
            text = target.pop("action_yaml")
            target.pop("action", None)
            if str(text or "").strip():
                target["action"] = _load(str(text))

    take(spec)
    spec["overrides"] = [dict(o) for o in spec.get("overrides") or []]
    for override in spec["overrides"]:
        take(override)
    return spec


def _clean(value: Any) -> Any:
    """Drop empty values the form sends for fields left blank."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            item = _clean(item)
            if item in (None, "", [], {}):
                continue
            out[key] = item
        return out
    if isinstance(value, list):
        return [item for item in (_clean(v) for v in value) if item not in (None, "", [], {})]
    return value


async def _json_or_empty(request: web.Request) -> dict[str, Any]:
    if not request.can_read_body:
        return {}
    try:
        return await request.json()
    except ValueError:
        return {}


def _dumps(data: Any) -> str:
    import json  # pylint: disable=import-outside-toplevel

    return json.dumps(data, default=str)
