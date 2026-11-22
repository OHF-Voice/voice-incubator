"""Streaming audio to a satellite: wait for each playback's own drain, and
outside a call stay close to the device's clock."""

from __future__ import annotations

import asyncio
import struct
import wave
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiohttp import web

from voice_incubator import server
from voice_incubator.commands import parse_command
from voice_incubator.protocol import BYTE_RATE, SAMPLE_RATE, Message

from .test_actions import TEA, StubHass, StubTimers


class Recorder:
    """Stands in for the app: records what _play sends, and when."""

    _play_stream = server.VoiceIncubatorApp._play_stream

    def __init__(self):
        self.options = SimpleNamespace(drain_timeout=5.0)
        self.sent: list[tuple[float, Message, int]] = []

    async def _send(self, session, msg_type, payload=b""):
        self.sent.append((asyncio.get_running_loop().time(), msg_type, len(payload)))


def _session():
    return SimpleNamespace(drained=asyncio.Event())


@pytest.mark.parametrize("streaming_wav,fail_at,cue_pending", [
    (False, None, False), (True, None, False), (True, 1, False), (True, 1, True), (True, 2, False),
])
def test_selected_tts_plays_over_the_call_without_a_media_player(streaming_wav, fail_at, cue_pending):
    async def scenario():
        wav = BytesIO()
        pcm = struct.pack("<hh", 12000, 4000) * 640
        with wave.open(wav, "wb") as file:
            file.setparams((2, 2, SAMPLE_RATE * 2, 0, "NONE", "not compressed"))
            if not streaming_wav:
                file.writeframes(pcm)
        # Home Assistant's Wyoming integration sends a zero-frame header
        # before the PCM, without rewriting the header at the end.
        encoded = wav.getvalue() + (pcm if streaming_wav else b"")
        requests = []

        async def tts_url(request):
            assert request.headers["Authorization"] == "Bearer test-token"
            requests.append(await request.json())
            return web.json_response({"url": "https://unused.invalid/api/tts_proxy/test.wav", "path": "/api/tts_proxy/test.wav"})

        async def get_audio(request):
            if len(requests) == fail_at:
                return web.Response(body=b"not audio")
            if streaming_wav:
                # A clean HTTP 200/EOF is also what HA sends if the provider
                # stops early after producing these frames.
                response = web.StreamResponse(headers={"Content-Type": "audio/wav"})
                await response.prepare(request)
                await response.write(encoded)
                await response.write_eof()
                return response
            return web.Response(body=encoded, content_type="audio/wav")

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
            app._quiet = server.VoiceIncubatorApp._quiet.__get__(app)
            app.options.drain_timeout = 0.1
            app._area_voice = AsyncMock(return_value=(None, None))
            app._model_lock = asyncio.Lock()
            app.speech = SimpleNamespace(synthesize=Mock(return_value=b"\x00\x80"), sound=AsyncMock(return_value=b""))
            frames = []
            open_stream = False

            async def send(session, message, payload=b""):
                nonlocal open_stream
                frames.append((message, payload))
                if message == Message.AUDIO_OUT:
                    open_stream = True
                if message == Message.AUDIO_OUT_END and open_stream:
                    open_stream = False
                    session.drained.set()

            app._send = send
            session = _session()
            session.call = None
            if cue_pending:
                session.call = SimpleNamespace(cues=server.Cues(app, session))
                await session.call.cues.play(b"\x00\x01")
            elif fail_at == 1:
                # A timer's ring (or an announcement's chime) already drained.
                await server.VoiceIncubatorApp._play(app, session, b"\x00\x01")
            frames.clear()
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
            result = await server.VoiceIncubatorApp._say(app, session, answer, record, satellite=satellite)
        finally:
            await runner.cleanup()

        assert requests == [
            {"engine_id": "tts.wyoming", "message": text, "cache": True, "language": "de-DE",
             "options": {"preferred_format": "wav", "voice": "chosen"}}
            for text in (["First sentence.", "Second sentence."] if fail_at == 2 else ["First sentence."])
        ]
        app.speech.synthesize.assert_not_called()
        assert [stage["tts"] for stage in record.stages if stage["stage"] == "speech_failed"] == (
            ["tts.wyoming"] if fail_at else []
        )
        assert [stage["text"] for stage in record.stages if stage["stage"] == "speech"] == (
            [] if fail_at == 1 else ["First sentence."]
        )
        assert [kind for kind, _ in frames].count(Message.AUDIO_OUT_END) == (0 if fail_at == 1 and not cue_pending else 1)
        assert result == ("failed" if fail_at else "unverified")
        pcm = b"".join(payload for kind, payload in frames if kind == Message.AUDIO_OUT)
        if fail_at == 1:
            assert not pcm
        else:
            assert len(pcm) == 640
            assert struct.unpack_from("<h", pcm, 320)[0] == pytest.approx(8000, abs=5)

    asyncio.run(scenario())


