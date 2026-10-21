"""The web UI and its JSON API.

Served through Home Assistant's ingress when running as an app, so every URL
the page uses is relative, and only the ingress proxy may connect. Standalone
it listens on localhost unless told otherwise: the UI can run actions in Home
Assistant with an admin token.
"""

from __future__ import annotations

import asyncio
import copy
import functools
import json
import logging
import math
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aiohttp import web
from ruamel.yaml import YAML

from adapt_ctc.config import ConfigError
from adapt_ctc.template import TemplateError, expand

from . import languages, music, timers
from .endpoint import parse as parse_end_of_speech
from .commands import COMMAND_ID, DYNAMIC_LISTS, FALLBACK_STEPS, ICON, NUMBER_PRESETS, VERBOSITY, parse_command
from .fallback import needs_heard_slots
from .hass import VOICE_DOMAINS, HassError
from .skills import LEVELS, SKILLS, available, home_skills, own_skills
from .skills import answer as skill_answer
from .skills import load as load_skill_settings
from .store import NAME_KINDS, command_language, compile_store, dump, file_language, language_name, plain
from .protocol import SAMPLE_RATE
from .voices import language_code
from .pronounce import guess, model_phones
from .wake import THRESHOLDS
from .wake import ConfigError as WakeConfigError
from .wake_words import MAX_CUTOFF, MAX_WAKE_WORDS, SPECIAL

if TYPE_CHECKING:
    from .server import VoiceCallApp

_LOGGER = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "web"
def library_dir() -> Path:
    """The command library: the app's language's (voice_call/languages)."""
    return languages.current().library
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
            web.post("/api/examples", api.examples),
            web.post("/api/commands/{id}/duplicate", api.duplicate_command),
            web.get("/api/yaml", api.get_yaml),
            web.put("/api/yaml", api.put_yaml),
            web.post("/api/yaml/dump", api.yaml_dump),
            web.put("/api/names/{kind}/{id}", api.put_name),
            web.delete("/api/names/{kind}/{id}", api.delete_name),
            web.put("/api/lists/{name}", api.put_list),
            web.put("/api/unknown", api.put_unknown),
            web.put("/api/fallback", api.put_fallback),
            web.get("/api/skills", api.skills),
            web.put("/api/skills/{id}", api.put_skill),
            web.delete("/api/lists/{name}", api.delete_list),
            web.get("/api/library", api.library),
            web.post("/api/library/add", api.library_add),
            web.get("/api/hass/catalog", api.catalog),
            web.get("/api/satellites", api.satellites),
            web.put("/api/satellites/{key}", api.put_satellite),
            web.post("/api/satellites/{key}/debug", api.debug_session),
            web.get("/api/satellites/{key}/live", api.live),
            web.get("/api/settings", api.get_settings),
            web.get("/api/wake_words", api.wake_words),
            web.post("/api/wake_words", api.add_wake_word),
            web.put("/api/wake_words/{id}", api.put_wake_word),
            web.post("/api/wake_words/{id}/check", api.check_wake_word),
            web.delete("/api/wake_words/{id}", api.delete_wake_word),
            web.get("/api/voices", api.voices),
            web.get("/api/agents", api.agents),
            web.get("/api/voices/{entity_id}", api.tts_voices),
            web.put("/api/settings", api.put_settings),
            web.get("/api/calls", api.calls),
            web.get("/api/calls/{id}", api.call),
            web.delete("/api/calls/{id}", api.delete_call),
            web.get("/api/calls/{id}/audio/{name}", api.call_audio),
            web.post("/api/calls/{id}/replay", api.replay),
            web.post("/api/calls/{id}/speaker", api.add_voice),
            web.get("/api/speakers", api.speakers),
            web.post("/api/try", api.try_text),
        ]
    )
    runner = web.AppRunner(application, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, options.http_host, options.http_port)
    await site.start()
    _LOGGER.info("Web UI on http://%s:%d/", options.http_host, options.http_port)
    return runner


VARIANTS = {
    "here": "on this satellite",
    "device": "on a device by name",
    "place": "in an area or floor by name",
}
"""What a library family's versions act on, in the order the library shows
them: the satellite it is said to (nothing named), a device named, or an area
or floor named."""

LIBRARY_ONLY = ("needs", "variants", "formerly")
"""Library keys that are not the command's: not added with it."""


def library_files() -> list[tuple[Path, dict[str, Any]]]:
    yaml = YAML(typ="safe")
    return [(path, plain(yaml.load(path.read_text("utf-8")) or {})) for path in sorted(library_dir().glob("*.yaml"))]


def library_versions(family_id: str, spec: dict[str, Any]) -> list[tuple[str, str | None, dict[str, Any]]]:
    """A library command's versions, ``[(id, variant, spec)]``.

    With ``variants`` it is a family: one version for each of ``here``,
    ``device`` and ``place`` it has (id ``<family>_<variant>``), each the
    family's keys with the variant's own on top -- a null one removes the
    family's. Else it is its own only version. ``needs`` stays in the spec;
    ``variants`` and ``formerly`` (ids the family once had) do not."""
    base = {k: v for k, v in spec.items() if k not in ("variants", "formerly")}
    variants = spec.get("variants")
    if not variants:
        return [(family_id, None, base)]
    unknown = sorted(set(variants) - set(VARIANTS))
    if unknown:
        raise ConfigError(f"Library command {family_id}: no variant {', '.join(unknown)} (one of {', '.join(VARIANTS)})")
    out = []
    for variant in VARIANTS:
        if variant not in variants:
            continue
        merged = {**base, **(variants[variant] or {})}
        out.append((f"{family_id}_{variant}", variant, copy.deepcopy({k: v for k, v in merged.items() if v is not None})))
    return out


