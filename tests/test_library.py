"""The library: every command valid, ids unique, icons shipped."""

import json
import re
from pathlib import Path

import pytest

from adapt_ctc.config import ConfigError
from voice_call.commands import parse_command
from voice_call.web import load_library

WEB = Path(__file__).resolve().parent.parent / "voice_call/web"


def shipped_icons() -> set[str]:
    text = (WEB / "icons.js").read_text()
    return set(json.loads(re.search(r"const ICONS = (\{.*?\});", text, re.S).group(1)))


def test_every_library_command_is_valid():
    entries = load_library()
    assert entries and not [(e["id"], e["error"]) for e in entries if e.get("error")]


def test_ids_are_unique_across_files():
    # The library page marks a command added by its id.
    ids = [e["id"] for e in load_library()]
    assert len(ids) == len(set(ids))


def test_every_icon_is_shipped():
    icons = shipped_icons()
    used = {e["spec"]["icon"] for e in load_library() if e["spec"].get("icon")}
    used |= {e["group_icon"] for e in load_library() if e["group_icon"]}
    kinds = set(re.findall(r'"(mdi:[a-z0-9-]+)"', re.search(r"const KIND_ICONS = \{.*?\};", (WEB / "app.js").read_text()).group(0)))
    assert used and not (used | kinds) - icons
    assert all(e["spec"].get("icon") for e in load_library())


def test_icons_are_material_design_names():
    base = {"description": "x", "examples": ["x"], "response": "ok"}
    assert parse_command("x", {**base, "icon": "mdi:lightbulb"})
    with pytest.raises(ConfigError, match="Material Design Icon"):
        parse_command("x", {**base, "icon": "lightbulb"})
