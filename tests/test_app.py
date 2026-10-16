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
      area: {count: 1, optional: true}
    action:
      action: light.turn_on
      target: "{{ target }}"
    response: Turned on the lights.
    overrides:
      - scope: {satellites: [nobody]}
        disabled: true
  set_brightness:
    description: set the brightness of the lights
    require: [[brightness, percent]]
    optional: [[set, change, make], [light, lights], [to], [in], [brightness], [percent]]
    names:
      area: {count: 1, optional: true}
    number: percentage
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
        assert await satellite.call(read_call(CALLS / "call-15788.wav")) == "done"
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
        assert {"split", "speaker", "recognition", "action", "speech"} <= set(stages)
        assert variables["speaker"] == {"name": None, "score": None}  # nobody enrolled
        split = next(s for s in record.stages if s["stage"] == "split")
        # The CTC evidence found the wake phrase again, and the command starts after it.
        assert split["phrase"] is not None
        assert split["cut"] >= split["wake_end"] - 0.01
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
            assert tried["dry_run"]
            assert len(hass.scripts) == scripts  # nothing ran since the debug session started

            # Command groups: a satellite given "kids" runs only its commands.
            async def tried_command(text):
                record = await (await http.post("/api/try", json={"text": text, "satellite": MAC})).json()
                return next(s for s in record["stages"] if s["stage"] == "recognition")

            assert (await (await http.put("/api/groups/kids", json={"name": "Kids", "commands": ["what_time"]})).json())["ok"]
            assert (await http.put(f"/api/satellites/{MAC}", json={"command_groups": ["kids"]})).status == 200
            recognition = await tried_command("turn on the lights")
            assert recognition["command"] is None
            assert recognition["skipped"][0] == {"command": "lights_on", "why": "not in this satellite's command groups"}
            assert (await tried_command("what time is it"))["command"] == "what_time"
            # The home's groups, where a satellite has none of its own.
            assert (await http.put(f"/api/satellites/{MAC}", json={"command_groups": None})).status == 200
            assert (await http.put("/api/settings", json={"command_groups": ["kids"]})).status == 200
            assert (await tried_command("turn on the lights"))["command"] is None
            assert (await http.put("/api/settings", json={"command_groups": ["nope"]})).status == 400
            # Deleting the group frees every satellite.
            assert (await http.delete("/api/groups/kids")).status == 200
            assert "command_groups" not in app.store.settings
            assert (await tried_command("turn on the lights"))["command"] == "lights_on"

            # A question no command takes is answered: exactly, or (not here:
            # there is no question model) as live.
            record = await app.try_text("what is twelve times eight", MAC)
            assert record.outcome == "answered"
            assert next(s for s in record.stages if s["stage"] == "speech")["text"] == "It's 96."
            record = await app.try_text("what are the top news headlines", MAC)
            assert next(s for s in record.stages if s["stage"] == "speech")["text"] == "Sorry, I can't look that up."

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
    wildcard: {slot: query, after: [play], to_end: true, required: true, max_words: 8}
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
        assert satellite.played == played  # nothing streamed: the satellite fetched it

        # Free text comes from the STT entity.
        hass.stt_text = "Please play the album Kind of Blue."
        # (Piper's "play" at the very start is often heard as "they".)
        record = await app.try_text("please play kind of blue", MAC)
        recognition = next(s for s in record.stages if s["stage"] == "recognition")
        assert recognition["command"] == "play_music", recognition["recognition"].get("transcript")
        assert recognition["slots"]["query"] == "the album Kind of Blue"
        assert recognition["text_source"] == "stt.fake_whisper"
        assert recognition["stt"]["asked"] and hass.stt_requests[-1]["entity_id"] == "stt.fake_whisper"

        # Responses on another player; the satellite doesn't wait for it.
        stored = app.store.satellites.setdefault(MAC, {})
        stored["speak_on"] = "media_player.kitchen_speaker"
        assert await satellite.call(read_call(CALLS / "call-15788.wav")) == "done"
        assert spoken[-1]["media_player_entity_id"] == "media_player.kitchen_speaker"

        # Languages: German for this satellite's STT and TTS.
        stored.update(stt_language="de", tts_language="de-DE")
        await app.try_text("please play kind of blue", MAC)
        assert hass.stt_requests[-1]["header"].endswith("language=de-DE")
        assert await satellite.call(read_call(CALLS / "call-15788.wav")) == "done"
        assert spoken[-1]["language"] == "de-DE"

        # The multilingual TDT model, picked and loaded while running.
        if (ROOT / "models/parakeet-tdt-v3-int8").exists():
            app.store.settings["tdt_model"] = "multilingual"
            await app.change_tdt()
            assert app.engine.tdt_dir.name == "parakeet-tdt-v3-int8" and app.tdt_status is None
            assert [m["loaded"] for m in app.tdt_models()] == [False, True]
            stored.pop("stt", None)  # back to the built-in TDT for text
            app.store.settings.pop("stt", None)
            record = await app.try_text("please play kind of blue", MAC)
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
