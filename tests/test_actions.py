"""Running a command: which response is said for how it went."""

from __future__ import annotations

import asyncio
from typing import Any

from voice_call import actions
from voice_call.commands import parse_command
from voice_call.engine import Decision, Satellite
from voice_call.hass import HassError

from .fake_hass import FakeHass

SATELLITE = Satellite("aabbccddeeff", "Kitchen", "kitchen", "Kitchen", timer_list="voice_timer_list.k_timers")
TEA = {"timer_id": "3", "name": "tea", "status": "paused", "created_duration": 240, "remaining": 90}


class StubHass:
    """Just the calls actions.run makes; templates are rendered with jinja2."""

    def __init__(self) -> None:
        self.fake = FakeHass()
        self.fail = False
        self.script_response: Any = None
        self.services: list[tuple[str, dict]] = []
        self.events: list[dict] = []

    async def execute_script(self, sequence, variables=None):
        if self.fail:
            raise HassError("Action light.turn_on not found")
        return self.script_response

    async def render_template(self, template, variables=None):
        return self.fake.render(template, variables or {})

    async def call_service(self, domain, service, data, target, return_response=False):
        if self.fail:
            raise HassError("Timer not found")
        self.services.append((service, data))
        return {target["entity_id"]: {"timer_id": "9"}} if return_response else None

    async def fire_event(self, event_type, data):
        self.events.append(data)


class StubTimers:
    ready = True

    def __init__(self, running):
        self.running = running

    async def fresh_timers(self, key):
        return self.running


def run(spec, hass, slots=None, integration=None, dry_run=False):
    command = parse_command("c", {"description": "do it", "examples": ["do it"], **spec})
    decision = Decision({"transcript": "do it"}, command, command.behavior, "command", slots=slots or {})
    return asyncio.run(actions.run(hass, decision, SATELLITE, None, dry_run, integration))


PAUSE = {"timer": "pause", "response": {"done": "Paused.", "no_timers": "Nothing to pause.", "already": "{{ result.speech }} Say resume."}}


def test_timer_responses_by_outcome():
    hass = StubHass()
    done = run(PAUSE, hass, integration=StubTimers([{**TEA, "status": "active"}]))
    assert (done.name, done.speech) == ("done", "Paused.")
    assert hass.services == [("pause_timer", {"timer_id": "3"})]

    nothing = run(PAUSE, hass, integration=StubTimers([]))
    assert (nothing.name, nothing.speech) == ("no_timers", "Nothing to pause.")
    already = run(PAUSE, hass, integration=StubTimers([TEA]))
    assert already.speech == "The tea timer is already paused. Say resume."
    # No response for this outcome: the built-in sentence.
    missing = run(PAUSE, hass, {"timer": "lasagna"}, integration=StubTimers([TEA]))
    assert (missing.name, missing.speech) == ("not_found", "There's no lasagna timer. There's the tea timer.")
    assert len(hass.services) == 1  # only the one that was done called anything
    assert [e["outcome"] for e in hass.events] == ["done", "no_timers", "already", "not_found"]
    assert hass.events[0]["response"] == "Paused." and hass.events[0]["timer"]["name"] == "tea"


def test_a_plain_response_is_only_for_done():
    hass = StubHass()
    outcome = run({"timer": "pause", "response": "Paused."}, hass, integration=StubTimers([]))
    assert outcome.speech == "There are no timers."


def test_failed():
    hass = StubHass()
    hass.fail = True
    spec = {"action": {"action": "light.turn_on"}, "response": {"done": "On.", "failed": "No luck: {{ result.error }}"}}
    outcome = run(spec, hass)
    assert (outcome.name, outcome.speech) == ("failed", "No luck: Action light.turn_on not found")
    assert outcome.error == "Action light.turn_on not found"
    assert hass.events[-1]["outcome"] == "failed"
    # Without a failed response: the built-in one.
    assert run({"action": {"action": "light.turn_on"}, "response": "On."}, hass).speech == actions.FAILED
    # A timer service refused, and timers without the integration.
    outcome = run({**PAUSE, "response": {"failed": "Timers are broken."}}, hass, integration=StubTimers([{**TEA, "status": "active"}]))
    assert (outcome.name, outcome.speech) == ("failed", "Timers are broken.")
    outcome = run({"timer": "pause"}, StubHass())
    assert (outcome.name, outcome.speech) == ("failed", actions.TIMERS_NEED_INTEGRATION)


