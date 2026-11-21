"""Streaming audio to a satellite: wait for each playback's own drain, and
outside a call stay close to the device's clock."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import numpy as np

from voice_incubator import server
from voice_incubator.protocol import BYTE_RATE, OUTPUT_RATE, SAMPLE_RATE, Message
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
