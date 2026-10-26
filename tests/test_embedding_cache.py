"""Document embeddings kept for the next start (engine.CachedEmbedder): made
once, read back by the same model, made anew by another."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from voice_call.engine import CachedEmbedder


class Counting:
    """An embedder that says what it was asked to embed."""

    def __init__(self, model_dir: Path) -> None:
        self.model_dir = model_dir
        self.asked: list[str] = []

    def embed(self, texts, document=False):
        self.asked.extend(texts)
        return np.array([[float(len(t)), 1.0] for t in texts], dtype=np.float32)


def model(path: Path, size: int) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "model.onnx").write_bytes(bytes(size))
    return path


def test_kept_for_the_next_start(tmp_path):
    saved = tmp_path / "data/embeddings.npz"
    first = Counting(model(tmp_path / "gemma", 10))
    cache = CachedEmbedder(first, saved)
    vectors = cache.embed(["turn on the lights", "what time is it"], document=True)
    cache.embed(["turn on the lights"], document=True)
    assert first.asked == ["turn on the lights", "what time is it"]  # once each
    cache.embed(["a query"])  # queries are not kept
    cache.save()
    assert saved.is_file()

    # The next start: the same model reads them back, and embeds only what is new.
    again = Counting(first.model_dir)
    cache = CachedEmbedder(again, saved)
    assert np.array_equal(cache.embed(["turn on the lights", "what time is it"], document=True), vectors)
    cache.embed(["play some jazz"], document=True)
    assert again.asked == ["play some jazz"]
    cache.save()

    # Sentences no build uses any more are left out.
    third = CachedEmbedder(Counting(first.model_dir), saved)
    third.embed(["play some jazz"], document=True)
    third.save()
    with np.load(saved) as kept:
        assert kept["texts"].tolist() == ["play some jazz"]

    # Another model (or another export of it) embeds anew.
    other = Counting(model(tmp_path / "gemma", 11))
    CachedEmbedder(other, saved).embed(["play some jazz"], document=True)
    assert other.asked == ["play some jazz"]


def test_an_unreadable_file_is_embedded_anew(tmp_path):
    saved = tmp_path / "embeddings.npz"
    saved.write_bytes(b"not numpy")
    embedder = Counting(model(tmp_path / "m", 5))
    cache = CachedEmbedder(embedder, saved)
    cache.embed(["hello"], document=True)
    assert embedder.asked == ["hello"]
    cache.save()  # replaces it
    assert CachedEmbedder(Counting(embedder.model_dir), saved).embed(["hello"], document=True).shape == (1, 2)
