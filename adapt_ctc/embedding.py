"""Sentence embeddings, for example sentences said in other words.

When neither the CTC evidence nor the TDT transcript matched an example
sentence word for word, the transcripts are embedded with EmbeddingGemma 2
(the text model of onnx-community's ONNX export, 4-bit) and compared with the
examples by cosine similarity. "Do I need a membrella tomorrow?" is then
close to the example "will I need an umbrella tomorrow?".

Similarities alone don't separate intents: every request to a voice
assistant is fairly close to every other. What does is the margin over the
next candidate. The candidates are the other intents' examples, and a
built-in list of everyday requests (``data/distractors.<language>.txt``) that
stand for "none of the examples", so that a config with a single example
intent still has something to beat. Requires the ``embedding`` extra
(``tokenizers``).
"""

from __future__ import annotations

import sys
from importlib import resources
from pathlib import Path

import numpy as np

from .runtime import session_options

_HUB = Path.home() / ".cache/huggingface/hub/models--onnx-community--embeddinggemma-2-ONNX/snapshots"


def _default_model() -> Path:
    snapshots = sorted(_HUB.glob("*/onnx/model_q4.onnx"))
    return snapshots[-1].parent.parent if snapshots else _HUB


DEFAULT_EMBEDDING_MODEL = _default_model()

# EmbeddingGemma's task prefixes: transcripts are queries, examples documents.
QUERY_PROMPT = "task: search result | query: "
DOCUMENT_PROMPT = "title: none | text: "


class Embedder:
    """EmbeddingGemma 2's text model: ``onnx/model_q4.onnx`` and
    ``tokenizer.json`` in a snapshot of onnx-community/embeddinggemma-2-ONNX."""

    def __init__(self, model_dir: Path, num_threads: int = 2) -> None:
        import onnxruntime as ort  # pylint: disable=import-outside-toplevel
        from tokenizers import Tokenizer  # pylint: disable=import-outside-toplevel

        model_dir = Path(model_dir)
        self.model_dir = model_dir.resolve()
        self._tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        self._session = ort.InferenceSession(
            str(model_dir / "onnx" / "model_q4.onnx"),
            sess_options=session_options(num_threads),
            providers=["CPUExecutionProvider"],
        )
        # The export is multimodal; text takes no image, video or audio.
        self._no_media = {
            i.name: np.zeros((0, i.shape[-1]), dtype=np.float32)
            for i in self._session.get_inputs()
            if i.name not in ("input_ids", "attention_mask")
        }

    def embed(self, texts: list[str], document: bool = False) -> np.ndarray:
        """Unit vectors ``[len(texts), 768]``."""
        if not texts:
            return np.zeros((0, 768), dtype=np.float32)
        prompt = DOCUMENT_PROMPT if document else QUERY_PROMPT
        encoded = self._tokenizer.encode_batch([prompt + text for text in texts])
        length = max(len(e.ids) for e in encoded)
        ids = np.zeros((len(encoded), length), dtype=np.int64)
        mask = np.zeros_like(ids)
        for row, e in enumerate(encoded):
            ids[row, : len(e.ids)] = e.ids
            mask[row, : len(e.ids)] = 1
        vectors = self._session.run(
            ["sentence_embedding"], {"input_ids": ids, "attention_mask": mask, **self._no_media}
        )[0]
        return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)


def distractors(language: str) -> list[str]:
    """The built-in everyday requests for a language; none when there is no list."""
    try:
        text = resources.files("adapt_ctc").joinpath(f"data/distractors.{language}.txt").read_text("utf-8")
    except FileNotFoundError:
        return []
    return [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]


def load_embedder(model_dir: Path, num_threads: int, prog: str) -> Embedder | None:
    """The embedder, or None with a warning when it cannot be loaded."""
    if not (Path(model_dir) / "onnx" / "model_q4.onnx").is_file():
        print(
            f"{prog}: warning: no embedding model at {model_dir}; example sentences "
            "said in other words will not be matched",
            file=sys.stderr,
        )
        return None
    try:
        return Embedder(model_dir, num_threads)
    except ImportError:
        print(
            f"{prog}: warning: matching examples by embedding needs tokenizers "
            "(pip install 'adapt-ctc[embedding]')",
            file=sys.stderr,
        )
        return None
