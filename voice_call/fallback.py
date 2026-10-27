"""When no command matched: the closest one by meaning, then GLiNER.

The chain, in order, each step only when the one before found nothing:

1. **CTC** (adapt-ctc): keywords and example sentences. Most commands end here.
2. **Meaning** (EmbeddingGemma, already loaded for hints) picks the command:
   the closest is taken only when it is very close (``EMBED_ACCEPT``) and
   well ahead of the runner-up (``EMBED_MARGIN``) -- another command, not
   another way of saying the same one (its combinations are one command).
   What was said in it -- names, numbers, values, free text -- is heard by
   the model (GLiNER: its slots' entities) and picks the combination; a
   command that hears nothing runs at once. Off unless ``embedding_accept``
   is set.
3. **The model** -- GLiNER -- picks the command when meaning did not: one
   label per command, among the closest few, or "not a command", and the
   entities its slots take. Its pick is checked like a match: the command
   must be available on the satellite, names map back to ids, values to list
   values, and what was heard must be one of its combinations, exactly.
4. **Fail**: the hint, as before.

Commands described alike -- the versions of a library command, on this
satellite, a device by name, an area or floor by name -- are one command to
both steps (``label_of``), and what is heard in the sentence picks the
version, as it picks a combination.

Both steps are opt-in, per command (``fallback: [meaning, llm]`` -- ``llm``,
the model step's id from when it was an LLM): a command nobody chose for a
step is never run by it.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .commands import Command, command_of, match_slots
from .languages.en.skills.numbers import FRACTIONS, SCALES, WORD_NUM, parse_num

_LOGGER = logging.getLogger(__name__)

EMBED_ACCEPT = 0.80
"""Cosine similarity at which the closest command by meaning is run."""
EMBED_MARGIN = 0.05
"""How far ahead of the runner-up (another command, or an everyday request
that is not a command) it must be."""


# ------------------------------------------------------------- the arguments


@dataclass
class Parameter:
    """How one argument the model hears becomes one slot."""

    slot: str
    kind: str
    """name | number | duration | list | text"""
    choices: dict[str, str] = field(default_factory=dict)
    """For names and lists: what may be said (lowercase) -> id or value."""
    many: bool = False
    dynamic: bool = False
    """A list the app keeps (running timers): any value is taken as said."""


class Arguments:
    """What every enabled command's slots may be heard as -- a name, a number,
    a duration, a list's value, free text -- and what is heard, back into its
    slots."""

    def __init__(self, compiled: Any, dynamic: dict[str, list[str]] | None = None) -> None:
        self.compiled = compiled
        data = compiled.data or {}
        self.names: dict[str, dict[str, Any]] = data.get("names") or {}
        self.lists: dict[str, Any] = data.get("lists") or {}
        self.dynamic = dynamic or {}
        self.parameters: dict[str, dict[str, Parameter]] = {}
        self.required: dict[str, list[str]] = {}
        for command in sorted(compiled.commands.values(), key=lambda c: c.id):
            if command.disabled:
                continue
            self.parameters[command.id], self.required[command.id] = self._parameters(command)

    # ---------------------------------------------------------------- out

    def _parameters(self, command: Command) -> tuple[dict[str, Parameter], list[str]]:
        """Every slot it hears, as a parameter; required when every
        combination has it."""
        intent = command.intent
        parameters: dict[str, Parameter] = {}
        always = set.intersection(*(set(c) for c in command.combinations)) if command.combinations else set()
        fixed = set(command.behavior.slots)

        for kind, spec in (intent.get("names") or {}).items():
            if kind in fixed:
                continue
            spec = spec if isinstance(spec, dict) else {"count": spec}
            choices = self._name_choices(kind, spec)
            if not choices:
                continue
            parameters[kind] = Parameter(kind, "name", choices, int(spec.get("count") or 1) > 1)

        if "number" in intent:
            slot = command.number_slot
            parameters[slot] = Parameter(slot, "duration" if command.duration else "number")

        for slot, spec in (intent.get("lists") or {}).items():
            listed = spec if isinstance(spec, str) else (spec or {}).get("list")
            parameters[slot] = Parameter(slot, "list", self._list_choices(str(listed)), dynamic=listed in self.dynamic)

        wildcard = intent.get("wildcard")
        if wildcard and wildcard.get("slot") not in parameters:
            slot = wildcard["slot"]
            parameters[slot] = Parameter(slot, "text")

        return parameters, [slot for slot in parameters if slot in always]

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

    def places_left(self, command: Command, slots: dict[str, Any], text: str) -> list[str]:
        """Areas and floors the sentence names (by name or alias, whole words,
        outside its free text) that are not in ``slots`` -- what was heard,
        plus the command's fixed target: "play music in the office" for a
        command that takes no room."""
        said = f" {_key(text)} "
        for name, parameter in self.parameters.get(command.id, {}).items():
            if parameter.kind == "text" and slots.get(name):
                said = said.replace(f" {_key(slots[name])} ", " ")
        # A device's own name may say a room: "the bedroom fan".
        devices = set(slots.get("entity") or command.behavior.slots.get("entity") or [])
        for phrase, item_id in sorted(self._name_choices("entity", {}).items(), key=lambda kv: -len(kv[0])):
            if item_id in devices:
                said = said.replace(f" {phrase} ", " ")
        taken = {
            (kind, str(item))
            for kind in ("area", "floor")
            for item in (slots.get(kind) or command.behavior.slots.get(kind) or [])
        }
        out = []
        for kind in ("area", "floor"):
            for phrase, item_id in self._name_choices(kind, {}).items():
                if f" {phrase} " in said and (kind, item_id) not in taken:
                    out.append(f"{kind} {self._label(kind, item_id)}")
        return list(dict.fromkeys(out))

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
        """What the model heard as the command's slots, and what was wrong
        with it (unknown names or values; slots that are no combination of
        it). With the sentence (``text``), names, values and free text it does
        not say are dropped."""
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
        if not problems:
            problems += combination_problems(command, set(slots))
        return slots, problems

    def takes(self, command: Command, name: str, value: Any, text: str | None) -> bool:
        """Whether this argument would be one of its slots: a name it has, a
        value of its list, a number or text the sentence says."""
        parameter = self.parameters.get(command.id, {}).get(name)
        if parameter is None or value in (None, "", []):
            return False
        heard: dict[str, Any] = {}
        problems: list[str] = []
        self._convert(parameter, name, value, text, heard, problems)
        return bool(heard) and not problems

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


def combination_problems(command: Command, heard: set[str]) -> list[str]:
    """Why these heard slots are no combination of the command: what the
    closest one that has them all is missing, or that they don't go
    together. Empty when they are one."""
    if command.combination_for(heard) is not None:
        return []
    fits = [c for c in command.combinations if set(c) >= heard]
    if fits:
        closest = min(fits, key=len)
        return [f"no {slot}" for slot in closest if slot not in heard]
    return [f"{' and '.join(sorted(heard))} don't go together"]


def _heard(value: Any, text: str, choices: dict[str, str] | None = None) -> bool:
    """Whether the sentence says this argument: a name, value or free text the
    model did not make up. With ``choices``, any way of saying the same id."""
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


# ------------------------------------------------------------------ GLiNER


@dataclass(frozen=True)
class GlinerInfo:
    name: str
    directory: str
    """Under models/: what scripts/export_gliner.py writes."""
    mb: int
    licence: str


GLINER_MODELS = {
    "gliner2.5-small-v1": GlinerInfo("GLiNER2.5 small, 8-bit", "gliner2.5-small-v1-onnx", 78, "Apache-2.0"),
}
DEFAULT_FALLBACK_MODEL = "gliner2.5-small-v1"
GLINER_THRESHOLD = 0.3
"""The least probability GLiNER's pick may have; it must also beat "not a
command". Small, 8-bit, on the fallback benchmark with eight commands chosen
and the closest eight in meaning to tell them from: 17 of 34 right and none
wrong at 0.3, 13 at 0.5, 18 at 0.2 (README "When nothing matches")."""
GLINER_ENTITY_MIN = 0.5
"""The least probability an argument GLiNER finds may have."""
NOT_A_COMMAND_LABEL = "something else: a question, small talk or thanks"

# What GLiNER looks for to fill a parameter: a label and a description, as on
# the fallback benchmark.
_ENTITY_NAMES = {
    "area": ("room", "a room or area of the home, like the kitchen or the bedroom"),
    "floor": ("floor", "a floor of the house, like upstairs or downstairs"),
    "entity": ("device", "a device, appliance or other thing in the home, like the coffee maker, the bedroom fan or the front door"),
}
_ENTITY_TEXT = {
    "query": ("music", "an artist, album, song, playlist or genre to play"),
    "item": ("item", "something to buy, or a task to do, as said"),
    "reminder": ("reminder", "what to be reminded of, like to call mom"),
    "time": ("clock time", "a time of day, like seven thirty or 6 am"),
    "timer": ("timer name", "the name of a timer, like pasta or eggs"),
}
_ENTITY_LISTS = {
    "asked": ("state", "a state asked about, like locked, unlocked, open or closed"),
    "color": ("color", "a color, like red or blue"),
    "device_class": ("cover type", "a kind of window or door cover, like blinds, curtains, shades or garage door"),
    "timer_name": ("timer name", "the name of a timer, like pasta or eggs"),
}
_ENTITY_NUMBERS = {
    "percentage": ("percentage", "a percentage, like thirty percent"),
    "temperature": ("temperature", "a temperature, like 72 degrees"),
}
_ARTICLE = re.compile(r"^(?:the|my|our|a|an)\s+", re.I)
_NUMBER_UNITS = {"percent", "per", "cent", "degrees", "degree"}


def entity_type(name: str, parameter: Parameter) -> tuple[str, str]:
    """What GLiNER looks for to fill a parameter: label, description."""
    spoken = name.replace("_", " ")
    if parameter.kind == "name":
        return _ENTITY_NAMES.get(parameter.slot, _ENTITY_NAMES["entity"])
    if parameter.kind == "duration":
        return "duration", "a length of time, like ten minutes or an hour and a half"
    if parameter.kind == "number":
        return _ENTITY_NUMBERS.get(name, (spoken, f"the {spoken}, a number"))
    if parameter.kind == "list":
        if name in _ENTITY_LISTS:
            return _ENTITY_LISTS[name]
        values = sorted(set(parameter.choices.values()))[:10]
        return spoken, f"a {spoken}" + (f", like {', '.join(values)}" if values else "")
    return _ENTITY_TEXT.get(name, (spoken, f"the {spoken}, as said"))


def spoken_number(text: str) -> Any:
    """A number GLiNER found, as a value: "thirty percent" -> 30."""
    words = [w for w in re.sub(r"[^a-z0-9. ]", " ", str(text).lower()).split() if w not in _NUMBER_UNITS]
    for end in range(len(words), 0, -1):
        value = parse_num(" ".join(words[:end]))
        if value is not None:
            return value
    return text


class GlinerPicker:
    """GLiNER2.5 picks a command (voice_call/gliner.py): one pass classifies
    the sentence among commands, by their descriptions, and finds the
    arguments they take. ``load`` and ``pick`` are slow-ish: call them from a
    thread."""

    def __init__(
        self, model_dir: Path | None, n_threads: int = 2, model: str = DEFAULT_FALLBACK_MODEL,
        threshold: float = GLINER_THRESHOLD,
    ) -> None:
        self.model_dir = model_dir
        self.n_threads = n_threads
        self.model = model
        self.threshold = threshold
        self.runtime: Any = None
        self.signature: str | None = None
        self.load_seconds: float | None = None
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        info = GLINER_MODELS.get(self.model)
        return info.name if info else self.model

    @staticmethod
    def find_model(model: str, root: Path) -> Path | None:
        """Its exported files under ``root``/models, if they are there."""
        path = root / "models" / GLINER_MODELS[model].directory
        return path if (path / "gliner2_onnx.json").is_file() else None

    def load(self, signature: str | None = None) -> None:
        from .gliner import GlinerOnnx  # pylint: disable=import-outside-toplevel

        with self._lock:
            started = time.perf_counter()
            if self.runtime is None:
                if self.model_dir is None:
                    raise RuntimeError(f"No {self.name} model")
                self.runtime = GlinerOnnx(self.model_dir, threads=self.n_threads)
                self.runtime.extract("warm up", {"item": "something"}, ("command", ["add", "remove"]))
            self.signature = signature
            self.load_seconds = time.perf_counter() - started
            _LOGGER.info("%s ready (%.1f s)", self.name, self.load_seconds)

    @property
    def ready(self) -> bool:
        return self.runtime is not None and self.signature is not None

    def pick(self, text: str, labels: list[str], entities: dict[str, str]) -> tuple[dict[str, Any], float]:
        """Every label's probability (multi-label) and the entities found."""
        with self._lock:
            started = time.perf_counter()
            out = self.runtime.extract(text, entities or None, ("command", labels))
            return out, time.perf_counter() - started

    def hear(self, text: str, entities: dict[str, str]) -> tuple[dict[str, Any], float]:
        """Only the entities: for a command already decided on."""
        with self._lock:
            started = time.perf_counter()
            out = self.runtime.extract(text, entities, None)
            return out, time.perf_counter() - started


