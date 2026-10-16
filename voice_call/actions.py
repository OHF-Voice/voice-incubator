"""Doing what a command says, in Home Assistant.

Every kind of command -- a spoken response, a script, a scene, an action -- is
run the same way: as an action sequence through ``execute_script``, with these
variables, which the sequence and the response template can both use:

==============  ============================================================
``slots``       what was heard, plus the command's fixed slots: ``entity``,
                ``area``, ``floor`` (lists of ids), ``number`` (or the
                preset's name: ``percentage``, ``duration`` ...), value
                lists, free text
``satellite``   ``id``, ``name``, ``area_id``, ``area_name``, ``floor_id``,
                ``floor_name``, ``device_id``, ``timer_list``
``speaker``     ``name`` (None when not recognized) and ``score``
``target``      ``entity_id``/``area_id``/``floor_id`` from the slots, or
                else the satellite's area: ``target: "{{ target }}"``
``command``     ``id``, ``description``, ``text`` (what was heard)
``place``       where it acts: ``here`` (only the satellite's area),
                ``said`` ("in the Kitchen", "upstairs"; empty when unknown),
                ``areas`` (ids; a floor's every area)
``result``      in the response only: what the sequence returned, plus
                ``outcome`` and ``speech`` (what would be said without the
                response); ``error`` when it failed
``timer``       for a timer command, in the response only: the timer it was
                about; ``timers``: the ones it listed
==============  ============================================================

A timer command (``timer: cancel`` ...) is carried out by the app on the
satellite's voice_timer_list (voice_call/timers.py), and says what it did unless
the command has a response of its own. A music command (``music: play`` ...)
is carried out on a Music Assistant player (voice_call/music.py); its response
also sees ``player`` (the one it acted on), ``players`` and, when it searched,
``media`` (what it found).

How it went is the outcome: ``done``, ``failed`` (Home Assistant refused), a
timer operation's own (``no_timers``, ``not_found`` ...), or the ``outcome`` a
script returned. The command's response for that outcome is spoken, or else
the built-in sentence: what the timer operation says, the script's
``speech``, FAILED.

How much is said when it is done is the command's verbosity
(commands.VERBOSITY): a beep, or its response for that level -- or the
closest written -- else the built-in sentence for it. A beep is for what the
satellite's own area can see happen: a command that acted elsewhere says the
``elsewhere`` level instead (settings.yaml ``beep_elsewhere``), and a
question always answers.

Every command that runs also fires a ``voice_call_command`` event with the
same variables plus ``outcome`` and ``response`` (what was said), so
automations can follow along.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from . import music as music_ops
from . import timers
from .commands import VERBOSITY, Behavior, Command, closest_text
from .engine import Decision, Satellite
from .hass import HassError, HomeAssistant

TIMERS_NEED_INTEGRATION = "Timers need the Voice Call integration in Home Assistant."

_LOGGER = logging.getLogger(__name__)

EVENT_TYPE = "voice_call_command"
FAILED = "Sorry, that didn't work."
BEEP_ELSEWHERE = "specific"
"""What a command that beeps says when it acted out of the satellite's area."""


@dataclass
class Outcome:
    variables: dict[str, Any]
    sequence: list[dict]
    dry_run: bool
    response: Any = None
    """What the sequence returned (``stop`` with ``response_variable``)."""

    speech: str = ""
    name: str = "done"
    """How it went: ``done``, ``failed``, a timer operation's or a script's own."""

    error: str | None = None
    timings: dict[str, float] = field(default_factory=dict)
    timer: dict[str, Any] | None = None
    """For a timer command: what it planned (voice_call/timers.py)."""

    elsewhere: str = BEEP_ELSEWHERE
    """The level for a beep when it acted out of the satellite's area."""

    music: dict[str, Any] | None = None
    """For a music command: the players, and what it searched and found."""

    verbosity: dict[str, Any] | None = None
    """When done: the level said at, and why."""

    def to_json(self) -> dict[str, Any]:
        return {
            "outcome": self.name,
            "verbosity": self.verbosity,
            "timer": self.timer,
            "music": self.music,
            "dry_run": self.dry_run,
            "variables": self.variables,
            "sequence": self.sequence,
            "response": self.response,
            "speech": self.speech,
            "error": self.error,
            "timings": {k: round(v, 4) for k, v in self.timings.items()},
        }


