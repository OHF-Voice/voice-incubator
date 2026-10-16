"""Second-stage wake word verification via wyoming-wake-kaldi.

The on-device detector has already fired; this re-decodes the pre-roll window
against explicit wake/background/prefix/confusable grammars. It answers both
"was that really the wake word" and "where does the command start".

Every wake word in ``wake.yaml`` can be verified, but each costs resident
decoder processes, so a word's graphs and decoders are started the first time
a satellite listens for it (``ensure``), not all at startup. A wake word that
is not in ``wake.yaml`` -- a custom one -- has no verifier: ``verify`` returns
None and the detection stands.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
import wave
from pathlib import Path

from wyoming_wake_kaldi.config import load_config
from wyoming_wake_kaldi.service import WakeService
from wyoming_wake_kaldi.model import prepare_model
from wyoming_wake_kaldi.pool import maybe_start_decoder
from wyoming_wake_kaldi.train import train_wake_word
from wyoming_wake_kaldi.verify import Verifier, VerifyResult

from .protocol import BYTES_PER_SAMPLE, SAMPLE_RATE

_LOGGER = logging.getLogger(__name__)

DEFAULT_WAKE_WORD = "okay_nabu"


class WakeVerifier:
    def __init__(self, config_path: Path, wake_word: str = DEFAULT_WAKE_WORD) -> None:
        self.config_path = Path(config_path)
        self.wake_word = wake_word
        """Verified when ``verify`` is not told which word fired."""
        self.config = load_config(self.config_path)
        self._service: WakeService | None = None
        self._lock = asyncio.Lock()

    def knows(self, wake_word: str) -> bool:
        """Whether this wake word has a verifier entry."""
        return self.config.resolve(wake_word) is not None

    @property
    def started(self) -> list[str]:
        return sorted(self._service.verifiers) if self._service else []

    async def start(self, wake_words: tuple[str, ...] | None = None) -> None:
        """Check the tools and models, and start ``wake_words`` (default: the
        default wake word)."""
        daemon = self.config_path.parent / "models/wake-kaldi-decoderd/verify_decoderd"
        service = WakeService(self.config, daemon if daemon.exists() else None)
        service.tools.check()
        for model_id, model_config in self.config.models.items():
            service.models[model_id] = prepare_model(model_id, model_config.directory, self.config.train_dir)
        self._service = service
        if not self.knows(self.wake_word):
            raise RuntimeError(f"Wake word {self.wake_word!r} not in {self.config_path}")
        await self.ensure(wake_words if wake_words is not None else (self.wake_word,))

    async def ensure(self, wake_words) -> None:
        """Build the graphs and start the decoders of any of these not running."""
        assert self._service is not None
        service = self._service
        async with self._lock:
            for name in wake_words:
                config = self.config.resolve(name)
                if config is None or config.id in service.verifiers:
                    continue
                model = service.models[config.model]
                graph_dir = await train_wake_word(config, model, self.config.train_dir, service.tools)
                pool = None
                if self.config.resident_decoders:
                    pool = await maybe_start_decoder(
                        service.daemon_bin, model, graph_dir, service.tools, self.config.decoder
                    )
                    if pool is not None:
                        service._decoders.append(pool)  # pylint: disable=protected-access
                service.verifiers[config.id] = Verifier(
                    config, model, graph_dir, service.tools, pool, self.config.decoder
                )
                _LOGGER.info("Wake verifier ready (%s)", config.id)

    async def stop(self) -> None:
        if self._service is not None:
            await self._service.stop()
            self._service = None

    async def verify(self, pcm: bytes, wake_word: str | None = None) -> VerifyResult | None:
        """Verify a window of 16 kHz mono PCM ending at the detection. None:
        this wake word has no verifier."""
        assert self._service is not None
        config = self.config.resolve(wake_word or self.wake_word)
        if config is None:
            return None
        if config.id not in self._service.verifiers:
            await self.ensure([config.id])
        verifier = self._service.verifiers[config.id]

        # The decoder reads a file; keep it out of the project tree.
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as temp:
            with wave.open(temp.name, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(BYTES_PER_SAMPLE)
                wav.setframerate(SAMPLE_RATE)
                wav.writeframes(pcm)
            return await verifier.verify(Path(temp.name))
