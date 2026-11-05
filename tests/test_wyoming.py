"""The Wyoming endpoints (voice_labs/wyoming_server.py): what Home Assistant's
pipelines get from the app -- and the corrected transcript
(voice_labs/corrected.py) they hear commands with.

The app's scenario needs the models; skipped without them."""

from __future__ import annotations

import asyncio
import json
import socket
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import soxr
from piper import SynthesisConfig
from wyoming.asr import Transcribe, Transcript
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.client import AsyncTcpClient
from wyoming.handle import Handled, HandledChunk, HandledStart, HandledStop, NotHandled
from wyoming.info import Describe, Info
from wyoming.tts import Synthesize, SynthesizeChunk, SynthesizeStart, SynthesizeStop, SynthesizeStopped, SynthesizeVoice

from voice_labs import corrected, languages
from voice_labs.actions import level_for
from voice_labs.commands import Behavior
from voice_labs.engine import Engine, Satellite
from voice_labs.piper_voices import UNUSED_DAYS, Uses, catalog
from voice_labs.server import Options, VoiceLabsApp, tdt_models
from voice_labs.store import compile_store
from voice_labs.tts import load_voice
from voice_labs.wyoming_server import HOME_ASSISTANT, Endpoints, Heard, HeardCache

from . import fake_hass

ROOT = Path(__file__).resolve().parent.parent
VOICE = ROOT / "models/piper/en_US-kristin-medium.onnx"
OTHER_VOICE = "en_GB-jenny_dioco-medium"
"""A voice that is here, besides the one replies are said in."""
needs_models = pytest.mark.skipif(
    not (ROOT / "models/speech-to-phrase").exists() or not (ROOT / "models/parakeet-tdt-int8").exists()
    or not VOICE.exists() or not (ROOT / f"models/piper/{OTHER_VOICE}.onnx").exists(),
    reason="needs the models",
)


# --------------------------------------------------------------- what was heard


def _heard(text: str, satellite_id: str | None = None) -> Heard:
    return Heard(text, np.zeros(1, dtype=np.float32), (np.zeros((1, 1)), 0.08), None, satellite_id, "r")


def test_what_was_heard_is_taken_once_by_its_words():
    cache = HeardCache()
    cache.keep(_heard("Turn on the Nanoleaf."))
    assert cache.take("turn on the nanoleaf", None).text == "Turn on the Nanoleaf."
    assert cache.take("turn on the nanoleaf", None) is None
    # Another satellite's words are its own; a satellite unsaid is anyone's.
    cache.keep(_heard("what time is it", "assist_satellite.kitchen"))
    assert cache.take("What time is it?", "assist_satellite.office") is None
    assert cache.take("What time is it?", "assist_satellite.kitchen") is not None
    cache.keep(_heard("what time is it", "assist_satellite.kitchen"))
    assert cache.take("What time is it?", None) is not None


def test_what_was_heard_is_kept_a_while_and_only_the_last_few():
    cache = HeardCache(keep_seconds=30, most=2)
    for text in ("one", "two", "three"):
        cache.keep(_heard(text))
    assert cache.take("one", None) is None and cache.take("three", None) is not None
    stale = _heard("four")
    stale.at = time.monotonic() - 31
    cache.keep(stale)
    assert cache.take("four", None) is None


def test_only_home_assistant_connects_to_the_app():
    endpoints = Endpoints(None, "0.0.0.0", 10800, HOME_ASSISTANT)
    assert endpoints.allows(("127.0.0.1", 50000), ("127.0.0.1", 10800))
    assert endpoints.allows(("172.30.32.1", 50000), ("172.30.32.1", 10800))  # the Supervisor's network
    assert endpoints.allows(("172.30.33.4", 50000), ("192.168.1.5", 10800))
    assert endpoints.allows(("192.168.1.5", 50000), ("192.168.1.5", 10800))  # this machine, by its own address
    assert endpoints.allows(("::ffff:192.168.1.5", 50000, 0, 0), ("::ffff:192.168.1.5", 10800, 0, 0))
    assert not endpoints.allows(("192.168.1.9", 50000), ("192.168.1.5", 10800))  # another on the network
    # Standalone: whoever can reach the address it listens on.
    assert Endpoints(None, "127.0.0.1", 10800).allows(("192.168.1.9", 50000), ("192.168.1.5", 10800))