def variables(
    command: Command,
    slots: dict[str, Any],
    satellite: Satellite,
    speaker: dict[str, Any] | None,
    text: str,
    catalog: Any = None,
) -> dict[str, Any]:
    target: dict[str, list[str]] = {}
    for slot, key in (("entity", "entity_id"), ("area", "area_id"), ("floor", "floor_id")):
        if slots.get(slot):
            target[key] = list(slots[slot])
    if not target and satellite.area_id:
        target = {"area_id": [satellite.area_id]}
    return {
        "slots": slots,
        "satellite": satellite.as_dict(),
        "speaker": speaker or {"name": None, "score": None},
        "target": target,
        "command": {"id": command.id, "description": command.description, "text": text},
        "place": place(slots, satellite, catalog),
    }


def place(slots: dict[str, Any], satellite: Satellite, catalog: Any = None) -> dict[str, Any]:
    """Where a command acts: the areas, floors and devices it names, or else
    the satellite's area. ``here`` when that is only the satellite's area --
    a device in no known area is not here."""
    own = satellite.area_id
    named_areas = [str(a) for a in slots.get("area") or []]
    floors = [str(f) for f in slots.get("floor") or []]
    entities = [str(e) for e in slots.get("entity") or []]
    if not (named_areas or floors or entities):
        return {"here": True, "said": _in_area(satellite.area_name) if satellite.area_name else "", "areas": [own] if own else []}
    areas = list(named_areas)
    entity_areas: list[str] = []
    if catalog is not None:
        for floor in floors:
            areas += [a.area_id for a in catalog.areas.values() if a.floor_id == floor]
        for entity_id in entities:
            entity = catalog.entities.get(entity_id)
            if entity is not None and entity.area_id:
                entity_areas.append(entity.area_id)
    areas = list(dict.fromkeys(areas + entity_areas))
    unplaced = any(catalog is None or not getattr(catalog.entities.get(e), "area_id", None) for e in entities)
    here = bool(own) and not unplaced and (not floors or areas == [own]) and set(areas) == {own}

    def area_name(area_id: str) -> str:
        if area_id == own and satellite.area_name:
            return satellite.area_name
        area = catalog.areas.get(area_id) if catalog is not None else None
        return area.name if area is not None else area_id.replace("_", " ").title()

    if floors:
        names = [catalog.floors[f].name if catalog is not None and f in catalog.floors else f.replace("_", " ") for f in floors]
        said = " and ".join(_on_floor(name) for name in names)
    else:
        said = " and ".join(_in_area(area_name(a)) for a in (named_areas or list(dict.fromkeys(entity_areas))))
    return {"here": here, "said": said, "areas": areas}


def _in_area(name: str) -> str:
    """"in the Kitchen", "in Sam's Room", "outside"."""
    lower = name.lower()
    if lower in ("upstairs", "downstairs", "outside", "outdoors", "inside", "indoors"):
        return lower
    if lower.startswith("the ") or "'s" in lower:
        return f"in {name}"
    return f"in the {name}"


def _on_floor(name: str) -> str:
    """"upstairs", "on the First Floor", "in the Basement"."""
    lower = name.lower()
    if lower in ("upstairs", "downstairs"):
        return lower
    if "floor" in lower or "level" in lower:
        return f"on the {name}" if not lower.startswith("the ") else f"on {name}"
    return _in_area(name)


