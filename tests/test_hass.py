"""The Home Assistant connection outlives its listeners' mistakes, and the
home takes what it has one of."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from websockets.exceptions import ConnectionClosedError

from voice_incubator.commands import HOME_TAKEN
from voice_incubator.hass import Catalog, Entity, HomeAssistant
from voice_incubator.server import VoiceIncubatorApp

from . import fake_hass


@pytest.mark.asyncio
async def test_registry_burst_does_not_cancel_a_running_catalog_listener(monkeypatch):
    hass = HomeAssistant("http://127.0.0.1:1", "token")
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def refresh():
        calls.append("started")
        if len(calls) == 1:
            entered.set()
            await release.wait()
        calls.append("finished")

    monkeypatch.setattr(hass, "refresh_catalog", refresh)
    hass._schedule_refresh(0)
    await asyncio.wait_for(entered.wait(), 1)
    for _ in range(3):
        hass._schedule_refresh(0)
    release.set()
    await asyncio.wait_for(hass._refresh_task, 1)
    assert calls == ["started", "finished", "started", "finished"]


@pytest.mark.asyncio
async def test_closed_socket_during_send_is_a_recoverable_connection_error():
    hass = HomeAssistant("http://127.0.0.1:1", "token")
    hass._socket = SimpleNamespace(send=AsyncMock(side_effect=ConnectionClosedError(None, None)))
    with pytest.raises(ConnectionError, match="disconnected"):
        await hass.call({"type": "get_states"})
    assert not hass._pending


@pytest.mark.asyncio
async def test_reads_only_recorded_assist_exposure(monkeypatch):
    hass = HomeAssistant("http://127.0.0.1:1", "token")
    call = AsyncMock(return_value={"exposed_entities": {"light.desk": {"conversation": True}, "light.cloud": {"alexa": True}}})
    monkeypatch.setattr(hass, "call", call)
    assert await hass.exposed_entities() == {"light.desk"}
    call.assert_awaited_once_with({"type": "homeassistant/expose_entity/list"})


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


def test_home_assistant_still_starting_is_waited_for():
    """Home Assistant restarted, or started after the app: nothing is read
    (its integration's state, its sentence triggers) until it has started."""

    async def main() -> None:
        fake, server = await fake_hass.serve()
        fake.state = "STARTING"
        hass = HomeAssistant(f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}", fake_hass.TOKEN)
        connected = []
        hass.on_connect(lambda: connected.append(True))
        await hass.start()
        try:
            assert not await hass.wait_connected(0.5) and connected == []
            await fake.started()
            assert await hass.wait_connected(5)
            await asyncio.sleep(0.1)
            assert connected == [True] and hass.catalog.entities
        finally:
            await hass.stop()
            server.close()

    asyncio.run(main())


def test_timers_before_the_engine_is_built():
    """Home Assistant answers before the models are loaded: the timers it
    reports then wait for the engine (which starts with their names)."""
    app = SimpleNamespace(sessions={}, engine=None, integration=SimpleNamespace(running_names=lambda: ["pizza"]))
    VoiceIncubatorApp._timers_changed(app)  # pylint: disable=protected-access


def test_the_home_takes_what_home_assistant_has_one_of():
    """The home's weather, thermostat and shopping list: Home Assistant's,
    where it has just one and none is chosen -- once each, so what is chosen
    afterwards stands, none too."""
    catalog = Catalog()

    def has(*entities: tuple[str, str] | tuple[str, str, list[str]], stated: bool = True) -> None:
        for entity_id, name, *aliases in entities:
            catalog.entities[entity_id] = Entity(entity_id, name, aliases[0] if aliases else [], attributes={} if stated else None)

    has(("weather.forecast_home", "Forecast Home"), ("climate.hall", "Hall"), ("climate.attic", "Attic"),
        ("todo.groceries", "Shopping List"), ("todo.house", "To-do"), ("todo.errands", "Errands", ["shopping-list"]))
    # Two thermostats, two shopping lists (one by its alias): a choice.
    assert catalog.home_things() == {"weather": "weather.forecast_home", "climate": None, "shopping_list": None}
    del catalog.entities["todo.errands"], catalog.entities["climate.attic"]
    has(("weather.gone", "Gone"), stated=False)  # no state: not there
    assert catalog.home_things() == {"weather": "weather.forecast_home", "climate": "climate.hall", "shopping_list": "todo.groceries"}
    catalog.entities = {}
    has(("todo.shopping_list", "Einkaufsliste"))  # the integration's, in any language
    assert catalog.home_things() == {"weather": None, "climate": None, "shopping_list": "todo.shopping_list"}

    saved = []
    app = SimpleNamespace(
        store=SimpleNamespace(settings={"home": {"climate": "climate.attic"}}, save_settings=lambda: saved.append(1)),
        hass=SimpleNamespace(catalog=catalog),
    )
    VoiceIncubatorApp._take_home(app)  # pylint: disable=protected-access
    # The thermostat chosen already counts; the weather is looked for again.
    assert app.store.settings == {
        "home": {"climate": "climate.attic", "shopping_list": "todo.shopping_list"},
        HOME_TAKEN: ["climate", "shopping_list"],
    }
    # Cleared afterwards: that stands.
    app.store.settings["home"] = {"climate": "climate.attic"}
    has(("weather.forecast_home", "Forecast Home"))
    VoiceIncubatorApp._take_home(app)  # pylint: disable=protected-access
    assert app.store.settings == {
        "home": {"climate": "climate.attic", "weather": "weather.forecast_home"},
        HOME_TAKEN: ["climate", "shopping_list", "weather"],
    }
    assert len(saved) == 2
    VoiceIncubatorApp._take_home(app)  # pylint: disable=protected-access
    assert len(saved) == 2  # nothing new: not saved again
