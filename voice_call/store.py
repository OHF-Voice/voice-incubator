"""Where commands, satellites and settings live.

``commands.yaml`` holds the voice commands, shared names and lists. The web UI
writes it and people may edit it by hand: it is read and written round-trip,
so comments and order survive a save from the UI, and a hand edit is picked up
without a restart. ``satellites.yaml``, ``settings.yaml`` and
``wake_words.yaml`` (what the Wake words page changed, per wake word) are the
UI's own.
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

STORE_KEYS = {"language", "commands", "names", "lists", "unknown", "settings"}
NAME_KINDS = ("entities", "areas", "floors")

NEW_FILE = """\
# Voice commands for voice_call. The web UI writes this file, and you can edit
# it too: comments survive, and changes are picked up without a restart.
#
#   language:  what the commands are said in (no key: English); a command
#              made in another language says its own (language: de). Only
#              the app's language's are heard and shown.
#   names:     what things are called, each pointing at one Home Assistant id;
#              in another language than the file's under its code
#              (kitchen: {{name: Kitchen, de: {{name: Küche}}}})
#   lists:     value lists for slots ("red" -> red), shared by commands
#   commands:  what is listened for, and what happens
#   unknown:   what is said when nothing understood a sentence
language: {language}
names: {{}}
lists: {{}}
commands: {{}}
"""

DEFAULT_SETTINGS: dict[str, Any] = {
    # Debugging: keep every call's audio and full trace on disk.
    "keep_audio": False,
    # Oldest saved calls are deleted beyond this many.
    "max_saved_calls": 200,
}


def _yaml() -> YAML:
    yaml = YAML()  # round-trip, YAML 1.2: "on" and "off" stay words
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
    data = {**data, "names": said}
    commands = parse_commands({k: v for k, v in every.items() if str(k) not in hidden})
    unknown_response = parse_unknown(data.get("unknown"))
    config = adapt_config(data, commands)
    if config is not None:
        parse_config(config)  # adapt-ctc's own checks: keywords, names, numbers
    return Compiled(
        data, commands, config, shared_examples(commands)[1], unknown_response, language, hidden, hidden_names
    )


@dataclass
class Store:
    data_dir: Path
    satellites: dict[str, dict[str, Any]] = field(default_factory=dict)
    settings: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_SETTINGS))
    wake_tuning: dict[str, dict[str, Any]] = field(default_factory=dict)
    """Per wake word: its cutoff, confusables, pronunciations and thresholds,
    where the Wake words page changed them."""

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._mtime: float | None = None

    @property
    def commands_path(self) -> Path:
        return self.data_dir / "commands.yaml"

    @property
    def satellites_path(self) -> Path:
        return self.data_dir / "satellites.yaml"

    @property
    def settings_path(self) -> Path:
        return self.data_dir / "settings.yaml"

    @property
    def wake_tuning_path(self) -> Path:
        return self.data_dir / "wake_words.yaml"

    @property
    def calls_dir(self) -> Path:
        return self.data_dir / "calls"

    # ------------------------------------------------------------- commands

    def read_text(self) -> str:
        if not self.commands_path.exists():
            _write_atomic(self.commands_path, NEW_FILE.format(language=languages.current().code))
        return self.commands_path.read_text(encoding="utf-8")

    def load(self) -> Compiled:
        """The commands file, validated. Raises ConfigError (or a YAML error)."""
        text = self.read_text()
        self._mtime = self.commands_path.stat().st_mtime
        return compile_store(plain(self._parse(text)))

    def changed_on_disk(self) -> bool:
        try:
            return self.commands_path.stat().st_mtime != self._mtime
        except FileNotFoundError:
            return False

    @staticmethod
    def _parse(text: str) -> CommentedMap:
        data = _yaml().load(text) or CommentedMap()
        if not isinstance(data, dict):
            raise ConfigError("commands file: expected a mapping")
        return data

    def save_text(self, text: str) -> Compiled:
        """Replace the whole file, after validating it."""
        compiled = compile_store(plain(self._parse(text)))
        self._write(text)
        return compiled

    def update(self, edit) -> Compiled:
        """Apply ``edit(document)`` to the round-trip document, validate the
        result and save it. ``edit`` changes the CommentedMap in place."""
        document = self._parse(self.read_text())
        for key in ("names", "lists", "commands"):
            if document.get(key) is None:
                document[key] = CommentedMap()
        edit(document)
        compiled = compile_store(plain(document))
        stream = io.StringIO()
        _yaml().dump(document, stream)
        self._write(stream.getvalue())
        return compiled

    def put_command(self, command_id: str, spec: dict[str, Any], old_id: str | None = None) -> Compiled:
        """Save a command. A new one is in the app's language: it says so
        (``language:``) when that is not the file's."""

        def edit(document: CommentedMap) -> None:
            commands = document["commands"]
            here, default = languages.current().code, file_language(document)
            taken = commands.get(command_id)
            if taken is not None and command_language(taken, default) != here:
                raise ConfigError(f"“{command_id}” is taken by a command in {language_name(command_language(taken, default))}; pick another id")
            said = {**spec}
            if "language" not in said and here != default:
                said["language"] = here
            node = styled(said)
            if old_id and old_id != command_id and old_id in commands:
                # Renamed: keep its place in the file.
                index = list(commands).index(old_id)
                del commands[old_id]
                commands.insert(index, command_id, node)
            else:
                commands[command_id] = node

        return self.update(edit)

    def duplicate_command(
        self, command_id: str, new_id: str, title: str | None = None, satellite: str | None = None
    ) -> Compiled:
        """Copy a command, as saved, to ``new_id``, right after it: on
        ``satellite`` (its key), or the Home's (None)."""

        def edit(document: CommentedMap) -> None:
            commands = document["commands"]
            if command_id not in commands:
                raise FileNotFoundError(f"No command {command_id}")
            if new_id in commands:
                raise ConfigError(f"There is already a command {new_id!r}")
            spec = plain(commands[command_id])
            if title:
                spec["title"] = title
            if satellite:
                spec["satellite"] = satellite
            else:
                spec.pop("satellite", None)
            commands.insert(list(commands).index(command_id) + 1, new_id, styled(spec))

        return self.update(edit)

    def delete_command(self, command_id: str) -> Compiled:
        def edit(document: CommentedMap) -> None:
            document["commands"].pop(command_id, None)

        return self.update(edit)

    def put_fallback(self, steps: dict[str, list[str]]) -> Compiled:
        """For each fallback step given (``meaning``, ``llm``), exactly which
        commands it may run (their ``fallback`` key)."""
        def edit(document: CommentedMap) -> None:
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
                    command["fallback"] = styled(keep)
                else:
                    command.pop("fallback", None)

        return self.update(edit)

    def put_unknown(self, response: str | None) -> Compiled:
        """What is said when nothing understood a sentence; None or empty:
        the built-in hint."""

        def edit(document: CommentedMap) -> None:
            if response:
                document["unknown"] = styled({"response": response})
            else:
                document.pop("unknown", None)

        return self.update(edit)

    def put_name(self, kind: str, item_id: str, spec: dict[str, Any] | None) -> Compiled:
        if kind not in NAME_KINDS:
            raise ConfigError(f"names: unknown kind {kind!r}")

        def edit(document: CommentedMap) -> None:
            names = document["names"]
            if names.get(kind) is None:
                names[kind] = CommentedMap()
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
                items[item_id] = styled(kept)
            else:
                items.pop(item_id, None)  # named in no language any more

        return self.update(edit)

    def put_list(self, name: str, values: dict[str, Any] | None) -> Compiled:
        def edit(document: CommentedMap) -> None:
            if values is None:
                document["lists"].pop(name, None)
            else:
                document["lists"][name] = styled(values)

        return self.update(edit)

    def _write(self, text: str) -> None:
        _write_atomic(self.commands_path, text)
        self._mtime = self.commands_path.stat().st_mtime

    # ---------------------------------------------------- satellites, settings

    def load_extras(self) -> None:
        yaml = _yaml()
        if self.satellites_path.exists():
            self.satellites = plain(yaml.load(self.satellites_path.read_text("utf-8")) or {})
        if self.settings_path.exists():
            loaded = plain(yaml.load(self.settings_path.read_text("utf-8")) or {})
            self.settings = {**DEFAULT_SETTINGS, **loaded}
        if self.wake_tuning_path.exists():
            self.wake_tuning = plain(yaml.load(self.wake_tuning_path.read_text("utf-8")) or {})

    def save_satellites(self) -> None:
        _write_atomic(self.satellites_path, dump(self.satellites))

    def save_settings(self) -> None:
        _write_atomic(self.settings_path, dump(self.settings))

    def save_wake_tuning(self) -> None:
        _write_atomic(self.wake_tuning_path, dump(self.wake_tuning))

    def satellite(self, key: str) -> dict[str, Any]:
        return self.satellites.get(key) or {}


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
