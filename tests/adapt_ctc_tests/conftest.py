"""Shared helpers: a toy vocabulary and log-probabilities built to order."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

from adapt_ctc.acoustic import SPM_SPACE, Vocabulary
from adapt_ctc.cli import DEFAULT_MODEL

FIXTURES = Path(__file__).parent / "fixtures"

# Whole words, so a test can say what was "heard" one word per piece, plus a
# few subword pieces for segmentation and word-break tests.
WORDS = """
turn on off switch the a lights light in and kitchen bedroom den living room
set brightness to percent fifty fifteen five seventy two one hundred half
timer for minutes seconds minute hour an play music what weather tomorrow please
okay nabu natalie now
""".split()
SUBWORDS = ["eco", "e", "co", "bee", "b", "ee", "o", "c", "s"]


def toy_vocab() -> Vocabulary:
    pieces = ["<unk>"]
    pieces += [SPM_SPACE + w for w in WORDS]
    pieces += [SPM_SPACE + w for w in SUBWORDS if SPM_SPACE + w not in pieces]
    pieces += SUBWORDS + [SPM_SPACE, ".", "'"]
    pieces.append("<blk>")
    return Vocabulary(dict(enumerate(pieces)))


@pytest.fixture
def vocab() -> Vocabulary:
    return toy_vocab()


def utterance(vocab: Vocabulary, script: list, blank: float = 0.9) -> np.ndarray:
    """Log-probabilities for a scripted utterance.

    Each entry is a word (the frame puts ``blank`` probability on that word's
    piece) or a dict of ``{word: probability}`` for a frame the model hedges
    on. Words are word-start pieces; ``"+co"`` names the continuation piece. Every word frame is followed by a blank frame; the utterance is padded
    with silence.
    """
    ids = {p: i for i, p in vocab.id2piece.items()}

    def piece(word: str) -> str:
        if word.startswith("+"):
            return word[1:]
        return SPM_SPACE + word if SPM_SPACE + word in ids else word

    frames = [{"<blk>": 1.0}]
    for entry in script:
        if isinstance(entry, dict):
            frames.append({piece(w): p for w, p in entry.items()})
        else:
            frames.append({piece(entry): blank})
        frames.append({"<blk>": 1.0})
    frames.append({"<blk>": 1.0})

    log_probs = np.full((len(frames), vocab.size), np.log(1e-6), dtype=np.float32)
    for t, frame in enumerate(frames):
        rest = 1.0 - sum(frame.values())
        for name, prob in frame.items():
            log_probs[t, ids[name]] = np.log(prob)
        if rest > 1e-6:
            log_probs[t, vocab.blank_id] = np.logaddexp(
                log_probs[t, vocab.blank_id], np.log(rest)
            )
    return log_probs


def model_dir() -> Path | None:
    path = Path(os.environ.get("ADAPT_CTC_MODEL", DEFAULT_MODEL))
    return path if (path / "tokens.txt").exists() else None
