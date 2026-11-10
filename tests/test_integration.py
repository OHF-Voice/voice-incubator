"""Which integration the app talks to (voice_incubator/integration.py): the
one installed now, or, for a release, the one from before the rename."""

from __future__ import annotations

import asyncio

from voice_incubator.hass import HassError
from voice_incubator.integration import NOT_INSTALLED, Integration


class Hass:
    """Home Assistant with ``installed`` integrations, ``configured`` ones
    already set up."""

    def __init__(self, installed: tuple[str, ...], configured: tuple[str, ...] = ()) -> None:
        self.installed, self.configured = installed, list(configured)
        self.integration_domain = "voice_incubator"
        self.flows: list[str] = []

    async def config_entries(self, domain: str) -> list[dict]:
        return [{"state": "loaded"}] if domain in self.configured else []

    async def start_config_flow(self, domain: str) -> dict:
        if domain not in self.installed:
            raise HassError("Invalid handler specified")
        self.flows.append(domain)
        self.configured.append(domain)
        return {"type": "create_entry"}


def _ensure(hass: Hass) -> Integration:
    integration = Integration.__new__(Integration)
    integration.hass, integration.status = hass, "connecting"
    asyncio.run(integration.ensure())
    return integration


def test_the_integration_as_it_is_now():
    hass = Hass(("voice_incubator",))
    assert _ensure(hass).status == "ready" and hass.flows == ["voice_incubator"]
    assert hass.integration_domain == "voice_incubator"
    # Set up already: nothing more.
    hass.flows.clear()
    assert _ensure(hass).status == "ready" and hass.flows == []


def test_the_integration_from_before_the_rename():
    # Set up then, and not updated: talked to as it is.
    hass = Hass(("voice_labs",), configured=("voice_labs",))
    assert _ensure(hass).status == "ready" and hass.flows == []
    assert hass.integration_domain == "voice_labs"
    # Installed, not set up: set up.
    hass = Hass(("voice_labs",))
    assert _ensure(hass).status == "ready" and hass.flows == ["voice_labs"]
    assert hass.integration_domain == "voice_labs"
    # Updated, the old one's entry still there: the new one is set up and used.
    hass = Hass(("voice_incubator", "voice_labs"), configured=("voice_labs",))
    assert _ensure(hass).status == "ready" and hass.flows == ["voice_incubator"]
    assert hass.integration_domain == "voice_incubator"


def test_no_integration():
    assert _ensure(Hass(())).status == NOT_INSTALLED
