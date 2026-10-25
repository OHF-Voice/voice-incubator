"""Home Assistant's STT and TTS entities, in place of the built-in models.

Chosen for every satellite (``settings.json``: ``stt``, ``tts``,
``tts_voice``, ``stt_language``, ``tts_language``) and per satellite
(``satellites.json``, the same keys, where ``builtin`` means the app's own
model and no key means "as every satellite"), plus ``speak_on``: a media
player that says a satellite's responses instead of the satellite itself.

A language is a code the entity supports (``de-DE``); without one, the
entity's best match for the app's language.

* **STT.** The CTC matcher always runs. Text -- examples not found in the CTC
  evidence, free text, hints -- comes from the STT entity instead of the TDT
  head, and only when it is needed (``HassTranscriber``, which the engine
  calls from its thread). Should it fail, the TDT head stands in. Set to
  always run (``stt_always``), the entity hears the command as it is said
  (``StreamingTranscriber``): its transcript is there whether or not it is
  needed, and sooner when it is.
* **TTS.** Responses are spoken by ``tts.speak`` on the satellite's own media
  player (the firmware's announcement pipeline: Home Assistant sends the URL,
  the device fetches it) or on ``speak_on``. The acknowledgement sound, for a
  command with nothing to say, is still the satellite's own.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

import numpy as np

from . import languages
from .hass import HassError, HomeAssistant

_LOGGER = logging.getLogger(__name__)

BUILTIN = "builtin"
DEFAULT_VOICE = "default"
AUTO = "auto"
"""A satellite's own choice of the entity's best match for the app's language."""
"""A satellite's own choice of the TTS entity's default voice."""
STT_TIMEOUT = 15.0
STREAM_TIMEOUT = 90.0
"""A streamed request: the longest command (60 s at most), then an answer."""
STT_AUDIO = ("agc", "aec", "aec_boost")
"""What an STT entity hears: the satellite's AGC capture (what commands are
recognized from), or its AEC capture (the satellite's own playback taken out)
-- as captured, or boosted to a normal level."""


@dataclass
class VoiceChoice:
    """What speaks and listens for one satellite: entity ids, or None for the
    built-in models."""

    stt: str | None = None
    tts: str | None = None
    tts_voice: str | None = None
    speak_on: str | None = None
    """A media player for the responses; None: the satellite itself."""
    stt_language: str | None = None
    tts_language: str | None = None
    """None: the entity's best match for the app's language."""
    stt_audio: str = "agc"
    """The capture the STT entity hears: ``agc``, ``aec`` or ``aec_boost``
    (STT_AUDIO)."""
    stt_always: bool = False
    """The STT entity hears every command, as it is said, not only when its
    words are needed."""


def choose(settings: dict[str, Any], stored: dict[str, Any]) -> VoiceChoice:
    """Every satellite's choice, unless this one has its own."""

    def pick(key: str) -> str | None:
        value = stored.get(key) if key in stored else settings.get(key)
        return None if value in (None, "", BUILTIN, DEFAULT_VOICE, AUTO) else str(value)

    tts = pick("tts")
    stt = pick("stt")
    return VoiceChoice(
        stt=stt,
        tts=tts,
        tts_voice=pick("tts_voice") if tts else None,
        speak_on=(str(stored["speak_on"]) if tts and stored.get("speak_on") else None),
        stt_language=pick("stt_language") if stt else None,
        tts_language=pick("tts_language") if tts else None,
        stt_audio=(pick("stt_audio") if stt and pick("stt_audio") in STT_AUDIO else "agc"),
        stt_always=bool(stt) and (stored.get("stt_always") if "stt_always" in stored else settings.get("stt_always")) is True,
    )


LANGUAGE_CODE = re.compile(r"^[a-z]{2,3}([-_][A-Za-z0-9]{2,8})*$")


def language_code(value: Any) -> str:
    """A language code as given ("de", "de-DE"), or "" for automatic.
    Raises ValueError for anything else."""
    value = str(value or "").strip()
    if value and value != AUTO and not LANGUAGE_CODE.match(value):
        raise ValueError(f"{value!r} is not a language code (like de or de-DE)")
    return value


