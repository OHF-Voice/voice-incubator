"""Voice commands: what is listened for, and what happens.

A command is one adapt-ctc intent plus what to do when it matches::

    commands:
      ecobee_on:
        description: turn on the thermostat      # a verb phrase, for hints
        slots: {entity: [climate.ecobee]}        # fixed slots: the target
        examples: [turn on the EcoBee, turn on the Echo Bee]
        require: [[turn on, turn ... on], [ecobee, echo bee]]
        script: script.heat_up                   # or scene:, action:, or none
        response: "OK, {{ slots.entity[0] }} is on"

``title``, ``icon`` (a Material Design Icon, ``mdi:lightbulb``) and
``library`` (the library file it was added from, ``music``: where the
Home page lists it) are only for the UI.

Listening is adapt-ctc's own vocabulary -- ``examples`` (sentences, or
templates of them: ``(turn|switch) on the light[s]``), ``require``,
``optional``, ``exclude``, ``names``, ``number``, ``lists`` and ``wildcard``
-- with two additions: ``number`` may be a preset (``percentage``,
``temperature``, ``duration``, ``count``) and may name its slot.

``slots`` are values every match carries as if they had been heard: the
entity, area or floor a command was made for, so the phrase can be anything
while the target stays fixed.

What is heard -- names, a number, value lists, free text -- is never
optional. ``combinations`` lists the sets of them that may be said together,
each required whole (the first is the one examples and the description belong
to); without it, all of them, together::

    names: {area: {count: 1}, floor: {count: 1}}
    combinations: [[], [area], [floor]]    # nothing named: the satellite's area

Each combination is an adapt-ctc intent of its own (``lights_off``,
``lights_off:area``, ``lights_off:floor``; see ``command_of``), so a command
never runs with something that was said left out: "turn off the lights in the
bedroom" is the area combination, not the satellite's area.

Exactly what happens is one of ``script`` (a script entity, run with the
variables as its data), ``scene`` (activated), ``action`` (one Home Assistant
action or a list of them, as in a script), ``timer`` (an operation on the
satellite's own timers: start, cancel, cancel_all, pause, resume, add, subtract,
status; see voice_call/timers.py), or ``music`` (playing or controlling Music
Assistant's players: play, pause, next, volume_up ...; see voice_call/music.py),
plus an optional ``response`` template that is spoken. A command may also only
respond, or do nothing at all -- "never mind" -- which by default says nothing,
not even the acknowledgement sound.

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

``satellite`` puts a command on one satellite (by its key); without it, the
command is the Home's, and runs on every satellite that takes the Home's
commands (all do unless told not to: ``home: false`` in ``satellites.yaml``).
A command that should do something else on one satellite is a copy of it on
that satellite: there, its own copy goes first::

    commands:
      lights_on_kids:
        satellite: d83bdaa3d5f4

``fallback`` lets the steps after keywords and examples run the command when
nothing else matched (voice_call/fallback.py): ``meaning`` when a sentence is
very close to it in meaning (the model hears what was said in it), ``llm``
when the small LLM picks it. Neither unless chosen::

    lights_off:
      fallback: [meaning, llm]
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any

from adapt_ctc.config import ConfigError
from adapt_ctc.template import TemplateError, expand
from adapt_ctc.text import sentence_key

from . import languages

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
# How much a command says when it is done. Silent is nothing at all, not even
# a sound ("never mind"); a beep is the acknowledgement sound (a question still
# answers either way); brief is a fixed sentence ("Turned on the lights.",
# which text-to-speech caches); specific says what was heard ("... in the
# Kitchen", "the 5 minute timer"); verbose what it did, in full ("Turned on
# Ceiling, Lamp and Strip in the Kitchen."). Other outcomes always say what
# went wrong. By number, 0 to 4.
VERBOSITY = ("silent", "beep", "brief", "specific", "verbose")
QUIET = VERBOSITY[:2]
"""The levels with nothing to say."""
SPOKEN = VERBOSITY[2:]
"""The levels with something to say, which a done response can be written for."""
COMMAND_KEYS = {
    "description",
    "slots",
    "response",
    "verbosity",
    "disabled",
    "title",
    "icon",
    "library",
    "satellite",
    "fallback",
    "combinations",
    *LISTEN_KEYS,
    *DO_KEYS,
}
# Keys a command no longer takes, and what to do instead.
REMOVED_KEYS = {
    "scope": "a command is the Home's or one satellite's (satellite: its key)",
    "overrides": "make a copy of the command that does the other thing (Duplicate, on its page) "
    "on the satellite it is for: there, its own copy goes first",
    "groups": "command groups are gone: a command is the Home's, or one satellite's (satellite: its key)",
}
FALLBACK_STEPS = ("meaning", "llm")

# Shared settings for every home, whatever its language: examples that win
# when said word for word. The embedding names the closest command when
# nothing matched; it never accepts one. The language's own -- its filler
# words, name cues, compounds -- come from its pack (base_config).
BASE_CONFIG: dict[str, Any] = {
    "numbers": {"detect": {"min": 0, "max": 200}},
    "settings": {"examples_first": True, "examples_whole": True, "embedding_accept": False},
}


def base_config() -> dict[str, Any]:
    """The matcher's config for every home in the app's language, before
    its commands: BASE_CONFIG and the language pack's (``Pack.adapt``)."""
    return {**copy.deepcopy(BASE_CONFIG), **copy.deepcopy(languages.current().adapt)}

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

    response: str | dict[str, Any] | None = None
    """A template, or one for each outcome (see ``response_for``); done's may
    be one for each level of verbosity (SPOKEN)."""

    verbosity: str | None = None
    """How much it says when done (VERBOSITY); None: ``default_verbosity``."""

    def response_for(self, outcome: str, level: str | None = None) -> str | None:
        """The template for this outcome; a plain response is for ``done``.
        Written by level, done's is the one for ``level`` (the command's own,
        unless given), else the closest written: less said, then more."""
        texts = self.texts(outcome)
        if not texts:
            return None
        return closest_text(texts, level or self.level)

    def texts(self, outcome: str) -> dict[str, str]:
        """The templates for this outcome by level; one written for every
        level is under each."""
        value = self.response.get(outcome) if isinstance(self.response, dict) else (
            self.response if outcome == "done" else None
        )
        if not value:
            return {}
        if isinstance(value, dict):
            return {level: value[level] for level in SPOKEN if value.get(level)}
        return dict.fromkeys(SPOKEN, value)

    @property
    def question(self) -> bool:
        """Whether it only answers (a response, or a timer's or music's
        status): it never just beeps."""
        return self.does == "respond" or self.timer == "status" or self.music == "status"

    @property
    def default_verbosity(self) -> str:
        """Music controls (pause, next, volume ...) beep; the rest say their
        response (an action without one still beeps)."""
        if self.music and self.music not in ("play", "add", "radio", "status"):
            return "beep"
        if self.does == "nothing":
            return "silent"
        return "specific"

    @property
    def level(self) -> str:
        return self.verbosity or self.default_verbosity

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
        if self.does == "nothing":
            return ("done",)
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
        # Only a response, or nothing at all: "never mind".
        return "respond" if self.texts("done") else "nothing"

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
    behavior: Behavior
    intent: dict[str, Any]
    """Everything it listens for: every slot, as one adapt-ctc intent (the
    fallback's tools are made from it)."""

    number_slot: str = "number"
    duration: bool = False
    disabled: bool = False
    title: str = ""
    satellite: str | None = None
    """The satellite it is on (its key); None: the Home's."""
    fallback: tuple[str, ...] = ()
    """The steps after keywords and examples that may run it (FALLBACK_STEPS)."""
    combinations: tuple[tuple[str, ...], ...] = ((),)
    """The sets of slots that may be heard together, each required whole."""
    intents: dict[str, dict[str, Any]] = field(default_factory=dict)
    """One adapt-ctc intent for each combination, by name (``command_of``)."""

    @property
    def takes_nothing(self) -> bool:
        """Whether it can run with nothing heard (a combination of no slots)."""
        return () in self.combinations

    @property
    def takes_something(self) -> bool:
        """Whether some combination needs a name, number, value or text heard."""
        return any(self.combinations)

    @property
    def heard_slots(self) -> tuple[str, ...]:
        """Every slot some combination hears, in order."""
        return tuple(dict.fromkeys(slot for combination in self.combinations for slot in combination))

    def combination_for(self, heard: set[str] | frozenset[str]) -> tuple[str, ...] | None:
        """The combination these heard slots are, exactly; None if none is."""
        wanted = set(heard)
        return next((c for c in self.combinations if set(c) == wanted), None)