def test_a_command_that_beeps_says_its_brief_response_where_it_cant():
    behavior = Behavior(verbosity="beep")
    assert level_for(behavior, here=True) == ("beep", "the command's")
    assert level_for(behavior, here=True, beeps=False) == ("brief", "the satellite can't beep")
    assert level_for(Behavior(verbosity="silent"), here=True, beeps=False)[0] == "silent"


def test_voices_home_assistant_stopped_asking_for_are_removed(tmp_path):
    voices = catalog()
    old, recent, kept = sorted(voices)[:3]
    for key in (old, recent, kept):
        for path in voices[key].local_files(tmp_path):
            path.write_text("x")
    uses = Uses(tmp_path / "used.json")
    now = time.time()
    days = 24 * 3600
    for key, when in ((old, now - (UNUSED_DAYS + 1) * days), (recent, now - days), (kept, now - (UNUSED_DAYS + 5) * days)):
        uses.used(key, when)
    assert Uses(tmp_path / "used.json").last.keys() == {old, recent, kept}  # written down
    assert uses.sweep(tmp_path, kept={kept}, now=now) == [old]
    assert not voices[old].model(tmp_path).exists() and not any(p.exists() for p in voices[old].local_files(tmp_path))
    assert voices[recent].model(tmp_path).exists() and voices[kept].model(tmp_path).exists()
    assert json.loads((tmp_path / "used.json").read_text()).keys() == {recent, kept}
    # A voice the app fetched for itself, never asked for, isn't Home Assistant's to remove.
    for path in voices[sorted(voices)[3]].local_files(tmp_path):
        path.write_text("x")
    assert uses.sweep(tmp_path, kept=set(), now=now + (UNUSED_DAYS + 2) * days) == [recent, kept]
    assert voices[sorted(voices)[3]].model(tmp_path).exists()


# ---------------------------------------------------------- the corrected transcript


@dataclass
class _Hit:
    start: int
    end: int


@dataclass
class _Meaning:
    phrase: str
    name_type: str | None = None
    name_id: str | None = None
    value: Decimal | None = None
    unit: str | None = None


@dataclass
class _Use:
    role: str
    hit: _Hit
    meaning: _Meaning


NAMES = {
    "entities": {
        "light.nanoleaf": ["Nanoleaf"], "light.desk_lamp": ["Desk Lamp"],
        "lock.garage_side_door": ["Side Door", "Garage Side Door"],
    },
    "areas": {"office": ["Office"], "garage": ["Garage"]},
}
WORDS = [("Turn", 0.16, 0.32), ("on", 0.4, 0.48), ("the", 0.56, 0.64), ("Neno", 0.64, 0.88), ("leave.", 0.96, 1.28)]
THE = _Use("filler", _Hit(7, 7), _Meaning("the"))
NANOLEAF = _Use("name", _Hit(8, 14), _Meaning("nanoleaf", "entity", "light.nanoleaf"))


