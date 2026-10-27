"""Where commands, satellites and settings live.

The web UI's own files, as JSON: ``commands.json`` (the voice commands,
shared names and lists), ``satellites.json``, ``settings.json``,
``wake_words.json`` (what the Wake words page changed, per wake word) and
``people.json`` (each person's own things: voice_call/people.py). Only
the UI edits them: each is read once and written whole when it changes. (They
were YAML, read and written round-trip so that a hand edit's comments
survived: 386 ms to read the commands and 218 ms to write them on an HA Green,
against under a millisecond as JSON.)

A data directory from before keeps its ``.yaml`` files: each is read once,
while its JSON is not there yet, and copied to it. The YAML stays as it was
and is not read again.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from adapt_ctc.config import ConfigError, parse_config

from . import languages
from .commands import FALLBACK_STEPS, Command, adapt_config, parse_commands, parse_unknown, shared_examples

_LOGGER = logging.getLogger(__name__)

STORE_KEYS = {"language", "commands", "names", "lists", "unknown", "settings", "version"}
VERSION = 2
"""What a commands file is: 2, its commands from the library follow it
(voice_call/library.py); before, they were copies of it, and are made to
follow it when read."""
NAME_KINDS = ("entities", "areas", "floors")

def new_store(language: str) -> dict[str, Any]:
    """A home's commands before it has any.

    ``language``: what the commands are said in (none: English); a command
    made in another language says its own (``language: de``), and only the
    app's language's are heard and shown. ``names``: what things are called,
    each pointing at one Home Assistant id; in another language than the
    store's under its code (``kitchen: {name: Kitchen, de: {name: Küche}}``).
    ``lists``: value lists for slots ("red" -> red), shared by commands,
    besides the language's own (colors, on_off ...); a value's phrases in
    another language than the store's go under its code
    (``red: {en: [red], de: [rot]}``). ``commands``: what is listened for, and
    what happens. ``unknown``: what is said when nothing understood a
    sentence."""
    return {"version": VERSION, "language": language, "names": {}, "lists": {}, "commands": {}}


DEFAULT_SETTINGS: dict[str, Any] = {
    # Debugging: keep every call's audio and full trace on disk.
    "keep_audio": False,
    # Oldest saved calls are deleted beyond this many.
    "max_saved_calls": 200,
}


def _yaml() -> YAML:
    """For a command shown as YAML (its YAML tab), and the YAML files of
    before. Round-trip, YAML 1.2: "on" and "off" stay words."""
    yaml = YAML()
    yaml.default_flow_style = False
    yaml.width = 100
    yaml.indent(mapping=2, sequence=2, offset=0)
    return yaml


def styled(value: Any) -> Any:
    """Plain data as ruamel nodes the way people write them: mappings as
    blocks, phrase lists (and lists of them, like keyword groups) inline."""
    if isinstance(value, dict):
        out = CommentedMap()
        for key, item in value.items():
            out[key] = styled(item)
        return out
    if isinstance(value, list):
        out = CommentedSeq(styled(item) for item in value)
        scalars = all(not isinstance(v, (dict, list)) for v in value)
        phrase_groups = all(
            isinstance(v, list) and all(not isinstance(x, (dict, list)) for x in v) for v in value
        )
        if value and (scalars or phrase_groups):
            out.fa.set_flow_style()
        return out
    return value


def dump(value: Any) -> str:
    stream = io.StringIO()
    _yaml().dump(styled(value), stream)
    return stream.getvalue()


def plain(data: Any) -> Any:
    """ruamel's commented containers as plain dicts and lists."""
    return json.loads(json.dumps(data, default=str))


@dataclass
class Compiled:
    """A store that parses: its commands and their adapt-ctc config."""

    data: dict[str, Any]
    commands: dict[str, Command]
    config: dict[str, Any] | None
    """None when no command is enabled."""
    twins: dict[str, tuple[str, ...]] = field(default_factory=dict)
    """Intents that say another's example sentences (a command's copy), by
    the intent the sentences went to (``shared_examples``)."""
    unknown: str | None = None
    """The response template when nothing understood a sentence; None: the
    built-in hint."""
    language: str = languages.DEFAULT
    """What the commands are said in (``language:``; none: English), unless
    a command says its own."""
    hidden: dict[str, str] = field(default_factory=dict)
    """Commands in another language than the app's, by id: their language.
    Not heard, not shown; kept for when the app is in theirs again."""
    hidden_names: dict[str, int] = field(default_factory=dict)
    """Things named in other languages but not the app's: how many, by
    language. ``data``'s names are the app's language's alone."""
    hidden_lists: dict[str, int] = field(default_factory=dict)
    """Lists with phrases in other languages but none in the app's: how
    many, by language. ``data``'s lists are the language's built-in ones
    (``Pack.lists``) with the home's own in it on top."""