def load_library() -> list[dict[str, Any]]:
    """Example commands shipped with the app, by file: one entry for each
    version of each command (``family``, ``variant``: see
    library_versions)."""
    entries = []
    for path, data in library_files():
        file_lists = data.get("lists") or {}
        for family_id, family in (data.get("commands") or {}).items():
            try:
                versions = library_versions(family_id, family)
            except ConfigError as err:
                versions = [(family_id, None, {k: v for k, v in family.items() if k not in ("variants", "formerly")})]
                failed = str(err)
            else:
                failed = None
            for command_id, variant, spec in versions:
                entries.append(_library_entry(path.stem, data, file_lists, family_id, family, command_id, variant, spec, failed))
    return entries


def _library_entry(
    file: str,
    data: dict[str, Any],
    file_lists: dict[str, Any],
    family_id: str,
    family: dict[str, Any],
    command_id: str,
    variant: str | None,
    full: dict[str, Any],
    failed: str | None,
) -> dict[str, Any]:
    spec = {k: v for k, v in full.items() if k not in LIBRARY_ONLY}
    # The file's shared value lists this command uses: added with it.
    used = sorted(
        {
            str(value["list"])
            for value in (spec.get("lists") or {}).values()
            if isinstance(value, dict) and value.get("list") in file_lists
        }
        | {str(value) for value in (spec.get("lists") or {}).values() if isinstance(value, str) and value in file_lists}
    )
    family_title = family.get("title") or family_id
    if variant:
        # Its own, else the family's with what it acts on.
        title = ((family["variants"].get(variant) or {}).get("title")) or f"{family_title} {VARIANTS[variant]}"
    else:
        title = spec.get("title") or command_id
    entry = {
        "file": file,
        "group": data.get("title") or file,
        "group_icon": data.get("icon"),
        "id": command_id,
        "title": title,
        "description": spec.get("description", ""),
        "family": family_id,
        "family_title": family_title,
        "family_icon": family.get("icon"),
        "family_description": family.get("description", ""),
        "variant": variant,
        "spec": spec,
        "lists": {name: file_lists[name] for name in used},
        "needs": full.get("needs") or [],
    }
    if failed:
        entry["error"] = failed
        return entry
    try:
        # adapt-ctc's own checks too: keywords, names, numbers.
        compile_store({"lists": entry["lists"], "commands": {command_id: spec}})
    except ConfigError as err:
        entry["error"] = str(err)
    return entry


@functools.lru_cache(maxsize=1)
def library_sections() -> tuple[list[dict[str, Any]], dict[str, str]]:
    """The library's files in order -- ``{file, title, icon}`` -- and which
    file each library command id is in. The library ships with the app."""
    sections, ids = [], {}
    for path, data in library_files():
        sections.append({"file": path.stem, "title": data.get("title") or path.stem, "icon": data.get("icon")})
        for family_id, family in (data.get("commands") or {}).items():
            # Each version's id, and the family's own and former ones: a
            # command added before the library had versions.
            versions = [str(v) for v in (family.get("variants") or {})]
            for command_id in [family_id, *(f"{family_id}_{v}" for v in versions), *(family.get("formerly") or [])]:
                ids.setdefault(str(command_id), path.stem)
    return sections, ids


