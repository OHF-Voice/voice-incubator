"""Timer commands: start, cancel, pause, resume, add or take off time, and
"how long is left", on the satellite's own voice_timer_list.

A command says which operation (``timer: cancel``); which timer comes from the
slots and from the timers that are running on that satellite:

* ``timer_name`` -- a running timer's name, recognized as such (the
  ``running_timers`` value list, kept current by the app), matched exactly;
* ``timer`` -- free text, matched to the running timers' names loosely, since
  it is spelled however the speech model heard it ("Lasaga");
* ``duration`` -- the length the timer was set for ("the 5 minute timer");
* none of them -- the only running timer.

Several timers left after all that is ambiguous for anything but a status
question, and is answered with what is running.

Every plan has an ``outcome`` saying how it went, so a command can say something
else for each (``response: {done: ..., no_timers: ...}``):

``done``              it was done (or, for status, said)
``no_timers``         nothing is running on this satellite
``not_found``         the timer asked for (by name or length) isn't running
``ambiguous``         several timers fit; asks which
``already``           pausing a paused timer, resuming a running one
``missing_duration``  starting a timer or adding time, with no length heard
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

OPERATIONS = ("start", "cancel", "cancel_all", "pause", "resume", "add", "subtract", "status")
SERVICES = {
    "cancel": "cancel_timer",
    "pause": "pause_timer",
    "resume": "unpause_timer",
    "add": "add_time",
    "subtract": "subtract_time",
}
# Which running timers an operation can act on.
APPLIES_TO = {
    "cancel": ("active", "paused"),
    "pause": ("active",),
    "resume": ("paused",),
    "add": ("active", "paused"),
    "subtract": ("active", "paused"),
    "status": ("active", "paused"),
}
# How each operation can turn out (Plan.outcome), in the order they are offered.
OUTCOMES = {
    "start": ("done", "missing_duration"),
    "cancel": ("done", "no_timers", "not_found", "ambiguous"),
    "cancel_all": ("done", "no_timers"),
    "pause": ("done", "no_timers", "not_found", "ambiguous", "already"),
    "resume": ("done", "no_timers", "not_found", "ambiguous", "already"),
    "add": ("done", "no_timers", "not_found", "ambiguous", "missing_duration"),
    "subtract": ("done", "no_timers", "not_found", "ambiguous", "missing_duration"),
    "status": ("done", "no_timers", "not_found"),
}
NAME_MATCH = 0.6
"""Least similarity between free text and a timer's name to take it as that
timer (difflib ratio, normalized text)."""


@dataclass
class Plan:
    """What a timer command comes to: a service call (or none), the timer it
    is about, and what to say."""

    speech: str
    outcome: str = "done"
    """How it went: one of OUTCOMES[operation]."""

    service: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    timer: dict[str, Any] | None = None
    timers: list[dict[str, Any]] = field(default_factory=list)
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    """Extra service calls (cancelling all)."""

    def to_json(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "speech": self.speech,
            "service": self.service,
            "data": self.data,
            "timer": self.timer,
            "calls": self.calls,
        }


def plan(operation: str, slots: dict[str, Any], running: list[dict[str, Any]]) -> Plan:
    """Decide what ``operation`` does with these slots and running timers."""
    duration = _seconds(slots.get("duration"))
    if operation == "start":
        if not duration:
            return Plan("How long should the timer be?", "missing_duration")
        name = _clean_name(slots.get("timer"))
        data: dict[str, Any] = {"duration": duration}
        if name:
            data["name"] = name
        timer = {"name": name, "created_duration": duration}
        return Plan(f"{_describe(timer, capital=True)} started.", "done", "create_timer", data, timer)

    if not running:
        return Plan("There are no timers.", "no_timers")

    if operation == "cancel_all":
        calls = [("cancel_timer", {"timer_id": t["timer_id"]}) for t in running]
        count = len(running)
        return Plan(
            "Cancelled the timer." if count == 1 else f"Cancelled {count} timers.",
            calls=calls,
            timers=running,
        )

    # Which timers were meant, whatever their state; then which of them the
    # operation applies to (pausing a paused timer is "already", not "no such timer").
    candidates, asked = _match(running, slots, duration if operation not in ("add", "subtract") else None)
    if not candidates:
        return Plan(_not_found(asked, running), "not_found")

    if operation == "status":
        said = _join([_status(t) for t in candidates])
        return Plan(
            said[0].upper() + said[1:] + ".",
            timers=candidates,
            timer=candidates[0] if len(candidates) == 1 else None,
        )

    ready = [t for t in candidates if t.get("status") in APPLIES_TO[operation]]
    if not ready:
        return Plan(
            _already(operation, candidates),
            "already",
            timers=candidates,
            timer=candidates[0] if len(candidates) == 1 else None,
        )
    if len(ready) > 1:
        listed = _join([_describe(t) for t in ready])
        return Plan(f"Which timer? There's {listed}.", "ambiguous", timers=ready)

    timer = ready[0]
    data = {"timer_id": timer["timer_id"]}
    if operation in ("add", "subtract"):
        if not duration:
            return Plan("How much time?", "missing_duration", timer=timer)
        data["duration"] = duration
        verb = "Added" if operation == "add" else "Took"
        prep = "to" if operation == "add" else "off"
        return Plan(
            f"{verb} {_spoken_duration(duration)} {prep} {_describe(timer)}.",
            "done",
            SERVICES[operation],
            data,
            timer,
        )
    done = {"cancel": "cancelled", "pause": "paused", "resume": "resumed"}[operation]
    return Plan(f"{_describe(timer, capital=True)} {done}.", "done", SERVICES[operation], data, timer)


# What each outcome says, from a few made-up timers: the defaults a command's
# own responses replace (shown in the UI).
_SAMPLE = [
    {"timer_id": "1", "name": "pizza", "status": "active", "created_duration": 600, "remaining": 412},
    {"timer_id": "2", "name": None, "status": "active", "created_duration": 300, "remaining": 61},
    {"timer_id": "3", "name": "tea", "status": "paused", "created_duration": 240, "remaining": 90},
]
_SCENARIOS: list[tuple[dict[str, Any], list[dict[str, Any]]]] = [
    ({"timer": "pizza", "duration": 600}, []),
    ({"timer_name": "pizza"}, _SAMPLE),
    ({"timer_name": "pizza", "duration": 120}, _SAMPLE),
    ({"timer_name": "tea"}, _SAMPLE),
    ({"timer_name": "lasagna"}, _SAMPLE),
    ({}, _SAMPLE),
    ({}, [{**t, "status": "paused"} for t in _SAMPLE]),
    ({}, []),
]


def outcome_examples() -> dict[str, dict[str, str]]:
    """operation -> outcome -> what is said, for every outcome OUTCOMES lists."""
    out: dict[str, dict[str, str]] = {}
    for operation, outcomes in OUTCOMES.items():
        said: dict[str, str] = {}
        for slots, running in _SCENARIOS:
            result = plan(operation, slots, running)
            said.setdefault(result.outcome, result.speech)
        out[operation] = {outcome: said[outcome] for outcome in outcomes}
    return out


def _match(
    pool: list[dict[str, Any]], slots: dict[str, Any], duration: float | None
) -> tuple[list[dict[str, Any]], str | None]:
    """Running timers that fit what was said, and how it was asked for."""
    exact = slots.get("timer_name")
    text = _clean_name(slots.get("timer"))
    asked = None
    candidates = pool
    if exact:
        asked = str(exact)
        candidates = [t for t in candidates if _norm(t.get("name")) == _norm(exact)]
    elif text:
        asked = text
        scored = [(difflib.SequenceMatcher(None, _norm(text), _norm(t.get("name"))).ratio(), t) for t in candidates if t.get("name")]
        best = max((score for score, _ in scored), default=0.0)
        candidates = [t for score, t in scored if score >= NAME_MATCH and score >= best - 0.05]
    if duration:
        sized = [t for t in candidates if abs(float(t.get("created_duration") or 0) - duration) < 1]
        if asked is None:
            asked = f"{_spoken_duration(duration, adjective=True)} timer"
            candidates = sized
        elif sized:
            # With a name, the length only tells apart timers of that name.
            candidates = sized
    return candidates, asked


def _not_found(asked: str | None, running: list[dict[str, Any]]) -> str:
    listed = _join([_describe(t) for t in running])
    if not asked:
        return f"There's {listed}."
    name = asked if asked.endswith("timer") else f"{asked} timer"
    return f"There's no {name}. There's {listed}."


def _already(operation: str, timers: list[dict[str, Any]]) -> str:
    state = "paused" if operation == "pause" else "running"
    if len(timers) == 1:
        return f"{_describe(timers[0], capital=True)} is already {state}."
    return f"All the timers are already {state}."


def _status(timer: dict[str, Any]) -> str:
    left = _spoken_duration(float(timer.get("remaining") or 0))
    if timer.get("status") == "paused":
        return f"{_describe(timer)} is paused with {left} left"
    return f"{left} left on {_describe(timer)}"


def progress(running: list[dict[str, Any]], now: datetime | None = None) -> dict[str, Any]:
    """What the satellite's LED ring shows (the ``timer_progress`` event): the
    active timer that finishes soonest -- its time left of how long it runs --
    or ``shown: 0``. Paused timers are not shown, as in the stock firmware."""
    now = now or datetime.now(UTC)
    best: tuple[float, dict[str, Any]] | None = None
    for timer in running:
        if timer.get("status") != "active":
            continue
        left = float(timer.get("remaining") or 0)
        if timer.get("finishes_at"):
            try:
                left = (datetime.fromisoformat(str(timer["finishes_at"])) - now).total_seconds()
            except ValueError:
                pass
        left = max(0.0, left)
        if best is None or left < best[0]:
            best = (left, timer)
    if best is None:
        return {"shown": 0}
    left, timer = best
    total = float(timer.get("total_duration") or timer.get("created_duration") or left or 1)
    return {"shown": 1, "remaining": round(left, 2), "total": round(max(total, left), 2), "paused": 0}


def stops_ringing(operation: str | None, slots: dict[str, Any]) -> bool:
    """Whether a command said over a ringing alarm is only "stop that": a
    cancel that names no timer."""
    return operation in ("cancel", "cancel_all") and not any(
        slots.get(k) for k in ("timer", "timer_name", "duration")
    )


def _describe(timer: dict[str, Any], capital: bool = False) -> str:
    """"the pizza timer", or "the 5 minute timer"."""
    name = timer.get("name")
    if name:
        text = f"the {name} timer"
    else:
        text = f"the {_spoken_duration(float(timer.get('created_duration') or 0), adjective=True)} timer"
    return text[0].upper() + text[1:] if capital else text


def _spoken_duration(seconds: float, adjective: bool = False) -> str:
    """"1 hour and 5 minutes"; as an adjective, "1 hour 5 minute"."""
    total = int(round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    parts = []
    for value, unit in ((hours, "hour"), (minutes, "minute"), (secs, "second")):
        if value or (unit == "second" and not parts):
            plural = "" if adjective or value == 1 else "s"
            parts.append(f"{value} {unit}{plural}")
    if hours:
        parts = parts[:2]  # an hour and some: seconds don't matter
    if adjective:
        return " ".join(parts)
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def _join(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def _seconds(duration: Any) -> float | None:
    if isinstance(duration, dict):
        return float(duration.get("total_seconds") or 0) or None
    if isinstance(duration, (int, float)):
        return float(duration) or None
    return None


def _clean_name(value: Any) -> str | None:
    if not value:
        return None
    text = re.sub(r"[^\w' -]+", "", str(value)).strip()
    return text.lower() or None


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())
