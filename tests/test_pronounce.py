"""Pronunciations for the wake word verifier: espeak's guesses in the model's
phones, and the page's changes merged over wake.yaml."""

from __future__ import annotations

from pathlib import Path

import pytest

from voice_labs.pronounce import guess, model_phones, to_phones
from voice_labs.wake import WakeVerifier
from voice_labs.wake import ConfigError as WakeConfigError

ROOT = Path(__file__).resolve().parent.parent
KALDI = ROOT / "models/wake-kaldi-models/en_US-rhasspy"

needs_kaldi = pytest.mark.skipif(not (KALDI / "lexicon.db").exists(), reason="needs the wake-kaldi model")


def test_to_phones():
    # Stress moves onto the vowel; long marks go; diphthongs and affricates
    # are one phone; the flap is a t; a doubled consonant is said once.
    assert to_phones("nˈɑːbuː") == ["n", "ˈɑ", "b", "u"]
    assert to_phones("tˈeɪɾɚ") == ["t", "ˈeɪ", "t", "ɚ"]
    assert to_phones("dʒˈɑːɹvɪs") == ["d͡ʒ", "ˈɑ", "ɹ", "v", "ɪ", "s"]
    assert to_phones("ɹˈʊɹɹəl") == ["ɹ", "ˈʊ", "ɹ", "ə", "l"]
    assert to_phones("bˈɜːɹd") == ["b", "ˈɚ", "d"]
    assert to_phones("nˈɑː¡") is None


def test_guess():
    nabu = guess("nabu")
    if nabu is None:
        pytest.skip("no espeak-ng (Piper)")
    assert nabu == "n ˈɑ b u"
    assert guess("tater") == "t ˈeɪ t ɚ"


@needs_kaldi
def test_model_phones():
    phones = dict((p, (w, pron)) for p, w, pron in model_phones(KALDI))
    assert phones["d͡ʒ"] == ("jobs", "d͡ʒ ˈɑ b z")


@needs_kaldi
def test_build(tmp_path):
    verifier = WakeVerifier(ROOT / "wake.yaml", train_dir=tmp_path)
    assert verifier.config.train_dir == tmp_path
    assert verifier.default_thresholds("hey_mycroft")["theta_prefix"] == 1.2
    assert verifier.default_thresholds()["theta_prefix"] == 0.85

    tuning = {
        "okay_nabu": {
            "confusables": ["okay nabil", "okay nadine"],
            "pronunciations": {"nabil": "n ə b ˈi l"},
            "thresholds": {"theta_competitor": 0.2},
        },
        "hey_tater": {"confusables": ["hey potato"]},
    }
    config = verifier.build(tuning, {"hey_tater": "hey tater", "hey_marcel": "hey marcel"})
    nabu = config.wake_words["okay_nabu"]
    assert [p.text for p in nabu.confusables] == ["okay nabil", "okay nadine"]
    assert nabu.thresholds.theta_competitor == 0.2 and nabu.thresholds.theta_last == 0.35
    # Shipped pronunciations no confusable uses any more are left out.
    assert "nabu" in nabu.lexicon and "dabu" not in nabu.lexicon
    # A custom word with confusables is verified, under its id alone.
    assert config.wake_words["hey_tater"].names == ("hey_tater",)
    assert "hey_marcel" not in config.wake_words

    with pytest.raises(WakeConfigError, match="glorbo"):
        verifier.build({"okay_nabu": {"confusables": ["okay glorbo"]}}, {})
    lexicon = verifier.lexicon()
    try:
        assert verifier.unknown_words(["okay glorbo", "okay /n ˈæ b u/", "hey nabu"], {"nabu": "n ˈæ b u"}, lexicon) == ["glorbo"]
    finally:
        lexicon.close()
