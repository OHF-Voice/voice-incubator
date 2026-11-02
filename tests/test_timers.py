"""Timer commands: which timer is meant, and what is said."""

import copy
from datetime import datetime

import pytest

from adapt_ctc.config import parse_config
from voice_labs import timers
from voice_labs.engine import Engine
from voice_labs.languages.en import reading as english_reading
from voice_labs.languages.en import speech as english
from voice_labs.store import compile_store

RUNNING = [
    {"timer_id": "1", "name": "lasagna", "status": "active", "created_duration": 1800, "remaining": 754},
    {"timer_id": "2", "name": None, "status": "active", "created_duration": 300, "remaining": 61},
    {"timer_id": "3", "name": "tea", "status": "paused", "created_duration": 240, "remaining": 90},
]


def duration(seconds):
    return {"total_seconds": seconds}


def test_start():
    plan = timers.plan("start", {"duration": duration(300), "timer": "Pizza"}, [])
    assert (plan.service, plan.data, plan.speech) == (
        "create_timer",
        {"duration": 300, "name": "pizza"},
        "Started the pizza timer for 5 minutes.",
    )
    assert timers.plan("start", {}, []).speech == "How long should the timer be?"
    assert timers.plan("start", {"duration": duration(90)}, []).speech == "Started a 1 minute 30 second timer."


@pytest.mark.parametrize(
    ("slots", "timer_id"),
    [
        ({"timer_name": "lasagna"}, "1"),  # recognized from the running timers
        ({"timer": "lasaga"}, "1"),  # free text, spelled as heard
        ({"duration": duration(300)}, "2"),
        ({"timer": "tea"}, "3"),
    ],
)
def test_cancel_picks_the_timer(slots, timer_id):
    plan = timers.plan("cancel", slots, RUNNING)
    assert plan.service == "cancel_timer" and plan.data == {"timer_id": timer_id}


def test_one_running_timer_needs_no_name():
    plan = timers.plan("cancel", {}, RUNNING[1:2])
    assert plan.data == {"timer_id": "2"} and plan.speech == "Cancelled the 5 minute timer."


def test_several_running_timers_are_ambiguous():
    plan = timers.plan("cancel", {}, RUNNING)
    assert plan.service is None
    assert plan.speech == "Which timer? There's the lasagna timer, the 5 minute timer and the tea timer."


def test_no_such_timer():
    plan = timers.plan("cancel", {"timer": "pizza"}, RUNNING)
    assert (plan.service, plan.outcome) == (None, "not_found")
    assert plan.speech.startswith("There's no pizza timer. There's the lasagna timer")
    plan = timers.plan("cancel", {"duration": duration(120)}, RUNNING)
    assert plan.speech.startswith("There's no 2 minute timer.")
    plan = timers.plan("cancel", {}, [])
    assert (plan.outcome, plan.speech) == ("no_timers", "There are no timers.")


def test_a_name_wins_over_a_length_that_does_not_fit():
    plan = timers.plan("cancel", {"timer_name": "lasagna", "duration": duration(600)}, RUNNING)
    assert plan.data == {"timer_id": "1"}


def test_pause_and_resume_only_apply_to_their_state():
    assert timers.plan("resume", {}, RUNNING).data == {"timer_id": "3"}  # the only paused one
    plan = timers.plan("pause", {"timer_name": "tea"}, RUNNING)
    assert (plan.service, plan.outcome, plan.speech) == (None, "already", "The tea timer is already paused.")
    assert plan.timer["timer_id"] == "3"
    plan = timers.plan("resume", {"timer": "lasagna"}, RUNNING)
    assert (plan.outcome, plan.speech) == ("already", "The lasagna timer is already running.")
    plan = timers.plan("pause", {}, RUNNING[2:])
    assert (plan.outcome, plan.speech) == ("already", "The tea timer is already paused.")
    paused = [{**t, "status": "paused"} for t in RUNNING]
    assert timers.plan("pause", {}, paused).speech == "All the timers are already paused."


def test_outcomes():
    assert timers.plan("start", {}, []).outcome == "missing_duration"
    assert timers.plan("start", {"duration": duration(60)}, []).outcome == "done"
    assert timers.plan("cancel", {}, RUNNING).outcome == "ambiguous"
    assert timers.plan("add", {"timer_name": "tea"}, RUNNING).outcome == "missing_duration"
    assert timers.plan("cancel_all", {}, []).outcome == "no_timers"
    assert timers.plan("status", {}, RUNNING).outcome == "done"
    assert timers.plan("status", {"timer": "pizza"}, RUNNING).outcome == "not_found"


