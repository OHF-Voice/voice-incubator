"""Timer commands: which timer is meant, and what is said."""

import copy

import pytest

from adapt_ctc.config import parse_config
from voice_call import timers
from voice_call.engine import Engine
from voice_call.store import compile_store

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
        "The pizza timer started.",
    )
    assert timers.plan("start", {}, []).speech == "How long should the timer be?"
    assert timers.plan("start", {"duration": duration(90)}, []).speech == "The 1 minute 30 second timer started."


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
    assert plan.data == {"timer_id": "2"} and plan.speech == "The 5 minute timer cancelled."


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
    assert timers._spoken_duration(3725) == "1 hour and 2 minutes"
    assert timers._spoken_duration(1) == "1 second"
    assert timers._spoken_duration(0) == "0 seconds"


COMMANDS = {
    "cancel_timer": {
        "description": "cancel a timer",
        "require": [["cancel"], ["timer"]],
        "lists": {"timer_name": {"list": "running_timers", "optional": True}},
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
    # Nothing running: the optional slot is dropped, the command that needs it too.
    assert "running_timers" not in config["lists"]
    assert "lists" not in config["intents"]["cancel_timer"] or not config["intents"]["cancel_timer"]["lists"]
    assert "named_only" not in config["intents"]
    parse_config(config)

    engine.dynamic["running_timers"] = ["lasagna", "tea", "!!"]
    config = copy.deepcopy(compiled.config)
    engine._fill_dynamic(config)
    assert config["lists"]["running_timers"] == {"lasagna": ["lasagna"], "tea": ["tea"]}
    assert set(config["intents"]) == {"cancel_timer", "named_only"}
    parse_config(config)


def test_dynamic_list_names_are_reserved():
    from adapt_ctc.config import ConfigError

    with pytest.raises(ConfigError, match="kept by the app"):
        compile_store({"lists": {"running_timers": ["a"]}, "commands": copy.deepcopy(COMMANDS)})
