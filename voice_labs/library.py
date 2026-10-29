"""The command library, and the commands that follow it.

The library (voice_labs/languages/<code>/library/*.yaml) is example commands
shipped with the app, a file for each section (Lights and switches, Date and
time, ...); a command with versions (``variants``) is a family of commands,
one for each of ``here``, ``device`` and ``place``.

**A command added from the library follows it** (as a Home Assistant
blueprint's automations do): it is saved as a reference -- ``from:
todo/shopping_list_add`` -- with what you set on top (``SETTINGS``: where it
runs, how much it says, the steps when nothing matches, its implied target,
example sentences of your own, its title and icon), and is the library's
version as the app has it now: a fix to the library reaches it. Changing
anything else takes control of it: it becomes a copy, as a command made from
scratch is, and the library's changes no longer reach it.

A reference without ``language`` is in the app's language, whichever it is.
"""

from __future__ import annotations

import copy
import functools
import re
from pathlib import Path
from typing import Any

from adapt_ctc.config import ConfigError
from ruamel.yaml import YAML

from . import languages
from .store import compile_store, plain

REFERENCE = "from"
"""A command's key that names the library command it follows (``file/id``)."""
SETTINGS = ("satellites", "disabled", "verbosity", "fallback", "title", "icon", "slots", "examples", "language")
"""What a command that follows the library may set itself. ``slots``: its
implied target, in place of the library's; ``examples``: sentences added to
the library's."""
REPLACED = ("satellites", "disabled", "verbosity", "fallback", "title", "icon", "slots")
"""The settings that take the library's place (null: none of it)."""
COPY_SUFFIX = r"(_\d+|_[a-z]{2}|_copy(_\d+)?)?"
"""What an added command's id may have after the library's: a number (one
added again), a language (another's has the id), a copy's."""


def library_dir(code: str | None = None) -> Path:
    """The command library: the app's language's, or ``code``'s
    (voice_labs/languages)."""
    return (languages.load(code) if code else languages.current()).library


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


def library_files(code: str | None = None) -> list[tuple[Path, dict[str, Any]]]:
    """The library's files, ``[(path, data)]``, by title: its sections as the
    Home page and the library list them."""
    yaml = YAML(typ="safe")
    files = [(path, plain(yaml.load(path.read_text("utf-8")) or {})) for path in library_dir(code).glob("*.yaml")]
    return sorted(files, key=lambda file: str(file[1].get("title") or file[0].stem).lower())


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
    library_versions). A copy of ``_library``'s."""
    return plain(_library(languages.current().code))


@functools.cache
def _library(code: str) -> list[dict[str, Any]]:
    """The library in ``code``, read once -- and checked, in the app's
    language: it ships with the app. 1.6 s on an HA Green, mostly parsing
    YAML. Not to be changed: ``load_library`` hands out copies."""
    entries = []
    checked = code == languages.current().code
    for path, data in library_files(code):
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
                entries.append(_library_entry(path.stem, data, file_lists, family_id, family, command_id, variant, spec, failed, checked))
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
    checked: bool = True,
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
    if not checked:
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




def entry(ref: str, code: str | None = None) -> dict[str, Any] | None:
    """The library command ``ref`` (``file/id``) names, in ``code``'s
    library (the app's language's), or None. A family's former id (one it
    had before it had versions) is its first version; one moved to another
    section is found there (ids are the library's, whatever the section)."""
    file, _, command_id = str(ref).partition("/")
    every = _library(code or languages.current().code)
    entries = [e for e in every if e["file"] == file]
    found = next((e for e in entries if e["id"] == command_id), None)
    if found is None:
        found = next((e for e in every if e["id"] == command_id), None)
    if found is None:
        for path, data in library_files(code):
            if path.stem != file:
                continue
            for family_id, family in (data.get("commands") or {}).items():
                if command_id in (family.get("formerly") or []):
                    found = next((e for e in entries if e["family"] == family_id), None)
    return found


def base(ref: str, code: str | None = None) -> dict[str, Any]:
    """The library's version of ``ref``, as a command: with its title and
    section. Raises ConfigError when the library has no such command."""
    found = entry(ref, code)
    if found is None:
        raise ConfigError(f"{ref} is not in the library: take control of it, or delete it")
    spec = copy.deepcopy(found["spec"])
    spec["title"] = found["title"]
    spec["library"] = found["file"]
    return spec


def follows(spec: Any) -> bool:
    """Whether a saved command follows the library."""
    return isinstance(spec, dict) and bool(spec.get(REFERENCE))


def resolve(command_id: str, spec: dict[str, Any]) -> dict[str, Any]:
    """A command that follows the library, as the app runs it: the
    library's version, with its settings on top. Raises ConfigError."""
    unknown = sorted(set(spec) - {REFERENCE, *SETTINGS})
    if unknown:
        raise ConfigError(
            f"commands.{command_id}: {', '.join(unknown)} can't be changed while it follows the library: take control of it"
        )
    out = base(str(spec[REFERENCE]))
    for key in REPLACED:
        if key in spec:
            if spec[key] is None:
                out.pop(key, None)
            else:
                out[key] = copy.deepcopy(spec[key])
    if spec.get("examples"):
        theirs = [str(e) for e in out.get("examples") or []]
        out["examples"] = theirs + [str(e) for e in spec["examples"] if str(e) not in theirs]
    if spec.get("language"):
        out["language"] = spec["language"]
    return out


def settings(spec: dict[str, Any], library: dict[str, Any]) -> dict[str, Any]:
    """A command that follows the library, saved as what differs from the
    library's version (``library``): ``spec`` is its reference and settings,
    as the command page sends them."""
    out: dict[str, Any] = {REFERENCE: str(spec[REFERENCE])}
    for key in REPLACED:
        if key not in spec:
            continue
        mine, theirs = spec[key], library.get(key)
        if key == "slots":
            mine, theirs = mine or {}, theirs or {}
        if mine != theirs:
            out[key] = mine
    theirs = [str(e) for e in library.get("examples") or []]
    added = [str(e) for e in spec.get("examples") or [] if str(e) not in theirs]
    if added:
        out["examples"] = added
    if spec.get("language"):
        out["language"] = spec["language"]
    return out


def migrate(document: dict[str, Any]) -> list[str]:
    """Commands added from the library before they followed it -- copies of
    it, with ``library: <file>`` -- made to follow it: what they set
    themselves is kept (``SETTINGS``), the rest is the library's now. One the
    library no longer has stays a copy. Returns the ids changed."""
    default = str(document.get("language") or languages.current().code)
    changed = []
    for command_id, spec in list((document.get("commands") or {}).items()):
        if not isinstance(spec, dict) or follows(spec) or not spec.get("library"):
            continue
        code = str(spec.get("language") or default)
        try:
            library_entries = _library(code)
        except ValueError:
            continue  # no such language
        matches = [e for e in library_entries if re.fullmatch(re.escape(e["id"]) + COPY_SUFFIX, str(command_id))]
        # Its section's, else one moved to another.
        found = next((e for e in matches if e["file"] == spec["library"]), matches[0] if matches else None)
        if found is None:
            continue
        theirs = base(f"{found['file']}/{found['id']}", code)
        # What the copy has, as it was (one it lacks, the library's version
        # of then lacked too: the library's now).
        kept = {key: spec[key] for key in REPLACED if key in spec}
        reference = settings({REFERENCE: f"{found['file']}/{found['id']}", **kept, "examples": spec.get("examples")}, theirs)
        if "language" in spec:
            reference["language"] = spec["language"]
        document["commands"][command_id] = reference
        changed.append(str(command_id))
    return changed
