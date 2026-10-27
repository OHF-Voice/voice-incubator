"""Running a command: which response is said for how it went."""

from __future__ import annotations

import asyncio
from typing import Any

from voice_call import actions
from voice_call.commands import parse_command
from voice_call.engine import Decision, Satellite
from voice_call.hass import HassError
from voice_call.languages.en import speech as english

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

    async def handle_intent(self, name, slots, variables, language, device_id=None, text=None, exposed_only=True):
        if self.fail:
            raise HassError("Connection lost")
        return self.fake.handle_intent(name, slots, variables, device_id=device_id, exposed_only=exposed_only)


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
    assert run({"action": {"action": "light.turn_on"}, "response": "On."}, hass).speech == english.FAILED
    # A timer service refused, and timers without the integration.
    outcome = run({**PAUSE, "response": {"failed": "Timers are broken."}}, hass, integration=StubTimers([{**TEA, "status": "active"}]))
    assert (outcome.name, outcome.speech) == ("failed", "Timers are broken.")
    outcome = run({"timer": "pause"}, StubHass())
    assert (outcome.name, outcome.speech) == ("failed", english.TIMERS_NEED_INTEGRATION)
    # A script that returned text: what it should return instead.
    class TextScript(StubHass):
        async def execute_script(self, sequence, variables=None):
            raise HassError("home_assistant_error: Failed to process the returned action response data, "
                            "expected a dictionary, but got <class 'str'>")

    texted = run({"script": "script.good_night"}, TextScript())
    assert texted.name == "failed" and texted.error.endswith("(a script returns a mapping: {speech: ...}, not text)")


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
    assert say(LIGHTS, hass=hass).speech == english.FAILED


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
    assert english.place([("area", "Sam's Room")]) == "in Sam's Room"


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


LIGHTS_ON = {"intent": {"name": "HassTurnOn", "slots": {"domain": ["light"]}}, "response": {"done": {
    "specific": "Turned on the lights {{ place.said }}.", "verbose": "Turned on {{ result.names | join(', ') }}."}}}


def test_an_intent_acts_on_what_is_exposed_here():
    hass = StubHass()
    done = run(LIGHTS_ON, hass)
    assert (done.name, done.speech) == ("done", "Turned on the lights in the Kitchen.")
    # The satellite's area, as Assist's: by id, and preferred for names.
    assert hass.fake.intents[-1]["slots"] == {"domain": ["light"], "area": "kitchen", "preferred_area_id": "kitchen"}
    assert done.to_json()["sequence"] == [{"intent": "HassTurnOn", "slots": hass.fake.intents[-1]["slots"]}]
    verbose = run({**LIGHTS_ON, "verbosity": "verbose"}, hass)
    assert verbose.speech == "Turned on Kitchen Ceiling."

    # Why not, said for where.
    hass.fake.unexposed.add("light.kitchen_ceiling")
    hidden = run(LIGHTS_ON, hass)
    assert (hidden.name, hidden.speech) == ("not_exposed", "Nothing like that in the Kitchen is exposed to Assist.")
    hass.fake.unexposed.clear()
    hass.fake.states["light.kitchen_ceiling"] = ("unavailable", {})
    gone = run(LIGHTS_ON, hass)
    assert (gone.name, gone.speech) == ("unavailable", "Everything like that in the Kitchen is unavailable right now.")
    nothing = run(LIGHTS_ON, hass, {"area": ["living_room"]})
    assert (nothing.name, nothing.speech) == ("no_devices", "There's nothing like that in the Living Room.")


def test_an_intent_for_each_thing_named():
    hass = StubHass()
    # Each device by entity id -- a name is the Names page's: exposed to
    # Assist or not -- said as itself when it can't be.
    hass.fake.unexposed.add("light.desk_lamp")
    named = run({"intent": "HassTurnOn"}, hass, {"entity": ["light.desk_lamp"]})
    assert named.name == "done" and hass.fake.intents[-1]["exposed_only"] is False
    assert hass.fake.intents[-1]["slots"]["name"] == "light.desk_lamp"
    hass.fake.states["light.desk_lamp"] = ("unavailable", {})
    gone = run({"intent": "HassTurnOn"}, hass, {"entity": ["light.desk_lamp"]})
    assert (gone.name, gone.speech) == ("unavailable", "That's unavailable right now.")
    del hass.fake.states["light.desk_lamp"]
    hass.fake.unexposed.clear()
    # A floor: everything of the kind on it.
    upstairs = run(LIGHTS_ON, hass, {"floor": ["first"]})
    assert upstairs.name == "done" and hass.fake.intents[-1]["slots"]["floor"] == "first"
    # A slot from what was heard, rendered in Home Assistant.
    run({"intent": {"name": "HassLightSet", "slots": {"brightness": "{{ slots.percentage }}"}}}, hass, {"percentage": 40})
    assert hass.fake.intents[-1]["slots"]["brightness"] == "40"


