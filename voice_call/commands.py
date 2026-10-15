"""Voice commands: what is listened for, where, and what happens.

A command is one adapt-ctc intent plus what to do when it matches::

    commands:
      ecobee_on:
        description: turn on the thermostat      # a verb phrase, for hints
        scope: {areas: [living_room]}            # or `all` (the default)
        slots: {entity: [climate.ecobee]}        # fixed slots: the target
        examples: [turn on the EcoBee, turn on the Echo Bee]
        require: [[turn on, turn ... on], [ecobee, echo bee]]
        script: script.heat_up                   # or scene:, action:, or none
        response: "OK, {{ slots.entity[0] }} is on"
        overrides:
          - scope: {satellites: [aabbccddeeff]}
            action: {action: climate.turn_on, target: {entity_id: climate.office}}

``title`` and ``icon`` (a Material Design Icon, ``mdi:lightbulb``) are only
for the UI.

Listening is adapt-ctc's own vocabulary -- ``examples``, ``require``,
``optional``, ``exclude``, ``names``, ``number``, ``lists`` and ``wildcard``
-- with two additions: ``number`` may be a preset (``percentage``,
``temperature``, ``duration``, ``count``) and may name its slot.

``slots`` are values every match carries as if they had been heard: the
entity, area or floor a command was made for, so the phrase can be anything
while the target stays fixed.

Exactly what happens is one of ``script`` (a script entity, run with the
variables as its data), ``scene`` (activated), ``action`` (one Home Assistant
action or a list of them, as in a script), ``timer`` (an operation on the
satellite's own timers: start, cancel, cancel_all, pause, resume, add, subtract,
status; see voice_call/timers.py), or ``music`` (playing or controlling Music
Assistant's players: play, pause, next, volume_up ...; see voice_call/music.py),
plus an optional ``response`` template that is spoken. A command may also only
respond.

``response`` may instead say something different for each way it can turn
out (its outcome): ``done``, ``failed`` (Home Assistant refused), what a
timer operation found (``no_timers``, ``not_found``, ``ambiguous``,
``already``, ``missing_duration``), what a music operation found
(``no_player``, ``nothing_playing``, ``not_found`` ...), or whatever
``outcome`` a script returns. A plain template is the ``done`` one; an outcome without its own
says the built-in sentence::

    response:
      done: Paused.
      no_timers: There's nothing to pause.

``scope`` says where a command is available: everywhere, in some areas, or on
some satellites. ``overrides`` change what happens (and the fixed slots) in a
narrower scope, or switch the command off there (``disabled: true``): an
override for a satellite wins over one for its area, which wins over the
command itself.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any

from adapt_ctc.config import ConfigError

COMMAND_ID = re.compile(r"^[a-z0-9_]+$")
ICON = re.compile(r"^mdi:[a-z0-9-]+$")
NAME_SLOTS = ("entity", "area", "floor")
DURATION_UNITS = ("second", "minute", "hour")
UNIT_WORDS = ["seconds", "second", "minutes", "minute", "hours", "hour"]

LISTEN_KEYS = (
    "examples",
    "require",
    "optional",
    "exclude",
    "names",
    "number",
    "lists",
    "wildcard",
)
DO_KEYS = ("script", "scene", "action", "timer", "music")
OUTCOME_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
# Every command is done or failed; a timer command also has its operation's
# outcomes (timers.OUTCOMES), and a script or action whatever it returns.
BASE_OUTCOMES = ("done", "failed")
COMMAND_KEYS = {
    "description",
    "scope",
    "slots",
    "response",
    "overrides",
    "disabled",
    "title",
    "icon",
    *LISTEN_KEYS,
    *DO_KEYS,
}
OVERRIDE_KEYS = {"scope", "slots", "response", "disabled", *DO_KEYS}

# Shared settings for every home: the core intents' filler words and name
# cues, and examples that win when said word for word. The embedding names the
# closest command when nothing matched; it never accepts one.
BASE_CONFIG: dict[str, Any] = {
    "language": "en",
    "filler": ["the", "a", "an", "please", "my", "can you", "could you", "would you", "it", "of"],
    "name_cues": ["the", "my", "named", "called"],
    "numbers": {"detect": {"min": 0, "max": 200}},
    "settings": {"examples_first": True, "embedding_accept": False},
}

# Value lists the app keeps current itself; commands refer to them by name
# (``lists: {timer_name: {list: running_timers, optional: true}}``). A placeholder
# stands in for validation; the engine puts the real values in (and drops a slot
# whose list is empty) every time it builds.
DYNAMIC_LISTS = {
    "running_timers": "the names of the timers running on any satellite",
}
DYNAMIC_PLACEHOLDER = {"placeholder": ["placeholder"]}

# The number specs core.yaml tuned for these, keyed by preset. A preset may be
# refined with its own keys: {preset: percentage, min: 1}.
NUMBER_PRESETS: dict[str, dict[str, Any]] = {
    "percentage": {
        "min": 0,
        "max": 100,
        "prior": [
            {"multiple_of": 10, "cost": 0},
            {"multiple_of": 5, "cost": 0.5},
            {"cost": 1.5},
        ],
    },
    "temperature": {
        "min": 10,
        "max": 90,
        "step": 0.5,
        "prior": [{"multiple_of": 1, "cost": 0}, {"cost": 1.0}],
    },
    "duration": {
        "min": 0.5,
        "max": 100,
        "step": 0.5,
        "units": list(DURATION_UNITS),
        "prior": [
            {"when": ["seconds", "second"], "multiple_of": 5, "cost": 0},
            {"when": ["seconds", "second"], "cost": 2.5},
            {"multiple_of": 1, "cost": 0},
            {"cost": 1.0},
        ],
    },
    "count": {"min": 1, "max": 100},
}


@dataclass(frozen=True)
class Scope:
    areas: tuple[str, ...] = ()
    satellites: tuple[str, ...] = ()

    @property
    def everywhere(self) -> bool:
        return not self.areas and not self.satellites

    def specificity(self, satellite: str | None, area: str | None) -> int | None:
        """How closely this scope fits a satellite: 2 by name, 1 by its area,
        0 everywhere; None when it does not include it."""
        if satellite is not None and satellite in self.satellites:
            return 2
        if area is not None and area in self.areas:
            return 1
        if self.everywhere:
            return 0
        return None

    def to_data(self) -> Any:
        if self.everywhere:
            return "all"
        out: dict[str, list[str]] = {}
        if self.areas:
            out["areas"] = list(self.areas)
        if self.satellites:
            out["satellites"] = list(self.satellites)
        return out


@dataclass
class Behavior:
    """What happens when a command matches."""

    slots: dict[str, Any] = field(default_factory=dict)
    script: str | None = None
    scene: str | None = None
    action: list[dict] | None = None
    timer: str | None = None
    """A timer operation, done by the app on the satellite's timer list."""

    music: str | None = None
    """A music operation, done by the app on a Music Assistant player."""

    response: str | dict[str, str] | None = None
    """A template, or one for each outcome (see ``response_for``)."""

    disabled: bool = False

    def response_for(self, outcome: str) -> str | None:
        """The template for this outcome; a plain response is for ``done``."""
        if isinstance(self.response, dict):
            return self.response.get(outcome) or None
        return self.response if outcome == "done" else None

    def outcomes(self) -> tuple[str, ...] | None:
        """The outcomes a response may be given for; None when any may be
        (a script or an action can return its own)."""
        if self.timer:
            from .timers import OUTCOMES  # pylint: disable=import-outside-toplevel

            return (*OUTCOMES[self.timer], "failed")
        if self.music:
            from .music import OUTCOMES as MUSIC_OUTCOMES  # pylint: disable=import-outside-toplevel

            return (*MUSIC_OUTCOMES[self.music], "failed")
        if self.script or self.action:
            return None
        return BASE_OUTCOMES

    @property
    def does(self) -> str:
        if self.script:
            return "script"
        if self.scene:
            return "scene"
        if self.action:
            return "action"
        if self.timer:
            return "timer"
        if self.music:
            return "music"
        return "respond"

    def sequence(self) -> list[dict]:
        """The Home Assistant action sequence, empty for a response only.

        A script gets the variables as its data, so its fields (or plain
        variables) see ``slots``, ``satellite``, ``speaker`` and ``target``;
        what it returns with ``stop``/``response_variable`` comes back as the
        response's ``result``."""
        if self.script:
            data = {key: "{{ %s }}" % key for key in ("slots", "satellite", "speaker", "target", "command")}
            return [
                {"action": self.script, "data": data, "response_variable": "result"},
                # A script that returns nothing still stops with a mapping.
                {"variables": {"result": "{{ result if result is mapping else {} }}"}},
                {"stop": "done", "response_variable": "result"},
            ]
        if self.scene:
            return [{"action": "scene.turn_on", "target": {"entity_id": self.scene}}]
        if self.timer or self.music:
            return []  # the app does these itself (voice_call/timers.py, music.py)
        return copy.deepcopy(self.action or [])


