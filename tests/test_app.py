"""The whole app on recorded Voice PE calls: a fake satellite places them, a
fake Home Assistant receives what the commands do.

Needs the models and the recordings in calls/; skipped without them.
"""

from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path

import aiohttp
import pytest

from voice_labs import people as people_mod
from voice_labs.server import Options, VoiceLabsApp
from voice_labs.store import styled

from . import fake_hass
from .fake_satellite import FakeSatellite, read_call, serve

ROOT = Path(__file__).resolve().parent.parent
CALLS = ROOT / "calls"
MAC = "aabbccddeeff"

pytestmark = pytest.mark.skipif(
    not (ROOT / "models/speech-to-phrase").exists() or not (CALLS / "call-15788.wav").exists(),
    reason="needs the models and recorded calls",
)

COMMANDS = """\
names:
  areas:
    kitchen: {name: Kitchen}
    bedroom: {name: Bedroom}
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
    response: Turned on the lights.
  set_brightness:
    description: set the brightness of the lights
    require: [[brightness, percent]]
    optional: [[set, change, make], [light, lights], [to], [in], [brightness], [percent]]
    names:
      area: {count: 1}
    number: percentage
    combinations: [[percentage], [area, percentage]]
    action:
      action: light.turn_on
      target: "{{ target }}"
      data: {brightness_pct: "{{ slots.percentage }}"}
  what_time:
    description: tell the time
    examples: [what time is it]
    require: [[what time]]
    response: "It's time."
  start_timer:
    description: set a timer
    require: [[timer, time], [minutes, minute, seconds, second, hours, hour]]
    optional: [[set, start, create], [for], [named, called, name]]
    wildcard: {slot: timer, before: [timer, time], after: [named, called, name]}
    number: duration
    combinations: [[duration], [duration, timer]]
    timer: start
"""