def test_an_intent_with_no_area_here():
    hass = StubHass()
    nowhere = Satellite("s", "Nowhere")
    command = parse_command("c", {"description": "do it", "examples": ["do it"], **LIGHTS_ON})
    decision = Decision({"transcript": "do it"}, command, command.behavior, "command")
    outcome = asyncio.run(actions.run(hass, decision, nowhere, None, False))
    assert (outcome.name, outcome.speech) == ("no_area", english.NO_AREA) and not hass.fake.intents
    # The garage door from the kitchen: nothing there, so the whole home's.
    garage = run({"intent": {"name": "HassTurnOn", "slots": {"domain": ["cover"]}, "anywhere": True}}, hass)
    assert garage.name == "done" and [i["slots"].get("area") for i in hass.fake.intents] == ["kitchen", None]


def test_an_intent_dry_run_and_failure():
    hass = StubHass()
    dry = run(LIGHTS_ON, hass, dry_run=True)
    assert dry.name == "done" and dry.sequence and not hass.fake.intents
    hass.fail = True
    failed = run(LIGHTS_ON, hass)
    assert (failed.name, failed.speech) == ("failed", english.FAILED)
    hass.fail = False
    unknown = run({"intent": "HassNoSuchThing"}, hass)
    assert unknown.name == "failed" and "Unknown intent" in unknown.error


def test_intents_through_the_integration_or_rest():
    """The client: the Voice Call integration's command, else the REST API
    (why it matched nothing read from its message)."""
    from voice_call.hass import HomeAssistant  # pylint: disable=import-outside-toplevel

    from . import fake_hass  # pylint: disable=import-outside-toplevel

    async def go() -> None:
        fake, server = await fake_hass.serve()
        rest, rest_port = await fake_hass.serve_rest(fake)
        port = server.sockets[0].getsockname()[1]
        client = HomeAssistant(f"http://127.0.0.1:{rest_port}", fake_hass.TOKEN, f"ws://127.0.0.1:{port}/api/websocket")
        await client.start()
        try:
            assert await client.wait_connected(10)
            slots = {"domain": ["light"], "area": "kitchen"}
            done = await client.handle_intent("HassTurnOn", slots, {}, "en", device_id="device-x")
            assert [t["id"] for t in done["response"]["data"]["success"]] == ["light.kitchen_ceiling"]
            assert fake.intents[-1]["how"] == "websocket" and fake.intents[-1]["device_id"] == "device-x"
            # What is exposed to Assist, for the questions; with the catalog.
            assert "light.kitchen_ceiling" in client.catalog.exposed
            fake.unexposed.add("light.kitchen_ceiling")
            assert "light.kitchen_ceiling" not in await client.exposed_entities()
            fake.intent_command = False
            hidden = await client.handle_intent("HassTurnOn", slots, {}, "en")
            assert fake.intents[-1]["how"] == "rest"
            assert hidden["error"]["code"] == "no_match" and hidden["error"]["reason"] == "assistant"
            # A device named: exposed or not.
            named = await client.handle_intent("HassTurnOn", {"name": "light.kitchen_ceiling"}, {}, "en", exposed_only=False)
            assert "error" not in named and fake.intents[-1]["exposed_only"] is False
            # Without the integration, from the exposure settings.
            assert await client.exposed_entities() == fake.exposed()
        finally:
            await client.stop()
            await rest.cleanup()
            server.close()

    asyncio.run(go())


def test_the_questions_count_what_is_exposed():
    """The questions the library answers itself (are any lights on, is
    everything locked) see what is exposed to Assist; the command's event
    and record leave it out."""
    hass = StubHass()
    hass.catalog = type("Catalog", (), {"exposed": {"light.kitchen_ceiling"}, "entities": {}, "areas": {}, "floors": {}})()
    spec = {"response": "{{ exposed | join(',') }}"}
    said = run(spec, hass)
    assert said.speech == "light.kitchen_ceiling"
    assert "exposed" not in said.to_json()["variables"] and "exposed" not in hass.events[-1]


