"""Running a command: which response is said for how it went."""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Any

from voice_labs import actions
from voice_labs.commands import parse_command
from voice_labs.engine import Decision, Satellite
from voice_labs.hass import HassError
from voice_labs.languages.en import speech as english

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
        self.todo_items: dict[str, list[dict]] = {}
        """To-do lists' items (todo.get_items), by entity."""

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
        if domain == "todo" and service == "get_items":
            return {target["entity_id"]: {"items": list(self.todo_items.get(target["entity_id"], []))}}
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


def run(spec, hass, slots=None, integration=None, dry_run=False, names=None):
    command = parse_command("c", {"description": "do it", "examples": ["do it"], **spec})
    decision = Decision({"transcript": "do it"}, command, command.behavior, "command", slots=slots or {})
    return asyncio.run(actions.run(hass, decision, SATELLITE, None, dry_run, integration, names=names))


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


def test_a_command_asks_first():
    """``confirm``: the question said and nothing done, unless answered yes
    (``confirmed``) -- or, a template, unless it renders false. Nothing is
    asked when it would do nothing."""
    hass = StubHass()
    spec = {"timer": "cancel_all", "confirm": "{{ timers | count > 1 }}",
            "response": {"confirm": "Cancel all {{ timers | count }} timers?"}}
    both = StubTimers([{**TEA, "status": "active"}, {**TEA, "timer_id": "4", "name": "pizza"}])
    command = parse_command("c", {"description": "cancel all the timers", "examples": ["cancel all the timers"], **spec})
    decision = Decision({"transcript": "cancel all the timers"}, command, command.behavior, "command")
    asked = asyncio.run(actions.run(hass, decision, SATELLITE, None, False, both))
    assert (asked.name, asked.speech, asked.sequence) == ("confirm", "Cancel all 2 timers?", [])
    assert hass.services == [] and hass.events[-1]["outcome"] == "confirm"
    done = asyncio.run(actions.run(hass, decision, SATELLITE, None, False, both, confirmed=True))
    assert done.name == "done" and [s for s, _ in hass.services] == ["cancel_timer", "cancel_timer"]
    # One timer: not asked. None: nothing to ask about.
    hass.services.clear()
    one = run(spec, hass, integration=StubTimers([{**TEA, "status": "active"}]))
    assert one.name == "done" and hass.services == [("cancel_timer", {"timer_id": "3"})]
    assert run(spec, hass, integration=StubTimers([])).name == "no_timers"
    # Always, with its description asked when it has no question of its own.
    lights = run({"action": {"action": "light.turn_off"}, "confirm": True}, hass)
    assert (lights.name, lights.speech) == ("confirm", "Do it?")
    assert "confirm" in parse_command("c", {"description": "x", "examples": ["x"], "confirm": True}).behavior.outcomes()
    assert parse_command("c", {"description": "x", "examples": ["x"], "confirm": False}).behavior.confirm is None


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
        from voice_labs.hass import Area, Entity, Floor  # pylint: disable=import-outside-toplevel

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


def say(spec, slots=None, elsewhere=actions.BEEP_ELSEWHERE, hass=None, integration=None, satellite=SATELLITE):
    hass = hass or StubHass()
    command = parse_command("c", {"description": "do it", "examples": ["do it"], **spec})
    decision = Decision({"transcript": "do it"}, command, command.behavior, "command", slots=slots or {})
    return asyncio.run(actions.run(hass, decision, satellite, None, False, integration, None, elsewhere))


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


