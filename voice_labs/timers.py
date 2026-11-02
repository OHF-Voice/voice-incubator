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
question, and is answered with what is running. What each says is the app's
language's (its speech module's timer sentences).

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
from datetime import UTC, datetime, timedelta
from typing import Any

from . import alarms, languages

OPERATIONS = (
    "start", "alarm", "cancel", "cancel_all", "pause", "resume", "add", "subtract", "status",
    # On the calendar (voice_labs/alarms.py), in a language that keeps them there.
    "reminder", "cancel_alarm", "cancel_reminder", "alarm_status", "reminder_status",
)
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
    "alarm": ("done", "missing_time"),
    "cancel": ("done", "no_timers", "not_found", "ambiguous"),
    "cancel_all": ("done", "no_timers"),
    "pause": ("done", "no_timers", "not_found", "ambiguous", "already"),
    "resume": ("done", "no_timers", "not_found", "ambiguous", "already"),
    "add": ("done", "no_timers", "not_found", "ambiguous", "missing_duration"),
    "subtract": ("done", "no_timers", "not_found", "ambiguous", "missing_duration"),
    "status": ("done", "no_timers", "not_found"),
    **{op: outcomes for op, outcomes in alarms.OUTCOMES.items() if op != "alarm"},
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

    levels: dict[str, str] = field(default_factory=dict)
    """When done: what it says by level of verbosity (``speech`` is the
    specific one); none for a status, which answers."""

    def to_json(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "speech": self.speech,
            "levels": self.levels,
            "service": self.service,
            "data": self.data,
            "timer": self.timer,
            "calls": self.calls,
        }


def plan(
    operation: str, slots: dict[str, Any], running: list[dict[str, Any]], now: datetime | None = None
) -> Plan:
    """Decide what ``operation`` does with these slots and running timers.
    ``now`` (local time) is for an alarm."""
    now = now or datetime.now()
    say = languages.speech()
    if operation == "alarm":
        return _alarm(slots, now)
    duration = _seconds(slots.get("duration"))
    if operation == "start":
        if not duration:
            return Plan(say.TIMER_HOW_LONG, "missing_duration")
        name = _clean_name(slots.get("timer"))
        data: dict[str, Any] = {"duration": duration}
        if name:
            data["name"] = name
        timer = {"name": name, "created_duration": duration}
        ends = now + timedelta(seconds=duration)
        return _done("create_timer", data, timer, say.timer_started(timer, (ends.hour, ends.minute)))

    if not running:
        return Plan(say.NO_TIMERS, "no_timers")

    if operation == "cancel_all":
        calls = [("cancel_timer", {"timer_id": t["timer_id"]}) for t in running]
        levels = say.timers_cancelled(running)
        return Plan(levels["specific"], calls=calls, timers=running, levels=levels)

    # Which timers were meant, whatever their state; then which of them the
    # operation applies to (pausing a paused timer is "already", not "no such timer").
    candidates, asked = _match(running, slots, duration if operation not in ("add", "subtract") else None)
    if not candidates:
        return Plan(say.timer_not_found(asked, running), "not_found")

    if operation == "status":
        return Plan(
            say.timer_status(candidates),
            timers=candidates,
            timer=candidates[0] if len(candidates) == 1 else None,
        )

    ready = [t for t in candidates if t.get("status") in APPLIES_TO[operation]]
    if not ready:
        return Plan(
            say.timer_already(operation, candidates),
            "already",
            timers=candidates,
            timer=candidates[0] if len(candidates) == 1 else None,
        )
    if len(ready) > 1:
        return Plan(say.which_timer(ready), "ambiguous", timers=ready)

    timer = ready[0]
    data = {"timer_id": timer["timer_id"]}
    if operation in ("add", "subtract"):
        if not duration:
            return Plan(say.TIMER_HOW_MUCH, "missing_duration", timer=timer)
        data["duration"] = duration
        left = float(timer.get("remaining") or 0) + (duration if operation == "add" else -duration)
        return _done(SERVICES[operation], data, timer, say.timer_changed(operation, timer, duration, max(0.0, left)))
    return _done(SERVICES[operation], data, timer, say.timer_done(operation, timer))


