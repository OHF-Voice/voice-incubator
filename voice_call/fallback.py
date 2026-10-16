"""When no command matched: the closest one by meaning, then a small LLM.

The chain, in order, each step only when the one before found nothing:

1. **CTC** (adapt-ctc): keywords and example sentences. Most commands end here.
2. **Meaning** (EmbeddingGemma, already loaded for hints): the closest command
   is taken only when it is very close (``EMBED_ACCEPT``), well ahead of the
   runner-up (``EMBED_MARGIN``), and needs nothing heard -- a command whose
   slots are all fixed or optional. Off unless ``embedding_accept`` is set.
3. **LLM** (Hammer 2.1 0.5B, llama.cpp): every enabled command is a tool, its
   slots the parameters -- names as their spoken names, value lists as their
   values, numbers, durations and free text as text. The call is then
   checked like a match: the command must be available on the satellite,
   names map back to ids, values to list values, and nothing required may be
   missing.
4. **Fail**: the hint, as before.

The LLM is given every command, whatever the satellite, so the system and
tools prefix is evaluated once and reused by every request (llama.cpp keeps
the longest common prefix of its context). Availability is checked after.
"""

from __future__ import annotations

import io
import json
import logging
import re
import threading
import time
import tokenize
from ast import literal_eval
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .commands import Command, match_slots

_LOGGER = logging.getLogger(__name__)

EMBED_ACCEPT = 0.80
"""Cosine similarity at which the closest command by meaning is run."""
EMBED_MARGIN = 0.05
"""How far ahead of the runner-up (another command, or an everyday request
that is not a command) it must be."""

HAMMER_REPO = "Melvin56/Hammer2.1-0.5b-GGUF"
HAMMER_FILE = "hammer2.1-0.5b-Q8_0.gguf"
MAX_TOKENS = 96
CONTEXT_OVERHEAD = 384 + MAX_TOKENS + 128
"""Hammer's template wraps the tools in instructions; plus the reply and the
request itself."""

SYSTEM_PROMPT = (
    "You control a smart home by voice. Call the one tool that does what the "
    "sentence asks. Give only the arguments the sentence says, in its words. "
    "If no tool does what it asks, or it is a question no tool answers, call none."
)
USER_PROMPT = 'Sentence: "{text}"'


# ----------------------------------------------------------------- the tools


@dataclass
class Parameter:
    """How one tool argument becomes one slot."""

    slot: str
    kind: str
    """name | number | duration | list | text"""
    choices: dict[str, str] = field(default_factory=dict)
    """For names and lists: what may be said (lowercase) -> id or value."""
    many: bool = False
    dynamic: bool = False
    """A list the app keeps (running timers): any value is taken as said."""