def test_an_area_says_at_most_its_own():
    # The area's "Say at most": no more, wherever it acted; a question still answers.
    quiet = dataclasses.replace(SATELLITE, settings={"at_most": "beep"})
    brief = dataclasses.replace(SATELLITE, settings={"at_most": "brief"})
    office = say(LIGHTS, {"area": ["office"]}, satellite=quiet)
    assert office.speech == "" and office.verbosity["why"] == "the most said in the satellite's area"
    assert say(LIGHTS, {"area": ["office"]}, satellite=brief).speech == "Turned on the lights."
    assert say({**LIGHTS, "verbosity": "brief"}, satellite=brief).speech == "Turned on the lights."
    question = say({"verbosity": "verbose", "response": "It's late."}, satellite=quiet)
    assert question.speech == "It's late." and question.verbosity["why"] == "a question always answers"


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
    """The client: the Voice Labs integration's command, else the REST API
    (why it matched nothing read from its message)."""
    from voice_labs.hass import HomeAssistant  # pylint: disable=import-outside-toplevel

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


def test_the_questions_count_the_devices_added_to_the_area():
    """The questions the library answers itself (are any lights on, is
    everything locked) see the devices added on the Names page, each in its
    area there, but those not part of it -- not what is exposed to Assist; the
    command's event and record leave them out."""
    from voice_labs.hass import Catalog, Entity  # pylint: disable=import-outside-toplevel
    from voice_labs.store import Device  # pylint: disable=import-outside-toplevel

    hass = StubHass()
    hass.catalog = Catalog(entities={
        "light.kitchen_ceiling": Entity("light.kitchen_ceiling", "Kitchen Ceiling", area_id="kitchen"),
        "light.lamp": Entity("light.lamp", "Lamp", area_id="office"),
        "light.night": Entity("light.night", "Night Light", area_id="kitchen"),
        "light.exposed": Entity("light.exposed", "Exposed", area_id="kitchen"),
    }, exposed={"light.exposed"})
    devices = {"light.kitchen_ceiling": Device(), "light.lamp": Device(area="kitchen"), "light.night": Device(implicit=False)}
    spec = {"response": "{{ devices | dictsort | map('join', '=') | join(',') }}"}
    command = parse_command("c", {"description": "do it", "examples": ["do it"], **spec})
    decision = Decision({"transcript": "do it"}, command, command.behavior, "command", slots={})
    said = asyncio.run(actions.run(hass, decision, SATELLITE, None, devices=devices))
    assert said.speech == "light.kitchen_ceiling=kitchen,light.lamp=kitchen"
    assert "devices" not in said.to_json()["variables"] and "devices" not in hass.events[-1]


class CapableHass(StubHass):
    """Devices that can do some things and not others: the catalog keeps
    what their states say. Intents act on the devices named, else every
    light in the area (HassLightSet asks nothing of them)."""

    def __init__(self) -> None:
        super().__init__()
        from voice_labs.hass import Catalog, Entity  # pylint: disable=import-outside-toplevel

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


def test_a_script_says_its_speech_or_plays_the_sound():
    """Without a response of its own, a script command says the speech the
    script returned; with none, nothing: the acknowledgement sound."""
    hass = StubHass()
    hass.script_response = {"speech": "Good night. Sleep well."}
    said = run({"script": "script.good_night"}, hass)
    assert (said.name, said.speech, said.silent) == ("done", "Good night. Sleep well.", False)
    hass.script_response = {}
    sound = run({"script": "script.good_night"}, hass)
    assert (sound.name, sound.speech, sound.silent) == ("done", "", False)


