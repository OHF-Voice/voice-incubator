#!/usr/bin/env python3
"""Throwaway stand-in for the Home Assistant component plus the app.

Discovers the device over mDNS (or takes --host), opens the control connection,
records a call's microphone audio, waits for the end of the voice command with
pysilero-vad, then echoes the whole recording back for playback.

    pip install pysilero-vad zeroconf
    python3 voice_labs_test.py --save-dir /tmp/calls
"""

import argparse
import asyncio
import contextlib
import json
import logging
import struct
import time
import wave
from pathlib import Path

_LOGGER = logging.getLogger("voice_labs_test")

SERVICE_TYPE = "_voice-labs._tcp.local."

SAMPLE_RATE = 16000
BYTES_PER_SAMPLE = 2
BYTE_RATE = SAMPLE_RATE * BYTES_PER_SAMPLE

HEADER = struct.Struct(">BH")
MAX_PAYLOAD = 4096

# Device to us
HELLO = 0x01
CALL_START = 0x02
AUDIO_IN = 0x03
CALL_END = 0x04
STATUS = 0x05
PLAYBACK_DRAINED = 0x06

# Us to device
AUDIO_OUT = 0x10
AUDIO_OUT_END = 0x11
EVENT = 0x12
HANGUP = 0x13

NAMES = {
    HELLO: "hello",
    CALL_START: "call_start",
    AUDIO_IN: "audio_in",
    CALL_END: "call_end",
    STATUS: "status",
    PLAYBACK_DRAINED: "playback_drained",
}

# pysilero-vad consumes exactly 512 samples (32 ms) at 16 kHz.
VAD_CHUNK_BYTES = 512 * BYTES_PER_SAMPLE
VAD_CHUNK_SECONDS = VAD_CHUNK_BYTES / BYTE_RATE


async def discover(timeout: float) -> tuple[str, int]:
    """Find the device the way the Home Assistant component will."""
    from zeroconf import ServiceStateChange
    from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

    found: asyncio.Future[tuple[str, int]] = asyncio.get_running_loop().create_future()

    def on_change(zeroconf, service_type, name, state_change) -> None:
        if state_change is not ServiceStateChange.Added or found.done():
            return

        async def resolve() -> None:
            info = AsyncServiceInfo(service_type, name)
            await info.async_request(zeroconf, 3000)
            addresses = info.parsed_addresses()
            if addresses and not found.done():
                props = {
                    k.decode(): v.decode()
                    for k, v in (info.properties or {}).items()
                    if k and v
                }
                _LOGGER.info("Discovered %s at %s:%d %s", name, addresses[0], info.port, props)
                found.set_result((addresses[0], info.port))

        asyncio.ensure_future(resolve())

    async with AsyncZeroconf() as azc:
        browser = AsyncServiceBrowser(azc.zeroconf, SERVICE_TYPE, handlers=[on_change])
        try:
            return await asyncio.wait_for(found, timeout)
        finally:
            await browser.async_cancel()


class VadEndpointer:
    """Marks the end of the voice command after enough trailing silence."""

    def __init__(self, threshold: float, silence_s: float, min_speech_s: float) -> None:
        from pysilero_vad import SileroVoiceActivityDetector

        self._vad = SileroVoiceActivityDetector()
        self._threshold = threshold
        self._silence_s = silence_s
        self._min_speech_s = min_speech_s
        self._leftover = b""
        self._speech_s = 0.0
        self._silence_run_s = 0.0
        self.speech_started = False

    def process(self, audio: bytes) -> bool:
        """Feed audio; returns True once the command has ended."""
        self._leftover += audio

        while len(self._leftover) >= VAD_CHUNK_BYTES:
            chunk = self._leftover[:VAD_CHUNK_BYTES]
            self._leftover = self._leftover[VAD_CHUNK_BYTES:]

            if self._vad.process_chunk(chunk) >= self._threshold:
                self._speech_s += VAD_CHUNK_SECONDS
                self._silence_run_s = 0.0
                if not self.speech_started and self._speech_s >= self._min_speech_s:
                    self.speech_started = True
                    _LOGGER.info("Speech started")
            elif self.speech_started:
                self._silence_run_s += VAD_CHUNK_SECONDS
                if self._silence_run_s >= self._silence_s:
                    _LOGGER.info(
                        "Speech ended (%.2fs speech, %.2fs trailing silence)",
                        self._speech_s,
                        self._silence_run_s,
                    )
                    return True

        return False


class Call:
    """One wake-word-to-hangup session."""

    def __init__(self, args: argparse.Namespace, info: dict) -> None:
        self.args = args
        self.info = info
        self.audio = bytearray()
        self.vad_fed = 0
        self.preroll_bytes = int(info.get("preroll_bytes", 0))
        self.started = time.monotonic()
        self.ended = False
        self.vad = VadEndpointer(args.vad_threshold, args.silence, args.min_speech)

    @property
    def preroll_ms(self) -> int:
        return self.preroll_bytes * 1000 // BYTE_RATE

    def save(self, save_dir: Path) -> Path:
        save_dir.mkdir(parents=True, exist_ok=True)
        path = save_dir / f"call-{int(self.started)}.wav"
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(BYTES_PER_SAMPLE)
            wav.setframerate(SAMPLE_RATE)
            wav.writeframes(self.audio)
        return path


