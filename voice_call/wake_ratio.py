"""A second opinion on a rejected wake word, from the command model.

The Kaldi verifier makes three separate decisions, and they do not fail
together. Its `confusable` test is the one that needs a pronunciation lexicon --
telling "okay nabu" from "okay now" -- and nothing here can do that job:
Parakeet has no word "nabu" and prefers "okay now" for most real wake words.

Its `last` and `prefix` tests are a different question -- is this the wake word
or is it just speech -- and there the command model is strong where Kaldi is
weakest. So this is a second accept path for `last` and `prefix` rejections
only. A `confusable` rejection stands.

The score is the per-frame log-probability the window gives up when it is
explained as "okay nabu" (blank before and after) instead of as a loop over the
commonest short words, both measured from the all-blank path through the same
CTC log-probabilities:

    score = (filler gain - wake gain) / frames

Low means "okay nabu" explains the window nearly as well as ordinary speech
does. Three choices carry the separation, all measured
(scripts/calibrate_wake_ratio.py, README "A second opinion on a rejected wake
word"):

* The wake phrase is spelled in **lowercase pieces only**, each word whole
  (first piece starts the word, no break inside). Allowing the capitalised
  pieces -- `▁Okay` is one -- recovers fewer wake words at every false-accept
  rate measured. It is also what the speech-to-phrase grammar did.
* The filler is the **words spelled by the first 255 pieces** of the
  vocabulary (SentencePiece numbers them in merge order, so these are the
  commonest fragments), in lowercase pieces. Against the greedy path instead,
  a wake word the model reads as "Okay now" -- many of them -- scores as a poor
  fit however clearly it was said.
* **"okay now" is not a spelling** of the wake word, though the model prefers
  it for most of them. Kaldi tests `prefix` and `last` before `confusable`, so
  a spoken "okay now" can arrive here as a `last` rejection; with "okay now" as
  a spelling, 98% of those were overturned.

Nothing here is specific to a recognizer beyond adapt-ctc's ``CtcModel``: the
graph is built from ``model.vocab`` and scored with adapt-ctc's own Viterbi.
"""

from __future__ import annotations

import logging

import numpy as np

_LOGGER = logging.getLogger(__name__)

# Rejections this is allowed to overturn. `confusable` is deliberately absent.
OVERRIDABLE = frozenset({"last", "prefix"})

# Per-frame nats. At or below this the rejection is overturned.
#
# Calibrated on the rejections it exists to overturn -- true wake words the
# Kaldi thresholds refused on `last` or `prefix` (Voice PE taps, cutoff 0.2,
# adaptive tail) -- and on windows that must stay refused: speech and music,
# the command said right after the wake word (of those Kaldi rejected on
# last/prefix), and TTS confusables the detector fired on and Kaldi rejected
# on last/prefix:
#
#   threshold  jenn  mike  natalie  oliver  mike*  speech/music  commands  confusables
#        1.20 81.3% 89.2%    90.7%   92.9%  83.3%         0.17%     0.19%        14.8%
#        1.30 89.8% 94.6%    93.5%   97.1%  90.3%         0.69%     0.23%        27.5%
#        1.40 95.8% 96.5%    95.9%   99.1%  94.9%         2.06%     0.29%        43.4%
#   (old 1.20 82.0% 92.5%    94.2%   96.6%  87.5%         0.83%     1.20%        54.0%)
#
# (* an earlier session, held out from choosing the score.) At 1.3 it takes
# fewer false accepts than the speech-to-phrase ratio at 1.2 on every kind of
# negative and recovers as many or more for every speaker but one.
DEFAULT_THRESHOLD = 1.3

FILLER_PIECES = 255
"""The filler is the words spelled by vocabulary pieces 1..N. 63 (what the
speech-to-phrase version used) and 1023 both separate less well."""

LANGUAGE = "en"


def lowercase_vocabulary(vocab):
    """The same vocabulary with every piece that has a capital letter removed.

    Token ids are unchanged, so graphs built from it index the model's own
    log-probabilities.
    """
    from adapt_ctc.acoustic import Vocabulary

    lower = Vocabulary({i: p for i, p in vocab.id2piece.items() if p == p.lower()})
    if lower.blank_id != vocab.blank_id or lower.size != vocab.size:
        raise ValueError("Vocabulary has no lowercase blank piece")
    return lower


def strict_graph(vocab, phrases):
    """Each phrase as whole words: first piece starts the word, no breaks inside."""
    from adapt_ctc.graph import Meaning, PhraseGraph
    from adapt_ctc.text import spoken_words

    graph = PhraseGraph(vocab)
    for phrase in phrases:
        for words in spoken_words(phrase, LANGUAGE):
            graph.add(words, Meaning("keyword", " ".join(words)))
    return graph