class Tools:
    """Every enabled command as an LLM tool, and back."""

    def subset(self, command_ids: list[str]) -> list[dict[str, Any]]:
        """These commands' tools, in this order."""
        by_name = {spec["function"]["name"]: spec for spec in self.specs}
        return [by_name[c] for c in command_ids if c in by_name]

    def __init__(self, compiled: Any, dynamic: dict[str, list[str]] | None = None) -> None:
        self.compiled = compiled
        data = compiled.data or {}
        self.names: dict[str, dict[str, Any]] = data.get("names") or {}
        self.lists: dict[str, Any] = data.get("lists") or {}
        self.dynamic = dynamic or {}
        self.specs: list[dict[str, Any]] = []
        self.parameters: dict[str, dict[str, Parameter]] = {}
        self.required: dict[str, list[str]] = {}
        for command in sorted(compiled.commands.values(), key=lambda c: c.id):
            if command.disabled:
                continue
            spec, parameters, required = self._tool(command)
            self.specs.append(spec)
            self.parameters[command.id] = parameters
            self.required[command.id] = required

    @property
    def signature(self) -> str:
        return json.dumps(self.specs, sort_keys=True)

    # ---------------------------------------------------------------- out

    def _tool(self, command: Command) -> tuple[dict[str, Any], dict[str, Parameter], list[str]]:
        intent = command.intent
        properties: dict[str, Any] = {}
        parameters: dict[str, Parameter] = {}
        required: list[str] = []
        fixed = set(command.behavior.slots)

        for kind, spec in (intent.get("names") or {}).items():
            if kind in fixed:
                continue
            spec = spec if isinstance(spec, dict) else {"count": spec}
            choices = self._name_choices(kind, spec)
            if not choices:
                continue
            many = int(spec.get("count") or 1) > 1
            item = {"type": "string", "enum": sorted({self._label(kind, i) for i in set(choices.values())})}
            properties[kind] = (
                {"type": "array", "items": item, "description": f"which {kind}s"}
                if many
                else {**item, "description": f"which {kind}"}
            )
            parameters[kind] = Parameter(kind, "name", choices, many)
            if not spec.get("optional") and int(spec.get("min", spec.get("count") or 0) or 0) > 0:
                required.append(kind)

        if "number" in intent:
            number = intent["number"] if isinstance(intent["number"], dict) else {}
            slot = command.number_slot
            if command.duration:
                properties[slot] = {"type": "string", "description": "how long, like '10 minutes'"}
                parameters[slot] = Parameter(slot, "duration")
            else:
                prop: dict[str, Any] = {"type": "number"}
                if "min" in number:
                    prop["minimum"] = number["min"]
                if "max" in number:
                    prop["maximum"] = number["max"]
                properties[slot] = prop
                parameters[slot] = Parameter(slot, "number")
            if not number.get("optional"):
                required.append(slot)

        for slot, spec in (intent.get("lists") or {}).items():
            listed = spec if isinstance(spec, str) else (spec or {}).get("list")
            optional = isinstance(spec, dict) and spec.get("optional")
            choices = self._list_choices(str(listed))
            prop = {"type": "string"}
            if listed in self.dynamic:
                # Not its values: they change, and the tools are a cached prefix.
                prop["description"] = _DYNAMIC_HELP.get(str(listed), f"a {slot}")
            elif choices:
                prop["enum"] = sorted(set(choices.values()))
            properties[slot] = prop
            parameters[slot] = Parameter(slot, "list", choices, dynamic=listed in self.dynamic)
            if not optional:
                required.append(slot)

        wildcard = intent.get("wildcard")
        if wildcard and wildcard.get("slot") not in parameters:
            slot = wildcard["slot"]
            properties[slot] = {"type": "string", "description": _WILDCARD_HELP.get(slot, f"the {slot}, as said")}
            parameters[slot] = Parameter(slot, "text")
            if wildcard.get("required"):
                required.append(slot)

        description = _description(command)
        spec = {
            "type": "function",
            "function": {
                "name": command.id,
                "description": description,
                "parameters": {"type": "object", "properties": properties, "required": required},
            },
        }
        return spec, parameters, required

    def _name_choices(self, kind: str, spec: dict[str, Any]) -> dict[str, str]:
        table = self.names.get({"entity": "entities", "area": "areas", "floor": "floors"}.get(kind, kind)) or {}
        ids = set(spec.get("ids") or []) or None
        domains = set(spec.get("domains") or []) or None
        out: dict[str, str] = {}
        for item_id, item in table.items():
            if ids is not None and item_id not in ids:
                continue
            if domains is not None and item_id.split(".", 1)[0] not in domains:
                continue
            item = item or {}
            for said in [item.get("name") or item_id, *(item.get("aliases") or [])]:
                out[_key(said)] = item_id
        return out

    def _label(self, kind: str, item_id: str) -> str:
        table = self.names.get({"entity": "entities", "area": "areas", "floor": "floors"}.get(kind, kind)) or {}
        return str((table.get(item_id) or {}).get("name") or item_id)

    def _list_choices(self, listed: str) -> dict[str, str]:
        if listed in self.dynamic:
            return {_key(v): v for v in self.dynamic[listed]}
        values = self.lists.get(listed) or {}
        out: dict[str, str] = {}
        if isinstance(values, list):
            return {_key(v): str(v) for v in values}
        for value, phrases in values.items():
            out[_key(value)] = str(value)
            for phrase in phrases if isinstance(phrases, list) else [phrases]:
                out[_key(phrase)] = str(value)
        return out

    # ----------------------------------------------------------------- in

    def slots(
        self, command: Command, arguments: dict[str, Any], text: str | None = None
    ) -> tuple[dict[str, Any], list[str]]:
        """A tool call's arguments as the command's slots, and what was wrong
        with them (unknown names or values, missing required ones). With the
        sentence (``text``), names, values and free text it does not say are
        dropped: a small model fills in arguments nobody gave."""
        parameters = self.parameters.get(command.id, {})
        required = set(self.required.get(command.id, []))
        heard: dict[str, Any] = {}
        problems: list[str] = []
        dropped: list[str] = []
        for name, value in (arguments or {}).items():
            parameter = parameters.get(name)
            if parameter is None or value in (None, "", []):
                continue
            # A bad value for an argument that may be left out is left out.
            problems_before = len(problems)
            self._convert(parameter, name, value, text, heard, problems)
            if len(problems) > problems_before and name not in required:
                dropped.extend(problems[problems_before:])
                del problems[problems_before:]
        slots = match_slots(command, heard)
        missing = [r for r in required if r not in slots]
        problems += [f"no {m}" for m in missing]
        return slots, problems

    def _convert(
        self, parameter: Parameter, name: str, value: Any, text: str | None, heard: dict[str, Any], problems: list[str]
    ) -> None:
        """One argument into ``heard``, or what is wrong with it into ``problems``."""
        if parameter.kind == "name":
            values = value if isinstance(value, list) else [value]
            ids = [parameter.choices.get(_key(v)) for v in values]
            if None in ids:
                problems.append(f"no {name} named {value!r}")
                return
            if text is not None and not all(_heard(v, text, parameter.choices) for v in values):
                return
            heard[name] = list(dict.fromkeys(i for i in ids if i))
        elif parameter.kind == "list":
            found = parameter.choices.get(_key(value))
            if found is None and not parameter.dynamic:
                problems.append(f"{value!r} is not a {name}")
                return
            if text is not None and not _heard(value, text, parameter.choices if found else None):
                return
            heard[name] = found or str(value)
        elif parameter.kind == "number":
            number = _number(value)
            if number is None:
                problems.append(f"{name} {value!r} is not a number")
                return
            heard["number"] = number
        elif parameter.kind == "duration":
            seconds = parse_duration(value)
            if not seconds:
                problems.append(f"{name} {value!r} is not a duration")
                return
            hours, rest = divmod(int(seconds), 3600)
            minutes, secs = divmod(rest, 60)
            heard.update(hours=hours, minutes=minutes, seconds=secs)
        else:
            # Free text must be in the sentence; a clock time may be
            # written differently ("six" -> "6:00 am"), its parser checks it,
            # but not with an am or pm the sentence did not say.
            value = str(value).strip()
            if name == "time" and text is not None and not _MERIDIEM.search(text):
                value = _MERIDIEM.sub("", value).strip()
            elif text is not None and not _heard(value, text):
                return
            heard[name] = value


