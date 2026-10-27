"""Between satellites: an announcement, and the intercom -- two fake
satellites, the app between them."""

from __future__ import annotations

import asyncio

from voice_call import tones
from voice_call.discover import Device
from voice_call.server import Options, VoiceCallApp

from . import fake_hass
from .fake_satellite import FakeSatellite, serve
from .test_app import free_port, pytestmark, wait_for  # noqa: F401 - skipped without the models

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
    app = VoiceCallApp(Options(
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
        assert hum in bytes(bedroom.heard)[len(tones.CONNECTED):]  # the kitchen, heard in the bedroom
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
    finally:
        runner.cancel()
        await app.stop()
        for server in servers:
            server.close()
        hass_server.close()