def closest_text(texts: dict[str, str], level: str) -> str | None:
    """The text for ``level`` (silent or a beep counts as brief), else the
    closest: less said first, then more."""
    index = SPOKEN.index(level) if level in SPOKEN else 0 if level in QUIET else SPOKEN.index("specific")
    order = list(SPOKEN[index::-1]) + list(SPOKEN[index + 1 :])
    return next((texts[lvl] for lvl in order if texts.get(lvl)), None)


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
    for key in sorted(set(spec) & set(REMOVED_KEYS)):
        raise ConfigError(f"{where}.{key}: no longer used: {REMOVED_KEYS[key]}")
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
    _no_optional_slots(intent, where)
    heard = _heard_slots(intent, number_slot)
    combinations = _combinations(spec.get("combinations"), heard, f"{where}.combinations")
    intents = {
        combination_name(command_id, combination, index == 0): _combination_intent(
            intent, combination, number_slot, duration, primary=index == 0
        )
        for index, combination in enumerate(combinations)
    }
    if duration:
        _ensure_unit_keywords(intent)
    behavior = _behavior(spec, where)
    _check_outcomes(behavior, f"{where}.response")

    return Command(
        command_id,
        description,
        behavior,
        intent,
        number_slot,
        duration,
        bool(spec.get("disabled", False)),
        str(spec.get("title") or ""),
        _satellite(spec.get("satellite"), f"{where}.satellite"),
        _fallback_steps(spec.get("fallback"), f"{where}.fallback"),
        combinations,
        intents,
    )