class Connection:
    def __init__(self, reader, writer, args: argparse.Namespace) -> None:
        self.reader = reader
        self.writer = writer
        self.args = args
        self.call: Call | None = None
        self.drained = asyncio.Event()
        self._tasks: set[asyncio.Task] = set()

    def spawn(self, coro) -> None:
        """Run alongside the read loop; echo() waits on frames the loop delivers."""
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def send(self, msg_type: int, payload: bytes = b"") -> None:
        self.writer.write(HEADER.pack(msg_type, len(payload)) + payload)
        # Propagates the device's TCP backpressure to whatever produced the audio.
        await self.writer.drain()

    async def send_json(self, msg_type: int, obj: dict) -> None:
        await self.send(msg_type, json.dumps(obj).encode())

    async def send_event(self, event_type: str, data: dict | None = None) -> None:
        await self.send_json(EVENT, {"type": event_type, "data": data or {}})

    async def run(self) -> None:
        while True:
            header = await self.reader.readexactly(HEADER.size)
            msg_type, length = HEADER.unpack(header)
            payload = await self.reader.readexactly(length) if length else b""
            await self.handle(msg_type, payload)

    async def handle(self, msg_type: int, payload: bytes) -> None:
        if msg_type == AUDIO_IN:
            await self.on_audio(payload)
            return

        name = NAMES.get(msg_type, f"0x{msg_type:02X}")
        info = json.loads(payload) if payload else {}

        if msg_type == STATUS:
            _LOGGER.debug("status: %s", info)
        elif msg_type == HELLO:
            _LOGGER.info("Device: %s", info)
        elif msg_type == CALL_START:
            self.call = Call(self.args, info)
            self.drained.clear()
            self.spawn(self.watchdog(self.call))
            _LOGGER.info(
                "Call started: wake_word=%r pre-roll=%dms",
                info.get("wake_word", ""),
                self.call.preroll_ms,
            )
            await self.send_event("listening", {"stage": "listening"})
        elif msg_type == CALL_END:
            _LOGGER.info("Device ended call: %s", info.get("reason"))
            self.call = None
        elif msg_type == PLAYBACK_DRAINED:
            _LOGGER.info("Playback drained")
            self.drained.set()
        else:
            _LOGGER.warning("Unhandled %s: %s", name, info)

    async def watchdog(self, call: "Call") -> None:
        """End the call even if the VAD never fires or audio stops arriving."""
        await asyncio.sleep(self.args.max_command)
        if self.call is call and not call.ended:
            _LOGGER.warning(
                "Command exceeded %.1fs (%.2fs audio received), ending anyway",
                self.args.max_command,
                len(call.audio) / BYTE_RATE,
            )
            call.ended = True
            self.spawn(self.echo(call))

    async def on_audio(self, chunk: bytes) -> None:
        call = self.call
        if call is None or call.ended:
            return

        call.audio += chunk

        # Everything up to preroll_bytes is pre-wake-word audio; the command
        # itself starts after it, so that is all the VAD should see.
        start = max(call.preroll_bytes, call.vad_fed)
        new_audio = bytes(call.audio[start:])
        if new_audio:
            call.vad_fed = len(call.audio)
            if call.vad.process(new_audio):
                call.ended = True
                self.spawn(self.echo(call))
                return


    async def echo(self, call: Call) -> None:
        """Send the whole recording back for playback, then hang up."""
        if self.args.save_dir:
            _LOGGER.info("Saved %s", call.save(Path(self.args.save_dir)))

        if self.args.play_file:
            with wave.open(self.args.play_file, "rb") as src:
                assert src.getframerate() == SAMPLE_RATE and src.getnchannels() == 1
                audio = src.readframes(src.getnframes())
        elif self.args.echo_preroll:
            audio = bytes(call.audio)
        else:
            audio = bytes(call.audio[call.preroll_bytes :])
        _LOGGER.info(
            "Received %.2fs total (%.2fs pre-roll); echoing %.2fs",
            len(call.audio) / BYTE_RATE,
            call.preroll_bytes / BYTE_RATE,
            len(audio) / BYTE_RATE,
        )

        await self.send_event("playing", {"stage": "playing", "duration_ms": len(audio) * 1000 // BYTE_RATE})

        for offset in range(0, len(audio), MAX_PAYLOAD):
            await self.send(AUDIO_OUT, audio[offset : offset + MAX_PAYLOAD])
        await self.send(AUDIO_OUT_END)

        try:
            await asyncio.wait_for(self.drained.wait(), timeout=self.args.drain_timeout)
        except TimeoutError:
            _LOGGER.warning("Timed out waiting for playback to drain")

        await self.send_json(HANGUP, {"reason": "done"})
        self.call = None


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", help="device address (skips mDNS discovery)")
    parser.add_argument("--port", type=int, default=6055)
    parser.add_argument("--discover-timeout", type=float, default=10.0)
    parser.add_argument("--vad-threshold", type=float, default=0.5)
    parser.add_argument("--silence", type=float, default=1.0, help="trailing silence to end the command")
    parser.add_argument("--min-speech", type=float, default=0.2)
    parser.add_argument("--max-command", type=float, default=15.0)
    parser.add_argument("--drain-timeout", type=float, default=30.0)
    parser.add_argument("--echo-preroll", action="store_true", help="echo the wake word too")
    parser.add_argument(
        "--play-file",
        help="play this 16kHz mono wav instead of echoing the mic (playback-chain test)",
    )
    parser.add_argument("--save-dir", help="write each call to a wav file here")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)

    if args.host:
        host, port = args.host, args.port
    else:
        _LOGGER.info("Browsing for %s", SERVICE_TYPE)
        host, port = await discover(args.discover_timeout)

    while True:
        try:
            _LOGGER.info("Connecting to %s:%d", host, port)
            reader, writer = await asyncio.open_connection(host, port)
        except OSError as err:
            _LOGGER.warning("Connect failed (%s), retrying in 5s", err)
            await asyncio.sleep(5)
            continue

        try:
            await Connection(reader, writer, args).run()
        except (asyncio.IncompleteReadError, ConnectionError) as err:
            _LOGGER.warning("Connection lost: %s", err)
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

        await asyncio.sleep(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
