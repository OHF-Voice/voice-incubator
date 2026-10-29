"""Alarms and timed reminders, on a Local Calendar in Home Assistant.

In a language that has a ``calendar`` module (English, for now), an alarm or a
reminder with a time is an event on a calendar of the app's own -- Home
Assistant's Local Calendar named "Voice Labs", which the app creates when it
connects -- so it outlives a restart, may be for another day or repeat
("every weekday at 7"), and shows in Home Assistant's calendar, where it can
be changed. Each event says which satellite set it, and of which kind
(``[voice_labs:alarm:<satellite>]`` in its description).

The app follows the calendar's next days (``calendar/event/subscribe``: Home
Assistant sends them again whenever they change; read over REST where they
come without their uids, or can't be followed) and, when one starts, rings
the satellite that set it: an alarm as a timer that finished, until "stop"; a
reminder is said. An event without the mark -- one added in Home Assistant --
is a reminder said on every satellite. One missed by up to ``LATE`` (the app
was not running) still goes off; older, it is let go.

A reminder without a time goes on a to-do list instead, as it did.
"""

from __future__ import annotations

import asyncio
import difflib
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from types import ModuleType
from typing import Any
from zoneinfo import ZoneInfo

from . import languages
from .hass import HassError, HomeAssistant

_LOGGER = logging.getLogger(__name__)

CALENDAR_NAME = "Voice Labs"
"""The Local Calendar the app keeps alarms and reminders on."""

WINDOW = timedelta(days=8)
"""How far ahead the app follows the calendar (and lists alarms)."""

LATE = timedelta(minutes=5)
"""How late an alarm or a reminder still goes off."""

RENEW = 900.0
"""Seconds between moving the followed window on (and reading it again)."""

POLL = 300.0
"""Seconds between reads of the calendar where it can't be followed (a Home
Assistant without ``calendar/event/subscribe``)."""

OPERATIONS = ("alarm", "reminder", "cancel_alarm", "cancel_reminder", "alarm_status", "reminder_status")
"""The timer operations done on the calendar (``alarm`` there only in a
language with a calendar module)."""

MARK = re.compile(r"\[voice_labs:(alarm|reminder):([^\]\s]*)\]")


def mark(kind: str, satellite: str | None) -> str:
    return f"[voice_labs:{kind}:{satellite or ''}]"


def words() -> ModuleType | None:
    """The app's language's calendar module; None for a language without."""
    return languages.current().calendar


@dataclass
class Event:
    """An alarm or a reminder, as the calendar has it."""

    uid: str
    start: datetime
    summary: str
    kind: str = "reminder"
    satellite: str | None = None
    rrule: str | None = None
    recurrence_id: str | None = None
    description: str = ""

    @property
    def key(self) -> tuple[str, str]:
        """This occurrence."""
        return self.uid, self.recurrence_id or self.start.isoformat()

    def to_json(self) -> dict[str, Any]:
        return {
            "uid": self.uid, "start": self.start.isoformat(), "summary": self.summary, "kind": self.kind,
            "satellite": self.satellite, "rrule": self.rrule,
        }


def parse_event(raw: dict[str, Any], tz: ZoneInfo) -> Event | None:
    """An event Home Assistant sent -- its start a string (subscribed) or
    ``{"dateTime": ...}`` (REST); None for an all-day one (no time to go off)."""
    start = raw.get("start")
    if isinstance(start, dict):
        start = start.get("dateTime")  # {"date": ...} is all day
    if not isinstance(start, str) or "T" not in start and " " not in start:
        return None
    try:
        when = datetime.fromisoformat(start)
    except ValueError:
        return None
    when = when.replace(tzinfo=tz) if when.tzinfo is None else when.astimezone(tz)
    description = str(raw.get("description") or "")
    found = MARK.search(description)
    return Event(
        uid=str(raw.get("uid") or start),
        start=when,
        summary=str(raw.get("summary") or ""),
        kind=found.group(1) if found else "reminder",
        satellite=(found.group(2) or None) if found else None,
        rrule=raw.get("rrule") or None,
        recurrence_id=raw.get("recurrence_id") or None,
        description=description,
    )


