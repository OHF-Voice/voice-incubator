"""Second-stage wake word verification via wyoming-wake-kaldi.

The on-device detector has already fired; this re-decodes the pre-roll window
against explicit wake/background/prefix/confusable grammars. It answers both
"was that really the wake word" and "where does the command start".
"""

from __future__ import annotations

import logging
import tempfile
import wave
from pathlib import Path

from wyoming_wake_kaldi.config import load_config
from wyoming_wake_kaldi.service import WakeService
from wyoming_wake_kaldi.verify import VerifyResult

from .protocol import BYTES_PER_SAMPLE, SAMPLE_RATE

_LOGGER = logging.getLogger(__name__)


class WakeVerifier:
    def __init__(self, config_path: Path, wake_word: str = "okay_nabu") -> None:
        self.config_path = Path(config_path)
        self.wake_word = wake_word
        self._service: WakeService | None = None

    async def start(self) -> None:
        config = load_config(self.config_path)
        daemon = self.config_path.parent / "models/wake-kaldi-decoderd/verify_decoderd"
        self._service = WakeService(config, daemon if daemon.exists() else None)
        await self._service.start()
        if self._service.resolve(self.wake_word) is None:
            raise RuntimeError(f"Wake word {self.wake_word!r} not in {self.config_path}")
        _LOGGER.info("Wake verifier ready (%s)", self.wake_word)

    async def stop(self) -> None:
        if self._service is not None:
            await self._service.stop()
            self._service = None

    async def verify(self, pcm: bytes) -> VerifyResult:
        """Verify a window of 16 kHz mono PCM ending at the detection."""
        assert self._service is not None
        wake_word = self._service.resolve(self.wake_word)
        assert wake_word is not None
        verifier = self._service.verifiers[wake_word.id]

        # The decoder reads a file; keep it out of the project tree.
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as temp:
            with wave.open(temp.name, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(BYTES_PER_SAMPLE)
                wav.setframerate(SAMPLE_RATE)
                wav.writeframes(pcm)
            return await verifier.verify(Path(temp.name))