@dataclass
class Command:
    id: str
    description: str
    scope: Scope
    behavior: Behavior
    overrides: list[tuple[Scope, dict[str, Any]]]
    intent: dict[str, Any]
    """The adapt-ctc intent."""

    number_slot: str = "number"
    duration: bool = False
    disabled: bool = False
    title: str = ""

    def resolve(self, satellite: str | None, area: str | None) -> tuple[Behavior, str] | None:
        """What happens on this satellite, and which scope decided it; None
        where the command is not available."""
        best: tuple[int, int, Behavior, str] | None = None
        base = self.scope.specificity(satellite, area)
        if base is not None:
            best = (base, -len(self.overrides) - 1, self.behavior, "command")
        for index, (scope, changes) in enumerate(self.overrides):
            fit = scope.specificity(satellite, area)
            if fit is None:
                continue
            # Narrower wins; between equals an override wins over the command,
            # and the first listed over the rest.
            if best is None or (fit, -index) > (best[0], best[1]):
                best = (fit, -index, _apply(self.behavior, changes), f"override {index + 1}")
        if best is None or best[2].disabled:
            return None
        return best[2], best[3]


def _apply(base: Behavior, changes: dict[str, Any]) -> Behavior:
    behavior = copy.deepcopy(base)
    if "slots" in changes:
        behavior.slots = changes["slots"]
    if any(key in changes for key in DO_KEYS):
        behavior.script = changes.get("script")
        behavior.scene = changes.get("scene")
        behavior.action = changes.get("action")
        behavior.timer = changes.get("timer")
        behavior.music = changes.get("music")
    if "response" in changes:
        behavior.response = changes["response"]
    behavior.disabled = bool(changes.get("disabled", False))
    return behavior


