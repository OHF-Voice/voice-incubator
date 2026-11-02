"""Two commands said together (voice_labs/compound.py): split where the
whole sentence leaves "and" or "then" unexplained, a clause that is no
command alone borrowing from the other, both or neither -- typed, and said
(Piper, through the CTC and TDT heads)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from voice_labs import compound, languages
from voice_labs.languages.en import compound as english

ROOT = Path(__file__).resolve().parent.parent
CTC = ROOT / "models/speech-to-phrase/stt_en_parakeet_tdt_ctc_110m-int8"
TDT = ROOT / "models/parakeet-tdt-int8"
KRISTIN = ROOT / "models/piper/en_US-kristin-medium.onnx"


@pytest.fixture(autouse=True)
def in_english():
    languages.use("en")


def test_where_a_sentence_may_split():
    words = "lock the door and then turn off the lights".split()
    assert compound.splits(words) == [(3, 5)]
    assert compound.splits("turn off the lights then lock the door".split()) == [(4, 5)]
    assert compound.splits("and turn off the lights and".split()) == []  # nothing on one side
    # "And" within an amount joins no commands.
    for amount in ("set a timer for 1 hour and 30 seconds", "set a timer for an hour and a half", "two hundred and fifty"):
        assert compound.splits(amount.split()) == [], amount
    assert compound.splits("set a timer for 5 minutes and a timer for 10 minutes".split()) == [(6, 7)]
    assert compound.borrowings(["turn", "off", "the", "lights"], ["the", "tv"], "second") == [
        "turn the tv", "turn off the tv", "turn off the the tv",
    ]
    assert compound.borrowings(["turn", "off", "the", "kitchen"], ["bedroom", "lights"], "first") == ["turn off the kitchen lights"]


@pytest.mark.parametrize(
    ("first", "second", "said"),
    [
        ("Turned off the lights.", "Turned off the TV.", "Turned off the lights and the TV."),
        ("Turned on the lights.", "Turned off the TV.", "Turned on the lights and turned off the TV."),
        ("Turned off Desk Lamp.", "Turned off Floor Lamp.", "Turned off Desk Lamp and Floor Lamp."),
        ("Set the kitchen lights to red.", "Set the bedroom lights to blue.", "Set the kitchen lights to red and the bedroom lights to blue."),
        ("Turned on the TV.", "Set the volume to 50%.", "Turned on the TV and set the volume to 50%."),
        ("Locked the front door.", "TV is off.", "Locked the front door and TV is off."),
        ("Turned on the lights.", "Turned on the lights.", "Turned on the lights."),
    ],
)
def test_what_both_say_is_one_sentence(first, second, said):
    assert english.both(first, second) == said


def test_what_both_say():
    assert compound.both(None, None) is None  # both silent
    assert compound.both("", "") == ""  # the acknowledgement sound, once
    assert compound.both("Turned on the lights.", "") == "Turned on the lights."
    assert compound.both(None, "Locked the front door.") == "Locked the front door."
    assert compound.alone("cancel all the timers") == "Ask me to cancel all the timers on its own."


def test_only_a_language_that_hears_them():
    assert compound.joiners() == ("and then", "and", "then")
    languages.use("de")
    assert compound.joiners() == () and not compound.splits("mach das licht an und dann aus".split())


@pytest.fixture(name="engine", scope="module")
def fixture_engine():
    """English's library (but the timers) in a home with a TV, two lamps and
    a front door, and a satellite in its Office."""
    from voice_labs.engine import Engine, Satellite  # pylint: disable=import-outside-toplevel
    from voice_labs.library import load_library  # pylint: disable=import-outside-toplevel
    from voice_labs.store import compile_store  # pylint: disable=import-outside-toplevel

    if not CTC.is_dir():
        pytest.skip("no CTC model")
    languages.use("en")
    entries = [e for e in load_library() if e["file"] != "timers"]
    store = {
        "names": {
            "entities": {
                "media_player.tv": {"name": "TV"}, "light.desk_lamp": {"name": "Desk Lamp"},
                "light.salt_and_pepper": {"name": "Salt and Pepper Lamp"}, "scene.rock_and_roll": {"name": "Rock and Roll"},
                "light.nanoleaf": {"name": "Home Assistant Light"},
                "light.floor_lamp": {"name": "Floor Lamp"}, "lock.front_door": {"name": "Front Door"},
            },
            "areas": {"kitchen": {"name": "Kitchen"}, "bedroom": {"name": "Bedroom"}, "office": {"name": "Office"}},
        },
        "lists": {name: values for e in entries for name, values in e["lists"].items()},
        "commands": {e["id"]: e["spec"] for e in entries},
    }
    engine = Engine(CTC, TDT if TDT.is_dir() else None, None, threads=2)
    engine.build(compile_store(store))
    return engine, Satellite("s", "Office", "office", "Office")


def heard(decision):
    """Each command a decision runs, with what it acts on."""
    out = []
    while decision is not None and decision.command is not None:
        out.append((decision.command.id, decision.slots.get("entity") or decision.slots.get("area")))
        decision = decision.then
    return out


ROUTES = [
    # Two commands.
    ("turn on the lights and turn off the TV", [("turn_on_here", None), ("turn_off_device", ["media_player.tv"])]),
    ("lock the front door and then turn off the lights", [("lock", ["lock.front_door"]), ("turn_off_here", None)]),
    # One command, two targets: the second borrows the first's verb.
    ("turn off the desk lamp and the floor lamp", [("turn_off_device", ["light.desk_lamp"]), ("turn_off_device", ["light.floor_lamp"])]),
    ("turn off the lights and the TV", [("turn_off_here", None), ("turn_off_device", ["media_player.tv"])]),
    ("turn on the lights in the kitchen and the bedroom", [("turn_on_place", ["kitchen"]), ("turn_on_place", ["bedroom"])]),
    ("turn off the kitchen and bedroom lights", [("turn_off_place", ["kitchen"]), ("turn_off_place", ["bedroom"])]),
    # A second that names nothing acts on what the first named.
    ("turn on the kitchen lights and set the brightness to 50", [("turn_on_place", ["kitchen"]), ("set_brightness_place", ["kitchen"])]),
    # One target, two commands: "its" is the server's (test_its_is_what_the_first_acted_on).
    ("turn on the TV and set its volume to 50%", [("turn_on_device", ["media_player.tv"]), ("set_volume_here", None)]),
    # One command, whole: "and" is part of it -- a list item, a name.
    ("add apples and bananas to the shopping list", [("shopping_list_add", None)]),
    ("turn on the salt and pepper lamp", [("turn_on_device", ["light.salt_and_pepper"])]),
    ("activate rock and roll", [("scene", ["scene.rock_and_roll"])]),
    ("turn on the salt and pepper lamp and the TV", [("turn_on_device", ["light.salt_and_pepper"]), ("turn_on_device", ["media_player.tv"])]),
    # Not two commands: neither runs, not half.
    ("turn off the lights and goodnight", []),
]


@pytest.mark.parametrize(("text", "commands"), ROUTES)
def test_typed(engine, text, commands):
    engine, office = engine
    assert heard(engine.recognize_text(text, office)) == commands


class Stt:
    """An STT entity's place: says what it is told to."""

    source = "stt.home_assistant_cloud"

    def __init__(self, text):
        self.text = text

    def transcribe(self, audio):
        return self.text

    def transcribe_words(self, audio):
        return self.text, None