@pytest.fixture
def call_playback():
    app = Recorder()
    app.language = server.languages.load("en")
    app.voice_for = lambda selected, _who: selected
    for name in ("_asks_back", "_say", "_carry_out", "_run_command", "_ask_agent", "_agent_answered", "agent_voice"):
        setattr(app, name, getattr(server.VoiceIncubatorApp, name).__get__(app))
    app._quiet = AsyncMock()
    app._model_lock = asyncio.Lock()
    app._area_voice = AsyncMock(return_value=(None, None))
    pcm = b"\x00\x10" * 320
    app.speech = SimpleNamespace(synthesize=Mock(return_value=pcm))
    wav = BytesIO()
    with wave.open(wav, "wb") as file:
        file.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
    app.hass = StubHass()
    app.hass.text_to_speech = AsyncMock(return_value=wav.getvalue() + pcm)
    app.voices = SimpleNamespace(tts_language=AsyncMock(return_value="en"))
    app._save = app._feed_kaldi = app._after_identify = Mock()
    app.identifies = lambda: False
    app.speaking = lambda *_: server.people.Speaker()
    app._speaker_event = lambda _who: {}
    app._speaker_stop = lambda _call: None
    app._transcriber = AsyncMock(return_value=None)
    app.wake_words = SimpleNamespace(phrases=lambda _word: [])
    app._wake_json = lambda _word: None
    app._referring = app._listed = AsyncMock(side_effect=lambda decision, *_: decision)
    app._cue, app._event, app._hang_up = AsyncMock(), AsyncMock(), AsyncMock()

    async def send(session, message, payload=b""):
        await Recorder._send(app, session, message, payload)
        if message == Message.AUDIO_OUT_END:
            session.drained.set()

    app._send = send
    return app


