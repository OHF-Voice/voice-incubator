"""Shared, process-wide verification state.

Graphs are built (or validated against their cache) once at startup, so the
first client does not pay for it, and the resident decoders -- when the
daemon is available -- stay warm for the life of the process.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from .config import ServiceConfig, WakeWordConfig
from .kaldi import SpeechTools
from .model import PreparedModel, prepare_model
from .pool import ResidentDecoder, maybe_start_decoder
from .train import train_wake_word
from .verify import VerifyResult, Verifier

_LOGGER = logging.getLogger(__name__)


class WakeService:
    """Holds one Verifier per configured wake word."""

    def __init__(
        self, config: ServiceConfig, daemon_bin: Path | None = None
    ) -> None:
        self.config = config
        self.daemon_bin = daemon_bin
        self.tools = SpeechTools.from_dir(config.tools_dir)
        self.models: dict[str, PreparedModel] = {}
        self.verifiers: dict[str, Verifier] = {}
        self._decoders: list[ResidentDecoder] = []

    async def start(self) -> None:
        self.tools.check()

        for model_id, model_config in self.config.models.items():
            self.models[model_id] = prepare_model(
                model_id, model_config.directory, self.config.train_dir
            )

        for wake_id, wake_word in self.config.wake_words.items():
            model = self.models[wake_word.model]
            graph_dir = await train_wake_word(
                wake_word, model, self.config.train_dir, self.tools
            )
            pool = None
            if self.config.resident_decoders:
                pool = await maybe_start_decoder(
                    self.daemon_bin,
                    model,
                    graph_dir,
                    self.tools,
                    self.config.decoder,
                )
                if pool is not None:
                    self._decoders.append(pool)
            self.verifiers[wake_id] = Verifier(
                wake_word, model, graph_dir, self.tools, pool, self.config.decoder
            )

        _LOGGER.info(
            "Ready: %s", ", ".join(sorted(self.config.wake_words)) or "(no wake words)"
        )

    async def stop(self) -> None:
        await asyncio.gather(
            *(decoder.stop() for decoder in self._decoders), return_exceptions=True
        )
        self._decoders = []

    def resolve(self, name: str) -> WakeWordConfig | None:
        return self.config.resolve(name)

    async def verify(self, wake_id: str, wav_path: Path) -> VerifyResult:
        return await self.verifiers[wake_id].verify(wav_path)