class AlarmCalendar:
    """The calendar, from the app's side: made if missing, followed, and
    written to. ``on_due`` gets each event as it starts."""

    def __init__(self, hass: HomeAssistant, on_due: Callable[[Event], None]) -> None:
        self.hass = hass
        self.on_due = on_due
        self.entity_id: str | None = None
        self.status = "connecting"
        """``connecting``, ``ready``, or why not: shown in the UI."""
        self.tz = ZoneInfo("UTC")
        self.events: list[Event] = []
        """The followed window's events, each occurrence, by start."""
        self._fired: dict[tuple[str, str], datetime] = {}
        self._waiting: dict[tuple[str, str], asyncio.Task] = {}
        self._subscription: int | None = None
        self._subscribable = True
        """Whether Home Assistant has ``calendar/event/subscribe``."""
        self._renew: asyncio.Task | None = None
        self._reading: asyncio.Task | None = None
        hass.on_connect(self._on_connect)

    @property
    def ready(self) -> bool:
        return self.status == "ready"

    def now(self) -> datetime:
        return datetime.now(self.tz)

    # --------------------------------------------------------------- setup

    async def _on_connect(self) -> None:
        self._subscription = None  # died with the old connection
        try:
            config = await self.hass.call({"type": "get_config"})
            self.tz = ZoneInfo(str((config or {}).get("time_zone") or "UTC"))
        except (HassError, ConnectionError, OSError, TimeoutError, KeyError, ValueError) as err:
            _LOGGER.warning("No time zone from Home Assistant (%s): UTC", err)
        await self.ensure()
        if self.ready:
            await self.follow()

    async def ensure(self) -> None:
        """Find the calendar, or make it."""
        try:
            self.entity_id = await self._find()
            if self.entity_id is None:
                flow = await self.hass.start_config_flow("local_calendar")
                if flow.get("type") == "form":
                    await self.hass.config_flow_step(flow["flow_id"], {"calendar_name": CALENDAR_NAME})
                _LOGGER.info("Made the %s calendar", CALENDAR_NAME)
                for _ in range(10):  # its entity comes a moment after
                    self.entity_id = await self._find()
                    if self.entity_id:
                        break
                    await asyncio.sleep(0.5)
            self.status = "ready" if self.entity_id else f"Home Assistant made no {CALENDAR_NAME} calendar."
        except HassError as err:
            self.status = f"No {CALENDAR_NAME} calendar: Home Assistant's Local Calendar couldn't be set up ({err})."
            _LOGGER.warning(self.status)
        except (ConnectionError, OSError, TimeoutError) as err:
            self.status = f"Could not reach Home Assistant: {err}"

    async def _find(self) -> str | None:
        """The calendar entity of the Local Calendar named CALENDAR_NAME."""
        entries = [e for e in await self.hass.config_entries("local_calendar") if e.get("title") == CALENDAR_NAME]
        if not entries:
            return None
        ids = {e.get("entry_id") for e in entries}
        registry = await self.hass.call({"type": "config/entity_registry/list"}) or []
        return next(
            (e["entity_id"] for e in registry if e.get("config_entry_id") in ids and e["entity_id"].startswith("calendar.")),
            None,
        )

    # -------------------------------------------------------------- follow

    async def follow(self) -> None:
        """Follow the next days' events, and move the window on now and then
        (from now: a reconnect lost the subscription)."""
        if self._renew is not None:
            self._renew.cancel()
        self._renew = asyncio.ensure_future(self._renewing())

    async def _renewing(self) -> None:
        while True:
            try:
                await self._follow_once()
            except (HassError, ConnectionError, OSError, TimeoutError) as err:
                _LOGGER.warning("Could not follow the %s calendar: %s", CALENDAR_NAME, err)
            await asyncio.sleep(RENEW if self._subscription is not None else POLL)

    async def _follow_once(self) -> None:
        """Subscribe to the window from now, as the frontend does -- changes
        come by themselves -- and read it."""
        now = self.now()
        if self._subscription is not None:
            old, self._subscription = self._subscription, None
            await self.hass.unsubscribe(old)
        if self._subscribable:
            try:
                self._subscription = await self.hass.subscribe(
                    {"type": "calendar/event/subscribe", "entity_id": self.entity_id,
                     "start": (now - LATE).isoformat(), "end": (now + WINDOW).isoformat()},
                    lambda event: self._pushed(event.get("events")),
                )
            except HassError as err:
                if "unknown_command" not in str(err) and "Unknown command" not in str(err):
                    raise
                self._subscribable = False  # an older Home Assistant: read now and then
        await self._read()

    def _pushed(self, raw: list[dict[str, Any]] | None) -> None:
        """What the subscription sent: used as it is when each event has its
        uid (needed to cancel one), else read again over REST."""
        if raw is not None and all(r.get("uid") for r in raw):
            self.changed(raw)
        elif self._reading is None or self._reading.done():
            self._reading = asyncio.ensure_future(self._reread())

    async def _read(self) -> None:
        now = self.now()
        self.changed(await self.hass.calendar_events(str(self.entity_id), now - LATE, now + WINDOW))

    def changed(self, raw: list[dict[str, Any]] | None) -> None:
        """The window's events, as Home Assistant has them now."""
        if raw is None:
            return  # it couldn't read the calendar: keep what we have
        events = [e for e in (parse_event(r, self.tz) for r in raw) if e is not None]
        self.events = sorted(events, key=lambda e: e.start)
        self._schedule()

    def _schedule(self) -> None:
        now = self.now()
        wanted = {e.key: e for e in self.events if e.start > now - LATE and e.key not in self._fired}
        for key in [k for k in self._waiting if k not in wanted]:
            self._waiting.pop(key).cancel()
        for key, event in wanted.items():
            task = self._waiting.get(key)
            if task is None or task.done():
                self._waiting[key] = asyncio.ensure_future(self._go_off(event))
        # Forget what went off long ago.
        for key in [k for k, at in self._fired.items() if at < now - 2 * LATE]:
            del self._fired[key]

    async def _go_off(self, event: Event) -> None:
        delay = (event.start - self.now()).total_seconds()
        if delay > 0:
            await asyncio.sleep(delay)
        self._waiting.pop(event.key, None)
        self._fired[event.key] = event.start
        try:
            self.on_due(event)
        except Exception:  # noqa: BLE001 - a satellite's trouble mustn't stop the calendar
            _LOGGER.exception("Could not ring %s", event.summary)

    # --------------------------------------------------------------- write

    async def create(self, kind: str, summary: str, start: datetime, rrule: str | None,
                     satellite: str, satellite_name: str) -> None:
        """Add an alarm or a reminder, set on ``satellite``."""
        event: dict[str, Any] = {
            "summary": summary,
            "dtstart": start.isoformat(),
            "dtend": (start + timedelta(minutes=5)).isoformat(),
            "description": f"Set by voice on {satellite_name}. {mark(kind, satellite)}",
        }
        if rrule:
            event["rrule"] = rrule
        await self.hass.call({"type": "calendar/event/create", "entity_id": self.entity_id, "event": event})
        await self._reread()

    async def delete(self, event: Event) -> None:
        """Remove an alarm or a reminder, every time it repeats."""
        await self.hass.call({"type": "calendar/event/delete", "entity_id": self.entity_id, "uid": event.uid})
        self.events = [e for e in self.events if e.uid != event.uid]
        self._schedule()
        await self._reread()

    async def _reread(self) -> None:
        """Read it now: after a change of the app's own (the subscription
        need not send one), or one it sent without uids."""
        try:
            await self._read()
        except (HassError, ConnectionError, OSError, TimeoutError) as err:
            _LOGGER.warning("Could not read the %s calendar: %s", CALENDAR_NAME, err)

    def upcoming(self, kind: str, satellite: str | None = None) -> list[Event]:
        """The ones still to come, each once (a repeating one by its next
        time), soonest first: a satellite's own, and those of none."""
        now, seen, out = self.now(), set(), []
        for event in self.events:
            if event.kind != kind or event.start <= now or event.uid in seen:
                continue
            if satellite is not None and event.satellite not in (None, satellite):
                continue
            seen.add(event.uid)
            out.append(event)
        return out