# ------------------------------------------------------------------ parsing


def parse_commands(data: dict[str, Any]) -> dict[str, Command]:
    """Commands from a store's ``commands`` mapping. Raises ConfigError."""
    commands = {}
    for command_id, spec in (data or {}).items():
        command_id = str(command_id)
        commands[command_id] = parse_command(command_id, spec)
    return commands


def parse_command(command_id: str, spec: Any) -> Command:
    where = f"commands.{command_id}"
    if not COMMAND_ID.match(command_id):
        raise ConfigError(f"{where}: ids are lowercase letters, digits and underscores")
    spec = _mapping(spec, where)
    unknown = set(spec) - COMMAND_KEYS
    if unknown:
        raise ConfigError(f"{where}: unknown key(s) {sorted(unknown)}")
    if spec.get("icon") is not None and not ICON.match(str(spec["icon"])):
        raise ConfigError(f"{where}.icon: a Material Design Icon, like mdi:lightbulb")
    description = str(spec.get("description") or "").strip()
    if not description:
        raise ConfigError(f"{where}.description: say what the command does ('turn on the lights')")

    intent: dict[str, Any] = {"description": description}
    for key in LISTEN_KEYS:
        if spec.get(key) not in (None, [], {}):
            intent[key] = copy.deepcopy(spec[key])
    if not (set(intent) - {"description"}):
        raise ConfigError(f"{where}: needs examples or keywords to listen for")

    number_slot, duration = "number", False
    if "number" in intent:
        intent["number"], number_slot, duration = _number(intent["number"], f"{where}.number")
        if duration:
            _ensure_unit_keywords(intent)
    behavior = _behavior(spec, where)
    if behavior.does == "respond" and not behavior.response_for("done"):
        raise ConfigError(f"{where}: needs a script, scene, action or response")
    _check_outcomes(behavior, f"{where}.response")

    overrides = []
    for index, override in enumerate(spec.get("overrides") or []):
        at = f"{where}.overrides[{index}]"
        override = _mapping(override, at)
        unknown = set(override) - OVERRIDE_KEYS
        if unknown:
            raise ConfigError(f"{at}: unknown key(s) {sorted(unknown)}")
        scope = _scope(override.get("scope"), f"{at}.scope")
        if scope.everywhere:
            raise ConfigError(f"{at}.scope: an override needs areas or satellites")
        changes = {k: v for k, v in override.items() if k != "scope"}
        _behavior(changes, at)  # validate
        if sum(1 for key in DO_KEYS if changes.get(key)) > 1:
            raise ConfigError(f"{at}: give one of {', '.join(DO_KEYS)}")
        if "slots" in changes:
            changes["slots"] = _fixed_slots(changes["slots"], f"{at}.slots")
        if "action" in changes:
            changes["action"] = _actions(changes["action"], f"{at}.action")
        if "response" in changes:
            changes["response"] = _response(changes["response"], f"{at}.response")
        _check_outcomes(_apply(behavior, changes), f"{at}.response")
        overrides.append((scope, changes))

    return Command(
        command_id,
        description,
        _scope(spec.get("scope"), f"{where}.scope"),
        behavior,
        overrides,
        intent,
        number_slot,
        duration,
        bool(spec.get("disabled", False)),
        str(spec.get("title") or ""),
    )