@pytest.mark.parametrize("direct_tts", [False, True])
@pytest.mark.parametrize("disambiguate", [False, True])
def test_follow_up_commands_do_not_ask_unanswerable_questions(call_playback, direct_tts, disambiguate):
    async def scenario():
        app = call_playback
        satellite = server.Satellite(
            "test", "Test satellite", timer_list="voice_timer_list.test",
            voice=server.choose({"tts": "tts.wyoming"} if direct_tts else {}, {}),
        )
        app.satellite = lambda _key: satellite
        transcript = "Pause the timer" if disambiguate else "Cancel all timers"
        command = parse_command("timer", {"description": transcript, "examples": [transcript], **(
            {"timer": "pause"} if disambiguate else {"timer": "cancel_all", "confirm": "{{ timers | count > 1 }}"}
        )})
        decision = server.Decision({"transcript": transcript}, command, command.behavior, "command")
        app.engine = SimpleNamespace(recognize_call=Mock(return_value=(decision, None)))
        app.integration = StubTimers([
            {**TEA, "status": "active"}, {**TEA, "status": "active", "timer_id": "4", "name": "pizza"},
        ])
        app.music = app.alarms = app.agent = None
        app.beep_elsewhere = app.voice_devices = lambda: None
        app.said_names = lambda _area: None
        app.store = SimpleNamespace(people={})
        app.context = SimpleNamespace(remember=Mock())
        call = server.Call(0, "test")
        session = SimpleNamespace(call=call, device=SimpleNamespace(listen=True), satellite="test", drained=asyncio.Event())
        await server.VoiceIncubatorApp._respond_unguarded(app, session, call)

        assert not app.hass.services  # confirmation or choice was never skipped
        assert any(message == Message.AUDIO_OUT and size for _, message, size in app.sent)
        follow_up = app._hang_up.await_args.kwargs["listen"]
        if direct_tts:
            assert follow_up is None
            app._cue.assert_not_awaited()
            app.speech.synthesize.assert_not_called()
            assert [call.args[1] for call in app.hass.text_to_speech.await_args_list] == (
                ["Say the full command again and specify which one you mean."] if disambiguate else
                ["I can't listen for confirmation with this voice here.", "I didn't run the command."]
            )
        else:
            assert follow_up.kind == ("which" if disambiguate else "confirm")
            assert follow_up.question.endswith("?")
            app._cue.assert_awaited_once_with(session, "listening")

    asyncio.run(scenario())


@pytest.mark.parametrize("streamed", [False, True])
@pytest.mark.parametrize("route", ["builtin", "direct", "timeout"])
def test_agent_question_playback_and_follow_up(call_playback, tmp_path, streamed, route):
    async def scenario():
        app = call_playback
        satellite = server.Satellite("test", "Test satellite", area_id="kitchen")
        app.satellite = lambda _key: satellite
        app.store = SimpleNamespace(settings={"agent_voice": {"tts": "tts.wyoming", "tts_voice": "agent voice"}} if route != "builtin" else {})
        app.calls = server.CallLog(tmp_path, {})
        app._call_audio = server.VoiceIncubatorApp._call_audio
        if route == "timeout":
            app.hass.text_to_speech.side_effect = TimeoutError()
        app.agent = server.Agent(app.hass, "conversation.test" if streamed else server.HOME_ASSISTANT_AGENT)
        requests = []

        def respond(message):
            requests.append(message)
            first = len(requests) == 1
            # The first question relies on the result flag, not punctuation.
            return {"conversation_id": "kept", "continue_conversation": first, "response": {
                "speech": {"plain": {"speech": "Tell me which room." if first else "The kitchen is ready."}},
            }}

        async def subscribe(message, handler):
            result = respond(message)
            text = result["response"]["speech"]["plain"]["speech"]
            handler({"type": "delta", "delta": {"role": "assistant", "content": text}})
            handler({"type": "result", "result": result})
            return 1, None

        app.hass.call = AsyncMock(side_effect=lambda message, **_: respond(message))
        app.hass.command = lambda name: name
        app.hass.subscribe_result = subscribe
        app.hass.unsubscribe = AsyncMock()
        session = SimpleNamespace(device=SimpleNamespace(listen=True), satellite="test", drained=asyncio.Event())

        async def wake_and_say(text):
            session.call = server.Call(0, "test", record=app.calls.begin("call", satellite.as_dict()))
            app.engine = SimpleNamespace(recognize_call=Mock(return_value=(server.Decision({"transcript": text}), None)))
            await server.VoiceIncubatorApp._respond_unguarded(app, session, session.call)

        await wake_and_say("Can you set that up?")
        follow_up = app._hang_up.await_args.kwargs["listen"]
        if route == "builtin":
            assert follow_up.kind == "agent"
            assert [call.args[0] for call in app.speech.synthesize.call_args_list] == ["Tell me which room."]
            return

        assert follow_up is None
        if route == "timeout":
            record = app.calls.recent[-1]
            compact = record.to_json(full=False)
            assert compact["outcome"] == "answered" and compact["speech_failed"] is True
            assert "said" not in compact and not app.sent
            assert [s["error"] for s in record.stages if s["stage"] == "speech_failed"] == ["TTS request timed out"]
            app.speech.synthesize.assert_not_called()
            return

        assert requests[0].get("conversation_id") is None
        await wake_and_say("In the kitchen")
        assert requests[1]["conversation_id"] == "kept"
        assert [call.args for call in app.hass.text_to_speech.await_args_list] == [
            ("tts.wyoming", text, "en", "agent voice")
            for text in ["Tell me which room.", "Use the wake word before replying.", "The kitchen is ready."]
        ]
        assert [call.args[1] for call in app._cue.await_args_list] == ["asking", "asking"]
        app.speech.synthesize.assert_not_called()

    asyncio.run(scenario())


