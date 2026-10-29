"""Transcription with Parakeet's TDT transducer head, for example sentences.

An intent may give example sentences instead of keywords. Those are matched
only when no keyword intent matched: the command is then transcribed by the
same model's transducer head, which writes far cleaner text than the CTC
head's greedy path, and the transcript is compared with the examples. The
CTC export has no transducer, so this is a second model (voice_labs's
``parakeet-tdt-int8``), decoded with sherpa-onnx (the ``tdt`` extra). A
command that matches on keywords never pays for it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from .acoustic import SAMPLE_RATE

DEFAULT_TDT_MODEL = Path(__file__).resolve().parent.parent / "models/parakeet-tdt-int8"


def model_file(model_dir: Path, part: str) -> Path | None:
    """``encoder.onnx``, or ``encoder.int8.onnx`` as sherpa-onnx's own
    releases name it; None when neither is there."""
    for name in (f"{part}.onnx", f"{part}.int8.onnx"):
        path = Path(model_dir) / name
        if path.is_file():
            return path
    return None


def has_model(model_dir: Path | None) -> bool:
    return model_dir is not None and all(model_file(model_dir, p) for p in ("encoder", "decoder", "joiner"))


class TdtTranscriber:
    """A NeMo TDT model exported for sherpa-onnx: ``encoder.onnx``,
    ``decoder.onnx``, ``joiner.onnx`` (or their ``.int8.onnx``) and
    ``tokens.txt``."""

    def __init__(self, model_dir: Path, num_threads: int = 2) -> None:
        import sherpa_onnx  # pylint: disable=import-outside-toplevel

        model_dir = Path(model_dir)
        self.model_dir = model_dir.resolve()
        self._recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(model_file(model_dir, "encoder")),
            decoder=str(model_file(model_dir, "decoder")),
            joiner=str(model_file(model_dir, "joiner")),
            tokens=str(model_dir / "tokens.txt"),
            num_threads=num_threads,
            sample_rate=SAMPLE_RATE,
            feature_dim=80,
            decoding_method="greedy_search",
            model_type="nemo_transducer",
        )

    def transcribe(self, audio: np.ndarray) -> str:
        return self.transcribe_words(audio)[0]

    def transcribe_words(self, audio: np.ndarray) -> tuple[str, list[tuple[str, float, float]]]:
        """The transcript, and its words with start and end times in seconds.
        A word ends where the next one starts (or at its last token plus
        that token's duration)."""
        stream = self._recognizer.create_stream()
        stream.accept_waveform(SAMPLE_RATE, np.asarray(audio, dtype=np.float32))
        self._recognizer.decode_stream(stream)
        result = stream.result
        tokens = list(result.tokens)
        starts = list(result.timestamps)
        durations = list(getattr(result, "durations", None) or [0.08] * len(tokens))
        words: list[list] = []
        for token, start, duration in zip(tokens, starts, durations):
            if token.startswith(" ") or not words:
                words.append([token.strip(), start, start + duration])
            else:
                words[-1][0] += token
                words[-1][2] = start + duration
        return result.text.strip(), [(w, s, e) for w, s, e in words if w]


def load_transcriber(model_dir: Path, num_threads: int, prog: str) -> TdtTranscriber | None:
    """The transcriber, or None with a warning when it cannot be loaded."""
    if not has_model(Path(model_dir)):
        print(
            f"{prog}: warning: no TDT model at {model_dir}; example sentences "
            "will not be matched",
            file=sys.stderr,
        )
        return None
    try:
        return TdtTranscriber(model_dir, num_threads)
    except ImportError:
        print(
            f"{prog}: warning: example sentences need sherpa-onnx "
            "(pip install 'adapt-ctc[tdt]'); they will not be matched",
            file=sys.stderr,
        )
        return None