def test_names_and_numbers_are_written_over_the_words_said_where_they_were():
    assert corrected.written_over(WORDS, [THE, NANOLEAF], 0.08, NAMES) == "Turn on the Nanoleaf."
    words = [("set", 0.16, 0.24), ("the", 0.48, 0.56), ("desk", 0.56, 0.8), ("lamp", 0.88, 1.04), ("to", 1.2, 1.28),
             ("fifteen", 1.44, 1.68), ("percent.", 1.76, 2.24)]
    spans = [
        _Use("optional", _Hit(2, 2), _Meaning("set")),
        _Use("filler", _Hit(6, 6), _Meaning("the")),
        _Use("name", _Hit(7, 13), _Meaning("desk lamp", "entity", "light.desk_lamp")),
        _Use("number", _Hit(18, 20), _Meaning("fifty", value=Decimal(50))),
        _Use("require", _Hit(22, 24), _Meaning("percent")),
    ]
    assert corrected.written_over(words, spans, 0.08, NAMES) == "set the Desk Lamp to 50 percent."
    # A name the transducer heard nothing of goes where it was said; a
    # number with its unit is left as it was said.
    gap = [("Turn", 0.16, 0.32), ("on", 0.4, 0.48), ("the", 0.56, 0.64)]
    assert corrected.written_over(gap, [NANOLEAF], 0.08, NAMES) == "Turn on the Nanoleaf"
    minutes = _Use("number", _Hit(5, 15), _Meaning("a minute and a half", value=Decimal("1.5"), unit="minute"))
    assert corrected.written_over(WORDS, [minutes], 0.08, NAMES) == "Turn on the Neno leave."
    # A word a frame before a name, which isn't one of its words and ends
    # where it starts, isn't the name's ("the", when no filler was heard).
    assert corrected.written_over(WORDS, [NANOLEAF], 0.08, NAMES) == "Turn on the Nanoleaf."


def test_a_name_is_written_as_it_was_said():
    """The door heard by its name in the garage, after "garage": not its whole
    house name, which says the garage again -- each word the name's, however
    the two heads timed it: "side" a frame before the matcher's span, "door"
    running on into the silence, or after it."""
    garage = _Use("name", _Hit(21, 24), _Meaning("garage", "area", "garage"))
    door = _Use("name", _Hit(27, 32), _Meaning("side door", "entity", "lock.garage_side_door"))
    unlock = _Use("require", _Hit(14, 18), _Meaning("unlock"))
    for words in (
        [("Unlock", 1.12, 1.52), ("garage", 1.68, 2.0), ("side", 2.08, 2.16), ("door.", 2.32, 3.68)],
        [("Unlock", 1.12, 1.52), ("garage", 1.68, 2.0), ("side", 2.16, 2.4), ("door.", 2.64, 3.5)],
    ):
        assert corrected.written_over(words, [unlock, garage, door], 0.08, NAMES) == "Unlock Garage Side Door."


def test_slots_compare_as_they_run():
    assert corrected._slots({"entity": ["b", "a"], "item": "Oat milk.", "n": 50}) == corrected._slots(
        {"entity": ["a", "b"], "item": "oat milk", "n": 50.0}
    )


@pytest.fixture(name="engine", scope="module")
def fixture_engine():
    languages.use("en")
    ctc = languages.current().ctc_dir(ROOT)
    tdt = tdt_models(ROOT).get("own")
    if not ctc.is_dir() or tdt is None or not tdt.is_dir() or not VOICE.exists():
        pytest.skip("needs the models")
    engine = Engine(ctc, tdt, None, threads=2)
    engine.build(compile_store({
        "names": {
            "areas": {"garage": {"name": "Garage"}},
            "entities": {
                "light.nanoleaf": {"name": "Nanoleaf"}, "light.govee": {"name": "Govee Strip"},
                "lock.garage_side_door": {"name": "Side Door", "house": ["Garage Side Door"]},
            },
        },
        "commands": {
            "unlock": {
                "description": "unlock a lock",
                "require": [["unlock"]],
                "optional": [["in"]],
                "names": {"entity": {"count": 1}, "area": {"count": 1}},
                "combinations": [["entity"], ["entity", "area"]],
                "action": {"action": "lock.unlock", "target": "{{ target }}"},
            },
            "device_on": {
                "description": "turn on a device",
                "require": [["turn on", "turn ... on", "switch on"]],
                "names": {"entity": {"count": 1}},
                "combinations": [["entity"]],
                "action": {"action": "homeassistant.turn_on", "target": "{{ target }}"},
            },
            "what_time": {"description": "tell the time", "examples": ["what time is it"], "require": [["what time"]]},
        },
    }))
    engine.entity_area = {"lock.garage_side_door": "garage"}.get
    return engine