def test_what_sensors_say():
    """The Sensors library group: a sensor's value in its unit, a binary
    sensor in its device class's words, yes or no when asked."""
    from pathlib import Path  # pylint: disable=import-outside-toplevel

    import yaml  # pylint: disable=import-outside-toplevel

    library = yaml.safe_load((Path(actions.__file__).parent / "languages/en/library/sensors.yaml").read_text())["commands"]
    hass = StubHass()
    hass.fake.states.update({
        "sensor.outside_temperature": ("12.50", {"friendly_name": "Outside Temperature", "unit_of_measurement": "°C"}),
        "sensor.dryer_power": ("1200.0", {"friendly_name": "Dryer Power", "unit_of_measurement": "W"}),
        "sensor.washer": ("rinse_cycle", {"friendly_name": "Washer"}),
        "binary_sensor.front_door": ("off", {"friendly_name": "Front Door", "device_class": "door"}),
        "binary_sensor.basement": ("on", {"friendly_name": "Basement", "device_class": "moisture"}),
    })

    def said(command, slots):
        spec = {k: v for k, v in library[command].items() if k not in ("title", "needs")}
        return run(spec, hass, slots).speech

    assert said("sensor_value", {"entity": ["sensor.outside_temperature"]}) == "Outside Temperature is 12.5 degrees."
    assert said("sensor_value", {"entity": ["sensor.dryer_power"]}) == "Dryer Power is 1200 watts."
    assert said("sensor_value", {"entity": ["sensor.washer"]}) == "Washer is rinse cycle."
    assert said("binary_sensor_state", {"entity": ["binary_sensor.front_door"], "asked": "open"}) == "No, Front Door is closed."
    assert said("binary_sensor_state", {"entity": ["binary_sensor.front_door"], "asked": "closed"}) == "Yes, Front Door is closed."
    assert said("binary_sensor_state", {"entity": ["binary_sensor.basement"], "asked": "wet"}) == "Yes, Basement is wet."
    assert said("binary_sensor_state", {"entity": ["binary_sensor.basement"], "asked": "running"}) == "Basement is wet."


SHOPPING = {
    "adds_to_list": {"slot": "item", "list": "entity", "each": True},
    "action": [{"repeat": {"for_each": "{{ items }}", "sequence": [{"action": "todo.add_item"}]}}],
    "response": {"done": {
        "specific": "Added {{ slots.item }} to the shopping list.",
        "verbose": "{{ ('Added ' ~ added) if added else '' }}{{ ' and put ' ~ put_back ~ ' back' if put_back }}.",
    }},
}


def test_what_the_list_has_is_not_added_again():
    """Each item said: added, checked back on when it was checked off, or
    left as it is when it is on the list -- the same words as spoken, apart
    from emoji, case, an article, a quantity and a plural."""
    hass = StubHass()
    hass.todo_items["todo.shopping"] = [
        {"summary": "🍎 Apples", "status": "needs_action"},
        {"summary": "Milk 🥛", "status": "completed"},
        {"summary": "Mac and Cheese", "status": "needs_action"},
        {"summary": "Peas", "status": "needs_action"},
    ]

    def add(item, verbosity=None):
        return run({**SHOPPING, **({"verbosity": verbosity} if verbosity else {})}, hass,
                   {"entity": ["todo.shopping"], "item": item})

    added = add("bananas")
    assert (added.name, added.variables["items"], added.variables["back"]) == ("done", ["bananas"], [])
    assert added.speech == "Added bananas to the shopping list."
    # Checked off: checked back on, as the list has it; said as added but when verbose.
    back = add("the milk")
    assert (back.variables["items"], back.variables["back"]) == ([], ["Milk 🥛"])
    assert back.speech == "Added Milk 🥛 to the shopping list."
    assert add("milk and pears", "verbose").speech == "Added pears and put Milk 🥛 back."
    # On it already: nothing to add, and said; with others, said after them.
    there = add("two apples")
    assert (there.name, there.speech) == ("already_on_list", "🍎 Apples is already on the list.")
    assert add("apple and pears").speech == "Added pears to the shopping list. 🍎 Apples is already on the list."
    assert add("mac and cheese").name == "already_on_list"  # one item, called that
    # Not merely close in spelling: "pears" are not "peas".
    assert add("pears").variables["items"] == ["pears"]
    # Said twice, added once.
    assert add("figs and figs").variables["items"] == ["figs"]
    # A list that can't be read: all of it added.
    hass.fail = True
    assert run(SHOPPING, hass, {"entity": ["todo.shopping"], "item": "apples"}, dry_run=True).variables["items"] == ["apples"]


