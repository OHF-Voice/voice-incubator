#!/usr/bin/env python3
"""Export a GLiNER2.5 checkpoint for voice_call/gliner.py: ONNX networks, an
8-bit encoder, the classifier's weights and the tokenizer, into
models/<name>-onnx -- then check the result against the gliner2 package.

Needs what the app does not: torch, gliner2 and onnxscript. In a venv of its
own (Python 3.10-3.12; torch from download.pytorch.org/whl/cpu):

    pip install torch --index-url https://download.pytorch.org/whl/cpu
    pip install "gliner2[local]" onnxscript onnx onnxruntime tokenizers
    python scripts/export_gliner.py fastino/gliner2.5-small-v1

What is exported, and what voice_call/gliner.py does instead:

- ``encoder.onnx``: the DeBERTa encoder, input ids -> token states; then
  ``encoder-w8.onnx``, what the app runs: its layers' weights in 8 bits and
  its embedding in 4 (onnxruntime's MatMulNBits, block 32; activations stay
  float, and the embedding can only be 4). With 4-bit layers the small model
  clips spans worse: "add apples" was the item in "for the shopping list,
  add apples please". int8 (dynamic, activations quantized) is not made: it
  breaks DeBERTa-v3 -- no command right on the fallback benchmark.
- ``boundary_a.onnx``: word and entity-type states -> boundary states, start
  and end logits, inside evidence, the candidate pool's projections, null and
  count logits.
- The candidate pool -- top boundaries, pairing, a quota per entity type,
  de-duplication -- branches on sizes and sorts stably, which ONNX cannot
  express: voice_call/gliner.py chooses it, in numpy.
- ``boundary_b.onnx``: the chosen candidates -> their scores for every
  entity type.
- ``classifier.npz``: the classification head, a two-layer MLP.

Two stand-ins make the export exact and dynamic, each checked against what
it replaces: stable sorts become comparisons (ONNX has no stable sort), and
the boundary encoder's EOS goes last (it is written at each row's length,
which pins the width; one unpadded sentence's length is its width).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parent.parent
ENCODER = "encoder-w8.onnx"


class Encoder(torch.nn.Module):
    def __init__(self, encoder):
        super().__init__()
        self.encoder = encoder

    def forward(self, input_ids, attention_mask):
        return self.encoder(input_ids=input_ids, attention_mask=attention_mask).last_hidden_state


class BoundaryA(torch.nn.Module):
    """Words and entity types -> what the candidate pool is chosen from."""

    def __init__(self, head):
        super().__init__()
        self.head = head

    def forward(self, text_states, text_mask, query_states, query_mask):
        head = self.head
        encoding = head.boundary_encoder(text_states, text_mask)
        m = head.boundary_query_head(encoding.states, encoding.mask, text_states, text_mask, query_states, query_mask)
        pool = head.shared_pool_builder
        return (
            encoding.states, encoding.mask, m.start_logits, m.end_logits, m.inside_prefix, m.inside_prefix_mean,
            pool.start_projection(encoding.states), pool.end_projection(encoding.states),
            head.null_projection(query_states).squeeze(-1), head.count_head(query_states).squeeze(-1),
        )


class BoundaryB(torch.nn.Module):
    """The chosen candidates, scored for every entity type: [1, C, Q]."""

    def __init__(self, head):
        super().__init__()
        self.head = head

    def forward(self, boundary_states, query_states, query_mask, indices, candidate_mask, compat,
                start_logits, end_logits, inside_prefix, inside_prefix_mean, text_states, text_mask):
        from gliner2.models.boundary.pool import PooledCandidates  # pylint: disable=import-outside-toplevel

        pooled = PooledCandidates(
            indices=indices, mask=candidate_mask, proposal_logits=None, gold_mask=None, compat_logits=compat
        )
        score, _ = self.head.shared_pool_scorer(
            boundary_states, query_states, query_mask, pooled, start_logits, end_logits, inside_prefix,
            text_mask.sum(1), text_states, text_mask, inside_prefix_mean=inside_prefix_mean,
        )
        return score


def stable_argsort(x, dim=-1, descending=False, stable=True):
    """torch.argsort(stable=True) from comparisons: an element's place is how
    many come before it, by value then by index. O(n^2); n is at most ~1000."""
    assert dim in (-1, x.dim() - 1), dim
    n = x.shape[-1]
    idx = torch.arange(n, device=x.device)
    xi, xj = x.unsqueeze(-1), x.unsqueeze(-2)
    earlier = idx.unsqueeze(0) < idx.unsqueeze(1)
    beats = (xj > xi) if descending else (xj < xi)
    rank = (beats | ((xj == xi) & earlier)).sum(-1)
    return torch.zeros_like(rank).scatter(-1, rank, idx.expand_as(rank).contiguous())


def stable_sort(x, dim=-1, descending=False, stable=True):
    order = stable_argsort(x, dim, descending)
    return x.gather(-1, order), order


def shift_right_with_eos(text_states, text_lengths, eos_state):
    b, _, h = text_states.shape
    return torch.cat((text_states, eos_state.to(text_states.dtype).view(1, 1, h).expand(b, 1, h)), dim=1)


def shift_left_with_bos(text_states, bos_state):
    b, _, h = text_states.shape
    return torch.cat((bos_state.to(text_states.dtype).view(1, 1, h).expand(b, 1, h), text_states), dim=1)


def install_stand_ins() -> None:
    from gliner2.models.boundary import encoding  # pylint: disable=import-outside-toplevel

    x = torch.tensor([[3.0, 1.0, 3.0, -5.0, 1.0, 7.0]])
    for descending in (False, True):
        assert torch.equal(stable_argsort(x, descending=descending), torch.argsort(x, descending=descending, stable=True))
    keys = torch.randint(0, 5, (2, 50))
    assert torch.equal(stable_argsort(keys), torch.argsort(keys, stable=True))
    states, lengths, eos = torch.randn(1, 7, 5), torch.tensor([7]), torch.randn(5)
    assert torch.equal(shift_right_with_eos(states, lengths, eos), encoding.shift_right_with_eos(states, lengths, eos))
    assert torch.equal(shift_left_with_bos(states, eos), encoding.shift_left_with_bos(states, eos))
    torch.sort, torch.argsort = stable_sort, stable_argsort
    encoding.shift_right_with_eos, encoding.shift_left_with_bos = shift_right_with_eos, shift_left_with_bos


def export(model, out: Path) -> None:
    h = model.hidden_size
    ids = torch.randint(5, 1000, (1, 37))
    batch, seq = torch.export.Dim("batch", min=1, max=16), torch.export.Dim("seq", min=2, max=1024)
    torch.onnx.export(
        Encoder(model.encoder), (ids, torch.ones_like(ids)), out / "encoder.onnx", dynamo=True,
        input_names=["input_ids", "attention_mask"], output_names=["hidden"],
        dynamic_shapes=({0: batch, 1: seq}, {0: batch, 1: seq}), external_data=False,
    )
    head = model.boundary_head
    words, queries = torch.export.Dim("words", min=2, max=512), torch.export.Dim("queries", min=1, max=64)
    args = (torch.randn(1, 9, h), torch.ones(1, 9, dtype=torch.bool), torch.randn(1, 3, h), torch.ones(1, 3, dtype=torch.bool))
    torch.onnx.export(
        BoundaryA(head), args, out / "boundary_a.onnx", dynamo=True,
        input_names=["text_states", "text_mask", "query_states", "query_mask"],
        output_names=["boundary_states", "boundary_mask", "start_logits", "end_logits", "inside_prefix",
                      "inside_prefix_mean", "start_all", "end_all", "null_logits", "count_log_rates"],
        dynamic_shapes=({1: words}, {1: words}, {1: queries}, {1: queries}), external_data=False,
    )
    with torch.no_grad():
        a = BoundaryA(head)(*args)
    c = 192
    b_args = (a[0], args[2], args[3], torch.randint(0, 9, (1, c, 2)), torch.ones(1, c, dtype=torch.bool), torch.randn(1, c),
              a[2], a[3], a[4], a[5], args[0], args[1])
    boundaries = torch.export.Dim("boundaries", min=3, max=513)
    candidates = torch.export.Dim("candidates", min=1, max=4096)
    torch.onnx.export(
        BoundaryB(head), b_args, out / "boundary_b.onnx", dynamo=True,
        input_names=["boundary_states", "query_states", "query_mask", "indices", "candidate_mask", "compat",
                     "start_logits", "end_logits", "inside_prefix", "inside_prefix_mean", "text_states", "text_mask"],
        output_names=["scores"],
        dynamic_shapes=({1: boundaries}, {1: queries}, {1: queries}, {1: candidates}, {1: candidates}, {1: candidates},
                        {1: queries, 2: boundaries}, {1: queries, 2: boundaries}, {1: queries, 2: boundaries},
                        {1: queries}, {1: words}, {1: words}),
        external_data=False,
    )
    layers = [m for m in model.classifier if isinstance(m, torch.nn.Linear)]
    assert len(layers) == 2 and isinstance(model.classifier[1], torch.nn.ReLU), model.classifier
    np.savez(out / "classifier.npz", **{f"w{i}": l.weight.detach().numpy() for i, l in enumerate(layers)},
             **{f"b{i}": l.bias.detach().numpy() for i, l in enumerate(layers)})
    model.processor.tokenizer.save_pretrained(out)


def quantize(out: Path) -> None:
    """``ENCODER``: the layers' weights in 8 bits, then the embedding's in 4."""
    import onnx  # pylint: disable=import-outside-toplevel
    from onnxruntime.quantization.matmul_nbits_quantizer import (  # pylint: disable=import-outside-toplevel
        MatMulNBitsQuantizer,
    )

    logging.getLogger("onnxruntime.quantization.matmul_nbits_quantizer").setLevel(logging.WARNING)
    model = onnx.load(str(out / "encoder.onnx"))
    for bits, ops in ((8, ("MatMul",)), (4, ("Gather",))):
        quantizer = MatMulNBitsQuantizer(model, bits=bits, block_size=32, is_symmetric=True, op_types_to_quantize=ops)
        quantizer.process()
        model = quantizer.model.model
    onnx.save(model, str(out / ENCODER))


def check(model, out: Path, encoder: str) -> None:
    """The port against gliner2: identical prompts and marker positions;
    with the float encoder, identical entities and probabilities."""
    sys.path.insert(0, str(ROOT))
    from voice_call.gliner import GlinerOnnx  # pylint: disable=import-outside-toplevel

    port = GlinerOnnx(out, threads=4, encoder=encoder)
    captured = {}
    original = model._extract_from_batch  # pylint: disable=protected-access

    def spy(batch, *args, **kwargs):
        captured["batch"] = batch
        return original(batch, *args, **kwargs)

    model._extract_from_batch = spy  # pylint: disable=protected-access
    labels = ["play an artist, album, song, playlist or genre", "add something to the shopping list", "set a timer",
              "something else: a question, small talk or thanks"]
    types = {"music": "an artist, album, song, playlist or genre to play", "item": "something to buy",
             "duration": "a length of time, like ten minutes"}
    worst, differ = 0.0, []
    for text in ("I want to hear Radiohead", "we need milk and eggs", "give me ten minutes", "thank you",
                 "put on some jazz in the kitchen, please!", "add eggs, milk and bread to the shopping list"):
        schema = model.create_schema().classification("command", labels, multi_label=True, cls_threshold=0.0)
        with torch.inference_mode():
            reference = model.extract(text, schema.entities(types), include_confidence=True, include_spans=True)
        b = captured["batch"]
        built = port.build(text, types, ("command", labels))
        assert built["ids"][0].tolist() == b.input_ids[0].tolist(), text
        assert built["word_positions"] == b.text_word_indices[0][b.text_word_mask[0]].tolist(), text
        assert built["markers"]["entities"] == b.query_marker_indices[0][b.query_marker_mask[0]].tolist(), text
        assert built["markers"]["classification"] == b.cls_marker_indices[0][b.cls_marker_mask[0]].tolist(), text
        got = port.extract(text, types, ("command", labels))
        expected = {x["label"]: x["confidence"] for x in reference["command"]}
        worst = max(worst, max(abs(expected[label] - got["classification"][label]) for label in labels))
        for name in types:
            r = sorted((e["text"], e["start"], e["end"]) for e in reference["entities"][name])
            g = sorted((e[0], e[2], e[3]) for e in got["entities"][name])
            if r != g:
                differ.append(f"{text!r} {name}: gliner2 {r}, port {g}")
    print(f"{encoder}: prompts identical; classification within {worst:.1e}; entities differ in {len(differ)}")
    for line in differ:
        print("  " + line)
    if encoder == "encoder.onnx":
        assert worst < 1e-4 and not differ, "the export does not match gliner2"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("checkpoint", help="e.g. fastino/gliner2.5-small-v1")
    parser.add_argument("--out", type=Path, help="default: models/<name>-onnx")
    args = parser.parse_args()
    from gliner2 import AutoExtractor  # pylint: disable=import-outside-toplevel

    out = args.out or ROOT / "models" / f"{args.checkpoint.split('/')[-1]}-onnx"
    out.mkdir(parents=True, exist_ok=True)
    model = AutoExtractor.from_pretrained(args.checkpoint).eval()
    install_stand_ins()
    export(model, out)
    quantize(out)
    pool = model.boundary_head.shared_pool_builder
    bs = model.boundary_settings
    tokenizer = model.processor.tokenizer
    settings = {
        "model": args.checkpoint,
        "encoder": ENCODER,
        "source": f"https://huggingface.co/{args.checkpoint} (Apache-2.0), exported by scripts/export_gliner.py, "
        "encoder 8-bit, embedding 4-bit (MatMulNBits, block 32)",
        **{key: getattr(bs, key) for key in ("pair_temperature", "classification_temperature", "abstention_threshold",
                                             "overlap_policy", "adaptive_threshold")},
        "pool": {"top_k": pool.pool_boundary_top_k, "size": pool.pool_size, "min_per_query": pool.min_pool_per_query},
        "special_tokens": {t: tokenizer.convert_tokens_to_ids(t) for t in (
            "[SEP_STRUCT]", "[SEP_TEXT]", "[P]", "[C]", "[E]", "[R]", "[L]", "[EXAMPLE]", "[OUTPUT]", "[DESCRIPTION]")},
    }
    assert not settings["adaptive_threshold"] and settings["overlap_policy"] == "flat", settings
    (out / "gliner2_onnx.json").write_text(json.dumps(settings, indent=1))
    for encoder in ("encoder.onnx", ENCODER):
        check(model, out, encoder)
    (out / "encoder.onnx").unlink()  # the app uses the 8-bit one
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