def said(text: str) -> np.ndarray:
    """``text`` as Piper says it (without its randomness), at 16 kHz."""
    voice = load_voice(VOICE)
    chunks = list(voice.synthesize(text, syn_config=SynthesisConfig(noise_scale=0.0, noise_w_scale=0.0)))
    audio = np.concatenate([np.frombuffer(c.audio_int16_bytes, dtype="<i2") for c in chunks]).astype(np.float32) / 32768
    return soxr.resample(audio, chunks[0].sample_rate, 16000)


@pytest.mark.parametrize("text,name", [("turn on the nanoleaf", "Nanoleaf"), ("switch on the govee strip", "Govee Strip")])
def test_the_home_s_names_are_spelled_as_they_are(engine, text, name):
    satellite = Satellite("s", "Somewhere")
    audio = said(text)
    decision = engine.recognize_command(audio, satellite)
    assert decision.command is not None and decision.command.id == "device_on"
    transcript, words = engine.transcriber.transcribe_words(audio)
    names = {"entities": {"light.nanoleaf": ["Nanoleaf"], "light.govee": ["Govee Strip"]}}
    text_out, how = corrected.correct(
        decision, transcript, words, engine.model.frame_seconds, names, lambda t: engine.recognize_text(t, satellite)
    )
    assert name in text_out and how["as"] in ("names", "heard")
    assert corrected.same(decision, engine.recognize_text(text_out, satellite))


def test_a_name_said_with_its_area(engine):
    """The door by its name in the garage, the garage said first: written so,
    not as its whole house name after "Garage" -- which, read back, runs the
    same, but leaves a word unexplained."""
    satellite = Satellite("s", "Somewhere")
    audio = said("unlock garage side door")
    decision = engine.recognize_command(audio, satellite)
    assert decision.command.id == "unlock" and decision.slots["entity"] == ["lock.garage_side_door"]
    transcript, words = engine.transcriber.transcribe_words(audio)
    names = {"entities": {"lock.garage_side_door": ["Side Door", "Garage Side Door"]}, "areas": {"garage": ["Garage"]}}
    text, how = corrected.correct(
        decision, transcript, words, engine.model.frame_seconds, names, lambda t: engine.recognize_text(t, satellite)
    )
    assert text.rstrip(".") == "Unlock Garage Side Door" and how["as"] == "names"
    assert not corrected.same(decision, engine.recognize_text("Unlock Garage Garage Side Door door.", satellite))


def test_nothing_recognized_is_what_was_heard(engine):
    satellite = Satellite("s", "Somewhere")
    audio = said("the quick brown fox")
    decision = engine.recognize_command(audio, satellite)
    transcript, words = engine.transcriber.transcribe_words(audio)
    assert decision.command is None
    out, how = corrected.correct(decision, transcript, words, 0.08, {}, lambda t: engine.recognize_text(t, satellite))
    assert (out, how) == (transcript, {"as": "heard"})


# ------------------------------------------------------------------ the app