class CapableHass(StubHass):
    """Devices that can do some things and not others: the catalog keeps
    what their states say. Intents act on the devices named, else every
    light in the area (HassLightSet asks nothing of them)."""

    def __init__(self) -> None:
        super().__init__()
        from voice_call.hass import Catalog, Entity  # pylint: disable=import-outside-toplevel

        lights = [("light.kitchen_ceiling", "Kitchen Ceiling", ["onoff"]), ("light.kitchen_strip", "Kitchen Strip", ["hs", "color_temp"]),
                  ("light.desk_lamp", "Desk Lamp", ["onoff"])]
        self.catalog = Catalog(entities={e: Entity(e, n, area_id="office" if "desk" in e else "kitchen",
                                                   attributes={"supported_color_modes": modes}) for e, n, modes in lights})
        self.catalog.entities["climate.hvac"] = Entity("climate.hvac", "HVAC", attributes={
            "supported_features": 8 | 1, "fan_modes": ["on_low", "on_high", "auto_low", "auto_high", "off"], "hvac_modes": ["off", "heat"]})
        self.catalog.exposed = set(self.catalog.entities)
        self.now: list[Any] = ["auto_high"]
        self.feature = False

    async def render_value(self, template, variables=None):
        return self.now

    async def handle_intent(self, name, slots, variables, language, device_id=None, text=None, exposed_only=True):
        self.fake.intents.append({"intent": name, "slots": slots})
        if self.feature:
            return {"error": {"code": "no_match", "reason": "feature", "name": None}}
        acted = [e for e in self.catalog.entities.values()
                 if e.entity_id == slots.get("name") or (slots.get("area") and e.area_id == slots["area"] and e.domain == "light")]
        return {"response": {"speech": {}, "data": {"success": [{"type": "entity", "id": e.entity_id, "name": e.name} for e in acted]}},
                "unavailable": []}


COLOR = {"intent": {"name": "HassLightSet", "slots": {"color": "red"}}, "supports": "color",
         "response": {"done": {"specific": "Turned the lights {{ place.said }} red.", "verbose": "Turned {{ result.names | join(', ') }} red."}}}


def test_only_what_can_does_it():
    hass = CapableHass()
    # In the kitchen: only the light that changes color, by name.
    some = run(COLOR, hass)
    assert (some.name, some.speech) == ("done", "Turned the lights in the Kitchen red.")
    assert [i["slots"].get("name") for i in hass.fake.intents] == ["light.kitchen_strip"]
    assert some.skipped == ["Kitchen Ceiling"]
    # What was left out is said when verbose.
    verbose = run({**COLOR, "verbosity": "verbose"}, hass)
    assert verbose.speech == "Turned Kitchen Strip red. Kitchen Ceiling can't change color."
    # Nothing there can: nothing runs.
    hass.fake.intents.clear()
    office = run(COLOR, hass, {"area": ["office"]})
    assert (office.name, office.speech) == ("not_supported", "Nothing in the Office can change color.")
    assert not hass.fake.intents
    # A device named that can't: said as itself.
    named = run(COLOR, hass, {"entity": ["light.desk_lamp"]})
    assert (named.name, named.speech) == ("not_supported", "Desk Lamp can't change color.") and not hass.fake.intents
    # Home Assistant's own refusal, for what it checks itself.
    hass.feature = True
    refused = run({"intent": {"name": "HassClimateSetTemperature", "slots": {"temperature": 20}}}, hass, {"area": ["office"]})
    assert (refused.name, refused.speech) == ("not_supported", "Nothing in the Office can do that.")


def test_a_mode_as_the_device_has_it():
    hass = CapableHass()
    fan = {"supports": {"fan_mode": "mode"}, "lists": {"mode": {"list": "fan_modes"}}, "home": "climate",
           "action": [{"action": "climate.set_fan_mode", "data": {"fan_mode": "{{ slots.mode }}"}}],
           "response": "Set the fan to {{ slots.mode }}."}
    command = parse_command("c", {"description": "do it", "examples": ["do it"], **fan})

    def said(mode, **kwargs):
        decision = Decision({"transcript": "do it"}, command, command.behavior, "command", slots={"mode": mode})
        return asyncio.run(actions.run(hass, decision, SATELLITE, None, False, home={"climate": "climate.hvac"}, **kwargs))

    # "Low", from auto high: auto low.
    assert said("low").speech == "Set the fan to auto_low."
    hass.now = ["on_high"]
    assert said("low").speech == "Set the fan to on_low."
    # One it doesn't have.
    gone = said("circulate")
    assert (gone.name, gone.speech) == ("not_supported", "HVAC can't set its fan to circulate.")
