"""Speakers, end to end: one enrolled from a satellite while she reads the
passage aloud (Piper's voices), and her command cut short when someone else
starts talking after it -- but not when she goes on herself.

Needs the models (speech, speaker, Piper's libritts_r voice); skipped without them.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import aiohttp
import numpy as np
import pytest

from voice_call.enrollment import PASSAGE
from voice_call.server import Options, VoiceCallApp

from . import fake_hass
from .fake_satellite import FakeSatellite, serve
from .test_app import COMMANDS, free_port, wait_for

ROOT = Path(__file__).resolve().parent.parent
VOICE = ROOT / "models/piper/en_US-libritts_r-medium.onnx"
MAC = "aabbccddeeff"
RATE = 16000
ALICE, OTHER = 101, 2138
"""Two of the voice's speakers, about as alike as two people picked at random (0.25)."""
COMMAND = "Turn on the lights in the kitchen."
TALK = ["Can you pass me the salt, please?", "I think we need more bread as well.",
        "Did anyone see where I put my glasses?"]

pytestmark = pytest.mark.skipif(
    not VOICE.exists()
    or not (ROOT / "models/speaker/redimnet_b2_vox2.onnx").exists()
    or not (ROOT / "models/speech-to-phrase").exists(),
    reason="needs the speech, speaker and Piper models",
)


@pytest.fixture(scope="module")
def piper():
    from piper import PiperVoice  # pylint: disable=import-outside-toplevel

    return PiperVoice.load(VOICE)