COMBINATION_SEPARATOR = ":"


def command_of(intent_name: str) -> str:
    """The command an adapt-ctc intent is a combination of: ``lights_off``
    for ``lights_off:area``."""
    return intent_name.split(COMBINATION_SEPARATOR, 1)[0]


def combination_name(command_id: str, combination: tuple[str, ...], primary: bool = False) -> str:
    """The adapt-ctc intent of one combination: the command's id for the first
    (its examples and description go with it), else ``id:area+floor``."""
    if primary:
        return command_id
    return f"{command_id}{COMBINATION_SEPARATOR}{'+'.join(combination) or 'nothing'}"


_REQUIRED_ONLY = (
    "{where}: slots are never optional; list the ones that may be said together "
    "in combinations ([[], [area]] for 'with or without an area')"
)


def _no_optional_slots(intent: dict[str, Any], where: str) -> None:
    for kind, spec in (intent.get("names") or {}).items():
        if isinstance(spec, dict) and (spec.get("optional") or spec.get("min") == 0):
            raise ConfigError(_REQUIRED_ONLY.format(where=f"{where}.names.{kind}"))
    for slot, spec in (intent.get("lists") or {}).items():
        if isinstance(spec, dict) and "optional" in spec:
            raise ConfigError(_REQUIRED_ONLY.format(where=f"{where}.lists.{slot}"))
    number = intent.get("number")
    if isinstance(number, dict) and "optional" in number:
        raise ConfigError(_REQUIRED_ONLY.format(where=f"{where}.number"))
    wildcard = intent.get("wildcard")
    if isinstance(wildcard, dict) and ("required" in wildcard or "optional" in wildcard):
        raise ConfigError(_REQUIRED_ONLY.format(where=f"{where}.wildcard"))


def _heard_slots(intent: dict[str, Any], number_slot: str) -> tuple[str, ...]:
    """The slots it listens for, in order: names, value lists, the number,
    free text."""
    slots = [str(kind) for kind in intent.get("names") or {}]
    slots += [str(slot) for slot in intent.get("lists") or {}]
    if "number" in intent:
        slots.append(number_slot)
    wildcard = intent.get("wildcard")
    if isinstance(wildcard, dict) and wildcard.get("slot"):
        slots.append(str(wildcard["slot"]))
    return tuple(dict.fromkeys(slots))


