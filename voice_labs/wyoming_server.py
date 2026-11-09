"""Wyoming endpoints: the app's speech, voices and commands for Home
Assistant's own voice pipelines (Assist) -- satellites that aren't the
app's, and what's typed.

One server (``port``, 10800 as an app), a Wyoming Protocol integration in
Home Assistant, which the Supervisor announces to it:

* **Text-to-speech**: Piper, every voice of its catalog (piper_voices), each
  downloaded the first time it is asked for, and removed once it has gone
  unused for 30 days (piper_voices.Uses). Text that comes in pieces (an
  LLM's answer) is said a sentence at a time, as each is whole.
* **A conversation agent** (``handle``): the app's commands, skills,
  fallback and conversation agent, as on a satellite of the app's in the
  asking satellite's area (VoiceLabsApp.handle_text). An LLM's answer is
  streamed as it comes. A question asked back -- a command's confirm, the
  agent's -- keeps the conversation going (``continue_conversation``).
* **Speech-to-text** that writes what it heard as the command it heard, with
  the home's names spelled right (voice_labs/corrected.py) -- its words the
  speech-to-text the app uses (the Settings page's: the TDT head, or an STT
  entity it stands in for), as on its own satellites: the entity asked when
  its words are needed, or hearing the audio as it comes ("Always, as you
  speak": VoiceLabsApp.hear, transcribe).

What speech-to-text gave is kept a little while with its audio
(``HeardCache``): when the same words come to the conversation agent, from
the same satellite, it is the audio that is recognized -- as the app hears
it, a doubtful match heard again, free text spelled by the TDT head -- not
the words.

As an app, only Home Assistant may connect: this machine, and the
Supervisor's network (``allow``). Standalone, the address it listens on
decides.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import ipaddress
import logging
import re
import socket
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from functools import partial
from typing import Any

import aiohttp
import numpy as np
from wyoming.asr import Transcribe, Transcript
from wyoming.audio import AudioChunk, AudioChunkConverter, AudioStart, AudioStop
from wyoming.error import Error
from wyoming.event import Event
from wyoming.handle import Handled, HandledChunk, HandledStart, HandledStop, NotHandled
from wyoming.info import (
    AsrModel,
    AsrProgram,
    Attribution,
    Describe,
    HandleModel,
    HandleProgram,
    Info,
    TtsProgram,
    TtsVoice,
    TtsVoiceSpeaker,
)
from wyoming.server import AsyncEventHandler, AsyncTcpServer
from wyoming.tts import Synthesize, SynthesizeChunk, SynthesizeStart, SynthesizeStop, SynthesizeStopped

from .piper_voices import catalog as piper_catalog
from .tts import sentences_of, stream_sentences

_LOGGER = logging.getLogger(__name__)

PORT = 10800
"""The app's server, as an app."""

SAMPLE_RATE = 16000
SAMPLES_PER_CHUNK = 1024
"""Speech is sent this many samples at a time."""

HOME_ASSISTANT = ("host", "127.0.0.0/8", "::1/128", "172.30.32.0/23")
"""Who may connect, as an app: this machine (``host``: from one of its own
addresses, where Home Assistant runs -- whichever it connects from: to the
Supervisor's network's address, it may come from the host's on the LAN) and
the Supervisor's network."""

SUPERVISOR = "http://supervisor"

KEEP_HEARD = 30.0
"""How long what speech-to-text heard is kept for the conversation agent."""
MOST_HEARD = 4

CONTEXT_KEYS = ("conversation_id", "device_id", "satellite_id")
"""What Home Assistant says of a request (Wyoming's context): its
conversation, and the satellite asking -- its device and its entity."""

APP = Attribution(name="Open Home Foundation", url="https://github.com/OHF-Voice/voice-labs")
PARAKEET = Attribution(name="NVIDIA", url="https://huggingface.co/nvidia/parakeet-tdt_ctc-110m")
PIPER = Attribution(name="Piper", url="https://github.com/OHF-Voice/piper1-gpl")


@dataclass
class Spoken:
    """A voice to say text with, as it was asked for (VoiceLabsApp.wyoming_voice)."""

    key: str
    """As Piper names it."""
    model: Any
    """Piper's voice (PiperVoice)."""
    settings: dict[str, Any]
    """How it speaks (piper_voices.synthesis's)."""
    rate: int
    """Its samples per second."""


