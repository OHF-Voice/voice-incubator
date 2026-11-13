"""Document embeddings kept for the next start (engine.CachedEmbedder): made
once, read back by the same model, made anew by another."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from voice_incubator.engine import CachedEmbedder


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


def test_embedded_ahead_is_kept_until_the_library_changes(tmp_path):
    saved = tmp_path / "embeddings.npz"
    first = Counting(model(tmp_path / "gemma", 10))
    cache = CachedEmbedder(first, saved)
    cache.embed(["turn on the lights"], document=True)  # a build
    cache.keep_ahead(["what time is it", "set a timer", "turn on the lights"])
    assert cache.missing(["what time is it", "set a timer", "turn on the lights"]) == ["what time is it", "set a timer"]
    cache.embed_ahead(["what time is it", "set a timer"])
    cache.save()

    # The next start: a build that uses none of them keeps them, unasked.
    again = Counting(first.model_dir)
    cache = CachedEmbedder(again, saved)
    cache.embed(["play some jazz"], document=True)
    cache.save()
    with np.load(saved) as kept:
        assert set(kept["texts"].tolist()) == {"turn on the lights", "what time is it", "set a timer", "play some jazz"}
    assert cache.missing(["set a timer", "what time is it"]) == []
    assert again.asked == ["play some jazz"]

    # The library says other things now: those it no longer says go.
    cache.keep_ahead(["set a timer"])
    cache.save()
    with np.load(saved) as kept:
        assert set(kept["texts"].tolist()) == {"set a timer", "play some jazz"}


def test_a_library_add_embeds_nothing_new():
    """Each library version's sentences are what a build of it embeds --
    as the recognizer gathers them (_embed_examples) -- with several added
    together, as Add N commands does."""
    from types import SimpleNamespace

    from adapt_ctc.config import parse_config
    from adapt_ctc.matcher import Recognizer

    from voice_incubator.engine import embedded_texts, library_texts
    from voice_incubator.library import REFERENCE, load_library
    from voice_incubator.store import compile_store

    ahead = set(library_texts())
    entries = [e for e in load_library() if e["file"] in ("lights", "timers", "weather")]
    assert entries
    lists = {name: values for e in entries for name, values in e["lists"].items()}
    document = {"language": "en", "lists": lists, "commands": {e["id"]: {REFERENCE: f"{e['file']}/{e['id']}"} for e in entries}}
    config = parse_config(compile_store(document).config)

    texts = embedded_texts(config)
    assert set(texts) <= ahead

    # The recognizer embeds just those (then the everyday requests).
    asked = []
    stub = SimpleNamespace(config=config, meaning_texts={}, embedder=SimpleNamespace(
        embed=lambda t, document=False: (asked.append(list(t)), np.ones((len(t), 2), dtype=np.float32))[1]))
    Recognizer._embed_examples(stub)
    assert asked[0] == texts
