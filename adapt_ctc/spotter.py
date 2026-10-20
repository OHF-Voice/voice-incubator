"""Mining the audio for phrases: where each one could have been said, and how
well that explains the sound.

All scores are relative to the greedy path, the single best piece in every
frame. A phrase scored over frames ``s..e`` costs the log-probability its best
CTC alignment gives up against greedy over those same frames, so it is never
negative and 0 means the model's own reading of those frames *is* the phrase.
That makes the cost of a span comparable to any other span and to leaving the
audio unexplained, which is what intents are ranked on.

One Viterbi pass over the compiled phrase graph scores every phrase at every
end frame. Phrases may start at any frame (the frames before are free), and
each state carries the frame its path started on and how many pieces it
emitted, so every candidate span comes out with its boundaries.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

from .acoustic import Vocabulary
from .graph import ROOT, CompiledGraph, Meaning

try:
    from . import _viterbi
except ImportError:  # pragma: no cover - no compiler at install time
    _viterbi = None

NEG = np.float32(-np.inf)


@dataclass(eq=False)
class Hit:
    """One place a phrase could have been said."""

    terminal: int
    meanings: list[Meaning]
    start: int
    """First frame (inclusive)."""

    end: int
    """Last frame (inclusive)."""

    penalty: float
    """Nats given up against the greedy path over the same frames."""

    tokens: int
    dominated_by: "Hit | None" = None
    """A phrase that explains the same audio clearly better."""

    inside: "Hit | None" = None
    """A longer number this one is only part of ("one hundred" of "one
    hundred fifty")."""

    @property
    def heard(self) -> bool:
        return self.dominated_by is None and self.inside is None

    @property
    def per_token(self) -> float:
        return self.penalty / max(1, self.tokens)

    @property
    def phrase(self) -> str:
        return self.meanings[0].phrase

    @property
    def meaningful(self) -> bool:
        return any(m.meaningful for m in self.meanings)

    @property
    def center(self) -> int:
        return (self.start + self.end) // 2

    def overlap(self, other: "Hit") -> int:
        return max(0, min(self.end, other.end) - max(self.start, other.start) + 1)


@dataclass
class Word:
    """A word of the greedy transcript and the frames its pieces fired on."""

    text: str
    frames: list[int] = field(default_factory=list)

    @property
    def start(self) -> int:
        return self.frames[0]

    @property
    def end(self) -> int:
        return self.frames[-1]


@dataclass
class Spotting:
    frame_seconds: float
    num_frames: int
    transcript: str
    words: list[Word]
    hits: list[Hit]
    score: np.ndarray
    """``[T, M]`` best relative log-probability of each terminal ending at each frame."""

    start: np.ndarray
    tokens: np.ndarray
    graph: CompiledGraph
    visits: int = 0
    """Arc states the search updated (the full search: frames x arcs)."""

    live: bool = False
    """Whether the live-state search produced this (see :func:`search`)."""

    _closest: tuple[np.ndarray, ...] | None = field(default=None, repr=False)

    def closest(self, terminal: int) -> tuple[float, int, int, int] | None:
        """Best placement of a phrase regardless of the gate:
        ``(penalty, start, end, tokens)``. The live search only scores
        placements that could have ended within the gate, so a phrase far
        off has none."""
        penalty, start, end, tokens, _ = self._closest_arrays()
        if not np.isfinite(penalty[terminal]):
            return None
        return (
            float(penalty[terminal]),
            int(start[terminal]),
            int(end[terminal]),
            int(tokens[terminal]),
        )

    def closest_of(self, terminals: np.ndarray) -> tuple[float, int, int, int] | None:
        """The best placement among several phrases, by cost per piece:
        ``(cost per piece, terminal, start, end)``."""
        _, start, end, _, per_token = self._closest_arrays()
        costs = per_token[terminals]
        index = int(costs.argmin())
        if not np.isfinite(costs[index]):
            return None
        terminal = int(terminals[index])
        return float(costs[index]), terminal, int(start[terminal]), int(end[terminal])

    def _closest_arrays(self) -> tuple[np.ndarray, ...]:
        """For every terminal at once: the end frame with the lowest cost per piece."""
        if self._closest is None:
            penalty = -self.score.astype(np.float64)
            finite = np.isfinite(penalty)
            per_token = np.where(
                finite, np.maximum(penalty, 0.0) / np.maximum(self.tokens, 1), np.inf
            )
            end = per_token.argmin(axis=0)
            columns = np.arange(penalty.shape[1])
            self._closest = (
                penalty[end, columns],
                self.start[end, columns],
                end,
                self.tokens[end, columns],
                per_token[end, columns],
            )
        return self._closest


def relative_log_probs(
    log_probs: np.ndarray, vocab: Vocabulary
) -> tuple[np.ndarray, np.ndarray]:
    """Per-frame log-probabilities minus the frame's best, for pieces and for
    blank. Sentence punctuation is not speech, so it counts as blank."""
    best = log_probs.max(axis=1, keepdims=True)
    blank = log_probs[:, vocab.blank_id]
    if vocab.punctuation_ids:
        blank = np.maximum(blank, log_probs[:, vocab.punctuation_ids].max(axis=1))
    return (log_probs - best).astype(np.float32), (blank - best[:, 0]).astype(np.float32)


def viterbi(
    graph: CompiledGraph,
    rel_tokens: np.ndarray,
    rel_blank: np.ndarray,
    gate: float | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Best path ending at each terminal at each frame.

    Returns ``score``, ``start`` and ``tokens``, each ``[T, M]``. With a
    ``gate``, partial paths that can no longer finish within ``gate`` nats per
    piece are dropped (see :func:`search`). Uses the native module when it
    was built; it gives identical results.
    """
    return search(graph, rel_tokens, rel_blank, gate)[:3]


def search(
    graph: CompiledGraph,
    rel_tokens: np.ndarray,
    rel_blank: np.ndarray,
    gate: float | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, int]:
    """:func:`viterbi`, plus how many arc states it updated.

    Without a gate this is the full search: every arc, every frame. With one
    it is the live-state search. A state is dropped once its cost exceeds
    ``gate * (pieces so far + graph.remaining)``: even a perfect rest of the
    phrase could not bring it within the gate. Whatever would follow from a
    dropped state would be dropped too, so every phrase end the full search
    scores within the gate comes out identical. Only the far misses are lost,
    which the rejection reasons quote as the "closest" placement.
    """
    if _viterbi is None:
        score, start, tokens = viterbi_numpy(graph, rel_tokens, rel_blank, gate)
        return score, start, tokens, rel_tokens.shape[0] * graph.num_arcs

    num_frames, vocab_size = rel_tokens.shape
    terminals = graph.terminals.shape[0]
    i32 = np.int32
    score = np.empty((num_frames, terminals), dtype=np.float32)
    start = np.empty((num_frames, terminals), dtype=i32)
    tokens = np.empty((num_frames, terminals), dtype=i32)
    rel_tokens = np.ascontiguousarray(rel_tokens, dtype=np.float32)
    rel_blank = np.ascontiguousarray(rel_blank, dtype=np.float32)

    def c(array: np.ndarray) -> np.ndarray:
        return np.ascontiguousarray(array, dtype=i32)

    if gate is None:
        _viterbi.viterbi(
            c(graph.arc_token),
            c(graph.arc_src),
            np.ascontiguousarray(graph.from_root, dtype=np.uint8),
            c(graph.in_ptr),
            c(graph.in_arcs),
            c(graph.terminals),
            rel_tokens,
            rel_blank,
            score,
            start,
            tokens,
            graph.num_arcs,
            graph.num_positions,
            int(graph.in_arcs.shape[0]),
            terminals,
            num_frames,
            vocab_size,
        )
        return score, start, tokens, num_frames * graph.num_arcs

    if graph.root_ptr.shape[0] != vocab_size + 1:
        raise ValueError("log-probabilities do not match the graph's vocabulary")
    visits = _viterbi.viterbi_live(
        c(graph.arc_token),
        c(graph.arc_src),
        c(graph.arc_dst),
        c(graph.in_ptr),
        c(graph.in_arcs),
        c(graph.out_ptr),
        c(graph.out_arcs),
        c(graph.remaining),
        c(graph.terminal_of),
        c(graph.root_ptr),
        c(graph.root_arcs),
        c(graph.root_reach),
        rel_tokens,
        rel_blank,
        score,
        start,
        tokens,
        float(gate),
        graph.num_arcs,
        graph.num_positions,
        terminals,
        num_frames,
        vocab_size,
    )
    return score, start, tokens, int(visits)


def viterbi_numpy(
    graph: CompiledGraph,
    rel_tokens: np.ndarray,
    rel_blank: np.ndarray,
    gate: float | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The reference implementation, vectorized over arcs with numpy.

    With a ``gate`` it applies the live-state search's pruning (dropped
    states become -inf), so it computes what the native live search does,
    though it still visits every arc.
    """
    num_frames = rel_tokens.shape[0]
    num_arcs = graph.num_arcs
    num_positions = graph.num_positions
    terminals = graph.terminals
    num_terminals = terminals.shape[0]

    arc_token = graph.arc_token
    arc_src = graph.arc_src
    pos_in = graph.pos_in
    pos_in_token = graph.pos_in_token
    term_in = pos_in[terminals]
    rows_p = np.arange(num_positions)
    rows_m = np.arange(num_terminals)

    # Emitting state per arc, plus a sentinel at index A that is never reachable.
    emit = np.full(num_arcs + 1, NEG, dtype=np.float32)
    e_start = np.zeros(num_arcs + 1, dtype=np.int32)
    e_tokens = np.zeros(num_arcs + 1, dtype=np.int32)
    # Blank state per position: a phrase partly said, waiting for its next piece.
    blank = np.full(num_positions, NEG, dtype=np.float32)
    b_start = np.zeros(num_positions, dtype=np.int32)
    b_tokens = np.zeros(num_positions, dtype=np.int32)

    enter = np.where(graph.from_root, np.float32(0.0), NEG).astype(np.float32)
    arc_scores = rel_tokens[:, arc_token]  # [T, A]

    score = np.full((num_frames, num_terminals), NEG, dtype=np.float32)
    start = np.zeros((num_frames, num_terminals), dtype=np.int32)
    tokens = np.zeros((num_frames, num_terminals), dtype=np.int32)
    if gate is not None:
        arc_remaining = graph.remaining[graph.arc_dst]

    for t in range(num_frames):
        # Best arc into each position, and best whose piece differs from it:
        # CTC needs a blank between two identical pieces.
        incoming = emit[pos_in]
        k1 = incoming.argmax(axis=1)
        a1 = pos_in[rows_p, k1]
        tok1 = pos_in_token[rows_p, k1]
        v1 = emit[a1]
        incoming = np.where(pos_in_token == tok1[:, None], NEG, incoming)
        k2 = incoming.argmax(axis=1)
        a2 = pos_in[rows_p, k2]
        v2 = incoming[rows_p, k2]

        same = arc_token == tok1[arc_src]
        pred_arc = np.where(same, a2[arc_src], a1[arc_src])
        pred_val = np.where(same, v2[arc_src], v1[arc_src])

        # Candidates for each arc: hold its piece, leave a blank, follow the
        # previous piece directly, or start a phrase here.
        best = emit[:num_arcs].copy()
        best_start = e_start[:num_arcs].copy()
        best_tokens = e_tokens[:num_arcs].copy()

        from_blank = blank[arc_src]
        take = from_blank > best
        best = np.where(take, from_blank, best)
        best_start = np.where(take, b_start[arc_src], best_start)
        best_tokens = np.where(take, b_tokens[arc_src] + 1, best_tokens)

        take = pred_val > best
        best = np.where(take, pred_val, best)
        best_start = np.where(take, e_start[pred_arc], best_start)
        best_tokens = np.where(take, e_tokens[pred_arc] + 1, best_tokens)

        take = enter > best
        best = np.where(take, enter, best)
        best_start = np.where(take, t, best_start)
        best_tokens = np.where(take, 1, best_tokens)

        # Blank after a position: stay, or arrive from its best incoming arc.
        take = v1 > blank
        new_blank = np.where(take, v1, blank) + rel_blank[t]
        b_start = np.where(take, e_start[a1], b_start)
        b_tokens = np.where(take, e_tokens[a1], b_tokens)
        new_blank[ROOT] = NEG
        blank = new_blank

        emit[:num_arcs] = best + arc_scores[t]
        e_start[:num_arcs] = best_start
        e_tokens[:num_arcs] = best_tokens

        if gate is not None:
            dead = -emit[:num_arcs] > gate * (best_tokens + arc_remaining)
            emit[:num_arcs][dead] = NEG
            blank[-blank > gate * (b_tokens + graph.remaining)] = NEG

        # A phrase ends on the frame its last piece fires.
        k = emit[term_in].argmax(axis=1)
        last = term_in[rows_m, k]
        score[t] = emit[last]
        start[t] = e_start[last]
        tokens[t] = e_tokens[last]

    return score, start, tokens


def _logsumexp(values: np.ndarray, axis: int) -> np.ndarray:
    peak = np.max(values, axis=axis, keepdims=True)
    finite = np.isfinite(peak)
    shift = np.where(finite, peak, 0.0)
    with np.errstate(divide="ignore"):
        total = shift + np.log(np.sum(np.exp(values - shift), axis=axis, keepdims=True))
    return np.squeeze(np.where(finite, total, -np.inf), axis=axis)


def forward_numpy(
    graph: CompiledGraph, rel_tokens: np.ndarray, rel_blank: np.ndarray
) -> np.ndarray:
    """Summed evidence for each terminal ending at each frame, ``[T, M]``.

    The Viterbi recursion with log-sum-exp in place of max: it adds up every
    alignment, every segmentation and every start frame, where Viterbi keeps
    the single best. Relative to the greedy path like everything else, so it
    can exceed 0 when the model spreads a word over several spellings.
    Meant for small graphs (the wake word's); there is no native version.
    """
    num_frames = rel_tokens.shape[0]
    num_arcs = graph.num_arcs
    arc_token, arc_src = graph.arc_token, graph.arc_src
    pos_in = graph.pos_in
    same = graph.pos_in_token[arc_src] == arc_token[:, None]  # needs a blank between
    term_in = pos_in[graph.terminals]

    emit = np.full(num_arcs + 1, -np.inf)
    blank = np.full(graph.num_positions, -np.inf)
    enter = np.where(graph.from_root, 0.0, -np.inf)
    out = np.full((num_frames, graph.terminals.shape[0]), -np.inf)
    rel_tokens = rel_tokens.astype(np.float64)
    rel_blank = rel_blank.astype(np.float64)
    for t in range(num_frames):
        incoming = emit[pos_in]
        follow = _logsumexp(np.where(same, -np.inf, incoming[arc_src]), axis=1)
        arrive = _logsumexp(np.stack([emit[:num_arcs], blank[arc_src], follow, enter]), axis=0)
        new_blank = rel_blank[t] + np.logaddexp(blank, _logsumexp(incoming, axis=1))
        new_blank[ROOT] = -np.inf
        emit[:num_arcs] = rel_tokens[t, arc_token] + arrive
        blank = new_blank
        out[t] = _logsumexp(emit[term_in], axis=1)
    return out


def extract_hits(
    graph: CompiledGraph,
    score: np.ndarray,
    start: np.ndarray,
    tokens: np.ndarray,
    gate: float,
) -> list[Hit]:
    """Every placement of a phrase within ``gate`` nats per piece, one per
    distinct stretch of audio."""
    penalty = -score.astype(np.float64)
    with np.errstate(invalid="ignore"):
        per_token = penalty / np.maximum(tokens, 1)
        ok = np.isfinite(penalty) & (per_token <= gate)

    best: dict[tuple[int, int], tuple[float, int, int]] = {}
    for frame, terminal in zip(*np.nonzero(ok)):
        key = (int(terminal), int(start[frame, terminal]))
        value = (float(penalty[frame, terminal]), int(frame), int(tokens[frame, terminal]))
        # As cheap and more pieces: the longer form of a name ("Wohnzimmers"
        # where "Wohnzimmer" also fits) explains more of what was said.
        if key not in best or value[0] < best[key][0] or (value[0] == best[key][0] and value[2] > best[key][2]):
            best[key] = value

    candidates: dict[int, list[tuple[float, int, int, int]]] = defaultdict(list)
    for (terminal, first), (pen, last, count) in best.items():
        candidates[terminal].append((pen, first, last, count))

    hits: list[Hit] = []
    for terminal, found in candidates.items():
        kept: list[Hit] = []
        for pen, first, last, count in sorted(found):
            hit = Hit(terminal, graph.meanings[terminal], first, last, max(0.0, pen), count)
            if not any(hit.overlap(other) for other in kept):
                kept.append(hit)
        hits.extend(kept)
    hits.sort(key=lambda h: (h.start, h.end, h.penalty))
    return hits


def mark_dominated(hits: list[Hit], margin: float) -> None:
    """A hit is not heard when another phrase covering mostly the same frames
    explains them better by ``margin`` nats: "turn off" on audio that says
    "turn on" scores well enough to pass a gate, but never better than "turn
    on" itself.

    A rival lying strictly inside the hit cannot beat it. Frames a phrase
    leaves out cost it nothing, so "on" is always at least as cheap as the
    "turn on" around it; that says nothing about whether "turn" was said.
    """
    if len(hits) < 2:
        return
    starts = np.array([h.start for h in hits])
    ends = np.array([h.end for h in hits])
    penalties = np.array([h.penalty for h in hits])
    lengths = ends - starts + 1
    overlap = np.clip(
        np.minimum.outer(ends, ends) - np.maximum.outer(starts, starts) + 1, 0, None
    )
    mutual = 2 * overlap >= np.maximum.outer(lengths, lengths)
    better = penalties[None, :] + margin <= penalties[:, None]  # [h, g]: g beats h
    inside = (
        (starts[None, :] >= starts[:, None])
        & (ends[None, :] <= ends[:, None])
        & (lengths[None, :] < lengths[:, None])
    )  # [h, g]: g lies strictly within h
    np.fill_diagonal(mutual, False)
    beaten = mutual & better & ~inside
    for index in np.nonzero(beaten.any(axis=1))[0]:
        rivals = np.nonzero(beaten[index])[0]
        hits[index].dominated_by = hits[int(rivals[np.argmin(penalties[rivals])])]


def mark_inside_numbers(hits: list[Hit], margin: float) -> None:
    """Numbers are read longest-first. "one hundred" inside "one hundred
    fifty" is not a number of its own unless the longer reading is clearly
    worse; otherwise an intent limited to 0-100 would take 100 and shrug off
    "fifty" as one stray word."""
    numbers = [
        h for h in hits if h.dominated_by is None and all(m.kind == "number" for m in h.meanings)
    ]
    for part in numbers:
        for whole in numbers:
            if (
                whole is not part
                and whole.start <= part.start
                and part.end <= whole.end
                and whole.end - whole.start > part.end - part.start
                and whole.penalty < part.penalty + margin
                and (part.inside is None or whole.end - whole.start > part.inside.end - part.inside.start)
            ):
                part.inside = whole


def greedy_words(log_probs: np.ndarray, vocab: Vocabulary) -> tuple[str, list[Word]]:
    """The model's own reading: transcript text and its words with their frames."""
    ids: list[int] = []
    words: list[Word] = []
    previous = None
    for frame, token in enumerate(log_probs.argmax(axis=1)):
        token = int(token)
        if token != previous and token != vocab.blank_id:
            ids.append(token)
            if not vocab.is_punctuation(token):
                piece = vocab.id2piece.get(token, "")
                if vocab.starts_word(token) or not words:
                    words.append(Word(""))
                words[-1].text += piece.lstrip("▁")
                words[-1].frames.append(frame)
        previous = token
    return vocab.ids_to_text(ids), [w for w in words if w.frames]


def spot(
    graph: CompiledGraph,
    vocab: Vocabulary,
    log_probs: np.ndarray,
    frame_seconds: float,
    gate: float,
    margin: float,
    live: bool = True,
) -> Spotting:
    """Mine the audio for every phrase in the graph. ``live`` selects the
    live-state search (the default) over the full one."""
    rel_tokens, rel_blank = relative_log_probs(log_probs, vocab)
    score, start, tokens, visits = search(
        graph, rel_tokens, rel_blank, gate if live else None
    )
    hits = extract_hits(graph, score, start, tokens, gate)
    mark_dominated(hits, margin)
    mark_inside_numbers(hits, margin)
    transcript, words = greedy_words(log_probs, vocab)
    return Spotting(
        frame_seconds=frame_seconds,
        num_frames=int(log_probs.shape[0]),
        transcript=transcript,
        words=words,
        hits=hits,
        score=score,
        start=start,
        tokens=tokens,
        graph=graph,
        visits=visits,
        live=live,
    )
