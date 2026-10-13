"""Connects to an ESPHome voice_call device and runs one call at a time."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import socket
import time
import wave
from dataclasses import dataclass, field, replace
from typing import Any
from pathlib import Path

from .endpoint import Endpointer
from .fst_hybrid import FstHybrid
from .discover import Device, Discovery
from .hass import HomeAssistant
from .mixednet import WakeModel, load as load_wake_model
from .protocol import (
    BYTE_RATE,
    BYTES_PER_SAMPLE,
    HEADER,
    MAX_PAYLOAD,
    SAMPLE_RATE,
    Message,
    encode,
    encode_json,
    ms_to_bytes,
)
from .recognize import CommandRecognizer
from .tdt import TdtTranscriber
from .tts import Speech
from .wake import WakeVerifier
from .wake_ratio import WakeRatio
from .wildcards import WildcardMatcher

_LOGGER = logging.getLogger(__name__)

# A connection that survives this long is treated as healthy, which resets the
# reconnect backoff.
STABLE_CONNECTION_SECONDS = 30.0

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _device_id(hello: dict) -> str:
    """A filesystem-safe identity for the device that sent this HELLO.

    ESPHome names are only unique if the YAML sets name_add_mac_suffix, so the
    tail of the MAC is appended to keep identically-named devices apart.
    """
    name = _UNSAFE.sub("-", str(hello.get("name") or "device")).strip("-")
    mac = _UNSAFE.sub("", str(hello.get("mac") or ""))
    return f"{name}-{mac[-6:]}" if mac else name


@dataclass
class Options:
    host: str | None = None
    """Pin to one device. None serves every satellite mDNS finds."""
    port: int = 6055
    hass_url: str = "http://localhost:8123"
    hass_token: str = ""
    language: str = "en"
    silence_seconds: float = 1.0
    max_command_seconds: float = 15.0
    drain_timeout: float = 30.0
    max_score: float | None = None
    save_dir: Path | None = None
    second_pass: bool = True
    """Re-transcribe with Parakeet's TDT head when the grammar decode fails.
    Costs ~450 ms and ~590 MB on a Pi 4; disable there if memory is tight."""
    tdt_threads: int = 2
    reconnect_seconds: float = 5.0
    max_reconnect_seconds: float = 60.0
    """Ceiling for the backoff after short-lived connections."""
    wake_models: tuple[str, ...] = ("okay_nabu", "vad")
    """Wake word models pushed to the device when it connects, by file stem in
    models/wake. Nothing is compiled into the firmware, so an empty list leaves
    the device unable to detect anything. A model named ``vad`` gates the rest."""
    wake_ratio: float | None = None
    """Per-frame threshold for the second opinion on a rejected wake word.
    Negative disables it."""
    wake_probability_cutoff: float | None = None
    """Overrides every model's manifest cutoff. The stock 0.85 wants a voice at
    the device; a loudspeaker across a room peaks far lower."""


@dataclass
class Call:
    """Audio and state for one wake-word-to-hangup session."""

    preroll_bytes: int
    wake_word: str
    started: float = field(default_factory=time.monotonic)
    audio: bytearray = field(default_factory=bytearray)
    """The AGC capture. The command is split out of this one."""
    aec: bytearray = field(default_factory=bytearray)
    """The same call tapped earlier in the XMOS pipeline, recorded only."""
    command: bytearray = field(default_factory=bytearray)
    verified: bool = False
    finished: bool = False
    endpointer: Endpointer | None = None
    split_bytes: int = 0


@dataclass
class Session:
    """One connected satellite.

    Everything here used to live on the app. Splitting it out is what lets one
    set of models serve several devices: the models are the expensive part, and
    a second satellite now costs a socket and a buffer rather than another
    450 MB of ONNX.
    """

    key: str
    host: str
    port: int
    writer: asyncio.StreamWriter | None = None
    call: Call | None = None
    drained: asyncio.Event = field(default_factory=asyncio.Event)
    device_id: str = "unknown-device"

    def __str__(self) -> str:
        return self.device_id


class VoiceCallApp:
    def __init__(self, options: Options) -> None:
        self.options = options
        root = Path(__file__).resolve().parent.parent
        self.root = root
        self.hass = HomeAssistant(options.hass_url, options.hass_token)
        self.wake = WakeVerifier(root / "wake.yaml")
        # The fp32 encoder is 862 MB resident, which is most of a 2 GB Pi.
        # Prefer the int8 build when it has been produced.
        s2p_dir = root / "models/speech-to-phrase/stt_en_parakeet_tdt_ctc_110m-int8"
        if not (s2p_dir / "model.onnx").exists():
            s2p_dir = root / "models/speech-to-phrase/stt_en_parakeet_tdt_ctc_110m"
        self.recognizer = CommandRecognizer(
            s2p_dir,
            language=options.language,
            max_score=options.max_score,
        )
        self.wildcards = WildcardMatcher(language=options.language)
        # A second opinion on the two Kaldi tests that are about "is this the
        # wake word or just speech". Its `confusable` test is left alone.
        self.wake_ratio = (
            WakeRatio(threshold=options.wake_ratio)
            if options.wake_ratio is not None
            else WakeRatio()
        )
        self.grammars = FstHybrid(root / "var/grammars")
        # Prefer the int8 encoder: 172 MB vs 456 MB, same transcripts, faster.
        tdt_dir = root / "models/parakeet-tdt-int8"
        if not (tdt_dir / "encoder.onnx").exists():
            tdt_dir = root / "models/parakeet-tdt"
        self.tdt = (
            TdtTranscriber(tdt_dir, options.tdt_threads)
            if options.second_pass
            else None
        )
        self.speech = Speech(
            root / "models/piper/en_GB-jenny_dioco-medium.onnx",
            root / "sounds/acknowledge.mp3",
        )
        self._wake_models: list[WakeModel] = []
        self._sentences: list[str] = []
        self._has_grammar = False
        self._watcher: asyncio.Task | None = None
        self._tasks: set[asyncio.Task] = set()
        self._sessions: dict[str, asyncio.Task] = {}
        self._discovery: Discovery | None = None
        # The recognizer and the Kaldi decoders guard themselves; the FST
        # grammars, the TDT pass and Piper do not. Two satellites waking at
        # once is rare, so one lock over the rest is enough and keeps a
        # retrain from swapping grammars out mid-decode.
        self._model_lock = asyncio.Lock()

    async def start(self) -> None:
        sentences = await self.hass.sentence_triggers()
        if not sentences.templates:
            raise RuntimeError(
                "No conversation sentence triggers found in Home Assistant; "
                "the app has nothing to recognize"
            )
        self._log_sentences(sentences)
        self._sentences = sorted(sentences.templates)

        self._load_wake_models()

        await self.wake.start()
        await self.recognizer.start(sentences.plain)
        self._has_grammar = bool(sentences.plain)
        # Shares the recognizer's loaded net, so this costs two small grammars
        # rather than another model.
        if self.options.wake_ratio is None or self.options.wake_ratio >= 0:
            await asyncio.to_thread(self.wake_ratio.train, self.recognizer.model)
        self.wildcards.train(sentences.wildcard)
        await self._train_grammars(sentences.wildcard)
        await self.speech.start()
        if self.tdt is not None:
            await self.tdt.start()

        self._watcher = asyncio.ensure_future(
            self.hass.watch_automations(self._on_automations_changed)
        )

    async def stop(self) -> None:
        if self._watcher is not None:
            self._watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watcher
            self._watcher = None
        for task in list(self._sessions.values()):
            task.cancel()
        self._sessions.clear()
        if self._discovery is not None:
            await self._discovery.stop()
            self._discovery = None
        await self.wake.stop()

    def _load_wake_models(self) -> None:
        """Lower each configured .tflite into the blob the device executes.

        The device has no TFLite interpreter and no flatbuffer parser, so the
        graph is walked here and sent as a flat op table plus weights.
        """
        self._wake_models = []
        for stem in self.options.wake_models:
            path = self.root / "models/wake" / f"{stem}.tflite"
            if not path.exists():
                _LOGGER.error("Wake model %s not found", path)
                continue
            model = load_wake_model(path)
            if (
                self.options.wake_probability_cutoff is not None
                and not model.is_vad
            ):
                model = replace(
                    model, probability_cutoff=self.options.wake_probability_cutoff
                )
            self._wake_models.append(model)
            _LOGGER.info(
                "Wake model %r: %d bytes, cutoff %.2f, window %d%s",
                model.wake_word,
                len(model.blob),
                model.probability_cutoff,
                model.sliding_window_size,
                " (VAD gate)" if model.is_vad else "",
            )
        if not any(not m.is_vad for m in self._wake_models):
            _LOGGER.warning("No wake word model loaded; the device cannot wake")

    async def _push_wake_models(self, session: Session) -> None:
        """Send every model to the device. It keeps none across a reboot."""
        for slot, model in enumerate(self._wake_models):
            await self._send_json(session, Message.MODEL_START, model.header(slot))
            for offset in range(0, len(model.blob), MAX_PAYLOAD):
                await self._send(
                    session,
                    Message.MODEL_DATA,
                    model.blob[offset : offset + MAX_PAYLOAD],
                )
            await self._send_json(session, Message.MODEL_END, {"id": model.model_id})

    @staticmethod
    def _log_sentences(sentences) -> None:
        for kind, templates in (("grammar", sentences.plain), ("wildcard", sentences.wildcard)):
            for template in templates:
                _LOGGER.info(
                    "  [%s] %r (%s)",
                    kind,
                    template,
                    sentences.source.get(template.lower()),
                )

    async def _on_automations_changed(self) -> None:
        """Re-read sentence triggers and retrain if the vocabulary moved."""
        try:
            sentences = await self.hass.sentence_triggers()
        except OSError as err:
            _LOGGER.warning("Could not re-read sentence triggers: %s", err)
            return

        updated = sorted(sentences.templates)
        if updated == self._sentences:
            _LOGGER.debug("Automations reloaded; sentence triggers unchanged")
            return

        if not updated:
            # Keep decoding the old vocabulary rather than leaving the app deaf.
            _LOGGER.warning(
                "Automations reloaded with no sentence triggers left; "
                "keeping the previous %d",
                len(self._sentences),
            )
            return

        _LOGGER.info(
            "Sentence triggers changed (%d -> %d); retraining",
            len(self._sentences),
            len(updated),
        )
        self._log_sentences(sentences)
        # Held across the whole retrain: swapping grammars under an in-flight
        # select would be a race no amount of retrying recovers from.
        async with self._model_lock:
            try:
                await self.recognizer.train(sentences.plain)
            except (RuntimeError, ValueError) as err:
                _LOGGER.error("Retraining failed, keeping previous grammar: %s", err)
                return
            self._has_grammar = bool(sentences.plain)
            self.wildcards.train(sentences.wildcard)
            await self._train_grammars(sentences.wildcard)
        self._sentences = updated

    def _spawn(self, coro) -> None:
        """Run beside the read loop, which must stay free to deliver frames."""
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def run_forever(self) -> None:
        """Serve every satellite on the network, as they come and go."""
        if self.options.host:
            # Explicit host pins the app to one device: useful across subnets,
            # and for testing one satellite while others are on the network.
            await self._run_session(
                Session(key="manual", host=self.options.host, port=self.options.port)
            )
            return

        self._discovery = Discovery()
        await self._discovery.start()
        try:
            while True:
                change, device = await self._discovery.events.get()
                if change == "added":
                    self._start_session(device)
                else:
                    self._stop_session(device)
        finally:
            await self._discovery.stop()
            for task in list(self._sessions.values()):
                task.cancel()

    def _start_session(self, device: Device) -> None:
        existing = self._sessions.pop(device.key, None)
        if existing is not None:
            existing.cancel()
        _LOGGER.info("Satellite appeared: %s", device)
        session = Session(key=device.key, host=device.host, port=device.port)
        task = asyncio.ensure_future(self._run_session(session))
        self._sessions[device.key] = task
        task.add_done_callback(
            lambda t, key=device.key: self._sessions.pop(key, None)
            if self._sessions.get(key) is t
            else None
        )

    def _stop_session(self, device: Device) -> None:
        task = self._sessions.pop(device.key, None)
        if task is not None:
            _LOGGER.info("Satellite went away: %s", device)
            task.cancel()

    async def _run_session(self, session: Session) -> None:
        backoff = self.options.reconnect_seconds
        while True:
            try:
                _LOGGER.info("Connecting to %s:%d", session.host, session.port)
                reader, writer = await asyncio.open_connection(
                    session.host, session.port
                )
            except OSError as err:
                _LOGGER.warning("Connect failed (%s); retrying in %.0fs", err, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self.options.max_reconnect_seconds)
                continue

            self._enable_keepalive(writer)
            session.writer = writer
            started = time.monotonic()
            try:
                await self._read_loop(session, reader)
            except (asyncio.IncompleteReadError, ConnectionError, OSError) as err:
                # OSError covers the keepalive firing on a rebooted device,
                # which surfaces as ETIMEDOUT rather than a ConnectionError.
                _LOGGER.warning("[%s] Connection lost: %s", session, err)
            finally:
                session.call = None
                session.writer = None
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()

            # A connection that barely lived means something is wrong that
            # retrying fast will not fix -- most often a second app holding the
            # device. Backing off turns a busy loop into a slow one, and the
            # growing delay in the log is the clue that it is not the network.
            if time.monotonic() - started >= STABLE_CONNECTION_SECONDS:
                backoff = self.options.reconnect_seconds
            else:
                _LOGGER.warning(
                    "[%s] Connection lasted under %.0fs; retrying in %.0fs. If "
                    "this repeats, check whether another app is connected to "
                    "the device -- it accepts one client at a time.",
                    session,
                    STABLE_CONNECTION_SECONDS,
                    backoff,
                )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, self.options.max_reconnect_seconds)

    @staticmethod
    def _enable_keepalive(writer: asyncio.StreamWriter) -> None:
        """Notice a device that rebooted under an idle connection.

        Nothing crosses the socket between calls, so a device that reboots
        leaves the app holding a half-open connection it will never see fail --
        and the device, having lost its models to the reboot, stays deaf until
        the app reconnects and pushes them again.
        """
        sock = writer.get_extra_info("socket")
        if sock is None:
            return
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 10)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 5)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
        except OSError as err:
            _LOGGER.warning("Could not enable TCP keepalive: %s", err)

    async def _read_loop(
        self, session: Session, reader: asyncio.StreamReader
    ) -> None:
        while True:
            header = await reader.readexactly(HEADER.size)
            msg_type, length = HEADER.unpack(header)
            payload = await reader.readexactly(length) if length else b""
            await self._handle(session, msg_type, payload)

    async def _send(
        self, session: Session, msg_type: Message, payload: bytes = b""
    ) -> None:
        if session.writer is None:
            return
        session.writer.write(encode(msg_type, payload))
        # Propagates the device's TCP backpressure back to whatever produced
        # this audio, so playback is never queued faster than it drains.
        await session.writer.drain()

    async def _send_json(self, session: Session, msg_type: Message, obj: dict) -> None:
        if session.writer is None:
            return
        session.writer.write(encode_json(msg_type, obj))
        await session.writer.drain()

    async def _event(self, session: Session, event_type: str, **data) -> None:
        await self._send_json(
            session, Message.EVENT, {"type": event_type, "data": data}
        )

    async def _handle(self, session: Session, msg_type: int, payload: bytes) -> None:
        if msg_type == Message.AUDIO_IN:
            await self._on_audio(session, payload)
            return

        if msg_type == Message.AUDIO_IN_AEC:
            if session.call is not None:
                session.call.aec += payload
            return

        info = json.loads(payload) if payload else {}

        if msg_type == Message.HELLO:
            session.device_id = _device_id(info)
            _LOGGER.info("[%s] Device: %s", session, info)
            self._spawn(self._push_wake_models(session))
        elif msg_type == Message.MODEL_RESULT:
            if info.get("ok"):
                _LOGGER.info(
                    "[%s] Device loaded model %r (%s resident)",
                    session,
                    info.get("id"),
                    info.get("models"),
                )
            else:
                _LOGGER.error(
                    "[%s] Device rejected model %r: %s",
                    session,
                    info.get("id"),
                    info.get("error"),
                )
        elif msg_type == Message.CALL_START:
            self._on_call_start(session, info)
        elif msg_type == Message.CALL_END:
            _LOGGER.info("[%s] Device ended call: %s", session, info.get("reason"))
            session.call = None
        elif msg_type == Message.PLAYBACK_DRAINED:
            session.drained.set()
        elif msg_type == Message.STATUS:
            _LOGGER.debug("[%s] status: %s", session, info)
        else:
            _LOGGER.warning("Unhandled message 0x%02X: %s", msg_type, info)

    def _on_call_start(self, session: Session, info: dict) -> None:
        session.call = Call(
            preroll_bytes=int(info.get("preroll_bytes", 0)),
            wake_word=info.get("wake_word", ""),
        )
        session.drained.clear()
        _LOGGER.info(
            "[%s] Call started: wake_word=%r pre-roll=%dms%s",
            session,
            session.call.wake_word,
            session.call.preroll_bytes * 1000 // BYTE_RATE,
            (
                f" aec pre-roll={int(info['aec_preroll_bytes']) * 1000 // BYTE_RATE}ms"
                if info.get("aec")
                else ""
            ),
        )
        self._spawn(self._watchdog(session, session.call))

    async def _watchdog(self, session: Session, call: Call) -> None:
        """End the call even if the VAD never fires or audio stops arriving."""
        await asyncio.sleep(self.options.max_command_seconds)
        if session.call is call and not call.finished:
            _LOGGER.warning(
                "[%s] Command exceeded %.1fs; ending",
                session,
                self.options.max_command_seconds,
            )
            call.finished = True
            self._spawn(self._hang_up(session, "timeout"))

    async def _on_audio(self, session: Session, chunk: bytes) -> None:
        call = session.call
        if call is None or call.finished:
            return

        call.audio += chunk

        # The pre-roll is the verification window: it ends at the detection, so
        # it holds the wake word and whatever preceded it.
        if not call.verified:
            if len(call.audio) < call.preroll_bytes:
                return
            call.verified = True
            self._spawn(self._verify(session, call))
            return

        if call.endpointer is None:
            return  # verification still running; audio keeps accumulating

        start = max(call.split_bytes, len(call.audio) - len(chunk))
        new_audio = bytes(call.audio[start:])
        if not new_audio:
            return
        call.command += new_audio
        if call.endpointer.process(new_audio) and not call.finished:
            call.finished = True
            self._spawn(self._respond(session, call))

    async def _wake_rejected(
        self, session: Session, call: Call, result: Any, score: float | None
    ) -> None:
        """Hang up on a wake word neither stage would accept.

        Keep the audio: a rejected wake word is otherwise invisible, and the
        rejections are what need examining when the rate looks wrong.
        """
        if result.split:
            call.command += bytes(call.audio[ms_to_bytes(result.split * 1000) :])
        self._save(session, call, prefix="rejected-")
        if score is not None:
            _LOGGER.info(
                "[%s] Ratio agreed with the %s rejection (%.2f)",
                session,
                result.rejected_by,
                score,
            )
        await self._event(
            session, "wake_rejected", reason=result.rejected_by or "unknown"
        )
        call.finished = True
        await self._hang_up(session, "wake_rejected")

    async def _verify(self, session: Session, call: Call) -> None:
        window = bytes(call.audio[: call.preroll_bytes])
        result = await self.wake.verify(window)
        _LOGGER.info(
            "[%s] Wake verification: accepted=%s rejected_by=%s split=%s "
            "decode=%.0fms",
            session,
            result.accepted,
            result.rejected_by,
            result.split,
            result.decode_ms,
        )

        if not result.accepted:
            # The thresholds Kaldi applies to the phrase and to its last word
            # were calibrated on adult speech, and they refuse a lot of real
            # wake words from other voices. Ask the recognizer, which is
            # already loaded, whether the window is the wake word at all --
            # but never about `confusable`, which is the one decision it gets
            # wrong far more often than Kaldi does.
            overturned, score = self.wake_ratio.would_accept(
                result.rejected_by, window
            )
            if overturned:
                _LOGGER.info(
                    "[%s] Wake %s rejection overturned on the ratio (%.2f)",
                    session,
                    result.rejected_by,
                    score if score is not None else float("nan"),
                )
                await self._event(
                    session, "wake_overturned", reason=result.rejected_by or "unknown"
                )
            else:
                return await self._wake_rejected(session, call, result, score)

        # Everything after the wake phrase is command audio, including what
        # arrived while the decode was running.
        call.split_bytes = (
            ms_to_bytes(result.split * 1000) if result.split else call.preroll_bytes
        )
        call.endpointer = Endpointer(silence_seconds=self.options.silence_seconds)
        call.command += bytes(call.audio[call.split_bytes :])
        await self._event(session, "listening", stage="listening")
        if call.endpointer.process(bytes(call.command)) and not call.finished:
            call.finished = True
            self._spawn(self._respond(session, call))

    async def _respond(self, session: Session, call: Call) -> None:
        self._save(session, call)
        _LOGGER.info(
            "[%s] Command audio: %.2fs", session, len(call.command) / BYTE_RATE
        )

        command_text, heard = await self._recognize(bytes(call.command))
        # The recognizer logs its own scores without knowing which satellite
        # asked, so attribute the outcome here: with two devices the lines
        # interleave and the score line alone cannot be traced back.
        if command_text is None:
            _LOGGER.info("[%s] Not recognized; heard %r", session, heard)
            await self._event(session, "not_recognized", heard=heard)
            await self._hang_up(session, "not_recognized")
            return

        _LOGGER.info("[%s] Matched %r", session, command_text)
        await self._event(session, "recognized", text=command_text)
        response = await self.hass.converse(command_text, self.options.language)
        response = await self._render(response)
        await self._event(session, "responding", text=response)

        async with self._model_lock:
            audio = await self.speech.say(response)
        await self._play(session, audio)
        await self._hang_up(session, "done")

    async def _train_grammars(self, sentences: list[str]) -> None:
        """Compile a free-loop grammar per wildcard sentence (cached on disk)."""
        try:
            await asyncio.to_thread(
                self.grammars.train,
                sentences,
                self.recognizer.tokenizer,
                self.recognizer.tokenizer.num_tokens,
            )
        except (RuntimeError, ValueError, OSError) as err:
            _LOGGER.error("Wildcard grammars unavailable, falling back: %s", err)

    async def _recognize(self, pcm: bytes) -> tuple[str | None, str]:
        """Serialize recognition across satellites, then resolve."""
        async with self._model_lock:
            return await self._recognize_locked(pcm)

    async def _recognize_locked(self, pcm: bytes) -> tuple[str | None, str]:
        """Resolve command audio to a sentence Home Assistant will match.

        The constrained grammar is tried first. Wildcard triggers cannot be in
        that grammar, so a rejection falls back to the unconstrained transcript
        matched with hassil -- the same way Home Assistant matches them.
        """
        heard = ""
        if self._has_grammar:
            recognition = await self.recognizer.recognize(pcm)
            if recognition.accepted:
                return recognition.text, recognition.greedy_text
            heard = recognition.greedy_text
        else:
            heard = await self.recognizer.greedy_transcribe(pcm)

        if not self.wildcards.sentences:
            return None, heard

        # Decode each wildcard sentence as its own grammar -- literals pinned,
        # slots a free loop -- and take the cheapest. This settles *which*
        # sentence was said from acoustics, which matching a transcript cannot
        # do when the words that differ are the ones the decoder gets wrong
        # ("play the artist X" vs "play the album X").
        if self.grammars.available:
            log_probs = await self.recognizer.log_probs(pcm)
            chosen = await asyncio.to_thread(self.grammars.select, log_probs)
            if chosen is not None and chosen.canonical:
                _LOGGER.info(
                    "Grammar match %r (cost %.2f) -> %s %s",
                    chosen.text,
                    chosen.cost,
                    chosen.sentence,
                    chosen.slots,
                )
                return chosen.canonical, chosen.text or heard

        # Second pass. The CTC head decoded greedily at 80 ms frames mixes
        # subword paths ("Pplayay"); the transducer head is the decoder
        # Parakeet's published accuracy refers to. Sequential on purpose:
        # on a 4-core Pi one ONNX pass already saturates the cores, so running
        # this beside the CTC pass measured slower than after it.
        if self.tdt is not None and self.tdt.available:
            second = await self.tdt.transcribe(pcm)
            if second:
                _LOGGER.info("Second pass (TDT): %r", second)
                heard = second

        match = self.wildcards.match(heard)
        if match is None:
            _LOGGER.info("No wildcard trigger matches %r", heard)
            return None, heard

        # Home Assistant re-matches with plain hassil, so send the repaired
        # text (trigger's literals, heard slot values) rather than the raw
        # transcript -- otherwise anchoring rescues the match here and HA
        # loses it again.
        command = match.text or heard
        _LOGGER.info("Wildcard match %r -> %s -> %r", heard, match.sentence, command)
        return command, heard

    async def _render(self, response: str) -> str:
        """Render a response that still carries template syntax.

        Home Assistant renders set_conversation_response itself, so this is a
        safety net for a response that arrives unrendered by some other path.
        """
        if "{{" not in response and "{%" not in response:
            return response
        try:
            rendered = await self.hass.render_template(response)
        except (OSError, RuntimeError) as err:
            _LOGGER.warning("Could not render response template: %s", err)
            return response
        _LOGGER.info("Rendered response %r -> %r", response, rendered)
        return rendered

    async def _play(self, session: Session, pcm: bytes) -> None:
        for offset in range(0, len(pcm), MAX_PAYLOAD):
            await self._send(
                session, Message.AUDIO_OUT, pcm[offset : offset + MAX_PAYLOAD]
            )
        await self._send(session, Message.AUDIO_OUT_END)
        try:
            await asyncio.wait_for(
                session.drained.wait(), timeout=self.options.drain_timeout
            )
        except TimeoutError:
            _LOGGER.warning("[%s] Timed out waiting for playback to drain", session)

    async def _hang_up(self, session: Session, reason: str) -> None:
        _LOGGER.info("[%s] Hanging up: %s", session, reason)
        await self._send_json(session, Message.HANGUP, {"reason": reason})
        session.call = None

    def _save(self, session: Session, call: Call, prefix: str = "") -> None:
        if self.options.save_dir is None:
            return
        # One directory per device. Several VPEs flashed from the same YAML all
        # report the same name, so the MAC suffix is what actually separates
        # them, and timestamps alone would interleave their calls.
        out_dir = self.options.save_dir / session.device_id
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = int(call.started)
        for name, pcm in (
            ("call", call.audio),
            ("call-aec", call.aec),
            ("command", call.command),
        ):
            if not pcm:
                continue
            path = out_dir / f"{prefix}{name}-{stamp}.wav"
            with wave.open(str(path), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(BYTES_PER_SAMPLE)
                wav.setframerate(SAMPLE_RATE)
                wav.writeframes(pcm)
        _LOGGER.info(
            "Saved %s/{call,call-aec,command}-%d.wav", out_dir, stamp
        )