def library_of(command_id: str, spec: dict[str, Any]) -> str | None:
    """The library file a command came from: its ``library``, else the one
    with its id (also as a numbered or copied one, ``lights_on_2``)."""
    sections, ids = library_sections()
    if spec.get("library") in {section["file"] for section in sections}:
        return str(spec["library"])
    return ids.get(command_id) or ids.get(re.sub(r"(_copy)?(_\d+)?$", "", command_id))


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
                "tdt_models": app.tdt_models(),
                "tdt_choice": app.tdt_choice(),
                "language": app.language.to_json(),
                "fallback": app.fallback_status(),
                "agent": app.agent_status(),
                "tdt_status": app.tdt_status,
                "wake_ratio": sorted(app.wake_ratios),
                "integration": app.integration.status,
                "settings": app.store.settings,
                "debug_sessions": {k: s.to_json() for k, s in app.calls.sessions.items()},
                "voice_domains": list(VOICE_DOMAINS),
                "number_presets": sorted(NUMBER_PRESETS),
                "dynamic_lists": DYNAMIC_LISTS,
                # What is said for each outcome when a command has no response for it.
                "outcomes": {
                    "failed": app.language.speech.FAILED,
                    "timer_failed": app.language.speech.TIMERS_NEED_INTEGRATION,
                    "timer": timers.outcome_examples(),
                    "music": music.examples(),
                    # ... and when done, by level of verbosity.
                    "timer_levels": timers.level_examples(),
                    "music_levels": music.level_examples(),
                },
                "verbosity": {"levels": list(VERBOSITY), "beep_elsewhere": app.beep_elsewhere()},
                "end_of_speech": app.end_of_speech_status(),
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
        # The app's language's commands; the others are kept, not shown.
        here, default = languages.current().code, file_language(data)
        every = data.get("commands") or {}
        commands = {k: v for k, v in every.items() if command_language(v, default) == here}
        hidden: dict[str, int] = {}
        for spec in every.values():
            said = command_language(spec, default)
            if said != here:
                hidden[said] = hidden.get(said, 0) + 1
        details = {}
        for command_id, spec in commands.items():
            try:
                command = parse_command(command_id, spec)
                details[command_id] = {
                    "library": library_of(command_id, spec),
                    # Its satellite's key; None: the Home's.
                    "satellite": command.satellite,
                    "does": command.behavior.does,
                    # Meaning can't run it: it needs a name, number, value or text heard.
                    "needs_said": needs_heard_slots(command),
                    # How much it says when done, and whether it only answers.
                    "verbosity": command.behavior.level,
                    "default_verbosity": command.behavior.default_verbosity,
                    "question": command.behavior.question,
                }
            except ConfigError as err:
                details[command_id] = {
                    "error": str(err),
                    "library": library_of(command_id, spec if isinstance(spec, dict) else {}),
                    "satellite": spec.get("satellite") if isinstance(spec, dict) else None,
                }
        return web.json_response(
            {
                "commands": commands,
                "details": details,
                # Where the Home and satellite pages list them: the library's sections.
                "sections": library_sections()[0],
                "names": {kind: (data.get("names") or {}).get(kind) or {} for kind in NAME_KINDS},
                "lists": data.get("lists") or {},
                # What is said when nothing understood a sentence.
                "unknown": data.get("unknown") or {},
                # Commands in other languages, by language: how many.
                "hidden": [{"code": code, "name": language_name(code), "count": n} for code, n in sorted(hidden.items())],
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
        if spec.get("satellite"):
            self._satellite_key(spec["satellite"])
        compiled = self.app.store.put_command(command_id, spec, body.get("old_id"))
        return await self._saved(compiled)

    async def duplicate_command(self, request: web.Request) -> web.Response:
        """A copy of a command (``new_id``, ``title``) on ``satellite`` (its
        key), or the Home's (null)."""
        command_id = request.match_info["id"]
        body = await _json_or_empty(request)
        new_id = str(body.get("new_id") or f"{command_id}_copy").strip()
        if not COMMAND_ID.match(new_id):
            raise ConfigError("Id: lowercase letters, digits and underscores")
        compiled = self.app.store.duplicate_command(
            command_id,
            new_id,
            str(body.get("title") or "").strip() or None,
            self._satellite_key(body.get("satellite")),
        )
        await self.app.apply(compiled)
        return web.json_response({"ok": True, "id": new_id})

    async def check_command(self, request: web.Request) -> web.Response:
        """Validate a command without saving it."""
        body = await request.json()
        spec = _clean(_from_form(body.get("spec") or {}))
        command = parse_command(str(body.get("id") or "draft"), spec)
        return web.json_response({"ok": True, "intent": command.intent, "yaml": _dump(spec)})

    async def examples(self, request: web.Request) -> web.Response:
        """What example sentences stand for: each template's sentences (the
        first few), or what is wrong with it."""
        body = await request.json()
        out = []
        for text in body.get("examples") or []:
            text = str(text)
            try:
                sentences = expand(text)
            except TemplateError as err:
                out.append({"example": text, "error": str(err)})
                continue
            out.append({"example": text, "count": len(sentences), "sentences": sentences[:20]})
        return web.json_response({"examples": out})

    async def yaml_dump(self, request: web.Request) -> web.Response:
        body = await request.json()
        value = body.get("value")
        return web.json_response({"text": "" if value in (None, "", [], {}) else _dump(value)})

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

    async def put_unknown(self, request: web.Request) -> web.Response:
        """The Unknown command's ``response`` template; empty: the built-in."""
        body = await request.json()
        response = str(body.get("response") or "").strip()
        return await self._saved(self.app.store.put_unknown(response or None))

    async def put_fallback(self, request: web.Request) -> web.Response:
        """Which commands each fallback step may run: ``{meaning: [ids],
        llm: [ids]}``, exactly; a step left out is left as it is."""
        body = await request.json()
        steps = {}
        for step in FALLBACK_STEPS:
            if step in body:
                if not isinstance(body[step], list):
                    raise ConfigError(f"{step}: expected a list of command ids")
                steps[step] = [str(c) for c in body[step]]
        return await self._saved(self.app.store.put_fallback(steps))

    # --------------------------------------------------------------- skills

    async def skills(self, request: web.Request) -> web.Response:
        """Every skill -- how it answers (``responses``: its level and your
        templates, as set), its settings (``settings``, in full; and
        ``settings_error``, what is wrong with those stored, when they can't
        be used) and what it says at each level for its first example
        (``example``) -- the Home's (each unless taken off it), and each
        satellite's own (given to it)."""
        store = self.app.store
        configured = store.settings.get("skill_responses") or {}
        stored = store.settings.get("skill_settings") or {}
        out = []
        for skill in available().values():
            item = skill.to_json()
            item["responses"] = configured.get(skill.id) or {}
            settings, error = load_skill_settings(skill.id, stored.get(skill.id))
            item["settings"] = settings.to_json()
            item["settings_set"] = stored.get(skill.id) is not None
            if error:
                item["settings_error"] = error
            found = skill_answer(skill.examples[0], frozenset({skill.id}), {skill.id: settings})
            item["example"] = found.to_json() if found is not None else None
            out.append(item)
        return web.json_response(
            {
                "skills": out,
                "levels": list(LEVELS),
                "home": sorted(home_skills(store.settings.get("skills"))),
                "satellites": {key: sorted(own_skills(entry.get("skills"))) for key, entry in store.satellites.items()},
            }
        )

    async def put_skill(self, request: web.Request) -> web.Response:
        """Put a skill on the Home or take it off (``home``: true or false),
        give it to satellites or take it from them (``satellites``:
        ``{key: true | false}``), how it answers (``verbosity``: a level,
        null for the default; ``response``: a template for every level, or
        ``{brief, specific, verbose}``, empty for the built-in sentences),
        and its settings (``settings``: in full, as GET gives them; null for
        the built-in ones)."""
        skill_id = request.match_info["id"]
        if skill_id not in available():
            raise FileNotFoundError(f"No skill {skill_id}")
        body = await request.json()
        store = self.app.store
        if "settings" in body:
            try:
                stored = None if body["settings"] is None else SKILLS[skill_id].settings.store(body["settings"])
            except ValueError as err:
                raise ConfigError(f"{SKILLS[skill_id].name}: {err}") from err
            settings = store.settings.setdefault("skill_settings", {})
            if stored is None:
                settings.pop(skill_id, None)
            else:
                settings[skill_id] = stored
            if not settings:
                store.settings.pop("skill_settings")
            store.save_settings()
            self.app.configure_skills()
        if "verbosity" in body or "response" in body:
            configured = store.settings.setdefault("skill_responses", {})
            entry = dict(configured.get(skill_id) or {})
            if "verbosity" in body:
                level = body["verbosity"]
                if level in (None, ""):
                    entry.pop("verbosity", None)
                elif level not in LEVELS:
                    raise ConfigError(f"verbosity: one of {', '.join(LEVELS)}")
                else:
                    entry["verbosity"] = level
            if "response" in body:
                response = body["response"]
                if isinstance(response, dict):
                    unknown = set(response) - set(LEVELS)
                    if unknown:
                        raise ConfigError(f"response: by level ({', '.join(LEVELS)}), not {sorted(unknown)}")
                    response = {k: str(v).strip() for k, v in response.items() if str(v or "").strip()}
                elif response is not None and not isinstance(response, str):
                    raise ConfigError("response: a template, or one for each level")
                if isinstance(response, str):
                    response = response.strip()
                if response:
                    entry["response"] = response
                else:
                    entry.pop("response", None)
            if entry:
                configured[skill_id] = entry
            else:
                configured.pop(skill_id, None)
            if not configured:
                store.settings.pop("skill_responses", None)
            store.save_settings()
        if "home" in body:
            home = store.settings.setdefault("skills", {})
            if body["home"] is False:
                home[skill_id] = False
            else:
                home.pop(skill_id, None)
            if not home:
                store.settings.pop("skills")
            store.save_settings()
        satellites = body.get("satellites") or {}
        if not isinstance(satellites, dict):
            raise ConfigError("satellites: expected {satellite: true or false}")
        for key, on in satellites.items():
            if on is not None and not isinstance(on, bool):
                raise ConfigError(f"satellites.{key}: true or false")
            entry = store.satellites.setdefault(self._satellite_key(key) or "", {})
            own = {k: True for k in own_skills(entry.get("skills"))}
            if on:
                own[skill_id] = True
            else:
                own.pop(skill_id, None)
            if own:
                entry["skills"] = own
            else:
                entry.pop("skills", None)
        if satellites:
            store.save_satellites()
        return web.json_response({"ok": True})

    def _satellite_key(self, value: Any) -> str | None:
        """A satellite's key, or None (the Home)."""
        if value in (None, "", "home"):
            return None
        key = str(value)
        if key not in self.app.store.satellites and key not in self.app.sessions:
            raise ConfigError(f"No satellite {key!r}")
        return key

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
        spec["title"] = entry["title"]
        spec["library"] = entry["file"]
        satellite = self._satellite_key(body.get("satellite"))
        if satellite:
            spec["satellite"] = satellite
        # Shared lists it uses, unless yours already has one by that name.
        have = self.app.store.load().data.get("lists") or {}
        for name, values in entry["lists"].items():
            if name not in have:
                self.app.store.put_list(name, copy.deepcopy(values))
        # Every command's id, in any language: another's keeps its own.
        compiled = self.app.store.load()
        existing = {*compiled.commands, *compiled.hidden}
        base = SAFE_ID.sub("_", str(body.get("new_id") or entry["id"]).lower()).strip("_") or "command"
        if base in compiled.hidden:
            base = f"{base}_{languages.current().code}"  # turn_on_here_de, beside English's turn_on_here
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
        compiled = app.engine.compiled if app.engine is not None else None
        own_commands: dict[str, list[str]] = {}
        for command in (compiled.commands.values() if compiled is not None else ()):
            if command.satellite:
                own_commands.setdefault(command.satellite, []).append(command.id)
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
                    # Its own pick (absent: as every satellite), and what it
                    # listens for: what was pushed, or what will be.
                    "wake_words": stored.get("wake_words"),
                    "icon": stored.get("icon"),
                    # Whether it takes the Home's commands and skills, and
                    # its own: commands (by id) and skills.
                    "home": satellite.home,
                    "commands": own_commands.get(key, []),
                    "skills": sorted(own_skills(stored.get("skills"))),
                    "skills_used": sorted(satellite.skills or ()),
                    "wake_words_used": (
                        session.wake_words if session and session.wake_words else app.satellite_wake_words(key)
                    ),
                    "model_slots": (session.hello.get("model_slots") if session else None),
                    # As stored (absent: as every satellite) and as it comes out.
                    "voice": {
                        k: stored.get(k)
                        for k in ("stt", "tts", "tts_voice", "speak_on", "stt_language", "tts_language", "stt_audio", "stt_always")
                        if k in stored
                    },
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
                    # Its firmware can stream outside calls: it can be listened to live.
                    "can_listen": bool(session and session.hello.get("monitor")),
                    "listeners": len(session.listeners) if session else 0,
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
        for option in ("stt_language", "tts_language"):
            if option in body:
                value = _language(body[option], option)
                if value:
                    entry[option] = value
                else:
                    entry.pop(option, None)
        for option, domain in (("stt", "stt."), ("tts", "tts."), ("tts_voice", ""), ("speak_on", "media_player.")):
            if option in body:
                value = str(body[option] or "")
                if value and value != "builtin" and not value.startswith(domain):
                    raise ConfigError(f"{option}: expected a {domain.rstrip('.')} entity")
                if value:
                    entry[option] = value
                else:
                    entry.pop(option, None)
        if "stt_audio" in body:
            entry_audio = _stt_audio(body["stt_audio"])
            if entry_audio:
                entry["stt_audio"] = entry_audio
            else:
                entry.pop("stt_audio", None)
        if "stt_always" in body:
            # True or false of its own; null: as every satellite.
            always = _stt_always(body["stt_always"])
            if always is None:
                entry.pop("stt_always", None)
            else:
                entry["stt_always"] = always
        if "icon" in body:
            icon = str(body["icon"] or "")
            if icon and not ICON.match(icon):
                raise ConfigError("icon: a Material Design Icon, like mdi:sofa")
            if icon:
                entry["icon"] = icon
            else:
                entry.pop("icon", None)
        if "home" in body:
            # Absent: it takes the Home's commands and skills.
            if body["home"] is False:
                entry["home"] = False
            else:
                entry.pop("home", None)
        if "wake_words" in body:
            # Empty: as every satellite.
            picked = self._wake_word_ids(body["wake_words"])
            if picked:
                entry["wake_words"] = picked
            else:
                entry.pop("wake_words", None)
            self.app.wake_words_changed(key)
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

    async def live(self, request: web.Request) -> web.WebSocketResponse:
        """Listen to a satellite live, over a WebSocket: its microphone as
        16 kHz 16-bit mono PCM (binary messages, about a tenth of a second
        each) and JSON for what happens -- ``start``, ``wake`` (each model's
        probabilities, and wake words detected, which it does not answer
        meanwhile), ``call`` (a call that was going on ends), ``end``,
        ``error``. The satellite streams while someone listens, and shows
        that it does."""
        key = request.match_info["key"]
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        try:
            queue = await self.app.listen(key)
        except (ConnectionError, ConfigError) as err:
            await ws.send_json({"type": "error", "error": str(err)})
            await ws.close()
            return ws

        async def forward() -> None:
            await ws.send_json({"type": "start", "sample_rate": SAMPLE_RATE})
            while True:
                # What has piled up goes at once, audio in one message.
                batch = [await queue.get()]
                while not queue.empty():
                    batch.append(queue.get_nowait())
                audio: list[bytes] = []
                for item in [*batch, b""]:
                    if isinstance(item, bytes):
                        audio.append(item)
                        if item:
                            continue
                    if any(audio):
                        await ws.send_bytes(b"".join(audio))
                    audio = []
                    if item is None:
                        await ws.send_json({"type": "end", "why": "The satellite disconnected"})
                        await ws.close()
                        return
                    if isinstance(item, dict):
                        await ws.send_json(item)

        sender = asyncio.ensure_future(forward())
        try:
            async for _message in ws:
                pass  # the page says nothing; it closes the socket to stop
        finally:
            sender.cancel()
            await self.app.stop_listening(key, queue)
        return ws

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
            voices = await self.app.voices.voices(entity_id, request.query.get("language") or None)
        except (HassError, ConnectionError, OSError, TimeoutError) as err:
            return web.json_response({"voices": [], "error": str(err)})
        return web.json_response({"voices": [{"id": v.get("voice_id"), "name": v.get("name")} for v in voices]})

    async def agents(self, request: web.Request) -> web.Response:
        """Home Assistant's conversation agents, for the Settings page."""
        try:
            return web.json_response({"agents": await self.app.agent.agents()})
        except (HassError, ConnectionError, OSError, TimeoutError) as err:
            return web.json_response({"agents": [], "error": str(err) or type(err).__name__})

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
        for option in ("stt_language", "tts_language"):
            if option in body:
                value = _language(body[option], option)
                if value:
                    settings[option] = value
                else:
                    settings.pop(option, None)
        if "stt_audio" in body:
            audio = _stt_audio(body["stt_audio"])
            if audio:
                settings["stt_audio"] = audio
            else:
                settings.pop("stt_audio", None)
        if "stt_always" in body:
            if _stt_always(body["stt_always"]):
                settings["stt_always"] = True
            else:
                settings.pop("stt_always", None)
        if "tts" in body and not settings.get("tts"):
            settings.pop("tts_voice", None)
            settings.pop("tts_language", None)
        if "stt" in body and not settings.get("stt"):
            settings.pop("stt_language", None)
        fallback_changed = False
        for option in ("fallback_meaning", "fallback_llm"):
            if option in body:
                settings[option] = bool(body[option])
                fallback_changed = True
        if "fallback_top_k" in body:
            settings["fallback_top_k"] = max(1, min(int(body["fallback_top_k"]), 64))
            fallback_changed = True
        if "fallback_model" in body:
            from .fallback import GLINER_MODELS  # pylint: disable=import-outside-toplevel

            if str(body["fallback_model"]) not in GLINER_MODELS:
                raise ConfigError(f"fallback_model: one of {', '.join(GLINER_MODELS)}")
            settings["fallback_model"] = str(body["fallback_model"])
            fallback_changed = True
        if "fallback_threshold" in body:
            try:
                threshold = float(body["fallback_threshold"])
            except (TypeError, ValueError) as err:
                raise ConfigError("fallback_threshold: a number from 0 to 1") from err
            if not 0.0 <= threshold <= 1.0:
                raise ConfigError("fallback_threshold: a number from 0 to 1")
            settings["fallback_threshold"] = threshold
            fallback_changed = True
        # End of speech: each value set, or null for the default under it;
        # null for the whole of it restores every default.
        if "end_of_speech" in body:
            value = body["end_of_speech"]
            if value is None:
                settings.pop("end_of_speech", None)
            else:
                if not isinstance(value, dict):
                    raise ConfigError("end_of_speech: expected a mapping")
                merged = {**(settings.get("end_of_speech") or {}), **value}
                merged = {k: v for k, v in merged.items() if v is not None}
                try:
                    parse_end_of_speech(merged, self.app.end_of_speech_base())
                except ValueError as err:
                    raise ConfigError(str(err)) from err
                if merged:
                    settings["end_of_speech"] = {
                        k: v if k == "vad" else float(v) for k, v in merged.items()
                    }
                else:
                    settings.pop("end_of_speech", None)
        if "beep_elsewhere" in body:
            if body["beep_elsewhere"] not in VERBOSITY[1:]:
                raise ConfigError(f"beep_elsewhere: one of {', '.join(VERBOSITY[1:])}")
            settings["beep_elsewhere"] = body["beep_elsewhere"]
            self.app.store.save_settings()
        if fallback_changed:
            self.app.store.save_settings()
            asyncio.ensure_future(self.app.configure_fallback())
        # The Home Assistant conversation agent: what nothing here understood.
        if "agent" in body:
            agent = str(body["agent"] or "").strip()
            if len(agent) > 255 or any(c.isspace() for c in agent):
                raise ConfigError("agent: a conversation agent's id")
            if agent:
                settings["agent"] = agent
            else:
                settings.pop("agent", None)
        if "agent_keep_minutes" in body:
            try:
                minutes = float(body["agent_keep_minutes"])
            except (TypeError, ValueError) as err:
                raise ConfigError("agent_keep_minutes: minutes, from 0 to 1440") from err
            if not 0 <= minutes <= 1440:
                raise ConfigError("agent_keep_minutes: minutes, from 0 to 1440")
            settings["agent_keep_minutes"] = minutes
        if "agent" in body or "agent_keep_minutes" in body:
            self.app.configure_agent()
        if "tdt_model" in body:
            from .server import DEFAULT_TDT, TDT_ALIASES, TDT_MODELS  # pylint: disable=import-outside-toplevel

            choice = str(body["tdt_model"] or DEFAULT_TDT)
            choice = TDT_ALIASES.get(choice, choice)
            if choice not in TDT_MODELS:
                raise ConfigError(f"tdt_model: one of {', '.join(TDT_MODELS)}")
            settings["tdt_model"] = choice
            self.app.store.save_settings()
            # Loading takes a while; the UI follows it in the status.
            asyncio.ensure_future(self.app.change_tdt())
        if "wake_words" in body:
            picked = self._wake_word_ids(body["wake_words"])
            if picked:
                settings["wake_words"] = picked
            else:
                settings.pop("wake_words", None)
            self.app.wake_words_changed()
        self.app.store.save_settings()
        return web.json_response(settings)

    # ----------------------------------------------------------- wake words

    def _wake_word_ids(self, value: Any) -> list[str]:
        if not value:
            return []
        if not isinstance(value, list):
            raise ConfigError("wake_words: expected a list of ids")
        known = self.app.wake_words.scan()
        unknown = [str(v) for v in value if str(v) not in known]
        if unknown:
            raise ConfigError(f"wake_words: unknown {unknown}")
        picked = list(dict.fromkeys(str(v) for v in value))
        if len(picked) > MAX_WAKE_WORDS:
            raise ConfigError("wake_words: one wake word at a time")
        return picked

    async def wake_words(self, request: web.Request) -> web.Response:
        """Every wake word: what it runs at, its verifier (confusables,
        pronunciations, thresholds) and the defaults the page restores."""
        app = self.app
        catalog = app.wake_words

        def describe() -> list[dict[str, Any]]:
            # Lowered now (and cached), so a model the device cannot run says
            # so here. Hundreds of custom words take seconds: off the loop.
            tuning = app.store.wake_tuning
            raw = app.wake.raw(tuning, app.custom_wake_phrases())["wake_words"]
            words = []
            for word in catalog.scan().values():
                item = word.to_json()
                item["runs"] = catalog.model(word.id) is not None
                item["error"] = catalog.errors.get(word.id)
                item["tuning"] = tuning.get(word.id) or {}
                entry = app.wake.config.wake_words.get(word.id)
                shipped = app.wake.shipped_entry(word.id)
                item["verifier"] = (
                    {
                        "phrases": [p.text for p in entry.phrases],
                        "confusables": [p.text for p in entry.confusables],
                        "pronunciations": _pronunciations((raw.get(word.id) or {}).get("pronunciations")),
                        "thresholds": {k: getattr(entry.thresholds, k) for k in THRESHOLDS},
                        "second_opinion": app.ratio_threshold(word.id),
                    }
                    if entry is not None
                    else None
                )
                item["phrase"] = entry.phrase_text if entry is not None else (word.phrases[0] if word.phrases else word.name)
                item["defaults"] = {
                    "cutoff": word.default_cutoff,
                    "confusables": list((shipped or {}).get("confusables") or []),
                    "pronunciations": _pronunciations((shipped or {}).get("pronunciations")),
                    "thresholds": app.wake.default_thresholds(word.id),
                    "second_opinion": app.default_ratio(word.id),
                }
                words.append(item)
            return words

        words = await asyncio.to_thread(describe)
        preferred = app.wake_words.chosen(app.store.settings, {}, app.default_wake_words)
        model_dir = Path(app.wake.raw({}, {})["models"][app.wake.model_id]["directory"])
        return web.json_response(
            {
                "wake_words": words,
                "preferred": preferred[0] if preferred else None,
                "picked": app.store.settings.get("wake_words"),
                "custom_dir": str(catalog.custom_dir) if catalog.custom_dir else None,
                "error": app.wake_error,
                "phones": model_phones(model_dir),
                "max_cutoff": MAX_CUTOFF,
            }
        )

    def _wake_word(self, word_id: str):
        word = self.app.wake_words.get(word_id)
        if word is None:
            raise ConfigError(f"No wake word {word_id!r}")
        return word

    def _wake_phrases(self, word) -> list[str]:
        """What the verifier accepts as this wake word: as shipped, or a
        custom word's name ("hey tater")."""
        shipped = self.app.wake.shipped_entry(word.id)
        if shipped is not None:
            return [str(p) for p in shipped.get("phrases") or []]
        return list(word.phrases[:1])

    def _unknown_words(self, phrases: list[str], pronunciations: dict[str, str]) -> tuple[list[str], frozenset[str]]:
        """Words of ``phrases`` the verifier can't say, and its phones."""
        lexicon = self.app.wake.lexicon()
        try:
            return self.app.wake.unknown_words(phrases, pronunciations, lexicon), frozenset(lexicon.phones)
        finally:
            lexicon.close()

    async def check_wake_word(self, request: web.Request) -> web.Response:
        """As the page edits a wake word's confusables: the words of its phrase
        and confusables that are not in the verifier's dictionary, each with
        its pronunciation (given, or as shipped) and a guess, and what keeps
        it from being verified, if anything."""
        word = self._wake_word(request.match_info["id"])
        body = await request.json()
        confusables = _confusables(body.get("confusables"))
        given = _pronunciations(body.get("pronunciations"))
        app = self.app

        def check() -> dict[str, Any]:
            phrases = self._wake_phrases(word)
            shipped = _pronunciations((app.wake.shipped_entry(word.id) or {}).get("pronunciations"))
            pronunciations = {**shipped, **given}
            said = [*phrases, *confusables]
            unknown, inventory = self._unknown_words(said, {})
            words = [
                {
                    "word": w,
                    "pronunciation": pronunciations.get(w),
                    "guess": guess(w, inventory),
                    "in": [p for p in said if w in p.lower().split()],
                }
                for w in unknown
            ]
            error = None
            if confusables or not word.custom:
                # As it would be saved: the page fills in the guesses.
                guessed = {w["word"]: w["guess"] for w in words if w["guess"] and not w["pronunciation"]}
                tuning = {**app.store.wake_tuning, word.id: {"confusables": confusables, "pronunciations": {**guessed, **pronunciations}}}
                try:
                    app._verifier_config(tuning)  # pylint: disable=protected-access
                except WakeConfigError as err:
                    error = str(err)
            return {"words": words, "error": error}

        return web.json_response(await asyncio.to_thread(check))

    async def put_wake_word(self, request: web.Request) -> web.Response:
        """What the page sets for a wake word: its cutoff, confusables (a
        custom one with none is not verified), pronunciations, thresholds and
        second opinion. Absent or null: the default. Only what differs from
        the default is kept, so a change to what is shipped still reaches
        the rest."""
        word = self._wake_word(request.match_info["id"])
        body = await request.json()
        app = self.app
        shipped = app.wake.shipped_entry(word.id)
        entry: dict[str, Any] = {}

        cutoff = _number(body.get("cutoff"), "cutoff", 0.0, MAX_CUTOFF)
        if cutoff is not None and round(cutoff, 3) != round(word.default_cutoff, 3):
            entry["cutoff"] = cutoff

        confusables = None if body.get("confusables") is None else _confusables(body["confusables"])
        if confusables is not None:
            if shipped is None:
                if confusables:
                    entry["confusables"] = confusables
            elif set(confusables) != set(shipped.get("confusables") or []):
                entry["confusables"] = confusables

        # Verified: a built-in word, or a custom one given confusables.
        if shipped is not None or entry.get("confusables"):
            said = [*self._wake_phrases(word), *entry.get("confusables", (shipped or {}).get("confusables") or [])]
            said_words = {w for phrase in said for w in str(phrase).lower().split()}
            ours = _pronunciations((shipped or {}).get("pronunciations"))
            given = {
                w: p
                for w, p in _pronunciations(body.get("pronunciations")).items()
                if w in said_words and p != ours.get(w)
            }
            missing, _ = await asyncio.to_thread(self._unknown_words, said, {**ours, **given})
            if missing:
                raise ConfigError(
                    f"Not in the verifier's dictionary: {', '.join(missing)}. Give "
                    f"{'its' if len(missing) == 1 else 'their'} pronunciation."
                )
            if given:
                entry["pronunciations"] = given
            defaults = app.wake.default_thresholds(word.id)
            thresholds = {}
            for name in THRESHOLDS:
                value = _number((body.get("thresholds") or {}).get(name), name, 0.0, 10.0)
                if value is not None and round(value, 4) != round(defaults[name], 4):
                    thresholds[name] = value
            if thresholds:
                entry["thresholds"] = thresholds
            ratio = _number(body.get("second_opinion"), "second_opinion", 0.0, 10.0)
            if ratio is not None and round(ratio, 4) != round(app.default_ratio(word.id), 4):
                entry["second_opinion"] = ratio

        rebuilt = await app.tune_wake_word(word.id, entry or None)
        return web.json_response({"ok": True, "tuning": entry, "rebuilt": rebuilt})

    async def add_wake_word(self, request: web.Request) -> web.Response:
        """A custom wake word: its microWakeWord manifest and model, uploaded
        together. Saved in the custom directory, and refused if it cannot run
        on the device."""
        catalog = self.app.wake_words
        if catalog.custom_dir is None:
            raise ConfigError("No directory for custom wake words")
        manifest: dict[str, Any] | None = None
        model: bytes | None = None
        model_name = ""
        reader = await request.multipart()
        async for part in reader:
            data = await part.read(decode=False)
            name = part.filename or part.name or ""
            if name.endswith(".json"):
                try:
                    manifest = json.loads(data)
                except ValueError as err:
                    raise ConfigError(f"{name}: not JSON ({err})") from err
            elif name.endswith(".tflite"):
                model, model_name = bytes(data), Path(name).name
        if not isinstance(manifest, dict) or manifest.get("type") != "micro" or "micro" not in manifest:
            raise ConfigError("Expected a microWakeWord manifest (.json with \"type\": \"micro\")")
        if model is None:
            raise ConfigError("Expected the wake word's .tflite model too")
        word_id = re.sub(r"[^a-z0-9_]+", "_", Path(model_name).stem.lower()).strip("_")
        if not word_id or word_id in SPECIAL or word_id in {w.id for w in catalog.scan().values() if not w.custom}:
            raise ConfigError(f"{word_id or model_name!r} cannot be used as a wake word id")
        catalog.custom_dir.mkdir(parents=True, exist_ok=True)
        tflite = catalog.custom_dir / f"{word_id}.tflite"
        manifest_path = catalog.custom_dir / f"{word_id}.json"
        tflite.write_bytes(model)
        manifest_path.write_text(json.dumps({**manifest, "model": tflite.name}, indent=2), "utf-8")
        if await asyncio.to_thread(catalog.model, word_id) is None:
            error = catalog.errors.get(word_id, "unknown error")
            tflite.unlink(missing_ok=True)
            manifest_path.unlink(missing_ok=True)
            raise ConfigError(f"This model cannot run on the satellite: {error}")
        return web.json_response({"ok": True, "id": word_id})

    async def delete_wake_word(self, request: web.Request) -> web.Response:
        catalog = self.app.wake_words
        word = catalog.get(request.match_info["id"])
        if word is None or not word.custom:
            raise ConfigError("Only a custom wake word can be deleted")
        word.tflite.unlink(missing_ok=True)
        word.manifest_path.unlink(missing_ok=True)
        # Its verifier goes with it, and satellites listening for it fall
        # back to the preferred wake word.
        await self.app.tune_wake_word(word.id, None)
        return web.json_response({"ok": True})

    # ---------------------------------------------------------------- calls

    async def calls(self, request: web.Request) -> web.Response:
        return web.json_response({"calls": self.app.calls.list(request.query.get("satellite"))}, dumps=_dumps)

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

    async def add_voice(self, request: web.Request) -> web.Response:
        """This session's voice, into a speaker's profile: ``{name}``, one
        that exists or a new one."""
        body = await request.json()
        added = await self.app.add_voice(request.match_info["id"], str(body.get("name") or ""))
        return web.json_response({"ok": True, **added})

    async def speakers(self, request: web.Request) -> web.Response:
        return web.json_response({"enabled": self.app.speakers is not None, "speakers": self.app.speaker_list()})

    async def replay(self, request: web.Request) -> web.Response:
        body = await _json_or_empty(request)
        record = await self.app.replay(request.match_info["id"], bool(body.get("run_actions")))
        return web.json_response(record.to_json(), dumps=_dumps)

    async def try_text(self, request: web.Request) -> web.Response:
        body = await request.json()
        if not str(body.get("text") or "").strip():
            raise ConfigError("Type something to try")
        record = await self.app.try_text(
            str(body.get("text") or ""),
            body.get("satellite") or None,
            bool(body.get("run_actions")),
            bool(body.get("speak")),
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
    """The editor sends the action as YAML text (``action_yaml``)."""
    spec = dict(spec)
    if "action_yaml" in spec:
        text = spec.pop("action_yaml")
        spec.pop("action", None)
        if str(text or "").strip():
            spec["action"] = _load(str(text))
    return spec


def _confusables(value: Any) -> list[str]:
    """Confusables as the verifier reads them: lowercase words, one space
    apart, no punctuation (an inline /pronunciation/ kept), each once."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConfigError("confusables: expected a list of phrases")
    out = []
    for item in value:
        text = re.sub(r"[,.!?;:\"]", " ", str(item)).lower()
        text = " ".join(text.split())
        if text:
            out.append(text)
    return list(dict.fromkeys(out))


def _pronunciations(value: Any) -> dict[str, str]:
    """word -> pronunciation in the model's phones, spaced; a word with
    several (as shipped) keeps the first here."""
    out = {}
    for word, pron in (value or {}).items():
        if isinstance(pron, list):
            pron = pron[0] if pron else ""
        pron = " ".join(str(pron or "").split())
        if pron:
            out[str(word).lower()] = pron
    return out


def _number(value: Any, name: str, low: float, high: float) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as err:
        raise ConfigError(f"{name}: a number from {low} to {high}") from err
    if not low <= number <= high or math.isnan(number):
        raise ConfigError(f"{name}: a number from {low} to {high}")
    return number


def _stt_always(value: Any) -> bool | None:
    """Whether an STT entity always runs: true, false, or null (the default)."""
    if value is not None and not isinstance(value, bool):
        raise ConfigError("stt_always: true or false")
    return value


def _stt_audio(value: Any) -> str:
    """The capture an STT entity hears: "agc", "aec", or "" for the default."""
    from .voices import STT_AUDIO  # pylint: disable=import-outside-toplevel

    value = str(value or "")
    if value and value not in STT_AUDIO:
        raise ConfigError(f"stt_audio: one of {', '.join(STT_AUDIO)}")
    return value


def _language(value: Any, option: str) -> str:
    try:
        return language_code(value)
    except ValueError as err:
        raise ConfigError(f"{option}: {err}") from err


def _clean(value: Any) -> Any:
    """Drop empty values the form sends for fields left blank -- but not a
    combination of nothing said (``combinations: [[], [area]]``)."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key == "combinations" and isinstance(item, list):
                out[key] = [list(c) for c in item if isinstance(c, list)] or None
                if out[key] is None:
                    del out[key]
                continue
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
    """JSON a browser can read: a number that is not finite (a margin over
    nobody, in traces saved before it was left out) is null."""
    return json.dumps(_finite(data), default=str, allow_nan=False)


def _finite(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: _finite(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite(item) for item in value]
    return value
