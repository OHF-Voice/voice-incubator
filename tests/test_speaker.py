"""Speaker profiles: the colors the LED ring shows them in."""

from __future__ import annotations

import json

from voice_labs.speaker import MODEL_NAME, PALETTE, Profiles


def on_the_ring(rgb: tuple[int, int, int]) -> tuple[int, ...]:
    """What the VPE's LEDs show: ESPHome's gamma correction, 2.8."""
    return tuple(round(255 * (c / 255) ** 2.8) for c in rgb)


def test_no_palette_color_shows_red():
    # Red means someone is listening live.
    for name, rgb in PALETTE:
        r, g, b = on_the_ring(rgb)
        assert not (r > 150 and g < 80 and b < 80), name


def test_a_color_that_shows_red_is_replaced(tmp_path):
    path = tmp_path / "speakers.json"
    speaker = {"centroid": [1.0, 0.0], "n_windows": 1, "speech_s": 2.0, "cohesion": 1.0}
    path.write_text(json.dumps({"model": MODEL_NAME, "channel": "aec", "speakers": {
        "Mike": {**speaker, "color": [255, 96, 0]},  # the old palette's orange
        "Ann": {**speaker, "color": [0, 255, 64]},
    }}))
    profiles = Profiles(path, "aec")
    assert profiles.color("Ann") == (0, 255, 64)
    assert profiles.color("Mike") == (160, 0, 255)  # the first one free
    assert json.loads(path.read_text())["speakers"]["Mike"]["color"] == [160, 0, 255]