async def wait_for(condition, timeout: float = 20.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        assert asyncio.get_running_loop().time() < deadline, "timed out waiting"
        await asyncio.sleep(0.05)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_calls_end_to_end(tmp_path):
    asyncio.run(_scenario(tmp_path))


async def _scenario(tmp_path: Path) -> None:
    hass, hass_server = await fake_hass.serve()
    hass_port = hass_server.sockets[0].getsockname()[1]
    satellite = FakeSatellite(mac=":".join(MAC[i : i + 2] for i in range(0, 12, 2)), speed=8.0)
    satellite_server = await serve(satellite)
    satellite_port = satellite_server.sockets[0].getsockname()[1]

    # The satellite's area is its Home Assistant device's (Voice Labs integration).
    hass.device_areas[f"device-{MAC}"] = "kitchen"
    (tmp_path / "commands.yaml").write_text(COMMANDS)
    (tmp_path / "satellites.yaml").write_text(f"{MAC}: {{name: Kitchen VPE, area: office}}\n")
    http_port = free_port()
    app = VoiceLabsApp(
        Options(
            host="127.0.0.1",
            port=satellite_port,
            hass_url=f"http://127.0.0.1:{hass_port}",
            hass_token=fake_hass.TOKEN,
            data_dir=tmp_path,
            http_port=http_port,
            speaker_profiles=tmp_path / "speakers.json",
            threads=4,
        )
    )
    await app.start()
    runner = asyncio.ensure_future(app.run_forever())
    try:
        await asyncio.wait_for(satellite.ready.wait(), 30)

        # "turn on the lights": the satellite's area is the target.
        detection = {"probability": 230, "max_probability": 255, "cutoff": 51}
        assert await satellite.call(read_call(CALLS / "call-15788.wav"), detection=detection) == "done"
        script = hass.scripts[-1]
        assert script["sequence"] == [{"action": "light.turn_on", "target": "{{ target }}"}]
        variables = script["variables"]
        assert variables["target"] == {"area_id": ["kitchen"]}
        assert variables["satellite"]["area_id"] == "kitchen"
        assert variables["satellite"]["floor_id"] == "ground"
        assert variables["command"]["id"] == "lights_on"
        assert hass.events[-1]["event_type"] == "voice_labs_command"
        assert hass.events[-1]["data"]["response"] == "Turned on the lights."
        assert any(e["type"] == "recognized" for e in satellite.events)
        assert satellite.played > 0

        record = app.calls.recent[-1]
        stages = [s["stage"] for s in record.stages]
        assert stages[:2] == ["call_start", "wake_kaldi"]
        # What the wake word came in with, 0-1, also in the list.
        start = record.stages[0]
        assert (start["probability"], start["max_probability"], start["cutoff"]) == (0.902, 1.0, 0.2)
        assert start["name"] == "Okay Nabu"
        assert start["aec_lead"] == 905  # how the captures line up, for a replay
        assert app.calls.list()[0]["wake"]["probability"] == 0.902
        # The list also says what was heard, what ran and what was said.
        listed = app.calls.list()[0]
        assert "lights" in listed["heard"]
        assert (listed["command"], listed["said"]) == ("lights_on", "Turned on the lights.")
        assert {"split", "recognition", "action", "speech"} <= set(stages)
        # Nobody in the home: no voice to identify, and nobody speaking.
        assert "speaker" not in stages
        assert variables["speaker"] == {"name": None, "score": None, "by": None, "todo_list": None, "calendar": None, "playlist": None}
        assert variables["wake_word"] == {"id": "okay_nabu", "name": "Okay Nabu", "phrase": "okay nabu"}  # what woke it
        split = next(s for s in record.stages if s["stage"] == "split")
        # The CTC evidence found the wake phrase again, and the command starts after it.
        assert split["phrase"] is not None
        assert split["cut"] >= split["wake_end"] - 0.01
        # How long each step took.
        by_stage = {s["stage"]: s for s in record.stages}
        assert by_stage["wake_kaldi"]["decode_ms"] > 0
        assert split["seconds"] > 0
        assert by_stage["recognition"]["timings"] and by_stage["action"]["seconds"] > 0
        assert by_stage["speech"]["synthesis"] > 0 and by_stage["speech"]["seconds"] > 0  # Piper's
        assert not record.kept  # no debugging: no audio on disk

        # "set kitchen brightness to fifty percent": a named area and a number.
        assert await satellite.call(read_call(CALLS / "call-16773.wav")) == "done"
        variables = hass.scripts[-1]["variables"]
        assert variables["slots"] == {"area": ["kitchen"], "percentage": 50}
        assert variables["command"]["id"] == "set_brightness"

        # A debug session in dry run: nothing reaches Home Assistant, the
        # audio and trace are kept.
        app.calls.start_session(MAC, dry_run=True)
        scripts = len(hass.scripts)
        assert await satellite.call(read_call(CALLS / "call-15788.wav")) == "done"
        assert len(hass.scripts) == scripts
        record = app.calls.recent[-1]
        assert record.debug_session and record.dry_run and record.kept
        assert set(record.audio) == {"call", "call-aec", "command"}
        assert (tmp_path / "calls" / record.id / "trace.json").exists()
        action = next(s for s in record.stages if s["stage"] == "action")
        assert action["dry_run"] and action["sequence"]
        app.calls.stop_session(MAC)

        # A name nobody gave: not recognized, and said why.
        assert await satellite.call(read_call(CALLS / "call-19757.wav")) == "not_recognized"
        recognition = next(s for s in app.calls.recent[-1].stages if s["stage"] == "recognition")
        assert "garage" in recognition["message"]

        async with aiohttp.ClientSession(f"http://127.0.0.1:{http_port}") as http:
            status = await (await http.get("/api/status")).json()
            assert status["hass"] and status["commands"] == 4
            assert status["integration"] == "ready"
            satellites = (await (await http.get("/api/satellites")).json())["satellites"]
            assert satellites[0]["area"] == "kitchen"  # the device's, not satellites.yaml's
            assert satellites[0]["timer_list"] == f"voice_timer_list.{MAC}_timers"
            calls = (await (await http.get("/api/calls")).json())["calls"]
            assert len(calls) == 4 and calls[0]["outcome"] == "not recognized"
            kept = next(c for c in calls if c["kept"])
            audio = await http.get(f"/api/calls/{kept['id']}/audio/call")
            assert audio.status == 200 and audio.headers["Content-Type"] == "audio/wav"

            # Whose voice it was: into a new speaker, then the same one again.
            listed = await (await http.get("/api/speakers")).json()
            assert listed["enabled"] and listed["speakers"] == [] and listed["enrollment"] is None
            added = await (await http.post(f"/api/calls/{kept['id']}/speaker", json={"name": "mike"})).json()
            assert added["ok"] and added["name"] == "Mike" and added["learned"] == 0 and added["speech_s"] > 1
            added = await (await http.post(f"/api/calls/{kept['id']}/speaker", json={"name": "MIKE"})).json()
            assert added["name"] == "Mike" and added["learned"] == 1
            assert [s["name"] for s in (await (await http.get("/api/speakers")).json())["speakers"]] == ["Mike"]
            # One speaker enrolled: nobody is second, so there is no margin
            # -- and the trace is JSON a browser can read.
            app.calls.start_session(MAC, dry_run=True)
            assert await satellite.call(read_call(CALLS / "call-15788.wav")) == "done"
            app.calls.stop_session(MAC)
            heard = app.calls.recent[-1]
            assert next(s for s in heard.stages if s["stage"] == "speaker")["margin"] is None
            heard.stage("saved_before", margin=float("inf"))  # as traces saved before were
            strict = lambda constant: pytest.fail(f"{constant} is not JSON")  # noqa: E731
            trace = json.loads(await (await http.get(f"/api/calls/{heard.id}")).text(), parse_constant=strict)
            assert trace["stages"][-1]["margin"] is None
            json.loads(await (await http.get("/api/calls")).text(), parse_constant=strict)
            unkept = next(c for c in calls if not c["kept"])
            assert (await http.post(f"/api/calls/{unkept['id']}/speaker", json={"name": "Mike"})).status == 404
            assert (await http.post(f"/api/calls/{kept['id']}/speaker", json={"name": " "})).status == 400
            app.speakers.profiles.speakers.clear()  # nobody, for the rest

            # Replay the kept call through the current commands, as a dry run.
            replayed = await (await http.post(f"/api/calls/{kept['id']}/replay", json={})).json()
            assert replayed["kind"] == "replay" and replayed["outcome"] == "dry run"

            # Edit a command from the API: the next call uses it.
            spec = (await (await http.get("/api/commands/lights_on")).json())["spec"]
            spec["response"] = "Lights on in {{ satellite.area_name }}."
            saved = await http.put("/api/commands/lights_on", json={"spec": spec})
            assert (await saved.json())["ok"]
            bad = await http.put("/api/commands/lights_on", json={"spec": {"description": "x"}})
            assert bad.status == 400 and "needs examples or keywords" in (await bad.json())["error"]

            tried = await (await http.post("/api/try", json={"text": "turn on the lights", "satellite": MAC})).json()
            speech = next(s for s in tried["stages"] if s["stage"] == "speech")
            assert speech["text"] == "Lights on in Kitchen."
            # Typed: read as heard word for word, no speech recognition, no audio.
            recognition = next(s for s in tried["stages"] if s["stage"] == "recognition")
            assert recognition["text_source"] == "typed" and recognition["recognition"]["tdt_transcript"] == "turn on the lights"
            assert recognition["recognition"]["transcript"] == "turn on the lights" and not tried["audio"]
            record = await app.try_text("What is 50°F in Celsius?", MAC)
            assert next(s for s in record.stages if s["stage"] == "speech")["text"] == "It's 10 degrees Celsius."
            # Said, when asked to be.
            spoken_try = await (await http.post("/api/try", json={"text": "turn on the lights", "satellite": MAC, "speak": True})).json()
            assert next(s for s in spoken_try["stages"] if s["stage"] == "recognition")["text_source"] == "TDT"
            assert tried["dry_run"]
            assert len(hass.scripts) == scripts  # nothing ran since the debug session started

            # The whole home's commands, or an area's own: the satellite's
            # area's own goes first there; another area's isn't heard, nor the
            # whole home's turned off in its area.
            async def tried_command(text, area=None):
                body = {"text": text, "satellite": MAC} if area is None else {"text": text, "area": area}
                record = await (await http.post("/api/try", json=body)).json()
                return next(s for s in record["stages"] if s["stage"] == "recognition")

            copied = await (await http.post("/api/commands/what_time/duplicate", json={"new_id": "kids_time"})).json()
            assert copied["ok"]
            kids_time = {**app.store.document()["commands"]["kids_time"], "area": "kitchen"}
            assert (await http.put("/api/commands/kids_time", json={"spec": kids_time})).status == 200
            assert app.engine.compiled.commands["kids_time"].area == "kitchen"
            store_data = await (await http.get("/api/store")).json()
            assert store_data["details"]["kids_time"]["area"] == "kitchen" and store_data["details"]["what_time"]["area"] is None
            assert (await tried_command("what time is it"))["command"] == "kids_time"
            assert (await tried_command("what time is it", "office"))["command"] == "what_time"
            assert (await http.put("/api/areas/kitchen/off", json={"commands": ["lights_on"], "off": True})).status == 200
            assert app.store.document()["commands"]["lights_on"]["off"] == ["kitchen"]
            recognition = await tried_command("turn on the lights")
            assert recognition["command"] is None
            assert recognition["skipped"][0] == {"command": "lights_on", "why": "turned off in this area"}
            assert recognition["message"] == "That command isn't available in the Kitchen."
            assert (await tried_command("turn on the lights", "office"))["command"] == "lights_on"
            # Only the whole home's are turned off in areas.
            assert (await http.put("/api/areas/kitchen/off", json={"commands": ["kids_time"], "off": True})).status == 400
            assert (await http.put("/api/areas/kitchen/off", json={"commands": ["lights_on"], "off": False})).status == 200
            assert "off" not in app.store.document()["commands"]["lights_on"]
            assert (await tried_command("turn on the lights"))["command"] == "lights_on"
            assert (await http.delete("/api/commands/kids_time")).status == 200
            assert (await http.put(f"/api/satellites/{MAC}", json={"icon": "mdi:sofa"})).status == 200
            listed = next(s for s in (await (await http.get("/api/satellites")).json())["satellites"] if s["key"] == MAC)
            assert listed["icon"] == "mdi:sofa" and "home" not in listed
            assert (await http.put(f"/api/satellites/{MAC}", json={"icon": "sofa"})).status == 400
            assert (await http.put(f"/api/satellites/{MAC}", json={"icon": None})).status == 200
            assert "icon" not in app.store.satellites[MAC]

            # Commands chosen for the fallback steps; meaning can't take one
            # that needs something said.
            assert (await http.put("/api/fallback", json={"meaning": ["what_time"], "llm": ["lights_on"]})).status == 200
            store_data = await (await http.get("/api/store")).json()
            assert store_data["commands"]["what_time"]["fallback"] == ["meaning"]
            assert store_data["commands"]["lights_on"]["fallback"] == ["llm"]
            assert store_data["details"]["what_time"]["needs_said"] is False
            fallback = (await (await http.get("/api/status")).json())["fallback"]
            assert fallback["meaning_commands"] == 1 and fallback["llm_commands"] == 1
            assert fallback["model"] == "gliner2.5-small-v1" and fallback["threshold"] == 0.3
            assert list(fallback["models"]) == ["gliner2.5-small-v1"]  # GLiNER only
            assert (await http.put("/api/settings", json={"fallback_model": "granite-4.0-350m"})).status == 400
            assert (await http.put("/api/fallback", json={"llm": ["nope"]})).status == 400
            assert (await http.put("/api/settings", json={"fallback_model": "nope"})).status == 400
            assert (await http.put("/api/settings", json={"fallback_threshold": 1.5})).status == 400
            assert (await http.put("/api/settings", json={"fallback_threshold": 0.45})).status == 200
            assert (await (await http.get("/api/status")).json())["fallback"]["threshold"] == 0.45
            # What a command that beeps says when it acted out of sight.
            status = await (await http.get("/api/status")).json()
            assert status["verbosity"] == {"levels": ["silent", "beep", "brief", "specific", "verbose"], "beep_elsewhere": "specific",
                                           "at_most": None}
            assert (await http.put("/api/settings", json={"at_most": "specific"})).status == 400
            assert (await http.put("/api/settings", json={"at_most": "brief"})).status == 200
            assert app.satellite(MAC).settings["at_most"] == "brief"
            assert (await http.put("/api/settings", json={"at_most": None})).status == 200
            assert "at_most" not in app.store.settings
            assert status["outcomes"]["timer_levels"]["start"]["brief"] == "Timer started."
            assert (await http.put("/api/settings", json={"beep_elsewhere": "loud"})).status == 400
            assert (await http.put("/api/settings", json={"beep_elsewhere": "brief"})).status == 200
            assert (await (await http.get("/api/status")).json())["verbosity"]["beep_elsewhere"] == "brief"
            assert (await http.put("/api/fallback", json={"meaning": [], "llm": []})).status == 200
            # End of speech: what is set over the options, a null for a default.
            ends = (await (await http.get("/api/status")).json())["end_of_speech"]
            assert ends["used"]["vad"] == "silero" and ends["set"] == {}
            assert ends["vads"]["micro"]["name"] == "microVAD"
            assert (await http.put("/api/settings", json={"end_of_speech": {"vad": "nope"}})).status == 400
            assert (await http.put("/api/settings", json={"end_of_speech": {"silence_threshold": 0.8}})).status == 400
            assert (await http.put("/api/settings", json={"end_of_speech": {"vad": "micro", "silence_seconds": 0.6}})).status == 200
            assert app.end_of_speech().vad == "micro" and app.end_of_speech().silence_seconds == 0.6
            assert (await http.put("/api/settings", json={"end_of_speech": {"silence_seconds": None}})).status == 200
            assert app.store.settings["end_of_speech"] == {"vad": "micro"}
            assert app.end_of_speech().silence_seconds == app.options.silence_seconds
            assert (await http.put("/api/settings", json={"end_of_speech": None})).status == 200
            assert "end_of_speech" not in app.store.settings and app.end_of_speech().vad == "silero"
            # What an STT entity hears: AGC, or AEC; every satellite's, or its own.
            assert (await http.put("/api/settings", json={"stt_audio": "loud"})).status == 400
            assert (await http.put("/api/settings", json={"stt_audio": "aec"})).status == 200
            assert app.store.settings["stt_audio"] == "aec"
            assert (await http.put(f"/api/satellites/{MAC}", json={"stt_audio": "agc"})).status == 200
            assert app.store.satellites[MAC]["stt_audio"] == "agc"
            assert (await http.put(f"/api/satellites/{MAC}", json={"stt_audio": None})).status == 200
            assert (await http.put("/api/settings", json={"stt_audio": None})).status == 200
            assert "stt_audio" not in app.store.settings and "stt_audio" not in app.store.satellites[MAC]
            # Whether it always runs: every satellite's, and one's own (false too).
            assert (await http.put("/api/settings", json={"stt_always": "yes"})).status == 400
            assert (await http.put("/api/settings", json={"stt_always": True})).status == 200
            assert app.store.settings["stt_always"] is True
            assert (await http.put(f"/api/satellites/{MAC}", json={"stt_always": False})).status == 200
            assert app.store.satellites[MAC]["stt_always"] is False
            satellites = (await (await http.get("/api/satellites")).json())["satellites"]
            assert next(s for s in satellites if s["key"] == MAC)["voice"]["stt_always"] is False
            assert (await http.put(f"/api/satellites/{MAC}", json={"stt_always": None})).status == 200
            assert (await http.put("/api/settings", json={"stt_always": False})).status == 200
            assert "stt_always" not in app.store.settings and "stt_always" not in app.store.satellites[MAC]

            # A question no command takes is answered exactly by a skill; one
            # about now or here is not a skill's.
            record = await app.try_text("what is twelve times eight", MAC)
            assert record.outcome == "answered"
            assert next(s for s in record.stages if s["stage"] == "speech")["text"] == "It's 96."
            record = await app.try_text("what are the top news headlines", MAC)
            assert record.outcome == "not recognized"

            # Skills: on every satellite (unless set), on none, or on some.
            async def said(text, satellite=MAC):
                record = await app.try_text(text, satellite)
                return next(s for s in record.stages if s["stage"] == "speech")["text"]

            listed = await (await http.get("/api/skills")).json()
            assert [s["id"] for s in listed["skills"]] == ["spelling", "dates", "clock", "units", "arithmetic"]
            math = next(s for s in listed["skills"] if s["id"] == "arithmetic")
            assert math["example"]["levels"]["verbose"] == "Twelve times eight is 96." and math["responses"] == {}

            # How much a skill says, and what: a template for a level.
            assert (await http.put("/api/skills/arithmetic", json={"verbosity": "verbose"})).status == 200
            assert await said("what is twelve times eight") == "Twelve times eight is 96."
            body = {"verbosity": "brief", "response": {"brief": "{{ answer }}, {{ speaker.name or 'friend' }}"}}
            assert (await http.put("/api/skills/arithmetic", json=body)).status == 200
            assert app.store.settings["skill_responses"]["arithmetic"] == body
            assert await said("what is twelve times eight") == "96, friend"
            record = app.calls.recent[-1]
            assert next(s for s in record.stages if s["stage"] == "skill_response")["template"] == body["response"]["brief"]
            assert (await http.put("/api/skills/arithmetic", json={"verbosity": "loud"})).status == 400
            assert (await http.put("/api/skills/arithmetic", json={"response": {"shouted": "x"}})).status == 400
            assert (await http.put("/api/skills/arithmetic", json={"verbosity": None, "response": ""})).status == 200
            assert "skill_responses" not in app.store.settings
            assert await said("what is twelve times eight") == "It's 96."
            # A skill answers everywhere, but where it is turned off: in every
            # area, or in some.
            assert math["disabled"] is False and math["off"] == []
            assert (await http.put("/api/skills/arithmetic", json={"on": False})).status == 200
            assert app.store.settings["skills"] == {"arithmetic": {"disabled": True}}
            assert await said("what is twelve times eight") != "It's 96."
            assert await said("what is twelve times eight", None) != "It's 96."
            assert (await http.put("/api/skills/arithmetic", json={"on": True})).status == 200
            assert "skills" not in app.store.settings
            assert (await http.put("/api/areas/kitchen/off", json={"skills": ["arithmetic"], "off": True})).status == 200
            assert await said("what is twelve times eight") != "It's 96."  # the satellite's area
            assert await said("what is twelve times eight", None) == "It's 96."  # in no area
            listed = await (await http.get("/api/skills")).json()
            assert next(s for s in listed["skills"] if s["id"] == "arithmetic")["off"] == ["kitchen"]
            assert (await http.put("/api/areas/kitchen/off", json={"skills": ["arithmetic"], "off": False})).status == 200
            assert "skills" not in app.store.settings
            assert (await http.put("/api/skills/nope", json={"on": False})).status == 404
            assert (await http.put("/api/skills/live", json={"on": False})).status == 404
            assert (await http.put("/api/skills/units", json={"on": "off"})).status == 400
            assert (await http.put("/api/areas/kitchen/off", json={"skills": ["nope"], "off": True})).status == 404

            # The Home page's areas: those with something in them and those
            # added, each with its own settings over the whole home's.
            places = await (await http.get("/api/areas")).json()
            kitchen = next(a for a in places["areas"] if a["id"] == "kitchen")
            assert kitchen["satellites"] == [MAC] and kitchen["floor"] == "Ground Floor" and kitchen["icon"] == "mdi:stove"
            assert "bedroom" in {a["id"] for a in places["others"]} and places["floors"][0]["name"] == "Ground Floor"
            assert (await http.put("/api/areas/bedroom", json={})).status == 200
            assert "bedroom" in {a["id"] for a in (await (await http.get("/api/areas")).json())["areas"]}
            own = {"at_most": "beep", "climate": "climate.upstairs", "sounds": False, "silence_seconds": 1.2, "instrument": "koto"}
            assert (await http.put("/api/areas/kitchen", json={"settings": own})).status == 200
            here = app.satellite(MAC).settings
            assert here["at_most"] == "beep" and here["home"]["climate"] == "climate.upstairs" and here["sounds"] is False
            assert here["instrument"] == "koto" and app.end_of_speech(MAC).silence_seconds == 1.2
            assert app.place("office").settings["sounds"] is True  # the whole home's
            for wrong in ({"at_most": "loud"}, {"climate": "light.x"}, {"wake_word": "x"}, {"voice": "nobody"}, {"instrument": "tuba"}):
                assert (await http.put("/api/areas/kitchen", json={"settings": wrong})).status == 400, wrong
            assert (await http.put("/api/areas/kitchen", json={"settings": {k: None for k in own}})).status == 200
            assert app.store.settings["areas"] == {"bedroom": {}, "kitchen": {}}
            assert (await http.delete("/api/areas/bedroom")).status == 200
            assert (await http.delete("/api/areas/kitchen")).status == 200
            assert "areas" not in app.store.settings

            # A skill's settings: edited in full, stored as what differs.
            def skill(skills, skill_id):
                return next(s for s in skills["skills"] if s["id"] == skill_id)

            assert skill(listed, "clock")["settings"] == {"hours": 12} and not skill(listed, "clock")["settings_set"]
            assert (await http.put("/api/skills/clock", json={"settings": {"hours": 24}})).status == 200
            assert app.store.settings["skill_settings"] == {"clock": {"hours": 24}}
            assert await said("what time will it be in 45 minutes") == "It'll be 17:05."
            units = skill(listed, "units")["settings"]
            edited = [u for u in units["units"] if u["name"] != "stone"]
            edited.append({"name": "nautical mile", "measures": "length", "size": 1852, "also": ["nmi"]})
            body = {"settings": {**units, "units": edited, "digits": 3}}
            assert (await http.put("/api/skills/units", json=body)).status == 200
            assert app.store.settings["skill_settings"]["units"] == {
                "units": {"stone": False, "nautical mile": {"measures": "length", "size": 1852, "also": ["nmi"]}},
                "digits": 3,
            }
            assert await said("how many feet are in a nautical mile") == "It's 6076."
            assert await said("what is 5 miles in kilometers") == "It's 8.05 kilometers."
            listed = await (await http.get("/api/skills")).json()
            assert [u["name"] for u in skill(listed, "units")["settings"]["removed"]] == ["stone"]
            assert skill(listed, "units")["example"]["text"] == "It's 4."
            # What can't be stored is refused, and says why.
            bad = {"settings": {"units": [*edited, {"name": "smoot", "measures": "length", "size": -1}]}}
            response = await http.put("/api/skills/units", json=bad)
            assert response.status == 400 and "smoot" in (await response.json())["error"]
            assert (await http.put("/api/skills/dates", json={"settings": {"holidays": [{"name": "x", "when": "someday"}]}})).status == 400
            # Null: as built in.
            for skill_id in ("clock", "units"):
                assert (await http.put(f"/api/skills/{skill_id}", json={"settings": None})).status == 200
            assert "skill_settings" not in app.store.settings
            assert await said("what time will it be in 45 minutes") == "It'll be 5:05 PM."
            # Stored settings that can't be used (edited by hand): the built-in ones.
            app.store.settings["skill_settings"] = {"clock": {"hours": 13}}
            listed = await (await http.get("/api/skills")).json()
            assert skill(listed, "clock")["settings_error"] == "hours: 12 or 24"
            app.configure_skills()
            assert await said("what time will it be in 45 minutes") == "It'll be 5:05 PM."
            del app.store.settings["skill_settings"]
            app.configure_skills()

            # People, and "my": the speaker's own to-do list, where anyone has one.
            mine = {
                "description": "add something to my to-do list", "require": [["add"], ["my to do list"]], "optional": [["to"]],
                "wildcard": {"slot": "item", "after": ["add"], "before": ["to"], "bounded": True},
                "slots": {"entity": ["todo.to_do"]}, "mine": "todo_list",
                "action": {"action": "todo.add_item", "target": {"entity_id": "{{ slots.entity }}"}, "data": {"item": "{{ slots.item }}"}},
                "response": {"done": "Added {{ slots.item }} to {{ slots.entity[0] }}.", "no_speaker": "Who's asking?"},
            }
            assert (await http.put("/api/commands/todo_add_mine", json={"spec": mine})).status == 200
            # Nobody has one of their own: the command's own, whoever speaks.
            assert await said("add milk to my to do list") == "Added milk to todo.to_do."
            # One person: it's them, without a voice.
            made = await (await http.put("/api/people/mike", json={"own": {"todo_list": "todo.mike"}})).json()
            assert made["name"] == "Mike" and app.store.people == {"Mike": {"todo_list": "todo.mike"}}
            assert json.loads((tmp_path / "people.json").read_text()) == {"Mike": {"todo_list": "todo.mike"}}
            assert await said("add milk to my to do list") == "Added milk to todo.mike."
            record = app.calls.recent[-1]
            action = next(s for s in record.stages if s["stage"] == "action")
            assert action["variables"]["speaker"] == {
                "name": "Mike", "score": None, "by": "only_person", "todo_list": "todo.mike", "calendar": None, "playlist": None,
            }
            assert not app.identifies()
            # Two: someone must be known -- by their wake word, here.
            assert (await http.put("/api/people/Anna", json={"own": {"wake_words": ["okay_nabu"]}})).status == 200
            assert await said("add milk to my to do list") == "Who's asking?"
            anna = app.speaking(None, "okay_nabu")
            assert (anna.name, anna.by) == ("Anna", "wake_word") and app.speaking(None, "hey_jarvis").name is None
            people = (await (await http.get("/api/speakers")).json())["people"]
            assert [(p["name"], p["own"], p["voice"]) for p in people] == [
                ("Anna", {"wake_words": ["okay_nabu"]}, None), ("Mike", {"todo_list": "todo.mike"}, None),
            ]
            # Their voice for replies; the satellite's otherwise.
            assert (await http.put("/api/people/Anna", json={"own": {"tts": "tts.cloud", "tts_voice": "JennyNeural"}})).status == 200
            kitchen = app.satellite(MAC)
            assert app.voice_for(kitchen, app.speaking(None, None)).voice == kitchen.voice  # nobody known
            voiced = app.voice_for(kitchen, people_mod.speaking(app.store.people, [], "Anna")).voice
            assert (voiced.tts, voiced.tts_voice, voiced.tts_language) == ("tts.cloud", "JennyNeural", None)
            # Renamed, their things go with them; refused things say why.
            assert (await http.put("/api/people/anna", json={"own": {}, "name": "Ann"})).status == 200
            assert set(app.store.people) == {"Mike", "Ann"} and app.store.people["Ann"] == {}
            response = await http.put("/api/people/Ann", json={"own": {"todo_list": "calendar.ann"}})
            assert response.status == 400 and "todo entity" in (await response.json())["error"]
            assert (await http.delete("/api/people/Ann")).status == 200
            assert (await http.delete("/api/people/Ann")).status == 404
            assert (await http.delete("/api/people/Mike")).status == 200 and app.store.people == {}
            assert (await http.delete("/api/commands/todo_add_mine")).status == 200

            # An item on a to-do list, as the list has it: "test three" is "test 3".
            remove = {
                "description": "take something off the shopping list", "require": [["remove"], ["shopping list"]],
                "optional": [["from"]], "wildcard": {"slot": "item", "after": ["remove"], "before": ["from"], "bounded": True},
                "on_list": {"item": "todo.shopping_list"},
                "action": {"action": "todo.remove_item", "target": {"entity_id": "todo.shopping_list"}, "data": {"item": "{{ slots.item }}"}},
                "response": "Took {{ slots.item }} off.",
            }
            assert (await http.put("/api/commands/shopping_remove", json={"spec": remove})).status == 200
            hass.todo_items["todo.shopping_list"] = [{"summary": "Test 3", "status": "needs_action"}, {"summary": "Milk", "status": "completed"}]
            assert await said("remove test three from the shopping list") == "Took Test 3 off."
            assert await said("remove bread from the shopping list") == "Bread isn't on the list."
            record = app.calls.recent[-1]
            assert next(s for s in record.stages if s["stage"] == "action")["outcome"] == "not_on_list"
            # Several at once: each one on the list (the action sees them as
            # items); what isn't there is said too.
            hass.todo_items["todo.shopping_list"] += [{"summary": "Apples", "status": "needs_action"},
                                                      {"summary": "Bananas", "status": "needs_action"},
                                                      {"summary": "Mac and Cheese", "status": "needs_action"}]
            remove["action"] = {"action": "todo.remove_item", "target": {"entity_id": "todo.shopping_list"}, "data": {"item": "{{ items }}"}}
            assert (await http.put("/api/commands/shopping_remove", json={"spec": remove})).status == 200
            assert await said("remove apples and bananas from the shopping list") == "Took Apples and Bananas off."
            action = next(s for s in app.calls.recent[-1].stages if s["stage"] == "action")
            assert action["variables"]["items"] == ["Apples", "Bananas"]
            assert await said("remove mac and cheese from the shopping list") == "Took Mac and Cheese off."
            assert await said("remove apples bananas from the shopping list") == "Took Apples and Bananas off."
            assert await said("remove apples and kiwis from the shopping list") == "Took Apples off. Kiwis isn't on the list."
            assert await said("remove kiwis and figs from the shopping list") == "Kiwis and figs aren't on the list."
            assert (await http.delete("/api/commands/shopping_remove")).status == 200

            # The home's weather (Settings): what a command on it uses without its own.
            report = {
                "description": "tell the weather now", "require": [["weather"], ["report"]], "home": "weather",
                "response": "From {{ slots.entity[0] }}.",
            }
            assert (await http.put("/api/commands/weather_report", json={"spec": report})).status == 200
            assert await said("weather report") == "That isn't set up yet."
            record = app.calls.recent[-1]
            assert next(s for s in record.stages if s["stage"] == "action")["outcome"] == "not_set"
            assert (await http.put("/api/settings", json={"home": {"weather": "weather.home"}})).status == 200
            assert app.store.settings["home"] == {"weather": "weather.home"}
            assert (await (await http.get("/api/store")).json())["home"] == {"weather": "weather.home"}
            assert await said("weather report") == "From weather.home."
            # Its own implied target goes first.
            own = {**report, "slots": {"entity": ["weather.cabin"]}}
            assert (await http.put("/api/commands/weather_report", json={"spec": own})).status == 200
            assert await said("weather report") == "From weather.cabin."
            for wrong, why in (({"weather": "sensor.outside"}, "a weather entity"), ({"rain": "weather.home"}, "unknown 'rain'")):
                response = await http.put("/api/settings", json={"home": wrong})
                assert response.status == 400 and why in (await response.json())["error"]
            assert (await http.put("/api/settings", json={"home": {"weather": None}})).status == 200
            assert "home" not in app.store.settings
            assert (await http.delete("/api/commands/weather_report")).status == 200
            # "My" to-do list: the speaker's own, else the home's.
            mine = {
                "description": "add something to my to-do list", "require": [["add"], ["my to do list"]], "optional": [["to"]],
                "wildcard": {"slot": "item", "after": ["add"], "before": ["to"], "bounded": True},
                "home": "todo_list", "mine": "todo_list", "response": "Added {{ slots.item }} to {{ slots.entity[0] }}.",
            }
            assert (await http.put("/api/commands/todo_add_mine", json={"spec": mine})).status == 200
            assert (await http.put("/api/settings", json={"home": {"todo_list": "todo.house", "shopping_list": "todo.groceries"}})).status == 200
            assert app.store.settings["home"] == {"todo_list": "todo.house", "shopping_list": "todo.groceries"}
            assert await said("add milk to my to do list") == "Added milk to todo.house."
            assert (await http.put("/api/people/mike", json={"own": {"todo_list": "todo.mike"}})).status == 200
            assert await said("add milk to my to do list") == "Added milk to todo.mike."
            assert (await http.delete("/api/people/Mike")).status == 200
            assert (await http.put("/api/settings", json={"home": {"todo_list": None, "shopping_list": None}})).status == 200
            assert (await http.delete("/api/commands/todo_add_mine")).status == 200

            # The Unknown command: what is said when nothing understood it.
            assert await said("blorp the frobnicator") == "Sorry, I didn't understand that."
            template = "Sorry, I couldn't understand the sentence {{ transcript }}."
            assert (await http.put("/api/unknown", json={"response": template})).status == 200
            assert (await (await http.get("/api/store")).json())["unknown"] == {"response": template}
            assert await said("blorp the frobnicator") == "Sorry, I couldn't understand the sentence blorp the frobnicator."
            record = await app.try_text("blorp the frobnicator", MAC)
            unknown = next(s for s in record.stages if s["stage"] == "unknown")
            assert unknown["variables"]["hint"] == "" and unknown["variables"]["satellite"]["area_id"] == "kitchen"
            # Nothing rendered, or a template Home Assistant refuses: the default.
            assert (await http.put("/api/unknown", json={"response": "{% if hint %}{{ hint }}{% endif %}"})).status == 200
            assert await said("blorp the frobnicator") == "Sorry, I didn't understand that."
            assert (await http.put("/api/unknown", json={"response": "{{ nope("})).status == 200
            assert await said("blorp the frobnicator") == "Sorry, I didn't understand that."
            assert (await http.put("/api/unknown", json={"response": ""})).status == 200
            assert "unknown" not in json.loads((tmp_path / "commands.json").read_text())

            # What example templates stand for, for the editor.
            stands = (await (await http.post("/api/examples", json={"examples": ["turn on the light[s]", "(oops"]})).json())["examples"]
            assert stands == [
                {"example": "turn on the light[s]", "count": 2, "sentences": ["turn on the lights", "turn on the light"]},
                {"example": "(oops", "error": "'(' at 1 is never closed"},
            ]

            # The library: a command's versions, each added on its own.
            entries = (await (await http.get("/api/library")).json())["entries"]
            play = [e for e in entries if e["family"] == "play_music"]
            assert [(e["id"], e["variant"]) for e in play] == [
                ("play_music_here", "here"), ("play_music_device", "device"), ("play_music_place", "place")]
            added = await (await http.post("/api/library/add", json={"file": "music", "id": "play_music_device"})).json()
            assert added == {"ok": True, "id": "play_music_device", "ids": ["play_music_device"]}
            spec = (await (await http.get("/api/store")).json())["commands"]["play_music_device"]
            assert spec["title"] == "Play music on a different device" and spec["library"] == "music"
            assert spec["combinations"] == [["entity", "query"]] and spec["names"] == {"entity": {"count": 1, "domains": ["media_player"]}}
            assert not {"needs", "variants", "formerly"} & set(spec)
            assert (await http.delete("/api/commands/play_music_device")).status == 200
            # Several at once, rebuilt once; a copy of one you have gets a number.
            batch = [{"file": "timers", "id": "pause_timer"}, {"file": "timers", "id": "timer_status"}, {"file": "time", "id": "what_time"}]
            added = await (await http.post("/api/library/add", json={"entries": batch})).json()
            assert added["ids"] == ["pause_timer", "timer_status", "what_time_2"]
            assert {"pause_timer", "timer_status", "what_time_2"} <= set(app.engine.compiled.commands)
            assert (await http.post("/api/library/add", json={"entries": [{"file": "timers", "id": "nope"}]})).status == 404
            for gone in added["ids"]:
                assert (await http.delete(f"/api/commands/{gone}")).status == 200

            # A command in another language: kept, not shown, its id its own.
            def german(document):
                document["commands"]["play_music_device"] = styled(
                    {"description": "Musik abspielen", "examples": ["spiel musik"], "language": "de"})

            await app.apply(app.store.update(german))
            listed = await (await http.get("/api/store")).json()
            assert "play_music_device" not in listed["commands"] and "play_music_device" not in listed["details"]
            assert listed["hidden"] == [{"code": "de", "name": "German", "count": 1}]
            added = await (await http.post("/api/library/add", json={"file": "music", "id": "play_music_device"})).json()
            assert added["id"] == "play_music_device_en"
            for gone in ("play_music_device_en", "play_music_device"):
                assert (await http.delete(f"/api/commands/{gone}")).status == 200

            # A device's names: one of its area's another device has there,
            # or another's whole house name, is refused.
            def put(entity_id, body):
                return http.put(f"/api/names/entities/{entity_id}", json=body)

            assert (await put("light.desk_lamp", {"name": "Lamp", "house": ["Office Lamp"]})).status == 200
            bad = await put("light.bedroom_lamp", {"name": "Lamp", "house": ["Bedroom Lamp"], "area": "office"})
            assert bad.status == 400 and (await bad.json())["error"] == "“Lamp” is already Desk Lamp (light.desk_lamp)'s name in the Office"
            bad = await put("light.bedroom_lamp", {"name": "Lamp", "house": ["Office Lamp"]})
            assert bad.status == 400 and (await bad.json())["error"] == "“Office Lamp” is already Desk Lamp (light.desk_lamp)'s whole house name"
            assert (await put("light.bedroom_lamp", {"name": "Lamp", "house": ["Bedroom Lamp"], "with_area": False})).status == 200
            devices = (await (await http.get("/api/store")).json())["devices"]
            assert devices["light.bedroom_lamp"] == {
                "here": ["Lamp"], "house": ["Bedroom Lamp"], "implicit": True, "with_area": False, "area": None,
                "ha_area": "bedroom", "clashes": []}
            for gone in ("light.desk_lamp", "light.bedroom_lamp"):
                assert (await http.delete(f"/api/names/entities/{gone}")).status == 200

            # Piper's voice: one of the language's from its catalog, a speaker
            # it has, and its settings in their ranges -- the default left out;
            # tried with the settings before they are saved.
            listed = await (await http.get("/api/piper/voices")).json()
            assert listed["default"] == "en_US-kristin-medium" and "en_GB-vctk-medium" in {v["key"] for v in listed["voices"]}
            assert (await (await http.get("/api/status")).json())["piper"]["speaking"] == "en_US-kristin-medium"
            assert (await http.put("/api/settings", json={"piper": {"voice": "en_US-kristin-medium", "length_scale": 1.5}})).status == 200
            assert app.store.settings["piper"] == {"length_scale": 1.5}
            for bad in ({"voice": "de_DE-thorsten-medium"}, {"speaker": "p226"}, {"noise_scale": 9}):
                response = await http.put("/api/settings", json={"piper": bad})
                assert response.status == 400, bad
            tried = await http.post("/api/piper/try", json={"text": "Hello.", "length_scale": 1.0})
            assert tried.status == 200 and tried.content_type == "audio/wav" and (await tried.read())[:4] == b"RIFF"
            assert (await http.put("/api/settings", json={"piper": {}})).status == 200 and "piper" not in app.store.settings

            # A Home Assistant conversation agent gets what nothing here
            # understood, in a conversation kept on each satellite.
            async def agent_said(text):
                record = await app.try_text(text, MAC, run_actions=True)
                return next(s for s in record.stages if s["stage"] == "speech")["text"], record

            agents = (await (await http.get("/api/agents")).json())["agents"]
            assert [a["id"] for a in agents] == ["conversation.home_assistant", "conversation.chatty"]
            assert (await http.put("/api/settings", json={"agent": "conversation.chatty", "agent_keep_minutes": 5})).status == 200
            assert (await (await http.get("/api/status")).json())["agent"] == {"agent": "conversation.chatty", "keep_minutes": 5.0, "conversations": {}}
            text, record = await agent_said("who wrote the odyssey")
            assert text == "conversation.chatty heard who wrote the odyssey" and record.outcome == "answered"
            asked = hass.conversations[-1]
            assert asked["agent_id"] == "conversation.chatty" and "conversation_id" not in asked
            assert asked.get("device_id") == app.satellite(MAC).device_id
            # The next sentence follows on, in the same conversation ...
            await agent_said("and when was that")
            assert hass.conversations[-1]["conversation_id"] == "conversation-1"
            assert next(s for s in app.calls.recent[-1].stages if s["stage"] == "agent")["continued"]
            # ... until it has been quiet for longer than it is kept.
            app.agent.conversations[MAC].at -= 301
            await agent_said("who wrote the iliad")
            assert "conversation_id" not in hass.conversations[-1]
            # Skills answer first; a question about now or here is the agent's.
            assert (await agent_said("what is twelve times eight"))[0] == "It's 96."
            assert (await agent_said("what are the top news headlines"))[0] == "conversation.chatty heard what are the top news headlines"
            # What it doesn't understand either is the Unknown command's.
            asked = len(hass.conversations)
            assert (await agent_said("nonsense words"))[0] == "Sorry, I didn't understand that."
            assert len(hass.conversations) == asked + 1
            # A dry run never asks it: it could act in the home.
            record = await app.try_text("who wrote the odyssey", MAC)
            assert len(hass.conversations) == asked + 1
            assert next(s for s in record.stages if s["stage"] == "agent")["asked"] is False
            assert (await http.put("/api/settings", json={"agent": None})).status == 200
            assert "agent" not in app.store.settings
            assert (await http.put("/api/settings", json={"agent_keep_minutes": -1})).status == 400

            # Listening live: the satellite streams its microphone while
            # someone listens (and shows it), and its calls come through too.
            async with http.ws_connect(f"/api/satellites/{MAC}/live") as live:
                assert (await live.receive_json(timeout=5)) == {"type": "start", "sample_rate": 16000}
                await wait_for(lambda: satellite.monitoring == [True])
                listed = (await (await http.get("/api/satellites")).json())["satellites"]
                assert listed[0]["can_listen"] and listed[0]["listeners"] == 1
                audio, reports = 0, []
                while not (audio and len(reports) > 1):
                    message = await live.receive(timeout=5)
                    if message.type == aiohttp.WSMsgType.BINARY:
                        assert len(message.data) % 2 == 0
                        audio += len(message.data)
                    else:
                        reports.append(json.loads(message.data))
                # Each model's probabilities, by name, and the wake word heard
                # meanwhile (not answered: the device keeps listening).
                first = reports[0]
                assert first["type"] == "wake" and first["detected"] == ["okay_nabu"]
                (okay,) = first["models"]  # no VAD gate
                assert okay["name"] == "Okay Nabu" and okay["cutoff"] == 0.851 and 0 < okay["peak"] < 1
                assert reports[1]["detected"] == []
            await wait_for(lambda: satellite.monitoring == [True, False])
            async with http.ws_connect("/api/satellites/nobody/live") as live:
                assert (await live.receive_json(timeout=5))["type"] == "error"

        # A timer: started on the satellite's timer list, rung when it finishes,
        # stopped by "stop".
        timer_list = f"voice_timer_list.{MAC}_timers"
        assert await satellite.call(read_call(CALLS / "call-16880.wav")) == "done"
        create = next(m for m in hass.services if m["service"] == "create_timer")
        assert create["target"] == {"entity_id": timer_list}
        assert create["service_data"] == {"duration": 15}
        [timer_id] = hass.timers[timer_list]
        await wait_for(lambda: app.integration.running(MAC))
        # The ring shows it counting down.
        await wait_for(lambda: any(
            e["type"] == "timer_progress" and e["data"].get("shown") == 1 and e["data"]["total"] == 15
            for e in satellite.events
        ))
        played = satellite.played
        clears = satellite.clears  # once on connecting
        await hass.finish_timer(timer_list, timer_id)
        await wait_for(lambda: "stop" in satellite.models and satellite.played > played + 32000)
        assert any(e["type"] == "timer_ringing" for e in satellite.events)
        assert [e for e in satellite.events if e["type"] == "timer_progress"][-1]["data"] == {"shown": 0}
        assert await satellite.detect("Stop") == "timer_stopped"
        await wait_for(lambda: satellite.clears == clears + 1 and satellite.models == ["okay_nabu"])
        outcomes = [r.outcome for r in app.calls.recent]
        assert outcomes[-2:] == ["timer stopped", "timer stopped"] or outcomes[-1] == "timer stopped"

        # The center button stops one too.
        await hass._call_service(
            {
                "domain": "voice_timer_list",
                "service": "create_timer",
                "target": {"entity_id": timer_list},
                "service_data": {"duration": 30},
            }
        )
        [second] = [t for t in hass.timers[timer_list] if t != timer_id]
        await hass.finish_timer(timer_list, second)
        session = app.sessions[MAC]
        await wait_for(lambda: session.ringing is not None and not session.ringing.done() and "stop" in satellite.models)
        await satellite.device_event("stop_timer")
        await wait_for(lambda: session.ringing.done() and satellite.models == ["okay_nabu"])
        stopped = next(s for s in app.calls.recent[-1].stages if s["stage"] == "timer_stopped")
        assert stopped["by"] == "button" and app.calls.recent[-1].outcome == "timer stopped"

        # Home Assistant refuses the action: the satellite is told, the call
        # is an error.
        hass.fail_scripts = True
        assert await satellite.call(read_call(CALLS / "call-15788.wav")) == "done"
        record = app.calls.recent[-1]
        assert record.outcome == "error"
        assert next(s for s in record.stages if s["stage"] == "speech")["text"] == "Sorry, that didn't work."

        hass.fail_scripts = False
        said = read_call(CALLS / "call-15788.wav")

        # The wake word, and nothing said after it: the call ends 8 s on.
        assert await satellite.call(said[: int(1.5 * 32000)]) == "timeout"
        gave_up = next(s for s in app.calls.recent[-1].stages if s["stage"] == "timeout")
        assert gave_up["why"] == "no speech was heard" and gave_up["seconds"] == 8.0
        assert 8.0 <= gave_up["command_seconds"] < 9.0
    finally:
        runner.cancel()
        await app.stop()
        satellite_server.close()
        hass_server.close()


VOICE_COMMANDS = """\
commands:
  lights_on:
    description: turn on the lights
    require: [[turn on, switch on, turn ... on, switch ... on], [light, lights]]
    optional: [[all], [in]]
    action: {action: light.turn_on, target: "{{ target }}"}
    response: Turned on the lights.
  play_music:
    description: play music
    require: [[play]]
    wildcard: {slot: query, after: [play], to_end: true, max_words: 8}
    music: play
"""


def test_the_sounds(tmp_path):
    asyncio.run(_sounds(tmp_path))


async def _sounds(tmp_path):
    hass, hass_server = await fake_hass.serve()
    hass_port = hass_server.sockets[0].getsockname()[1]
    satellite = FakeSatellite(mac=":".join(MAC[i : i + 2] for i in range(0, 12, 2)), speed=8.0)
    satellite_server = await serve(satellite)
    hass.device_areas[f"device-{MAC}"] = "kitchen"
    (tmp_path / "commands.yaml").write_text(COMMANDS)
    http_port = free_port()
    app = VoiceLabsApp(Options(
        host="127.0.0.1", port=satellite_server.sockets[0].getsockname()[1], hass_url=f"http://127.0.0.1:{hass_port}",
        hass_token=fake_hass.TOKEN, data_dir=tmp_path, http_port=http_port, speaker_profiles=tmp_path / "speakers.json",
        threads=4,
    ))
    await app.start()
    runner = asyncio.ensure_future(app.run_forever())

    async def put(settings: dict) -> int:
        async with aiohttp.ClientSession(f"http://127.0.0.1:{http_port}") as http:
            async with http.put("/api/settings", json=settings) as response:
                return response.status

    async def call(name: str, ended: str) -> tuple[int, int]:
        """What the satellite was sent (bytes), and the reply's own."""
        played = satellite.played
        assert await satellite.call(read_call(CALLS / name)) == ended
        speech = next(s for s in app.calls.recent[-1].stages if s["stage"] == "speech")
        return satellite.played - played, round(speech["seconds"] * 32000)

    def near(sent: int, *parts: int) -> bool:
        return abs(sent - sum(parts)) <= 32  # the record's seconds are to the millisecond

    try:
        await asyncio.wait_for(satellite.ready.wait(), 30)
        speech = app.speech
        assert app.sounds() and speech.heard and speech.not_understood and speech.asking
        # Heard the moment the command ended, the reply after it in the same stream.
        sent, reply = await call("call-15788.wav", "done")
        assert reply > 0 and near(sent, len(speech.heard), reply)
        # Not understood: heard, then not understood before what is said.
        sent, reply = await call("call-19757.wav", "not_recognized")
        assert reply > 0 and near(sent, len(speech.heard), len(speech.not_understood), reply)
        # The conversation agent asked: heard, then asking, then its answer.
        assert await put({"agent": "conversation.chatty"}) == 200
        sent, reply = await call("call-19757.wav", "done")
        assert app.calls.recent[-1].outcome == "answered" and near(sent, len(speech.heard), len(speech.asking), reply)
        assert await put({"agent": None}) == 200
        # Played on the koto, from the next call on; the electric piano is the default.
        async with aiohttp.ClientSession(f"http://127.0.0.1:{http_port}") as http:
            status = await (await http.get("/api/status")).json()
        assert [i["id"] for i in status["instruments"]] == ["electric_piano", "koto"]
        assert status["instrument"] == "electric_piano" and status["instruments"][0]["name"] == "Electric piano"
        piano = speech.heard
        assert await put({"instrument": "koto"}) == 200 and app.store.settings["instrument"] == "koto"
        assert speech.heard != piano and speech.acknowledge == speech.instruments["koto"]["acknowledge"]
        sent, reply = await call("call-15788.wav", "done")
        assert near(sent, len(speech.heard), reply)
        assert await put({"instrument": "harp"}) == 400
        assert await put({"instrument": "electric_piano"}) == 200 and "instrument" not in app.store.settings
        assert speech.heard == piano
        # Turned off: the reply alone (and from before, thinking_sound: false meant so).
        assert await put({"sounds": False}) == 200
        assert app.store.settings["sounds"] is False and not app.sounds()
        sent, reply = await call("call-15788.wav", "done")
        assert near(sent, reply)
        assert await put({"sounds": True}) == 200 and "sounds" not in app.store.settings and app.sounds()
        app.store.settings["thinking_sound"] = False
        assert not app.sounds()
    finally:
        runner.cancel()
        await app.stop()
        satellite_server.close()
        hass_server.close()


LONG_REPLY = (
    "**Lights** are on. The first sentence is said while the next is made. Then a second one follows it. "
    "A third goes on a while longer, about the weather and the garden. A fourth says nothing much at all. "
    "A fifth is here only to be long. And a sixth, the last, which nobody should hear."
)


def test_the_button_cancels_a_reply(tmp_path):
    asyncio.run(_cancelled(tmp_path))


async def _cancelled(tmp_path):
    hass, hass_server = await fake_hass.serve()
    hass_port = hass_server.sockets[0].getsockname()[1]
    satellite = FakeSatellite(mac=":".join(MAC[i : i + 2] for i in range(0, 12, 2)), speed=8.0)
    satellite_server = await serve(satellite)
    hass.device_areas[f"device-{MAC}"] = "kitchen"
    (tmp_path / "commands.yaml").write_text(COMMANDS.replace("response: Turned on the lights.", f"response: {json.dumps(LONG_REPLY)}", 1))
    (tmp_path / "settings.yaml").write_text("sounds: false\n")
    app = VoiceLabsApp(Options(
        host="127.0.0.1", port=satellite_server.sockets[0].getsockname()[1], hass_url=f"http://127.0.0.1:{hass_port}",
        hass_token=fake_hass.TOKEN, data_dir=tmp_path, http_port=free_port(), speaker_profiles=tmp_path / "speakers.json",
        threads=4,
    ))
    await app.start()
    runner = asyncio.ensure_future(app.run_forever())
    try:
        await asyncio.wait_for(satellite.ready.wait(), 30)
        # Said a sentence at a time: the first is played while the rest are made.
        satellite.audio_out.clear()
        played, ends = satellite.played, satellite.ends
        call = asyncio.ensure_future(satellite.call(read_call(CALLS / "call-15788.wav")))
        await asyncio.wait_for(satellite.audio_out.wait(), 30)
        await satellite.cancel()  # the button
        assert await call == "cancelled"
        await wait_for(lambda: satellite.ends > ends)  # the stream ended, so the device plays again
        await asyncio.sleep(1.0)
        heard = satellite.played - played
        await asyncio.sleep(1.0)
        assert satellite.played - played == heard  # nothing more sent
        assert 0 < heard < 4 * 32000  # a sentence or two of the six
        record = app.calls.recent[-1]
        assert record.outcome == "cancelled" and record.summary == "cancelled on the satellite"
        assert not any("**" in str(s.get("text", "")) for s in record.stages if s["stage"] == "speech")
        # The next call is answered as ever.
        assert await satellite.call(read_call(CALLS / "call-15788.wav")) == "done"
    finally:
        runner.cancel()
        await app.stop()
        satellite_server.close()
        hass_server.close()


def test_firmware_that_keeps_stop_loaded(tmp_path):
    asyncio.run(_stop_kept(tmp_path))


async def _stop_kept(tmp_path):
    """Firmware that runs "stop" only while it arms it (HELLO ``stop_word``)
    keeps it loaded: pushed with the wake words, not for each ring."""
    hass, hass_server = await fake_hass.serve()
    hass_port = hass_server.sockets[0].getsockname()[1]
    satellite = FakeSatellite(
        mac=":".join(MAC[i : i + 2] for i in range(0, 12, 2)), speed=8.0, hello_extra={"stop_word": True}
    )
    satellite_server = await serve(satellite)
    (tmp_path / "commands.yaml").write_text(COMMANDS)
    (tmp_path / "settings.yaml").write_text("sounds: false\n")
    app = VoiceLabsApp(Options(
        host="127.0.0.1", port=satellite_server.sockets[0].getsockname()[1], hass_url=f"http://127.0.0.1:{hass_port}",
        hass_token=fake_hass.TOKEN, data_dir=tmp_path, http_port=free_port(), speaker_profiles=tmp_path / "speakers.json",
        threads=4,
    ))
    await app.start()
    runner = asyncio.ensure_future(app.run_forever())
    try:
        await asyncio.wait_for(satellite.ready.wait(), 30)
        await wait_for(lambda: satellite.models == ["okay_nabu", "stop"])
        assert satellite.model_headers["stop"]["slot"] == 2
        clears = satellite.clears

        # A timer rings with what is loaded, and "stop" stops it.
        timer_list = f"voice_timer_list.{MAC}_timers"
        await hass._call_service({
            "domain": "voice_timer_list", "service": "create_timer",
            "target": {"entity_id": timer_list}, "service_data": {"duration": 30},
        })
        [timer_id] = hass.timers[timer_list]
        await wait_for(lambda: app.integration.running(MAC))
        played = satellite.played
        await hass.finish_timer(timer_list, timer_id)
        session = app.sessions[MAC]
        await wait_for(lambda: session.ringing is not None and satellite.played > played + 32000)
        assert await satellite.detect("Stop") == "timer_stopped"
        await wait_for(lambda: any(e["type"] == "timer_stopped" for e in satellite.events))
        await asyncio.sleep(0.5)
        assert satellite.models == ["okay_nabu", "stop"] and satellite.clears == clears

        # Heard just as what it stops ended: nothing to stop, and no call.
        recent = len(app.calls.recent)
        assert await satellite.detect("Stop") == "nothing_to_stop"
        assert len(app.calls.recent) == recent
        assert await satellite.call(read_call(CALLS / "call-15788.wav")) == "done"
    finally:
        runner.cancel()
        await app.stop()
        satellite_server.close()
        hass_server.close()


def test_home_assistant_stt_and_tts(tmp_path):
    asyncio.run(_voices(tmp_path))


async def _voices(tmp_path: Path) -> None:
    """An STT entity writes what TDT would (and only when it is needed); a TTS
    entity speaks on the satellite's own media player -- the call lasts until
    the satellite says the announcement ended -- or on another one, while the
    acknowledgement sound stays on the satellite."""
    hass, hass_server = await fake_hass.serve()
    rest, rest_port = await fake_hass.serve_rest(hass)
    hass_port = hass_server.sockets[0].getsockname()[1]
    satellite = FakeSatellite(mac=":".join(MAC[i : i + 2] for i in range(0, 12, 2)), speed=8.0)
    satellite_server = await serve(satellite)
    satellite_port = satellite_server.sockets[0].getsockname()[1]

    spoken: list[dict] = []

    def on_speak(data: dict) -> None:
        spoken.append(data)
        if data["media_player_entity_id"] == fake_hass.ESPHOME_DEVICE["media_player"]:

            async def announce() -> None:
                await asyncio.sleep(0.2)
                await satellite.device_event("announcing")
                await asyncio.sleep(0.5)
                await satellite.device_event("announced")

            asyncio.ensure_future(announce())

    hass.on_speak = on_speak
    (tmp_path / "commands.yaml").write_text(VOICE_COMMANDS)
    # No sounds: what is streamed is counted.
    (tmp_path / "settings.yaml").write_text("stt: stt.fake_whisper\ntts: tts.fake_cloud\ntts_voice: JennyNeural\nsounds: false\n")
    app = VoiceLabsApp(
        Options(
            host="127.0.0.1",
            port=satellite_port,
            hass_url=f"http://127.0.0.1:{rest_port}",
            hass_websocket_url=f"ws://127.0.0.1:{hass_port}/api/websocket",
            hass_token=fake_hass.TOKEN,
            data_dir=tmp_path,
            http_port=free_port(),
            speaker_profiles=tmp_path / "speakers.json",
            threads=4,
        )
    )
    await app.start()
    runner = asyncio.ensure_future(app.run_forever())
    try:
        await asyncio.wait_for(satellite.ready.wait(), 30)

        # Keywords decide "turn on the lights": no text needed, no STT asked.
        played = satellite.played
        assert await satellite.call(read_call(CALLS / "call-15788.wav")) == "done"
        assert hass.stt_requests == []
        assert spoken[-1]["message"] == "Turned on the lights."
        assert spoken[-1]["media_player_entity_id"] == "media_player.fake_vpe_media_player"
        assert spoken[-1]["options"] == {"voice": "JennyNeural"} and spoken[-1]["language"] == "en-US"
        speech = next(s for s in app.calls.recent[-1].stages if s["stage"] == "speech")
        assert speech["started"] is not None and speech["ended"] >= speech["started"]
        assert 0 <= speech["requested"] <= speech["started"]  # Home Assistant took it, then the device said it
        assert satellite.played == played  # nothing streamed: the satellite fetched it

        # Its media player unavailable (Home Assistant can't connect to it):
        # Piper speaks, over the call.
        own_player = fake_hass.ESPHOME_DEVICE["media_player"]
        hass.states[own_player] = ("unavailable", {})
        asked = len(spoken)
        assert await satellite.call(read_call(CALLS / "call-15788.wav")) == "done"
        assert len(spoken) == asked and satellite.played > played
        failed = next(s for s in app.calls.recent[-1].stages if s["stage"] == "speech_failed")
        assert failed["on"] == own_player and "unavailable" in failed["error"]
        del hass.states[own_player]

        # Free text comes from the STT entity (said by Piper, so there is audio
        # for it to hear).
        hass.stt_text = "Please play the album Kind of Blue."
        # (Piper's "play" at the very start is often heard as "they".)
        record = await app.try_text("please play kind of blue", MAC, speak=True)
        recognition = next(s for s in record.stages if s["stage"] == "recognition")
        assert recognition["command"] == "play_music", recognition["recognition"].get("transcript")
        # Its words, without a filler at either edge, as the TDT head's are.
        assert recognition["slots"]["query"] == "album Kind of Blue"
        assert recognition["text_source"] == "stt.fake_whisper"
        assert recognition["stt"]["asked"] and hass.stt_requests[-1]["entity_id"] == "stt.fake_whisper"
        # It heard nothing (no text, no error): the TDT head's words stand in.
        hass.stt_text = ""
        record = await app.try_text("please play kind of blue", MAC, speak=True)
        recognition = next(s for s in record.stages if s["stage"] == "recognition")
        assert recognition["command"] == "play_music" and recognition["text_source"] == "TDT"
        assert recognition["stt"]["empty"] and recognition["recognition"]["tdt_transcript"]
        hass.stt_text = "what time is it"

        # Responses on another player; the satellite doesn't wait for it.
        stored = app.store.satellites.setdefault(MAC, {})
        stored["speak_on"] = "media_player.kitchen_speaker"
        assert await satellite.call(read_call(CALLS / "call-15788.wav")) == "done"
        assert spoken[-1]["media_player_entity_id"] == "media_player.kitchen_speaker"

        # Languages: German for this satellite's STT and TTS.
        stored.update(stt_language="de", tts_language="de-DE")
        await app.try_text("please play kind of blue", MAC, speak=True)
        assert hass.stt_requests[-1]["header"].endswith("language=de-DE")
        assert await satellite.call(read_call(CALLS / "call-15788.wav")) == "done"
        assert spoken[-1]["language"] == "de-DE"

        # The AEC capture to the STT entity, when it is asked: with no
        # commands on it, this satellite needs the words.
        def no_commands_here(on: bool) -> None:
            for command in app.engine.compiled.commands.values():
                command.area = "elsewhere" if on else None

        no_commands_here(True)
        stored.update(stt_audio="aec")
        asked = len(hass.stt_requests)
        await satellite.call(read_call(CALLS / "call-15788.wav"))
        recognition = next(s for s in app.calls.recent[-1].stages if s["stage"] == "recognition")
        assert len(hass.stt_requests) == asked + 1, recognition.get("stt")
        # From Kaldi's split, where it ended the wake phrase.
        kaldi_split = app.calls.recent[-1].marks["kaldi_split"]
        assert recognition["stt"]["audio"] == {"channel": "aec", "from": kaldi_split}
        requested = hass.stt_requests[-1]["bytes"]
        stored["stt_audio"] = "aec_boost"
        await satellite.call(read_call(CALLS / "call-15788.wav"))
        recognition = next(s for s in app.calls.recent[-1].stages if s["stage"] == "recognition")
        assert recognition["stt"]["audio"]["channel"] == "aec" and "gain_db" in recognition["stt"]["audio"]
        stored.pop("stt_audio")
        no_commands_here(False)

        # Always run: the STT entity hears every command as it is said --
        # here one the keywords decide, whose words aren't needed.
        async def recognized_after(stt_request):
            for _ in range(100):
                if len(hass.stt_requests) > stt_request:
                    break
                await asyncio.sleep(0.05)
            await asyncio.sleep(0.1)
            return next(s for s in app.calls.recent[-1].stages if s["stage"] == "recognition")

        stored["stt_always"] = True
        asked = len(hass.stt_requests)
        assert await satellite.call(read_call(CALLS / "call-15788.wav")) == "done"
        recognition = await recognized_after(asked)
        assert len(hass.stt_requests) == asked + 1
        streamed = hass.stt_requests[-1]
        assert streamed["chunks"] > 1 and streamed["bytes"] > 32000, streamed
        assert recognition["command"] == "lights_on" and not recognition["stt"]["asked"]
        assert recognition["stt"]["text"] == hass.stt_text and recognition["stt"]["streamed"]["seconds"] > 1
        assert app.calls.recent[-1].to_json(full=False)["heard"] == hass.stt_text
        # Needed: the streamed transcript, and no second request.
        no_commands_here(True)
        await satellite.call(read_call(CALLS / "call-15788.wav"))
        recognition = await recognized_after(asked + 1)
        assert len(hass.stt_requests) == asked + 2
        assert recognition["stt"]["asked"] and recognition["text_source"] == "stt.fake_whisper"
        assert recognition["stt"]["audio"] == {"channel": "agc", "from": kaldi_split}
        assert streamed["bytes"] == requested  # streamed or asked for: the same stretch
        # The AEC capture, lined up; boosted by the wake word's level in it.
        stored["stt_audio"] = "aec"
        await satellite.call(read_call(CALLS / "call-15788.wav"))
        recognition = await recognized_after(asked + 2)
        assert recognition["stt"]["audio"] == {"channel": "aec", "from": kaldi_split}
        assert abs(hass.stt_requests[-1]["bytes"] - streamed["bytes"]) < 3200, (hass.stt_requests[-1], streamed)
        stored["stt_audio"] = "aec_boost"
        await satellite.call(read_call(CALLS / "call-15788.wav"))
        recognition = await recognized_after(asked + 3)
        assert recognition["stt"]["audio"]["set_by"] == "the wake word" and recognition["stt"]["audio"]["gain_db"] > 0
        for key in ("stt_audio", "stt_always"):
            stored.pop(key)
        no_commands_here(False)

        # The multilingual TDT model, picked and loaded while running.
        if (ROOT / "models/parakeet-tdt-v3-int8").exists():
            app.store.settings["tdt_model"] = "multilingual"
            await app.change_tdt()
            assert app.engine.tdt_dir.name == "parakeet-tdt-v3-int8" and app.tdt_status is None
            assert [m["loaded"] for m in app.tdt_models()] == [False, True]
            stored.pop("stt", None)  # back to the built-in TDT for text
            app.store.settings.pop("stt", None)
            record = await app.try_text("please play kind of blue", MAC, speak=True)
            recognition = next(s for s in record.stages if s["stage"] == "recognition")
            assert recognition["command"] == "play_music" and recognition["text_source"] == "TDT"
            app.store.settings["tdt_model"] = "en"
            await app.change_tdt()
            assert app.engine.tdt_dir.name == "parakeet-tdt-int8"
    finally:
        runner.cancel()
        await app.stop()
        await rest.cleanup()
        satellite_server.close()
        hass_server.close()


def _library_commands(*wanted: tuple[str, str]) -> str:
    """Commands from the English library, as they are added from it."""
    from ruamel.yaml import YAML  # pylint: disable=import-outside-toplevel

    library = ROOT / "voice_labs/languages/en/library"
    out = {}
    for file, command in wanted:
        spec = YAML(typ="safe").load((library / f"{file}.yaml").read_text())["commands"][command]
        out[command] = {k: v for k, v in spec.items() if k != "needs"}
    return json.dumps({"commands": out})


def test_alarms_and_reminders_on_the_calendar(tmp_path):
    asyncio.run(_calendar(tmp_path))


async def _calendar(tmp_path: Path) -> None:
    """The app makes its Local Calendar; an alarm or a reminder with a time is
    an event on it, marked as the satellite's, which rings that satellite
    when it starts (an alarm until "stop", a reminder said once); one without
    a time goes on the to-do list."""
    from datetime import datetime, timedelta  # pylint: disable=import-outside-toplevel
    from zoneinfo import ZoneInfo  # pylint: disable=import-outside-toplevel

    hass, hass_server = await fake_hass.serve()
    rest, rest_port = await fake_hass.serve_rest(hass)
    hass_port = hass_server.sockets[0].getsockname()[1]
    satellite = FakeSatellite(mac=":".join(MAC[i : i + 2] for i in range(0, 12, 2)), speed=8.0)
    satellite_server = await serve(satellite)
    satellite_port = satellite_server.sockets[0].getsockname()[1]
    (tmp_path / "commands.yaml").write_text(_library_commands(
        ("timers", "set_alarm"), ("timers", "cancel_alarm"), ("timers", "alarm_status"),
        ("reminders", "reminder_status"), ("reminders", "remind_me"),
    ))
    app = VoiceLabsApp(
        Options(
            host="127.0.0.1",
            port=satellite_port,
            hass_url=f"http://127.0.0.1:{rest_port}",
            hass_websocket_url=f"ws://127.0.0.1:{hass_port}/api/websocket",
            hass_token=fake_hass.TOKEN,
            data_dir=tmp_path,
            http_port=free_port(),
            threads=4,
        )
    )
    await app.start()
    runner = asyncio.ensure_future(app.run_forever())

    async def said(text: str) -> str:
        record = await app.try_text(text, MAC, run_actions=True)
        return next(s for s in record.stages if s["stage"] == "speech")["text"]

    try:
        await asyncio.wait_for(satellite.ready.wait(), 30)
        assert app.alarms is not None
        await wait_for(lambda: app.alarms.ready)
        calendar = "calendar.voice_labs"
        assert hass.local_calendars == ["Voice Labs"] and app.alarms.entity_id == calendar
        assert str(app.alarms.tz) == "Europe/Berlin"

        # An alarm: an event in Home Assistant's time zone, marked as this satellite's.
        assert await said("set an alarm for 7 am tomorrow") == "Alarm set for 7:00 AM tomorrow."
        [event] = hass.calendar_events[calendar]
        assert event["summary"] == "Alarm" and f"[voice_labs:alarm:{MAC}]" in event["description"]
        start = datetime.fromisoformat(event["start"])
        assert start.hour == 7 and start.utcoffset() == start.astimezone(ZoneInfo("Europe/Berlin")).utcoffset()
        await wait_for(lambda: len(app.alarms.upcoming("alarm", MAC)) == 1)
        assert await said("what alarms do i have") == "You have an alarm at 7:00 AM tomorrow."

        # A reminder with a time, on the calendar too.
        assert (await said("remind me every tuesday at 7 pm to take out the bins")).startswith("I'll remind you to take out the bins")
        bins = hass.calendar_events[calendar][-1]
        assert bins["summary"] == "Take out the bins" and bins["rrule"] == "FREQ=WEEKLY;BYDAY=TU"
        # Without a time, on the to-do list.
        assert await said("remind me to buy milk") == "Added buy milk to your reminders."
        assert hass.services[-1]["service_data"] == {"item": "buy milk"}
        assert hass.services[-1]["target"] == {"entity_id": "todo.reminders"}

        # One added in Home Assistant is said on every satellite when it starts.
        soon = datetime.now(ZoneInfo("Europe/Berlin")) + timedelta(seconds=1)
        later = (soon + timedelta(minutes=5)).isoformat()
        await hass.add_calendar_event(calendar, summary="Water the plants", start=soon.isoformat(), end=later)
        await wait_for(lambda: app.calls.recent and app.calls.recent[-1].outcome == "reminder said")
        ringing = next(s for s in app.calls.recent[-1].stages if s["stage"] == "timer_ringing")
        assert [t["announce"] for t in ringing["timers"]] == ["Reminder: Water the plants."]

        # An alarm rings its satellite until "stop".
        soon = datetime.now(ZoneInfo("Europe/Berlin")) + timedelta(seconds=1)
        played = satellite.played
        await hass.add_calendar_event(
            calendar, summary="Alarm", start=soon.isoformat(), end=later,
            description=f"Set by voice on Kitchen. [voice_labs:alarm:{MAC}]",
        )
        session = app.sessions[MAC]
        await wait_for(lambda: session.ringing is not None and "stop" in satellite.models and satellite.played > played + 32000)
        assert await satellite.detect("Stop") == "timer_stopped"
        await wait_for(lambda: session.ringing.done() and satellite.models == ["okay_nabu"])
        rang = next(s for r in reversed(app.calls.recent) for s in r.stages if s["stage"] == "timer_ringing")
        assert rang["timers"][0]["announce"].startswith("It's ")

        # Without uids, what the subscription sends is read again over REST
        # (an alarm can't be cancelled without its uid); with them, it is used.
        assert hass.calendar_reads and all(e.uid.startswith("event-") for e in app.alarms.events)
        reads = hass.calendar_reads
        hass.calendar_push_uids = True
        await hass._push_calendar(calendar)
        await asyncio.sleep(0.2)
        assert hass.calendar_reads == reads

        # Cancelled: off the calendar (the one that rang has gone by).
        assert await said("cancel my alarm") == "Cancelled the alarm at 7:00 AM tomorrow."
        assert event["uid"] not in [e["uid"] for e in hass.calendar_events[calendar]]
        await wait_for(lambda: not app.alarms.upcoming("alarm", MAC))
        assert await said("cancel my alarm") == "No alarms are set."
    finally:
        runner.cancel()
        await app.stop()
        await rest.cleanup()
        satellite_server.close()
        hass_server.close()