def _done(service: str, data: dict[str, Any], timer: dict[str, Any], levels: dict[str, str]) -> Plan:
    """Done, saying the specific level by default."""
    return Plan(levels["specific"], "done", service, data, timer, levels=levels)


def _alarm(slots: dict[str, Any], now: datetime) -> Plan:
    """A timer that rings at a clock time: "seven thirty", "6 am", "half past
    six", "noon". Without am or pm, the next time it comes round."""
    clock = languages.reading().clock(str(slots.get("time") or ""))
    if clock is None:
        return Plan(languages.speech().ALARM_WHEN, "missing_time")
    hour, minute, meridiem = clock
    candidates = [hour] if meridiem or hour >= 12 or hour == 0 else [hour, hour + 12]
    if meridiem == "pm" and hour < 12:
        candidates = [hour + 12]
    elif meridiem == "am" and hour == 12:
        candidates = [0]
    best = None
    for candidate in candidates:
        at = now.replace(hour=candidate % 24, minute=minute, second=0, microsecond=0)
        seconds = (at - now).total_seconds()
        if seconds <= 0:
            seconds += 24 * 3600
        if best is None or seconds < best[0]:
            best = (seconds, candidate % 24)
    assert best is not None
    seconds, hour24 = best
    name = _clean_name(slots.get("timer")) or "alarm"
    data: dict[str, Any] = {"duration": int(round(seconds)), "name": name}
    timer = {"name": name, "created_duration": data["duration"]}
    return _done("create_timer", data, timer, languages.speech().alarm_set((hour24, minute), round(seconds / 60) * 60))


# What each outcome says, from a few made-up timers (named in the language's
# EXAMPLE_NAMES): the defaults a command's own responses replace (shown in the UI).
def _scenarios() -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    first, other, missing = languages.speech().EXAMPLE_NAMES["timers"]
    sample = [
        {"timer_id": "1", "name": first, "status": "active", "created_duration": 600, "remaining": 412},
        {"timer_id": "2", "name": None, "status": "active", "created_duration": 300, "remaining": 61},
        {"timer_id": "3", "name": other, "status": "paused", "created_duration": 240, "remaining": 90},
    ]
    return [
        ({"timer": first, "duration": 600, "time": languages.reading().ALARM_EXAMPLE}, []),
        ({"timer_name": first}, sample),
        ({"timer_name": first, "duration": 120}, sample),
        ({"timer_name": other}, sample),
        ({"timer_name": missing}, sample),
        ({}, sample),
        ({}, [{**t, "status": "paused"} for t in sample]),
        ({}, []),
    ]


def outcome_examples() -> dict[str, dict[str, str]]:
    """operation -> outcome -> what is said, for every outcome OUTCOMES lists."""
    out: dict[str, dict[str, str]] = {}
    calendar = alarms.outcome_examples()
    for operation, outcomes in OUTCOMES.items():
        if operation in alarms.OPERATIONS and (operation != "alarm" or alarms.words() is not None):
            out[operation] = calendar[operation]
            continue
        said: dict[str, str] = {}
        for slots, running in _scenarios():
            result = plan(operation, slots, running)
            said.setdefault(result.outcome, result.speech)
        out[operation] = {outcome: said[outcome] for outcome in outcomes}
    return out


def level_examples() -> dict[str, dict[str, str]]:
    """operation -> level -> what done says, for the UI (status answers; it
    has no levels)."""
    evening = datetime(2026, 1, 1, 21, 55)  # noqa: DTZ001 - local time, as plan takes it
    out: dict[str, dict[str, str]] = dict(alarms.level_examples())
    for operation in OPERATIONS:
        if operation in out or operation in alarms.OPERATIONS:
            continue
        for slots, running in _scenarios():
            result = plan(operation, slots, running, evening)
            if result.outcome == "done" and result.levels:
                out[operation] = result.levels
                break
    return out


def _match(
    pool: list[dict[str, Any]], slots: dict[str, Any], duration: float | None
) -> tuple[list[dict[str, Any]], str | float | None]:
    """Running timers that fit what was said, and how it was asked for: by
    name, or by length (seconds)."""
    exact = slots.get("timer_name")
    text = _clean_name(slots.get("timer"))
    asked: str | float | None = None
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
            asked = duration
            candidates = sized
        elif sized:
            # With a name, the length only tells apart timers of that name.
            candidates = sized
    return candidates, asked


