#!/usr/bin/env python3
"""A small sentence-embedding model for the app (adapt_ctc/embedding.py), in
place of EmbeddingGemma 2 on a machine short of memory: the Hugging Face
model's encoder as ONNX, its token embeddings and matrices quantized to int8,
beside its tokenizer and ``embedding.json`` (the prefixes it was trained with,
and how its token vectors become one).

Run with a Python that has torch and transformers; it quantizes with
onnxruntime from ``--quantize-python`` (default: the app's).

The app's embedder loads EmbeddingGemma alone: these exports need the loader
of the comparison they were made for (an encoder's ``last_hidden_state``,
averaged or its first token, per ``embedding.json``; texts never padded, as
int8 quantizes activations per batch), which was not kept. Measured
2026-10-26 with scripts/bench_embedding.py: e5-small-v2, bge-small-en-v1.5,
all-MiniLM-L6-v2, e5-base-v2 and multilingual-e5-small all rank the right
command first and in GLiNER's shortlist clearly less often than EmbeddingGemma
2 (in the top 8: 43-47 of 54 against 51).

    scripts/export_embedding.py intfloat/multilingual-e5-small models/embedding/multilingual-e5-small
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import torch
from transformers import AutoModel, AutoTokenizer

ROOT = Path(__file__).resolve().parent.parent

# The e5 family's prefixes: "query: " on both sides is its own advice for
# symmetric tasks -- a transcript against example sentences is one.
PREFIXES = {
    "e5": {"query": "query: ", "document": "query: "},
    "e5-passage": {"query": "query: ", "document": "passage: "},
    "bge": {"query": "", "document": ""},
    "minilm": {"query": "", "document": ""},
}
POOLING = {"e5": "mean", "e5-passage": "mean", "bge": "cls", "minilm": "mean"}

QUANTIZE = """
import sys
from onnxruntime.quantization import QuantType, quantize_dynamic
quantize_dynamic(sys.argv[1], sys.argv[2], weight_type=QuantType.QInt8, op_types_to_quantize=sys.argv[3].split(","),
                 per_channel=True)
"""


class Encoder(torch.nn.Module):
    def __init__(self, model) -> None:
        super().__init__()
        self.model = model

    def forward(self, input_ids, attention_mask):  # noqa: D102
        return self.model(input_ids=input_ids, attention_mask=attention_mask).last_hidden_state


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("model", help="a Hugging Face id (from its cache) or a folder")
    parser.add_argument("out", type=Path)
    parser.add_argument("--family", default="e5", choices=sorted(PREFIXES))
    parser.add_argument("--quantize-python", default=str(ROOT / ".venv/bin/python"))
    parser.add_argument("--quantize", default="MatMul", help="op types to quantize to int8 (Gather: the token table, "
                        "which dequantizes whole on every run)")
    args = parser.parse_args()

    tokenizer = AutoTokenizer.from_pretrained(args.model)
    # Eager attention: exported with SDPA, the padding mask came out wrong (a
    # batch's shorter sentences drifted from their own vectors, 0.93 cosine).
    model = AutoModel.from_pretrained(args.model, attn_implementation="eager").eval()
    args.out.mkdir(parents=True, exist_ok=True)
    # Of different lengths: with no padding to trace, the mask's use is left
    # out of the graph.
    sample = tokenizer(["query: turn on the lights in the kitchen please", "query: hi"], padding=True, return_tensors="pt")
    full = args.out / "model-fp32.onnx"
    with torch.no_grad():
        torch.onnx.export(
            Encoder(model), (sample["input_ids"], sample["attention_mask"]), str(full),
            input_names=["input_ids", "attention_mask"], output_names=["last_hidden_state"],
            dynamic_axes={"input_ids": {0: "batch", 1: "tokens"}, "attention_mask": {0: "batch", 1: "tokens"},
                          "last_hidden_state": {0: "batch", 1: "tokens"}},
            opset_version=17, dynamo=False,
        )
    if args.quantize == "none":
        full.replace(args.out / "model.onnx")
    else:
        subprocess.run([args.quantize_python, "-c", QUANTIZE, str(full), str(args.out / "model.onnx"), args.quantize], check=True)
        full.unlink()
    tokenizer.backend_tokenizer.no_padding()  # the sample's, not to be saved
    tokenizer.save_pretrained(args.out)
    for extra in ("tokenizer_config.json", "special_tokens_map.json", "sentencepiece.bpe.model", "vocab.txt"):
        (args.out / extra).unlink(missing_ok=True)
    (args.out / "embedding.json").write_text(json.dumps({
        "name": args.model.rstrip("/").split("/")[-1],
        "source": args.model,
        "prefix": PREFIXES[args.family],
        "pooling": POOLING[args.family],
        "max_tokens": 128,
    }, indent=2) + "\n")
    print(f"{args.out}: {sum(f.stat().st_size for f in args.out.iterdir()) / 1e6:.0f} MB", file=sys.stderr)
    shutil.rmtree(args.out / "__pycache__", ignore_errors=True)


if __name__ == "__main__":
    main()