# ---------------------------------------------------------------- planning


@dataclass
class Plan:
    """What an alarm or reminder command comes to: what to add, remove or put
    on a to-do list, and what to say."""

    speech: str
    outcome: str = "done"
    levels: dict[str, str] = field(default_factory=dict)
    create: dict[str, Any] | None = None
    """``{kind, summary, start, rrule}``."""
    delete: list[Event] = field(default_factory=list)
    todo: str | None = None
    """A reminder without a time: its text, for the to-do list."""

    def to_json(self) -> dict[str, Any]:
        create = dict(self.create or {})
        if create.get("start"):
            create["start"] = create["start"].isoformat()
        return {
            "outcome": self.outcome, "speech": self.speech, "levels": self.levels, "create": create or None,
            "delete": [e.to_json() for e in self.delete], "todo": self.todo,
        }


def _done(levels: dict[str, str], **kwargs: Any) -> Plan:
    return Plan(levels["specific"], "done", levels, **kwargs)


def plan(operation: str, slots: dict[str, Any], now: datetime, alarms: list[Event], reminders: list[Event],
         todo: bool = False) -> Plan:
    """Decide what ``operation`` does: with the satellite's ``alarms`` and
    ``reminders`` still to come; ``todo``, whether a reminder without a time
    has a to-do list to go on."""
    say = words()
    assert say is not None
    if operation == "alarm":
        text = str(slots.get("time") or "")
        found = say.when(text, now, alarm=True)
        if found is None:
            return Plan(say.ALARM_WHEN, "missing_time")
        levels = say.alarm_set(found.start, found.rrule, now)
        return _done(levels, create={"kind": "alarm", "summary": say.ALARM_TITLE, "start": found.start,
                                     "rrule": found.rrule})
    if operation == "reminder":
        text = str(slots.get("reminder") or "")
        found = say.when(text, now)
        if found is None:
            told = say.what(text)
            if not told:
                return Plan(say.REMINDER_WHAT, "missing_reminder")
            if say.wants_time(text) or not todo:
                return Plan(say.REMINDER_WHEN, "missing_time")
            return Plan(say.reminder_listed(told), "listed", todo=told)
        told = say.what(found.rest)
        if not told:
            return Plan(say.REMINDER_WHAT, "missing_reminder")
        levels = say.reminder_set(told, found.start, found.rrule, now, found.relative)
        return _done(levels, create={"kind": "reminder", "summary": told[:1].upper() + told[1:], "start": found.start,
                                     "rrule": found.rrule})
    if operation == "alarm_status":
        return Plan(say.alarms_listed(_said(alarms), now) if alarms else say.NO_ALARMS, "done" if alarms else "no_alarms")
    if operation == "reminder_status":
        listed = [{"start": e.start, "rrule": e.rrule, "text": e.summary} for e in reminders]
        return Plan(say.reminders_listed(listed, now) if listed else say.NO_REMINDERS,
                    "done" if listed else "no_reminders")
    if operation == "cancel_alarm":
        if not alarms:
            return Plan(say.NO_ALARMS, "no_alarms")
        text = str(slots.get("time") or "")
        picked, asked = _at(alarms, text, now, say, alarm=True)
        if not picked:
            when_said = say.said_when(asked.start, asked.rrule, now) if asked is not None else text
            return Plan(say.alarm_not_found(when_said, _said(alarms), now), "not_found")
        if len(picked) > 1:
            return Plan(say.which_alarm(_said(picked), now), "ambiguous")
        return _done(say.alarm_cancelled(_said(picked), now), delete=picked)
    if operation == "cancel_reminder":
        if not reminders:
            return Plan(say.NO_REMINDERS, "no_reminders")
        text = str(slots.get("reminder") or "")
        picked, asked = _at(reminders, text, now, say, alarm=False)
        picked = _about(picked, say.about(asked.rest if asked is not None else text), say)
        if not picked:
            return Plan(say.reminder_not_found(_said(reminders), now), "not_found")
        if len(picked) > 1:
            return Plan(say.which_reminder(_said(picked), now), "ambiguous")
        return _done(say.reminder_cancelled(_said(picked), now), delete=picked)
    raise ValueError(f"not a calendar operation: {operation}")


