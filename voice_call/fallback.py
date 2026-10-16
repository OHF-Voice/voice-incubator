"""When no command matched: the closest one by meaning, then a small LLM.

The chain, in order, each step only when the one before found nothing:

1. **CTC** (adapt-ctc): keywords and example sentences. Most commands end here.
2. **Meaning** (EmbeddingGemma, already loaded for hints): the closest command
   is taken only when it is very close (``EMBED_ACCEPT``), well ahead of the
   runner-up (``EMBED_MARGIN``), and needs nothing heard -- a command whose
   slots are all fixed or optional. Off unless ``embedding_accept`` is set.
3. **LLM** (Granite 4.0 350M or Hammer 2.1 0.5B, llama.cpp): commands are
   tools, their slots the parameters -- names as their spoken names, value
   lists as their values, numbers, durations and free text as text. The call
   is then checked like a match: the command must be available on the
   satellite, names map back to ids, values to list values, and nothing
   required may be missing.
4. **Fail**: the hint, as before.

Both steps are opt-in, per command (``fallback: [meaning, llm]``): a command
nobody chose for a step is never run by it.

With every chosen command as a tool, whatever the satellite, the system and
tools prefix is evaluated once and reused by every request (llama.cpp keeps
the longest common prefix of its context); availability is checked after.
With the closest few, they are evaluated each time.
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
from .skills.common import FRACTIONS, SCALES, WORD_NUM, parse_num

_LOGGER = logging.getLogger(__name__)

EMBED_ACCEPT = 0.80
"""Cosine similarity at which the closest command by meaning is run."""
EMBED_MARGIN = 0.05
"""How far ahead of the runner-up (another command, or an everyday request
that is not a command) it must be."""
LLM_MIN_SIMILARITY = 0.65
"""What the LLM picks must be at least this close in meaning to the sentence
(a small model plays "what's the capital of France" as music)..."""
LLM_BARE_MIN_SIMILARITY = 0.70
"""...and this close when it takes nothing from the sentence ("thank you" ran
"what time is it"). On the fallback benchmark the right picks were all at
0.678 or more; these refused half of Granite's wrong ones, a third of
Hammer's."""

@dataclass(frozen=True)
class ToolModelInfo:
    name: str
    repo: str
    file: str
    mb: int
    licence: str
    way_out: bool
    """Give it ``not_a_command`` (NOT_A_COMMAND_TOOL) and ask for that, not for
    no call. Granite needs it; Hammer, trained to answer ``[]``, does better
    without."""


TOOL_MODELS = {
    # Granite is the question model too: one download, and (memory-mapped)
    # one copy.
    "granite-4.0-350m": ToolModelInfo(
        "Granite 4.0 350M", "ibm-granite/granite-4.0-350m-GGUF", "granite-4.0-350m-Q8_0.gguf", 370, "Apache-2.0", True
    ),
    "hammer-2.1-0.5b": ToolModelInfo(
        "Hammer 2.1 0.5B", "Melvin56/Hammer2.1-0.5b-GGUF", "hammer2.1-0.5b-Q8_0.gguf", 531, "CC-BY-NC-4.0", False
    ),
}
DEFAULT_TOOL_MODEL = "granite-4.0-350m"
MAX_TOKENS = 96
CONTEXT_OVERHEAD = 384 + MAX_TOKENS + 128
"""The chat template wraps the tools in instructions (Hammer's are the
longest); plus the reply and the request itself."""
CALL_STARTS = ("<tool_call>", "```", "[", "{")
"""How a reply with a call begins; anything else is a refusal in words, and
is not waited for."""

SYSTEM_PROMPT = (
    "You control a smart home by voice. Call the one tool that does what the "
    "sentence asks. Give only the arguments the sentence says, in its words. "
    "If no tool does what it asks, or it is a question no tool answers, call none."
)
WAY_OUT_PROMPT = (
    "You control a smart home by voice. Call the one tool that does what the "
    "sentence asks. Give only the arguments the sentence says, in its words. "
    "If no other tool does what it asks -- a question about the world, small "
    "talk, thanks -- call not_a_command."
)
USER_PROMPT = 'Sentence: "{text}"'

