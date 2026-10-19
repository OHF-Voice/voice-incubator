"""The torch-free GLiNER port (voice_call/gliner.py) against reference data
from the gliner2 package (tests/fixtures/gliner_*, made by its export's
check): the prompt, the candidate pool, overlap resolution -- and, when the
exported model is in models/, the whole thing."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from voice_call.gliner import choose_candidates, normalize_text, resolve_flat, split_words

FIXTURES = Path(__file__).parent / "fixtures"
MODEL = Path(__file__).parent.parent / "models/gliner2.5-small-v1-onnx"
needs_model = pytest.mark.skipif(not (MODEL / "gliner2_onnx.json").is_file(), reason="no exported GLiNER model")


def test_words():
    assert split_words("I want to hear Radiohead, ok?") == [
        ("i", 0, 1), ("want", 2, 6), ("to", 7, 9), ("hear", 10, 14), ("radiohead", 15, 24), (",", 24, 25),
        ("ok", 26, 28), ("?", 28, 29),
    ]
    assert [w for w, _, _ in split_words("e-mail me@example.com, café")] == ["e-mail", "me@example.com", ",", "café"]
    assert normalize_text("we need milk") == "we need milk." and normalize_text("why?") == "why?"


def test_candidate_pools_as_gliner2_chooses_them():
    data = np.load(FIXTURES / "gliner_pools.npz")
    top_k, size, min_per_query = (int(v) for v in data["settings"])
    for case in range(4):
        def get(name, case=case):
            return data[f"{case}_{name}"]

        indices, mask, compat = choose_candidates(
            get("boundary_mask"), get("query_mask"), get("start_logits"), get("end_logits"),
            get("start_all"), get("end_all"), top_k, size, min_per_query,
        )
        assert np.array_equal(mask, get("mask")), case
        assert np.array_equal(indices[mask], get("indices")[mask]), case
        assert np.allclose(compat[mask], get("compat")[mask], atol=1e-5), case


def test_flat_overlap_resolution():
    # The set of spans that do not overlap with the most score in all, best first.
    assert resolve_flat([(0.9, 0, 3), (0.3, 0, 1), (0.4, 1, 3)]) == [(0.9, 0, 3)]
    assert resolve_flat([(0.9, 0, 3), (0.6, 0, 1), (0.5, 1, 3)]) == [(0.6, 0, 1), (0.5, 1, 3)]  # 1.1 > 0.9
    assert resolve_flat([(0.5, 0, 2), (0.25, 2, 4), (0.8, 1, 3)]) == [(0.8, 1, 3)]  # 0.75 < 0.8
    assert resolve_flat([(0.5, 0, 2), (0.45, 2, 4), (0.6, 1, 3)]) == [(0.5, 0, 2), (0.45, 2, 4)]
    assert resolve_flat([(0.7, 0, 2), (0.7, 0, 2)]) == [(0.7, 0, 2)]  # duplicates once
    assert resolve_flat([]) == []


@needs_model
def test_prompts_as_gliner2_builds_them():
    from voice_call.gliner import GlinerOnnx  # pylint: disable=import-outside-toplevel

    model = GlinerOnnx(MODEL, threads=2)
    for case in json.loads((FIXTURES / "gliner_prompts.json").read_text()):
        built = model.build(case["text"], case["entities"], ("command", case["labels"]))
        assert built["ids"][0].tolist() == case["ids"], case["text"]
        assert built["word_positions"] == case["word_positions"], case["text"]
        assert built["markers"].get("entities", []) == case["entity_markers"], case["text"]
        assert built["markers"]["classification"] == case["label_markers"], case["text"]


@needs_model
def test_extraction():
    from voice_call.gliner import GlinerOnnx  # pylint: disable=import-outside-toplevel

    model = GlinerOnnx(MODEL, threads=2)
    labels = ["play an artist, album, song, playlist or genre", "set a timer", "something else: a question, small talk or thanks"]
    types = {"music": "an artist, album, song, playlist or genre to play", "duration": "a length of time"}
    out = model.extract("play some jazz for ten minutes", types, ("command", labels))
    assert [e[0] for e in out["entities"]["music"]] == ["jazz"]
    assert [e[0] for e in out["entities"]["duration"]] == ["ten minutes"]
    out = model.extract("I'm in the mood for jazz", types, ("command", labels))
    assert max(out["classification"], key=out["classification"].get) == labels[0]
    # Single-label: probabilities that sum to one.
    single = model.extract("thank you", None, ("command", labels), multi_label=False)
    assert abs(sum(single["classification"].values()) - 1.0) < 1e-5


@needs_model
def test_spans_are_not_clipped():
    # As a call gave it (commands closest in meaning, their entity types):
    # with 4-bit layers the small model added "add apples".
    from voice_call.gliner import GlinerOnnx  # pylint: disable=import-outside-toplevel

    model = GlinerOnnx(MODEL, threads=2)
    labels = [
        "add something to the shopping list", "skip to the next song", "never mind, cancel the request",
        "pause the music", "resume the music", "tell the date", "play an artist, album, song, playlist or genre",
        "move the music to another speaker", "something else: a question, small talk or thanks",
    ]
    types = {
        "item": "something to buy, or a task to do, as said",
        "music": "an artist, album, song, playlist or genre to play",
        "device": "a device, appliance or other thing in the home, like the coffee maker, the bedroom fan or the front door",
    }
    out = model.extract("For the shopping list, add apples please.", types, ("command", labels))
    assert [e[0] for e in out["entities"]["item"]] == ["apples"]