def say(piper, text: str, speaker: int) -> np.ndarray:
    import soxr  # pylint: disable=import-outside-toplevel
    from piper import SynthesisConfig  # pylint: disable=import-outside-toplevel

    config = SynthesisConfig(speaker_id=speaker, noise_scale=0.5, noise_w_scale=0.6)
    audio = np.concatenate([c.audio_float_array for c in piper.synthesize(text, syn_config=config)])
    audio = soxr.resample(audio.astype(np.float32), piper.config.sample_rate, RATE)
    frames = audio[: len(audio) // 160 * 160].reshape(-1, 160)
    db = 10 * np.log10((frames**2).mean(1) + 1e-12)
    on = np.flatnonzero(db > db.max() - 40)
    return audio[on[0] * 160 : (on[-1] + 1) * 160]


def pcm(*parts) -> bytes:
    """Audio and pauses (seconds) as 16-bit PCM, at a talking level."""
    audio = np.concatenate([np.zeros(int(p * RATE), np.float32) if isinstance(p, float) else p for p in parts])
    return (audio / np.abs(audio).max() * 10 ** (-12 / 20) * 32767).astype("<i2").tobytes()


def call_audio(wake, *parts) -> tuple[bytes, list[float]]:
    """A call: the wake word ending just before the 1.5 s pre-roll does, then
    ``parts``; and where each audio part starts."""
    lead = max(0.05, 1.45 - len(wake) / RATE)
    starts, at = [], 1.45
    for p in parts:
        if not isinstance(p, float):
            starts.append(at)
        at += p if isinstance(p, float) else len(p) / RATE
    return pcm(lead, wake[-int((1.45 - lead) * RATE) :], *parts, 0.3), starts


def test_enrolled_speaker_and_someone_else(tmp_path, piper):
    asyncio.run(_scenario(tmp_path, piper))


async def _scenario(tmp_path: Path, piper) -> None:
    hass, hass_server = await fake_hass.serve()
    satellite = FakeSatellite(mac=":".join(MAC[i : i + 2] for i in range(0, 12, 2)), speed=8.0,
                              hello_extra={"aec_lead_samples": 0})
    satellite_server = await serve(satellite)
    hass.device_areas[f"device-{MAC}"] = "kitchen"
    (tmp_path / "commands.yaml").write_text(COMMANDS)
    (tmp_path / "satellites.yaml").write_text(f"{MAC}: {{name: Kitchen VPE}}\n")
    (tmp_path / "settings.yaml").write_text("end_of_speech: {silence_seconds: 0.5}\n")
    http_port = free_port()
    app = VoiceCallApp(Options(
        host="127.0.0.1", port=satellite_server.sockets[0].getsockname()[1],
        hass_url=f"http://127.0.0.1:{hass_server.sockets[0].getsockname()[1]}", hass_token=fake_hass.TOKEN,
        data_dir=tmp_path, http_port=http_port, speaker_profiles=tmp_path / "speakers.json", threads=4,
    ))
    # Piper's "Okay Nabu" often sits near a confusable phrase for Kaldi: what
    # is tested here comes after the wake word, so it stands (with Kaldi's split).
    verify = app._verify_wake  # pylint: disable=protected-access

    async def accepted(label, call):
        verdict = await verify(label, call)
        verdict.accepted = True
        return verdict

    app._verify_wake = accepted  # pylint: disable=protected-access
    await app.start()
    runner = asyncio.ensure_future(app.run_forever())
    try:
        await asyncio.wait_for(satellite.ready.wait(), 30)
        async with aiohttp.ClientSession(f"http://127.0.0.1:{http_port}") as http:
            async def api(method, path, body=None):
                async with http.request(method, f"/api/{path}", json=body) as response:
                    return response.status, await response.json()

            status, data = await api("GET", "speakers")
            assert data["speakers"] == [] and data["passage"] == PASSAGE
            assert data["satellites"] == [{"key": MAC, "name": "Kitchen VPE", "enroll": True}]

            # Alice reads the passage, near the satellite.
            satellite.room_audio = pcm(*[a for para in PASSAGE.split("\n\n") for a in (say(piper, para, ALICE), 0.6)])
            status, data = await api("POST", "speakers/enrollment", {"satellite": MAC, "name": "alice"})
            assert status == 200 and data["enrollment"]["name"] == "Alice" and data["enrollment"]["state"] == "listening"
            status, data = await api("POST", "speakers/enrollment", {"satellite": MAC, "name": "bob"})
            assert status == 400 and "Already enrolling Alice" in data["error"]
            await wait_for(lambda: app.enrollment.speech_s >= 40, 60)
            status, data = await api("POST", "speakers/enrollment/save")
            assert status == 200 and data["enrollment"]["state"] == "done", data
            assert satellite.monitoring == [True, False]
            status, data = await api("GET", "speakers")
            (alice,) = data["speakers"]
            assert alice["name"] == "Alice" and alice["seconds"] > 30 and alice["color_name"] == "green"
            assert alice["enrolled"][0]["satellite"] == "Kitchen VPE"
            satellite.room_audio = None

            # Her command, a pause shorter than the silence, then someone else.
            wake, command = say(piper, "Okay Nabu.", ALICE), say(piper, COMMAND, ALICE)
            satellite.speed = 2.0
            hass.stt_text = COMMAND  # the speech-to-text keeps the voices apart
            other = [a for t in TALK for a in (say(piper, t, OTHER), 0.2)][:-1]
            audio, starts = call_audio(wake, 0.15, command, 0.3, *other)
            other_start = starts[1]
            await satellite.call(audio, timeout=40)
            await wait_for(lambda: app.calls.recent[-1].outcome != "in progress", 30)
            record = app.calls.recent[-1]
            stages = {s["stage"]: s for s in record.stages}
            others = stages["endpoint"]["others"]
            assert others["speaker"] == "Alice" and others["at"] < others["other_from"] + 2.0, others
            assert record.marks["end"] < other_start + 2.5  # rather than after ~6 s of them
            assert stages["speaker"]["name"] == "Alice"
            assert stages["recognition"]["command"] == "lights_on" and record.outcome == "done"

            # Her going on herself: not cut.
            more = [a for t in TALK for a in (say(piper, t, ALICE), 0.2)][:-1]
            audio, starts = call_audio(wake, 0.15, command, 0.3, *more)
            more_start = starts[1]
            hass.stt_text = COMMAND + " " + " ".join(TALK)
            await satellite.call(audio, timeout=40)
            await wait_for(lambda: app.calls.recent[-1].outcome != "in progress", 30)
            record = app.calls.recent[-1]
            others = next(s for s in record.stages if s["stage"] == "endpoint").get("others", {})
            assert others.get("speaker") == "Alice" and "at" not in others, others
            assert record.marks["end"] > more_start + sum(len(a) / RATE if not isinstance(a, float) else a for a in more)

            # Renamed, recolored, forgotten.
            status, data = await api("PATCH", "speakers/Alice", {"name": "alicia", "color": "violet"})
            assert status == 200 and data["speaker"]["name"] == "Alicia" and data["speaker"]["color_name"] == "violet"
            status, data = await api("PATCH", "speakers/Alicia", {"color": "red"})
            assert status == 400 and "No color red" in data["error"]
            status, data = await api("DELETE", "speakers/Alicia")
            assert status == 200
            assert (await api("GET", "speakers"))[1]["speakers"] == []
            assert (await api("DELETE", "speakers/Alicia"))[0] == 404
    finally:
        runner.cancel()
        await app.stop()
