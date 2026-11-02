"""Between satellites: an announcement, and the intercom -- two fake
satellites, the app between them."""

from __future__ import annotations

import asyncio

import numpy as np

from voice_labs import tones
from voice_labs.discover import Device
from voice_labs.server import Options, VoiceLabsApp

from . import fake_hass
from .fake_satellite import FakeSatellite, read_call, serve
from .test_app import CALLS, free_port, pytestmark, wait_for  # noqa: F401 - skipped without the models

KITCHEN, BEDROOM = "aabbccddeeff", "112233445566"

COMMANDS = """\
names:
  areas:
    kitchen: {name: Kitchen}
    bedroom: {name: Bedroom}
commands:
  announce:
    description: announce something on the other satellites
    require: [[announce]]
    names:
      area: {count: 1}
    combinations: [[message], [area, message]]
    wildcard: {slot: message, after: [announce], to_end: true}
    announce: "{{ slots.message }}"
  intercom:
    description: talk with another room
    require: [[connect]]
    optional: [[to], [the]]
    names:
      area: {count: 1}
    intercom: connect
"""


def _mac(key: str) -> str:
    return ":".join(key[i : i + 2] for i in range(0, 12, 2))


def test_announcements_and_the_intercom(tmp_path):
    asyncio.run(_scenario(tmp_path))