@dataclass
class Turn:
    """What came of text said to the app (VoiceLabsApp.handle_text)."""

    text: str
    """What to say: an LLM's answer, all of it once it has come."""
    handled: bool = True
    """Something was understood: done, answered, asked or declined."""
    asks: bool = False
    """It asks something back: the satellite listens for the answer."""


class Remembered:
    """The TDT head, once per stretch of audio: what it made of it is kept,
    for the engine and the corrected transcript alike."""

    source = "TDT"

    def __init__(self, transcriber: Any) -> None:
        self.transcriber = transcriber
        self._heard: dict[bytes, tuple[str, list]] = {}

    def transcribe(self, audio: np.ndarray) -> str:
        return self.transcribe_words(audio)[0]

    def transcribe_words(self, audio: np.ndarray) -> tuple[str, list]:
        key = hashlib.blake2b(np.ascontiguousarray(audio).tobytes(), digest_size=16).digest()
        if key not in self._heard:
            self._heard[key] = self.transcriber.transcribe_words(audio)
        return self._heard[key]


@dataclass
class Hearing:
    """Speech coming in (VoiceLabsApp.hear): its audio so far, and the STT
    entity hearing it as it is said, when that always runs."""

    context: dict[str, Any]
    satellite: Any
    """Who is speaking (VoiceLabsApp.assist_satellite)."""
    tdt: Remembered | None
    stream: Any = None
    """The STT entity hearing it (voices.StreamingTranscriber), or None."""
    pcm: bytearray = field(default_factory=bytearray)
    kaldi: Any = None
    """Kaldi decoding it as it comes (kaldi_mode.Stream), in Kaldi mode."""

    def feed(self, pcm: bytes) -> None:
        self.pcm += pcm
        if self.stream is not None:
            self.stream.feed(pcm)
        if self.kaldi is not None:
            self.kaldi.feed(pcm)

    def cancel(self) -> None:
        """It won't be transcribed (the client left)."""
        if self.stream is not None and not self.stream.closed:
            self.stream.cancel()
        if self.kaldi is not None:
            self.kaldi.cancel()


@dataclass
class Heard:
    """What speech-to-text heard: the words it gave, and what they came from."""

    text: str
    audio: np.ndarray
    evidence: tuple[np.ndarray, float]
    """The CTC head's (Engine.model.log_probs)."""
    transcriber: Any
    """What the TDT head heard of it (Remembered), or None."""
    satellite_id: str | None
    record_id: str
    at: float = field(default_factory=time.monotonic)


