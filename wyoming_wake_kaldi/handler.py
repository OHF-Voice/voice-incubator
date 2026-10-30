"""Wyoming event handler.

The service is a *verifier*, not a streaming detector: a client that has
already detected a wake word on-device sends

    detect(names=[...]) -> audio-start -> audio-chunk* -> audio-stop

and gets back either

    detection(name=..., timestamp=<split point in ms>)

when the wake word is confirmed, or `not-detected` when it is not. The
timestamp is where the wake phrase ends in the submitted audio, i.e. where
the voice command begins.
"""

from __future__ import annotations

import json
import logging
import tempfile
import time
import wave
from datetime import datetime, timezone
from pathlib import Path

from wyoming.audio import AudioChunk, AudioChunkConverter, AudioStart, AudioStop
from wyoming.event import Event
from wyoming.info import Describe, Info
from wyoming.server import AsyncEventHandler
from wyoming.wake import Detect, Detection, NotDetected

from .service import WakeService
from .verify import VerifyResult

_LOGGER = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
SAMPLE_WIDTH = 2
CHANNELS = 1


class WakeKaldiEventHandler(AsyncEventHandler):
    def __init__(
        self,
        wyoming_info: Info,
        service: WakeService,
        debug_dir: Path | None,
        *args,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.wyoming_info_event = wyoming_info.event()
        self.service = service
        self.debug_dir = debug_dir
        self.converter = AudioChunkConverter(
            rate=SAMPLE_RATE, width=SAMPLE_WIDTH, channels=CHANNELS
        )

        self._requested: list[str] = []
        self._context: dict | None = None
        self._audio = bytearray()
        self._answered = False
        self._max_bytes = max(
            1,
            int(
                self.service.config.max_window_seconds
                * SAMPLE_RATE
                * SAMPLE_WIDTH
                * CHANNELS
            ),
        )

    async def handle_event(self, event: Event) -> bool:
        if Describe.is_type(event.type):
            await self.write_event(self.wyoming_info_event)
            return True

        if Detect.is_type(event.type):
            detect = Detect.from_event(event)
            self._requested = list(detect.names or [])
            self._context = detect.context
            self._reset_audio()
            _LOGGER.debug("detect: %s", self._requested or "(any)")
            return True

        if AudioStart.is_type(event.type):
            self._reset_audio()
            return True

        if AudioChunk.is_type(event.type):
            if self._answered:
                return True
            chunk = self.converter.convert(AudioChunk.from_event(event))
            self._audio.extend(chunk.audio)
            if len(self._audio) >= self._max_bytes:
                # Window full: decide now rather than buffering a whole stream.
                await self._answer()
            return True

        if AudioStop.is_type(event.type):
            if not self._answered:
                await self._answer()
            return True

        return True

    def _reset_audio(self) -> None:
        self._audio = bytearray()
        self._answered = False

    def _wake_word_ids(self) -> list[str]:
        """Configured wake words matching the `detect` request."""
        if not self._requested:
            return list(self.service.config.wake_words)

        ids: list[str] = []
        for name in self._requested:
            wake_word = self.service.resolve(name)
            if wake_word is None:
                _LOGGER.warning("Ignoring unconfigured wake word: %s", name)
            elif wake_word.id not in ids:
                ids.append(wake_word.id)
        return ids

    def _requested_name(self, wake_id: str) -> str:
        """Report back the exact name the client asked for, when it asked."""
        for name in self._requested:
            wake_word = self.service.resolve(name)
            if wake_word is not None and wake_word.id == wake_id:
                return name
        return wake_id

    async def _answer(self) -> None:
        self._answered = True
        started = time.monotonic()
        wake_ids = self._wake_word_ids()

        if not wake_ids:
            _LOGGER.warning(
                "No configured wake word matches %s", self._requested or "(any)"
            )
            await self.write_event(NotDetected(context=self._context).event())
            return

        if not self._audio:
            _LOGGER.warning("No audio received; not verifying")
            await self.write_event(NotDetected(context=self._context).event())
            return

        # Decode time is roughly linear in window length, so never decode more
        # than the window cap even if a client sent it all in one chunk.
        window = bytes(self._audio[: self._max_bytes])

        results: list[VerifyResult] = []
        with tempfile.TemporaryDirectory(prefix="wyoming-wake-kaldi-") as temp_dir:
            wav_path = Path(temp_dir) / "window.wav"
            _write_wav(wav_path, window)

            for wake_id in wake_ids:
                result = await self.service.verify(wake_id, wav_path)
                results.append(result)
                if result.accepted:
                    break

            if self.debug_dir is not None:
                self._write_debug(wav_path, results)

        accepted = next((r for r in results if r.accepted), None)
        seconds = len(window) / (SAMPLE_RATE * SAMPLE_WIDTH * CHANNELS)
        if accepted is not None and accepted.split_ms is not None:
            _LOGGER.info(
                "Accepted %s in %.2f s of audio: split at %d ms "
                "(lr_prefix=%.3f lr_last=%.3f margin=%.4f, %.0f ms)",
                accepted.wake_word_id,
                seconds,
                accepted.split_ms,
                accepted.lr_prefix or 0.0,
                accepted.lr_last or 0.0,
                accepted.confusable_margin or 0.0,
                (time.monotonic() - started) * 1000,
            )
            await self.write_event(
                Detection(
                    name=self._requested_name(accepted.wake_word_id),
                    timestamp=accepted.split_ms,
                    context=self._context,
                ).event()
            )
            return

        for result in results:
            _LOGGER.info(
                "Rejected %s in %.2f s of audio: %s "
                "(lr_prefix=%s lr_last=%s margin=%s)%s",
                result.wake_word_id,
                seconds,
                result.rejected_by,
                _fmt(result.lr_prefix),
                _fmt(result.lr_last),
                _fmt(result.confusable_margin),
                f" [{result.error}]" if result.error else "",
            )
        await self.write_event(NotDetected(context=self._context).event())

    def _write_debug(self, wav_path: Path, results: list[VerifyResult]) -> None:
        try:
            assert self.debug_dir is not None
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%f")
            session_dir = self.debug_dir / stamp
            session_dir.mkdir(parents=True, exist_ok=True)
            (session_dir / "window.wav").write_bytes(wav_path.read_bytes())
            (session_dir / "verify.json").write_text(
                json.dumps(
                    {
                        "requested": self._requested,
                        "results": [r.to_dict() for r in results],
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except OSError:
            _LOGGER.exception("Failed to write debug session")


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def _write_wav(path: Path, audio: bytes) -> None:
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(CHANNELS)
        wav_file.setsampwidth(SAMPLE_WIDTH)
        wav_file.setframerate(SAMPLE_RATE)
        wav_file.writeframes(audio)