def _behavior(spec: dict[str, Any], where: str) -> Behavior:
    given = [key for key in DO_KEYS if spec.get(key)]
    if len(given) > 1:
        raise ConfigError(f"{where}: give one of {', '.join(DO_KEYS)}, not {', '.join(given)}")
    script = spec.get("script")
    if script is not None:
        script = str(script)
        if not script.startswith("script."):
            raise ConfigError(f"{where}.script: expected a script entity id (script.…)")
    scene = spec.get("scene")
    if scene is not None:
        scene = str(scene)
        if not scene.startswith("scene."):
            raise ConfigError(f"{where}.scene: expected a scene entity id (scene.…)")
    timer = spec.get("timer")
    if timer is not None:
        from .timers import OPERATIONS  # pylint: disable=import-outside-toplevel

        timer = str(timer)
        if timer not in OPERATIONS:
            raise ConfigError(f"{where}.timer: one of {', '.join(OPERATIONS)}")
    music = spec.get("music")
    if music is not None:
        from .music import OPERATIONS as MUSIC_OPERATIONS  # pylint: disable=import-outside-toplevel

        music = str(music)
        if music not in MUSIC_OPERATIONS:
            raise ConfigError(f"{where}.music: one of {', '.join(MUSIC_OPERATIONS)}")
    response = _response(spec.get("response"), f"{where}.response")
    return Behavior(
        slots=_fixed_slots(spec.get("slots"), f"{where}.slots"),
        script=script,
        scene=scene,
        action=_actions(spec["action"], f"{where}.action") if spec.get("action") else None,
        timer=timer,
        music=music,
        response=response or None,
        disabled=bool(spec.get("disabled", False)),
    )


def _response(value: Any, where: str) -> str | dict[str, str] | None:
    """A template, or templates by outcome."""
    if value in (None, "", {}):
        return None
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        raise ConfigError(f"{where}: expected a template, or templates by outcome (done: …, failed: …)")
    out = {}
    for outcome, template in value.items():
        outcome = str(outcome)
        if not OUTCOME_NAME.match(outcome):
            raise ConfigError(f"{where}.{outcome}: outcomes are lowercase letters, digits and underscores")
        if template in (None, ""):
            continue
        if not isinstance(template, str):
            raise ConfigError(f"{where}.{outcome}: expected a template string")
        out[outcome] = template
    return out or None


def _check_outcomes(behavior: Behavior, where: str) -> None:
    """Responses only for outcomes this command can have."""
    allowed = behavior.outcomes()
    if allowed is None or not isinstance(behavior.response, dict):
        return
    unknown = [o for o in behavior.response if o not in allowed]
    if unknown:
        if behavior.timer or behavior.music:
            kind = f"{behavior.does}: {behavior.timer or behavior.music}"
        else:
            kind = {"respond": "only a response"}.get(behavior.does, f"a {behavior.does}")
        raise ConfigError(
            f"{where}: {', '.join(unknown)} can't happen with {kind}; use {', '.join(allowed)}"
        )


def _actions(value: Any, where: str) -> list[dict]:
    actions = value if isinstance(value, list) else [value]
    for index, action in enumerate(actions):
        if not isinstance(action, dict):
            raise ConfigError(f"{where}[{index}]: expected a Home Assistant action mapping")
    return copy.deepcopy(actions)


def _fixed_slots(value: Any, where: str) -> dict[str, Any]:
    slots = dict(_mapping(value or {}, where))
    for name in NAME_SLOTS:
        if name in slots:
            ids = slots[name] if isinstance(slots[name], list) else [slots[name]]
            slots[name] = [str(i) for i in ids if i]
    return slots


