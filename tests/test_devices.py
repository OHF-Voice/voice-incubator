"""A device named with a room: the one of that name there (typed text through
the real CTC model)."""

from __future__ import annotations

from pathlib import Path

import pytest

from voice_labs import actions
from voice_labs.engine import Engine, Satellite
from voice_labs.store import compile_store

CTC = Path(__file__).parent.parent / "models/speech-to-phrase/stt_en_parakeet_tdt_ctc_110m-int8"
STORE = {
    "names": {
        "areas": {"office": {"name": "Office"}, "bedroom": {"name": "Bedroom"}, "kitchen": {"name": "Kitchen"}},
        "entities": {"fan.office": {"name": "Fan"}, "fan.bedroom": {"name": "Fan"}, "switch.kettle": {"name": "Kettle"}},
    },
    "commands": {
        "device_on": {
            "description": "turn on a device",
            "require": [["turn on", "turn ... on"]],
            "optional": [["in"]],
            "names": {"entity": {"count": 1}, "area": {"count": 1}},
            "combinations": [["entity"], ["entity", "area"]],
            "action": {"action": "homeassistant.turn_on", "target": "{{ target }}"},
        }
    },
}
AREAS = {"fan.office": "office", "fan.bedroom": "bedroom", "switch.kettle": None}


@pytest.fixture(name="engine", scope="module")
def fixture_engine():
    if not CTC.is_dir():
        pytest.skip("no CTC model")
    engine = Engine(CTC, None, None, threads=2)
    engine.build(compile_store(STORE))
    engine.entity_area = AREAS.get
    return engine


KITCHEN = Satellite("k", "Kitchen", "kitchen", "Kitchen")
BEDROOM = Satellite("b", "Bedroom", "bedroom", "Bedroom")


def test_the_device_of_that_name_in_that_room(engine):
    decision = engine.recognize_text("turn on the fan in the office", KITCHEN)
    assert decision.command.id == "device_on" and decision.slots == {"entity": ["fan.office"], "area": ["office"]}
    # None there: nothing runs, and it says why.
    decision = engine.recognize_text("turn on the fan in the kitchen", KITCHEN)
    assert decision.command is None and decision.message == (
        "It looks like you're trying to turn on a device, but there's no Fan in the Kitchen."
    )
    decision = engine.recognize_text("turn on the kettle in the office", KITCHEN)
    assert decision.message.endswith("but I don't know which room Kettle is in.")


def test_a_shared_name_without_a_room_is_the_one_here(engine):
    assert engine.recognize_text("turn on the fan", BEDROOM).slots == {"entity": ["fan.bedroom"]}
    # Not here: as the matcher heard it.
    assert len(engine.recognize_text("turn on the fan", KITCHEN).slots["entity"]) == 1


def test_a_room_said_with_a_device_only_picks_it_out():
    satellite = Satellite("k", "Kitchen", "kitchen", "Kitchen")
    command = compile_store(STORE).commands["device_on"]
    values = actions.variables(command, {"entity": ["fan.office"], "area": ["office"]}, satellite, None, "")
    assert values["target"] == {"entity_id": ["fan.office"]}
    assert values["place"]["said"] == "in the Office"
    assert actions.variables(command, {"area": ["office"]}, satellite, None, "")["target"] == {"area_id": ["office"]}
