"""Write voice_labs/web/icons.js: the Material Design Icons the web UI shows.

Only the icons used are shipped: every ``icon:`` in the library, each
skill's (voice_labs/skills/), the defaults for each kind of command (DEFAULTS
in app.js), the editor's choices (PICKER below) and areas' (AREA_ICONS). Paths come from @mdi/js (Apache-2.0), e.g. the Home Assistant
frontend's node_modules:

    .venv/bin/python scripts/make_icons.py ~/opt/frontend/node_modules/@mdi/js/mdi.js
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from ruamel.yaml import YAML

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "voice_labs/web/icons.js"

DEFAULTS = [
    "mdi:message-text-outline",
    "mdi:flash-outline",
    "mdi:script-text-outline",
    "mdi:palette",
    "mdi:timer-outline",
    "mdi:music",
    "mdi:close-circle-outline",
    # A library section without an icon of its own.
    "mdi:folder-outline",
    # New command: a custom one.
    "mdi:pencil-plus-outline",
    # The Home page: your own commands, the skills (questions), and the
    # Unknown command.
    "mdi:pencil-outline",
    "mdi:comment-question-outline",
    "mdi:help-circle-outline",
    # New command: a library command's versions, and opening it to them.
    "mdi:chevron-right",
    "mdi:crosshairs-gps",
    "mdi:devices",
    "mdi:home-map-marker",
    # Sessions: each step of the pipeline, how it went, and what a session was.
    "mdi:shield-check-outline",
    "mdi:shield-off-outline",
    "mdi:scale-balance",
    "mdi:waveform",
    "mdi:account-voice",
    "mdi:content-cut",
    "mdi:ear-hearing",
    "mdi:robot-outline",
    "mdi:volume-high",
    "mdi:volume-off",
    "mdi:keyboard-outline",
    "mdi:replay",
    "mdi:bell-ring-outline",
    "mdi:timer-off-outline",
    "mdi:alert-circle-outline",
    "mdi:circle-medium",
    "mdi:check-circle",
    "mdi:close-circle",
    "mdi:alert-circle",
    # An area on do not disturb.
    "mdi:minus-circle-outline",
    # A follow-up: the question it answers, and the answer.
    "mdi:message-reply-outline",
    "mdi:message-reply-text-outline",
]
# A satellite's icon: where it is, or what it is.
SATELLITE_PICKER = [
    "mdi:speaker",
    "mdi:speaker-wireless",
    "mdi:microphone",
    "mdi:home",
    "mdi:sofa",
    "mdi:television",
    "mdi:silverware-fork-knife",
    "mdi:stove",
    "mdi:coffee",
    "mdi:bed",
    "mdi:baby-face-outline",
    "mdi:toy-brick",
    "mdi:desk",
    "mdi:shower",
    "mdi:washing-machine",
    "mdi:garage",
    "mdi:car",
    "mdi:flower",
    "mdi:dog",
    "mdi:account",
]
# Areas' icons, as Home Assistant's areas commonly have them (the Home page;
# another shows mdi:home-outline).
AREA_ICONS = [
    "mdi:home-outline",
    "mdi:sofa", "mdi:sofa-outline", "mdi:television", "mdi:bed", "mdi:bed-king", "mdi:bed-double", "mdi:bunk-bed",
    "mdi:silverware-fork-knife", "mdi:stove", "mdi:countertop", "mdi:fridge", "mdi:coffee", "mdi:table-chair",
    "mdi:desk", "mdi:chair-rolling", "mdi:laptop", "mdi:bookshelf", "mdi:shower", "mdi:bathtub", "mdi:toilet",
    "mdi:washing-machine", "mdi:tumble-dryer", "mdi:hanger", "mdi:wardrobe", "mdi:coat-rack",
    "mdi:baby-face-outline", "mdi:baby-carriage", "mdi:toy-brick", "mdi:human-child", "mdi:gamepad-variant",
    "mdi:dumbbell", "mdi:garage", "mdi:garage-variant", "mdi:car", "mdi:tools", "mdi:home-roof", "mdi:stairs",
    "mdi:door", "mdi:door-open", "mdi:balcony", "mdi:fence", "mdi:flower", "mdi:tree", "mdi:grass", "mdi:pool",
    "mdi:greenhouse", "mdi:warehouse", "mdi:home-floor-0", "mdi:home-floor-1", "mdi:home-floor-2",
]
PICKER = [
    "mdi:lightbulb",
    "mdi:lightbulb-outline",
    "mdi:lamp",
    "mdi:ceiling-light",
    "mdi:led-strip-variant",
    "mdi:fan",
    "mdi:air-conditioner",
    "mdi:thermometer",
    "mdi:radiator",
    "mdi:fireplace",
    "mdi:snowflake",
    "mdi:water",
    "mdi:television",
    "mdi:speaker",
    "mdi:music",
    "mdi:radio",
    "mdi:door",
    "mdi:lock",
    "mdi:lock-open-variant",
    "mdi:garage",
    "mdi:blinds",
    "mdi:window-shutter",
    "mdi:curtains",
    "mdi:robot-vacuum",
    "mdi:washing-machine",
    "mdi:coffee",
    "mdi:silverware-fork-knife",
    "mdi:home",
    "mdi:sofa",
    "mdi:bed",
    "mdi:shower",
    "mdi:sprinkler",
    "mdi:flower",
    "mdi:car",
    "mdi:dog",
    "mdi:account",
    "mdi:baby-face-outline",
    "mdi:shield-home",
    "mdi:camera",
    "mdi:bell-outline",
    "mdi:alarm",
    "mdi:calendar",
    "mdi:clock-outline",
    "mdi:weather-sunny",
    "mdi:weather-night",
    "mdi:cart",
    "mdi:format-list-checks",
    "mdi:star-outline",
    "mdi:heart-outline",
    "mdi:power",
    "mdi:lightning-bolt",
    "mdi:message-text-outline",
    "mdi:help-circle-outline",
]


def camel(icon: str) -> str:
    """mdi:timer-plus-outline -> mdiTimerPlusOutline."""
    return "mdi" + "".join(part.capitalize() for part in icon.split(":", 1)[1].split("-"))


def library_icons() -> list[str]:
    yaml = YAML(typ="safe")
    icons = []
    for path in sorted(ROOT.glob("voice_labs/languages/*/library/*.yaml")):
        data = yaml.load(path.read_text("utf-8")) or {}
        if data.get("icon"):
            icons.append(data["icon"])
        for spec in (data.get("commands") or {}).values():
            for version in [spec, *(spec.get("variants") or {}).values()]:
                if (version or {}).get("icon"):
                    icons.append(version["icon"])
    return icons


def skill_icons() -> list[str]:
    """Read, not imported: ``icon="mdi:..."`` in each skill's module."""
    icons = []
    for path in sorted((ROOT / "voice_labs/skills").glob("*.py")):
        icons += re.findall(r'icon="(mdi:[a-z0-9-]+)"', path.read_text("utf-8"))
    return icons


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mdi_js", type=Path, help="@mdi/js/mdi.js")
    args = parser.parse_args()
    source = args.mdi_js.read_text("utf-8")
    version = re.search(r"Material Design Icons v([\d.]+)", source)
    paths = dict(re.findall(r'export var (mdi\w+) = "([^"]+)"', source))
    wanted = list(dict.fromkeys(library_icons() + skill_icons() + DEFAULTS + PICKER + SATELLITE_PICKER + AREA_ICONS))
    missing = [i for i in wanted if camel(i) not in paths]
    if missing:
        raise SystemExit(f"Not in @mdi/js: {', '.join(missing)}")
    icons = {i: paths[camel(i)] for i in sorted(wanted)}
    OUT.write_text(
        f"// Material Design Icons v{version.group(1) if version else '?'} (Apache-2.0,\n"
        "// https://pictogrammers.com), only those the UI shows. Generated by\n"
        "// scripts/make_icons.py; do not edit.\n"
        f"const ICONS = {json.dumps(icons, indent=0)};\n"
        f"const ICON_PICKER = {json.dumps(PICKER)};\n"
        f"const SATELLITE_ICONS = {json.dumps(SATELLITE_PICKER)};\n",
        "utf-8",
    )
    print(f"{len(icons)} icons -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