COMMANDS = """\
names:
  areas:
    kitchen: {name: Kitchen}
    office: {name: Office}
  entities:
    light.nanoleaf: {name: Nanoleaf}
commands:
  lights_on:
    description: turn on the lights
    require: [[turn on, switch on, turn ... on, switch ... on], [light, lights]]
    optional: [[all], [in]]
    names:
      area: {count: 1}
    combinations: [[], [area]]
    action:
      action: light.turn_on
      target: "{{ target }}"
    verbosity: beep
    response: Turned on the lights.
  device_on:
    description: turn on a device
    require: [[turn on, turn ... on]]
    names:
      entity: {count: 1}
    combinations: [[entity]]
    action:
      action: homeassistant.turn_on
      target: "{{ target }}"
    response: Turned on {{ slots.entity | join }}.
  unlock:
    description: unlock the door
    require: [[unlock], [door]]
    confirm: true
    response:
      confirm: Unlock the door?
      done: Unlocked.
    action:
      action: lock.unlock
      target: {entity_id: lock.front_door}
  start_timer:
    description: set a timer
    require: [[timer, time], [minutes, minute, seconds, second, hours, hour]]
    optional: [[set, start, create], [for], [named, called, name]]
    number: duration
    combinations: [[duration]]
    timer: start
"""
KITCHEN = {"device_id": fake_hass.ASSIST_SATELLITE["id"], "satellite_id": fake_hass.ASSIST_SATELLITE["entity_id"]}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def ask(port: int, *events, until=None) -> list:
    """Send ``events`` to the server on ``port``; what comes back, until
    ``until`` says the last one is the end."""
    async with AsyncTcpClient("127.0.0.1", port) as client:
        for event in events:
            await client.write_event(event.event())
        got = []
        while True:
            event = await asyncio.wait_for(client.read_event(), 60)
            assert event is not None
            got.append(event)
            if until(event):
                return got


async def reply(port: int, text: str, **context: Any) -> list:
    done = (Handled.is_type, NotHandled.is_type)

    def ended(event) -> bool:
        return HandledStop.is_type(event.type) if streaming[0] else any(d(event.type) for d in done)

    streaming = [False]

    def until(event) -> bool:
        if HandledStart.is_type(event.type):
            streaming[0] = True
        return ended(event)

    return await ask(port, Transcript(text, context=context or None), until=until)


async def transcribe(port: int, audio: np.ndarray, context: dict | None = None) -> str:
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()
    chunks = [AudioChunk(rate=16000, width=2, channels=1, audio=pcm[i : i + 2048]) for i in range(0, len(pcm), 2048)]
    events = await ask(
        port, Transcribe(language="en", context=context), AudioStart(rate=16000, width=2, channels=1), *chunks, AudioStop(),
        until=lambda e: Transcript.is_type(e.type),
    )
    return Transcript.from_event(events[-1]).text


@needs_models
def test_home_assistant_s_pipelines(tmp_path):
    asyncio.run(_pipelines(tmp_path))


