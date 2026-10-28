"""Speakers, end to end: one enrolled from a satellite while she reads the
passage aloud (Piper's voices), her wake word recorded there, and her command
cut short when someone else starts talking after it -- but not when she goes
on herself.

Needs the models (speech, speaker, Piper's libritts_r voice); skipped without them.
"""

from __future__ import annotations

import asyncio
import json
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
    satellite = FakeSatellite(mac=":".join(MAC[i : i + 2] for i in range(0, 12, 2)), speed=8.0)
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
            # On, with nobody enrolled: the Settings page says so.
            ends = (await api("GET", "status"))[1]["end_of_speech"]
            assert ends["used"]["end_on_other_voice"] is True and ends["voices"] == 0
            assert data["satellites"] == [{"key": MAC, "name": "Kitchen VPE", "enroll": True, "wake_word": "Okay Nabu"}]

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

            # Her wake word, again and again at the satellite (round and round:
            # the room's audio loops); someone else's in between is not kept.
            takes = [a for _ in range(3) for a in (say(piper, "Okay Nabu.", ALICE), 0.8)]
            satellite.room_audio = pcm(0.5, *takes, say(piper, "Okay Nabu.", OTHER), 0.8)
            status, data = await api("POST", "speakers/recording", {"satellite": MAC, "name": "alice", "phrase": "okay nabu!"})
            assert status == 200, data
            recording = data["recording"]
            assert recording["speaker"] == "Alice" and recording["word"] == "okay_nabu" and recording["model"] == "okay_nabu"
            assert (await api("POST", "speakers/enrollment", {"satellite": MAC, "name": "bob"}))[0] == 400
            await wait_for(lambda: app.wake_recording.kept >= 7 and not app.wake_recording.checking, 60)
            status, data = await api("DELETE", "speakers/recording")
            assert data["recording"]["state"] == "done" and satellite.monitoring[-1] is False
            noted = data["recording"]["takes"]
            assert all(t["kept"] and t["heard"] is not None for t in noted if t["kept"])
            assert any(not t["kept"] and t["why"] == "Didn't sound like Alice" for t in noted), [(t["score"], t["kept"]) for t in noted]
            (alice,) = (await api("GET", "speakers"))[1]["speakers"]
            (word,) = alice["wake"]
            assert word["word"] == "okay_nabu" and word["phrase"] == "okay nabu" and word["count"] >= 7
            take = word["takes"][0]
            async with http.get(f"/api/speakers/Alice/wake/okay_nabu/{take['id']}.wav") as response:
                assert response.status == 200 and len(await response.read()) > RATE
            assert (await api("DELETE", f"speakers/Alice/wake/okay_nabu/{take['id']}"))[0] == 200
            assert (await api("GET", "wake_training"))[1]["recorded"] == {
                "okay_nabu": {"phrase": "okay nabu", "speakers": {"Alice": word["count"] - 1}}
            }
            satellite.room_audio = None

            # Her command, then someone else: after a pause shorter than the
            # silence, or straight after with no pause at all.
            wake, command = say(piper, "Okay Nabu.", ALICE), say(piper, COMMAND, ALICE)
            satellite.speed = 2.0
            hass.stt_text = COMMAND  # the speech-to-text keeps the voices apart
            other = [a for t in TALK for a in (say(piper, t, OTHER), 0.2)][:-1]
            for pause in (0.3, 0.0):
                audio, starts = call_audio(wake, 0.15, command, pause, *other)
                other_start = starts[1]
                await satellite.call(audio, timeout=40)
                await wait_for(lambda: app.calls.recent[-1].outcome != "in progress", 30)
                record = app.calls.recent[-1]
                stages = {s["stage"]: s for s in record.stages}
                others = stages["endpoint"]["others"]
                assert others["speaker"] == "Alice" and len(others["scores"]) == 2, others
                # About a second into them (two at most), rather than after ~6 s of
                # them; and her last word about where it was.
                assert record.marks["end"] < other_start + 2.5, (pause, record.marks["end"], other_start)
                said = starts[0] + len(command) / RATE - record.marks["kaldi_split"]
                assert abs(others["last_word"] - said) < 0.35, (pause, others, said)
                assert stages["speaker"]["name"] == "Alice"
                assert stages["recognition"]["command"] == "lights_on" and record.outcome == "done", pause

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

            # Turned off: her command runs on through them, as before.
            status, _ = await api("PUT", "settings", {"end_of_speech": {"end_on_other_voice": False}})
            assert status == 200 and json.loads((tmp_path / "settings.json").read_text())["end_of_speech"]["end_on_other_voice"] is False
            ends = (await api("GET", "status"))[1]["end_of_speech"]
            assert ends["set"]["end_on_other_voice"] is False and ends["voices"] == 1
            hass.stt_text = COMMAND
            audio, starts = call_audio(wake, 0.15, command, 0.0, *other)
            await satellite.call(audio, timeout=40)
            await wait_for(lambda: app.calls.recent[-1].outcome != "in progress", 30)
            record = app.calls.recent[-1]
            assert "others" not in next(s for s in record.stages if s["stage"] == "endpoint")
            assert record.marks["end"] > starts[1] + 4.0
            status, _ = await api("PUT", "settings", {"end_of_speech": {"end_on_other_voice": None}})
            assert "end_on_other_voice" not in (tmp_path / "settings.json").read_text()

            # Renamed, recolored, forgotten.
            status, data = await api("PATCH", "speakers/Alice", {"name": "alicia", "color": "violet"})
            assert status == 200 and data["speaker"]["name"] == "Alicia" and data["speaker"]["color_name"] == "violet"
            assert data["speaker"]["wake"][0]["count"] == word["count"] - 1  # her takes went with her
            status, data = await api("PATCH", "speakers/Alicia", {"color": "red"})
            assert status == 400 and "No color red" in data["error"]
            assert any((tmp_path / "wake_recordings").iterdir())
            status, data = await api("DELETE", "speakers/Alicia")
            assert status == 200
            assert (await api("GET", "speakers"))[1]["speakers"] == []
            assert not any((tmp_path / "wake_recordings").iterdir())
            assert (await api("DELETE", "speakers/Alicia"))[0] == 404
    finally:
        runner.cancel()
        await app.stop()
