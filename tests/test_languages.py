"""Languages: the packs, and what the app takes from its own."""

from __future__ import annotations

import inspect
import re
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


DATA = (str, dict, list, tuple, set, re.Pattern)


def public(module) -> dict[str, object]:
    """Its sentences and what makes them: not what it imports."""
    return {
        name: value
        for name, value in vars(module).items()
        if not name.startswith("_")
        and (isinstance(value, DATA) or getattr(value, "__module__", None) == module.__name__)
    }


@pytest.mark.parametrize("module", ["speech", "reading"])
@pytest.mark.parametrize("code", languages.codes())
def test_every_language_says_and_reads_what_english_does(code, module):
    english = public(getattr(languages.load("en"), module))
    own = public(getattr(languages.load(code), module))
    assert set(english) <= set(own), f"{code} lacks {sorted(set(english) - set(own))}"
    for name, value in english.items():
        if callable(value) and not isinstance(value, re.Pattern):
            assert inspect.signature(own[name]).parameters.keys() == inspect.signature(value).parameters.keys(), name
        elif isinstance(value, dict):
            assert set(own[name]) == set(value), name
        else:
            assert type(own[name]) is type(value) and own[name], name


def test_what_is_said_is_the_apps_language(english, monkeypatch):
    assert languages.speech() is english.speech
    assert english.speech.place([("area", "Kitchen")], to=True) == "to the Kitchen"
    assert english.speech.place([("floor", "First Floor"), ("floor", "upstairs")]) == "on the First Floor and upstairs"
    monkeypatch.setattr(languages, "_current", replace(english, code="xx"))
    with pytest.raises(ModuleNotFoundError):
        languages.speech()