_DYNAMIC_HELP = {"running_timers": "a running timer's name"}

_WILDCARD_HELP = {
    "query": "what to play: an artist, album, song, playlist or genre",
    "item": "the item, as said",
    "time": "the clock time, like '7:30 am'",
    "timer": "the timer's name, if it has one",
    "reminder": "what to remember, as said",
}


def _description(command: Command) -> str:
    """What the command does, and how it is said: its examples, else its
    keywords (the first of each required group)."""
    intent = command.intent
    said = list(intent.get("examples") or [])[:2]
    if not said and intent.get("require"):
        words = []
        for group in intent["require"]:
            first = group[0] if isinstance(group, list) else group
            words.append(str(first).replace(" ... ", " "))
        said = [" ".join(words)]
    text = command.description[0].upper() + command.description[1:]
    if said:
        text += ". Said like: " + ", ".join(f'"{s}"' for s in said)
    return text


def _heard(value: Any, text: str, choices: dict[str, str] | None = None) -> bool:
    """Whether the sentence says this argument: a name, value or free text the
    LLM did not make up. With ``choices``, any way of saying the same id."""
    said = f" {_key(text)} "
    if choices is not None:
        target = choices.get(_key(value))
        return any(f" {phrase} " in said for phrase, owner in choices.items() if owner == target)
    words = [w for w in _key(value).split() if w not in _FILLER]
    return bool(words) and sum(f" {w} " in said for w in words) >= max(1, len(words) - 1)


_FILLER = {"the", "a", "an", "my", "some", "of", "to", "please"}
_MERIDIEM = re.compile(r"\b(?:a\.?m\.?|p\.?m\.?|morning|evening|afternoon|night|tonight)\b", re.I)


