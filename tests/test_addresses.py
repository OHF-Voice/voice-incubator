"""Satellites by address (voice_incubator/addresses.py): what an entry may
be, and the sessions the server keeps to them."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from voice_incubator import addresses
from voice_incubator.addresses import Address, parse, parse_all
from voice_incubator.server import Session, VoiceIncubatorApp


def test_a_host_name_or_an_address_with_an_optional_port():
    assert parse("kitchen.lan") == Address("kitchen.lan")
    assert parse("Kitchen.LAN.") == Address("kitchen.lan")  # case and the root dot
    assert parse("kitchen") == Address("kitchen")
    assert parse("192.0.2.40") == Address("192.0.2.40")
    assert parse("192.0.2.40:7000") == Address("192.0.2.40", 7000)
    assert parse("fd00::40") == Address("fd00::40")
    assert parse("[FD00::40]:6056") == Address("fd00::40", 6056)
    assert parse("  kitchen.lan:6055  ") == Address("kitchen.lan")
    assert str(Address("fd00::40", 6056)) == "[fd00::40]:6056"
    assert str(Address("kitchen.lan")) == "kitchen.lan"


@pytest.mark.parametrize("entry", [
    "", "a b", "-kitchen", "kitchen-", "a..b", "a_b", "http://kitchen", "user@kitchen", "kitchen/x",
    "eff.253.123.101", "kitchen.123", "12345",  # a last label of digits (RFC 3696 section 2)
    # Addresses no host has (RFC 5771, RFC 1122, RFC 1112, RFC 4291).
    "239.253.123.101", "224.0.0.251", "0.0.0.0", "255.255.255.255", "240.0.0.1",
    "ff02::1", "[ff02::fb]:6055", "::", "[::]:6055",
    "1.2.3.256", "1.2.3", "kitchen:0", "kitchen:65536", "kitchen:x", "kitchen:",
    "[1.2.3.4]", "[fd00::40", "[fd00::40]6056", "fd00::40::1", "x" * 64 + ".lan",
    ".".join(["a" * 63] * 4) + ".b",  # over 253
])
def test_what_is_not_an_address_is_refused(entry):
    with pytest.raises(ValueError):
        parse(entry)


def test_a_name_of_digits_with_a_letter_tld_is_a_name():
    # RFC 1123 allows labels of digits ("3com"); only the last may not be.
    assert parse("3com.com") == Address("3com.com")
    assert parse("125.258.123.eff") == Address("125.258.123.eff")


def test_loopback_and_link_local_are_hosts():
    assert parse("127.0.0.1") == Address("127.0.0.1")
    assert parse("169.254.10.20") == Address("169.254.10.20")
    assert parse("::1") == Address("::1")
    assert parse("[fe80::1]:6056") == Address("fe80::1", 6056)


def test_the_longest_parts_are_accepted():
    assert parse("a" * 63 + ".lan").host == "a" * 63 + ".lan"
    assert parse("kitchen:65535").port == 65535
    assert parse("kitchen:1").port == 1


def test_a_list_keeps_its_order_once_each():
    assert parse_all("b.lan\n\na.lan\nb.lan:6055\n") == [Address("b.lan"), Address("a.lan")]
    assert parse_all(["a.lan", " "]) == [Address("a.lan")]
    assert parse_all(None) == []
    with pytest.raises(ValueError):
        parse_all(["ok.lan", "not ok"])
    with pytest.raises(ValueError):
        parse_all([f"h{i}.lan" for i in range(addresses.MAX_ADDRESSES + 1)])


# The server's part, on a stand-in for the app: its sessions are tasks that
# run until cancelled.


def _app(settings: dict, disabled=()):
    app = SimpleNamespace(
        store=SimpleNamespace(settings=settings),
        hass=SimpleNamespace(catalog=SimpleNamespace(disabled_satellites=set(disabled))),
        _addresses={}, _sessions={}, sessions={}, _serving=True,
        options=SimpleNamespace(host=None),
    )
    started = []

    async def run(session):
        started.append(session)
        await asyncio.Event().wait()

    app._run_session = run
    for name in ("follow_addresses", "_stop_address", "_served_elsewhere"):
        setattr(app, name, getattr(VoiceIncubatorApp, name).__get__(app))
    return app, started


def _cancel(app):
    for task in app._sessions.values():
        task.cancel()


def test_each_address_gets_a_session_and_loses_it_with_the_entry():
    async def scenario():
        app, started = _app({"satellite_addresses": ["kitchen.lan", "hall.lan:6056"]})
        app.follow_addresses()
        await asyncio.sleep(0)
        assert sorted((s.host, s.port) for s in started) == [("hall.lan", 6056), ("kitchen.lan", 6055)]
        assert all(s.key.startswith("address:") for s in started)
        app.follow_addresses()  # nothing changed: nothing more started
        await asyncio.sleep(0)
        assert len(started) == 2
        hall = app._sessions[Address("hall.lan", 6056).key]
        app.store.settings["satellite_addresses"] = ["kitchen.lan"]
        app.follow_addresses()
        await asyncio.sleep(0)
        assert hall.cancelled() and list(app._addresses) == [Address("kitchen.lan")]
        _cancel(app)
    asyncio.run(scenario())


def test_a_satellite_disabled_in_home_assistant_is_let_go_and_taken_back():
    async def scenario():
        app, started = _app({"satellite_addresses": ["kitchen.lan"]})
        app.follow_addresses()
        await asyncio.sleep(0)
        app._addresses[Address("kitchen.lan")] = "aabbccddeeff"  # its HELLO said so
        task = app._sessions[Address("kitchen.lan").key]
        app.hass.catalog.disabled_satellites.add("aabbccddeeff")
        app.follow_addresses()
        await asyncio.sleep(0)
        assert task.cancelled() and Address("kitchen.lan").key not in app._sessions
        app.hass.catalog.disabled_satellites.clear()
        app.follow_addresses()
        await asyncio.sleep(0)
        assert len(started) == 2
        _cancel(app)
    asyncio.run(scenario())


def test_a_bad_stored_entry_costs_only_itself():
    """Saving checks every entry; a stored one a later version refuses is
    skipped alone, the others still served."""
    async def scenario():
        app, started = _app({"satellite_addresses": ["kitchen.lan", "not ok", "kitchen.lan:6055"]})
        app.follow_addresses()
        await asyncio.sleep(0)
        assert [(s.host, s.port) for s in started] == [("kitchen.lan", 6055)]
        _cancel(app)
    asyncio.run(scenario())


def test_pinned_or_not_yet_serving_the_addresses_start_nothing():
    async def scenario():
        app, started = _app({"satellite_addresses": ["kitchen.lan"]})
        app._serving = False
        app.follow_addresses()
        app._serving, app.options.host = True, "10.0.0.9"
        app.follow_addresses()
        await asyncio.sleep(0)
        assert started == [] and app._sessions == {}
    asyncio.run(scenario())


class _Writer:
    def __init__(self, peer) -> None:
        self.peer = peer

    def get_extra_info(self, name):
        return self.peer if name == "peername" else None


def test_an_ip_port_is_connected_once_whoever_dials_it():
    """A satellite takes one client: a session whose host resolves to the IP
    and port another of the app's sessions is connected to waits without
    dialling, whether it came from mDNS or from the list."""
    async def scenario():
        app, _ = _app({})
        connected = Session(key="aabbccddeeff", host="127.0.0.1", port=6055)
        connected.writer = _Writer(("127.0.0.1", 6055))
        app.sessions["aabbccddeeff"] = connected
        by_name = Session(key="address:localhost", host="localhost", port=6055)
        by_ip = Session(key="address:127.0.0.1", host="127.0.0.1", port=6055)
        other_port = Session(key="address:127.0.0.1:6056", host="127.0.0.1", port=6056)
        unresolvable = Session(key="address:nowhere.invalid", host="nowhere.invalid", port=6055)
        assert await app._served_elsewhere(by_name)
        assert await app._served_elsewhere(by_ip)
        assert not await app._served_elsewhere(other_port)
        assert not await app._served_elsewhere(unresolvable)  # dialling says why
        assert not await app._served_elsewhere(connected)  # itself
        connected.writer = None  # it went
        assert not await app._served_elsewhere(by_name)
    asyncio.run(scenario())
