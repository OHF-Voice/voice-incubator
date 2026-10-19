"""GLiNER2.5 (fastino, Apache-2.0) without PyTorch: onnxruntime, numpy and
the tokenizers library -- for the command fallback (voice_call/fallback.py).

The networks are exported once with the gliner2 package
(scripts/export_gliner.py): the encoder (8-bit weights, a 4-bit embedding),
the boundary head in two parts -- the evidence candidate spans are chosen
from, and the chosen candidates' scores -- and the classifier's weights.
Choosing the candidates (top boundaries, pairing, a quota per entity type,
de-duplication) branches on sizes and needs a stable sort, so it is done here,
in numpy. What the gliner2 package does around the networks is ported for the
two tasks the fallback needs, entities (with descriptions) and one
classification: the prompt and its marker positions, word splitting, and
decoding (thresholds, abstention, flat overlap resolution). Against the
gliner2 package on 131 of the fallback benchmark's inputs: identical tokens,
positions and entities, probabilities within 3e-6 (README "When nothing
matches").

Ported from fastino-ai/GLiNER2 (Apache-2.0): processor.py
(_format_input_with_mapping, _transform_schema, _normalize_text),
processing/word_splitter.py, inference/overlap.py, models/boundary/engine.py,
models/boundary/pool.py (DocumentCandidatePool, _deduplicate_pool) and
proposal.py (select_top_boundaries).
"""

from __future__ import annotations

import bisect
import functools
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer


@functools.cache
def _words() -> re.Pattern[str]:
    """gliner2's word splitter. Its word characters include every combining
    mark in Unicode: built once, when first needed (a scan of every code
    point)."""
    marks = "".join(chr(code) for code in range(sys.maxunicode + 1) if unicodedata.category(chr(code)).startswith("M"))
    word = rf"[\w{marks}" + "‌‍]"
    return re.compile(
        rf"""(?:https?://[^\s]+|www\.[^\s]+)
        |[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{{2,}}
        |@[a-z0-9_]+
        |{word}+(?:[-_]{word}+)*
        |\S""",
        re.VERBOSE | re.IGNORECASE,
    )


def split_words(text: str) -> list[tuple[str, int, int]]:
    """(lowercased word, start, end) for each word and punctuation mark."""
    return [(m.group().lower(), m.start(), m.end()) for m in _words().finditer(text)]


def normalize_text(text: str) -> str:
    """As the gliner2 collation expects: ending in sentence punctuation."""
    if not text:
        return "."
    return text if text.endswith((".", "!", "?")) else text + "."