def test_the_whole_of_it_unless_each():
    hass = StubHass()
    hass.todo_items["todo.chores"] = [{"summary": "Call mom", "status": "completed"}]
    spec = {"adds_to_list": {"list": "todo.chores"}, "action": [{"action": "todo.add_item"}], "response": "ok"}
    whole = run(spec, hass, {"item": "call mom and dad"})
    assert (whole.variables["items"], whole.variables["back"]) == (["call mom and dad"], [])
    again = run(spec, hass, {"item": "call mom"})
    assert (again.variables["items"], again.variables["back"]) == ([], ["Call mom"])



def test_the_librarys_shopping_list_says_what_it_did():
    from voice_labs.library import base  # pylint: disable=import-outside-toplevel

    hass = StubHass()
    hass.todo_items["todo.shopping"] = [{"summary": "🥛 Milk", "status": "completed"}, {"summary": "Eggs", "status": "needs_action"}]
    spec = {k: v for k, v in base("todo/shopping_list_add").items() if k not in ("title", "library")}
    slots = {"entity": ["todo.shopping"], "item": "pears and milk"}
    assert run(spec, hass, slots).speech == "Added pears and 🥛 Milk to the shopping list."
    verbose = run({**spec, "verbosity": "verbose"}, hass, slots)
    assert verbose.speech == "Added pears to the shopping list and put 🥛 Milk back on it."
    assert run({**spec, "verbosity": "verbose"}, hass, {**slots, "item": "milk"}).speech == "Put 🥛 Milk back on the shopping list."
    assert run(spec, hass, {**slots, "item": "eggs"}).speech == "Eggs is already on the list."


def test_a_device_is_said_by_its_name_on_the_names_page():
    """Not as Home Assistant names it ("GW2000B Humidity"): the library's
    replies, and where it acted, by the Names page's names."""
    from voice_labs.library import base  # pylint: disable=import-outside-toplevel

    hass = StubHass()
    hass.fake.states["sensor.gw2000b_humidity"] = ("54", {"friendly_name": "GW2000B Humidity", "unit_of_measurement": "%"})
    names = actions.said_names({
        "entities": {"sensor.gw2000b_humidity": {"name": "Outdoor Humidity", "aliases": ["humidity outside"]}},
        "areas": {"kitchen": "Galley"},
    })
    spec = {k: v for k, v in base("sensors/sensor_value").items() if k not in ("title", "library")}
    slots = {"entity": ["sensor.gw2000b_humidity"]}
    assert run(spec, hass, slots, names=names).speech == "Outdoor Humidity is 54 percent."
    assert run(spec, hass, slots).speech == "GW2000B Humidity is 54 percent."  # not named: Home Assistant's
    assert run({"response": "Done {{ place.said }}."}, hass, {}, names=names).speech == "Done in the Galley."



def test_a_device_is_said_as_it_is_heard_there():
    """A device spoken of on a satellite in its area by its area's name, and
    elsewhere by its whole house name -- else, heard only with its area said,
    by its area's."""
    from voice_labs.store import Device  # pylint: disable=import-outside-toplevel

    devices = {
        "light.office_lamp": Device(None, ("Lamp",), ("Office Lamp",)),
        "light.overhead": Device(None, ("Overhead Light",), ()),
        "light.porch": Device(None, (), ("Porch Light",)),
        "light.night": Device(),
    }
    area = {"light.office_lamp": "office", "light.overhead": "office"}.get
    assert actions.device_said(devices, area, "office") == {
        "light.office_lamp": "Lamp", "light.overhead": "Overhead Light", "light.porch": "Porch Light"}
    assert actions.device_said(devices, area, "kitchen") == {
        "light.office_lamp": "Office Lamp", "light.overhead": "Overhead Light", "light.porch": "Porch Light"}