def _words(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s']", " ", text.lower()).split())


class HeardCache:
    """What speech-to-text gave a moment ago (KEEP_HEARD), by its words: the
    newest of each, taken once."""

    def __init__(self, keep_seconds: float = KEEP_HEARD, most: int = MOST_HEARD) -> None:
        self.keep_seconds = keep_seconds
        self.most = most
        self._heard: list[Heard] = []

    def keep(self, heard: Heard) -> None:
        self._drop_stale()
        self._heard.append(heard)
        del self._heard[: -self.most]

    def take(self, text: str, satellite_id: str | None) -> Heard | None:
        """What gave ``text``, when it was heard from the same satellite (or
        either can't say which)."""
        self._drop_stale()
        wanted = _words(text)
        for heard in reversed(self._heard):
            if _words(heard.text) != wanted or not wanted:
                continue
            if heard.satellite_id and satellite_id and heard.satellite_id != satellite_id:
                continue
            self._heard.remove(heard)
            return heard
        return None

    def _drop_stale(self) -> None:
        now = time.monotonic()
        self._heard = [h for h in self._heard if now - h.at <= self.keep_seconds]


class Endpoints:
    """The two servers, each connection a ``Handler``."""

    def __init__(self, app: Any, host: str, port: int, allow: tuple[str, ...] = ()) -> None:
        self.app = app
        self.host = host
        self.port = port
        self.allow = allow
        self._networks = [ipaddress.ip_network(a) for a in allow if a != "host"]
        self._server: AsyncTcpServer | None = None

    async def start(self) -> None:
        server = AsyncTcpServer(self.host, self.port)
        await server.start(partial(Handler, self))
        self._server = server
        _LOGGER.info("Wyoming: on %s:%d", self.host, self.port)

    async def stop(self) -> None:
        if self._server is not None:
            await self._server.stop()
            self._server = None

    def allows(self, peer: Any, local: Any) -> bool:
        """May the client at ``peer`` (on our ``local`` address) connect?"""
        if not self.allow:
            return True
        try:
            address = ipaddress.ip_address(str(peer[0]).split("%", 1)[0])
        except (TypeError, IndexError, ValueError):
            return False
        if address.version == 6 and address.ipv4_mapped is not None:
            address = address.ipv4_mapped
        if "host" in self.allow and (
            (local and str(address) == str(local[0]).removeprefix("::ffff:")) or own_address(address)
        ):
            return True
        return any(address in network for network in self._networks)

    def info(self) -> Info:
        language = self.app.language.code
        parakeet = AsrModel(
            name="parakeet", description="Parakeet", attribution=PARAKEET, installed=True, version=None,
            languages=[language],
        )
        voices = [
            TtsVoice(
                name=voice.key, description=f"{voice.to_json()['name']} ({voice.quality})", attribution=PIPER,
                installed=True, version=None, languages=[voice.language],
                speakers=[TtsVoiceSpeaker(name=s) for s in voice.speakers] or None,
            )
            for voice in sorted(piper_catalog().values(), key=lambda v: v.key)
        ]
        return Info(
            asr=[AsrProgram(
                name="Voice Labs", description="What Voice Labs heard, with your names", attribution=APP,
                installed=True, version=None, models=[parakeet],
            )],
            tts=[TtsProgram(
                name="Voice Labs", description="Piper", attribution=PIPER, installed=True, version=None,
                voices=voices, supports_synthesize_streaming=True,
            )],
            handle=[HandleProgram(
                name="Voice Labs", description="Your commands", attribution=APP, installed=True, version=None,
                models=[HandleModel(
                    name="voice_labs", description="Voice Labs", attribution=APP, installed=True, version=None,
                    languages=[language],
                )],
                supports_handled_streaming=True, supports_home_control=True,
            )],
        )


def own_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Is it one of this machine's addresses? Only those can be bound to."""
    family = socket.AF_INET if address.version == 4 else socket.AF_INET6
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((str(address), 0))
        except OSError:
            return False
    return True


async def announce(token: str, port: int, url: str = SUPERVISOR) -> str:
    """Tell Home Assistant where the app's server is, through the Supervisor
    (it offers to add it): at the app's address on the Supervisor's network.
    The address told."""
    async with aiohttp.ClientSession(headers={"Authorization": f"Bearer {token}"}) as session:
        async with session.get(f"{url}/addons/self/info") as response:
            response.raise_for_status()
            host = ((await response.json()).get("data") or {}).get("ip_address") or "127.0.0.1"
        uri = f"tcp://{host}:{port}"
        async with session.post(f"{url}/discovery", json={"service": "wyoming", "config": {"uri": uri}}) as response:
            response.raise_for_status()
    return uri


class Handler(AsyncEventHandler):
    """One connection. Home Assistant asks for one thing on each: a
    transcript, a reply, or speech."""

    def __init__(self, endpoints: Endpoints, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        super().__init__(reader, writer)
        self.endpoints = endpoints
        self.app = endpoints.app
        self.peer = writer.get_extra_info("peername")
        self.allowed = endpoints.allows(self.peer, writer.get_extra_info("sockname"))
        self._transcribe: Transcribe | None = None
        self._hearing: Hearing | None = None
        self._converter = AudioChunkConverter(rate=SAMPLE_RATE, width=2, channels=1)
        self._speaking: Speaking | None = None

    async def handle_event(self, event: Event) -> bool:
        if not self.allowed:
            _LOGGER.warning("Wyoming: refused %s -- only Home Assistant may connect", self.peer)
            return False
        try:
            return await self._handle_event(event)
        except ConnectionError as err:
            _LOGGER.info("Wyoming: %s left (%s)", self.peer, err)
            return False
        except Exception as err:  # noqa: BLE001 - told, not swallowed
            _LOGGER.exception("Wyoming: %s failed", event.type)
            with contextlib.suppress(ConnectionError, OSError):
                await self.write_event(Error(text=str(err) or type(err).__name__, code=type(err).__name__).event())
            return False

    async def _handle_event(self, event: Event) -> bool:
        if Describe.is_type(event.type):
            await self.write_event(self.endpoints.info().event())
        elif Transcribe.is_type(event.type):
            self._transcribe = Transcribe.from_event(event)
        elif AudioStart.is_type(event.type):
            if self._hearing is not None:
                self._hearing.cancel()
            self._hearing = await self.app.hear(self._transcribe.context if self._transcribe is not None else None)
        elif AudioChunk.is_type(event.type):
            if self._hearing is not None:
                self._hearing.feed(self._converter.convert(AudioChunk.from_event(event)).audio)
        elif AudioStop.is_type(event.type):
            if self._hearing is not None:
                hearing, self._hearing = self._hearing, None
                text = await self.app.transcribe(hearing)
                await self.write_event(Transcript(text=text, language=self.app.language.code).event())
        elif Transcript.is_type(event.type):
            await self._reply(Transcript.from_event(event))
        elif Synthesize.is_type(event.type):
            # In a stream, only its text again, for servers that don't stream.
            if self._speaking is None:
                await self._say(Synthesize.from_event(event))
        elif SynthesizeStart.is_type(event.type):
            voice = SynthesizeStart.from_event(event).voice
            self._speaking = Speaking(self, voice.name if voice else None, voice.speaker if voice else None)
        elif SynthesizeChunk.is_type(event.type):
            if self._speaking is not None:
                self._speaking.put(SynthesizeChunk.from_event(event).text)
        elif SynthesizeStop.is_type(event.type):
            if self._speaking is not None:
                speaking, self._speaking = self._speaking, None
                await speaking.finish()
            await self.write_event(SynthesizeStopped().event())
        return True

    async def disconnect(self) -> None:
        if self._speaking is not None:
            self._speaking.cancel()
        if self._hearing is not None:
            self._hearing.cancel()

    async def _reply(self, transcript: Transcript) -> None:
        """The text said to the conversation agent, done; its answer, an
        LLM's as it comes."""
        streamed = False

        async def on_text(piece: str) -> None:
            nonlocal streamed
            if not streamed:
                await self.write_event(HandledStart().event())
                streamed = True
            await self.write_event(HandledChunk(text=piece).event())

        turn = await self.app.handle_text(transcript.text, transcript.context, on_text)
        if not turn.handled and not streamed:
            await self.write_event(NotHandled(text=turn.text).event())
            return
        await self.write_event(Handled(text=turn.text, continue_conversation=turn.asks or None).event())
        if streamed:
            await self.write_event(HandledStop().event())

    async def _say(self, synthesize: Synthesize) -> None:
        """Text, all of it at once: one stretch of audio."""
        voice = await self.app.wyoming_voice(
            synthesize.voice.name if synthesize.voice else None, synthesize.voice.speaker if synthesize.voice else None
        )
        await self.write_event(AudioStart(rate=voice.rate, width=2, channels=1).event())
        for sentence in sentences_of(synthesize.text):
            await self.send_audio(await self.app.speak(sentence, voice), voice.rate)
        await self.write_event(AudioStop().event())

    async def send_audio(self, audio: bytes, rate: int) -> None:
        step = SAMPLES_PER_CHUNK * 2
        for start in range(0, len(audio), step):
            await self.write_event(AudioChunk(rate=rate, width=2, channels=1, audio=audio[start : start + step]).event())


class Speaking:
    """Text that comes in pieces (synthesize-chunk), said a sentence at a
    time as each is whole."""

    def __init__(self, handler: Handler, voice: str | None, speaker: str | None) -> None:
        self.handler = handler
        self._pieces: asyncio.Queue[str | None] = asyncio.Queue()
        self._task = asyncio.ensure_future(self._run(voice, speaker))

    def put(self, text: str) -> None:
        self._pieces.put_nowait(text)

    async def finish(self) -> None:
        """All of it has come: once it has all been said."""
        self._pieces.put_nowait(None)
        await self._task

    def cancel(self) -> None:
        self._task.cancel()

    async def _text(self) -> AsyncIterator[str]:
        while (piece := await self._pieces.get()) is not None:
            yield piece

    async def _run(self, voice_name: str | None, speaker: str | None) -> None:
        app = self.handler.app
        voice = await app.wyoming_voice(voice_name, speaker)
        async for sentence in stream_sentences(self._text()):
            # Each sentence its own stretch of audio, as it is made.
            audio = await app.speak(sentence, voice)
            await self.handler.write_event(AudioStart(rate=voice.rate, width=2, channels=1).event())
            await self.handler.send_audio(audio, voice.rate)
            await self.handler.write_event(AudioStop().event())