class GlinerOnnx:
    """One exported model (``gliner2_onnx.json`` and its files). ``extract``
    takes ~0.25 s on a Pi 5 for the small model: call it from a thread."""

    def __init__(
        self, model_dir: Path | str, threads: int = 4, encoder: str | None = None, low_memory: bool = False
    ) -> None:
        model_dir = Path(model_dir)
        self.settings = json.loads((model_dir / "gliner2_onnx.json").read_text())
        encoder = encoder or self.settings.get("encoder", "encoder.onnx")
        self.tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        providers = ["CPUExecutionProvider"]
        # Constant folding precomputes parts of the encoder (DeBERTa's relative
        # positions) in full precision: ~125 MB more (measured with a 4-bit
        # encoder), and ~30% faster.
        self.encoder = ort.InferenceSession(
            str(model_dir / encoder), options, providers=providers,
            disabled_optimizers=["ConstantFolding"] if low_memory else None,
        )
        self.boundary_a = ort.InferenceSession(str(model_dir / "boundary_a.onnx"), options, providers=providers)
        self.boundary_b = ort.InferenceSession(str(model_dir / "boundary_b.onnx"), options, providers=providers)
        weights = np.load(model_dir / "classifier.npz")
        self.classifier = [(weights[f"w{i}"].T.astype(np.float32), weights[f"b{i}"].astype(np.float32)) for i in range(2)]
        self._cache: dict[str, list[int]] = {}

    # ------------------------------------------------------------- prompt

    def _ids(self, piece: str) -> list[int]:
        ids = self._cache.get(piece)
        if ids is None:
            ids = self.tokenizer.encode(piece, add_special_tokens=False).ids
            self._cache[piece] = ids
        return ids

    @staticmethod
    def _schema(parent: str, fields: list[str], marker: str, descriptions: dict[str, str] | None = None) -> list[str]:
        prompt = parent
        for label, description in (descriptions or {}).items():
            if label in fields:
                prompt += f" [DESCRIPTION] {label}: {description}"
        tokens = ["(", "[P]", prompt, "("]
        for field in fields:
            tokens += [marker, field]
        return tokens + [")", ")"]

    def build(self, text: str, entities: dict[str, str] | list[str] | None, classification: tuple[str, list[str]] | None):
        """The model's input for one text: ids, and where the text's words,
        the entity types and the classification labels are."""
        schemas, kinds = [], []
        if entities:
            names = list(entities)
            descriptions = entities if isinstance(entities, dict) else None
            schemas.append(self._schema("entities", names, "[E]", {k: v for k, v in (descriptions or {}).items() if v}))
            kinds.append("entities")
        if classification:
            task, labels = classification
            schemas.append(self._schema(task, list(labels), "[L]"))
            kinds.append("classification")
        text = normalize_text(text)
        words = split_words(text)

        combined: list[str] = []
        markers: set[int] = set()
        offset = 0
        for schema in schemas:
            combined += schema + ["[SEP_STRUCT]"]
            markers.update(offset + i for i in range(4, len(schema) - 2, 2))
            offset += len(schema) + 1
        if combined:
            combined.pop()
        text_start = len(combined) + 1
        combined += ["[SEP_TEXT]"] + [w for w, _, _ in words]

        ids: list[int] = []
        word_positions: list[int] = []
        marker_positions: list[list[int]] = [[] for _ in schemas]
        schema_index = 0
        for index, piece in enumerate(combined):
            position = len(ids)
            ids += self._ids(piece)
            if index >= text_start:
                word_positions.append(position)
            elif piece == "[SEP_STRUCT]":
                schema_index += 1
            elif index in markers:
                marker_positions[schema_index].append(position)
        return {
            "text": text,
            "words": words,
            "ids": np.array([ids], dtype=np.int64),
            "word_positions": word_positions,
            "markers": dict(zip(kinds, marker_positions)),
        }

    # ------------------------------------------------------------ inference

    def extract(
        self,
        text: str,
        entities: dict[str, str] | list[str] | None = None,
        classification: tuple[str, list[str]] | None = None,
        multi_label: bool = True,
        threshold: float = 0.5,
    ) -> dict[str, Any]:
        """{"entities": {type: [(text, confidence, start, end)]},
        "classification": {label: probability}}."""
        built = self.build(text, entities, classification)
        hidden = self.encoder.run(None, {"input_ids": built["ids"], "attention_mask": np.ones_like(built["ids"])})[0][0]
        out: dict[str, Any] = {}
        if entities:
            out["entities"] = self._entities(built, hidden, list(entities), threshold)
        if classification:
            states = hidden[built["markers"]["classification"]]
            x = states
            for i, (weight, bias) in enumerate(self.classifier):
                x = x @ weight + bias
                if i == 0:
                    x = np.maximum(x, 0.0)
            logits = x[:, 0] / self.settings["classification_temperature"]
            if multi_label:
                probs = _sigmoid(logits)
            else:
                probs = np.exp(logits - logits.max())
                probs /= probs.sum()
            out["classification"] = dict(zip(classification[1], probs.tolist()))
        return out

    def _entities(self, built, hidden, names: list[str], threshold: float) -> dict[str, list]:
        text_states = hidden[built["word_positions"]][None].astype(np.float32)
        query_states = hidden[built["markers"]["entities"]][None].astype(np.float32)
        text_mask = np.ones(text_states.shape[:2], dtype=bool)
        query_mask = np.ones(query_states.shape[:2], dtype=bool)
        (boundary_states, boundary_mask, start_logits, end_logits, inside_prefix, inside_prefix_mean,
         start_all, end_all, null_logits, _counts) = self.boundary_a.run(None, {
            "text_states": text_states, "text_mask": text_mask, "query_states": query_states, "query_mask": query_mask,
        })
        pool = self.settings["pool"]
        indices, candidate_mask, compat = choose_candidates(
            boundary_mask[0], query_mask[0], start_logits[0], end_logits[0], start_all[0], end_all[0],
            pool["top_k"], pool["size"], pool["min_per_query"],
        )
        scores = self.boundary_b.run(None, {
            "boundary_states": boundary_states, "query_states": query_states, "query_mask": query_mask,
            "indices": indices[None], "candidate_mask": candidate_mask[None], "compat": compat[None],
            "start_logits": start_logits, "end_logits": end_logits, "inside_prefix": inside_prefix,
            "inside_prefix_mean": inside_prefix_mean, "text_states": text_states, "text_mask": text_mask,
        })[0]
        pair_logits = scores[0].T[None]  # [1, Q, C]
        indices = np.broadcast_to(indices[None, None], (1, len(names)) + indices.shape)
        valid = np.broadcast_to(candidate_mask[None, None], (1, len(names), len(candidate_mask)))
        probs = _sigmoid(pair_logits[0] / self.settings["pair_temperature"])
        null = _sigmoid(null_logits[0])
        words, text = built["words"], built["text"]
        out = {}
        for q, name in enumerate(names):
            if null[q] > self.settings["abstention_threshold"]:
                out[name] = []
                continue
            keep = valid[0, q] & (probs[q] >= threshold)
            scored = [(float(probs[q, c]), int(indices[0, q, c, 0]), int(indices[0, q, c, 1])) for c in np.nonzero(keep)[0]]
            items = []
            for probability, start, end in resolve_flat(scored):
                if not 0 <= start < end <= len(words):
                    continue
                char_start, char_end = words[start][1], words[end - 1][2]
                surface = text[char_start:char_end].strip()
                if surface:
                    items.append((surface, probability, char_start, char_end))
            out[name] = items
        return out