class HassTranscriber:
    """An STT entity in place of the TDT head, for one command. The engine
    calls it from its thread; the request runs on the event loop. The text is
    asked for once, and only if the engine needs it."""

    def __init__(
        self,
        hass: HomeAssistant,
        entity_id: str,
        language: str,
        loop: asyncio.AbstractEventLoop,
        fallback: Any = None,
    ) -> None:
        self.hass = hass
        self.entity_id = entity_id
        self.language = language
        self.loop = loop
        self.fallback = fallback
        self.source = entity_id
        self.error: str | None = None
        self.seconds: float | None = None
        self.asked = False
        """The engine needed the words."""
        self._text: str | None = None
        self.audio_for: Any = None
        """The audio to send in place of the command's as the engine cut it: a
        function of it, returning the audio and what it is -- the call from
        Kaldi's split, of the AGC or AEC capture (server.stt_audio_for)."""
        self.audio: dict[str, Any] = {"channel": "agc"}

    def transcribe(self, audio: np.ndarray) -> str:
        self.asked = True
        if self._text is None:
            sent = audio
            if self.audio_for is not None:
                sent, self.audio = self.audio_for(audio)
            pcm = (np.clip(sent, -1.0, 1.0) * 32767).astype("<i2").tobytes()
            started = time.perf_counter()
            future = asyncio.run_coroutine_threadsafe(
                self.hass.speech_to_text(self.entity_id, pcm, self.language), self.loop
            )
            try:
                self._text = future.result(STT_TIMEOUT)
            except Exception as err:  # noqa: BLE001 - any failure falls back
                future.cancel()
                self.error = str(err) or type(err).__name__
                _LOGGER.warning("%s failed (%s); using TDT", self.entity_id, self.error)
                if self.fallback is None:
                    self._text = ""
                else:
                    self.source = "TDT"
                    self._text = self.fallback.transcribe(audio)
            self.seconds = time.perf_counter() - started
        return self._text

    def transcribe_words(self, audio: np.ndarray) -> tuple[str, list | None]:
        """No word times from an STT entity: the engine finds free text by its
        keywords instead."""
        return self.transcribe(audio), None

    def to_json(self) -> dict[str, Any]:
        return {
            "entity": self.entity_id,
            "language": self.language,
            "asked": self.asked,
            "text": self._text,
            "seconds": round(self.seconds, 3) if self.seconds is not None else None,
            "error": self.error,
            "audio": self.audio,
        }