def filler_tokens(vocab, pieces: int = FILLER_PIECES) -> np.ndarray:
    """Token ids of the words spelled by vocabulary pieces 1..``pieces``,
    themselves spelled in lowercase pieces as whole words."""
    from adapt_ctc.acoustic import SPM_SPACE

    words = {
        vocab.id2piece[i].lstrip(SPM_SPACE).lower()
        for i in range(1, pieces + 1)
        if i in vocab.id2piece and i != vocab.blank_id
    }
    graph = strict_graph(lowercase_vocabulary(vocab), sorted(w for w in words if w.isalpha()))
    return np.unique(graph.compile().arc_token).astype(np.int64)


def gain_over_blank(log_probs: np.ndarray, vocab) -> np.ndarray:
    """``[T, V]`` log-probability of each piece minus blank's (punctuation
    counts as blank), so that a path's sum is its gain over all-blank."""
    from adapt_ctc.spotter import relative_log_probs

    rel_tokens, rel_blank = relative_log_probs(log_probs, vocab)
    return np.ascontiguousarray(rel_tokens - rel_blank[:, None])


def phrase_gain(graph, over_blank: np.ndarray) -> float:
    """Best gain over all-blank of the window explained as blank, a phrase
    of the graph, blank: frames outside the phrase cost nothing here."""
    from adapt_ctc.spotter import viterbi

    frames = over_blank.shape[0]
    score, _, _ = viterbi(graph, over_blank, np.zeros(frames, dtype=np.float32))
    return float(score.max())


def loop_gain(tokens: np.ndarray | None, over_blank: np.ndarray) -> float:
    """Gain over all-blank of a free loop over ``tokens`` (None: every piece,
    the greedy path)."""
    best = over_blank if tokens is None else over_blank[:, tokens]
    return float(np.maximum(best.max(axis=1), 0.0).sum())


class WakeRatio:
    """How much better than ordinary speech "okay nabu" explains a window."""

    def __init__(self, wake_word: str = "okay nabu", threshold: float | None = None):
        self.wake_word = wake_word
        self.threshold = DEFAULT_THRESHOLD if threshold is None else threshold
        self._model = None
        self._wake = None
        self._filler: np.ndarray | None = None

    def train(self, model) -> None:
        """Build the wake graph and the filler from an adapt-ctc ``CtcModel``."""
        vocab = getattr(model, "vocab", None)
        if vocab is None:
            raise TypeError("WakeRatio needs an adapt_ctc CtcModel (with .vocab)")
        wake = strict_graph(lowercase_vocabulary(vocab), [self.wake_word])
        if wake.unspeakable:
            raise ValueError(f"Cannot spell {self.wake_word!r} with this vocabulary")

        # A loop over the commonest short words: the window's cost when nothing
        # in particular is required of it. Raw wake cost on its own says
        # nothing -- it measures fit to the path the graph forced, not whether
        # a better path existed.
        self._filler = filler_tokens(vocab)
        self._wake = wake.compile()
        self._model = model
        _LOGGER.info(
            "Wake ratio ready (%r, threshold %.2f, %d filler pieces)",
            self.wake_word,
            self.threshold,
            len(self._filler),
        )

    @property
    def available(self) -> bool:
        return self._wake is not None and self._filler is not None

    def score(self, pcm: bytes) -> float | None:
        """The per-frame score for 16 kHz mono int16 audio, or None."""
        if not self.available:
            return None
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        if audio.size == 0:
            return None
        log_probs, _ = self._model.log_probs(audio)
        return self.score_log_probs(log_probs)

    def score_log_probs(self, log_probs: np.ndarray) -> float | None:
        """The same score from ``[T, V]`` log-probabilities already computed."""
        if not self.available or len(log_probs) == 0:
            return None
        over_blank = gain_over_blank(log_probs, self._model.vocab)
        wake = phrase_gain(self._wake, over_blank)
        if not np.isfinite(wake):
            return None  # too short to hold the phrase
        return (loop_gain(self._filler, over_blank) - wake) / over_blank.shape[0]

    def would_accept(self, rejected_by: str | None, pcm: bytes) -> tuple[bool, float | None]:
        """Whether this rejection should be overturned, and the score behind it."""
        if rejected_by not in OVERRIDABLE or not self.available:
            return False, None
        score = self.score(pcm)
        if score is None:
            return False, None
        return score <= self.threshold, score
