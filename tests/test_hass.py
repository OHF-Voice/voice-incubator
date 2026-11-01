"""The Home Assistant connection outlives its listeners' mistakes."""

from __future__ import annotations

from types import SimpleNamespace

from voice_labs.hass import HomeAssistant
from voice_labs.server import VoiceLabsApp


def test_a_failing_event_handler_keeps_the_connection():
    hass = HomeAssistant("http://127.0.0.1:1", "token")
    heard = []

    def broken(event):
        raise AssertionError("not ready")

    hass._subscriptions[1] = broken  # pylint: disable=protected-access
    hass._subscriptions[2] = heard.append  # pylint: disable=protected-access
    hass._dispatch({"id": 1, "type": "event", "event": {"type": "timers"}})  # pylint: disable=protected-access
    hass._dispatch({"id": 2, "type": "event", "event": {"type": "timers"}})  # pylint: disable=protected-access
    assert heard == [{"type": "timers"}]


def test_timers_before_the_engine_is_built():
    """Home Assistant answers before the models are loaded: the timers it
    reports then wait for the engine (which starts with their names)."""
    app = SimpleNamespace(sessions={}, engine=None, integration=SimpleNamespace(running_names=lambda: ["pizza"]))
    VoiceLabsApp._timers_changed(app)  # pylint: disable=protected-access
