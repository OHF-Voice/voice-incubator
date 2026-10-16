"""Wake words: built in and verified, or custom and run at their own cutoff;
picked for every satellite or per satellite."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import aiohttp
import pytest

from voice_call.server import Options, VoiceCallApp
from voice_call.wake_words import WakeWords, spoken

from . import fake_hass
from .fake_satellite import FakeSatellite, read_call, serve
from .test_app import CALLS, COMMANDS, MAC, free_port, wait_for

ROOT = Path(__file__).resolve().parent.parent
BUILTIN = ROOT / "models/wake"

needs_models = pytest.mark.skipif(
    not (BUILTIN / "hey_jarvis.tflite").exists(), reason="needs models/wake"
)


def custom_word(directory: Path, word_id: str, name: str, cutoff: float, twin: bool = False) -> None:
    """A custom wake word (any model will do: hey_jarvis's), as Tater ships
    them -- optionally with an ESPHome twin manifest beside it."""
    directory.mkdir(parents=True, exist_ok=True)
    shutil.copy(BUILTIN / "hey_jarvis.tflite", directory / f"{word_id}.tflite")
    manifest = {
        "type": "micro",
        "wake_word": name,
        "model": f"{word_id}.tflite",
        "trained_languages": ["en"],
        "micro": {"probability_cutoff": cutoff, "sliding_window_size": 5, "feature_step_size": 10},
    }
    (directory / f"{word_id}.json").write_text(json.dumps(manifest))
    if twin:
        (directory / f"{word_id}.esphome.json").write_text(json.dumps(manifest))
        (directory / f"{word_id}.oww.json").write_text(json.dumps({"type": "openWakeWord", "model": "x.onnx"}))


def test_spoken():
    assert spoken("Hey_Tater!") == "hey tater"
    assert spoken("Dr. Brule") == "dr brule"


@needs_models
def test_catalog(tmp_path):
    custom = tmp_path / "wake_words"
    custom_word(custom, "hey_tater", "Hey Tater", 0.97, twin=True)
    custom_word(custom, "hey_jarvis", "Not Jarvis", 0.9)  # cannot shadow a built-in word
    custom_word(custom, "stop", "Stop", 0.9)  # nor "stop"
    custom_word(custom, "hey_athena", "Hey Athena", 1.0)  # would never fire
    (custom / "broken.tflite").write_bytes(b"not a model")
    (custom / "broken.json").write_text(json.dumps({"type": "micro", "wake_word": "Broken", "model": "broken.tflite", "micro": {}}))

    words = WakeWords(BUILTIN, custom, lambda w: w in ("okay_nabu", "hey_jarvis", "hey_mycroft"))
    catalog = words.scan()
    assert {"okay_nabu", "hey_jarvis", "hey_mycroft", "hey_tater", "broken"} <= set(catalog)
    assert "vad" not in catalog and "stop" not in catalog
    assert catalog["hey_jarvis"].name == "Hey Jarvis" and not catalog["hey_jarvis"].custom
    # Verified words run low; custom ones at their manifest's cutoff.
    assert catalog["okay_nabu"].verified and catalog["okay_nabu"].cutoff == 0.2
    assert catalog["hey_mycroft"].cutoff == 0.7
    tater = catalog["hey_tater"]
    assert tater.custom and not tater.verified and tater.cutoff == 0.97
    assert tater.phrases == ("hey tater",)
    assert catalog["hey_athena"].cutoff == 0.99

    model = words.model("hey_tater")
    assert model is not None and model.wake_word == "hey_tater" and model.probability_cutoff == 0.97
    assert words.model("okay_nabu").probability_cutoff == 0.2
    assert words.model("vad").is_vad and words.model("stop") is not None
    assert words.model("broken") is None and "broken" in words.errors

    # What the device reports back: an id, or (older firmware) a name.
    assert words.resolve("hey_tater") == "hey_tater"
    assert words.resolve("Okay Nabu") == "okay_nabu"
    assert words.resolve("Stop") == "stop"
    assert words.resolve("Hey Tater") == "hey_tater"
    assert words.resolve("nothing") is None

    # A satellite's own pick, else every satellite's, else the default.
    assert words.chosen({}, {}, ("okay_nabu",)) == ["okay_nabu"]
    assert words.chosen({"wake_words": ["hey_jarvis", "gone"]}, {}, ("okay_nabu",)) == ["hey_jarvis"]
    assert words.chosen({"wake_words": ["hey_jarvis"]}, {"wake_words": ["hey_tater"]}, ()) == ["hey_tater"]
    assert words.chosen({}, {"wake_words": ["gone"]}, ("okay_nabu",)) == ["okay_nabu"]

    # The override moves verified words only.
    override = WakeWords(BUILTIN, custom, lambda w: w == "okay_nabu", cutoff_override=0.4)
    assert override.get("okay_nabu").cutoff == 0.4 and override.get("hey_tater").cutoff == 0.97


@pytest.mark.skipif(
    not (ROOT / "models/speech-to-phrase").exists() or not (CALLS / "call-15788.wav").exists(),
    reason="needs the models and recorded calls",
)
def test_wake_words_per_satellite(tmp_path):
    asyncio.run(_per_satellite(tmp_path))


async def _per_satellite(tmp_path: Path) -> None:
    hass, hass_server = await fake_hass.serve()
    hass_port = hass_server.sockets[0].getsockname()[1]
    satellite = FakeSatellite(mac=":".join(MAC[i : i + 2] for i in range(0, 12, 2)), speed=8.0, model_slots=6)
    satellite_server = await serve(satellite)
    satellite_port = satellite_server.sockets[0].getsockname()[1]
    custom_word(tmp_path / "wake_words", "hey_tater", "Hey Tater", 0.97)
    (tmp_path / "commands.yaml").write_text(COMMANDS)
    (tmp_path / "satellites.yaml").write_text(f"{MAC}: {{name: Kitchen VPE, wake_words: [hey_mycroft, hey_tater]}}\n")
    (tmp_path / "settings.yaml").write_text("wake_words: [hey_jarvis]\n")
    http_port = free_port()
    app = VoiceCallApp(
        Options(
            host="127.0.0.1",
            port=satellite_port,
            hass_url=f"http://127.0.0.1:{hass_port}",
            hass_token=fake_hass.TOKEN,
            data_dir=tmp_path,
            http_port=http_port,
            wake_words_dir=tmp_path / "wake_words",
            speaker_id=False,
            threads=4,
        )
    )
    await app.start()
    runner = asyncio.ensure_future(app.run_forever())
    base = f"http://127.0.0.1:{http_port}/api"
    try:
        await asyncio.wait_for(satellite.ready.wait(), 30)
        # Its own pick, after the VAD gate; the custom word at its own cutoff.
        await wait_for(lambda: satellite.models == ["vad", "hey_mycroft", "hey_tater"])
        assert satellite.model_headers["hey_mycroft"]["probability_cutoff"] == 0.7
        assert satellite.model_headers["hey_tater"]["probability_cutoff"] == 0.97
        assert satellite.model_headers["hey_tater"]["wake_word"] == "hey_tater"
        await wait_for(lambda: "hey_mycroft" in app.wake.started)

        async with aiohttp.ClientSession() as http:
            async with http.get(f"{base}/wake_words") as response:
                listed = {w["id"]: w for w in (await response.json())["wake_words"]}
            assert listed["hey_tater"]["custom"] and listed["hey_tater"]["runs"]
            assert listed["okay_nabu"]["verified"]

            # A custom wake word is not verified: the detection stands. (This
            # recording says "okay nabu", which is not looked for after it.)
            assert await satellite.call(read_call(CALLS / "call-15788.wav"), wake_word="hey_tater") != "wake_rejected"
            record = app.calls.recent[-1]
            stages = [s["stage"] for s in record.stages]
            assert "wake_unverified" in stages and "wake_kaldi" not in stages
            assert record.outcome != "wake rejected"

            # Back to every satellite's pick.
            async with http.put(f"{base}/satellites/{MAC}", json={"wake_words": None}) as response:
                assert response.status == 200
            await wait_for(lambda: satellite.models == ["vad", "hey_jarvis"])
            async with http.put(f"{base}/settings", json={"wake_words": ["okay_nabu", "hey_mycroft"]}) as response:
                assert response.status == 200
            await wait_for(lambda: satellite.models == ["vad", "okay_nabu", "hey_mycroft"])
            async with http.put(f"{base}/settings", json={"wake_words": ["nope"]}) as response:
                assert response.status == 400

            # Uploaded, then deleted.
            form = aiohttp.FormData()
            manifest = {"type": "micro", "wake_word": "Hey Spud", "model": "whatever.tflite", "micro": {"probability_cutoff": 0.95}}
            form.add_field("manifest", json.dumps(manifest), filename="hey_spud.json")
            form.add_field("model", (BUILTIN / "hey_mycroft.tflite").read_bytes(), filename="hey_spud.tflite")
            async with http.post(f"{base}/wake_words", data=form) as response:
                assert (await response.json())["id"] == "hey_spud"
            assert app.wake_words.get("hey_spud").cutoff == 0.95
            form = aiohttp.FormData()
            form.add_field("manifest", json.dumps(manifest), filename="bad.json")
            form.add_field("model", b"garbage", filename="bad.tflite")
            async with http.post(f"{base}/wake_words", data=form) as response:
                assert response.status == 400
            assert not (tmp_path / "wake_words/bad.tflite").exists()
            async with http.delete(f"{base}/wake_words/hey_spud") as response:
                assert response.status == 200
            assert app.wake_words.get("hey_spud") is None
            async with http.delete(f"{base}/wake_words/okay_nabu") as response:
                assert response.status == 400
    finally:
        runner.cancel()
        await app.stop()
        satellite_server.close()
        hass_server.close()