async def run(
    hass: HomeAssistant,
    decision: Decision,
    satellite: Satellite,
    speaker: dict[str, Any] | None,
    dry_run: bool = False,
    integration=None,
    music: music_ops.Music | None = None,
    elsewhere: str = BEEP_ELSEWHERE,
) -> Outcome:
    """Run the decided command (unless ``dry_run``) and render what to say.
    ``elsewhere``: the level a command that beeps says at when it acted out
    of the satellite's area."""
    command, behavior = decision.command, decision.behavior
    assert command is not None and behavior is not None
    catalog = getattr(hass, "catalog", None)
    values = variables(command, decision.slots, satellite, speaker, decision.transcript, catalog)
    outcome = Outcome(values, behavior.sequence(), dry_run, elsewhere=elsewhere)
    if behavior.timer:
        return await _run_timer(hass, behavior, values, satellite, outcome, integration)
    if behavior.music:
        return await _run_music(hass, behavior, values, satellite, outcome, music or music_ops.Music(hass))

    if outcome.sequence and not dry_run:
        started = time.perf_counter()
        try:
            outcome.response = await hass.execute_script(outcome.sequence, values)
        except (HassError, ConnectionError, OSError, TimeoutError) as err:
            outcome.error = str(err)
            _LOGGER.error("Command %s failed: %s", command.id, err)
            return await _say(hass, behavior, values, outcome, "failed", FAILED, {"error": str(err)})
        finally:
            outcome.timings["execute"] = time.perf_counter() - started

    returned = outcome.response if isinstance(outcome.response, dict) else {}
    name = returned.get("outcome")
    speech = returned.get("speech")
    return await _say(
        hass,
        behavior,
        values,
        outcome,
        name if isinstance(name, str) and name else "done",
        speech if isinstance(speech, str) else "",
        returned,
    )


async def _run_timer(
    hass: HomeAssistant,
    behavior: Behavior,
    values: dict[str, Any],
    satellite: Satellite,
    outcome: Outcome,
    integration,
) -> Outcome:
    """Plan a timer operation against the satellite's running timers, carry
    it out (unless a dry run), and say how it went."""
    if integration is None or not integration.ready or not satellite.timer_list:
        outcome.error = "no timer list for this satellite"
        return await _say(hass, behavior, values, outcome, "failed", TIMERS_NEED_INTEGRATION, {"error": outcome.error})
    started = time.perf_counter()
    try:
        running = await integration.fresh_timers(satellite.key)
    except (HassError, ConnectionError, OSError, TimeoutError) as err:
        outcome.error = str(err)
        return await _say(hass, behavior, values, outcome, "failed", FAILED, {"error": str(err)})
    plan = timers.plan(behavior.timer, values["slots"], running)
    target = {"entity_id": satellite.timer_list}
    calls = ([(plan.service, plan.data)] if plan.service else []) + plan.calls
    outcome.sequence = [
        {"action": f"voice_timer_list.{service}", "target": target, "data": data}
        for service, data in calls
    ]
    outcome.timer = plan.to_json()
    values = {**values, "timer": plan.timer, "timers": plan.timers}
    if not outcome.dry_run:
        try:
            for service, data in calls:
                response = await hass.call_service(
                    "voice_timer_list",
                    service,
                    data,
                    target,
                    return_response=service == "create_timer",
                )
                if service == "create_timer" and plan.timer is not None:
                    created = (response or {}).get(satellite.timer_list) or {}
                    plan.timer["timer_id"] = created.get("timer_id")
        except (HassError, ConnectionError, OSError, TimeoutError) as err:
            outcome.error = str(err)
            _LOGGER.error("Timer command failed: %s", err)
            return await _say(hass, behavior, values, outcome, "failed", FAILED, {"error": str(err)})
    outcome.timings["timer"] = time.perf_counter() - started
    return await _say(hass, behavior, values, outcome, plan.outcome, plan.levels or plan.speech)


