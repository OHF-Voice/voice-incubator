"""Optional, one-way Home Assistant names, resolved before normal compilation.

Generated names never enter the saved Names document. Manual entries win;
local voice controls and exclusions live in ``ha_names``. Taking control
materializes one entry using the ordinary Names format.
"""

from __future__ import annotations

import copy
from typing import Any

from adapt_ctc.config import ConfigError

from . import languages
from .hass import VOICE_DOMAINS, Catalog
from .store import NAME_KINDS, file_language, name_in, phrase_key

KEY = "ha_names"
DOMAINS = set(VOICE_DOMAINS) - {"weather", "todo", "shopping_list", "calendar"}
REFRESH_SECONDS = 30
CONTROLS = ("implicit", "with_area", "whole_home")


def config(document: dict) -> dict:
    value = document.get(KEY) or {}
    if not isinstance(value, dict) or set(value) - {"enabled", "excluded", "overrides"}:
        raise ConfigError("ha_names: enabled, excluded and overrides only")
    if "enabled" in value and not isinstance(value["enabled"], bool):
        raise ConfigError("ha_names.enabled: true or false")
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


def import_once(document: dict, catalog: Catalog | None, ids: list[str]) -> None:
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
    for kind, items in entries.items():
        for item_id, entry in items.items():
            if kind == "entities":
                entry["area"] = catalog.entities[item_id].area_id or ""
            document.setdefault("names", {}).setdefault(kind, {})[item_id] = _in_language(document, entry)