ABOUT_MATCH = 0.6
"""Least likeness of what was said to a reminder's text to take it as that
reminder: the share of its words in the text, or the texts' difflib ratio."""


def _at(events: list[Event], text: str, now: datetime, say: ModuleType, alarm: bool) -> tuple[list[Event], Any]:
    """The events at the time ``text`` says -- am or pm when it doesn't say
    ("cancel the 7 o'clock alarm") -- on the day it says, if it says one;
    and the time read (None without one)."""
    asked = say.when(text, now, alarm=alarm) if text else None
    if asked is None:
        on = say.day(text, now) if text else None
        return ([e for e in events if e.rrule or e.start.date() == on] if on is not None else list(events)), None
    clock = languages.reading().clock(asked.said)
    hour, minute, meridiem = clock if clock else (asked.start.hour, asked.start.minute, "am")
    picked = [
        e for e in events
        if e.start.minute == minute
        and (e.start.hour == asked.start.hour if meridiem or hour > 12 else e.start.hour % 12 == hour % 12)
    ]
    if say.wants_time(asked.said) and not asked.rrule:
        picked = [e for e in picked if e.rrule or e.start.date() == asked.start.date()]
    return picked, asked


def _about(events: list[Event], words: list[str], say: ModuleType) -> list[Event]:
    """The reminders most like what was said they are about; all of them when
    nothing was."""
    if not words or not events:
        return events
    said = " ".join(words)

    def likeness(event: Event) -> float:
        theirs = say.about(event.summary)
        shared = sum(1 for w in words if w in theirs) / len(words)
        return max(shared, difflib.SequenceMatcher(None, said, " ".join(theirs)).ratio())

    scored = [(likeness(e), e) for e in events]
    best = max(score for score, _ in scored)
    if best < ABOUT_MATCH:
        return []
    return [e for score, e in scored if score >= best - 0.05]


