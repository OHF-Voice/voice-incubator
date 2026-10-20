"""The vectorized graph Viterbi against a brute-force CTC reference."""

import itertools

import numpy as np
import pytest

from adapt_ctc.graph import Meaning, PhraseGraph
from adapt_ctc import spotter
from adapt_ctc.spotter import greedy_words, relative_log_probs, viterbi

from .conftest import utterance


def _sequences(vocab, words, flexible):
    """Every piece sequence the lattice should accept, by brute force."""
    pieces = [(core, ws, i) for (core, ws), ids in vocab.pieces.items() for i in ids]

    def spell(text, at_start, must_start):
        """Pieces spelling ``text``; ``at_start`` when a unit begins here."""
        if not text:
            yield []
            return
        for core, ws, token in pieces:
            if not text.startswith(core):
                continue
            if at_start and must_start and not ws:
                continue
            if ws and not at_start and not flexible:
                continue
            for rest in spell(text[len(core) :], False, False):
                yield [token] + rest
        if at_start and vocab.space_id is not None:
            for core, ws, token in pieces:
                if not ws and text.startswith(core):
                    for rest in spell(text[len(core) :], False, False):
                        yield [vocab.space_id, token] + rest

    per_unit = [
        list(spell(word, True, index == 0 or not flexible)) for index, word in enumerate(words)
    ]
    for combo in itertools.product(*per_unit):
        yield [t for part in combo for t in part]


def _reference(rel_tokens, rel_blank, sequence):
    """Free-start linear CTC Viterbi: best score ending on the last piece at each frame."""
    T = rel_tokens.shape[0]
    L = len(sequence)
    out = np.full(T, -np.inf)
    # states: piece 0, blank 0, piece 1, blank 1, ..., piece L-1
    S = 2 * L - 1
    alpha = np.full(S, -np.inf)
    for t in range(T):
        new = np.full(S, -np.inf)
        for s in range(S):
            if s % 2 == 0:
                k = s // 2
                cands = [alpha[s]]
                if k == 0:
                    cands.append(0.0)
                else:
                    cands.append(alpha[s - 1])
                    if sequence[k] != sequence[k - 1]:
                        cands.append(alpha[s - 2])
                new[s] = max(cands) + rel_tokens[t, sequence[k]]
            else:
                new[s] = max(alpha[s], alpha[s - 1]) + rel_blank[t]
        alpha = new
        out[t] = alpha[S - 1]
    return out


@pytest.mark.parametrize(
    ("words", "flexible"),
    [
        (["turn", "on"], False),
        (["living", "room"], False),
        (["eco", "bee"], True),
        (["ecobee"], True),
        (["seventy", "two", "and", "a", "half"], False),
    ],
)
def test_viterbi_matches_reference(vocab, words, flexible):
    graph = PhraseGraph(vocab)
    graph.add(words, Meaning("keyword", " ".join(words)), flexible=flexible)
    compiled = graph.compile()
    rng = np.random.default_rng(1234)
    for _ in range(5):
        logits = rng.normal(size=(14, vocab.size)).astype(np.float32) * 3
        log_probs = logits - np.logaddexp.reduce(logits, axis=1, keepdims=True)
        rel_tokens, rel_blank = relative_log_probs(log_probs, vocab)
        score, _, _ = viterbi(compiled, rel_tokens, rel_blank)
        sequences = list(_sequences(vocab, words, flexible))
        assert sequences
        expected = np.max([_reference(rel_tokens, rel_blank, s) for s in sequences], axis=0)
        np.testing.assert_allclose(score[:, 0], expected, rtol=1e-5, atol=1e-4)


def test_flexible_name_accepts_any_word_breaks(vocab):
    graph = PhraseGraph(vocab)
    graph.add(["ecobee"], Meaning("name", "ecobee", "entity", "climate.ecobee"), flexible=True)
    compiled = graph.compile()
    for script in (
        ["eco", "bee"],
        ["e", "co", "bee"],
        ["eco", "+b", "+ee"],
        ["e", "+co", "bee"],
        ["\u2581", "+eco", "+bee"],
    ):
        log_probs = utterance(vocab, script)
        rel_tokens, rel_blank = relative_log_probs(log_probs, vocab)
        score, start, _ = viterbi(compiled, rel_tokens, rel_blank)
        assert score[:, 0].max() > -0.5, script


def test_start_frames_and_token_counts(vocab):
    graph = PhraseGraph(vocab)
    graph.add(["turn", "on"], Meaning("keyword", "turn on"))
    compiled = graph.compile()
    log_probs = utterance(vocab, ["please", "turn", "on", "the", "lights"])
    rel_tokens, rel_blank = relative_log_probs(log_probs, vocab)
    score, start, tokens = viterbi(compiled, rel_tokens, rel_blank)
    end = int(score[:, 0].argmax())
    assert (start[end, 0], end, tokens[end, 0]) == (3, 5, 2)