def test_every_outcome_has_an_example():
    examples = timers.outcome_examples()
    assert set(examples) == set(timers.OPERATIONS)
    for operation, outcomes in timers.OUTCOMES.items():
        assert tuple(examples[operation]) == outcomes
    assert examples["pause"]["already"] == "The tea timer is already paused."


def test_add_and_subtract_time():
    plan = timers.plan("add", {"timer_name": "tea", "duration": duration(120)}, RUNNING)
    assert plan.service == "add_time" and plan.data == {"timer_id": "3", "duration": 120}
    assert plan.speech == "Added 2 minutes to the tea timer."
    plan = timers.plan("subtract", {"timer_name": "lasagna", "duration": duration(60)}, RUNNING)
    assert plan.speech == "Took 1 minute off the lasagna timer."
    assert timers.plan("add", {"timer_name": "tea"}, RUNNING).speech == "How much time?"


def test_status():
    plan = timers.plan("status", {}, RUNNING)
    assert plan.speech == (
        "12 minutes and 34 seconds left on the lasagna timer, 1 minute and 1 second left on "
        "the 5 minute timer and the tea timer is paused with 1 minute and 30 seconds left."
    )
    assert timers.plan("status", {"timer_name": "tea"}, RUNNING).speech == (
        "The tea timer is paused with 1 minute and 30 seconds left."
    )


def test_cancel_all():
    plan = timers.plan("cancel_all", {}, RUNNING)
    assert [c[1]["timer_id"] for c in plan.calls] == ["1", "2", "3"]
    assert plan.speech == "Cancelled 3 timers."


def test_stop_over_a_ringing_alarm():
    assert timers.stops_ringing("cancel", {})
    assert not timers.stops_ringing("cancel", {"timer_name": "tea"})
    assert not timers.stops_ringing("status", {})


def test_spoken_durations():
    assert english.duration(3725) == "1 hour and 2 minutes"
    assert english.duration(1) == "1 second"
    assert english.duration(0) == "0 seconds"


COMMANDS = {
    "cancel_timer": {
        "description": "cancel a timer",
        "require": [["cancel"], ["timer"]],
        "lists": {"timer_name": {"list": "running_timers"}},
        "combinations": [[], ["timer_name"]],
        "timer": "cancel",
    },
    "named_only": {
        "description": "cancel a named timer",
        "require": [["stop"], ["timer"]],
        "lists": {"timer_name": "running_timers"},
        "timer": "cancel",
    },
}


class FakeEngine(Engine):
    """Just the dynamic list handling, without models."""

    def __init__(self):  # pylint: disable=super-init-not-called
        self.dynamic = {"running_timers": []}


def test_running_timers_list_fills_in_or_drops_out():
    compiled = compile_store({"commands": copy.deepcopy(COMMANDS)})
    engine = FakeEngine()

    config = copy.deepcopy(compiled.config)
    engine._fill_dynamic(config)
    # Nothing running: what hears a timer's name is dropped.
    assert "running_timers" not in config["lists"]
    assert set(config["intents"]) == {"cancel_timer"}
    parse_config(config)

    engine.dynamic["running_timers"] = ["lasagna", "tea", "!!"]
    config = copy.deepcopy(compiled.config)
    engine._fill_dynamic(config)
    assert config["lists"]["running_timers"] == {"lasagna": ["lasagna"], "tea": ["tea"]}
    assert set(config["intents"]) == {"cancel_timer", "cancel_timer:timer_name", "named_only"}
    parse_config(config)


def test_dynamic_list_names_are_reserved():
    from adapt_ctc.config import ConfigError

    with pytest.raises(ConfigError, match="kept by the app"):
        compile_store({"lists": {"running_timers": ["a"]}, "commands": copy.deepcopy(COMMANDS)})


