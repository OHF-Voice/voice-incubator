"""Home Assistant's STT and TTS entities, in place of the built-in models.

Chosen for every satellite (``settings.yaml``: ``stt``, ``tts``,
``tts_voice``, ``stt_language``, ``tts_language``) and per satellite
(``satellites.yaml``, the same keys, where ``builtin`` means the app's own
model and no key means "as every satellite"), plus ``speak_on``: a media
player that says a satellite's responses instead of the satellite itself.

A language is a code the entity supports (``de-DE``); without one, the
entity's best match for English.

* **STT.** The CTC matcher always runs. Text -- examples not found in the CTC
  evidence, free text, hints -- comes from the STT entity instead of the TDT
  head, and only when it is needed (``HassTranscriber``, which the engine
  calls from its thread). Should it fail, the TDT head stands in.
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

from .hass import HassError, HomeAssistant

_LOGGER = logging.getLogger(__name__)

BUILTIN = "builtin"
DEFAULT_VOICE = "default"
AUTO = "auto"
"""A satellite's own choice of the entity's best language for English."""
"""A satellite's own choice of the TTS entity's default voice."""
LANGUAGE = "en"
STT_TIMEOUT = 15.0
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
    """None: the entity's best match for English."""
    stt_audio: str = "agc"
    """The capture the STT entity hears: ``agc``, ``aec`` or ``aec_boost``
    (STT_AUDIO)."""


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
        self._text: str | None = None
        self.audio_for: Any = None
        """The audio to send in place of the command's (AGC) audio: a function
        of it, returning the audio and what it is (server.aec_for_stt)."""
        self.audio: dict[str, Any] = {"channel": "agc"}

    def transcribe(self, audio: np.ndarray) -> str:
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
            "asked": self._text is not None,
            "text": self._text,
            "seconds": round(self.seconds, 3) if self.seconds is not None else None,
            "error": self.error,
            "audio": self.audio,
        }


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
        """The best match for English, per entity."""
        self._supported: dict[str, list[str]] = {}
        """Every language, per entity."""

    async def engines(self) -> dict[str, list[dict[str, Any]]]:
        """For the UI: every STT and TTS entity, with every language it
        supports, and its best match for English (``english``)."""
        names = {e.entity_id: e.name for e in self.hass.catalog.entities.values()}
        out: dict[str, list[dict[str, Any]]] = {}
        for kind, listing in (("stt", self.hass.stt_engines), ("tts", self.hass.tts_engines)):
            every = await listing(None)
            english = {p["engine_id"]: _first(p) for p in await listing(LANGUAGE)}
            out[kind] = [
                {
                    "entity_id": p["engine_id"],
                    "name": names.get(p["engine_id"]) or p.get("name") or p["engine_id"],
                    "languages": p.get("supported_languages") or [],
                    "english": english.get(p["engine_id"]),
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
        variant of it: "de" -> "de-DE"), else its best match for English."""
        if wanted:
            matched = _match(wanted, await self._supported_languages(entity_id, self.hass.stt_engines))
            if matched is None:
                raise HassError(f"{entity_id} doesn't support {wanted}")
            return matched
        if entity_id not in self._stt_languages:
            self._stt_languages[entity_id] = _best(await self.hass.stt_engines(LANGUAGE), entity_id)
        language = self._stt_languages[entity_id]
        if language is None:
            raise HassError(f"{entity_id} doesn't understand English")
        return language

    async def tts_language(self, entity_id: str, wanted: str | None = None) -> str | None:
        if wanted:
            return _match(wanted, await self._supported_languages(entity_id, self.hass.tts_engines)) or wanted
        if entity_id not in self._tts_languages:
            self._tts_languages[entity_id] = _best(await self.hass.tts_engines(LANGUAGE), entity_id)
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