def _key(text: Any) -> str:
    return " ".join(re.sub(r"[^\w']+", " ", str(text).lower()).split())


def _number(value: Any) -> float | int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    found = re.search(r"-?\d+(\.\d+)?", str(value))
    if not found:
        return None
    number = float(found.group(0))
    return int(number) if number.is_integer() else number


_UNITS = {"second": 1, "sec": 1, "minute": 60, "min": 60, "hour": 3600, "hr": 3600}
_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30,
    "forty": 40, "forty five": 45, "fifty": 50, "sixty": 60, "ninety": 90, "half": 0.5, "a half": 0.5,
}


def parse_duration(value: Any) -> float | None:
    """Seconds from "10 minutes", "1 hour and 30 minutes", "an hour and a
    half", "90 seconds", "00:10:00" or a number of seconds."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) if value > 0 else None
    text = str(value).lower().strip()
    clock = re.fullmatch(r"(\d+):(\d{2})(?::(\d{2}))?", text)
    if clock:
        parts = [int(p) for p in clock.groups() if p is not None]
        if len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
        return parts[0] * 60 + parts[1]
    total = 0.0
    last_unit = None
    for amount, unit in re.findall(r"(\d+(?:\.\d+)?|[a-z]+(?: [a-z]+)?)\s*(hours?|hrs?|minutes?|mins?|seconds?|secs?)", text):
        number = float(amount) if amount[0].isdigit() else _WORDS.get(amount.split()[-1], _WORDS.get(amount))
        if number is None:
            continue
        unit_seconds = _UNITS[re.sub(r"s$", "", unit)]
        total += number * unit_seconds
        last_unit = unit_seconds
    if "and a half" in text and last_unit:
        total += last_unit / 2
    if not total and re.fullmatch(r"\d+(\.\d+)?", text):
        total = float(text)
    return total or None


# ------------------------------------------------------------------ the LLM


def parse_hammer(text: str) -> list[tuple[str, dict[str, Any]]]:
    """Hammer's fenced list of JSON (or Python-literal) function calls."""
    value = text.strip()
    if value.startswith("```") and value.endswith("```"):
        value = value[3:-3].strip()
        if value.startswith("json"):
            value = value[4:].lstrip()
    if not value:
        return []
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        try:
            tokens = [
                token._replace(string={"true": "True", "false": "False", "null": "None"}.get(token.string, token.string))
                if token.type == tokenize.NAME
                else token
                for token in tokenize.generate_tokens(io.StringIO(value).readline)
            ]
            decoded = literal_eval(tokenize.untokenize(tokens))
        except (SyntaxError, tokenize.TokenError, ValueError):
            return []
    if isinstance(decoded, dict):
        decoded = [decoded]
    if not isinstance(decoded, list):
        return []
    calls = []
    for raw in decoded:
        if not isinstance(raw, dict):
            continue
        if isinstance(raw.get("function"), dict):
            raw = raw["function"]
        name, arguments = raw.get("name"), raw.get("arguments") or {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}
        if isinstance(name, str) and isinstance(arguments, dict):
            calls.append((name, arguments))
    return calls