LANGUAGE_CODE = re.compile(r"[a-z]{2,3}")


def file_language(data: dict[str, Any]) -> str:
    """What a store's commands are said in when they don't say (``language:``)."""
    return str(data.get("language") or languages.DEFAULT)


def language_name(code: str) -> str:
    """"English" for en; the code itself for a language without a pack."""
    try:
        return languages.load(code).name
    except ValueError:
        return code


NAME_FIELDS = ("name", "aliases")


def name_in(entry: Any, here: str, default: str) -> Any:
    """What a thing is called in ``here`` -- its ``name``/``aliases`` when
    ``here`` is the file's language (``default``), else its part under
    ``here``'s code -- as adapt-ctc reads a name; None when it isn't named in
    ``here``."""
    if isinstance(entry, str):
        return entry if here == default else None
    if not isinstance(entry, dict):
        return None
    if here != default:
        return entry.get(here) or None
    own = {k: v for k, v in entry.items() if k in NAME_FIELDS and v}
    return own or None


def named_languages(entry: Any, default: str) -> list[str]:
    """Every language a thing is named in."""
    if isinstance(entry, str):
        return [default]
    if not isinstance(entry, dict):
        return []
    own = [default] if any(entry.get(k) for k in NAME_FIELDS) else []
    return own + [k for k, v in entry.items() if k not in NAME_FIELDS and v]


def names_in(names: dict[str, Any], here: str, default: str) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    """A store's names as said in ``here``, by kind, and how many things are
    named in other languages but not in ``here``, by language."""
    out: dict[str, dict[str, Any]] = {}
    hidden: dict[str, int] = {}
    for kind, items in (names or {}).items():
        out[kind] = {}
        for item_id, entry in (items or {}).items():
            if isinstance(entry, dict):
                for key in entry:
                    if key not in NAME_FIELDS and not LANGUAGE_CODE.fullmatch(str(key)):
                        raise ConfigError(f"names.{kind}.{item_id}: {key!r} is neither name, aliases nor a language code")
            said = name_in(entry, here, default)
            if said is not None:
                out[kind][str(item_id)] = said
                continue
            for language in named_languages(entry, default):
                hidden[language] = hidden.get(language, 0) + 1
    return out, hidden


def list_in(values: Any, here: str, default: str) -> Any:
    """A list's values with their phrases in ``here`` -- a value's phrases
    when ``here`` is the file's language (``default``), else its phrases
    under ``here``'s code -- as adapt-ctc reads a list; None without any."""
    if isinstance(values, list):
        return values if here == default and values else None
    if not isinstance(values, dict):
        return None
    out = {}
    for value, said in values.items():
        phrases = (said.get(here) if isinstance(said, dict) else said if here == default else None)
        if phrases:
            out[value] = phrases
    return out or None


def lists_in(lists: dict[str, Any], here: str, default: str) -> tuple[dict[str, Any], dict[str, int]]:
    """A store's value lists as said in ``here``, and how many have phrases
    in other languages but none in ``here``, by language."""
    out, hidden = {}, {}
    for name, values in (lists or {}).items():
        if isinstance(values, dict):
            for value, said in values.items():
                if isinstance(said, dict):
                    for key in said:
                        if not LANGUAGE_CODE.fullmatch(str(key)):
                            raise ConfigError(f"lists.{name}.{value}: {key!r} is no language code")
        said = list_in(values, here, default)
        if said is not None:
            out[str(name)] = said
            continue
        for language in sorted(list_languages(values, default)):
            hidden[language] = hidden.get(language, 0) + 1
    return out, hidden


def list_languages(values: Any, default: str) -> set[str]:
    """Every language a list has phrases in."""
    if isinstance(values, list):
        return {default} if values else set()
    out: set[str] = set()
    for said in (values or {}).values() if isinstance(values, dict) else ():
        if isinstance(said, dict):
            out |= {str(code) for code, phrases in said.items() if phrases}
        elif said:
            out.add(default)
    return out