async def _run_music(
    hass: HomeAssistant,
    behavior: Behavior,
    values: dict[str, Any],
    satellite: Satellite,
    outcome: Outcome,
    music: music_ops.Music,
) -> Outcome:
    """Find the player (and, to play, the music), carry it out (unless a dry
    run), and say how it went."""
    operation = behavior.music
    assert operation is not None
    slots = values["slots"]
    query = slots.get("query")
    if operation == "play" and music_ops.generic(query):
        operation = "resume"  # "play some music": whatever was playing
    named, nearby = _music_players(hass, slots, satellite)
    own = satellite.media_player
    started = time.perf_counter()
    trace: dict[str, Any] = {"operation": operation, "own": own, "named": named, "nearby": nearby}
    outcome.music = trace
    try:
        players = await music.players(list(dict.fromkeys(([own] if own else []) + (named or []) + nearby)))
    except (HassError, ConnectionError, OSError, TimeoutError, ValueError) as err:
        outcome.error = str(err)
        return await _say(hass, behavior, values, outcome, "failed", FAILED, {"error": str(err)})
    trace["players"] = {k: p.as_dict() for k, p in players.items()}
    choosing = "play" if operation == "radio" and not music_ops.generic(query) else operation
    choice = music_ops.choose(choosing, players, own, named, nearby, music.paused_by_voice(satellite.key))
    chosen = choice.players
    values = {
        **values,
        "player": chosen[0].as_dict() if chosen else None,
        "players": [p.as_dict() for p in chosen],
        "place": _players_place(chosen, satellite, getattr(hass, "catalog", None)) if chosen else values["place"],
    }
    if choice.outcome:
        return await _say(hass, behavior, values, outcome, choice.outcome, music_ops.SAID[choice.outcome])

    ids = [p.entity_id for p in chosen]
    calls: list[tuple[str, str, dict]] = []
    try:
        if operation in music_ops.FIND:
            if operation == "radio" and music_ops.generic(query):
                item = await music.current(ids[0])
                if item is None:
                    return await _say(hass, behavior, values, outcome, "nothing_playing", music_ops.NOTHING_PLAYING)
            elif music_ops.generic(query):
                return await _say(hass, behavior, values, outcome, "missing_query", music_ops.SAID["missing_query"])
            else:
                item = await music.find(str(query), slots.get("media_type"), ids[0])
                trace["queries"] = [q.__dict__ for q in music_ops.queries(query, slots.get("media_type"))]
                if item is None:
                    return await _say(hass, behavior, values, outcome, "not_found", f"I couldn't find {music_ops.clean(query)}.")
            trace["found"] = {**item.as_dict(), "score": round(item.score, 3)}
            values = {**values, "media": item.as_dict()}
            data: dict[str, Any] = {"media_id": item.uri, "media_type": item.media_type}
            if operation == "add":
                data["enqueue"] = "add"
            elif operation == "radio":
                data["radio_mode"] = True
            speech: str | dict[str, str] = music_ops.found_said(operation, item, chosen)
            calls = [("music_assistant", "play_media", data)]
            name = "done"
        else:
            controls, speech, name = music_ops.control(operation, slots, chosen)
            if name == "done" and not speech:
                speech = music_ops.control_said(operation, slots, chosen)
            calls = [("media_player", service, data) for service, data in controls]
    except LookupError as err:
        outcome.error = str(err)
        return await _say(hass, behavior, values, outcome, "failed", FAILED, {"error": str(err)})
    except (HassError, ConnectionError, OSError, TimeoutError) as err:
        outcome.error = str(err)
        _LOGGER.error("Music command failed: %s", err)
        return await _say(hass, behavior, values, outcome, "failed", FAILED, {"error": str(err)})

    target = {"entity_id": ids}
    outcome.sequence = [{"action": f"{d}.{s}", "target": target, "data": data} for d, s, data in calls]
    if not outcome.dry_run:
        try:
            for domain, service, data in calls:
                await hass.call_service(domain, service, data, target)
        except (HassError, ConnectionError, OSError, TimeoutError) as err:
            outcome.error = str(err)
            _LOGGER.error("Music command failed: %s", err)
            return await _say(hass, behavior, values, outcome, "failed", FAILED, {"error": str(err)})
        if operation == "pause":
            music.remember_paused(satellite.key, ids)
        elif operation in ("resume", "play", "stop"):
            music.forget_paused(satellite.key)
    outcome.timings["music"] = time.perf_counter() - started
    return await _say(hass, behavior, values, outcome, name, speech)