def test_the_transcript_splits_what_the_audio_misheard(engine):
    """The CTC head heard "light" as "like": the first clause, on its own
    audio, names nothing -- but the transcript's words split as typed do."""
    from adapt_ctc.text import spoken_words  # pylint: disable=import-outside-toplevel

    engine, office = engine
    misheard = "to turn on a home assistant like and set its brightness to ten percent"
    log_probs, _ = engine.model.vocab.text_log_probs(spoken_words(misheard, "en")[0])
    decision = engine.decide(
        log_probs, engine.model.frame_seconds, np.zeros(16000, dtype=np.float32), office,
        Stt("To turn on a home assistant light and set its brightness to 10%?"),
    )
    assert heard(decision) == [("turn_on_device", ["light.nanoleaf"]), ("set_brightness_here", None)]
    assert decision.compound["from"] == "transcript"


def test_never_half(engine):
    engine, office = engine
    decision = engine.recognize_text("turn off the lights and goodnight", office)
    assert decision.compound == {"half": "turn_off_here", "split": False}
    assert decision.message == "It looks like you're trying to turn off the lights, but I didn't understand all of it."
    assert decision.fallback is None  # meaning and the model would pick one


def test_its_is_what_the_first_acted_on(engine):
    from voice_labs.context import hear_again  # pylint: disable=import-outside-toplevel

    engine, office = engine
    second = engine.recognize_text("turn on the TV and set its volume to 50%", office).then
    found = hear_again(second.transcript, ["media_player.tv"], {"media_player.tv": "TV"}, lambda t: engine.recognize_text(t, office))
    assert found is not None and (found[0].command.id, found[0].slots) == ("set_volume_device", {"entity": ["media_player.tv"], "percentage": 50})


@pytest.mark.parametrize(
    ("text", "commands"),
    [
        ("Turn on the lights and turn off the TV.", ROUTES[0][1]),
        ("Lock the front door, and then turn off the lights.", ROUTES[1][1]),
        ("Turn off the desk lamp and the floor lamp.", ROUTES[2][1]),
        ("Turn off the lights and the TV.", ROUTES[3][1]),
    ],
)
def test_said(engine, text, commands):
    """Said, each clause is matched on its own stretch of the audio."""
    from voice_labs.tts import Speech  # pylint: disable=import-outside-toplevel

    if not KRISTIN.is_file():
        pytest.skip("no Piper voice")
    engine, office = engine
    speech = Speech(KRISTIN, ROOT / "models", synthesis=lambda: {"noise_scale": 0.0, "noise_w_scale": 0.0})
    import asyncio  # pylint: disable=import-outside-toplevel

    asyncio.run(speech.use(KRISTIN))
    pcm = speech.synthesize(text)
    audio = np.frombuffer(bytes(int(0.3 * 32000)) + pcm, dtype="<i2").astype(np.float32) / 32768
    assert heard(engine.recognize_command(audio, office)) == commands