def _combinations(value: Any, heard: tuple[str, ...], where: str) -> tuple[tuple[str, ...], ...]:
    """The combinations, each in the slots' own order; all of them together
    when none are given."""
    if value in (None, []):
        return (heard,)
    if not isinstance(value, list) or not all(isinstance(c, list) for c in value):
        raise ConfigError(f"{where}: a list of slot lists, like [[], [area], [floor]]")
    out: list[tuple[str, ...]] = []
    for index, combination in enumerate(value):
        unknown = [str(s) for s in combination if str(s) not in heard]
        if unknown:
            known = ", ".join(heard) or "none"
            raise ConfigError(f"{where}[{index}]: {unknown[0]!r} is not a slot it listens for ({known})")
        ordered = tuple(s for s in heard if s in {str(x) for x in combination})
        if ordered in out:
            raise ConfigError(f"{where}[{index}]: {list(ordered)} is listed twice")
        out.append(ordered)
    unused = [s for s in heard if not any(s in c for c in out)]
    if unused:
        raise ConfigError(f"{where}: {unused[0]!r} is in no combination; add one or stop listening for it")
    return tuple(out)


def _combination_intent(
    intent: dict[str, Any], combination: tuple[str, ...], number_slot: str, duration: bool, primary: bool
) -> dict[str, Any]:
    """The adapt-ctc intent that hears exactly these slots."""
    out = copy.deepcopy(intent)
    if not primary:
        out.pop("examples", None)
        out.pop("description", None)
    names = {k: v for k, v in (out.get("names") or {}).items() if k in combination}
    if names:
        out["names"] = names
    else:
        out.pop("names", None)
    lists = {k: v for k, v in (out.get("lists") or {}).items() if k in combination}
    if lists:
        out["lists"] = lists
    else:
        out.pop("lists", None)
    if "number" in out and number_slot not in combination:
        del out["number"]
    wildcard = out.get("wildcard")
    if isinstance(wildcard, dict):
        if wildcard.get("slot") in combination:
            out["wildcard"] = {**wildcard, "required": True}
        else:
            del out["wildcard"]
    if duration and "number" in out:
        _ensure_unit_keywords(out)
    return out


def _fallback_steps(value: Any, where: str) -> tuple[str, ...]:
    if value in (None, []):
        return ()
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise ConfigError(f"{where}: a list of {' and '.join(FALLBACK_STEPS)}")
    unknown = [str(v) for v in value if v not in FALLBACK_STEPS]
    if unknown:
        raise ConfigError(f"{where}: {unknown[0]!r} is not one of {', '.join(FALLBACK_STEPS)}")
    return tuple(step for step in FALLBACK_STEPS if step in value)


def _satellite(value: Any, where: str) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{where}: a satellite's key")
    return value.strip()


UNKNOWN_KEYS = {"response"}


def parse_unknown(data: Any) -> str | None:
    """What is said when nothing understood a sentence, from a store's
    ``unknown`` mapping: a response template, or None (the built-in hint).
    Raises ConfigError."""
    if data in (None, {}):
        return None
    spec = _mapping(data, "unknown")
    extra = set(spec) - UNKNOWN_KEYS
    if extra:
        raise ConfigError(f"unknown: unknown key(s) {sorted(extra)} (use response)")
    response = spec.get("response")
    if response in (None, ""):
        return None
    if not isinstance(response, str):
        raise ConfigError("unknown.response: expected a template string")
    return response


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
    verbosity = _verbosity(spec.get("verbosity"), f"{where}.verbosity")
    return Behavior(
        slots=_fixed_slots(spec.get("slots"), f"{where}.slots"),
        script=script,
        scene=scene,
        action=_actions(spec["action"], f"{where}.action") if spec.get("action") else None,
        timer=timer,
        music=music,
        response=response or None,
        verbosity=verbosity,
    )