async def _pipelines(tmp_path: Path) -> None:
    hass, hass_server = await fake_hass.serve()
    rest, rest_port = await fake_hass.serve_rest(hass)
    hass_port = hass_server.sockets[0].getsockname()[1]
    (tmp_path / "commands.yaml").write_text(COMMANDS)
    port = free_port()
    app = VoiceLabsApp(Options(
        hass_url=f"http://127.0.0.1:{rest_port}", hass_websocket_url=f"ws://127.0.0.1:{hass_port}/api/websocket",
        hass_token=fake_hass.TOKEN, data_dir=tmp_path, http_port=free_port(),
        speaker_profiles=tmp_path / "speakers.json", threads=4, wyoming_port=port,
    ))
    await app.start()
    try:
        await app.hass.wait_connected(10)
        await app.hass.refresh_catalog()

        # What it offers.
        [info] = await ask(port, Describe(), until=lambda e: True)
        info = Info.from_event(info)
        assert [a.name for a in info.asr] == ["Voice Labs"] and info.asr[0].models[0].languages == ["en"]
        assert info.handle[0].supports_handled_streaming and info.handle[0].supports_home_control
        tts = info.tts[0]
        assert tts.supports_synthesize_streaming and len(tts.voices) == len(catalog())
        assert all(v.installed for v in tts.voices)  # downloaded when asked for

        # A command, in the satellite's area; one that beeps says its brief response.
        [handled] = await reply(port, "turn on the lights", conversation_id="c1", **KITCHEN)
        assert Handled.from_event(handled).text == "Turned on the lights."
        assert not Handled.from_event(handled).continue_conversation
        assert hass.scripts[-1]["variables"]["target"] == {"area_id": ["kitchen"]}
        record = app.calls.recent[-1]
        assert record.kind == "wyoming" and record.satellite["name"] == "Kitchen Voice"
        assert record.satellite["area_id"] == "kitchen" and record.outcome == "done"

        # Nothing understood (and no agent): not handled, and said why.
        [not_handled] = await reply(port, "the quick brown fox jumps", **KITCHEN)
        assert NotHandled.is_type(not_handled.type) and NotHandled.from_event(not_handled).text

        # A command that asks first; its answer comes in the same conversation.
        [asked] = await reply(port, "unlock the door", conversation_id="c2", **KITCHEN)
        assert Handled.from_event(asked).text == "Unlock the door?" and Handled.from_event(asked).continue_conversation
        scripts = len(hass.scripts)
        [done] = await reply(port, "yes", conversation_id="c2", **KITCHEN)
        assert Handled.from_event(done).text == "Unlocked." and len(hass.scripts) == scripts + 1
        # Asked once: "yes" again is nothing.
        [again] = await reply(port, "yes", conversation_id="c2", **KITCHEN)
        assert NotHandled.is_type(again.type)

        # A timer is Home Assistant's own agent's, to ring on its satellite.
        [timer] = await reply(port, "set a timer for five minutes", **KITCHEN)
        sent = hass.conversations[-1]
        assert sent["agent_id"] == "conversation.home_assistant" and sent["device_id"] == KITCHEN["device_id"]
        assert Handled.from_event(timer).text == "conversation.home_assistant heard set a timer for five minutes"

        # The conversation agent's answer is streamed as it comes, and its
        # question back keeps the conversation going.
        app.store.settings["agent"] = "conversation.chatty"
        app.configure_agent()
        hass.asks_back = True
        events = await reply(port, "what is the tallest mountain", conversation_id="c3", **KITCHEN)
        assert HandledStart.is_type(events[0].type) and HandledStop.is_type(events[-1].type)
        pieces = "".join(HandledChunk.from_event(e).text for e in events if HandledChunk.is_type(e.type))
        handled = Handled.from_event(events[-2])
        assert handled.text == pieces == "conversation.chatty heard what is the tallest mountain. Anything else?"
        assert handled.continue_conversation
        await reply(port, "no thanks", conversation_id="c3", **KITCHEN)
        assert app.calls.recent[-1].stages[-2]["stage"] == "answer"  # the agent's question, answered
        hass.asks_back = False
        app.store.settings["agent"] = None
        app.configure_agent()

        # Speech: the voice replies are said in, all at once ...
        events = await ask(port, Synthesize("Hello there. How are you?"), until=lambda e: AudioStop.is_type(e.type))
        start = AudioStart.from_event(events[0])
        audio = b"".join(AudioChunk.from_event(e).audio for e in events if AudioChunk.is_type(e.type))
        assert start.rate == 22050 and len(audio) / (2 * start.rate) > 1.0
        # ... and another, its text in pieces: a sentence at a time.
        voice = SynthesizeVoice(name=OTHER_VOICE)
        events = await ask(
            port, SynthesizeStart(voice=voice), SynthesizeChunk("Hello the"), SynthesizeChunk("re. How are"),
            SynthesizeChunk(" you?"), Synthesize("Hello there. How are you?", voice=voice), SynthesizeStop(),
            until=lambda e: SynthesizeStopped.is_type(e.type),
        )
        assert sum(AudioStart.is_type(e.type) for e in events) == 2 == sum(AudioStop.is_type(e.type) for e in events)
        assert OTHER_VOICE in json.loads((tmp_path / "piper_used.json").read_text())

        # Speech-to-text, with the home's names; the conversation agent then
        # hears the audio again, as the satellite's.
        command = said("turn on the nanoleaf")
        text = await transcribe(port, command, KITCHEN)
        assert "Nanoleaf" in text
        transcribed = app.calls.recent[-1]
        assert transcribed.outcome == "transcribed"
        assert next(s for s in transcribed.stages if s["stage"] == "transcript")["text"] == text
        [handled] = await reply(port, text, **KITCHEN)
        assert Handled.from_event(handled).text == "Turned on light.nanoleaf."
        recognition = next(s for s in app.calls.recent[-1].stages if s["stage"] == "recognition")
        assert recognition["heard"] == transcribed.id
        # Heard once: the same words again are read as words.
        await reply(port, text, **KITCHEN)
        assert "heard" not in next(s for s in app.calls.recent[-1].stages if s["stage"] == "recognition")
        assert hass.stt_requests == []  # no STT entity chosen

        # With the STT entity the app hears with, "When needed": asked when
        # nothing here makes out the words ...
        app.store.settings["stt"] = "stt.fake_whisper"
        hass.stt_text = "The quick brown fox."
        assert await transcribe(port, said("the quick brown fox"), KITCHEN) == "The quick brown fox."
        assert hass.stt_requests[-1]["entity_id"] == "stt.fake_whisper"
        stt = next(s for s in app.calls.recent[-1].stages if s["stage"] == "recognition")["stt"]
        assert stt["asked"] and stt["text"] == "The quick brown fox."
        # ... and not for a command the audio made out: the TDT head's words.
        asked = len(hass.stt_requests)
        hass.stt_text = "Turn on Nanoleaf."
        assert await transcribe(port, command, KITCHEN) == text and len(hass.stt_requests) == asked
        transcribed = app.calls.recent[-1]
        assert not next(s for s in transcribed.stages if s["stage"] == "recognition")["stt"]["asked"]
        # The conversation agent then has Home Assistant's words, not the entity's.
        await reply(port, text, **KITCHEN)
        recognition = next(s for s in app.calls.recent[-1].stages if s["stage"] == "recognition")
        assert recognition["heard"] == transcribed.id and recognition["from_home_assistant"]
        assert recognition["text_source"] == "home_assistant" and recognition["recognition"]["tdt_transcript"] == text
        # "Always, as you speak": it hears every command as it comes, and its
        # words are tried first -- as they are, when they read back ...
        app.store.settings["stt_always"] = True
        assert await transcribe(port, command, KITCHEN) == "Turn on Nanoleaf."
        assert hass.stt_requests[-1]["bytes"] == 2 * len(command) and len(hass.stt_requests) == asked + 1
        stt = next(s for s in app.calls.recent[-1].stages if s["stage"] == "recognition")["stt"]
        assert stt["entity"] == "stt.fake_whisper" and stt["streamed"]["seconds"] == pytest.approx(len(command) / 16000, abs=0.01)
        # ... else the TDT head's, with the names written over them.
        hass.stt_text = "Turn on the lamp."
        text = await transcribe(port, command, KITCHEN)
        how = next(s for s in app.calls.recent[-1].stages if s["stage"] == "transcript")
        assert "Nanoleaf" in text and how["as"] == "names" and how["tried"][0]["text"] == "Turn on the lamp."
        app.store.settings["stt_always"] = False

        # The app's own speech-to-text (an STT entity called Voice Labs) would
        # ask itself: the TDT head hears instead.
        asked = len(hass.stt_requests)
        app.store.settings["stt"] = "stt.voice_labs"
        assert "Nanoleaf" in await transcribe(port, command, KITCHEN) and len(hass.stt_requests) == asked
        named = app.assist_satellite(KITCHEN)
        app.store.settings["stt"] = "stt.mine"
        app.hass.catalog.stt_names["stt.mine"] = "Voice Labs"
        assert app.stt_entity(app.assist_satellite(KITCHEN)) is None and named.voice.stt == "stt.voice_labs"
    finally:
        await app.stop()
        await rest.cleanup()
        hass_server.close()
