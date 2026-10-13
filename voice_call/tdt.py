"""Second-pass transcription with Parakeet's TDT transducer head.

speech-to-phrase ships only the CTC head of parakeet-tdt_ctc-110m, and decodes
it greedily against a grammar. When no trained sentence matches, the greedy CTC
text is all we have to fall back on -- and at 80 ms frames it mixes subword
paths ("Pplayay", "cororporate"). This runs the same model's transducer head,
which is the decoder NVIDIA's published accuracy actually refers to.

It is a *second* pass: only used when the constrained decode fails, so the
accepted path never pays for it.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import numpy as np

from .protocol import SAMPLE_RATE

_LOGGER = logging.getLogger(__name__)


class TdtTranscriber:
    def __init__(self, model_dir: Path, num_threads: int = 2) -> None:
        self.model_dir = Path(model_dir)
        # Deliberately not all cores: this can run beside the CTC pass, and on
        # a 4-core Pi two greedy ORT pools fighting is slower than either alone.
        self.num_threads = num_threads
        self._recognizer = None

    @property
    def available(self) -> bool:
        return self._recognizer is not None

    async def start(self) -> None:
        encoder = self.model_dir / "encoder.onnx"
        if not encoder.exists():
            _LOGGER.warning(
                "No TDT model at %s; second-pass transcription disabled",
                self.model_dir,
            )
            return
        self._recognizer = await asyncio.to_thread(self._load)
        _LOGGER.info("TDT transducer ready (%d threads)", self.num_threads)

    def _load(self):
        import sherpa_onnx

        return sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(self.model_dir / "encoder.onnx"),
            decoder=str(self.model_dir / "decoder.onnx"),
            joiner=str(self.model_dir / "joiner.onnx"),
            tokens=str(self.model_dir / "tokens.txt"),
            num_threads=self.num_threads,
            sample_rate=SAMPLE_RATE,
            feature_dim=80,
            decoding_method="greedy_search",
            model_type="nemo_transducer",
        )

    async def transcribe(self, pcm: bytes) -> str:
        if self._recognizer is None:
            return ""
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        return await asyncio.to_thread(self._transcribe, samples)

    def _transcribe(self, samples: np.ndarray) -> str:
        assert self._recognizer is not None
        stream = self._recognizer.create_stream()
        stream.accept_waveform(SAMPLE_RATE, samples)
        self._recognizer.decode_stream(stream)
        return stream.result.text.strip()