NOT_A_COMMAND = "not_a_command"
NOT_A_COMMAND_TOOL = {
    "type": "function",
    "function": {
        "name": NOT_A_COMMAND,
        "description": "Nothing to do: the sentence is a question about the world, small talk, thanks, "
        "or asks for something none of the other tools does",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}
"""A way out. Granite calls some tool for almost any sentence ("what time is
it" for "what's the capital of France"); given one that means "none of
these", it takes it about half the time (scripts/bench_fallback.py)."""


def with_way_out(specs: list[dict[str, Any]], way_out: bool = True) -> list[dict[str, Any]]:
    """The tools, and last (the prefix stays the same) the way out -- unless a
    command has its name."""
    if not way_out or not specs or any(spec["function"]["name"] == NOT_A_COMMAND for spec in specs):
        return specs
    return [*specs, NOT_A_COMMAND_TOOL]


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
            if text is not None and float(number) not in said_numbers(text):
                return
            heard["number"] = number
        elif parameter.kind == "duration":
            seconds = parse_duration(value)
            if not seconds:
                problems.append(f"{name} {value!r} is not a duration")
                return
            said = parse_duration(text) if text is not None else seconds
            if said is None or abs(said - seconds) > 1:
                return
            hours, rest = divmod(int(seconds), 3600)
            minutes, secs = divmod(rest, 60)
            heard.update(hours=hours, minutes=minutes, seconds=secs)
        else:
            # Free text must be in the sentence; a clock time may be
            # written differently ("six" -> "6:00 am"), its parser checks it,
            # but not with an am or pm the sentence did not say.
            value = str(value).strip()
            if name == "time":
                if text is not None and not _MERIDIEM.search(text):
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


def said_numbers(text: str) -> set[float]:
    """Every number a sentence says, in digits or words, and every run of
    words within one ("two hundred and fifty" is 250, and 2, 200, 50)."""
    numeric = set(WORD_NUM) | set(SCALES) | {"and", "a", "an", "half", "quarter"} | set(FRACTIONS)
    out: set[float] = set()
    run: list[str] = []

    def flush() -> None:
        for start in range(len(run)):
            for end in range(start + 1, len(run) + 1):
                if run[start] in ("and", "a", "an") and end == start + 1:
                    continue  # alone, not a number
                value = parse_num(" ".join(run[start:end]))
                if value is not None:
                    out.add(float(value))
        run.clear()

    for word in re.findall(r"\d+(?:\.\d+)?|[a-z]+", str(text).lower().replace(",", "")):
        if word[0].isdigit() or word in numeric:
            run.append(word)
        else:
            flush()
    flush()
    return out


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
        if amount[0].isdigit():
            number = float(amount)
        elif amount.split()[0] == "half" and amount.split()[-1] in ("a", "an"):
            number = 0.5  # "half an hour"
        else:
            number = _WORDS.get(amount.split()[-1], _WORDS.get(amount))
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


def parse_calls(text: str) -> list[tuple[str, dict[str, Any]]]:
    """The tool calls in a reply: Granite's ``<tool_call>`` blocks (the last
    one may be cut at its closing tag, a stop sequence), Hammer's fenced list,
    or JSON objects one after another."""
    blocks = re.findall(r"<tool_call>(.*?)(?:</tool_call>|$)", text, re.S)
    if blocks:
        return [call for block in blocks for call in parse_hammer(block)]
    calls = parse_hammer(text)
    if calls:
        return calls
    decoder = json.JSONDecoder()
    value, at = text.strip(), 0
    while (start := value.find("{", at)) >= 0:
        try:
            decoded, at = decoder.raw_decode(value, start)
        except json.JSONDecodeError:
            break
        calls += parse_hammer(json.dumps(decoded))
    return calls


def llama_available() -> bool:
    """Whether llama.cpp is installed."""
    try:
        import llama_cpp  # noqa: F401  # pylint: disable=import-outside-toplevel,unused-import
    except ImportError:
        return False
    return True


class ToolModel:
    """A small LLM in llama.cpp that picks a command as a tool call, with the
    tools as a reused prefix. Calls are serialized; ``load`` and ``call`` are
    slow, run them in a thread."""

    def __init__(self, model_path: Path | None = None, n_threads: int = 2, model: str = DEFAULT_TOOL_MODEL) -> None:
        self.model_path = model_path
        self.n_threads = n_threads
        self.model = model
        info = TOOL_MODELS.get(model)
        self.way_out = bool(info and info.way_out)
        self.prompt = WAY_OUT_PROMPT if self.way_out else SYSTEM_PROMPT
        self.llm: Any = None
        self.tools: list[dict[str, Any]] = []
        self.signature: str | None = None
        self.load_seconds: float | None = None
        self._lock = threading.Lock()
        self.error: str | None = None

    @property
    def name(self) -> str:
        info = TOOL_MODELS.get(self.model)
        return info.name if info else self.model

    @staticmethod
    def find_model(
        model: str = DEFAULT_TOOL_MODEL, download: bool = False, cache_dir: Path | None = None
    ) -> Path | None:
        """The GGUF in the Hugging Face cache, fetched first when asked to."""
        info = TOOL_MODELS[model]
        return find_gguf(info.repo, info.file, download, cache_dir)

    def load(self, tools: list[dict[str, Any]], signature: str) -> None:
        """Load the model if needed, and evaluate the system and tools prefix."""
        from llama_cpp import Llama  # pylint: disable=import-outside-toplevel

        with self._lock:
            started = time.perf_counter()
            needed = _context_size(tools)
            if self.llm is None or self.llm.n_ctx() < needed:
                if self.model_path is None:
                    raise RuntimeError(f"No {self.name} model")
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
                messages=[{"role": "system", "content": self.prompt}, {"role": "user", "content": ""}],
                tools=tools,
                max_tokens=1,
            )
            self.load_seconds = time.perf_counter() - started
            _LOGGER.info(
                "%s ready: %d tools, context %d, %.1f s", self.name, len(tools), self.llm.n_ctx(), self.load_seconds
            )

    @property
    def ready(self) -> bool:
        return self.llm is not None and self.signature is not None

    def call(
        self, text: str, tools: list[dict[str, Any]] | None = None
    ) -> tuple[list[tuple[str, dict[str, Any]]], str, float]:
        """The tool calls for one sentence, the raw reply, and seconds. With
        ``tools``, only those (no cached prefix: they are evaluated each time).
        A reply that is not a call is cut short."""
        content = USER_PROMPT.format(text=text.strip())
        pieces: list[str] = []
        with self._lock:
            started = time.perf_counter()
            for chunk in self.llm.create_chat_completion(
                messages=[{"role": "system", "content": self.prompt}, {"role": "user", "content": content}],
                tools=tools if tools is not None else self.tools,
                temperature=0.0,
                max_tokens=MAX_TOKENS,
                stop=["</tool_call>"],
                stream=True,
            ):
                pieces.append((chunk["choices"][0].get("delta") or {}).get("content") or "")
                said = "".join(pieces).lstrip()
                if len(said) >= len(CALL_STARTS[0]) and not said.startswith(CALL_STARTS):
                    break
            seconds = time.perf_counter() - started
        raw = "".join(pieces)
        return parse_calls(raw), raw, seconds


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
    chars = max(len(SYSTEM_PROMPT), len(WAY_OUT_PROMPT)) + len(USER_PROMPT) + len(json.dumps(tools))
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