# ---------------------------------------------------------------- the chain


@dataclass
class Found:
    """What the fallback decided on, and how."""

    command: Command | None = None
    slots: dict[str, Any] = field(default_factory=dict)
    by: str | None = None
    """meaning | gliner"""
    trace: dict[str, Any] = field(default_factory=dict)


def needs_heard_slots(command: Command) -> bool:
    """Whether some way of saying it needs something heard: a name, number,
    value or free text (a combination that is not empty)."""
    return command.takes_something


def label_of(command: Command) -> str:
    """What meaning and the model step know a command by: its description.
    Commands that describe themselves alike are one choice to them -- the
    versions of a library command (on this satellite, on a device by name, in
    an area or floor by name), a copy on a satellite -- and what is heard in
    the sentence picks among them, as it picks a combination."""
    return command.description or command.id


def kin(compiled: Any, command: Command) -> list[Command]:
    """``command`` and the enabled commands that describe themselves alike."""
    label = label_of(command)
    return [command, *(c for c in compiled.commands.values() if c.id != command.id and not c.disabled and label_of(c) == label)]


@dataclass
class Fit:
    """Which of a command's versions takes what the model heard."""

    command: Command | None = None
    slots: dict[str, Any] = field(default_factory=dict)
    arguments: dict[str, Any] = field(default_factory=dict)
    combination: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def by_meaning(closest: dict[str, Any] | None, command: Command | None) -> tuple[bool, dict[str, Any]]:
    """Whether meaning is sure of the command, and why (not). It picks the
    command only: what was said in it is heard by the model."""
    if closest is None or command is None:
        return False, {"why": "nothing close"}
    similarity = float(closest.get("similarity") or 0.0)
    margin = similarity - float(closest.get("next_similarity") or 0.0)
    trace = {"command": command.id, "similarity": round(similarity, 4), "margin": round(margin, 4)}
    if similarity < EMBED_ACCEPT:
        return False, {**trace, "why": "not close enough"}
    if margin < EMBED_MARGIN:
        return False, {**trace, "why": f"too close to {closest.get('next')}"}
    return True, trace