MASK_LOGIT = np.float32(-1.0e4)


def _sigmoid(x: np.ndarray) -> np.ndarray:
    """Without overflow at the masked -1e4 logits."""
    return 0.5 * (1.0 + np.tanh(0.5 * x))


def _descending(values: np.ndarray, axis: int = -1) -> np.ndarray:
    """Stable descending order: ties keep their order, as torch's stable sort."""
    return np.argsort(-values, axis=axis, kind="stable")


def _top_boundaries(scores: np.ndarray, valid: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
    order = _descending(np.where(valid, scores, MASK_LOGIT))[: min(k, len(scores))]
    keep = valid[order]
    return np.where(keep, order, 0), keep


def choose_candidates(
    boundary_mask, query_mask, start_logits, end_logits, start_all, end_all, top_k: int, size: int, min_per_query: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """One de-duplicated pool of candidate spans for every entity type:
    indices [C, 2] (half-open word boundaries), mask [C], compat [C]."""
    n, d = start_all.shape
    scale = np.float32(np.sqrt(d))
    q_boundary = boundary_mask[None, :] & query_mask[:, None]
    union_start = np.where(q_boundary, start_logits, MASK_LOGIT).max(0)
    union_end = np.where(q_boundary, end_logits, MASK_LOGIT).max(0)
    union_valid = boundary_mask & query_mask.any()
    starts, starts_valid = _top_boundaries(union_start, union_valid, top_k)
    ends, ends_valid = _top_boundaries(union_end, union_valid, top_k)
    pair_s = np.repeat(starts, len(ends))
    pair_e = np.tile(ends, len(starts))
    pair_valid = (starts_valid[:, None] & ends_valid[None, :] & (ends[None, :] > starts[:, None])).reshape(-1)
    compat = (start_all[pair_s] * end_all[pair_e]).sum(-1) / scale
    union_pair = compat + union_start[pair_s] + union_end[pair_e]

    quota = min(min_per_query, len(pair_s))
    q = len(query_mask)
    if quota:
        per_query = start_logits[:, pair_s] + end_logits[:, pair_e] + compat[None, :]
        per_query_valid = pair_valid[None, :] & query_mask[:, None]
        ranked = _descending(np.where(per_query_valid, per_query, MASK_LOGIT))[:, :quota]
        quota_keys = (pair_s[ranked] * n + pair_e[ranked]).reshape(-1)
        quota_valid = np.take_along_axis(per_query_valid, ranked, -1).reshape(-1)
        bonus = np.arange(quota, 0, -1, dtype=np.float32)
        quota_scores = (np.full((q, quota), -MASK_LOGIT * np.float32(0.5), dtype=np.float32) + bonus).reshape(-1)
    else:
        quota_keys = np.zeros(0, dtype=np.int64)
        quota_valid = np.zeros(0, dtype=bool)
        quota_scores = np.zeros(0, dtype=np.float32)
    keys = np.concatenate((quota_keys, pair_s * n + pair_e)).astype(np.int64)
    scores = np.concatenate((quota_scores, union_pair.astype(np.float32)))
    valid = np.concatenate((quota_valid, pair_valid))

    # De-duplicate, keeping each span's highest-priority occurrence.
    keys = np.where(valid, keys, n * n)
    scores = np.where(valid, scores, MASK_LOGIT)
    by_score = _descending(scores)
    keys, scores, valid = keys[by_score], scores[by_score], valid[by_score]
    by_key = np.argsort(keys, kind="stable")
    keys, scores, valid = keys[by_key], scores[by_key], valid[by_key]
    first = np.ones_like(valid)
    first[1:] = keys[1:] != keys[:-1]
    keep = valid & first
    order = _descending(np.where(keep, scores, MASK_LOGIT))[:size]
    keys, keep = keys[order], keep[order]
    if len(keys) < size:
        keys = np.pad(keys, (0, size - len(keys)))
        keep = np.pad(keep, (0, size - len(keep)), constant_values=False)
    keys = np.where(keep, keys, 0)
    s, e = keys // n, keys % n
    indices = np.where(keep[:, None], np.stack((s, e), -1), 0).astype(np.int64)
    compat = np.where(keep, (start_all[s] * end_all[e]).sum(-1) / scale, 0.0).astype(np.float32)
    return indices, keep, compat


def resolve_flat(scored: list[tuple[float, int, int]]) -> list[tuple[float, int, int]]:
    """The maximum-total-score non-overlapping subset (weighted interval
    scheduling), ranked by descending score -- gliner2's "flat" policy."""
    if not scored:
        return []
    indexed = list(enumerate(scored))

    def rank_key(row):
        index, (score, start, end) = row
        return (-score, start, end, index)

    distinct, seen = [], set()
    for row in sorted(indexed, key=rank_key):
        boundaries = (row[1][1], row[1][2])
        if boundaries not in seen:
            seen.add(boundaries)
            distinct.append(row)
    by_end = sorted(distinct, key=lambda row: (row[1][2], row[1][1], -row[1][0], row[0]))
    ends = [item[2] for _, item in by_end]
    predecessors = [bisect.bisect_right(ends, item[1], 0, index) - 1 for index, (_, item) in enumerate(by_end)]
    best: list[tuple[float, tuple[int, ...]]] = [(0.0, ())]

    def selection_key(selection):
        rows = [by_end[index] for index in selection]
        return tuple(rank_key(row) for row in sorted(rows, key=rank_key))

    for index, (_, item) in enumerate(by_end):
        previous_score, previous_selection = best[predecessors[index] + 1]
        with_item = (previous_score + item[0], previous_selection + (index,))
        without_item = best[index]
        if with_item[0] > without_item[0]:
            best.append(with_item)
        elif with_item[0] < without_item[0]:
            best.append(without_item)
        elif len(with_item[1]) > len(without_item[1]):
            best.append(with_item)
        elif len(with_item[1]) < len(without_item[1]):
            best.append(without_item)
        else:
            best.append(with_item if selection_key(with_item[1]) < selection_key(without_item[1]) else without_item)
    selected = [by_end[index] for index in best[-1][1]]
    selected.sort(key=rank_key)
    return [item for _, item in selected]