def test_the_ring_shows_every_timer_soonest_on_top():
    from datetime import UTC, datetime, timedelta

    now = datetime(2026, 10, 15, 20, 0, tzinfo=UTC)
    soon = {"timer_id": "a", "status": "active", "total_duration": 600, "finishes_at": (now + timedelta(seconds=90)).isoformat()}
    later = {"timer_id": "b", "status": "active", "total_duration": 300, "finishes_at": (now + timedelta(seconds=200)).isoformat()}
    paused = {"timer_id": "c", "status": "paused", "total_duration": 60, "remaining": 5}
    kept: dict[str, int] = {}
    assert timers.colors([soon, later, paused], kept) == {"a": 0, "b": 1, "c": 2}
    # The soonest, for firmware from before; then each, soonest first, on the
    # longest's scale. Paused: not shown (but it keeps its color).
    assert timers.progress([later, soon, paused], kept, now) == {
        "shown": 1, "remaining": 90.0, "total": 600.0, "paused": 0,
        "count": 2, "scale": 600.0, "left0": 90.0, "color0": -1, "left1": 200.0, "color1": timers.COLORS[1],
    }
    assert timers.progress([paused], kept, now) == {"shown": 0}
    assert timers.progress([], now=now) == {"shown": 0}
    # Without an end time (a fake), what was left when it was reported.
    assert timers.progress([{"status": "active", "created_duration": 15, "remaining": 15}], now=now)["remaining"] == 15.0


def test_each_timer_keeps_its_color():
    def timer(timer_id):
        return {"timer_id": timer_id, "status": "active"}

    kept: dict[str, int] = {}
    timers.colors([timer("a"), timer("b")], kept)
    # The first gone: the next new one takes its color, the free one first.
    assert timers.colors([timer("b"), timer("c")], kept) == {"b": 1, "c": 0}
    # Every color taken: round again.
    many = [timer(str(i)) for i in range(len(timers.COLORS) + 2)]
    assert [timers.colors(many, {})[str(i)] for i in range(len(many))] == [*range(len(timers.COLORS)), 0, 1]


def test_the_ring_is_told_only_what_changed():
    last = {"shown": 1, "remaining": 90.0, "total": 600.0, "paused": 0, "count": 1, "scale": 600.0, "left0": 90.0, "color0": -1}
    assert timers.same_progress(last, {**last, "remaining": 80.2, "left0": 80.2}, 10.0)
    assert not timers.same_progress(last, {**last, "remaining": 70.0, "left0": 70.0}, 10.0)  # changed by 10 s more
    assert not timers.same_progress(last, {**last, "color0": timers.COLORS[1]}, 0.0)
    assert not timers.same_progress(last, {"shown": 0}, 0.0)


@pytest.mark.parametrize(
    ("said", "clock"),
    [
        ("seven thirty", (7, 30, None)),
        ("7:30 am", (7, 30, "am")),
        ("6 pm", (6, 0, "pm")),
        ("half past six", (6, 30, None)),
        ("quarter to eight in the morning", (7, 45, "am")),
        ("ten past nine at night", (9, 10, "pm")),
        ("twenty to seven", (6, 40, None)),
        ("seven oh five", (7, 5, None)),
        ("19:45", (19, 45, None)),
        ("830", (8, 30, None)),
        ("noon", (12, 0, "pm")),
        ("midnight", (0, 0, "am")),
        ("whenever", None),
    ],
)
def test_clock_times(said, clock):
    assert english_reading.clock(said) == clock


def test_alarms_ring_at_the_next_time_it_comes_round():
    evening = datetime(2026, 10, 15, 22, 0)
    plan = timers.plan("alarm", {"time": "seven"}, [], evening)
    assert plan.outcome == "done" and plan.service == "create_timer"
    assert plan.data == {"duration": 9 * 3600, "name": "alarm"} and plan.speech == "Set an alarm for 7:00 AM."
    assert timers.plan("alarm", {"time": "7 pm"}, [], evening).data["duration"] == 21 * 3600
    assert timers.plan("alarm", {"time": "noon"}, [], evening).speech == "Set an alarm for noon."
    morning = datetime(2026, 10, 15, 6, 0)
    assert timers.plan("alarm", {"time": "seven"}, [], morning).data["duration"] == 3600
    assert timers.plan("alarm", {"time": "6:30", "timer": "school"}, [], morning).data == {"duration": 1800, "name": "school"}
    missing = timers.plan("alarm", {}, [], evening)
    assert missing.outcome == "missing_time" and missing.service is None