class Hammer:
    """Hammer 2.1 0.5B in llama.cpp, with the tools as a reused prefix. Calls
    are serialized; ``load`` and ``call`` are slow, run them in a thread."""

    def __init__(self, model_path: Path | None = None, n_threads: int = 2) -> None:
        self.model_path = model_path
        self.n_threads = n_threads
        self.llm: Any = None
        self.tools: list[dict[str, Any]] = []
        self.signature: str | None = None
        self.load_seconds: float | None = None
        self._lock = threading.Lock()
        self.error: str | None = None

    @staticmethod
    def find_model(download: bool = False, cache_dir: Path | None = None) -> Path | None:
        """The GGUF in the Hugging Face cache, fetched first when asked to."""
        return find_gguf(HAMMER_REPO, HAMMER_FILE, download, cache_dir)

    @staticmethod
    def available() -> bool:
        """Whether llama.cpp is installed."""
        try:
            import llama_cpp  # noqa: F401  # pylint: disable=import-outside-toplevel,unused-import
        except ImportError:
            return False
        return True

    def load(self, tools: list[dict[str, Any]], signature: str) -> None:
        """Load the model if needed, and evaluate the system and tools prefix."""
        from llama_cpp import Llama  # pylint: disable=import-outside-toplevel

        with self._lock:
            started = time.perf_counter()
            needed = _context_size(tools)
            if self.llm is None or self.llm.n_ctx() < needed:
                if self.model_path is None:
                    raise RuntimeError("No Hammer model")
                self.llm = None
                self.llm = Llama(
                    model_path=str(self.model_path),
                    n_ctx=needed,
                    n_threads=self.n_threads,
                    verbose=False,
                )
            self.tools = tools
            self.signature = signature
            # Hammer's template wants a message after the system one; the empty
            # user turn shares the whole prefix with real requests.
            self.llm.create_chat_completion(
                messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": ""}],
                tools=tools,
                max_tokens=1,
            )
            self.load_seconds = time.perf_counter() - started
            _LOGGER.info("Hammer ready: %d tools, context %d, %.1f s", len(tools), self.llm.n_ctx(), self.load_seconds)

    @property
    def ready(self) -> bool:
        return self.llm is not None and self.signature is not None

    def call(
        self, text: str, tools: list[dict[str, Any]] | None = None
    ) -> tuple[list[tuple[str, dict[str, Any]]], str, float]:
        """The tool calls for one sentence, the raw reply, and seconds. With
        ``tools``, only those (no cached prefix: they are evaluated each time)."""
        content = USER_PROMPT.format(text=text.strip())
        with self._lock:
            started = time.perf_counter()
            response = self.llm.create_chat_completion(
                messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": content}],
                tools=tools if tools is not None else self.tools,
                temperature=0.0,
                max_tokens=MAX_TOKENS,
            )
            seconds = time.perf_counter() - started
        message = response["choices"][0]["message"]
        raw = message.get("content") or ""
        calls = parse_hammer(raw)
        for tool_call in message.get("tool_calls") or []:
            function = tool_call.get("function") or {}
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = {}
            calls.append((function.get("name"), arguments))
        return calls, raw, seconds


def find_gguf(repo: str, filename: str, download: bool = False, cache_dir: Path | None = None) -> Path | None:
    """A model from Hugging Face. With ``download``: from the default cache (a
    checkout's) or else downloaded into ``cache_dir`` (the app's data);
    without, only what ``cache_dir`` already has."""
    try:
        from huggingface_hub import hf_hub_download  # pylint: disable=import-outside-toplevel
    except ImportError:
        return None
    places = [(None, False), (cache_dir, True)] if download else [(cache_dir, False)]
    for where, fetch in places:
        try:
            return Path(
                hf_hub_download(repo, filename, local_files_only=not fetch, cache_dir=str(where) if where else None)
            )
        except Exception:  # noqa: BLE001 - not there (yet), or no network
            continue
    return None