def chosen(compiled: Any, step: str) -> list[str]:
    """The commands chosen for a fallback step (``meaning`` or ``llm``), in
    id order."""
    return sorted(c.id for c in compiled.commands.values() if step in c.fallback and not c.disabled)


def needs_model(compiled: Any) -> list[str]:
    """The commands chosen for meaning that hear something: the model step's
    model hears it for them, even with none chosen for the model step."""
    return [c for c in chosen(compiled, "meaning") if compiled.commands[c].takes_something]


BY = "GLiNER"
"""What the model step is called in the trace."""


class Fallback:
    """Meaning, then GLiNER, for one engine. ``prepare`` after every build
    (new commands, new arguments); ``decide`` from the engine's thread."""

    def __init__(
        self, embedding_accept: bool = False, model: GlinerPicker | None = None, top_k: int | None = None
    ) -> None:
        self.embedding_accept = embedding_accept
        self.model = model
        """The model step's: GLiNER."""
        self.top_k = top_k
        """How many commands GLiNER chooses among: the closest in meaning."""
        self.arguments: Arguments | None = None
        self.chosen: list[str] = []
        """The commands chosen for the model step."""
        self.hearing: list[str] = []
        """The commands chosen for meaning that hear something: the model
        hears it for them."""
        self.signature: str | None = None
        """What the model must have been loaded for."""
        self.status = "off" if model is None else "waiting for the commands"
        """The model step's state; without a model, why (the app says)."""
        self._thread: threading.Thread | None = None

    def prepare(self, engine: Any, wait: bool = False) -> None:
        """New commands: new arguments, and the model loaded for them, if it
        isn't (in the background unless ``wait``)."""
        compiled = engine.compiled
        if compiled is None:
            return
        arguments = Arguments(compiled, engine.dynamic)
        self.arguments = arguments
        self.chosen = chosen(compiled, "llm")
        self.hearing = needs_model(compiled)
        model = self.model
        self.signature = model.model if model is not None else None
        if model is None:
            return
        if not [c for c in self.chosen or self.hearing if c in arguments.parameters]:
            self.status = "no commands chosen for it"
            return
        if model.ready and model.signature == self.signature:
            self.status = "ready"
            return

        def load() -> None:
            try:
                model.load(self.signature)
                self.status = "ready"
            except Exception as err:  # noqa: BLE001 - the chain works without it
                self.status = f"not available: {err}"
                _LOGGER.exception("%s could not be loaded", model.name)

        self.status = "loading"
        if wait:
            load()
        else:
            self._thread = threading.Thread(target=load, name="fallback-model", daemon=True)
            self._thread.start()

    def decide(self, text: str, closest: Any, compiled: Any, satellite: Any, ranked: Any = None) -> Found:
        """``closest()`` is the closest command by meaning, ``ranked()`` every
        command by meaning (each costs an embedding, so only asked for when
        needed). Only commands chosen for a step are run by it."""
        found = Found()
        scores: dict[str, float] | None = None

        def by_similarity() -> dict[str, float]:
            """Every command's similarity to the sentence, closest first (one
            embedding, when first asked for)."""
            nonlocal scores
            if scores is None:
                scores = {command_of(i): s for i, s in reversed(ranked())} if ranked is not None else {}
                scores = dict(sorted(scores.items(), key=lambda item: -item[1]))
            return scores

        nearest: list[Any] = []

        def closest_once() -> Any:
            """The closest command by meaning (one embedding, when first asked for)."""
            if not nearest:
                nearest.append(closest() if closest is not None else None)
            return nearest[0]

        if chosen(compiled, "meaning") and not self.embedding_accept:
            found.trace["meaning"] = {"skipped": "turned off on the Settings page"}
        elif self.embedding_accept and chosen(compiled, "meaning"):
            near = closest_once()
            command = compiled.commands.get(command_of(near["intent"])) if near else None
            accept, why = by_meaning(near, command)
            mine = [c for c in kin(compiled, command) if "meaning" in c.fallback] if command is not None else []
            if accept and not mine:
                accept, why = False, {**why, "why": "not chosen for meaning"}
            found.trace["meaning"] = why
            here = satellite.first(mine)
            if accept and here:
                if not any(c.takes_something for c in here):
                    for candidate in here:
                        if self._places_fit(candidate, {}, text, why):
                            return Found(candidate, {}, "meaning", found.trace)
                    return found
                # Meaning picked the command; what was said in it, the model
                # hears, and that picks the version and the combination.
                return self._fill(found, why, here, text, satellite)
        if self.model is None or self.arguments is None or not self.chosen or not text.strip():
            if self.model is None and self.chosen:
                found.trace["llm"] = {"by": BY, "skipped": self.status}
            return found
        if not self._model_ready():
            found.trace["llm"] = {"by": BY, "skipped": self.status}
            return found
        trace: dict[str, Any] = {"by": BY}
        found.trace["llm"] = trace
        return self._pick_with_gliner(found, trace, text, compiled, satellite, by_similarity, closest_once)

    def _fit(self, members: list[Command], arguments_of: Any, text: str) -> Fit:
        """Which of a command's versions takes what the model heard
        (``arguments_of(version)``): its slots are one of its combinations,
        and the sentence names no room or floor it leaves out. Of those, the
        one that takes the most of it -- "play jazz on the Mark 2" is the
        version that takes a device, not the one that plays it here. None
        fitting: what was wrong with each."""
        assert self.arguments is not None
        best: Fit | None = None
        tried: list[Fit] = []
        for command in members:
            arguments = arguments_of(command)
            slots, problems = self.arguments.slots(command, arguments, text)
            if not problems:
                problems = [f"heard {place}, which it doesn't take" for place in self.arguments.places_left(command, slots, text)]
            combination = list(command.combination_for(set(slots) - set(command.behavior.slots)) or [])
            fit = Fit(None if problems else command, slots, arguments, combination, problems)
            tried.append(fit)
            if fit.command is not None and (best is None or len(combination) > len(best.combination)):
                best = fit
        if best is not None:
            return best
        if len(tried) == 1:
            return tried[0]
        return Fit(
            slots=tried[0].slots if tried else {},
            arguments=tried[0].arguments if tried else {},
            problems=[f"{command.id}: {problem}" for command, fit in zip(members, tried) for problem in fit.problems],
        )

    def _places_fit(self, command: Command, slots: dict[str, Any], text: str, trace: dict[str, Any]) -> bool:
        """Whether the sentence names no room or floor the command left out;
        else why not, in ``trace``. "Play music in the office" is not resuming
        the music here."""
        left = self.arguments.places_left(command, slots, text) if self.arguments is not None else []
        if left:
            trace["problems"] = [f"heard {place}, which it doesn't take" for place in left]
        return not left

    def _model_ready(self) -> bool:
        model = self.model
        return model is not None and self.arguments is not None and model.ready and model.signature == self.signature

    def _fill(self, found: Found, trace: dict[str, Any], members: list[Command], text: str, satellite: Any) -> Found:
        """Meaning picked a command that hears something: GLiNER hears what
        was said in it (its versions' entity types), and that picks the
        version and its combination -- nothing said is one too, when it has
        it."""
        model = self.model
        if not self._model_ready() or not text.strip():
            trace["why"] = "it hears names, numbers or text, and there is no model to hear them"
            return found
        assert model is not None
        types = self._entity_types([c.id for c in members])
        out, seconds = model.hear(text, types) if types else ({"entities": {}}, 0.0)
        entities = out.get("entities") or {}

        def arguments_of(command: Command) -> dict[str, Any]:
            return self._gliner_arguments(command, entities, text)

        trace.update(
            heard_by=BY,
            entities={k: [[e[0], round(e[1], 3)] for e in v] for k, v in entities.items() if v},
            seconds=round(seconds, 3),
        )
        fit = self._fit(members, arguments_of, text)
        trace["arguments"] = fit.arguments
        if fit.command is None:
            trace["problems"] = fit.problems
            return found
        trace["command"] = fit.command.id
        trace["combination"] = fit.combination
        return Found(fit.command, fit.slots, "meaning", found.trace)

    def _entity_types(self, command_ids: list[str]) -> dict[str, str]:
        """What GLiNER looks for, for these commands' slots: label -> description."""
        assert self.arguments is not None
        types: dict[str, str] = {}
        for command_id in command_ids:
            for name, parameter in self.arguments.parameters.get(command_id, {}).items():
                label, description = entity_type(name, parameter)
                types.setdefault(label, description)
        return types

    def _pick_with_gliner(
        self,
        found: Found,
        trace: dict[str, Any],
        text: str,
        compiled: Any,
        satellite: Any,
        by_similarity: Any,
        closest: Any = None,
    ) -> Found:
        """GLiNER picks among the commands -- one label each, its description,
        which a command's versions share -- or "not a command", and what it
        hears in the sentence picks the version and the combination. It is
        given the closest ``top_k`` in meaning, chosen or not -- with no others
        to tell it from, it would take anything for the one there is ("never
        mind" played as music) -- and its pick runs only when it was chosen,
        sure enough (``threshold``), ahead of "not a command", available here,
        and what it heard is a combination of it. A pick that can take none
        of what it heard gives way to the surest one that can ("I need cookies
        to be on the shopping list": what's on it, 0.44, which takes no item,
        and adding to it, 0.39, which does). Hearing nothing in it, it
        runs only when meaning puts the same command first, ahead of the
        everyday requests that are no command too: there is nothing else to
        check it by ("thank you" was "say good night", "what's twelve times
        eight" turned on repeat)."""
        model, arguments = self.model, self.arguments
        assert model is not None and arguments is not None
        groups: dict[str, list[Command]] = {}
        for command in compiled.commands.values():
            if not command.disabled and command.id in arguments.parameters and label_of(command) != NOT_A_COMMAND_LABEL:
                groups.setdefault(label_of(command), []).append(command)
        # Each label shown by its closest command, closest first.
        similarity = by_similarity()
        shown: dict[str, str] = {}
        for command_id in list(similarity) or sorted(compiled.commands):
            command = compiled.commands.get(command_id)
            if command is not None and label_of(command) in groups:
                shown.setdefault(label_of(command), command_id)
        order = list(shown)
        mine = {label for label, members in groups.items() if any(c.id in self.chosen for c in members)}
        if not similarity:
            # Without an embedding model nothing is closest: its own commands,
            # then the others to tell them from, rather than the first by id
            # (a shopping list was never among them).
            order = [label for label in order if label in mine] + [label for label in order if label not in mine]
        candidates = order[: self.top_k] if self.top_k else order
        if not mine & set(candidates):
            trace["skipped"] = "none of its commands is close in meaning"
            return found
        types = self._entity_types([c.id for label in candidates for c in groups[label]])
        out, seconds = model.pick(text, [*candidates, NOT_A_COMMAND_LABEL], types)
        probabilities = dict(out["classification"])
        none = probabilities.pop(NOT_A_COMMAND_LABEL)
        scores = {label: probabilities[label] for label in candidates}
        entities = out.get("entities") or {}
        best = max(candidates, key=lambda label: scores[label])
        heard = {kind for kind, found in entities.items() if any(e[1] >= GLINER_ENTITY_MIN for e in found)}

        def takes(label: str) -> bool:
            """Whether a label's commands take something of what was heard."""
            return any(
                entity_type(name, parameter)[0] in heard
                for command in groups[label]
                for name, parameter in arguments.parameters[command.id].items()
            )

        took = best
        if heard and not takes(best):
            best = max((label for label in candidates if takes(label)), key=lambda label: scores[label], default=best)
        trace.update(
            candidates=[shown[label] for label in candidates],
            scores={shown[label]: round(p, 4) for label, p in scores.items()},
            not_a_command=round(none, 4),
            entities={k: [[e[0], round(e[1], 3)] for e in v] for k, v in entities.items() if v},
            seconds=round(seconds, 3),
        )
        trace["raw"] = f"{shown[took]} {scores[took]:.2f}, not a command {none:.2f}" + "".join(
            f"; {k}: " + ", ".join(f"\u201c{e[0]}\u201d {e[1]:.2f}" for e in v) for k, v in entities.items() if v
        )
        if best != took:
            trace["instead_of"] = {"command": shown[took], "why": "it takes none of what was heard"}
        if none >= scores[best]:
            trace["said"] = "not a command"
            return found
        if scores[best] < model.threshold:
            trace["said"] = f"not sure enough ({scores[best]:.2f}, needs {model.threshold:.2f})"
            return found
        members = [c for c in groups[best] if c.id in self.chosen]
        if not members:
            trace["refused"] = {"command": shown[best], "why": "not chosen for it"}
            return found
        available = satellite.first(members)
        if not available:
            trace["command"] = members[0].id
            trace["problems"] = [satellite.why_unavailable(members[0])]
            return found
        fit = self._fit(available, lambda c: self._gliner_arguments(c, entities, text), text)
        trace["arguments"] = fit.arguments
        trace["command"] = (fit.command or available[0]).id
        if fit.command is None:
            trace["problems"] = fit.problems
            return found
        trace["combination"] = fit.combination
        if not fit.combination:
            near = closest() if closest is not None else None
            first = compiled.commands.get(command_of(near["intent"])) if near else None
            if first is None or label_of(first) != best:
                trace["refused"] = {"command": fit.command.id, "why": "not what meaning puts first, and nothing was heard in it"}
                return found
            if near.get("next_similarity") is not None and near["next_similarity"] > near["similarity"]:
                trace["refused"] = {
                    "command": fit.command.id,
                    "why": f"closer in meaning to {near['next']}, which is no command, and nothing was heard in it",
                }
                return found
        return Found(fit.command, fit.slots, "gliner", found.trace)

    def _gliner_arguments(self, command: Command, entities: dict[str, list], text: str) -> dict[str, Any]:
        """A command's arguments from what GLiNER found: for each slot the
        surest it can take (a name it has, a value of its list, a number
        the sentence says), or all of them for many names; numbers as values,
        names without "the". The same words found as two slots ("living room"
        as a room and as a floor) are the surer one."""
        arguments = self.arguments
        assert arguments is not None
        picked: list[tuple[float, int, int, str, Any]] = []
        for name, parameter in arguments.parameters[command.id].items():
            label, _ = entity_type(name, parameter)
            found = sorted((e for e in entities.get(label) or [] if e[1] >= GLINER_ENTITY_MIN), key=lambda e: -e[1])
            for entity in found:
                said, confidence, start, end = entity[0], entity[1], entity[2], entity[3]
                value = _ARTICLE.sub("", said) if parameter.kind == "name" else said
                if parameter.kind == "number":
                    value = spoken_number(value)
                if not arguments.takes(command, name, value, text):
                    continue
                picked.append((confidence, start, end, name, value))
                if not parameter.many:
                    break
        kept: list[tuple[float, int, int, str, Any]] = []
        for pick in sorted(picked, key=lambda p: -p[0]):
            if any(pick[1] < other[2] and other[1] < pick[2] for other in kept if other[3] != pick[3]):
                continue
            kept.append(pick)
        out: dict[str, Any] = {}
        for _confidence, _start, _end, name, value in sorted(kept, key=lambda p: p[1]):
            if arguments.parameters[command.id][name].many:
                out.setdefault(name, []).append(value)
            else:
                out[name] = value
        return out