@pytest.mark.parametrize("route,expected", [
    ("builtin", ["reminder said", "announced"]),
    ("partial", ["reminder delivery unverified", "announcement delivery unverified"]),
    ("failed", ["reminder not said", "not announced"]),
])
def test_one_shot_delivery_reports_the_speech_result(tmp_path, route, expected):
    async def scenario():
        app = Recorder()
        app.calls = server.CallLog(tmp_path, {})
        satellite = server.Satellite(
            "test", "Test satellite", voice=server.choose({} if route == "builtin" else {"tts": "tts.wyoming"}, {}),
            settings={"instrument": "", "pitch": 0},
        )
        app.satellite = lambda _key: satellite
        app._say = server.VoiceIncubatorApp._say.__get__(app)
        app._busy = server.VoiceIncubatorApp._busy.__get__(app)
        app._quiet = app._play = app._event = AsyncMock()
        app._stop_rings = Mock()
        app._after_ring = Mock(return_value=None)
        app._spawn = Mock()
        app.options.timer_ring_seconds = 10
        app.wake_words = SimpleNamespace(model=lambda _word: None)
        app.voices = SimpleNamespace(tts_language=AsyncMock(return_value="en"))
        app._area_voice = AsyncMock(return_value=(None, None))
        app._model_lock = asyncio.Lock()
        pcm = b"\x00\x10" * 320
        app.speech = SimpleNamespace(ring=AsyncMock(return_value=b"\x00\x10"), synthesize=Mock(return_value=pcm))
        wav = BytesIO()
        with wave.open(wav, "wb") as file:
            file.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
        # HA's clean EOF after a streaming header and a little PCM cannot
        # prove that the entire requested sentence made it out of the engine.
        app.hass = SimpleNamespace(text_to_speech=AsyncMock(
            return_value=wav.getvalue() + pcm, side_effect=TimeoutError() if route == "failed" else None,
        ))
        session = server.Session("test", "test", 0, writer=Mock(), satellite="test")
        session.ringing_timers = [{"name": "Bins", "announce": "Take the bins out.", "once": True, "timer_id": "bins"}]

        async def send(session, message, payload=b""):
            if message == Message.AUDIO_OUT_END:
                session.drained.set()

        app._send = send
        await server.VoiceIncubatorApp._ring(app, session)
        await server.VoiceIncubatorApp._announce(app, session, "Dinner is ready.", "")

        assert [record.outcome for record in app.calls.recent] == expected
        for record in app.calls.recent:
            assert [stage["error"] for stage in record.stages if stage["stage"] == "speech_failed"] == (
                ["TTS request timed out"] if route == "failed" else []
            )
            speech = next((stage for stage in record.stages if stage["stage"] == "speech"), None)
            compact = record.to_json(full=False)
            assert compact.get("speech_failed", False) is (route == "failed")
            if route == "failed":
                assert speech is None and "said" not in compact
            else:
                assert compact["said"] == speech["text"]
                assert compact.get("speech_unverified", False) is (route == "partial")
        app._stop_rings.assert_not_called()
        if route != "builtin":
            app.speech.synthesize.assert_not_called()

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
