"""Commands that follow the library (voice_labs/library.py): the library's
version with their own settings on top, saved as what differs; copies from
before, made to follow it; taking control."""

from __future__ import annotations

import json

import pytest

from adapt_ctc.config import ConfigError
from voice_labs import languages
from voice_labs.library import base, migrate, resolve, settings
from voice_labs.store import VERSION, Store, compile_store


@pytest.fixture(autouse=True)
def english():
    yield languages.use("en")


def test_the_librarys_version_with_its_settings():
    theirs = base("lights/turn_on_here")
    spec = resolve("lights", {"from": "lights/turn_on_here", "satellites": ["sat1"], "verbosity": "brief", "examples": ["lights on"]})
    assert spec["require"] == theirs["require"] and spec["title"] == theirs["title"] and spec["library"] == "lights"
    assert spec["satellites"] == ["sat1"] and spec["verbosity"] == "brief"
    assert spec["examples"] == [*(theirs.get("examples") or []), "lights on"]  # beside the library's
    # Its own implied target in place of the library's; null: none.
    shopping = resolve("s", {"from": "todo/shopping_list_add", "slots": {"entity": ["todo.groceries"]}})
    assert shopping["slots"] == {"entity": ["todo.groceries"]}
    assert "verbosity" not in resolve("s", {"from": "todo/shopping_list_add", "verbosity": None})
    with pytest.raises(ConfigError, match="require can't be changed while it follows the library"):
        resolve("x", {"from": "lights/turn_on_here", "require": [["lights"]]})
    with pytest.raises(ConfigError, match="lights/nope is not in the library"):
        resolve("x", {"from": "lights/nope"})
    # One moved to another section is found there: Basics was split up.
    assert resolve("w", {"from": "basic/weather"})["library"] == "weather"
    assert resolve("l", {"from": "basic/turn_on_here"})["library"] == "lights"
    assert resolve("v", {"from": "music/own_volume_up"})["library"] == "satellites"


def test_saved_as_what_differs():
    theirs = base("timers/start_timer")
    page = {"from": "timers/start_timer", "satellites": None, "disabled": None, "verbosity": "verbose",
            "fallback": None, "title": theirs["title"], "icon": theirs.get("icon"), "slots": {}, "examples": []}
    assert settings(page, theirs) == {"from": "timers/start_timer", "verbosity": "verbose"}


def test_copies_from_before_follow_the_library():
    theirs = base("lights/turn_on_here")
    document = {"commands": {
        # As added then: the library's keys, and what was set since.
        "turn_on_here": {**theirs, "examples": [*(theirs.get("examples") or []), "lights on"], "satellites": ["sat1"]},
        "start_timer": {**base("timers/start_timer"), "verbosity": "verbose"},
        # An older version: its keys are the library's now.
        "shopping_list_add": {"title": "Add to the shopping list", "library": "basic", "description": "old",
                              "require": [["add"], ["list"]], "response": "Added."},
        "what_time_de": {**base("time/what_time", "de"), "language": "de"},
        "start_timer_2": {**base("timers/start_timer")},
        "gone": {"description": "x", "examples": ["x"], "library": "basic"},
        "mine": {"description": "tell the time", "examples": ["what time is it"], "response": "Noon."},
    }}
    changed = migrate(document)
    commands = document["commands"]
    assert sorted(changed) == ["shopping_list_add", "start_timer", "start_timer_2", "turn_on_here", "what_time_de"]
    assert commands["turn_on_here"] == {"from": "lights/turn_on_here", "satellites": ["sat1"], "examples": ["lights on"]}
    assert commands["start_timer"] == {"from": "timers/start_timer", "verbosity": "verbose"}
    # One from a section since split up: from where it is now.
    assert commands["shopping_list_add"] == {"from": "todo/shopping_list_add"}
    assert commands["what_time_de"] == {"from": "time/what_time", "language": "de"}
    assert commands["start_timer_2"] == {"from": "timers/start_timer"}
    assert commands["gone"]["library"] == "basic" and commands["mine"]["response"] == "Noon."  # left as they were
    compiled = compile_store(document)
    assert compiled.commands["shopping_list_add"].behavior.home == "shopping_list"
    assert compiled.hidden == {"what_time_de": "de"}


def test_the_store_moves_once_and_keeps_the_file_as_it_was(tmp_path):
    before = {"commands": {"start_timer": {**base("timers/start_timer"), "verbosity": "verbose"}}}
    (tmp_path / "commands.json").write_text(json.dumps(before))
    store = Store(tmp_path)
    assert store.document()["commands"]["start_timer"] == {"from": "timers/start_timer", "verbosity": "verbose"}
    assert json.loads((tmp_path / "commands.json").read_text())["version"] == VERSION
    assert json.loads((tmp_path / "commands.before-library.json").read_text()) == before
    compiled = store.load()
    assert compiled.commands["start_timer"].behavior.verbosity == "verbose"
    # Taking control: a copy, which the library's changes no longer reach.
    copy = resolve("start_timer", store.document()["commands"]["start_timer"])
    store.put_command("start_timer", {**copy, "response": "Started."})
    assert "from" not in Store(tmp_path).document()["commands"]["start_timer"]
    # A new store is as it is now.
    assert Store(tmp_path / "new").document()["version"] == VERSION
