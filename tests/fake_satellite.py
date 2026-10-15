"""A stand-in for an ESPHome voice_call device, replaying recorded calls.

It listens like the device does, says HELLO when the app connects, accepts the
wake word models it is pushed, and then places calls: each WAV is a call as the
app saves them (``call-*.wav``: 1.5 s of pre-roll ending at the detection,
then the command), streamed as the AGC capture (and, unless ``--no-aec``, as
the AEC capture too). Playback is acknowledged as drained at once, and the next
call starts after the app hangs up.

    .venv/bin/python -m tests.fake_satellite --port 6056 calls/call-15788.wav
    .venv/bin/python -m voice_call --host 127.0.0.1 --port 6056 ...
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

from voice_call.protocol import BYTE_RATE, HEADER, Message, encode, encode_json

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
    models: list[str] = field(default_factory=list)
    """Model ids loaded, in order; MODEL_CLEAR empties it."""
    clears: int = 0

    def __post_init__(self) -> None:
        self.ready = asyncio.Event()
        self._writer: asyncio.StreamWriter | None = None
        self._hangup = asyncio.Event()
        self._models = 0

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._writer = writer
        writer.write(encode_json(Message.HELLO, {"name": self.name, "mac": self.mac}))
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

    async def _on(self, msg_type: int, payload: bytes) -> None:
        assert self._writer is not None
        if msg_type == Message.MODEL_START:
            self.models.append(json.loads(payload).get("id"))
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
        elif msg_type == Message.AUDIO_OUT_END:
            self._writer.write(encode(Message.PLAYBACK_DRAINED))
            await self._writer.drain()
        elif msg_type == Message.EVENT:
            event = json.loads(payload)
            self.events.append(event)
            _LOGGER.info("event %s %s", event.get("type"), event.get("data"))
        elif msg_type == Message.HANGUP:
            reason = json.loads(payload).get("reason") if payload else ""
            self.hangups.append(reason)
            _LOGGER.info("hang up: %s", reason)
            self._hangup.set()

    async def detect(self, wake_word: str, timeout: float = 10.0) -> str:
        """A wake word model fired with nothing to say after it (\"stop\")."""
        return await self.call(bytes(CHUNK * 4), timeout=timeout, wake_word=wake_word)

    async def call(self, pcm: bytes, timeout: float = 30.0, wake_word: str = "okay_nabu") -> str:
        """Place one call; returns why the app hung up."""
        await self.ready.wait()
        assert self._writer is not None
        self._hangup.clear()
        preroll = int(PREROLL_SECONDS * BYTE_RATE)
        info = {"preroll_bytes": preroll, "wake_word": wake_word}
        if self.aec:
            info.update(aec=True, aec_preroll_bytes=preroll)
        self._writer.write(encode_json(Message.CALL_START, info))
        silence = bytes(CHUNK)
        offset = 0
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while not self._hangup.is_set() and loop.time() < deadline:
            chunk = pcm[offset : offset + CHUNK] if offset < len(pcm) else silence
            offset += CHUNK
            self._writer.write(encode(Message.AUDIO_IN, chunk))
            if self.aec:
                self._writer.write(encode(Message.AUDIO_IN_AEC, chunk))
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
