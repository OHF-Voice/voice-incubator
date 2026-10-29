"""Piper's voices, downloaded the first time one is used (voice_labs/piper_voices.py)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
import pytest

from voice_labs import languages
from voice_labs.piper_voices import catalog, synthesis, voices
from voice_labs.tts import Speech

ROOT = Path(__file__).resolve().parent.parent
KRISTIN = ROOT / "models/piper/en_US-kristin-medium.onnx"


def test_the_catalog_has_english_voices_to_download(tmp_path):
    """English's voice is one Piper's catalog has; each voice's files, to
    download into models/piper, checked by MD5; its sample, the repository's."""
    english = languages.load("en")
    listed = voices(english.piper_languages)
    assert english.piper.removesuffix(".onnx") in {v.key for v in listed}
    assert {v.language for v in listed} == {"en_US", "en_GB"} and len(listed) >= 30
    assert [v.key for v in listed] == sorted((v.key for v in listed), key=lambda k: (k.split("-")[0], k))
    kristin = catalog()["en_US-kristin-medium"]
    todo = kristin.missing(tmp_path)
    assert sorted(path.name for path, _ in todo) == [
        "en_US-kristin-medium.MODEL_CARD", "en_US-kristin-medium.onnx", "en_US-kristin-medium.onnx.json"]
    assert all(file.algorithm == "md5" and kristin.revision in file.url for _, file in todo)
    assert kristin.sample().endswith("en/en_US/kristin/medium/samples/speaker_0.mp3")
    vctk = catalog()["en_GB-vctk-medium"]
    assert vctk.sample(vctk.speakers[3]).endswith("samples/speaker_3.mp3")
    assert kristin.model(tmp_path) == tmp_path / "en_US-kristin-medium.onnx"


def test_settings_as_piper_takes_them():
    assert synthesis({}, {}) == {}
    assert synthesis({"voice": "x", "length_scale": "1.5", "volume": 0.5, "speaker": "p226"}, {"p225": 0, "p226": 1}) == {
        "length_scale": 1.5, "volume": 0.5, "speaker_id": 1}
    assert synthesis({"speaker": "nobody"}, {"p225": 0}) == {}


@pytest.mark.skipif(not KRISTIN.is_file(), reason="needs Kristin's voice")
def test_a_voice_speaks_as_set():
    """Slower with a longer length scale, quieter with less volume; the
    voice's own settings as its defaults; another voice from now on."""
    settings: dict = {}
    speech = Speech(KRISTIN, ROOT / "sounds", synthesis=lambda: settings)
    asyncio.run(speech.start())
    defaults = speech.defaults
    assert defaults["speakers"] == [] and 0.5 < defaults["noise_scale"] < 1 and defaults["volume"] == 1.0

    def said(**chosen):
        pcm = np.frombuffer(speech.synthesize("Turned on the lights in the kitchen.", chosen or None), "<i2")
        return len(pcm), float(np.sqrt(np.mean(pcm.astype(np.float64) ** 2)))

    length, loudness = said()
    assert said(length_scale=2.0)[0] > 1.4 * length  # Piper varies each time: 1.7-2x
    assert said(volume=0.5)[1] < 0.7 * loudness
    settings["length_scale"] = 2.0  # the Settings page's, asked each time
    assert len(speech.synthesize("Turned on the lights in the kitchen.")) > 1.4 * length * 2
    other = ROOT / "models/piper/en_US-ellie-medium.onnx"
    if other.is_file():
        asyncio.run(speech.use(other))
        assert speech.voice_path == other
