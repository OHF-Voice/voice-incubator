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


def test_every_response_template_renders():
    """Each library response, at each level, renders with Home Assistant's
    template functions (stubbed): a typo shows here, not in a call."""
    import jinja2  # pylint: disable=import-outside-toplevel

    from voice_call.commands import SPOKEN, parse_command  # pylint: disable=import-outside-toplevel

    class States:
        """states("light.x"), and states.light: Home Assistant's both."""

        light = ()
        lock = ()

        def __call__(self, entity_id):
            return "on"

    env = jinja2.Environment(undefined=jinja2.ChainableUndefined)
    env.globals.update(
        now=__import__("datetime").datetime.now, states=States(), state_attr=lambda e, a: 21 if a == "temperature" else "Ceiling",
        is_state=lambda e, s: False, area_entities=lambda a: ["light.ceiling", "light.lamp", "switch.kettle"],
        area_name=lambda a: "Kitchen", floor_name=lambda f: "Upstairs", floor_areas=lambda f: ["kitchen"],
        area_id=lambda e: "kitchen",
    )
    env.filters.setdefault("round", lambda value, n=0: round(float(value), n))
    env.tests["match"] = lambda value, pattern: __import__("re").match(pattern, str(value)) is not None
    variables = {
        "slots": {"entity": ["light.ceiling"], "area": ["kitchen"], "item": "milk", "reminder": "call Sam",
                  "percentage": 30, "temperature": 21, "color": "red", "device_class": "blind", "asked": "open"},
        "satellite": {"area_id": "kitchen", "area_name": "Kitchen"},
        "place": {"here": False, "said": "in the Kitchen", "areas": ["kitchen"]},
        "command": {"id": "c", "description": "do it", "text": "do it"},
        "result": {"outcome": "done", "speech": "", "count": 2},
    }
    lights = None
    for entry in load_library():
        spec = {k: v for k, v in entry["spec"].items() if k != "needs"}
        behavior = parse_command(entry["id"], spec).behavior
        for level in SPOKEN:
            template = behavior.response_for("done", level)
            if template:
                said = env.from_string(template).render(**variables)
                if entry["id"] == "lights_on" and level == "verbose":
                    lights = said
    assert lights is not None and "Turned on Ceiling and Ceiling" in lights