def test_a_script_picks_its_outcome():
    hass = StubHass()
    spec = {"script": "script.check", "response": {"done": "All good.", "open": "The {{ result.door }} is open."}}
    hass.script_response = {"outcome": "open", "door": "garage door"}
    outcome = run(spec, hass)
    assert (outcome.name, outcome.speech) == ("open", "The garage door is open.")
    # An outcome without a response says the script's speech, or nothing.
    hass.script_response = {"outcome": "closed", "speech": "Everything is shut."}
    assert run(spec, hass).speech == "Everything is shut."
    hass.script_response = {"outcome": "closed"}
    assert run(spec, hass).speech == ""
    hass.script_response = {}
    outcome = run(spec, hass)
    assert (outcome.name, outcome.speech) == ("done", "All good.")


def test_a_dry_run_says_what_would_be_said():
    hass = StubHass()
    outcome = run(PAUSE, hass, integration=StubTimers([]), dry_run=True)
    assert (outcome.name, outcome.speech) == ("no_timers", "Nothing to pause.")
    assert not hass.events and not hass.services


LIGHTS = {
    "action": {"action": "light.turn_on", "target": "{{ target }}"},
    "verbosity": "beep",
    "response": {"done": {"brief": "Turned on the lights.", "specific": "Turned on the lights {{ place.said }}."}},
}


class StubCatalog:
    """Areas, floors and where devices are, as Home Assistant's catalog has them."""

    def __init__(self) -> None:
        from voice_call.hass import Area, Entity, Floor  # pylint: disable=import-outside-toplevel

        self.areas = {
            "kitchen": Area("kitchen", "Kitchen", floor_id="ground"),
            "office": Area("office", "Office", floor_id="first"),
            "den": Area("den", "Den", floor_id="den_floor"),
        }
        self.floors = {"ground": Floor("ground", "Ground Floor"), "first": Floor("first", "Upstairs"), "den_floor": Floor("den_floor", "Basement")}
        self.entities = {
            "switch.kettle": Entity("switch.kettle", "Kettle", area_id="kitchen"),
            "fan.desk": Entity("fan.desk", "Desk Fan", area_id="office"),
            "switch.porch": Entity("switch.porch", "Porch"),
        }


def say(spec, slots=None, elsewhere=actions.BEEP_ELSEWHERE, hass=None, integration=None):
    hass = hass or StubHass()
    command = parse_command("c", {"description": "do it", "examples": ["do it"], **spec})
    decision = Decision({"transcript": "do it"}, command, command.behavior, "command", slots=slots or {})
    return asyncio.run(actions.run(hass, decision, SATELLITE, None, False, integration, None, elsewhere))


def test_a_beep_is_for_what_happens_here():
    here = say(LIGHTS)
    assert here.speech == "" and here.verbosity["level"] == "beep" and here.verbosity["place"]["here"]
    named_here = say(LIGHTS, {"area": ["kitchen"]})
    assert named_here.speech == ""
    # Out of sight: the elsewhere level (specific unless set), or a beep anyway.
    office = say(LIGHTS, {"area": ["office"]})
    assert office.speech == "Turned on the lights in the Office."
    assert office.verbosity == {"level": "specific", "why": "it acted out of the satellite's area",
                                "place": {"here": False, "said": "in the Office", "areas": ["office"]}}
    assert say(LIGHTS, {"area": ["office"]}, elsewhere="brief").speech == "Turned on the lights."
    assert say(LIGHTS, {"area": ["office"]}, elsewhere="beep").speech == ""
    # What went wrong is always said.
    hass = StubHass()
    hass.fail = True
    assert say(LIGHTS, hass=hass).speech == actions.FAILED


