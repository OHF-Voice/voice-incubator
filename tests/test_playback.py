"""Streaming audio to a satellite: wait for each playback's own drain, and
outside a call stay close to the device's clock."""

from __future__ import annotations

import asyncio
import json
import struct
import wave
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiohttp import web

import numpy as np

from voice_incubator import server
from voice_incubator.agent import Reply
from voice_incubator.protocol import BYTE_RATE, HEADER, MAX_PAYLOAD, OUTPUT_RATE, SAMPLE_RATE, Message
from voice_incubator.tts import pcm_at


class Recorder:
    """Stands in for the app: records what _play sends, and when."""

    _play_stream = server.VoiceIncubatorApp._play_stream

    def __init__(self):
        self.options = SimpleNamespace(drain_timeout=5.0)
        self.sent: list[tuple[float, Message, int]] = []

    async def _send(self, session, msg_type, payload=b""):
        self.sent.append((asyncio.get_running_loop().time(), msg_type, len(payload)))


def _session(output_rate: int = SAMPLE_RATE):
    return SimpleNamespace(drained=asyncio.Event(), output_rate=output_rate)


@pytest.mark.parametrize("mode", ["legacy", "display", "congested", "cancelled"])
def test_display_events_do_not_wait_for_the_answer_or_control_audio(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(server, "WakeVerifier", Mock())  # the call is already accepted

    async def scenario():
        app = server.VoiceIncubatorApp(server.Options(data_dir=tmp_path))
        call = server.Call(0, "manual")
        hello = {"protocol": 2}
        if mode != "legacy":
            hello.update(early_transcript=True, reply_chunks=True)
        session = server.Session("test", "test", 0, call=call, hello=hello)
        release_first, release_rest, asking, changed, cancelled = (asyncio.Event() for _ in range(5))
        frames = []
        congested = False

        def write(frame):
            kind, size = HEADER.unpack_from(frame)
            payload = frame[HEADER.size:]
            assert len(payload) == size <= MAX_PAYLOAD
            frames.append((kind, json.loads(payload) if kind in (Message.EVENT, Message.HANGUP) else payload))
            changed.set()

        session.writer = SimpleNamespace(
            transport=SimpleNamespace(get_write_buffer_size=lambda: 65536 if congested else 0,
                                      get_write_buffer_limits=lambda: (16384, 65536)),
            is_closing=lambda: False, write=write, drain=AsyncMock(),
        )
        first, rest = "The answer is ", "forty-two. " + "🌟" * 400

        async def ask(*_, on_text, **_kw):
            asking.set()
            try:
                await release_first.wait()
                on_text(first)
                await release_rest.wait()
                on_text(rest)
                return Reply("conversation.test", first + rest, understood=True)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        app.agent.ask = ask
        app.agent_for = lambda _: "conversation.test"
        app.engine = SimpleNamespace(recognize_call=lambda *_a, **_kw: (
            server.Decision({"tdt_transcript": "what is the answer"}), None,
        ))
        app._transcriber = AsyncMock(return_value=None)
        app._area_voice = AsyncMock(return_value=(None, None))
        app.speech.synthesize = lambda *_: b"\x12\x34"

        def events(kind):
            return [data["data"] for msg, data in frames if msg == Message.EVENT and data["type"] == kind]

        async def wait_for(predicate):
            while not predicate():
                changed.clear()
                await changed.wait()

        task = call.responding = asyncio.create_task(app._respond_unguarded(session, call))
        await asking.wait()
        assert events("transcript") == ([{"text": "what is the answer"}] if mode != "legacy" else [])
        assert not events("recognized")
        congested = mode == "congested"
        release_first.set()
        await wait_for(lambda: events("responding"))
        # Let the caption task run while the agent has supplied no complete sentence.
        if mode in ("display", "cancelled"):
            await wait_for(lambda: events("reply_chunk"))
            assert events("reply_chunk") == [{"text": first}]
        else:
            await asyncio.sleep(0)
            assert not events("reply_chunk")
        assert events("responding") == [{"text": ""}]
        assert not any(msg == Message.AUDIO_OUT for msg, _ in frames)
        if mode == "cancelled":
            await app._handle(session, Message.CALL_END, b'{"reason":"cancelled"}')
            with pytest.raises(asyncio.CancelledError):
                await task
            await cancelled.wait()
            assert frames[-1] == (Message.AUDIO_OUT_END, b"")
            assert events("reply_chunk") == [{"text": first}]
            return

        congested = False
        release_rest.set()
        await wait_for(lambda: any(msg == Message.AUDIO_OUT_END for msg, _ in frames))
        assert not any(msg == Message.HANGUP for msg, _ in frames)
        await app._handle(session, Message.PLAYBACK_DRAINED, b"")
        await task
        assert [frame for frame in frames if frame[0] != Message.EVENT] == [
            (Message.AUDIO_OUT, b"\x12\x34"), (Message.AUDIO_OUT_END, b""), (Message.HANGUP, {"reason": "done"}),
        ]
        assert "".join(chunk["text"] for chunk in events("reply_chunk")) == (first + rest if mode == "display" else "")

    asyncio.run(asyncio.wait_for(scenario(), 5))


@pytest.mark.parametrize("output_rate", [SAMPLE_RATE, OUTPUT_RATE])
@pytest.mark.parametrize("fail_at", [None, 1, 2])
def test_selected_tts_plays_over_the_call_without_a_media_player(fail_at, output_rate):
    # A satellite with no media player in Home Assistant: the TTS entity's
    # audio is played over the call, at the satellite's rate. What it fails
    # to say (the first sentence, or the second of three), Piper says, with
    # the rest.
    async def scenario():
        wav = BytesIO()
        with wave.open(wav, "wb") as file:
            file.setparams((2, 2, SAMPLE_RATE * 2, 0, "NONE", "not compressed"))
            file.writeframes(struct.pack("<hh", 12000, 4000) * 640)
        requests = []

        async def tts_url(request):
            assert request.headers["Authorization"] == "Bearer test-token"
            requests.append(await request.json())
            return web.json_response({"url": "https://unused.invalid/api/tts_proxy/test.wav", "path": "/api/tts_proxy/test.wav"})

        async def get_audio(_request):
            if len(requests) == fail_at:
                return web.Response(body=b"not audio")
            return web.Response(body=wav.getvalue(), content_type="audio/wav")

        rest = web.Application()
        rest.router.add_post("/api/tts_get_url", tts_url)
        rest.router.add_get("/api/tts_proxy/test.wav", get_audio)
        runner = web.AppRunner(rest)
        await runner.setup()
        try:
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            port = runner.addresses[0][1]
            app = Recorder()
            app.hass = server.HomeAssistant(f"http://127.0.0.1:{port}", "test-token")
            app.voices = SimpleNamespace(tts_language=AsyncMock(return_value="de-DE"))
            app._quiet = AsyncMock()
            app._area_voice = AsyncMock(return_value=(None, None))
            app._model_lock = asyncio.Lock()
            app.speech = SimpleNamespace(synthesize=Mock(return_value=b"\x00\x80"), sound=AsyncMock(return_value=b""))
            frames = []

            async def send(session, message, payload=b""):
                frames.append((message, payload))
                if message == Message.AUDIO_OUT_END:
                    session.drained.set()

            app._send = send
            satellite = server.Satellite("test", "Test satellite", voice=server.choose({"tts": "tts.default"}, {}))
            satellite = server.VoiceIncubatorApp.voice_for(
                app, satellite, server.people.Speaker(
                    name="Anna", by="voice", own={"tts": "tts.wyoming", "tts_voice": "chosen", "tts_language": "de-DE"},
                ),
            )
            answer = "First sentence."
            if fail_at == 2:
                answer = server.Streamed()
                answer.put("First sentence. Second sentence. Third sentence.")
                answer.end()
            record = server.CallRecord("test", "call", satellite.as_dict())
            await server.VoiceIncubatorApp._say(app, _session(output_rate), answer, record, satellite=satellite)
        finally:
            await runner.cleanup()

        sentences = ["First sentence.", "Second sentence.", "Third sentence."] if fail_at == 2 else ["First sentence."]
        assert requests == [
            {"engine_id": "tts.wyoming", "message": text, "cache": True, "language": "de-DE",
             "options": {"preferred_format": "wav", "voice": "chosen"}}
            for text in sentences[: fail_at or 1]
        ]
        by_piper = sentences[fail_at - 1 :] if fail_at else []
        assert [call.args[0] for call in app.speech.synthesize.call_args_list] == by_piper
        assert all(call.args[3] == output_rate for call in app.speech.synthesize.call_args_list)
        assert [stage["tts"] for stage in record.stages if stage["stage"] == "speech_failed"] == (
            ["tts.wyoming"] if fail_at else []
        )
        speech = record.stages[-1]
        assert speech["stage"] == "speech" and speech["text"] == " ".join(sentences)
        assert speech.get("tts") == (None if fail_at == 1 else "tts.wyoming")
        assert [kind for kind, _ in frames].count(Message.AUDIO_OUT_END) == 1
        pcm = b"".join(payload for kind, payload in frames if kind == Message.AUDIO_OUT)
        # 640 frames at 32 kHz, resampled once to the satellite's rate.
        entity = 0 if fail_at == 1 else 640 * output_rate // (SAMPLE_RATE * 2) * 2
        assert len(pcm) == entity + 2 * len(by_piper)
        if entity:
            assert struct.unpack_from("<h", pcm, entity // 2)[0] == pytest.approx(8000, abs=20)

    asyncio.run(scenario())


def test_a_stale_drained_does_not_end_the_next_playback():
    async def scenario():
        app, session = Recorder(), _session()
        session.drained.set()  # left over from the previous response
        play = asyncio.ensure_future(server.VoiceIncubatorApp._play(app, session, bytes(8192)))
        await asyncio.sleep(0.1)
        assert not play.done()  # still waiting for this playback's drain
        session.drained.set()
        await asyncio.wait_for(play, 1)

    asyncio.run(scenario())


def test_pieces_play_as_one_stream():
    # A reply said a sentence at a time: each sent as it comes, one end.
    async def scenario():
        app, session = Recorder(), _session()

        async def sentences():
            for size in (4096, 6000, 100):
                await asyncio.sleep(0.05)  # being synthesized
                yield bytes(size)

        play = asyncio.ensure_future(server.VoiceIncubatorApp._play_stream(app, session, sentences()))
        while not any(m == Message.AUDIO_OUT_END for _, m, _ in app.sent):
            await asyncio.sleep(0.02)
        sizes = [n for _, m, n in app.sent if m == Message.AUDIO_OUT]
        assert sum(sizes) == 10196 and [m for _, m, _ in app.sent].count(Message.AUDIO_OUT_END) == 1
        assert app.sent[-1][0] - app.sent[0][0] >= 0.09  # not held back until the last
        session.drained.set()
        await asyncio.wait_for(play, 1)

    asyncio.run(scenario())


def test_paced_audio_stays_just_ahead_of_the_device():
    async def scenario():
        app, session = Recorder(), _session()
        pcm = bytes(int(1.0 * BYTE_RATE))  # one second
        play = asyncio.ensure_future(server.VoiceIncubatorApp._play(app, session, pcm, paced=True))
        while not any(m == Message.AUDIO_OUT_END for _, m, _ in app.sent):
            await asyncio.sleep(0.02)
        start = app.sent[0][0]
        total = 0
        for at, msg, size in app.sent:
            if msg == Message.AUDIO_OUT:
                # Never more than the lead (plus one chunk) ahead of real time.
                assert total / BYTE_RATE - (at - start) <= server.PLAY_LEAD + server.MAX_PAYLOAD / BYTE_RATE + 0.05
                total += size
        assert app.sent[-1][0] - start >= 1.0 - server.PLAY_LEAD - 0.2
        session.drained.set()
        await asyncio.wait_for(play, 1)

    asyncio.run(scenario())


def test_what_follows_the_sounds_waits_a_beat():
    # A command done at once: the acknowledgement doesn't run straight on from
    # the heard sound; done later, it doesn't wait.
    async def scenario():
        app, session = Recorder(), _session()
        cues = server.Cues(app, session)
        loop = asyncio.get_running_loop()
        begun = loop.time()
        await cues.play(bytes(int(0.3 * BYTE_RATE)))
        await cues.pause(0.2)
        assert loop.time() - begun >= 0.49
        await asyncio.sleep(0.3)
        begun = loop.time()
        await cues.pause(0.2)
        assert loop.time() - begun < 0.05

    asyncio.run(scenario())


def test_a_48_khz_satellite_gets_everything_at_48_khz():
    """A satellite told OUTPUT_RATE (CONFIG): the app's sounds, made at
    16 kHz, are resampled; speech made at its rate goes as it is; pacing
    follows its clock."""
    async def scenario():
        app, session = Recorder(), _session(OUTPUT_RATE)
        tone = (np.sin(np.arange(1600) / 5) * 8000).astype("<i2").tobytes()  # 0.1 s at 16 kHz
        spoken = bytes(OUTPUT_RATE * 2 // 10)  # 0.1 s at 48 kHz

        async def pieces():
            yield tone
            yield spoken, OUTPUT_RATE

        play = asyncio.ensure_future(server.VoiceIncubatorApp._play_stream(app, session, pieces(), paced=True))
        while not any(m == Message.AUDIO_OUT_END for _, m, _ in app.sent):
            await asyncio.sleep(0.02)
        sent = sum(n for _, m, n in app.sent if m == Message.AUDIO_OUT)
        assert sent == len(pcm_at(tone, SAMPLE_RATE, OUTPUT_RATE)) + len(spoken)
        assert abs(sent - 2 * len(spoken)) <= 4  # 0.2 s at 48 kHz
        session.drained.set()
        await asyncio.wait_for(play, 1)

    asyncio.run(scenario())


def test_resampled_speech_is_turned_down_not_clipped():
    """Full scale at 22 kHz overshoots once resampled; it is scaled back
    into range rather than clipped."""
    from voice_incubator.tts import _to_device_pcm
    rng = np.random.default_rng(0)
    loud = rng.uniform(-1, 1, 22050).astype(np.float32)
    loud /= np.abs(loud).max()
    out = np.frombuffer(_to_device_pcm(loud, 22050, OUTPUT_RATE), dtype="<i2")
    assert len(out) == OUTPUT_RATE and np.abs(out.astype(np.int32)).max() <= 32767
    assert np.mean(np.abs(out) >= 32766) < 1e-4  # scaled, not clipped flat