def _players_place(players: list[music_ops.Player], satellite: Satellite, catalog: Any) -> dict[str, Any]:
    """Where music acts: the players' areas (the satellite's own player is
    where it is)."""
    areas = []
    for player in players:
        entity = catalog.entities.get(player.entity_id) if catalog is not None else None
        if player.entity_id == satellite.media_player:
            areas.append(satellite.area_id)
        else:
            areas.append(entity.area_id if entity is not None else None)
    here = bool(areas) and all(a is not None and a == satellite.area_id for a in areas)
    known = [a for a in dict.fromkeys(areas) if a]
    said = place({"area": known}, satellite, catalog)["said"] if known else ""
    return {"here": here, "said": said, "areas": known}


def _music_players(
    hass: HomeAssistant, slots: dict[str, Any], satellite: Satellite
) -> tuple[list[str] | None, list[str]]:
    """The players a command names (entities, or Music Assistant's in the
    areas and floors it names; None when it names none), and Music
    Assistant's others in the satellite's area."""
    catalog = getattr(hass, "catalog", None)
    players = catalog.music_players() if catalog is not None else []
    named: list[str] | None = None
    if slots.get("entity") or slots.get("area") or slots.get("floor"):
        named = [str(e) for e in slots.get("entity") or [] if str(e).startswith("media_player.")]
        areas = set(slots.get("area") or [])
        floors = set(slots.get("floor") or [])
        if floors and catalog is not None:
            areas |= {a.area_id for a in catalog.areas.values() if a.floor_id in floors}
        named += [p.entity_id for p in players if areas and p.area_id in areas and p.entity_id not in named]
    nearby = [
        p.entity_id
        for p in players
        if satellite.area_id and p.area_id == satellite.area_id and p.entity_id != satellite.media_player
    ]
    return named, nearby


def level_for(behavior: Behavior, here: bool, elsewhere: str = BEEP_ELSEWHERE) -> tuple[str, str]:
    """The verbosity a done command says at, and why."""
    level = behavior.level
    why = "the command's" if behavior.verbosity else "the default for what it does"
    if level == "beep" and behavior.question:
        return "brief", "a question always answers"
    if level == "beep" and not here and elsewhere in VERBOSITY and elsewhere != "beep":
        return elsewhere, "it acted out of the satellite's area"
    return level, why


async def _say(
    hass: HomeAssistant,
    behavior: Behavior,
    values: dict[str, Any],
    outcome: Outcome,
    name: str,
    default: str | dict[str, str],
    result: dict[str, Any] | None = None,
) -> Outcome:
    """What to say for how it went (``name``): the command's response for
    that outcome, rendered, or else ``default`` -- when done, at the level of
    verbosity (``default`` may be a sentence for each). Then tell
    automations."""
    outcome.name = name
    level = None
    if name == "done":
        level, why = level_for(behavior, bool(values["place"]["here"]), outcome.elsewhere)
        outcome.verbosity = {"level": level, "why": why, "place": values["place"]}
    if isinstance(default, dict):
        default = closest_text(default, level or "specific") or ""
    template = behavior.response_for(name, level) if level != "beep" else None
    if level == "beep":
        outcome.speech = ""
    elif template:
        started = time.perf_counter()
        try:
            rendered = await hass.render_template(
                template, {**values, "result": {**(result or {}), "outcome": name, "speech": default}}
            )
            outcome.speech = _tidy(rendered)
        except (HassError, ConnectionError, OSError, TimeoutError) as err:
            outcome.error = outcome.error or f"response template: {err}"
            _LOGGER.error("Could not render the response: %s", err)
            outcome.speech = FAILED
        finally:
            outcome.timings["render"] = time.perf_counter() - started
    else:
        outcome.speech = default
    if not outcome.dry_run:
        try:
            await hass.fire_event(EVENT_TYPE, {**values, "outcome": name, "response": outcome.speech})
        except (HassError, ConnectionError, OSError, TimeoutError) as err:
            _LOGGER.warning("Could not fire %s: %s", EVENT_TYPE, err)
    return outcome


def _tidy(text: Any) -> str:
    """A rendered response without the gaps an empty variable leaves:
    "Turned on the lights ." is "Turned on the lights."."""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return re.sub(r"\s+([.,!?;:])", r"\1", text)