def chosen(compiled: Any, step: str) -> list[str]:
    """The commands chosen for a fallback step (``meaning`` or ``llm``), in
    id order."""
    return sorted(c.id for c in compiled.commands.values() if step in c.fallback and not c.disabled)


class Fallback:
    """Meaning, then the LLM, for one engine. ``prepare`` after every build
    (new commands are new tools); ``decide`` from the engine's thread."""

    def __init__(
        self, embedding_accept: bool = False, llm: ToolModel | None = None, top_k: int | None = None
    ) -> None:
        self.embedding_accept = embedding_accept
        self.llm = llm
        self.top_k = top_k
        """Give the LLM only the commands closest in meaning, this many --
        when more than this many are chosen for it."""
        self.tools: Tools | None = None
        self.chosen: list[str] = []
        """The commands chosen for the LLM."""
        self.closest_only = False
        """Whether it gets the closest ``top_k`` of them, not all."""
        self.signature: str | None = None
        """What the LLM must have been loaded for."""
        self.status = "off" if llm is None else "waiting for the commands"
        self._thread: threading.Thread | None = None

    def prepare(self, engine: Any, wait: bool = False) -> None:
        """New commands: new tools. With every chosen command as a tool, their
        prefix is evaluated again; with the closest few, the context is sized
        for the longest few."""
        compiled = engine.compiled
        if compiled is None:
            return
        tools = Tools(compiled, engine.dynamic)
        self.tools = tools
        self.chosen = chosen(compiled, "llm")
        everything = tools.subset(self.chosen)
        self.closest_only = bool(self.top_k) and len(everything) > int(self.top_k or 0)
        llm = self.llm
        way_out = bool(llm is not None and llm.way_out)
        if self.closest_only:
            longest = with_way_out(sorted(everything, key=lambda spec: -len(json.dumps(spec)))[: self.top_k], way_out)
            specs, signature = longest, f"closest {self.top_k}, context {_context_size(longest)}"
        else:
            specs = with_way_out(everything, way_out)
            signature = json.dumps(specs, sort_keys=True)
        self.signature = signature
        if llm is None:
            return
        if not specs:
            self.status = "no commands chosen for it"
            return
        if llm.ready and llm.signature == signature:
            self.status = "ready"
            return

        def load() -> None:
            try:
                llm.load(specs, signature)
                self.status = "ready"
            except Exception as err:  # noqa: BLE001 - the chain works without it
                self.status = f"not available: {err}"
                _LOGGER.exception("%s could not be loaded", llm.name)

        self.status = "loading"
        if wait:
            load()
        else:
            self._thread = threading.Thread(target=load, name="fallback-llm", daemon=True)
            self._thread.start()

    def decide(self, text: str, closest: Any, compiled: Any, satellite: Any, ranked: Any = None) -> Found:
        """``closest()`` is the closest command by meaning, ``ranked()`` every
        command by meaning (each costs an embedding, so only asked for when
        needed). Only commands chosen for a step are run by it."""
        found = Found()
        if self.embedding_accept and chosen(compiled, "meaning"):
            near = closest()
            command = compiled.commands.get(near["intent"]) if near else None
            accept, why = by_meaning(near, command)
            if accept and command is not None and "meaning" not in command.fallback:
                accept, why = False, {**why, "why": "not chosen for meaning"}
            found.trace["meaning"] = why
            if accept and command is not None and satellite.available(command) is not None:
                return Found(command, {}, "meaning", found.trace)
        llm = self.llm
        if llm is None or self.tools is None or not self.chosen or not text.strip():
            return found
        if not llm.ready or llm.signature != self.signature:
            found.trace["llm"] = {"skipped": self.status}
            return found
        subset = None
        trace: dict[str, Any] = {}
        scores: dict[str, float] | None = None

        def by_similarity() -> dict[str, float]:
            """Every command's similarity to the sentence, closest first (one
            embedding, when first asked for)."""
            nonlocal scores
            if scores is None:
                scores = dict(ranked()) if ranked is not None else {}
            return scores

        if self.closest_only and ranked is not None:
            allowed = set(self.chosen)
            order = [
                c for c in by_similarity()
                if c in allowed and c in compiled.commands and satellite.available(compiled.commands[c])
            ]
            if not order:
                found.trace["llm"] = {"skipped": "none of its commands is available here"}
                return found
            subset = with_way_out(self.tools.subset(order[: self.top_k]), llm.way_out)
            trace["candidates"] = order[: self.top_k]
        calls, raw, seconds = llm.call(text, subset)
        trace.update(raw=raw, seconds=round(seconds, 3), calls=[[n, a] for n, a in calls])
        found.trace["llm"] = trace
        for name, arguments in calls:
            if name == NOT_A_COMMAND and name not in compiled.commands:
                trace["said"] = "not a command"
                return found
            command = compiled.commands.get(name)
            if command is None or command.disabled or name not in self.chosen:
                trace.setdefault("problems", []).append(f"no command {name}")
                continue
            slots, problems = self.tools.slots(command, arguments, text)
            close = by_similarity().get(command.id)
            floor = LLM_BARE_MIN_SIMILARITY if slots == match_slots(command, {}) else LLM_MIN_SIMILARITY
            if close is not None and close < floor:
                # Not what it meant, so no hint from it either.
                trace["refused"] = {"command": command.id, "similarity": round(close, 4), "why": "not close in meaning"}
                return found
            trace["command"] = command.id
            if problems:
                trace["problems"] = problems
                return found
            if satellite.available(command) is None:
                trace["problems"] = [satellite.why_unavailable(command)]
                return found
            return Found(command, slots, "llm", found.trace)
        return found
