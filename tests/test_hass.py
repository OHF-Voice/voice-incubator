"""The Home Assistant connection outlives its listeners' mistakes, and the
home takes what it has one of."""

from __future__ import annotations

from types import SimpleNamespace

from voice_labs.commands import HOME_TAKEN
from voice_labs.hass import Catalog, Entity, HomeAssistant
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
    VoiceLabsApp._take_home(app)  # pylint: disable=protected-access
    # The thermostat chosen already counts; the weather is looked for again.
    assert app.store.settings == {
        "home": {"climate": "climate.attic", "shopping_list": "todo.shopping_list"},
        HOME_TAKEN: ["climate", "shopping_list"],
    }
    # Cleared afterwards: that stands.
    app.store.settings["home"] = {"climate": "climate.attic"}
    has(("weather.forecast_home", "Forecast Home"))
    VoiceLabsApp._take_home(app)  # pylint: disable=protected-access
    assert app.store.settings == {
        "home": {"climate": "climate.attic", "weather": "weather.forecast_home"},
        HOME_TAKEN: ["climate", "shopping_list", "weather"],
    }
    assert len(saved) == 2
    VoiceLabsApp._take_home(app)  # pylint: disable=protected-access
    assert len(saved) == 2  # nothing new: not saved again
