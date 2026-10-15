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