def test_without_a_name_the_devices_added_to_the_area():
    """As voice sees it (devices given): "turn on the lights" acts on the
    lights added to the satellite's area on the Names page, each by name --
    in their area there, but those not part of it, Assist's exposure aside."""
    from voice_labs.store import Device  # pylint: disable=import-outside-toplevel

    hass = CapableHass()
    hass.catalog.exposed = set()  # not exposed to Assist: it doesn't count
    lights_on = {"intent": {"name": "HassTurnOn", "slots": {"domain": "light"}}, "response": "{{ result.names | join(', ') }}."}

    def turned_on(devices, **kw):
        hass.fake.intents.clear()
        command = parse_command("c", {"description": "do it", "examples": ["do it"], **lights_on, **kw})
        decision = Decision({"transcript": "do it"}, command, command.behavior, "command", slots={})
        outcome = asyncio.run(actions.run(hass, decision, SATELLITE, None, devices=devices))
        return outcome, [i["slots"].get("name") for i in hass.fake.intents]

    devices = {"light.kitchen_ceiling": Device(), "light.kitchen_strip": Device(implicit=False),
               "light.desk_lamp": Device(area="kitchen")}
    outcome, named = turned_on(devices)
    assert sorted(named) == ["light.desk_lamp", "light.kitchen_ceiling"]  # the strip not part of its area; the lamp moved here
    assert outcome.speech in ("Kitchen Ceiling, Desk Lamp.", "Desk Lamp, Kitchen Ceiling.")
    # None of the kind added there: said so, and no intent.
    outcome, named = turned_on({"light.kitchen_strip": Device(implicit=False)})
    assert (outcome.name, named) == ("no_devices", [])


class StubQuiet:
    """The integration, as far as do not disturb goes: these areas are on."""

    def __init__(self, *areas):
        self.areas = set(areas)

    async def quiet_areas(self):
        return set(self.areas)


def test_a_command_kept_out_of_an_area_on_do_not_disturb():
    """One that says so keeps out of an area on do not disturb, heard
    elsewhere; in that area itself it runs. Others run there anyway."""
    hass = StubHass()
    story = {"action": [{"action": "script.goodnight_story"}], "response": "Once upon a time.", "do_not_disturb": True}
    quiet = StubQuiet("bedroom", "kitchen")  # the satellite's own: never kept out
    outcome = run(story, hass, {"area": ["bedroom"]}, integration=quiet)
    assert (outcome.name, outcome.speech) == ("do_not_disturb", "Do not disturb is on in the Bedroom.")
    assert run(story, hass, {}, integration=quiet).name == "done"
    assert run({**story, "do_not_disturb": False}, hass, {"area": ["bedroom"]}, integration=quiet).name == "done"
    assert run(story, hass, {"area": ["bedroom"]}, integration=StubQuiet()).name == "done"


class StubSatellites:
    """The other satellites, to announce on."""

    def __init__(self, *areas):
        self.all = [{"key": area, "name": area.title(), "area_id": area} for area in areas]
        self.said: list[tuple[list[str], str]] = []

    def others(self, source, areas):
        return [s for s in self.all if areas is None or s["area_id"] in areas]

    def announce(self, keys, text, source):
        self.said.append((keys, text))


def test_an_announcement_skips_an_area_on_do_not_disturb():
    hass = StubHass()
    command = parse_command("a", {"description": "announce", "examples": ["x"], "announce": "Dinner is ready"})
    satellites = StubSatellites("office", "bedroom")

    def announce(quiet, dry_run=False):
        decision = Decision({"transcript": "x"}, command, command.behavior, "command", slots={})
        return asyncio.run(actions.run(hass, decision, SATELLITE, None, dry_run, StubQuiet(*quiet), satellites=satellites))

    outcome = announce(["bedroom"])
    assert outcome.speech == "Announced it everywhere. Do not disturb is on in the Bedroom."
    assert satellites.said == [(["office"], "Dinner is ready")]
    outcome = announce(["bedroom", "office"], dry_run=True)
    assert (outcome.name, outcome.speech) == ("do_not_disturb", "Do not disturb is on in the Office and in the Bedroom.")