def _scope(value: Any, where: str) -> Scope:
    if value in (None, "all", "everywhere"):
        return Scope()
    value = _mapping(value, where)
    unknown = set(value) - {"areas", "satellites"}
    if unknown:
        raise ConfigError(f"{where}: unknown key(s) {sorted(unknown)} (use areas, satellites)")

    def ids(key: str) -> tuple[str, ...]:
        items = value.get(key) or []
        if isinstance(items, str):
            items = [items]
        return tuple(str(i) for i in items)

    return Scope(ids("areas"), ids("satellites"))


def _number(value: Any, where: str) -> tuple[dict[str, Any], str, bool]:
    """An adapt-ctc number spec from a preset or a spec, with its slot name."""
    if isinstance(value, str):
        value = {"preset": value}
    value = dict(_mapping(value, where))
    preset = value.pop("preset", None)
    slot = str(value.pop("slot", "") or "")
    if preset is not None:
        if preset not in NUMBER_PRESETS:
            raise ConfigError(f"{where}.preset: one of {sorted(NUMBER_PRESETS)}")
        value = {**copy.deepcopy(NUMBER_PRESETS[preset]), **value}
    duration = bool(value.get("units"))
    if not slot:
        slot = "duration" if duration else (preset or "number")
    if slot in NAME_SLOTS or slot in ("media", "hours", "minutes", "seconds"):
        raise ConfigError(f"{where}.slot: {slot!r} is taken")
    return value, slot, duration


def _ensure_unit_keywords(intent: dict[str, Any]) -> None:
    """A duration's units must be keywords of the intent: required, unless
    the command already lists some of them."""
    groups = [g if isinstance(g, list) else [g] for g in intent.get("require", [])]
    groups += [g if isinstance(g, list) else [g] for g in intent.get("optional", [])]
    present = {phrase for group in groups for phrase in group}
    units = set(intent["number"].get("units") or [])
    whens = {w for rule in intent["number"].get("prior") or [] for w in rule.get("when") or []}
    if not (present & set(UNIT_WORDS)):
        intent.setdefault("require", []).append(list(UNIT_WORDS))
        return
    missing = sorted((units | whens) - present)
    if missing:
        intent.setdefault("optional", []).append(missing)


def _mapping(value: Any, where: str) -> dict:
    if not isinstance(value, dict):
        raise ConfigError(f"{where}: expected a mapping")
    return value


# ---------------------------------------------------------------- compiling


def adapt_config(store: dict[str, Any], commands: dict[str, Command]) -> dict[str, Any] | None:
    """The adapt-ctc config for every enabled command; None without any."""
    intents = {c.id: c.intent for c in commands.values() if not c.disabled}
    if not intents:
        return None
    config = copy.deepcopy(BASE_CONFIG)
    config["settings"].update(store.get("settings") or {})
    reserved = sorted(set(store.get("lists") or {}) & set(DYNAMIC_LISTS))
    if reserved:
        raise ConfigError(f"lists: {reserved} are kept by the app; use another name")
    config["lists"] = {
        **copy.deepcopy(store.get("lists") or {}),
        **{name: copy.deepcopy(DYNAMIC_PLACEHOLDER) for name in DYNAMIC_LISTS},
    }
    names = store.get("names") or {}
    if names:
        config["names"] = {k: v for k, v in copy.deepcopy(names).items() if v}
    config["intents"] = copy.deepcopy(intents)
    return config


def match_slots(command: Command, recognized: dict[str, Any]) -> dict[str, Any]:
    """Slots as Home Assistant sees them: names as id lists, the number under
    its slot name, a duration as one value."""
    slots = dict(recognized)
    if command.duration:
        parts = {unit: slots.pop(unit) for unit in ("hours", "minutes", "seconds") if unit in slots}
        if parts:
            total = (
                parts.get("hours", 0) * 3600 + parts.get("minutes", 0) * 60 + parts.get("seconds", 0)
            )
            total = round(total, 3)
            hours, rest = divmod(int(total), 3600)
            minutes, seconds = divmod(rest, 60)
            slots[command.number_slot] = {
                "hours": parts.get("hours", 0),
                "minutes": parts.get("minutes", 0),
                "seconds": parts.get("seconds", 0),
                "total_seconds": total,
                "text": f"{hours:02d}:{minutes:02d}:{seconds:02d}",
            }
    elif "number" in slots and command.number_slot != "number":
        slots[command.number_slot] = slots.pop("number")
    return slots
