"""Home Assistant STT and TTS entities in place of the built-in models."""

from __future__ import annotations

import asyncio

import numpy as np
import pytest

from voice_call.engine import free_text
from voice_call.hass import HassError, HomeAssistant
from voice_call.voices import HassTranscriber, VoiceChoice, Voices, choose

from . import fake_hass


def test_every_satellite_unless_its_own():
    settings = {"stt": "stt.whisper", "tts": "tts.cloud", "tts_voice": "Jenny"}
    assert choose(settings, {}) == VoiceChoice("stt.whisper", "tts.cloud", "Jenny")
    assert choose(settings, {"stt": "builtin", "tts_voice": "default"}) == VoiceChoice(None, "tts.cloud", None)
    assert choose(settings, {"tts": "tts.piper", "speak_on": "media_player.kitchen"}) == VoiceChoice(
        "stt.whisper", "tts.piper", "Jenny", "media_player.kitchen"
    )
    # Speaking elsewhere needs a TTS entity; the built-in voice is the satellite's.
    assert choose({}, {"speak_on": "media_player.kitchen", "tts_voice": "Jenny"}) == VoiceChoice()


@pytest.mark.parametrize(
    ("transcript", "wildcard", "heard", "expected"),
    [
        ("Play the album Kind of Blue.", {"after": ["play"], "to_end": True}, "", "the album Kind of Blue"),
        ("add oat milk to the shopping list", {"after": ["add"], "before": ["to"], "bounded": True}, "", "oat milk"),
        ("cancel the pizza timer", {"before": ["timer"]}, "pizza", "pizza"),
        ("cancel the deep dish pizza timer", {"before": ["timer"]}, "deep dish pizza", "deep dish pizza"),
        ("a timer named chicken nuggets", {"after": ["named"], "before": ["timer"]}, "chicken nuggets", "chicken nuggets"),
        ("remind me to call mom", {"after": ["to"], "to_end": True}, "", "call mom"),
        # A keyword more than once: the place closest to what the CTC head heard.
        (
            "Boo, How much time is left on the pizza timer?",
            {"before": ["timer", "time"], "after": ["named", "called", "name"]},
            "pizza",
            "pizza",
        ),
        ("set a timer named lasagna for the time being", {"before": ["timer", "time"], "after": ["named"]}, "lasagna", "lasagna"),
        ("what time is it", {"after": ["play"], "to_end": True}, "", None),
        ("add to the queue", {"after": ["add"], "before": ["to"], "bounded": True}, "", None),
    ],
)
def test_free_text_from_a_transcript_without_times(transcript, wildcard, heard, expected):
    assert free_text(transcript, wildcard, heard) == expected


class StubHass:
    def __init__(self, text=None, error=None):
        self.text, self.error, self.calls = text, error, []

    async def speech_to_text(self, entity_id, pcm, language):
        self.calls.append((entity_id, len(pcm), language))
        if self.error:
            raise HassError(self.error)
        return self.text


class StubTdt:
    def transcribe(self, audio):
        return "from tdt"


def test_the_transcriber_asks_once_from_the_engines_thread():
    async def scenario():
        hass = StubHass("turn on the lights")
        transcriber = HassTranscriber(hass, "stt.whisper", "en-US", asyncio.get_running_loop(), StubTdt())
        audio = np.zeros(16000, dtype=np.float32)
        first = await asyncio.to_thread(transcriber.transcribe, audio)
        again = await asyncio.to_thread(transcriber.transcribe_words, audio)
        assert first == "turn on the lights" and again == ("turn on the lights", None)
        assert hass.calls == [("stt.whisper", 32000, "en-US")]
        assert transcriber.source == "stt.whisper"

        failing = HassTranscriber(StubHass(error="503"), "stt.whisper", "en-US", asyncio.get_running_loop(), StubTdt())
        assert await asyncio.to_thread(failing.transcribe, audio) == "from tdt"
        assert failing.source == "TDT" and "503" in failing.error

    asyncio.run(scenario())


def test_through_home_assistant():
    async def scenario():
        fake, server = await fake_hass.serve()
        rest, rest_port = await fake_hass.serve_rest(fake)
        port = server.sockets[0].getsockname()[1]
        hass = HomeAssistant(f"http://127.0.0.1:{rest_port}", fake_hass.TOKEN, f"ws://127.0.0.1:{port}/api/websocket")
        await hass.start()
        try:
            assert await hass.wait_connected(5)
            voices = Voices(hass)
            engines = await voices.engines()
            assert [e["entity_id"] for e in engines["stt"]] == ["stt.fake_whisper"]
            assert await voices.stt_language("stt.fake_whisper") == "en-US"
            assert [v["voice_id"] for v in await voices.voices("tts.fake_cloud")] == ["JennyNeural", "GuyNeural"]
            fake.stt_text = "play kind of blue"
            assert await hass.speech_to_text("stt.fake_whisper", bytes(3200), "en-US") == "play kind of blue"
            assert fake.stt_requests[-1]["header"].endswith("language=en-US")
            assert fake.stt_requests[-1]["bytes"] == 3200
            assert hass.catalog.device_media_player("aabbccddeeff") == "media_player.fake_vpe_media_player"
            with pytest.raises(HassError):
                await voices.stt_language("stt.nope")
        finally:
            await hass.stop()
            await rest.cleanup()
            server.close()

    asyncio.run(scenario())
