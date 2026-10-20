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

from voice_call.server import Options, VoiceCallApp

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

    # The satellite's area is its Home Assistant device's (Voice Call integration).
    hass.device_areas[f"device-{MAC}"] = "kitchen"
    (tmp_path / "commands.yaml").write_text(COMMANDS)
    (tmp_path / "satellites.yaml").write_text(f"{MAC}: {{name: Kitchen VPE, area: office}}\n")
    http_port = free_port()
    app = VoiceCallApp(
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
        assert hass.events[-1]["event_type"] == "voice_call_command"
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
        assert app.calls.list()[0]["wake"]["probability"] == 0.902
        # The list also says what was heard, what ran and what was said.
        listed = app.calls.list()[0]
        assert "lights" in listed["heard"]
        assert (listed["command"], listed["said"]) == ("lights_on", "Turned on the lights.")
        assert {"split", "speaker", "recognition", "action", "speech"} <= set(stages)
        assert variables["speaker"] == {"name": None, "score": None}  # nobody enrolled
        split = next(s for s in record.stages if s["stage"] == "split")
        # The CTC evidence found the wake phrase again, and the command starts after it.
        assert split["phrase"] is not None
        assert split["cut"] >= split["wake_end"] - 0.01
        # How long each step took.
        by_stage = {s["stage"]: s for s in record.stages}
        assert by_stage["wake_kaldi"]["decode_ms"] > 0
        assert split["seconds"] > 0 and by_stage["speaker"]["seconds"] > 0
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
            assert (await (await http.get("/api/speakers")).json()) == {"enabled": True, "speakers": []}
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

            # The Home's commands, and a satellite's own: one that doesn't
            # take the Home's runs only its own.
            async def tried_command(text):
                record = await (await http.post("/api/try", json={"text": text, "satellite": MAC})).json()
                return next(s for s in record["stages"] if s["stage"] == "recognition")

            copied = await (await http.post("/api/commands/what_time/duplicate", json={"new_id": "kids_time", "satellite": MAC})).json()
            assert copied["ok"] and app.engine.compiled.commands["kids_time"].satellite == MAC
            store_data = await (await http.get("/api/store")).json()
            assert store_data["details"]["kids_time"]["satellite"] == MAC and store_data["details"]["what_time"]["satellite"] is None
            assert (await http.post("/api/commands/what_time/duplicate", json={"new_id": "x", "satellite": "nope"})).status == 400
            assert (await http.put(f"/api/satellites/{MAC}", json={"home": False, "icon": "mdi:sofa"})).status == 200
            assert app.store.satellites[MAC]["home"] is False and app.store.satellites[MAC]["icon"] == "mdi:sofa"
            recognition = await tried_command("turn on the lights")
            assert recognition["command"] is None
            assert recognition["skipped"][0] == {"command": "lights_on", "why": "the Home's, which this satellite doesn't take"}
            assert (await tried_command("what time is it"))["command"] == "kids_time"
            listed = next(s for s in (await (await http.get("/api/satellites")).json())["satellites"] if s["key"] == MAC)
            assert listed["home"] is False and listed["icon"] == "mdi:sofa" and listed["commands"] == ["kids_time"]
            assert (await http.put(f"/api/satellites/{MAC}", json={"icon": "sofa"})).status == 400
            # Taking the Home's again: its own copy still goes first.
            assert (await http.put(f"/api/satellites/{MAC}", json={"home": True, "icon": None})).status == 200
            assert "home" not in app.store.satellites[MAC] and "icon" not in app.store.satellites[MAC]
            assert (await tried_command("turn on the lights"))["command"] == "lights_on"
            assert (await tried_command("what time is it"))["command"] == "kids_time"
            assert (await http.delete("/api/commands/kids_time")).status == 200

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
            assert status["verbosity"] == {"levels": ["silent", "beep", "brief", "specific", "verbose"], "beep_elsewhere": "specific"}
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

            # Skills: the Home's (each unless taken off it), and a
            # satellite's own.
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
            assert "arithmetic" in listed["home"] and listed["satellites"].get(MAC, []) == []
            assert (await http.put("/api/skills/arithmetic", json={"home": False})).status == 200
            assert app.store.settings["skills"] == {"arithmetic": False}
            assert await said("what is twelve times eight") != "It's 96."
            assert await said("what is twelve times eight", None) != "It's 96."
            assert (await http.put("/api/skills/arithmetic", json={"satellites": {MAC: True}})).status == 200
            assert await said("what is twelve times eight") == "It's 96."  # its own
            assert await said("what is twelve times eight", None) != "It's 96."
            # Its own still answer when it doesn't take the Home's.
            spelled = await said("how do you spell cat")
            assert (await http.put(f"/api/satellites/{MAC}", json={"home": False})).status == 200
            assert await said("what is twelve times eight") == "It's 96."
            assert await said("how do you spell cat") != spelled
            assert (await http.put(f"/api/satellites/{MAC}", json={"home": True})).status == 200
            satellites = (await (await http.get("/api/satellites")).json())["satellites"]
            assert next(s for s in satellites if s["key"] == MAC)["skills"] == ["arithmetic"]
            assert (await http.put("/api/skills/arithmetic", json={"home": True, "satellites": {MAC: False}})).status == 200
            assert "skills" not in app.store.settings and "skills" not in app.store.satellites[MAC]
            assert (await http.put("/api/skills/nope", json={"home": False})).status == 404
            assert (await http.put("/api/skills/live", json={"home": False})).status == 404
            assert (await http.put("/api/skills/units", json={"satellites": {MAC: "off"}})).status == 400
            assert (await http.put("/api/skills/units", json={"satellites": {"nope": True}})).status == 400

            # A skill's settings: edited in full, stored as what differs.
            def skill(skills, skill_id):
                return next(s for s in skills["skills"] if s["id"] == skill_id)

            assert skill(listed, "clock")["settings"] == {"hours": 12} and not skill(listed, "clock")["settings_set"]
            assert (await http.put("/api/skills/clock", json={"settings": {"hours": 24}})).status == 200
            assert app.store.settings["skill_settings"] == {"clock": {"hours": 24}}
            assert await said("what time is 45 minutes after 3 pm") == "It's 15:45."
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
            assert await said("what time is 45 minutes after 3 pm") == "It's 3:45 PM."
            # Stored settings that can't be used (edited by hand): the built-in ones.
            app.store.settings["skill_settings"] = {"clock": {"hours": 13}}
            listed = await (await http.get("/api/skills")).json()
            assert skill(listed, "clock")["settings_error"] == "hours: 12 or 24"
            app.configure_skills()
            assert await said("what time is 45 minutes after 3 pm") == "It's 3:45 PM."
            del app.store.settings["skill_settings"]
            app.configure_skills()

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
            assert "unknown" not in (tmp_path / "commands.yaml").read_text()

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
            assert added == {"ok": True, "id": "play_music_device"}
            spec = (await (await http.get("/api/store")).json())["commands"]["play_music_device"]
            assert spec["title"] == "Play music on a different device" and spec["library"] == "music"
            assert spec["combinations"] == [["entity", "query"]] and spec["names"] == {"entity": {"count": 1, "domains": ["media_player"]}}
            assert not {"needs", "variants", "formerly"} & set(spec)
            assert (await http.delete("/api/commands/play_music_device")).status == 200

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
                vad, okay = first["models"]
                assert vad["vad"] and vad["name"] == "Voice activity"
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
        await wait_for(lambda: satellite.clears == clears + 1 and satellite.models == ["vad", "okay_nabu"])
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
        await wait_for(lambda: session.ringing.done() and satellite.models == ["vad", "okay_nabu"])
        stopped = next(s for s in app.calls.recent[-1].stages if s["stage"] == "timer_stopped")
        assert stopped["by"] == "button" and app.calls.recent[-1].outcome == "timer stopped"

        # Home Assistant refuses the action: the satellite is told, the call
        # is an error.
        hass.fail_scripts = True
        assert await satellite.call(read_call(CALLS / "call-15788.wav")) == "done"
        record = app.calls.recent[-1]
        assert record.outcome == "error"
        assert next(s for s in record.stages if s["stage"] == "speech")["text"] == "Sorry, that didn't work."
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
    (tmp_path / "settings.yaml").write_text("stt: stt.fake_whisper\ntts: tts.fake_cloud\ntts_voice: JennyNeural\n")
    app = VoiceCallApp(
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

        # The AEC capture to the STT entity, when it is asked: taking no
        # commands, this satellite needs the words.
        stored.update(stt_audio="aec", home=False)
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
        for key in ("stt_audio", "home"):
            stored.pop(key)

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
        stored["home"] = False
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
        for key in ("stt_audio", "home", "stt_always"):
            stored.pop(key)

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
