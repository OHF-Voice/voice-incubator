"""The second opinion on a rejected wake word (voice_incubator/wake_ratio.py),
on the real CTC model."""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest

from voice_incubator.wake_ratio import RatioVerdict, WakeRatio
from voice_incubator.wake_words import RATIO_RIVALS

ROOT = Path(__file__).parent.parent
CTC = ROOT / "models/speech-to-phrase/stt_en_parakeet_tdt_ctc_110m-int8"
WAV = ROOT / "tests/okay_nabu_turn_on_the_lights.wav"


@pytest.fixture(name="model", scope="module")
def fixture_model():
    if not (CTC / "model.onnx").exists():
        pytest.skip("no CTC model")
    from adapt_ctc.acoustic import CtcModel  # pylint: disable=import-outside-toplevel

    return CtcModel(CTC, num_threads=2)


@pytest.fixture(name="window", scope="module")
def fixture_window() -> bytes:
    """The 1.5 s pre-roll: "okay nabu" and the start of the command."""
    with wave.open(str(WAV), "rb") as handle:
        audio = np.frombuffer(handle.readframes(handle.getnframes()), dtype=np.int16)
    return audio[:24000].tobytes()


def _ratio(model, **kwargs) -> WakeRatio:
    ratio = WakeRatio(**kwargs)
    ratio.train(model)
    return ratio


def test_rivals_leave_a_real_wake_word_overturned(model, window):
    ratio = _ratio(model, rivals=RATIO_RIVALS["okay_nabu"])
    verdict = ratio.judge("last", window)
    assert verdict.overturned and verdict.kept_by is None
    assert verdict.rival in RATIO_RIVALS["okay_nabu"]
    assert verdict.rival_margin is not None and verdict.rival_margin < ratio.rival_margin


def test_a_rival_keeps_a_rejection_the_score_would_overturn(model, window):
    # Every rival is closer than a margin this low.
    ratio = _ratio(model, rivals=["okay natalie", "okay taboo"], rival_margin=-10.0)
    verdict = ratio.judge("prefix", window)
    assert verdict.score is not None and verdict.score <= ratio.threshold
    assert not verdict.overturned and verdict.kept_by == "rival"
    assert verdict.rival in ("okay natalie", "okay taboo")
    assert ratio.would_accept("prefix", window) == (False, verdict.score)


def test_a_poor_score_is_kept_by_the_score(model, window):
    ratio = _ratio(model, rivals=["okay natalie"], threshold=-10.0, rival_margin=-10.0)
    verdict = ratio.judge("last", window)
    assert not verdict.overturned and verdict.kept_by == "score"


def test_without_rivals_only_the_score_decides(model, window):
    verdict = _ratio(model).judge("last", window)
    assert verdict.overturned and verdict.rival is None and verdict.rival_margin is None


def test_a_confusable_rejection_is_not_scored(model, window):
    ratio = _ratio(model, rivals=RATIO_RIVALS["okay_nabu"])
    assert ratio.judge("confusable", window) == RatioVerdict(False)