def _context_size(tools: list[dict[str, Any]]) -> int:
    """chars / 3 bounds the tokens; rounded up to 256."""
    chars = len(SYSTEM_PROMPT) + len(USER_PROMPT) + len(json.dumps(tools))
    return ((chars // 3 + CONTEXT_OVERHEAD) // 256 + 1) * 256


# ---------------------------------------------------------------- the chain


@dataclass
class Found:
    """What the fallback decided on, and how."""

    command: Command | None = None
    slots: dict[str, Any] = field(default_factory=dict)
    by: str | None = None
    """meaning | llm"""
    trace: dict[str, Any] = field(default_factory=dict)


def needs_heard_slots(command: Command) -> bool:
    """Whether running it needs something said: a required name, number,
    value or free text that is not fixed."""
    intent = command.intent
    fixed = set(command.behavior.slots)
    for kind, spec in (intent.get("names") or {}).items():
        spec = spec if isinstance(spec, dict) else {"count": spec}
        if kind not in fixed and not spec.get("optional") and int(spec.get("count") or 0) > 0:
            return True
    number = intent.get("number")
    if number is not None and not (isinstance(number, dict) and number.get("optional")):
        return True
    for spec in (intent.get("lists") or {}).values():
        if not (isinstance(spec, dict) and spec.get("optional")):
            return True
    wildcard = intent.get("wildcard")
    return bool(wildcard and wildcard.get("required"))


def by_meaning(closest: dict[str, Any] | None, command: Command | None) -> tuple[bool, dict[str, Any]]:
    """Whether the closest command by meaning may run, and why (not)."""
    if closest is None or command is None:
        return False, {"why": "nothing close"}
    similarity = float(closest.get("similarity") or 0.0)
    margin = similarity - float(closest.get("next_similarity") or 0.0)
    trace = {"command": command.id, "similarity": round(similarity, 4), "margin": round(margin, 4)}
    if similarity < EMBED_ACCEPT:
        return False, {**trace, "why": "not close enough"}
    if margin < EMBED_MARGIN:
        return False, {**trace, "why": f"too close to {closest.get('next')}"}
    if needs_heard_slots(command):
        return False, {**trace, "why": "needs something said"}
    return True, trace


class Fallback:
    """Meaning, then the LLM, for one engine. ``prepare`` after every build
    (new commands are new tools); ``decide`` from the engine's thread."""

    def __init__(
        self, embedding_accept: bool = False, hammer: Hammer | None = None, top_k: int | None = None
    ) -> None:
        self.embedding_accept = embedding_accept
        self.hammer = hammer
        self.top_k = top_k
        """Give the LLM only the commands closest in meaning, this many."""
        self.tools: Tools | None = None
        self.signature: str | None = None
        """What the LLM must have been loaded for."""
        self.status = "off" if hammer is None else "waiting for the commands"
        self._thread: threading.Thread | None = None

    def prepare(self, engine: Any, wait: bool = False) -> None:
        """New commands: new tools. With every command as a tool, their prefix
        is evaluated again; with the closest few, the context is sized for
        the longest few."""
        compiled = engine.compiled
        if compiled is None:
            return
        tools = Tools(compiled, engine.dynamic)
        self.tools = tools
        if self.top_k:
            longest = sorted(tools.specs, key=lambda spec: -len(json.dumps(spec)))[: self.top_k]
            specs, signature = longest, f"closest {self.top_k}, context {_context_size(longest)}"
        else:
            specs, signature = tools.specs, tools.signature
        self.signature = signature
        hammer = self.hammer
        if hammer is None or (hammer.ready and hammer.signature == signature):
            return

        def load() -> None:
            try:
                hammer.load(specs, signature)
                self.status = "ready"
            except Exception as err:  # noqa: BLE001 - the chain works without it
                self.status = f"not available: {err}"
                _LOGGER.exception("Hammer could not be loaded")

        self.status = "loading"
        if wait:
            load()
        else:
            self._thread = threading.Thread(target=load, name="hammer", daemon=True)
            self._thread.start()

    def decide(self, text: str, closest: Any, compiled: Any, satellite: Any, ranked: Any = None) -> Found:
        """``closest()`` is the closest command by meaning, ``ranked()`` every
        command by meaning (each costs an embedding, so only asked for when
        needed)."""
        found = Found()
        if self.embedding_accept:
            near = closest()
            command = compiled.commands.get(near["intent"]) if near else None
            accept, why = by_meaning(near, command)
            found.trace["meaning"] = why
            if accept and command is not None and satellite.available(command) is not None:
                return Found(command, {}, "meaning", found.trace)
        hammer = self.hammer
        if hammer is None or self.tools is None or not text.strip():
            return found
        if not hammer.ready or hammer.signature != self.signature:
            found.trace["llm"] = {"skipped": self.status}
            return found
        subset = None
        trace: dict[str, Any] = {}
        if self.top_k and ranked is not None:
            order = [c for c, _ in ranked() if c in compiled.commands and satellite.available(compiled.commands[c])]
            if order:
                subset = self.tools.subset(order[: self.top_k])
                trace["candidates"] = order[: self.top_k]
        calls, raw, seconds = hammer.call(text, subset)
        trace.update(raw=raw, seconds=round(seconds, 3), calls=[[n, a] for n, a in calls])
        found.trace["llm"] = trace
        for name, arguments in calls:
            command = compiled.commands.get(name)
            if command is None or command.disabled:
                trace.setdefault("problems", []).append(f"no command {name}")
                continue
            slots, problems = self.tools.slots(command, arguments, text)
            trace["command"] = command.id
            if problems:
                trace["problems"] = problems
                return found
            if satellite.available(command) is None:
                trace["problems"] = [satellite.why_unavailable(command)]
                return found
            return Found(command, slots, "llm", found.trace)
        return found