def test_the_closest_written_level_is_said():
    spec = {**LIGHTS, "verbosity": "verbose"}
    assert say(spec).speech == "Turned on the lights in the Kitchen."  # no verbose one: specific
    only_brief = {**LIGHTS, "verbosity": "verbose", "response": {"done": {"brief": "Done."}}}
    assert say(only_brief).speech == "Done."
    only_verbose = {**LIGHTS, "verbosity": 2, "response": {"done": {"verbose": "All of them."}}}
    assert say(only_verbose).speech == "All of them."  # nothing less: the next more
    # A plain response is for every level; without a level, it is said as before.
    assert say({**LIGHTS, "verbosity": "brief", "response": "On."}).speech == "On."
    assert say({"action": {"action": "light.turn_on"}, "response": "On."}).speech == "On."
    assert say({"action": {"action": "light.turn_on"}}).speech == ""  # nothing to say: a beep


def test_a_question_always_answers():
    outcome = say({"verbosity": "beep", "response": "It's late."})
    assert outcome.speech == "It's late." and outcome.verbosity["why"] == "a question always answers"
    assert say({"verbosity": "silent", "response": "It's late."}).speech == "It's late."


def test_silent_says_nothing_not_even_a_beep():
    nothing = say({})  # "never mind": nothing to do, silent by default
    assert nothing.speech == "" and nothing.silent and nothing.verbosity["level"] == "silent"
    quiet = say({**LIGHTS, "verbosity": "silent"}, {"area": ["office"]})
    assert quiet.silent and quiet.speech == ""  # silent elsewhere too: only a beep is said there
    beep = say({"action": {"action": "light.turn_on"}})
    assert beep.speech == "" and not beep.silent


def test_where_it_acted():
    catalog = StubCatalog()
    place = lambda slots: actions.place(slots, SATELLITE, catalog)  # noqa: E731
    assert place({}) == {"here": True, "said": "in the Kitchen", "areas": ["kitchen"]}
    assert place({"entity": ["switch.kettle"]}) == {"here": True, "said": "in the Kitchen", "areas": ["kitchen"]}
    assert place({"entity": ["fan.desk"]})["said"] == "in the Office" and not place({"entity": ["fan.desk"]})["here"]
    # A device in no area could be anywhere: not here.
    assert place({"entity": ["switch.porch"]}) == {"here": False, "said": "", "areas": []}
    assert place({"floor": ["first"]}) == {"here": False, "said": "upstairs", "areas": ["office"]}
    assert place({"floor": ["ground"]}) == {"here": True, "said": "on the Ground Floor", "areas": ["kitchen"]}
    assert place({"floor": ["den_floor"]})["said"] == "in the Basement"
    assert place({"area": ["kitchen", "office"]})["said"] == "in the Kitchen and in the Office"
    assert actions.place({"area": ["office"]}, SATELLITE)["said"] == "in the Office"  # no catalog
    assert actions._in_area("Sam's Room") == "in Sam's Room"  # pylint: disable=protected-access


def test_timers_say_as_much_as_their_level():
    running = StubTimers([])
    start = {"timer": "start"}
    slots = {"duration": {"total_seconds": 300}}
    assert say(start, slots, integration=running).speech == "The 5 minute timer started."
    assert say({**start, "verbosity": "brief"}, slots, integration=running).speech == "Timer started."
    assert say({**start, "verbosity": "verbose"}, slots, integration=running).speech.startswith(
        "The 5 minute timer started, finishing at "
    )
    assert say({**start, "verbosity": "beep"}, slots, integration=running).speech == ""
    # A response of its own for one level; the built-in sentence for the rest.
    mine = {**start, "verbosity": "brief", "response": {"done": {"verbose": "Off it goes."}}}
    assert say(mine, slots, integration=running).speech == "Off it goes."
    # A question: a status answers at any level.
    status = {"timer": "status", "verbosity": "beep"}
    assert say(status, {}, integration=StubTimers([TEA])).speech == "The tea timer is paused with 1 minute and 30 seconds left."
