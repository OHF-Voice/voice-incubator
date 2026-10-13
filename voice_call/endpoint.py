"""End-of-command detection with pysilero-vad."""

from __future__ import annotations

import logging

from pysilero_vad import SileroVoiceActivityDetector

from .protocol import BYTE_RATE, BYTES_PER_SAMPLE

_LOGGER = logging.getLogger(__name__)

# pysilero-vad consumes exactly 512 samples (32 ms) of 16 kHz 16-bit mono.
CHUNK_BYTES = 512 * BYTES_PER_SAMPLE
CHUNK_SECONDS = CHUNK_BYTES / BYTE_RATE


class Endpointer:
    """Marks the end of the command after enough trailing silence."""

    def __init__(
        self,
        threshold: float = 0.5,
        silence_seconds: float = 1.0,
        min_speech_seconds: float = 0.2,
    ) -> None:
        self._vad = SileroVoiceActivityDetector()
        self._threshold = threshold
        self._silence_seconds = silence_seconds
        self._min_speech_seconds = min_speech_seconds
        self._leftover = b""
        self.speech_seconds = 0.0
        self.silence_seconds = 0.0
        self.speech_started = False

    def process(self, pcm: bytes) -> bool:
        """Feed command audio; True once the command has ended."""
        self._leftover += pcm

        while len(self._leftover) >= CHUNK_BYTES:
            chunk = self._leftover[:CHUNK_BYTES]
            self._leftover = self._leftover[CHUNK_BYTES:]

            if self._vad.process_chunk(chunk) >= self._threshold:
                self.speech_seconds += CHUNK_SECONDS
                self.silence_seconds = 0.0
                if (
                    not self.speech_started
                    and self.speech_seconds >= self._min_speech_seconds
                ):
                    self.speech_started = True
                    _LOGGER.debug("Speech started")
            elif self.speech_started:
                self.silence_seconds += CHUNK_SECONDS
                if self.silence_seconds >= self._silence_seconds:
                    _LOGGER.info(
                        "Command ended (%.2fs speech, %.2fs silence)",
                        self.speech_seconds,
                        self.silence_seconds,
                    )
                    return True

        return False