def _said(events: list[Event]) -> list[dict[str, Any]]:
    return [{"start": e.start, "rrule": e.rrule, "text": e.summary} for e in events]


OUTCOMES = {
    "alarm": ("done", "missing_time"),
    "reminder": ("done", "listed", "missing_time", "missing_reminder"),
    "cancel_alarm": ("done", "no_alarms", "not_found", "ambiguous"),
    "cancel_reminder": ("done", "no_reminders", "not_found", "ambiguous"),
    "alarm_status": ("done", "no_alarms"),
    "reminder_status": ("done", "no_reminders"),
}


def _examples() -> list[tuple[dict[str, Any], list[Event], list[Event]]]:
    now = _EXAMPLE_NOW
    seven = Event("1", now.replace(hour=7, minute=0) + timedelta(days=1), "Alarm", "alarm")
    weekday = Event("2", now.replace(hour=6, minute=30) + timedelta(days=1), "Alarm", "alarm",
                    rrule="FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR")
    call = Event("3", now.replace(hour=17, minute=0), "Call mom", "reminder")
    bins = Event("4", now.replace(hour=19, minute=0) + timedelta(days=6), "Take out the bins", "reminder")
    return [
        ({"time": "7:30 tomorrow", "reminder": "to call mom at 5 pm"}, [seven], [call]),
        ({"reminder": "to call mom"}, [], [call, bins]),
        ({"reminder": "to feed the cat"}, [], [call, bins]),
        ({"time": "", "reminder": "to call mom tomorrow"}, [seven, weekday], [call]),
        ({"time": "8", "reminder": "to"}, [seven, weekday], []),
        ({"reminder": "to call mom"}, [], []),
        ({}, [], []),
        ({"time": "7"}, [seven, weekday], [call]),
        ({}, [seven], []),
        ({}, [], [call, bins]),
    ]


_EXAMPLE_NOW = datetime(2026, 1, 1, 14, 0, tzinfo=ZoneInfo("UTC"))


def outcome_examples() -> dict[str, dict[str, str]]:
    """operation -> outcome -> what is said, for the UI; empty in a language
    without a calendar module."""
    if words() is None:
        return {op: dict.fromkeys(outcomes, "") for op, outcomes in OUTCOMES.items()}
    out: dict[str, dict[str, str]] = {}
    for operation, outcomes in OUTCOMES.items():
        said: dict[str, str] = {}
        for slots, alarms, reminders in _examples():
            result = plan(operation, slots, _EXAMPLE_NOW, alarms, reminders, todo=True)
            said.setdefault(result.outcome, result.speech)
        out[operation] = {outcome: said.get(outcome, "") for outcome in outcomes}
    return out


def level_examples() -> dict[str, dict[str, str]]:
    """operation -> level -> what done says, for the UI."""
    if words() is None:
        return {}
    out: dict[str, dict[str, str]] = {}
    for operation in OUTCOMES:
        for slots, alarms, reminders in _examples():
            result = plan(operation, slots, _EXAMPLE_NOW, alarms, reminders, todo=True)
            if result.outcome == "done" and result.levels:
                out[operation] = result.levels
                break
    return out
