"""A stand-in for an ESPHome voice_labs device, replaying recorded calls.

It listens like the device does, says HELLO when the app connects, accepts the
wake word models it is pushed, and then places calls: each WAV is a call as the
app saves them (``call-*.wav``: 1.5 s of pre-roll ending at the detection,
then the command), streamed as the AGC capture (and, unless ``--no-aec``, as
the AEC capture too). Playback is acknowledged as drained at once, and the next
call starts after the app hangs up.

    .venv/bin/python -m tests.fake_satellite --port 6056 calls/call-15788.wav
    .venv/bin/python -m voice_labs --host 127.0.0.1 --port 6056 ...
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from voice_labs.protocol import BYTE_RATE, HEADER, SAMPLE_RATE, Message, encode, encode_json

_LOGGER = logging.getLogger("fake_satellite")

PREROLL_SECONDS = 1.5
CHUNK = 1024


def read_call(path: Path) -> bytes:
    with wave.open(str(path), "rb") as wav:
        if wav.getframerate() != 16000 or wav.getnchannels() != 1 or wav.getsampwidth() != 2:
            raise ValueError(f"{path}: expected 16 kHz mono 16-bit")
        return wav.readframes(wav.getnframes())


@dataclass
class FakeSatellite:
    name: str = "fake-vpe"
    mac: str = "aa:bb:cc:dd:ee:ff"
    speed: float = 4.0
    """How much faster than real time audio is sent."""
    aec: bool = True
    events: list[dict[str, Any]] = field(default_factory=list)
    hangups: list[str] = field(default_factory=list)
    played: int = 0
    """Bytes of AUDIO_OUT received."""
    ends: int = 0
    """AUDIO_OUT_ENDs received: playback streams ended."""
    models: list[str] = field(default_factory=list)
    """Model ids loaded, in order; MODEL_CLEAR empties it."""
    clears: int = 0
    model_slots: int | None = None
    """Said in HELLO, as newer firmware does."""
    model_headers: dict[str, dict] = field(default_factory=dict)
    monitor: bool = True
    """Says in HELLO that it can stream outside calls, as newer firmware does."""
    monitoring: list[bool] = field(default_factory=list)
    """Every MONITOR the app sent: streaming, or not."""
    intercom: list[bool] = field(default_factory=list)
    """And whether each was for the intercom."""
    heard: bytearray = field(default_factory=bytearray)
    """Every AUDIO_OUT received, in order."""
    monitor_aec: bool = True
    """Says in HELLO that it streams the second capture too while monitored."""
    room_audio: bytes | None = None
    """What its microphones hear between calls (looped), streamed on both
    captures while monitored; else a quiet hum, on the first."""
    hello_extra: dict = field(default_factory=dict)
    """More to say in HELLO (``intercom``, ``listen``)."""
    listens: list[bool] = field(default_factory=list)
    """Every HANGUP's ``listen``: whether the app wanted an answer."""

    def __post_init__(self) -> None:
        self.ready = asyncio.Event()
        self.audio_out = asyncio.Event()
        """Set by AUDIO_OUT: the app is playing something."""
        self._writer: asyncio.StreamWriter | None = None
        self._hangup = asyncio.Event()
        self._models = 0
        self._streaming: asyncio.Task | None = None
        self._in_call = False

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._writer = writer
        hello = {"name": self.name, "mac": self.mac}
        if self.model_slots is not None:
            hello["model_slots"] = self.model_slots
        if self.monitor:
            hello["monitor"] = True
            if self.monitor_aec:
                hello["monitor_aec"] = True
        hello.update(self.hello_extra)
        writer.write(encode_json(Message.HELLO, hello))
        await writer.drain()
        try:
            while True:
                header = await reader.readexactly(HEADER.size)
                msg_type, length = HEADER.unpack(header)
                payload = await reader.readexactly(length) if length else b""
                await self._on(msg_type, payload)
        except (asyncio.IncompleteReadError, ConnectionError):
            _LOGGER.info("App disconnected")
        finally:
            self._writer = None
            if self._streaming is not None:
                self._streaming.cancel()

    async def _on(self, msg_type: int, payload: bytes) -> None:
        assert self._writer is not None
        if msg_type == Message.MODEL_START:
            header = json.loads(payload)
            self.models.append(header.get("id"))
            self.model_headers[header.get("id")] = header
        elif msg_type == Message.MODEL_CLEAR:
            self.models.clear()
            self.clears += 1
        if msg_type == Message.MODEL_END:
            info = json.loads(payload)
            self._models += 1
            self._writer.write(
                encode_json(Message.MODEL_RESULT, {"ok": True, "id": info.get("id"), "models": self._models})
            )
            await self._writer.drain()
            if self._models >= 1:
                self.ready.set()
        elif msg_type == Message.AUDIO_OUT:
            self.played += len(payload)
            self.heard += payload
            self.audio_out.set()
        elif msg_type == Message.AUDIO_OUT_END:
            self.ends += 1
            self._writer.write(encode(Message.PLAYBACK_DRAINED))
            await self._writer.drain()
        elif msg_type == Message.EVENT:
            event = json.loads(payload)
            self.events.append(event)
            _LOGGER.info("event %s %s", event.get("type"), event.get("data"))
        elif msg_type == Message.MONITOR:
            told = json.loads(payload)
            active = bool(told.get("active"))
            self.monitoring.append(active)
            self.intercom.append(bool(told.get("intercom")))
            if active and self._streaming is None:
                self._streaming = asyncio.ensure_future(self._stream())
            elif not active and self._streaming is not None:
                self._streaming.cancel()
                self._streaming = None
        elif msg_type == Message.HANGUP:
            told = json.loads(payload) if payload else {}
            reason = told.get("reason", "")
            self.listens.append(bool(told.get("listen")))
            self.hangups.append(reason)
            _LOGGER.info("hang up: %s", reason)
            self._hangup.set()

    async def _stream(self) -> None:
        """The microphone between calls, while monitored: a quiet hum, and
        about ten times a second what the models make of it -- the first
        report with a wake word heard (and, monitored, not answered). A call
        streams its own audio instead, as the device does."""
        tone = (np.sin(np.arange(CHUNK // 2) * 2 * np.pi * 440 / SAMPLE_RATE) * 3000).astype("<i2").tobytes()
        sent = offset = 0
        while self._writer is not None:
            if not self._in_call:
                if self.room_audio:
                    chunk = (self.room_audio + self.room_audio)[offset % len(self.room_audio) :][:CHUNK]
                    offset += CHUNK
                    self._writer.write(encode(Message.AUDIO_MONITOR, chunk))
                    if self.monitor_aec:
                        self._writer.write(encode(Message.AUDIO_MONITOR_AEC, chunk))
                else:
                    self._writer.write(encode(Message.AUDIO_MONITOR, tone))
                if sent % 3 == 0:
                    report = {
                        "models": [
                            {"id": m, **({"vad": True} if m == "vad" else {}), "cutoff": 217, "peak": 40 + sent % 50, "window": 30}
                            for m in self.models
                        ],
                        "detected": [m for m in self.models if m != "vad"][:1] if sent == 0 else [],
                    }
                    self._writer.write(encode_json(Message.WAKE_REPORT, report))
                sent += 1
                await self._writer.drain()
            await asyncio.sleep(CHUNK / BYTE_RATE / self.speed)

    async def device_event(self, kind: str) -> None:
        """Something happened here (the media player's announcement started or
        ended): tell the app."""
        assert self._writer is not None
        self._writer.write(encode_json(Message.DEVICE_EVENT, {"type": kind}))
        await self._writer.drain()

    async def cancel(self) -> None:
        """The button during a call: the device ends it, cutting off what it
        was playing."""
        assert self._writer is not None
        self._writer.write(encode_json(Message.CALL_END, {"reason": "cancelled"}))
        await self._writer.drain()
        self.hangups.append("cancelled")
        self._hangup.set()

    async def answer(self, pcm: bytes, timeout: float = 30.0) -> str:
        """The call firmware starts when the app hung up wanting an answer
        (HANGUP ``listen``): no wake word, no pre-roll -- ``pcm`` is all
        answer. Returns why the app hung up."""
        await self.ready.wait()
        assert self._writer is not None
        self._hangup.clear()
        info: dict[str, Any] = {"preroll_bytes": 0, "wake_word": "follow_up"}
        if self.aec:
            info.update(aec=True, aec_preroll_bytes=0)
        self._writer.write(encode_json(Message.CALL_START, info))
        self._in_call = True
        try:
            return await self._stream_call(pcm, timeout)
        finally:
            self._in_call = False

    async def detect(self, wake_word: str, timeout: float = 10.0) -> str:
        """A wake word model fired with nothing to say after it (\"stop\")."""
        return await self.call(bytes(CHUNK * 4), timeout=timeout, wake_word=wake_word)

    async def call(
        self, pcm: bytes, timeout: float = 30.0, wake_word: str = "okay_nabu", detection: dict | None = None,
        aec_pcm: bytes | None = None,
    ) -> str:
        """Place one call; returns why the app hung up. ``detection``: what
        the wake word came in with, as firmware that says so sends it
        (``probability``, ``max_probability``, ``cutoff``, 0-255).
        ``aec_pcm``: the second capture, when it isn't the same as the call's."""
        await self.ready.wait()
        assert self._writer is not None
        self._hangup.clear()
        preroll = int(PREROLL_SECONDS * BYTE_RATE)
        info = {"preroll_bytes": preroll, "wake_word": wake_word, **(detection or {})}
        if self.aec:
            info.update(aec=True, aec_preroll_bytes=preroll)
        self._writer.write(encode_json(Message.CALL_START, info))
        self._in_call = True
        try:
            return await self._stream_call(pcm, timeout, aec_pcm)
        finally:
            self._in_call = False

    async def _stream_call(self, pcm: bytes, timeout: float, aec_pcm: bytes | None = None) -> str:
        assert self._writer is not None
        silence = bytes(CHUNK)
        offset = 0
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while not self._hangup.is_set() and loop.time() < deadline:
            chunk = pcm[offset : offset + CHUNK] if offset < len(pcm) else silence
            offset += CHUNK
            self._writer.write(encode(Message.AUDIO_IN, chunk))
            if self.aec:
                second = chunk if aec_pcm is None else (aec_pcm[offset - CHUNK : offset] or silence)
                self._writer.write(encode(Message.AUDIO_IN_AEC, second))
            await self._writer.drain()
            await asyncio.sleep(CHUNK / BYTE_RATE / self.speed)
        if not self._hangup.is_set():
            self._writer.write(encode_json(Message.CALL_END, {"reason": "timeout"}))
            await self._writer.drain()
            return "timeout"
        return self.hangups[-1]


async def serve(satellite: FakeSatellite, host: str = "127.0.0.1", port: int = 0):
    return await asyncio.start_server(satellite.handle, host, port)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("calls", nargs="+", type=Path, help="call-*.wav recordings to replay")
    parser.add_argument("--port", type=int, default=6056)
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--pause", type=float, default=3.0, help="seconds between calls")
    parser.add_argument("--loop", action="store_true", help="replay the calls forever")
    parser.add_argument("--no-aec", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")

    async def run() -> None:
        satellite = FakeSatellite(speed=args.speed, aec=not args.no_aec)
        server = await serve(satellite, port=args.port)
        _LOGGER.info("Fake satellite on port %d", args.port)
        async with server:
            while True:
                for path in args.calls:
                    await satellite.ready.wait()
                    await asyncio.sleep(args.pause)
                    _LOGGER.info("Calling with %s", path)
                    await satellite.call(read_call(path))
                if not args.loop:
                    break
            await asyncio.Future()

    asyncio.run(run())


if __name__ == "__main__":
    main()
