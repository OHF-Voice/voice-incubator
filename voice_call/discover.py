"""mDNS discovery of ESPHome voice_call devices.

The browser stays alive for the lifetime of the app rather than resolving once:
satellites get power-cycled, reflashed and added, and a list captured at startup
goes stale the first time one of those happens.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from zeroconf import ServiceStateChange
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

SERVICE_TYPE = "_voice-call._tcp.local."

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Device:
    """A discovered satellite.

    ``key`` is what sessions are keyed on. The TXT record carries the MAC, which
    is the only thing separating several devices flashed from one YAML: without
    ``name_add_mac_suffix`` they all report the same name.
    """

    key: str
    host: str
    port: int
    service: str

    def __str__(self) -> str:
        return f"{self.key} at {self.host}:{self.port}"


def _key(service: str, info: AsyncServiceInfo) -> str:
    mac = (info.properties or {}).get(b"mac")
    if mac:
        return mac.decode(errors="replace").replace(":", "").lower()
    # No MAC in the TXT record: fall back to the service instance name, which
    # mDNS already guarantees is unique on the network.
    return service.split(".", 1)[0]


class Discovery:
    """Streams device arrivals and departures for as long as it is running."""

    def __init__(self) -> None:
        self.events: asyncio.Queue[tuple[str, Device]] = asyncio.Queue()
        self._by_service: dict[str, Device] = {}
        self._azc: AsyncZeroconf | None = None
        self._browser: AsyncServiceBrowser | None = None

    async def start(self) -> None:
        self._azc = AsyncZeroconf()
        self._browser = AsyncServiceBrowser(
            self._azc.zeroconf, SERVICE_TYPE, handlers=[self._on_change]
        )
        _LOGGER.info("Browsing for %s", SERVICE_TYPE)

    async def stop(self) -> None:
        if self._browser is not None:
            await self._browser.async_cancel()
            self._browser = None
        if self._azc is not None:
            await self._azc.async_close()
            self._azc = None

    def _on_change(self, zeroconf, service_type, name, state_change) -> None:
        if state_change is ServiceStateChange.Removed:
            device = self._by_service.pop(name, None)
            if device is not None:
                self.events.put_nowait(("removed", device))
            return

        async def resolve() -> None:
            info = AsyncServiceInfo(service_type, name)
            await info.async_request(zeroconf, 3000)
            addresses = info.parsed_addresses()
            if not addresses or info.port is None:
                return
            device = Device(_key(name, info), addresses[0], info.port, name)
            previous = self._by_service.get(name)
            if previous == device:
                return
            if previous is not None:
                # Same service, new address: drop the old session first.
                self.events.put_nowait(("removed", previous))
            self._by_service[name] = device
            self.events.put_nowait(("added", device))

        asyncio.ensure_future(resolve())


async def discover(timeout: float = 10.0) -> tuple[str, int]:
    """Return the first device's address and port. Kept for one-shot callers."""
    discovery = Discovery()
    await discovery.start()
    try:
        _, device = await asyncio.wait_for(discovery.events.get(), timeout)
        return device.host, device.port
    finally:
        await discovery.stop()
