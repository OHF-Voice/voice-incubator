"""A second opinion on a rejected wake word, from the model already loaded.

The Kaldi verifier makes three separate decisions, and they do not fail
together. Its `confusable` test is the one that needs a pronunciation lexicon --
telling "okay nabu" from "okay now" -- and nothing else here can do that job:
measured on true wake words, Parakeet prefers "okay now" on 83-92% of them,
because it has no pronunciation for `nabu` and an overwhelming prior for a
common word.

Its `last` and `prefix` tests are a different question -- is this the wake word
or is it just speech -- and there Parakeet is strong where Kaldi is weakest. On
the aligned corpus, the per-frame cost of decoding under "okay nabu" minus the
cost under a free loop separates wake words from the command said right after
them with no overlap for any speaker, including the two the Kaldi thresholds
reject most:

    speaker   wake p90   command p10   separation
    jenn         0.200         2.045       +1.845
    mike         1.130         1.661       +0.532
    natalie      1.031         1.871       +0.841
    oliver       1.186         1.729       +0.543

So this is a second accept path for `last` and `prefix` rejections only. A
`confusable` rejection stands: that is Kaldi being right about the thing it is
for, and overriding it would trade away the guard.
"""

from __future__ import annotations

import logging

import numpy as np

_LOGGER = logging.getLogger(__name__)

# Rejections this is allowed to overturn. `confusable` is deliberately absent.
OVERRIDABLE = frozenset({"last", "prefix"})

# Per-frame cost of the wake grammar minus a free loop. Below this the audio is
# explained by "okay nabu" nearly as well as by anything at all.
#
# Calibrated against the rejections it exists to overturn -- true wake words the
# Kaldi thresholds refused on `last` or `prefix` -- and against 1.5 s windows of
# the speech-and-music recording:
#
#     threshold   jenn    mike  natalie  oliver   speech/music accepted
#          1.00  84.6%   88.9%    88.6%   85.0%                   0.0%
#          1.20 100.0%  100.0%    98.6%   90.0%                   2.2%
#          1.40 100.0%  100.0%   100.0%  100.0%                   6.3%
#
# 1.2 recovers nearly all of them. The 2.2% is a worst case that cannot arise on
# that material anyway: this path only runs after the on-device detector has
# fired, and over nine channel-hours of it the detector never did.
DEFAULT_THRESHOLD = 1.2

# Wide enough that the free loop is not pruned into agreeing with the grammar,
# which would collapse the ratio towards zero for everything.
BEAM = 12.0


class WakeRatio:
    """How much better than nothing "okay nabu" explains a window of audio."""

    def __init__(self, wake_word: str = "okay nabu", threshold: float | None = None):
        self.wake_word = wake_word
        self.threshold = DEFAULT_THRESHOLD if threshold is None else threshold
        self._wake = None
        self._free = None
        self._model = None

    def train(self, model) -> None:
        """Build the two grammars against a loaded recognizer's tokenizer."""
        from speech_to_phrase.grammar import Grammar
        from speech_to_phrase.templates import templates_to_fst

        tokenizer = model.tokenizer
        self._model = model
        self._wake = Grammar.build(
            tokenizer.num_tokens,
            tokenizer.blank_id,
            templates_to_fst([self.wake_word], tokenizer=tokenizer, locale="en"),
        )
        # A loop over single tokens: the cost of the audio when nothing is
        # required of it. Raw grammar cost on its own says nothing -- it
        # measures fit to the path the grammar forced, not whether a better
        # path existed.
        self._free = Grammar.build(
            tokenizer.num_tokens,
            tokenizer.blank_id,
            templates_to_fst(
                ["{any}"],
                tokenizer=tokenizer,
                locale="en",
                list_values={
                    "any": [tokenizer.ids_to_text([i]) for i in range(1, 64)]
                },
            ),
        )
        _LOGGER.info("Wake ratio ready (%r, threshold %.2f)", self.wake_word, self.threshold)

    @property
    def available(self) -> bool:
        return self._wake is not None and self._free is not None

    def score(self, pcm: bytes) -> float | None:
        """The per-frame ratio for this window, or None if it cannot be had."""
        if not self.available:
            return None
        from speech_to_phrase.audio import load_audio

        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        log_probs = self._model.log_probs(load_audio(audio))
        if len(log_probs) == 0:
            return None
        blank = self._model.tokenizer.blank_id
        _, wake_cost, _ = self._wake.decode(log_probs, blank, beam=BEAM)
        _, free_cost, _ = self._free.decode(log_probs, blank, beam=BEAM)
        if wake_cost is None or free_cost is None:
            return None
        return float((wake_cost - free_cost) / len(log_probs))

    def would_accept(self, rejected_by: str | None, pcm: bytes) -> tuple[bool, float | None]:
        """Whether this rejection should be overturned, and the score behind it."""
        if rejected_by not in OVERRIDABLE or not self.available:
            return False, None
        score = self.score(pcm)
        if score is None:
            return False, None
        return score <= self.threshold, score