def test_greedy_words(vocab):
    log_probs = utterance(vocab, ["turn", "on", "the", "light", "+s", "."])
    transcript, words = greedy_words(log_probs, vocab)
    assert transcript == "turn on the lights."
    assert [w.text for w in words] == ["turn", "on", "the", "lights"]
    assert words[-1].frames == [7, 9]


@pytest.mark.skipif(spotter._viterbi is None, reason="native module not built")
def test_native_matches_numpy(vocab):
    graph = PhraseGraph(vocab)
    for words, flexible in [
        (["turn", "on"], False),
        (["turn", "off"], False),
        (["seventy", "two"], False),
        (["seventy", "two", "and", "a", "half"], False),
        (["eco", "bee"], True),
        (["living", "room"], True),
        (["the"], False),
    ]:
        graph.add(words, Meaning("keyword", " ".join(words)), flexible=flexible)
    compiled = graph.compile()
    rng = np.random.default_rng(7)
    for _ in range(10):
        logits = rng.normal(size=(30, vocab.size)).astype(np.float32) * 4
        log_probs = logits - np.logaddexp.reduce(logits, axis=1, keepdims=True)
        rel_tokens, rel_blank = relative_log_probs(log_probs, vocab)
        native = spotter.viterbi(compiled, rel_tokens, rel_blank)
        reference = spotter.viterbi_numpy(compiled, rel_tokens, rel_blank)
        finite = np.isfinite(reference[0])
        np.testing.assert_array_equal(native[0], reference[0])
        for got, want in zip(native[1:], reference[1:]):
            np.testing.assert_array_equal(got[finite], want[finite])


def _graph_and_frames(vocab, seed, frames=30, scale=3.0):
    graph = PhraseGraph(vocab)
    for words, flexible in [
        (["turn", "on"], False),
        (["turn", "off"], False),
        (["seventy", "two"], False),
        (["seventy", "two", "and", "a", "half"], False),
        (["eco", "bee"], True),
        (["living", "room"], True),
        (["the"], False),
    ]:
        graph.add(words, Meaning("keyword", " ".join(words)), flexible=flexible)
    rng = np.random.default_rng(seed)
    logits = rng.normal(size=(frames, vocab.size)).astype(np.float32) * scale
    log_probs = logits - np.logaddexp.reduce(logits, axis=1, keepdims=True)
    return graph.compile(), relative_log_probs(log_probs, vocab)


@pytest.mark.skipif(spotter._viterbi is None, reason="native module not built")
@pytest.mark.parametrize("gate", [2.5, 6.0])
def test_native_live_matches_numpy_live(vocab, gate):
    for seed in range(8):
        compiled, (rel_tokens, rel_blank) = _graph_and_frames(vocab, seed, scale=1.5)
        native = spotter.search(compiled, rel_tokens, rel_blank, gate)
        reference = spotter.viterbi_numpy(compiled, rel_tokens, rel_blank, gate)
        np.testing.assert_array_equal(native[0], reference[0])
        finite = np.isfinite(reference[0])
        for got, want in zip(native[1:3], reference[1:]):
            np.testing.assert_array_equal(got[finite], want[finite])


@pytest.mark.parametrize("gate", [2.5, 6.0])
def test_live_search_keeps_everything_within_the_gate(vocab, gate):
    kept = 0
    for seed in range(8):
        compiled, (rel_tokens, rel_blank) = _graph_and_frames(vocab, seed, scale=1.5)
        full = spotter.viterbi(compiled, rel_tokens, rel_blank)
        live = spotter.viterbi(compiled, rel_tokens, rel_blank, gate)
        within = -full[0] <= gate * np.maximum(full[2], 1)
        kept += int(within.sum())
        for got, want in zip(live, full):
            np.testing.assert_array_equal(got[within], want[within])
    assert kept > 0


def test_punctuation_with_the_word_marker():
    # NeMo's German model writes "▁." where Parakeet's English writes ".":
    # punctuation either way, not speech.
    from adapt_ctc.acoustic import Vocabulary

    vocab = Vocabulary(dict(enumerate(["<unk>", "▁licht", "▁.", ".", "▁,", "▁", "<blk>"])))
    assert [vocab.id2piece[i] for i in vocab.punctuation_ids] == ["▁.", ".", "▁,"]
    assert vocab.is_punctuation(2) and not vocab.is_punctuation(1) and not vocab.is_punctuation(5)
    assert vocab.space_id == 5 and ("", True) not in vocab.pieces
