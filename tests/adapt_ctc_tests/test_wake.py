"""Wake word verification on scripted log-probabilities, and the config section."""

from dataclasses import replace

import numpy as np
import pytest

from adapt_ctc import spotter
from adapt_ctc.config import ConfigError, WakeConfig, parse_config
from adapt_ctc.graph import Meaning, PhraseGraph
from adapt_ctc.wake import WakeVerifier

from .conftest import toy_vocab, utterance

WAKE = WakeConfig(
    phrases=("okay nabu", "okay now"),
    confusables=("okay natalie",),
    max_cost_per_token=2.0,
    min_margin=-1.0,
)


def verify(script, **changes):
    vocab = toy_vocab()
    config = replace(WAKE, **changes)
    return WakeVerifier(config, vocab).verify_log_probs(utterance(vocab, script), 0.08)


def test_accepts_the_wake_word():
    result = verify(["okay", "nabu", "turn", "on", "the", "lights"])
    assert result.accepted
    assert result.phrase == "okay nabu"
    assert (result.start, result.end) == (1, 3)
    assert result.margin > 0


def test_rejects_a_confusable():
    result = verify(["okay", "natalie", "turn", "on"])
    assert not result.accepted
    assert "'okay natalie' is better supported" in result.reason


def test_rejects_without_the_wake_word():
    result = verify(["what", "weather", "tomorrow"])
    assert not result.accepted
    assert "fits poorly" in result.reason


def test_split_follows_the_latest_good_spelling():
    # "now" fits this frame best, but "nabu" -- nearly as good -- ends later,
    # where the model heard the rest of the word.
    result = verify(["okay", {"now": 0.6, "nabu": 0.3}, {"nabu": 0.5, "now": 0.1}, "turn", "on"])
    assert result.accepted
    assert result.end == 5


def test_wake_config():
    config = parse_config(
        {
            "intents": {"X": {"require": ["a"]}},
            "wake": {"phrases": ["okay nabu"], "confusables": ["okay natalie"], "min_margin": -0.5},
        }
    )
    assert config.wake.phrases == ("okay nabu",)
    assert config.wake.min_margin == -0.5
    assert parse_config({"intents": {"X": {"require": ["a"]}}}).wake is None

    with pytest.raises(ConfigError, match="both phrases and confusables"):
        parse_config(
            {"intents": {"X": {"require": ["a"]}}, "wake": {"phrases": ["okay now"], "confusables": ["okay now"]}}
        )
    with pytest.raises(ConfigError, match="at least one phrase"):
        parse_config({"intents": {"X": {"require": ["a"]}}, "wake": {"phrases": []}})


@pytest.mark.parametrize("scoring", ["sum", "best"])
def test_both_scorings_decide_the_clear_cases(scoring):
    assert verify(["okay", "nabu", "turn", "on"], scoring=scoring).accepted
    assert not verify(["okay", "natalie", "turn", "on"], scoring=scoring).accepted


def test_summed_evidence_pools_spellings():
    # The model splits the word between two spellings: each alone is a weak
    # best path, together they are the wake word.
    hedge = {"nabu": 0.45, "now": 0.4, "natalie": 0.15}
    summed = verify(["okay", hedge, "turn", "on"])
    best = verify(["okay", hedge, "turn", "on"], scoring="best")
    assert summed.margin > best.margin
    assert summed.evidence > -summed.cost


def test_forward_is_never_below_the_best_path():
    vocab = toy_vocab()
    graph = PhraseGraph(vocab)
    for words, flexible in [(["turn", "on"], False), (["eco", "bee"], True), (["seventy", "two"], False)]:
        graph.add(words, Meaning("keyword", " ".join(words)), flexible=flexible)
    compiled = graph.compile()
    rng = np.random.default_rng(3)
    for _ in range(5):
        logits = rng.normal(size=(20, vocab.size)) * 2
        log_probs = (logits - np.logaddexp.reduce(logits, axis=1, keepdims=True)).astype(np.float32)
        rel_tokens, rel_blank = spotter.relative_log_probs(log_probs, vocab)
        summed = spotter.forward_numpy(compiled, rel_tokens, rel_blank)
        best, _, _ = spotter.viterbi(compiled, rel_tokens, rel_blank)
        finite = np.isfinite(best)
        assert np.array_equal(np.isfinite(summed), finite)
        assert (summed[finite] >= best[finite] - 1e-4).all()


def test_forward_equals_the_best_path_when_there_is_only_one():
    vocab = toy_vocab()
    graph = PhraseGraph(vocab)
    graph.add(["turn"], Meaning("keyword", "turn"))
    compiled = graph.compile()
    log_probs = utterance(vocab, ["turn"], blank=1 - 1e-6)
    rel_tokens, rel_blank = spotter.relative_log_probs(log_probs, vocab)
    summed = spotter.forward_numpy(compiled, rel_tokens, rel_blank)
    best, _, _ = spotter.viterbi(compiled, rel_tokens, rel_blank)
    assert summed.max() == pytest.approx(best.max(), abs=1e-3)


def test_scoring_config():
    base = {"intents": {"X": {"require": ["a"]}}}
    wake = parse_config({**base, "wake": {"phrases": ["okay nabu"]}}).wake
    assert (wake.scoring, wake.min_margin, wake.max_cost_per_token) == ("sum", -0.5, None)
    wake = parse_config(
        {**base, "wake": {"phrases": ["okay nabu"], "scoring": "best", "max_cost_per_token": 2}}
    ).wake
    assert (wake.scoring, wake.max_cost_per_token) == ("best", 2.0)
    with pytest.raises(ConfigError, match="'sum' or 'best'"):
        parse_config({**base, "wake": {"phrases": ["okay nabu"], "scoring": "max"}})