def command_language(spec: Any, default: str) -> str:
    """What a command is said in: its own ``language:``, else ``default``
    (the file's)."""
    own = spec.get("language") if isinstance(spec, dict) else None
    return str(own or default)


def compile_store(data: dict[str, Any]) -> Compiled:
    """Validate a whole store. Raises ConfigError naming what is wrong."""
    if not isinstance(data, dict):
        raise ConfigError("commands file: expected a mapping")
    unknown = set(data) - STORE_KEYS
    if unknown:
        raise ConfigError(f"unknown top-level key(s) {sorted(unknown)} (use {sorted(STORE_KEYS)})")
    language = file_language(data)
    if not LANGUAGE_CODE.fullmatch(language):
        raise ConfigError(f"language: a language code, like en or de (not {language!r})")
    # Only the app's language's commands: the others are kept, not heard.
    here = languages.current().code
    every = data.get("commands") or {}
    hidden = {}
    for command_id, spec in every.items():
        said = command_language(spec, language)
        if not LANGUAGE_CODE.fullmatch(said):
            raise ConfigError(f"{command_id}.language: a language code, like en or de (not {said!r})")
        if said != here:
            hidden[str(command_id)] = said
    names = data.get("names") or {}
    unknown = set(names) - set(NAME_KINDS)
    if unknown:
        raise ConfigError(f"names: unknown kind(s) {sorted(unknown)} (use {list(NAME_KINDS)})")
    # What things are called in the app's language: everything reads these.
    said, hidden_names = names_in(names, here, language)
    own_lists, hidden_lists = lists_in(data.get("lists") or {}, here, language)
    from .library import follows, resolve  # pylint: disable=import-outside-toplevel

    # One that follows the library is its version now, with its settings.
    heard = {str(k): (resolve(str(k), v) if follows(v) else v) for k, v in every.items() if str(k) not in hidden}
    kept = {str(k): heard.get(str(k), v) for k, v in every.items()}  # another language's as saved
    data = {**data, "names": said, "lists": {**languages.current().lists, **own_lists}, "commands": kept}
    commands = parse_commands(heard)
    unknown_response = parse_unknown(data.get("unknown"))
    config = adapt_config(data, commands)
    if config is not None:
        parse_config(config)  # adapt-ctc's own checks: keywords, names, numbers
    return Compiled(
        data, commands, config, shared_examples(commands)[1], unknown_response, language, hidden, hidden_names,
        hidden_lists,
    )


