"""Searching Home Assistant for a device to add to an area (the Names page):
the area's own first, and before anything is typed only those -- but none it
has already."""

from __future__ import annotations

import asyncio
import socket
from types import SimpleNamespace

import aiohttp

from voice_incubator.hass import Area, Catalog, Entity
from voice_incubator.store import Device
from voice_incubator.web import start_web

CATALOG = Catalog(
    areas={"kitchen": Area("kitchen", "Kitchen"), "den": Area("den", "Den")},
    entities={
        e.entity_id: e
        for e in (
            Entity("light.kitchen_ceiling", "Kitchen Ceiling", area_id="kitchen"),
            Entity("light.counter", "Counter", area_id="kitchen"),
            Entity("light.added", "Added", area_id="kitchen"),
            Entity("light.moved", "A Lamp Moved Here", area_id="den"),
            Entity("light.yaml", "Strip", area_id=None),
            Entity("light.den", "Den Lamp", area_id="den"),
        )
    },
)
DEVICES = {
    "light.added": Device(here=("Added",)),  # in the kitchen, as Home Assistant has it
    "light.moved": Device(area="kitchen", here=("Lamp",)),  # put in the kitchen here
}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_the_area_s_devices_first():
    async def scenario():
        port = _free_port()
        connected = asyncio.Event()
        connected.set()
        app = SimpleNamespace(
            options=SimpleNamespace(http_allow=(), http_host="127.0.0.1", http_port=port),
            hass=SimpleNamespace(catalog=CATALOG, connected=connected),
            voice_devices=lambda: dict(DEVICES),
        )
        runner = await start_web(app)

        async def search(**query) -> list[str]:
            async with http.get(f"http://127.0.0.1:{port}/api/hass/catalog", params={"kind": "entity", **query}) as response:
                return [r["id"] for r in (await response.json())["results"]]

        try:
            async with aiohttp.ClientSession() as http:
                # Nothing typed: the kitchen's own, but those it has.
                assert await search(area="kitchen") == ["light.counter", "light.kitchen_ceiling"]
                # Typed: the kitchen's first, then those in no area, then the rest.
                assert await search(area="kitchen", q="l") == ["light.counter", "light.kitchen_ceiling", "light.yaml", "light.den"]
                # The devices in no area: those Home Assistant has in none.
                assert await search(area="") == ["light.yaml"]
                # One moved out of the den, by its area there in Home Assistant.
                assert await search(area="", q="den") == ["light.moved", "light.den"]
                # Not for an area (a command's target): everything, by name.
                assert await search() == ["light.moved", "light.added", "light.counter", "light.den", "light.kitchen_ceiling", "light.yaml"]
        finally:
            await runner.cleanup()

    asyncio.run(scenario())
