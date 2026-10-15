"""Home Assistant's STT and TTS entities, in place of the built-in models.

Chosen for every satellite (``settings.yaml``: ``stt``, ``tts``,
``tts_voice``) and per satellite (``satellites.yaml``, the same keys, where
``builtin`` means the app's own model and no key means "as every satellite"),
plus ``speak_on``: a media player that says a satellite's responses instead of
the satellite itself.

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
import time
from dataclasses import dataclass
from typing import Any

import numpy as np

from .hass import HassError, HomeAssistant

_LOGGER = logging.getLogger(__name__)

BUILTIN = "builtin"
DEFAULT_VOICE = "default"
"""A satellite's own choice of the TTS entity's default voice."""
LANGUAGE = "en"
STT_TIMEOUT = 15.0


@dataclass
class VoiceChoice:
    """What speaks and listens for one satellite: entity ids, or None for the
    built-in models."""

    stt: str | None = None
    tts: str | None = None
    tts_voice: str | None = None
    speak_on: str | None = None
    """A media player for the responses; None: the satellite itself."""


def choose(settings: dict[str, Any], stored: dict[str, Any]) -> VoiceChoice:
    """Every satellite's choice, unless this one has its own."""

    def pick(key: str) -> str | None:
        value = stored.get(key) if key in stored else settings.get(key)
        return None if value in (None, "", BUILTIN, DEFAULT_VOICE) else str(value)

    tts = pick("tts")
    return VoiceChoice(
        stt=pick("stt"),
        tts=tts,
        tts_voice=pick("tts_voice") if tts else None,
        speak_on=(str(stored["speak_on"]) if tts and stored.get("speak_on") else None),
    )


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

    def transcribe(self, audio: np.ndarray) -> str:
        if self._text is None:
            pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2").tobytes()
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
        }


class Voices:
    """The STT and TTS entities Home Assistant has, and their languages."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._stt_languages: dict[str, str | None] = {}
        self._tts_languages: dict[str, str | None] = {}

    async def engines(self) -> dict[str, list[dict[str, Any]]]:
        """For the UI: entities that speak or understand the app's language."""
        stt = await self.hass.stt_engines(LANGUAGE)
        tts = await self.hass.tts_engines(LANGUAGE)
        names = {e.entity_id: e.name for e in self.hass.catalog.entities.values()}

        def entries(providers: list[dict]) -> list[dict[str, Any]]:
            return [
                {
                    "entity_id": p["engine_id"],
                    "name": names.get(p["engine_id"]) or p.get("name") or p["engine_id"],
                    "languages": p.get("supported_languages") or [],
                }
                for p in providers
            ]

        return {"stt": entries(stt), "tts": entries(tts)}

    async def stt_language(self, entity_id: str) -> str:
        if entity_id not in self._stt_languages:
            engines = await self.hass.stt_engines(LANGUAGE)
            self._stt_languages[entity_id] = _best(engines, entity_id)
        language = self._stt_languages[entity_id]
        if language is None:
            raise HassError(f"{entity_id} doesn't understand English")
        return language

    async def tts_language(self, entity_id: str) -> str | None:
        if entity_id not in self._tts_languages:
            self._tts_languages[entity_id] = _best(await self.hass.tts_engines(LANGUAGE), entity_id)
        return self._tts_languages[entity_id]

    async def voices(self, entity_id: str) -> list[dict[str, Any]]:
        language = await self.tts_language(entity_id)
        return await self.hass.tts_voices(entity_id, language) if language else []

    async def transcriber(self, entity_id: str, fallback: Any = None) -> HassTranscriber | Any:
        """The STT entity for one command, or the fallback when its language
        cannot be had."""
        try:
            language = await self.stt_language(entity_id)
        except (HassError, ConnectionError, OSError, TimeoutError) as err:
            _LOGGER.warning("%s: %s; using TDT", entity_id, err)
            return fallback
        return HassTranscriber(self.hass, entity_id, language, asyncio.get_running_loop(), fallback)

    def forget(self) -> None:
        """Entities changed: look their languages up again."""
        self._stt_languages.clear()
        self._tts_languages.clear()


def _best(engines: list[dict], entity_id: str) -> str | None:
    """The best match for the app's language, as Home Assistant ranked them."""
    engine = next((e for e in engines if e.get("engine_id") == entity_id), None)
    languages = (engine or {}).get("supported_languages") or []
    return languages[0] if languages else None
