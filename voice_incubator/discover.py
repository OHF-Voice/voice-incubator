"""mDNS discovery of satellites (protocol.SERVICE_TYPES).

The browser stays alive for the lifetime of the app rather than resolving once:
satellites get power-cycled, reflashed and added, and a list captured at startup
goes stale the first time one of those happens.

A satellite can be found under more than one name -- protocol 2 firmware
answers to protocol 1's too, for apps from before the rename -- and is one
satellite all the same: it comes and goes by its key, at the address of the
first name it has.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from zeroconf import ServiceStateChange
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

from .protocol import SERVICE_TYPES

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
    service_type: str = SERVICE_TYPES[0]

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
        self._by_key: dict[str, Device] = {}
        """What was last announced for each satellite."""
        self._azc: AsyncZeroconf | None = None
        self._browser: AsyncServiceBrowser | None = None

    async def start(self) -> None:
        self._azc = AsyncZeroconf()
        self._browser = AsyncServiceBrowser(
            self._azc.zeroconf, list(SERVICE_TYPES), handlers=[self._on_change]
        )
        _LOGGER.info("Browsing for %s", ", ".join(SERVICE_TYPES))

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
                self._settle(device.key)
            return

        async def resolve() -> None:
            info = AsyncServiceInfo(service_type, name)
            await info.async_request(zeroconf, 3000)
            addresses = info.parsed_addresses()
            if not addresses or info.port is None:
                return
            device = Device(_key(name, info), addresses[0], info.port, name, service_type)
            previous = self._by_service.get(name)
            if previous == device:
                return
            self._by_service[name] = device
            if previous is not None and previous.key != device.key:
                self._settle(previous.key)
            self._settle(device.key)

        asyncio.ensure_future(resolve())

    def _settle(self, key: str) -> None:
        """Announce what changed for one satellite: found, gone, or at a new
        address (gone, then found: the old session is dropped first)."""
        found = [d for d in self._by_service.values() if d.key == key]
        best = min(found, key=_rank, default=None)
        current = self._by_key.get(key)
        if best is not None and current is not None and (best.host, best.port) == (current.host, current.port):
            self._by_key[key] = best  # the same satellite, under whichever name
            return
        if current is not None:
            del self._by_key[key]
            self.events.put_nowait(("removed", current))
        if best is not None:
            self._by_key[key] = best
            self.events.put_nowait(("added", best))


def _rank(device: Device) -> int:
    try:
        return SERVICE_TYPES.index(device.service_type)
    except ValueError:
        return len(SERVICE_TYPES)