@dataclass
class Store:
    data_dir: Path
    satellites: dict[str, dict[str, Any]] = field(default_factory=dict)
    settings: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_SETTINGS))
    wake_tuning: dict[str, dict[str, Any]] = field(default_factory=dict)
    """Per wake word: its cutoff, confusables, pronunciations and thresholds,
    where the Wake words page changed them."""
    people: dict[str, dict[str, Any]] = field(default_factory=dict)
    """Each person's own things, by name (voice_call/people.py)."""

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._document: dict[str, Any] | None = None

    @property
    def commands_path(self) -> Path:
        return self.data_dir / "commands.json"

    @property
    def satellites_path(self) -> Path:
        return self.data_dir / "satellites.json"

    @property
    def settings_path(self) -> Path:
        return self.data_dir / "settings.json"

    @property
    def wake_tuning_path(self) -> Path:
        return self.data_dir / "wake_words.json"

    @property
    def people_path(self) -> Path:
        return self.data_dir / "people.json"

    @property
    def calls_dir(self) -> Path:
        return self.data_dir / "calls"

    # ------------------------------------------------------------- commands

    def document(self) -> dict[str, Any]:
        """The commands as saved (a copy): ``language``, ``names``, ``lists``,
        ``commands``, ``unknown``. Read from disk once."""
        if self._document is None:
            data = _read(self.commands_path)
            if data is None:
                data = new_store(languages.current().code)
                _write_atomic(self.commands_path, _dumps(data))
            if not isinstance(data, dict):
                raise ConfigError("commands file: expected a mapping")
            if int(data.get("version") or 1) < VERSION:
                data = self._follow_library(data)
            self._document = data
        return plain(self._document)

    def _follow_library(self, data: dict[str, Any]) -> dict[str, Any]:
        """Commands added from the library before they followed it, made to
        (library.migrate); the file as it was is kept beside it, once."""
        from .library import migrate  # pylint: disable=import-outside-toplevel

        before = _dumps(data)
        changed = migrate(data)
        data["version"] = VERSION
        kept = self.data_dir / "commands.before-library.json"
        if changed and not kept.exists():
            _write_atomic(kept, before)
        _write_atomic(self.commands_path, _dumps(data))
        if changed:
            _LOGGER.info("%d command(s) follow the library now: %s (as they were: %s)", len(changed), ", ".join(changed), kept.name)
        return data

    def load(self) -> Compiled:
        """The commands, validated. Raises ConfigError."""
        return compile_store(self.document())

    def update(self, edit) -> Compiled:
        """Apply ``edit(document)`` to a copy of the commands, validate the
        result and save it. ``edit`` changes the document in place (or
        replaces its parts)."""
        document = self.document()
        for key in ("names", "lists", "commands"):
            if document.get(key) is None:
                document[key] = {}
        edit(document)
        compiled = compile_store(plain(document))
        self._write(document)
        return compiled

    def put_command(self, command_id: str, spec: dict[str, Any], old_id: str | None = None) -> Compiled:
        """Save a command. A new one is in the app's language: it says so
        (``language:``) when that is not the file's."""

        def edit(document: dict[str, Any]) -> None:
            commands = document["commands"]
            here, default = languages.current().code, file_language(document)
            taken = commands.get(command_id)
            if taken is not None and command_language(taken, default) != here:
                raise ConfigError(f"“{command_id}” is taken by a command in {language_name(command_language(taken, default))}; pick another id")
            said = {**spec}
            if "language" not in said and here != default:
                said["language"] = here
            if old_id and old_id != command_id and old_id in commands:
                # Renamed: keep its place among the commands.
                index = list(commands).index(old_id)
                del commands[old_id]
                document["commands"] = _inserted(commands, index, command_id, said)
            else:
                commands[command_id] = said

        return self.update(edit)

    def duplicate_command(self, command_id: str, new_id: str, title: str | None = None) -> Compiled:
        """Copy a command, as saved, to ``new_id``, right after it."""

        def edit(document: dict[str, Any]) -> None:
            commands = document["commands"]
            if command_id not in commands:
                raise FileNotFoundError(f"No command {command_id}")
            if new_id in commands:
                raise ConfigError(f"There is already a command {new_id!r}")
            spec = plain(commands[command_id])
            if title:
                spec["title"] = title
            document["commands"] = _inserted(commands, list(commands).index(command_id) + 1, new_id, spec)

        return self.update(edit)

    def delete_command(self, command_id: str) -> Compiled:
        def edit(document: dict[str, Any]) -> None:
            document["commands"].pop(command_id, None)

        return self.update(edit)

    def put_fallback(self, steps: dict[str, list[str]]) -> Compiled:
        """For each fallback step given (``meaning``, ``llm``), exactly which
        commands it may run (their ``fallback`` key)."""
        def edit(document: dict[str, Any]) -> None:
            commands = document["commands"]
            for members in steps.values():
                unknown = sorted(set(members) - set(commands))
                if unknown:
                    raise ConfigError(f"No command {unknown[0]!r}")
            here, default = languages.current().code, file_language(document)
            for command_id, command in commands.items():
                if not isinstance(command, dict) or command_language(command, default) != here:
                    continue  # another language's: as it was
                current = command.get("fallback") or []
                current = [str(current)] if isinstance(current, str) else [str(c) for c in current]
                keep = [
                    step
                    for step in FALLBACK_STEPS
                    if (command_id in steps[step] if step in steps else step in current)
                ]
                if keep == current:
                    continue
                if keep:
                    command["fallback"] = keep
                else:
                    command.pop("fallback", None)

        return self.update(edit)

    def put_unknown(self, response: str | None) -> Compiled:
        """What is said when nothing understood a sentence; None or empty:
        the built-in hint."""

        def edit(document: dict[str, Any]) -> None:
            if response:
                document["unknown"] = {"response": response}
            else:
                document.pop("unknown", None)

        return self.update(edit)

    def put_name(self, kind: str, item_id: str, spec: dict[str, Any] | None) -> Compiled:
        if kind not in NAME_KINDS:
            raise ConfigError(f"names: unknown kind {kind!r}")

        def edit(document: dict[str, Any]) -> None:
            names = document["names"]
            if names.get(kind) is None:
                names[kind] = {}
            items = names[kind]
            here, default = languages.current().code, file_language(document)
            entry = plain(items.get(item_id))
            # What it is called in other languages than the app's stays.
            others = (
                {k: v for k, v in entry.items() if k != here and k not in NAME_FIELDS}
                if isinstance(entry, dict)
                else {}
            )
            if here != default:
                if isinstance(entry, str) or (isinstance(entry, dict) and any(entry.get(k) for k in NAME_FIELDS)):
                    base = {"name": entry} if isinstance(entry, str) else {k: entry[k] for k in NAME_FIELDS if k in entry}
                else:
                    base = {}
                kept = {**base, **others, **({here: spec} if spec is not None else {})}
            else:
                kept = {**(spec or {}), **others}
            if kept:
                items[item_id] = kept
            else:
                items.pop(item_id, None)  # named in no language any more

        return self.update(edit)

    def put_list(self, name: str, values: dict[str, Any] | None) -> Compiled:
        """A list's values and phrases in the app's language (None: none);
        its phrases in other languages stay."""

        def edit(document: dict[str, Any]) -> None:
            lists = document["lists"]
            here, default = languages.current().code, file_language(document)
            old = plain(lists.get(name))
            # Each value's phrases, by language.
            if isinstance(old, list):
                current = {str(v): {default: v} for v in old}
            elif isinstance(old, dict):
                current = {v: (dict(s) if isinstance(s, dict) else {default: s}) for v, s in old.items()}
            else:
                current = {}
            new = values or {}
            for value in [*current, *(v for v in new if v not in current)]:
                said = current.setdefault(value, {})
                if value in new:
                    said[here] = new[value]
                else:
                    said.pop(here, None)
            kept = {v: (s[default] if set(s) == {default} else s) for v, s in current.items() if s}
            if kept:
                lists[name] = kept
            else:
                lists.pop(name, None)  # no phrases in any language any more

        return self.update(edit)

    def _write(self, document: dict[str, Any]) -> None:
        text = _dumps(document)
        _write_atomic(self.commands_path, text)
        self._document = json.loads(text)  # its own copy: callers keep theirs

    # ---------------------------------------------------- satellites, settings

    def load_extras(self) -> None:
        self.satellites = _read(self.satellites_path) or {}
        self.settings = {**DEFAULT_SETTINGS, **(_read(self.settings_path) or {})}
        self.wake_tuning = _read(self.wake_tuning_path) or {}
        self.people = _read(self.people_path) or {}

    def save_satellites(self) -> None:
        _write_atomic(self.satellites_path, _dumps(self.satellites))

    def save_settings(self) -> None:
        _write_atomic(self.settings_path, _dumps(self.settings))

    def save_wake_tuning(self) -> None:
        _write_atomic(self.wake_tuning_path, _dumps(self.wake_tuning))

    def save_people(self) -> None:
        _write_atomic(self.people_path, _dumps(self.people))

    def satellite(self, key: str) -> dict[str, Any]:
        return self.satellites.get(key) or {}


def _inserted(items: dict[str, Any], index: int, key: str, value: Any) -> dict[str, Any]:
    """``items`` with ``key`` at ``index``."""
    pairs = [(k, v) for k, v in items.items() if k != key]
    pairs.insert(index, (key, value))
    return dict(pairs)


def _dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def _read(path: Path) -> Any:
    """A JSON file's data; else, the first time, the YAML file of before
    (``commands.yaml`` for ``commands.json``), copied to it; None without
    either."""
    if path.exists():
        return json.loads(path.read_text("utf-8"))
    before = path.with_suffix(".yaml")
    if not before.exists():
        return None
    try:
        data = plain(_yaml().load(before.read_text("utf-8"))) or {}
    except Exception as err:  # noqa: BLE001 - ruamel's errors are its own
        raise ConfigError(f"{before.name}: {err}") from err
    _write_atomic(path, _dumps(data))
    _LOGGER.info("%s copied to %s; %s is no longer read", before.name, path.name, before.name)
    return data


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(text)
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise
