"""Constrained recognition of the voice command via speech-to-phrase.

The grammar is built from Home Assistant's sentence triggers and nothing else,
so an unrecognized command is rejected rather than guessed at.
"""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from speech_to_phrase import Recognizer, Result, load_recognizer
from speech_to_phrase.audio import load_audio
from speech_to_phrase.defaults import default_max_score

_LOGGER = logging.getLogger(__name__)


@dataclass
class Recognition:
    text: str
    score: float
    margin: float
    accepted: bool
    greedy_text: str = ""
    """Unconstrained Parakeet transcript of the same audio, from the same
    forward pass as the grammar decode."""


class CommandRecognizer:
    def __init__(
        self,
        model_dir: Path,
        language: str = "en",
        backend: str = "nemo",
        max_score: float | None = None,
        greedy_on_reject: bool = True,
    ) -> None:
        self.model_dir = Path(model_dir)
        self.language = language
        self.backend = backend
        # Per-token acoustic penalty above which the audio did not really match
        # any trained sentence. Calibrated per backend by speech-to-phrase.
        self.max_score = (
            default_max_score(backend) if max_score is None else max_score
        )
        # What the model heard without the grammar, for the rejection path only.
        self.greedy_on_reject = greedy_on_reject
        self._recognizer: Recognizer | None = None
        self.sentences: list[str] = []
        # train() rebuilds the grammar in place, so a retrain must not overlap a
        # decode. Retraining takes about a second; a call in flight waits.
        self._lock = asyncio.Lock()

    async def start(self, sentences: list[str]) -> None:
        self._recognizer = await asyncio.to_thread(
            load_recognizer, self.backend, self.model_dir, self.language
        )
        await self.train(sentences)

    async def train(self, sentences: list[str]) -> None:
        """Build the grammar. An empty list leaves the model loaded but
        ungrammared, which is valid when every trigger is a wildcard --
        greedy_transcribe() still works, recognize() does not."""
        assert self._recognizer is not None
        async with self._lock:
            self.sentences = list(sentences)
            if not sentences:
                _LOGGER.info("No plain sentences; grammar decoding disabled")
                return
            await asyncio.to_thread(self._recognizer.train, sentences)
        _LOGGER.info(
            "Recognizer trained on %d sentence(s), max_score %.1f",
            len(sentences),
            self.max_score,
        )

    async def recognize(self, pcm: bytes) -> Recognition:
        assert self._recognizer is not None
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        async with self._lock:
            result: Result = await asyncio.to_thread(
                self._recognizer.transcribe, audio
            )
        accepted = bool(result.text) and math.isfinite(result.score) and (
            result.score <= self.max_score
        )
        # Free since speech-to-phrase returns it from the same forward pass.
        greedy_text = result.greedy_text if self.greedy_on_reject else ""

        _LOGGER.info(
            "Recognized %r (score %.3f, margin %.3f) -> %s%s",
            result.text,
            result.score,
            result.margin,
            "accepted" if accepted else "rejected",
            f"; heard {greedy_text!r}" if greedy_text else "",
        )
        return Recognition(
            result.text, result.score, result.margin, accepted, greedy_text
        )

    async def log_probs(self, pcm: bytes):
        """Per-frame CTC log-probabilities, for decoding against other grammars."""
        assert self._recognizer is not None
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        return await asyncio.to_thread(self._recognizer.model.log_probs, audio)

    @property
    def tokenizer(self):
        assert self._recognizer is not None
        return self._recognizer.model.tokenizer

    @property
    def model(self):
        """The loaded acoustic model, for anything that wants to decode against
        a grammar of its own rather than the command grammar."""
        assert self._recognizer is not None
        return self._recognizer.model

    async def greedy_transcribe(self, audio: bytes | np.ndarray) -> str:
        """Decode without the grammar: what Parakeet heard, in its own words.

        Only needed when there is no grammar to decode against; otherwise
        recognize() already returns this from its own forward pass.
        """
        assert self._recognizer is not None
        if isinstance(audio, bytes):
            audio = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0
        return await asyncio.to_thread(self._greedy, audio)

    def _greedy(self, audio: np.ndarray) -> str:
        assert self._recognizer is not None
        model = self._recognizer.model
        log_probs = model.log_probs(load_audio(audio))

        # Standard CTC collapse: drop blanks and runs of the same token.
        blank_id = model.tokenizer.blank_id
        ids: list[int] = []
        previous: int | None = None
        for token_id in log_probs.argmax(axis=-1):
            current = int(token_id)
            if current != previous and current != blank_id:
                ids.append(current)
            previous = current
        return model.tokenizer.ids_to_text(ids)