class StreamingTranscriber(HassTranscriber):
    """The STT entity hearing the command as it is said ("always run"):
    started when the wake word is verified, sent each chunk as it comes
    (``feed``) and ended with the command (``finish``). The engine asks it as
    it would ``HassTranscriber`` -- and waits for the entity's answer, not an
    upload -- or not at all; the transcript is had either way (``result``).
    Should the stream fail, a request as without it is made; should that
    fail, the TDT head stands in."""

    def __init__(
        self,
        hass: HomeAssistant,
        entity_id: str,
        language: str,
        loop: asyncio.AbstractEventLoop,
        fallback: Any = None,
        audio: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(hass, entity_id, language, loop, fallback)
        self.audio = audio or {"channel": "agc"}
        self.sent = 0
        """Bytes streamed."""
        self.position: int | None = None
        """Where the stream is in the capture it's cut from (the server's)."""
        self.closed = False
        self.dropped = False
        """The call ended without a command (``cancel``)."""
        self._ended: float | None = None
        self._answered: float | None = None
        self._chunks: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._task = loop.create_task(self._stream())
        self._task.add_done_callback(self._done)

    async def _upload(self):
        while (chunk := await self._chunks.get()) is not None:
            yield chunk

    async def _stream(self) -> str:
        # As long as the longest command, and then as long as one request.
        return await self.hass.speech_to_text(self.entity_id, self._upload(), self.language, timeout=STREAM_TIMEOUT)

    def _done(self, task: asyncio.Task) -> None:
        self._answered = time.perf_counter()
        if not task.cancelled() and task.exception() is not None:
            self.error = str(task.exception()) or type(task.exception()).__name__
            _LOGGER.warning("%s: streaming failed (%s)", self.entity_id, self.error)

    def feed(self, pcm: bytes) -> None:
        """More of the command (on the event loop)."""
        if pcm and not self.closed:
            self._chunks.put_nowait(pcm)
            self.sent += len(pcm)

    def finish(self) -> None:
        """The command ended: the entity has all of it."""
        if not self.closed:
            self.closed = True
            self._ended = time.perf_counter()
            self._chunks.put_nowait(None)

    def cancel(self) -> None:
        """The call ended without a command: nothing to hear."""
        self.closed = self.dropped = True
        self._task.cancel()

    async def result(self, timeout: float = STT_TIMEOUT) -> str | None:
        """What the entity heard (on the event loop), once the command
        ended; None when it failed or took longer than ``timeout``."""
        if self.dropped:
            return None
        try:
            return await asyncio.wait_for(asyncio.shield(self._task), timeout)
        except asyncio.CancelledError:
            if self._task.cancelled():
                return None
            raise
        except Exception:  # noqa: BLE001 - recorded in error
            if self.error is None and not self._task.done():
                self.error = f"no answer in {timeout:g} s"
            return None

    def transcribe(self, audio: np.ndarray) -> str:
        """The streamed transcript (from the engine's thread): waited for, if
        the entity is still working on it."""
        self.asked = True
        if self._text is None:
            future = asyncio.run_coroutine_threadsafe(self.result(), self.loop)
            try:
                text = future.result(STT_TIMEOUT + 1)
            except Exception:  # noqa: BLE001 - as a failed stream
                future.cancel()
                text = None
            if text is None:
                _LOGGER.warning("%s: no streamed transcript (%s); asking again", self.entity_id, self.error)
                return super().transcribe(audio)
            self._text = text
            self.seconds = self.waited
        return self._text

    @property
    def waited(self) -> float | None:
        """From the end of the command to the entity's answer."""
        if self._ended is None or self._answered is None:
            return None
        return max(0.0, self._answered - self._ended)

    def to_json(self) -> dict[str, Any]:
        out = super().to_json()
        if out["text"] is None and self._task.done() and not self._task.cancelled() and self._task.exception() is None:
            out["text"] = self._task.result()
        out["streamed"] = {
            "seconds": round(self.sent / 32000, 3),
            "after_speech": round(self.waited, 3) if self.waited is not None else None,
        }
        return out


class TypedText:
    """Typed text in place of speech recognition: what was typed is the
    transcript, whatever the audio (there is none)."""

    source = "typed"

    def __init__(self, text: str) -> None:
        self.text = text

    def transcribe(self, audio: np.ndarray) -> str:
        return self.text

    def transcribe_words(self, audio: np.ndarray) -> tuple[str, list | None]:
        """No word times: the engine finds free text by its keywords."""
        return self.text, None

    def to_json(self) -> dict[str, Any]:
        return {"typed": True, "text": self.text}


class Voices:
    """The STT and TTS entities Home Assistant has, and their languages."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._stt_languages: dict[str, str | None] = {}
        self._tts_languages: dict[str, str | None] = {}
        """The best match for the app's language, per entity."""
        self._supported: dict[str, list[str]] = {}
        """Every language, per entity."""

    async def engines(self) -> dict[str, list[dict[str, Any]]]:
        """For the UI: every STT and TTS entity, with every language it
        supports, and its best match for the app's language (``automatic``)."""
        names = {e.entity_id: e.name for e in self.hass.catalog.entities.values()}
        out: dict[str, list[dict[str, Any]]] = {}
        for kind, listing in (("stt", self.hass.stt_engines), ("tts", self.hass.tts_engines)):
            every = await listing(None)
            automatic = {p["engine_id"]: _first(p) for p in await listing(languages.current().code)}
            out[kind] = [
                {
                    "entity_id": p["engine_id"],
                    "name": names.get(p["engine_id"]) or p.get("name") or p["engine_id"],
                    "languages": p.get("supported_languages") or [],
                    "automatic": automatic.get(p["engine_id"]),
                }
                for p in every
            ]
            for entry in out[kind]:
                self._supported[entry["entity_id"]] = list(entry["languages"])
        return out

    async def _supported_languages(self, entity_id: str, listing) -> list[str]:
        if entity_id not in self._supported:
            for provider in await listing(None):
                self._supported[provider["engine_id"]] = list(provider.get("supported_languages") or [])
        return self._supported.get(entity_id, [])

    async def stt_language(self, entity_id: str, wanted: str | None = None) -> str:
        """The language to ask in: ``wanted`` if the entity supports it (or a
        variant of it: "de" -> "de-DE"), else its best match for the app's language."""
        if wanted:
            matched = _match(wanted, await self._supported_languages(entity_id, self.hass.stt_engines))
            if matched is None:
                raise HassError(f"{entity_id} doesn't support {wanted}")
            return matched
        if entity_id not in self._stt_languages:
            self._stt_languages[entity_id] = _best(await self.hass.stt_engines(languages.current().code), entity_id)
        language = self._stt_languages[entity_id]
        if language is None:
            raise HassError(f"{entity_id} doesn't understand {languages.current().name}")
        return language

    async def tts_language(self, entity_id: str, wanted: str | None = None) -> str | None:
        if wanted:
            return _match(wanted, await self._supported_languages(entity_id, self.hass.tts_engines)) or wanted
        if entity_id not in self._tts_languages:
            self._tts_languages[entity_id] = _best(await self.hass.tts_engines(languages.current().code), entity_id)
        return self._tts_languages[entity_id]

    async def voices(self, entity_id: str, wanted: str | None = None) -> list[dict[str, Any]]:
        language = await self.tts_language(entity_id, wanted)
        return await self.hass.tts_voices(entity_id, language) if language else []

    async def transcriber(
        self, entity_id: str, fallback: Any = None, wanted: str | None = None
    ) -> HassTranscriber | Any:
        """The STT entity for one command, or the fallback when its language
        cannot be had."""
        try:
            language = await self.stt_language(entity_id, wanted)
        except (HassError, ConnectionError, OSError, TimeoutError) as err:
            _LOGGER.warning("%s: %s; using TDT", entity_id, err)
            return fallback
        return HassTranscriber(self.hass, entity_id, language, asyncio.get_running_loop(), fallback)

    async def streaming_transcriber(
        self, entity_id: str, fallback: Any = None, wanted: str | None = None, audio: dict[str, Any] | None = None
    ) -> StreamingTranscriber | None:
        """The STT entity hearing a command as it is said, or None when its
        language cannot be had (it is asked as usual, or falls back, then)."""
        try:
            language = await self.stt_language(entity_id, wanted)
        except (HassError, ConnectionError, OSError, TimeoutError) as err:
            _LOGGER.warning("%s: %s; not streaming", entity_id, err)
            return None
        return StreamingTranscriber(self.hass, entity_id, language, asyncio.get_running_loop(), fallback, audio)

    def forget(self) -> None:
        """Entities changed: look their languages up again."""
        self._stt_languages.clear()
        self._tts_languages.clear()
        self._supported.clear()


def _first(provider: dict) -> str | None:
    languages = provider.get("supported_languages") or []
    return languages[0] if languages else None


def _best(engines: list[dict], entity_id: str) -> str | None:
    """The best match for the app's language, as Home Assistant ranked them."""
    engine = next((e for e in engines if e.get("engine_id") == entity_id), None)
    return _first(engine or {})


def _match(wanted: str, supported: list[str]) -> str | None:
    """``wanted`` among an entity's languages: exactly, or the first variant
    of the same language ("de" -> "de-DE"), ignoring case and _ for -."""
    def norm(code: str) -> str:
        return code.replace("_", "-").lower()

    for code in supported:
        if norm(code) == norm(wanted):
            return code
    base = norm(wanted).split("-")[0]
    return next((code for code in supported if norm(code).split("-")[0] == base), None)
