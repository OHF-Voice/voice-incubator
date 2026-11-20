"""Optional, one-way Home Assistant names, resolved before normal compilation.

Generated names never enter the saved Names document. Manual entries win;
local voice controls and exclusions live in ``ha_names``. Taking control
materializes one entry using the ordinary Names format.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from adapt_ctc.config import ConfigError

from . import capabilities, languages, library, music
from .hass import VOICE_DOMAINS, Catalog, Entity
from .store import NAME_KINDS, command_language, file_language, list_in, name_in, phrase_key

KEY = "ha_names"
DOMAINS = set(VOICE_DOMAINS) - {"weather", "todo", "shopping_list", "calendar"}
REFRESH_SECONDS = 30
CONTROLS = ("implicit", "with_area", "whole_home")


def config(document: dict) -> dict:
    value = document.get(KEY) or {}
    if not isinstance(value, dict) or set(value) - {"enabled", "excluded", "overrides", "auto_add_commands", "handled_commands"}:
        raise ConfigError("ha_names: enabled, excluded, overrides, auto_add_commands and handled_commands only")
    for key in ("enabled", "auto_add_commands"):
        if key in value and not isinstance(value[key], bool):
            raise ConfigError(f"ha_names.{key}: true or false")
    handled = value.get("handled_commands", [])
    if not isinstance(handled, list) or any(not isinstance(ref, str) for ref in handled):
        raise ConfigError("ha_names.handled_commands: a list of command references")
    excluded = value.get("excluded") or {}
    if not isinstance(excluded, dict) or set(excluded) - set(NAME_KINDS):
        raise ConfigError("ha_names.excluded: entities, areas or floors")
    for ids in excluded.values():
        if not isinstance(ids, list) or any(not isinstance(i, str) for i in ids):
            raise ConfigError("ha_names.excluded: lists of ids")
    overrides = value.get("overrides") or {}
    if not isinstance(overrides, dict):
        raise ConfigError("ha_names.overrides: a mapping of entity ids to controls")
    for controls in overrides.values():
        check_controls(controls)
    return value


def check_controls(controls: Any) -> None:
    if not isinstance(controls, dict) or set(controls) - {*CONTROLS, "aliases"}:
        raise ConfigError("HA names: implicit, with_area, whole_home and extra aliases only; take control to change names or area")
    for key in CONTROLS:
        if key in controls and not isinstance(controls[key], bool):
            raise ConfigError(f"HA names {key}: true or false")
    aliases = controls.get("aliases", {})
    if not isinstance(aliases, dict) or any(
        not isinstance(names, list) or any(not isinstance(n, str) or not n.strip() for n in names)
        for names in aliases.values()
    ):
        raise ConfigError("HA names aliases: lists of additional names by language")


def _unique(names: list[str]) -> list[str]:
    out = {}
    for name in names:
        if name.strip():
            out.setdefault(phrase_key(name), name.strip())
    return list(out.values())


def _without_area(name: str, area: str | None) -> str:
    prefix = f"{area} " if area else ""
    return name[len(prefix):].strip() if prefix and name.casefold().startswith(prefix.casefold()) else name.strip()


def generated(document: dict, catalog: Catalog | None) -> dict[str, dict[str, dict]]:
    """The eligible HA entries not already owned or explicitly excluded.

    Unknown exposure is never permission to add entities. The app retains
    its last successful catalog during a failed exposure refresh.
    """
    settings = config(document)
    out = {kind: {} for kind in NAME_KINDS}
    if catalog is None or catalog.exposed is None:
        return out
    manual = document.get("names") or {}
    excluded = settings.get("excluded") or {}
    overrides = settings.get("overrides") or {}
    language, default = languages.current().code, file_language(document)
    exposed = {i: entity for i in sorted(catalog.exposed)
               if (entity := catalog.entities.get(i)) is not None and entity.domain in DOMAINS}
    occupied = {entity.area_id for entity in exposed.values()}
    areas = {i: area for i, area in catalog.areas.items() if i in occupied}
    for kind, items in (("areas", areas), ("floors", catalog.floors)):
        for item_id, item in items.items():
            if item_id not in (manual.get(kind) or {}) and item_id not in excluded.get(kind, []):
                out[kind][item_id] = {"name": item.name, "aliases": _unique(item.aliases)}
    for entity_id, entity in exposed.items():
        if entity_id in (manual.get("entities") or {}) or entity_id in excluded.get("entities", []):
            continue
        controls = overrides.get(entity_id) or {}
        area = catalog.areas.get(entity.area_id)
        # A manually named room affects how its devices are spoken of too.
        room = name_in((manual.get("areas") or {}).get(entity.area_id), language, default)
        if isinstance(room, dict):
            room = room.get("name")
        label = str(room or area.name) if area is not None else None
        bare = _without_area(entity.name, area.name if area else None) or entity.name
        extra = (controls.get("aliases") or {}).get(language, [])
        here = _unique([bare, *(_without_area(a, area.name if area else None) for a in entity.aliases), *extra]) if area else []
        house = _unique([f"{label} {bare}" if label else entity.name,
                         *(f"{label} {_without_area(a, area.name)}" if label and area else a for a in entity.aliases),
                         *(f"{label} {a}" if label else a for a in extra)]) if controls.get("whole_home", True) else []
        out["entities"][entity_id] = {
            "name": here[0] if here else "", "aliases": here[1:], "house": house,
            "implicit": controls.get("implicit", True), "with_area": controls.get("with_area", True),
        }
    return out


def pending_commands(document: dict, catalog: Catalog | None) -> list[str]:
    """The applicable by-name commands to set up once in the current language."""
    settings = config(document)
    if not settings.get("enabled") or not settings.get("auto_add_commands") or catalog is None:
        return []
    return _applicable_commands(document, catalog, generated(document, catalog)["entities"])


def _applicable_commands(document: dict, catalog: Catalog, names: dict[str, dict]) -> list[str]:
    devices = [catalog.entities[i] for i, name in names.items() if name["name"] or name["house"]]
    if not devices:
        return []
    pack = languages.current()
    default = file_language(document)
    handled = set(config(document).get("handled_commands", []))
    lists = document.get("lists") or {}
    pending = []
    for base, entry in library.named_device_entries(pack.code).items():
        ref = f"{entry['file']}/{base}"
        if f"{pack.code}:{ref}" in handled:
            continue
        spec = entry["spec"]
        # Don't enable commands that listen for a turned-off or missing list.
        used = [v.get("list") if isinstance(v, dict) else v for v in (spec.get("lists") or {}).values()]
        if any(isinstance(name, str) and (lists.get(name) is False or
               (name not in pack.lists and list_in(lists.get(name), pack.code, default) is None)) for name in used):
            continue
        if any(_applies(base, spec, entity) for entity in devices):
            pending.append(ref)
    return pending


def _applies(command_id: str, spec: dict, entity: Entity) -> bool:
    if entity.domain not in spec["names"]["entity"]["domains"]:
        return False
    if command_id == "is_locked" and entity.domain == "binary_sensor":
        return (entity.attributes or {}).get("device_class") == "lock"
    if operation := spec.get("music"):
        if operation not in music.CONTROLS and entity.platform != "music_assistant":
            return False
        if operation == "volume_set" and capabilities.can(entity, "volume_set") is not True:
            return False
    needs = spec.get("supports") or ()
    return all(capabilities.can(entity, need) is True for need in ((needs,) if isinstance(needs, str) else needs))


def _device_ref(command_id: str, spec: dict, code: str) -> str | None:
    if not isinstance(spec, dict):
        return None
    entries = library.named_device_entries(code)
    if library.follows(spec):
        # The library also resolves references whose section moved.
        base = str(spec[library.REFERENCE]).partition("/")[2]
        return f"{entries[base]['file']}/{base}" if base in entries else None
    # Older commands that took control have no reference. Recognize their
    # standard id (also a numbered, translated or copied one in its section).
    for base, entry in entries.items():
        if command_id == base or (spec.get("library") == entry["file"] and re.fullmatch(base + library.COPY_SUFFIX, command_id)):
            return f"{entry['file']}/{base}"
    return None


def remember_command(document: dict, command_id: str, spec: dict) -> None:
    """Editing or removing a device command is a choice, even before opting in."""
    code = command_language(spec, file_language(document))
    if (ref := _device_ref(command_id, spec, code)) is not None:
        key = f"{code}:{ref}"
        handled = document.setdefault(KEY, {}).setdefault("handled_commands", [])
        if key not in handled:
            handled.append(key)


def add_commands(document: dict, refs: list[str]) -> None:
    """Save ordinary library references; never re-add a command the user removed."""
    if not refs:
        return
    here, default = languages.current().code, file_language(document)
    commands = document["commands"]
    installed = {_device_ref(i, spec, here) for i, spec in commands.items() if command_language(spec, default) == here}
    handled = document.setdefault(KEY, {}).setdefault("handled_commands", [])
    for ref in refs:
        handled.append(f"{here}:{ref}")
        if ref in installed:
            continue
        base = ref.split("/")[1]
        command_id, n = base, 2
        while command_id in commands:
            command_id, n = f"{base}_{n}", n + 1
        commands[command_id] = {library.REFERENCE: ref, **({"language": here} if here != default else {})}


def resolve(document: dict, catalog: Catalog | None) -> dict:
    """Supply ordinary Names entries without changing the saved document."""
    settings = config(document)
    if not settings.get("enabled"):
        return document
    out = copy.deepcopy(document)
    names = out["names"] = out.get("names") or {}
    for kind, entries in generated(document, catalog).items():
        if entries:
            names[kind] = {**(names.get(kind) or {}), **{i: _in_language(out, e) for i, e in entries.items()}}
    return out


def _in_language(document: dict, entry: dict) -> dict:
    if languages.current().code == file_language(document):
        return copy.deepcopy(entry)
    own = {k: v for k, v in entry.items() if k in ("area", "implicit", "with_area")}
    return {**own, languages.current().code: {k: v for k, v in entry.items() if k not in own}}


def following(document: dict, catalog: Catalog | None, kind: str, item_id: str) -> bool:
    return bool(config(document).get("enabled") and item_id in generated(document, catalog).get(kind, {}))


def exclude(document: dict, kind: str, item_id: str) -> None:
    if kind not in NAME_KINDS:
        raise ConfigError("HA names: entities, areas or floors")
    ids = document.setdefault(KEY, {}).setdefault("excluded", {}).setdefault(kind, [])
    if item_id not in ids:
        ids.append(item_id)


def take_control(document: dict, catalog: Catalog | None, kind: str, item_id: str) -> None:
    entry = generated(document, catalog).get(kind, {}).get(item_id)
    if catalog is None or entry is None or not config(document).get("enabled"):
        raise ConfigError("This name is not following Home Assistant")
    if kind == "entities":
        # Empty means explicitly in no area; None/absent follows HA.
        entry["area"] = catalog.entities[item_id].area_id or ""
    document.setdefault("names", {}).setdefault(kind, {})[item_id] = _in_language(document, entry)
    exclude(document, kind, item_id)


def resume_following(document: dict, catalog: Catalog, entity_id: str, replace_manual: bool) -> None:
    settings = config(document)
    if not settings.get("enabled"):
        raise ConfigError("Turn on Follow Home Assistant first")
    excluded = (settings.get("excluded") or {}).get("entities", [])
    if entity_id not in excluded:
        raise ConfigError("This device is not excluded from automatic sync")
    entity = catalog.entities.get(entity_id)
    if entity is None or entity.domain not in DOMAINS or entity_id not in (catalog.exposed or set()):
        raise ConfigError("Only supported devices currently exposed to Assist can resume following")
    manual = (document.get("names") or {}).get("entities") or {}
    if entity_id in manual and not replace_manual:
        raise ConfigError("Confirm replacing the manual entry before resuming")
    manual.pop(entity_id, None)
    settings["excluded"]["entities"] = [i for i in excluded if i != entity_id]


def import_once(document: dict, catalog: Catalog | None, ids: list[str], *, add_library_commands: bool = False) -> None:
    if config(document).get("enabled"):
        raise ConfigError("Stop following Home Assistant before importing manual names")
    if catalog is None or catalog.exposed is None:
        raise ConfigError("Connect to Home Assistant and read Assist exposure before importing")
    if not ids:
        raise ConfigError("Select at least one device to import")
    entries = generated(document, catalog)
    wanted = set(ids)
    unknown = wanted - set(entries["entities"])
    if unknown:
        raise ConfigError(f"Not eligible for import: {', '.join(sorted(unknown))}")
    entries["entities"] = {i: e for i, e in entries["entities"].items() if i in wanted}
    if add_library_commands:
        add_commands(document, _applicable_commands(document, catalog, entries["entities"]))
    for kind, items in entries.items():
        for item_id, entry in items.items():
            if kind == "entities":
                entry["area"] = catalog.entities[item_id].area_id or ""
            document.setdefault("names", {}).setdefault(kind, {})[item_id] = _in_language(document, entry)
