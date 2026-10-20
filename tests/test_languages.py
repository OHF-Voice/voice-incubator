"""Languages: the packs, and what the app takes from its own."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from adapt_ctc.config import ConfigError
from voice_call import languages, skills
from voice_call.commands import base_config
from voice_call.server import tdt_choice
from voice_call.store import NEW_FILE, compile_store

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(name="english")
def fixture_english():
    yield languages.use("en")
    languages.use("en")


def test_packs(english):
    assert "en" in languages.codes() and languages.load("en") is english
    with pytest.raises(ValueError, match="no language 'xx'"):
        languages.load("xx")
    assert english.library.is_dir() and sorted(p.name for p in english.library.glob("*.yaml"))
    assert english.ctc_dir(ROOT).name.startswith("stt_en_parakeet_tdt_ctc_110m")
    # Its matcher config is the base every home's commands build on.
    config = base_config()
    assert config["language"] == "en" and "the" in config["filler"] and config["settings"]["examples_first"]


def test_only_the_languages_skills(english, monkeypatch):
    assert list(skills.available()) == ["spelling", "dates", "clock", "units", "arithmetic"]
    monkeypatch.setattr(languages, "_current", replace(english, skills=("units",)))
    assert list(skills.available()) == ["units"]
    assert skills.home_skills() == {"units"} and skills.answer("how do you spell cat") is None
    assert skills.answer("how many cups are in a quart").skill == "units"
    monkeypatch.setattr(languages, "_current", replace(english, skills=()))
    assert skills.enabled() == frozenset() and skills.answer("how many cups are in a quart") is None


def test_the_commands_language(english):
    assert compile_store({}).language == "en"  # none said: English
    assert compile_store({"language": "de"}).language == "de"
    with pytest.raises(ConfigError, match="language"):
        compile_store({"language": "German"})
    assert "language: en\n" in NEW_FILE.format(language="en")


def test_the_transducer_choice():
    # "en", from before languages had packs, is the language's own.
    assert tdt_choice("en") == tdt_choice(None) == tdt_choice("own") == "own"
    assert tdt_choice("multilingual") == "multilingual" and tdt_choice("nope") == "own"