def _verbosity(value: Any, where: str) -> str | None:
    """A level by name, or 0 (silent) to 4 (verbose)."""
    if value in (None, ""):
        return None
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < len(VERBOSITY):
        return VERBOSITY[value]
    if str(value) in VERBOSITY:
        return str(value)
    raise ConfigError(f"{where}: one of {', '.join(VERBOSITY)} (or 0 to {len(VERBOSITY) - 1})")


def _response(value: Any, where: str) -> str | dict[str, Any] | None:
    """A template, or templates by outcome; done's may be templates by level."""
    if value in (None, "", {}):
        return None
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        raise ConfigError(f"{where}: expected a template, or templates by outcome (done: …, failed: …)")
    out: dict[str, Any] = {}
    for outcome, template in value.items():
        outcome = str(outcome)
        if not OUTCOME_NAME.match(outcome):
            raise ConfigError(f"{where}.{outcome}: outcomes are lowercase letters, digits and underscores")
        if template in (None, "", {}):
            continue
        if outcome == "done" and isinstance(template, dict):
            levels = {str(k): v for k, v in template.items() if v not in (None, "")}
            unknown = [k for k in levels if k not in SPOKEN]
            if unknown:
                raise ConfigError(f"{where}.done.{unknown[0]}: by level, one of {', '.join(SPOKEN)}")
            if any(not isinstance(v, str) for v in levels.values()):
                raise ConfigError(f"{where}.done: expected a template string for each level")
            if levels:
                out[outcome] = {level: levels[level] for level in SPOKEN if level in levels}
            continue
        if not isinstance(template, str):
            raise ConfigError(
                f"{where}.{outcome}: expected a template string"
                + (" (by level only when done)" if isinstance(template, dict) else "")
            )
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
            kind = {"respond": "only a response", "nothing": "nothing to do"}.get(behavior.does, f"a {behavior.does}")
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


def shared_examples(commands: dict[str, Command]) -> tuple[dict[str, dict[str, Any]], dict[str, tuple[str, ...]]]:
    """Every enabled command's intents, with each example sentence left only
    to the first that has it; and, for that one, the later intents that have
    it too (its twins). A copy of a command on a satellite says the same
    sentences: adapt-ctc gives a sentence one intent, and the engine runs the
    twin where the first is not available, or where the twin is the
    satellite's own. An intent left with nothing to
    listen for is only a twin."""
    intents: dict[str, dict[str, Any]] = {}
    twins: dict[str, list[str]] = {}
    owner: dict[str, str] = {}
    for command in commands.values():
        if command.disabled:
            continue
        for name, spec in command.intents.items():
            kept = []
            for text in spec.get("examples") or []:
                # A template is every sentence it stands for: one whose every
                # sentence another has is a twin's; one only some of whose
                # sentences another has is an error (adapt-ctc says which).
                keys = [sentence_key(s, languages.current().code) for s in example_sentences(str(text))]
                taken = [owner[k] for k in keys if owner.get(k, name) != name]
                if keys and len(taken) == len(keys):
                    if name not in twins.setdefault(taken[0], []):
                        twins[taken[0]].append(name)
                    continue
                for key in keys:
                    owner.setdefault(key, name)
                kept.append(text)
            if len(kept) < len(spec.get("examples") or []):
                spec = {**spec, "examples": kept} if kept else {k: v for k, v in spec.items() if k != "examples"}
                if not kept and not any(spec.get(k) for k in ("require", "names", "number", "lists", "wildcard")):
                    continue
            intents[name] = spec
    return intents, {name: tuple(others) for name, others in twins.items()}


def example_sentences(template: str) -> list[str]:
    """The sentences an example stands for (adapt_ctc.template); a template
    that can't be read, as written (adapt-ctc says what is wrong with it)."""
    try:
        return expand(template)
    except TemplateError:
        return [template]


def adapt_config(store: dict[str, Any], commands: dict[str, Command]) -> dict[str, Any] | None:
    """The adapt-ctc config for every enabled command; None without any."""
    intents, _twins = shared_examples(commands)
    if not intents:
        return None
    config = base_config()
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