async def _scenario(tmp_path) -> None:
    hass, hass_server = await fake_hass.serve()
    hass_port = hass_server.sockets[0].getsockname()[1]
    hum = bytes(range(256)) * 8
    kitchen = FakeSatellite(name="kitchen", mac=_mac(KITCHEN), speed=8.0, hello_extra={"intercom": True}, room_audio=hum)
    bedroom = FakeSatellite(name="bedroom", mac=_mac(BEDROOM), speed=8.0, hello_extra={"intercom": True})
    servers = [await serve(kitchen), await serve(bedroom)]
    hass.device_areas[f"device-{KITCHEN}"] = "kitchen"
    hass.device_areas[f"device-{BEDROOM}"] = "bedroom"
    (tmp_path / "commands.yaml").write_text(COMMANDS)
    app = VoiceLabsApp(Options(
        host="127.0.0.1", port=servers[0].sockets[0].getsockname()[1], hass_url=f"http://127.0.0.1:{hass_port}",
        hass_token=fake_hass.TOKEN, data_dir=tmp_path, http_port=free_port(), speaker_profiles=tmp_path / "speakers.json",
        threads=4,
    ))
    await app.start()
    runner = asyncio.ensure_future(app.run_forever())
    try:
        app._start_session(Device(BEDROOM, "127.0.0.1", servers[1].sockets[0].getsockname()[1], "test"))
        await asyncio.wait_for(asyncio.gather(kitchen.ready.wait(), bedroom.ready.wait()), 30)
        await wait_for(lambda: app.satellite(BEDROOM).area_id == "bedroom" and app.satellite(KITCHEN).area_id == "kitchen")

        # "Announce dinner is ready": the chime and it, on the other one only.
        record = await app.try_text("announce dinner is ready", KITCHEN, run_actions=True)
        action = next(s for s in record.stages if s["stage"] == "action")
        assert action["outcome"] == "done" and action["sequence"] == [{"announce": "dinner is ready", "satellites": [BEDROOM]}]
        await wait_for(lambda: {"type": "announcement", "data": {"active": False}} in bedroom.events)
        assert {"type": "announcement", "data": {"active": True}} in bedroom.events
        assert bytes(bedroom.heard).startswith(tones.CHIME) and len(bedroom.heard) > len(tones.CHIME) + 16000
        assert not kitchen.heard
        said = app.calls.recent[-1]
        assert (said.kind, said.outcome, said.summary) == ("announcement", "announced", "dinner is ready")
        # Nowhere else to say it.
        record = await app.try_text("announce dinner is ready in the kitchen", KITCHEN, run_actions=True)
        action = next(s for s in record.stages if s["stage"] == "action")
        assert (action["outcome"], action["speech"]) == ("no_satellites", "There's no other satellite in the Kitchen.")

        # Do not disturb in the bedroom: nothing from the kitchen gets there,
        # said; from the bedroom, its announcement still goes out.
        hass.do_not_disturb.update(kitchen=False, bedroom=True)
        record = await app.try_text("announce dinner is ready", KITCHEN, run_actions=True)
        action = next(s for s in record.stages if s["stage"] == "action")
        assert (action["outcome"], action["speech"]) == ("do_not_disturb", "Do not disturb is on in the Bedroom.")
        record = await app.try_text("connect to the bedroom", KITCHEN, run_actions=True)
        assert next(s for s in record.stages if s["stage"] == "action")["outcome"] == "do_not_disturb"
        record = await app.try_text("announce breakfast is ready", BEDROOM)
        action = next(s for s in record.stages if s["stage"] == "action")
        assert action["outcome"] == "done" and action["sequence"] == [{"announce": "breakfast is ready", "satellites": [KITCHEN]}]
        hass.do_not_disturb["bedroom"] = False

        # Old firmware: no intercom.
        app.sessions[BEDROOM].hello["intercom"] = False
        record = await app.try_text("connect to the bedroom", KITCHEN, run_actions=True)
        assert next(s for s in record.stages if s["stage"] == "action")["outcome"] == "needs_firmware"
        app.sessions[BEDROOM].hello["intercom"] = True
        record = await app.try_text("connect to the kitchen", KITCHEN, run_actions=True)
        assert next(s for s in record.stages if s["stage"] == "action")["outcome"] == "here"

        # "Connect to the bedroom": the tone on both, then each streams to the other.
        bedroom.heard.clear()
        record = await app.try_text("connect to the bedroom", KITCHEN, run_actions=True)
        action = next(s for s in record.stages if s["stage"] == "action")
        assert (action["outcome"], action["speech"]) == ("done", "Connecting to the Bedroom.")
        await wait_for(lambda: kitchen.intercom[-1:] == [True] and bedroom.intercom[-1:] == [True])
        await wait_for(lambda: len(bedroom.heard) > len(tones.CONNECTED) + 8 * len(hum))
        assert bytes(bedroom.heard).startswith(tones.CONNECTED)
        relayed = np.frombuffer(bytes(bedroom.heard)[len(tones.CONNECTED):], dtype="<i2")
        assert np.abs(relayed).max() > 1000  # the kitchen, heard in the bedroom (filtered: Duplex)
        await wait_for(lambda: len(kitchen.heard) > len(tones.CONNECTED) + 4096)  # and the other way
        assert app.intercom_problem(KITCHEN, BEDROOM) == "busy"
        # Busy meanwhile: an announcement waits for it.
        assert app.sessions[KITCHEN].intercom is app.sessions[BEDROOM].intercom

        # The button on either ends it on both: back to themselves, a tone.
        await bedroom.device_event("intercom_end")
        await wait_for(lambda: kitchen.monitoring[-1:] == [False] and bedroom.monitoring[-1:] == [False])
        assert app.sessions[KITCHEN].intercom is None and app.sessions[BEDROOM].intercom is None
        ended = next(r for r in reversed(app.calls.recent) if r.kind == "intercom")
        assert ended.outcome == "intercom ended" and "the button on" in ended.summary
        await wait_for(lambda: bytes(kitchen.heard).endswith(tones.ENDED))

        # One "okay nabu" heard by both: the one that heard it better answers,
        # the other ends its call ("answered_elsewhere"). The bedroom's is
        # farther: quieter, in more noise, on its echo-cancelled capture.
        said = read_call(CALLS / "call-15788.wav")
        near = np.frombuffer(said, dtype="<i2").astype(np.float64)
        far = near * 0.1 + np.random.default_rng(2).normal(0, 300, near.size)
        far = np.clip(far, -32768, 32767).astype("<i2").tobytes()
        ended = await asyncio.gather(kitchen.call(said, aec_pcm=said), bedroom.call(said, aec_pcm=far))
        assert ended[0] != "answered_elsewhere" and ended[1] == "answered_elsewhere", ended
        elsewhere = next(r for r in reversed(app.calls.recent) if r.outcome == "answered elsewhere")
        assert elsewhere.summary == "answered by kitchen"
        heard = next(s for s in elsewhere.stages if s["stage"] == "answered_elsewhere")["heard"]
        clarity = {h["satellite"]: h["clarity"] for h in heard}
        assert clarity[KITCHEN] > clarity[BEDROOM]
    finally:
        runner.cancel()
        await app.stop()
        for server in servers:
            server.close()
        hass_server.close()


