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

from . import languages

OPERATIONS = ("start", "alarm", "cancel", "cancel_all", "pause", "resume", "add", "subtract", "status")
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
    clock = parse_clock(str(slots.get("time") or ""))
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


_SMALL = {
    "zero": 0, "oh": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50}


def _number_words(words: list[str]) -> list[int | str]:
    """Number words to numbers ("twenty five" -> 25, "oh five" -> 0, 5),
    other words kept."""
    out: list[int | str] = []
    index = 0
    while index < len(words):
        word = words[index]
        if word.isdigit():
            out.append(int(word))
        elif word in _TENS:
            value = _TENS[word]
            if index + 1 < len(words) and words[index + 1] in _SMALL and 0 < _SMALL[words[index + 1]] < 10:
                value += _SMALL[words[index + 1]]
                index += 1
            out.append(value)
        elif word in _SMALL:
            out.append(_SMALL[word])
        else:
            out.append(word)
        index += 1
    return out


def parse_clock(text: str) -> tuple[int, int, str | None] | None:
    """A clock time said or written: (hour, minute, "am" | "pm" | None)."""
    text = text.lower().replace(".", "").replace("o'clock", " ").replace("oclock", " ")
    text = re.sub(r"(\d)(am|pm)\b", r"\1 \2", text)
    text = re.sub(r"(\d{1,2}):(\d{2})", r"\1 \2", text)
    words = re.findall(r"[a-z]+|\d+", text)
    meridiem = None
    if any(w in ("pm", "evening", "night", "afternoon", "tonight") for w in words):
        meridiem = "pm"
    elif any(w in ("am", "morning") for w in words):
        meridiem = "am"
    if "noon" in words or "midday" in words:
        return 12, 0, "pm"
    if "midnight" in words:
        return 0, 0, "am"
    tokens = _number_words(words)
    numbers = [t for t in tokens if isinstance(t, int)]
    if not numbers:
        return None
    joined = " ".join(str(t) for t in tokens)
    # "half past seven", "quarter past seven", "quarter to eight", "ten to eight"
    for pattern, offset in (
        (r"half past (\d+)", 30),
        (r"quarter past (\d+)", 15),
        (r"quarter to (\d+)", -15),
        (r"(\d+) (?:minutes )?past (\d+)", None),
        (r"(\d+) (?:minutes )?to (\d+)", None),
    ):
        found = re.search(pattern, joined)
        if not found:
            continue
        if offset is not None:
            hour, minute = int(found.group(1)), offset
        else:
            amount, hour = int(found.group(1)), int(found.group(2))
            minute = amount if "past" in found.group(0) else -amount
        if minute < 0:
            hour, minute = hour - 1, 60 + minute
        return _valid(hour % 24 if hour >= 0 else 23, minute, meridiem)
    # "7 30", "seven thirty", "7 oh 5", "1930", "7"
    first = numbers[0]
    if first >= 100 and len(numbers) == 1:
        return _valid(first // 100, first % 100, meridiem)
    minute = 0
    rest = numbers[1:]
    if rest and rest[0] == 0 and len(rest) > 1:  # "seven oh five"
        minute = rest[1]
    elif rest:
        minute = rest[0]
    return _valid(first, minute, meridiem)


def _valid(hour: int, minute: int, meridiem: str | None) -> tuple[int, int, str | None] | None:
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    if hour > 12:
        meridiem = None  # 24-hour
    return hour, minute, meridiem


# What each outcome says, from a few made-up timers: the defaults a command's
# own responses replace (shown in the UI).
_SAMPLE = [
    {"timer_id": "1", "name": "pizza", "status": "active", "created_duration": 600, "remaining": 412},
    {"timer_id": "2", "name": None, "status": "active", "created_duration": 300, "remaining": 61},
    {"timer_id": "3", "name": "tea", "status": "paused", "created_duration": 240, "remaining": 90},
]
_SCENARIOS: list[tuple[dict[str, Any], list[dict[str, Any]]]] = [
    ({"timer": "pizza", "duration": 600, "time": "seven thirty in the morning"}, []),
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


def level_examples() -> dict[str, dict[str, str]]:
    """operation -> level -> what done says, for the UI (status answers; it
    has no levels)."""
    evening = datetime(2026, 1, 1, 21, 55)  # noqa: DTZ001 - local time, as plan takes it
    out: dict[str, dict[str, str]] = {}
    for operation in OPERATIONS:
        for slots, running in _SCENARIOS:
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
