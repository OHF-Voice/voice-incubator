"""Streaming audio to a satellite: wait for each playback's own drain, and
outside a call stay close to the device's clock."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from voice_labs import server
from voice_labs.protocol import BYTE_RATE, Message


class Recorder:
    """Stands in for the app: records what _play sends, and when."""

    _play_stream = server.VoiceLabsApp._play_stream

    def __init__(self):
        self.options = SimpleNamespace(drain_timeout=5.0)
        self.sent: list[tuple[float, Message, int]] = []

    async def _send(self, session, msg_type, payload=b""):
        self.sent.append((asyncio.get_running_loop().time(), msg_type, len(payload)))


def _session():
    return SimpleNamespace(drained=asyncio.Event())


def test_a_stale_drained_does_not_end_the_next_playback():
    async def scenario():
        app, session = Recorder(), _session()
        session.drained.set()  # left over from the previous response
        play = asyncio.ensure_future(server.VoiceLabsApp._play(app, session, bytes(8192)))
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

        play = asyncio.ensure_future(server.VoiceLabsApp._play_stream(app, session, sentences()))
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
        play = asyncio.ensure_future(server.VoiceLabsApp._play(app, session, pcm, paced=True))
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