def test_an_areas_satellites_share_its_timers(tmp_path):
    asyncio.run(_shared_timers(tmp_path))


async def _shared_timers(tmp_path) -> None:
    """Two satellites in the Kitchen share its timer list: each shows its
    timers alike, each rings when one finishes, and "stop" on either stops
    both."""
    hass, hass_server = await fake_hass.serve()
    hass_port = hass_server.sockets[0].getsockname()[1]
    counter = FakeSatellite(name="counter", mac=_mac(KITCHEN), speed=8.0)
    table = FakeSatellite(name="table", mac=_mac(BEDROOM), speed=8.0)
    servers = [await serve(counter), await serve(table)]
    for key in (KITCHEN, BEDROOM):
        hass.device_areas[f"device-{key}"] = "kitchen"
    (tmp_path / "commands.yaml").write_text(COMMANDS)
    app = VoiceLabsApp(Options(
        host="127.0.0.1", port=servers[0].sockets[0].getsockname()[1], hass_url=f"http://127.0.0.1:{hass_port}",
        hass_token=fake_hass.TOKEN, data_dir=tmp_path, http_port=free_port(), speaker_profiles=tmp_path / "speakers.json",
        threads=4,
    ))
    await app.start()
    runner = asyncio.ensure_future(app.run_forever())
    timer_list = "voice_timer_list.kitchen_timers"

    def shown(satellite) -> list[dict]:
        return [e["data"] for e in satellite.events if e["type"] == "timer_progress"]

    try:
        app._start_session(Device(BEDROOM, "127.0.0.1", servers[1].sockets[0].getsockname()[1], "test"))
        await asyncio.wait_for(asyncio.gather(counter.ready.wait(), table.ready.wait()), 30)
        await wait_for(lambda: app.satellite(KITCHEN).timer_list == app.satellite(BEDROOM).timer_list == timer_list)
        assert app.integration.sharing(timer_list) == sorted([KITCHEN, BEDROOM])

        # Set from one: both show it, in the same color; a second, in the next.
        for seconds in (300, 600):
            await hass._call_service({"domain": "voice_timer_list", "service": "create_timer",
                                      "target": {"entity_id": timer_list}, "service_data": {"duration": seconds}})
        await wait_for(lambda: all(shown(s) and shown(s)[-1].get("count") == 2 for s in (counter, table)))
        for satellite in (counter, table):
            assert (shown(satellite)[-1]["color0"], shown(satellite)[-1]["color1"]) == (-1, 0xFFFF00)

        # Finished: both ring; "stop" on one stops both.
        soonest = min(hass.timers[timer_list], key=lambda t: hass.timers[timer_list][t]["created_duration"])
        await hass.finish_timer(timer_list, soonest)
        sessions = [app.sessions[KITCHEN], app.sessions[BEDROOM]]
        await wait_for(lambda: all(s.ringing is not None and not s.ringing.done() and "stop" in sat.models
                                   for s, sat in zip(sessions, (counter, table))))
        assert await table.detect("Stop") == "timer_stopped"
        await wait_for(lambda: all(s.ringing.done() for s in sessions))
        stopped = [next(st for st in r.stages if st["stage"] == "timer_stopped")["by"] for r in app.calls.recent if r.kind == "timer"]
        assert sorted(stopped[-2:]) == ["elsewhere", "stop"]
        await wait_for(lambda: any(e["type"] == "timer_stopped" for e in counter.events))
    finally:
        runner.cancel()
        await app.stop()
        for server in servers:
            server.close()
        hass_server.close()