LAYERS = 6
"""The most timers the ring shows at once, soonest first: each gets an LED
of its own at least."""
COLORS = (-1, 0xFFFF00, 0xFF00FF, 0x00FF00, 0x00FFFF, 0xFFFFFF)
"""Each running timer's color on the ring (0xRRGGBB), in order: -1 is the
LED Ring light's own, so a timer alone looks as it always did. The others
have each channel full or off: the ring's gamma (2.8) takes a channel
between down to nearly nothing, so orange showed red, yellow orange and
violet the blue of the LED Ring light. None is red (the mute switch and
errors) or blue (beside the LED Ring light's)."""


def colors(running: list[dict[str, Any]], kept: dict[str, int]) -> dict[str, int]:
    """Each running timer's color (its index in COLORS), by timer id: its own
    while it runs, paused too; a new one takes the first that no other has,
    and once every one is taken they go round again. ``kept`` (the
    satellite's) is changed in place."""
    ids = [str(t["timer_id"]) for t in running if t.get("timer_id")]
    for gone in set(kept) - set(ids):
        del kept[gone]
    for timer_id in ids:
        if timer_id not in kept:
            taken = set(kept.values())
            kept[timer_id] = next((c for c in range(len(COLORS)) if c not in taken), len(kept) % len(COLORS))
    return kept


def progress(
    running: list[dict[str, Any]], kept: dict[str, int] | None = None, now: datetime | None = None
) -> dict[str, Any]:
    """What the satellite's LED ring shows (the ``timer_progress`` event): the
    active timer that finishes soonest -- its time left of how long it runs --
    or ``shown: 0``. Paused timers are not shown, as in the stock firmware.

    And every active timer, soonest first (``count``, at most LAYERS; each
    ``left<i>`` and ``color<i>``, its color by ``kept``: ``colors``'), on
    the scale of the longest one's length (``scale``): the ring is the time
    to come, each timer's color as far as it runs, the sooner ones on top.
    Firmware from before draws the soonest alone."""
    now = now or datetime.now(UTC)
    active: list[tuple[float, float, dict[str, Any]]] = []
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
        total = float(timer.get("total_duration") or timer.get("created_duration") or left or 1)
        active.append((left, max(total, left), timer))
    if not active:
        return {"shown": 0}
    active.sort(key=lambda item: item[0])
    left, total, _ = active[0]
    shown: dict[str, Any] = {"shown": 1, "remaining": round(left, 2), "total": round(total, 2), "paused": 0}
    shown.update(count=min(len(active), LAYERS), scale=round(max(t for _, t, _ in active), 2))
    for index, (left, _, timer) in enumerate(active[:LAYERS]):
        shown[f"left{index}"] = round(left, 2)
        shown[f"color{index}"] = COLORS[(kept or {}).get(str(timer.get("timer_id")), 0)]
    return shown


def same_progress(last: dict[str, Any], shown: dict[str, Any], elapsed: float) -> bool:
    """Whether the ring shows ``shown`` already: ``last`` was sent
    ``elapsed`` seconds ago, and the device has counted it down since."""
    if {k: v for k, v in last.items() if not _counted(k)} != {k: v for k, v in shown.items() if not _counted(k)}:
        return False
    return all(abs(max(float(last[k]) - elapsed, 0.0) - float(shown[k])) < 1.0 for k in shown if _counted(k))


def _counted(key: str) -> bool:
    """A time left, which the device counts down by itself."""
    return key == "remaining" or (key.startswith("left") and key[4:].isdigit())


def stops_ringing(operation: str | None, slots: dict[str, Any]) -> bool:
    """Whether a command said over a ringing alarm is only "stop that": a
    cancel that names no timer (or alarm)."""
    return operation in ("cancel", "cancel_all", "cancel_alarm", "cancel_reminder") and not any(
        slots.get(k) for k in ("timer", "timer_name", "duration", "time", "reminder")
    )


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
    return re.sub(r"[\W_]+", "", str(value or "").lower())
