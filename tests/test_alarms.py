"""Alarms and timed reminders on the calendar: what is read from what was
said, and what each command does (voice_call/alarms.py, and the English
calendar words, voice_call/languages/en/calendar.py)."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from voice_call import alarms, languages

TZ = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 10, 21, 14, 0, tzinfo=TZ)  # a Wednesday afternoon


@pytest.fixture(autouse=True)
def english():
    languages.use("en")


def calendar():
    return languages.current().calendar


@pytest.mark.parametrize(
    ("said", "alarm", "start", "rrule", "rest"),
    [
        ("for 7 am tomorrow", True, "2026-10-22 07:00", None, ""),
        ("for seven thirty", True, "2026-10-21 19:30", None, ""),  # the next 7:30 to come round
        ("for half past six tomorrow morning", True, "2026-10-22 06:30", None, ""),
        ("for 6 on saturday", True, "2026-10-24 06:00", None, ""),  # another day: the morning
        ("for every weekday at 6:45", True, "2026-10-22 06:45", "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR", ""),
        ("in 20 minutes", True, "2026-10-21 14:20", None, ""),
        ("at 5 pm to call my mom", False, "2026-10-21 17:00", None, "to call my mom"),
        ("to call my mom at 5", False, "2026-10-21 17:00", None, "to call my mom"),
        ("that the plumber comes at 3 on friday", False, "2026-10-23 15:00", None, "that the plumber comes"),
        ("to take out the trash every tuesday at 7 pm", False, "2026-10-27 19:00", "FREQ=WEEKLY;BYDAY=TU",
         "to take out the trash"),
        ("in 10 minutes to check the oven", False, "2026-10-21 14:10", None, "to check the oven"),
    ],
)
def test_when(said, alarm, start, rrule, rest):
    found = calendar().when(said, NOW, alarm=alarm)
    assert found is not None
    assert found.start == datetime.fromisoformat(start).replace(tzinfo=TZ)
    assert found.rrule == rrule and found.rest.strip() == rest


@pytest.mark.parametrize(
    ("said", "alarm", "start", "rest"),
    [
        # A length alone is from now: speech-to-text, or the free text's edge, drops the "in".
        ("8 hours", True, "2026-10-21 22:00", ""),
        ("10 minutes to check the oven", False, "2026-10-21 14:10", "to check the oven"),
        # "tomorrow" split by speech-to-text, its "to" taken for the reminder's.
        ("seven pm to morrow", True, "2026-10-22 19:00", ""),
        ("morrow at nine to call the dentist", False, "2026-10-22 09:00", "to call the dentist"),
        ("to night at 10", True, "2026-10-21 22:00", ""),
        # ... but not "to night" that is no day.
        ("to go to night school at 7", False, "2026-10-21 19:00", "to go to night school"),
        # Speech-to-text's "seven hundred thirty".
        ("for seven hundred thirty", True, "2026-10-21 19:30", ""),
        # A length that is the reminder's, before its time.
        ("to rest for 10 minutes at 5 pm", False, "2026-10-21 17:00", "to rest for 10 minutes"),
    ],
)
def test_what_speech_to_text_makes_of_it(said, alarm, start, rest):
    found = calendar().when(said, NOW, alarm=alarm)
    assert found is not None
    assert found.start == datetime.fromisoformat(start).replace(tzinfo=TZ) and found.rest == rest


def test_an_hours_leading_zero():
    reading = languages.current().reading
    assert reading.clock("oh seven hundred") == (7, 0, None)
    assert reading.clock("zero seven thirty") == (7, 30, None)
    assert reading.clock("seven oh five") == (7, 5, None)


def test_no_time_is_none():
    assert calendar().when("to call mom", NOW) is None
    assert calendar().when("", NOW, alarm=True) is None


def test_what_is_said_back():
    assert calendar().what("to call my mom") == "call your mom"
    assert calendar().what("that me and my sister are out") == "you and your sister are out"


def _event(uid, hour, minute=0, days=1, kind="alarm", rrule=None, summary="Alarm"):
    start = NOW.replace(hour=hour, minute=minute) + timedelta(days=days)
    return alarms.Event(uid, start, summary, kind, rrule=rrule)


def test_alarm():
    plan = alarms.plan("alarm", {"time": "7:30 tomorrow"}, NOW, [], [])
    assert plan.outcome == "done" and plan.speech == "Alarm set for 7:30 AM tomorrow."
    assert plan.create == {"kind": "alarm", "summary": "Alarm", "start": NOW.replace(hour=7, minute=30) + timedelta(days=1),
                           "rrule": None}
    assert alarms.plan("alarm", {}, NOW, [], []).outcome == "missing_time"


def test_reminder_with_or_without_a_time():
    plan = alarms.plan("reminder", {"reminder": "at 5 pm to call my mom"}, NOW, [], [], todo=True)
    assert plan.outcome == "done" and plan.create["summary"] == "Call your mom"
    # Without a time: on the to-do list, if there is one; else it asks when.
    listed = alarms.plan("reminder", {"reminder": "to buy milk"}, NOW, [], [], todo=True)
    assert listed.outcome == "listed" and listed.todo == "buy milk" and listed.create is None
    assert alarms.plan("reminder", {"reminder": "to buy milk"}, NOW, [], []).outcome == "missing_time"
    # A day but no time asks for one rather than listing it.
    assert alarms.plan("reminder", {"reminder": "to buy milk tomorrow"}, NOW, [], [], todo=True).outcome == "missing_time"
    assert alarms.plan("reminder", {"reminder": "at 5"}, NOW, [], [], todo=True).outcome == "missing_reminder"


def test_cancel_picks_the_alarm():
    seven, seven_pm = _event("1", 7), _event("2", 19)
    weekdays = _event("3", 6, 30, rrule="FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR")
    # Without am or pm, either.
    plan = alarms.plan("cancel_alarm", {"time": "7"}, NOW, [seven, weekdays], [])
    assert plan.outcome == "done" and plan.delete == [seven]
    assert alarms.plan("cancel_alarm", {"time": "7"}, NOW, [seven, seven_pm], []).outcome == "ambiguous"
    assert alarms.plan("cancel_alarm", {"time": "7 pm"}, NOW, [seven, seven_pm], []).delete == [seven_pm]
    assert alarms.plan("cancel_alarm", {"time": "8"}, NOW, [seven], []).outcome == "not_found"
    assert alarms.plan("cancel_alarm", {}, NOW, [seven, weekdays], []).outcome == "ambiguous"
    assert alarms.plan("cancel_alarm", {}, NOW, [weekdays], []).delete == [weekdays]
    assert alarms.plan("cancel_alarm", {}, NOW, [], []).outcome == "no_alarms"


def test_status():
    plan = alarms.plan("alarm_status", {}, NOW, [_event("1", 7)], [])
    assert plan.speech == "You have an alarm at 7:00 AM tomorrow."
    assert alarms.plan("alarm_status", {}, NOW, [], []).speech == "No alarms are set."
    call = _event("2", 17, days=0, kind="reminder", summary="Call mom")
    assert "Call mom" in alarms.plan("reminder_status", {}, NOW, [], [call]).speech
    assert alarms.plan("reminder_status", {}, NOW, [], []).outcome == "no_reminders"


def test_events_from_home_assistant():
    raw = {"uid": "u1", "summary": "Alarm", "start": "2026-10-22T07:00:00+02:00", "end": "2026-10-22T07:05:00+02:00",
           "description": "Set by voice on Kitchen. [voice_call:alarm:aabbccddeeff]"}
    event = alarms.parse_event(raw, TZ)
    assert event.kind == "alarm" and event.satellite == "aabbccddeeff" and event.start.hour == 7
    # Added in Home Assistant: a reminder for every satellite.
    added = alarms.parse_event({**raw, "description": None, "summary": "Water the plants"}, TZ)
    assert added.kind == "reminder" and added.satellite is None
    # All day: nothing to go off at.
    assert alarms.parse_event({**raw, "start": "2026-10-22", "end": "2026-10-23"}, TZ) is None
    # Over REST, times are objects.
    rest = alarms.parse_event({**raw, "start": {"dateTime": raw["start"]}, "end": {"dateTime": raw["end"]}}, TZ)
    assert rest.start == event.start
    assert alarms.parse_event({**raw, "start": {"date": "2026-10-22"}, "end": {"date": "2026-10-23"}}, TZ) is None


def test_upcoming_is_each_once_and_the_satellites_own():
    cal = alarms.AlarmCalendar.__new__(alarms.AlarmCalendar)
    cal.tz = TZ
    soon = datetime.now(TZ) + timedelta(hours=1)
    cal.events = [
        alarms.Event("a", soon, "Alarm", "alarm", "kitchen", rrule="FREQ=DAILY", recurrence_id="1"),
        alarms.Event("a", soon + timedelta(days=1), "Alarm", "alarm", "kitchen", rrule="FREQ=DAILY", recurrence_id="2"),
        alarms.Event("b", soon, "Alarm", "alarm", "bedroom"),
        alarms.Event("c", soon - timedelta(hours=2), "Alarm", "alarm", "kitchen"),
    ]
    assert [e.uid for e in cal.upcoming("alarm", "kitchen")] == ["a"]
    assert [e.uid for e in cal.upcoming("alarm")] == ["a", "b"]
