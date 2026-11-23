"""Satellites by address (voice_incubator/addresses.py): what an entry may
be, and how the server learns which satellite each is, once, and hands it to
the remembered-address way of serving it (#21)."""

from __future__ import annotations

import asyncio
import contextlib
from types import SimpleNamespace

import pytest

from voice_incubator import addresses
from voice_incubator.addresses import Address, parse, parse_all
from voice_incubator.discover import Device
from voice_incubator.protocol import Message, encode_json
from voice_incubator.server import REMEMBERED, VoiceIncubatorApp, _hello_key


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


# The server's part, on a stand-in for the app: _start_session records what
# it would serve, settings and satellites.json are dicts, and a satellite is a
# small TCP server that says HELLO.

MAC = "aabbccddeeff"
MDNS = "_voice-incubator._tcp.local."


def _app(settings: dict, disabled=()):
    app = SimpleNamespace(
        store=SimpleNamespace(settings=settings, satellites={}, save_settings=lambda: None,
                              save_satellites=lambda: None),
        hass=SimpleNamespace(catalog=SimpleNamespace(disabled_satellites=set(disabled))),
        _learning={}, _sessions={}, _found={}, sessions={}, _discovery=object(),
        options=SimpleNamespace(host=None, reconnect_seconds=0.01, max_reconnect_seconds=0.02),
    )
    started: list[Device] = []

    def start(device):
        old = app._sessions.pop(device.key, None)
        if old is not None:
            old.cancel()
        started.append(device)
        app._sessions[device.key] = asyncio.ensure_future(asyncio.Event().wait())

    app._start_session = start
    for name in ("follow_addresses", "_follow_mac", "_follow", "_address_macs", "_save_address_macs",
                 "_follow_address", "_connected", "_serve_at"):
        setattr(app, name, getattr(VoiceIncubatorApp, name).__get__(app))
    return app, started


async def _satellite(hello: dict):
    """A satellite at 127.0.0.1:<port> that says HELLO on each connection;
    returns (server, port, connections seen)."""
    seen = []

    async def client(reader, writer):
        seen.append(1)
        writer.write(encode_json(Message.HELLO, hello))
        await writer.drain()
        with contextlib.suppress(Exception):
            await reader.read()
        writer.close()

    server = await asyncio.start_server(client, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1], seen


async def _followed(app, mac=None):
    """follow_addresses (or, given a MAC, a failed dial to it), and its tasks
    run to their end."""
    if mac is None:
        app.follow_addresses()
    else:
        app._follow_mac(mac)
    for _ in range(300):
        if not app._learning:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("still following")


def _cancel(app):
    for task in [*app._sessions.values(), *app._learning.values()]:
        task.cancel()


def test_the_first_time_it_is_asked_and_its_mac_kept():
    async def scenario():
        server, port, seen = await _satellite({"mac": "AA:BB:CC:DD:EE:FF", "name": "kitchen"})
        app, started = _app({"satellite_addresses": [f"127.0.0.1:{port}"]})
        await _followed(app)
        assert app.store.settings["satellite_addresses"] == [{"address": f"127.0.0.1:{port}", "mac": MAC}]
        assert [(d.key, d.host, d.port, d.service) for d in started] == [(MAC, "127.0.0.1", port, REMEMBERED)]
        assert app.store.satellites == {}  # satellites.json is the HELLO's to write (#21)
        await _followed(app)  # served where it points: nothing asked or started again
        assert len(seen) == 1 and len(started) == 1
        _cancel(app)
        server.close()
    asyncio.run(scenario())


def test_a_failed_dial_follows_its_listed_address_to_where_it_answers():
    """Its MAC known, found (by mDNS, or remembered) at an IP gone stale: a
    dial there fails, the address is asked again, and the satellite served at
    the IP that answered, replacing the stale session."""
    async def scenario():
        server, port, seen = await _satellite({"mac": MAC})
        app, started = _app({"satellite_addresses": [{"address": f"localhost:{port}", "mac": MAC}]})
        app._found[MAC] = Device(MAC, "192.0.2.77", port, MDNS)
        stale = app._sessions[MAC] = asyncio.ensure_future(asyncio.Event().wait())
        await _followed(app, MAC)
        assert [(d.host, d.port, d.service) for d in started] == [("127.0.0.1", port, REMEMBERED)]
        assert len(seen) == 1
        await asyncio.sleep(0)
        assert stale.cancelled()
        _cancel(app)
        server.close()
    asyncio.run(scenario())


def test_known_but_disabled_it_is_not_asked():
    async def scenario():
        server, port, seen = await _satellite({"mac": MAC})
        app, started = _app({"satellite_addresses": [{"address": f"localhost:{port}", "mac": MAC}]}, disabled={MAC})
        await _followed(app)
        assert seen == [] and started == []
        server.close()
    asyncio.run(scenario())


def test_connected_elsewhere_it_is_left_alone():
    async def scenario():
        app, started = _app({"satellite_addresses": [{"address": "localhost", "mac": MAC}]})
        app._found[MAC] = Device(MAC, "192.0.2.77", 6055, MDNS)
        app.sessions[MAC] = SimpleNamespace(writer=object())  # connected there
        await _followed(app, MAC)
        assert started == []
    asyncio.run(scenario())


def test_one_the_app_has_where_it_points_is_that_satellite_unasked():
    """Found by mDNS at the IP and port a listed name points to: its MAC is
    kept, and no second connection is made (a satellite takes one client)."""
    async def scenario():
        server, port, seen = await _satellite({"mac": MAC})
        app, started = _app({"satellite_addresses": [f"localhost:{port}"]})
        app._found[MAC] = Device(MAC, "127.0.0.1", port, MDNS)
        await _followed(app)
        assert app.store.settings["satellite_addresses"] == [{"address": f"localhost:{port}", "mac": MAC}]
        assert seen == [] and started == []
        server.close()
    asyncio.run(scenario())


def test_served_at_its_ip_as_mdns_would_report_it():
    """Learnt by name, it is served at the IP that answered: mDNS finding it
    there is the same host and port, which the app leaves alone."""
    async def scenario():
        server, port, _ = await _satellite({"mac": MAC})
        app, started = _app({"satellite_addresses": [f"localhost:{port}"]})
        await _followed(app)
        assert [(d.host, d.port) for d in started] == [("127.0.0.1", port)]
        _cancel(app)
        server.close()
    asyncio.run(scenario())


def test_one_disabled_in_home_assistant_is_kept_but_not_served():
    async def scenario():
        server, port, _ = await _satellite({"mac": MAC})
        app, started = _app({"satellite_addresses": [f"127.0.0.1:{port}"]}, disabled={MAC})
        await _followed(app)
        assert started == [] and app._found[MAC].service == REMEMBERED  # taken when enabled
        server.close()
    asyncio.run(scenario())


def test_whoever_answers_at_the_address_is_its_satellite():
    """The entry's MAC is another satellite's now (a lease reused, a name
    pointed elsewhere): what answers there is the one, kept and served."""
    async def scenario():
        server, port, seen = await _satellite({"mac": "112233445566"})
        app, started = _app({"satellite_addresses": [{"address": f"127.0.0.1:{port}", "mac": MAC}]})
        await _followed(app)
        assert app.store.settings["satellite_addresses"] == [{"address": f"127.0.0.1:{port}", "mac": "112233445566"}]
        assert len(seen) == 1 and [d.key for d in started] == ["112233445566"]
        _cancel(app)
        server.close()
    asyncio.run(scenario())


def test_taken_off_the_list_nothing_more_is_done():
    """As for any satellite since #21, its last address stays remembered;
    the list only stops following it."""
    async def scenario():
        app, started = _app({"satellite_addresses": []})
        app._learning[addresses.parse("kitchen.lan")] = task = asyncio.ensure_future(asyncio.Event().wait())
        app.follow_addresses()
        await asyncio.sleep(0)
        assert task.cancelled() and started == []
    asyncio.run(scenario())


def test_an_unreachable_address_is_asked_again_while_listed():
    async def scenario():
        server, port, _ = await _satellite({"mac": MAC})
        server.close()
        await server.wait_closed()
        app, started = _app({"satellite_addresses": [f"127.0.0.1:{port}"]})
        app.follow_addresses()
        await asyncio.sleep(0.1)
        assert started == [] and len(app._learning) == 1  # still asking
        app.store.settings["satellite_addresses"] = []
        app.follow_addresses()
        await asyncio.sleep(0)
        assert app._learning == {}
    asyncio.run(scenario())


def test_a_hello_without_a_mac_is_refused():
    async def scenario():
        server, port, _ = await _satellite({"name": "no mac"})
        with pytest.raises(ValueError):
            await _hello_key("127.0.0.1", port)
        server.close()
    asyncio.run(scenario())


def test_a_bad_stored_entry_costs_only_itself():
    """Saving checks every entry; a stored one a later version refuses is
    skipped alone, the others still followed."""
    async def scenario():
        app, _ = _app({"satellite_addresses": ["kitchen.invalid", "not ok", "kitchen.invalid:6055"]})
        app.follow_addresses()
        await asyncio.sleep(0)
        assert [str(a) for a in app._learning] == ["kitchen.invalid"]
        _cancel(app)
    asyncio.run(scenario())


def test_pinned_or_not_yet_serving_the_addresses_ask_nothing():
    async def scenario():
        app, started = _app({"satellite_addresses": ["kitchen.invalid"]})
        app._discovery = None  # not serving yet (or any more)
        app.follow_addresses()
        app._discovery, app.options.host = object(), "10.0.0.9"
        app.follow_addresses()
        await asyncio.sleep(0)
        assert started == [] and app._learning == {}
    asyncio.run(scenario())


def test_saving_keeps_each_remaining_address_its_mac():
    old = [{"address": "kitchen.lan", "mac": MAC}, {"address": "hall.lan", "mac": "112233445566"}, "porch.lan"]
    wanted = parse_all("hall.lan\nkitchen.lan\nnew.lan")
    assert addresses.with_macs(wanted, old) == [
        {"address": "hall.lan", "mac": "112233445566"},
        {"address": "kitchen.lan", "mac": MAC},
        {"address": "new.lan"},
    ]


def test_entries_old_and_new_are_read_alike():
    assert parse_all(["kitchen.lan", {"address": "hall.lan:6056", "mac": MAC}]) == [
        Address("kitchen.lan"), Address("hall.lan", 6056)]
    assert addresses.mac_of({"address": "a.lan", "mac": MAC}) == MAC
    assert addresses.mac_of({"address": "a.lan", "mac": "not a mac"}) is None
    assert addresses.mac_of("a.lan") is None and addresses.text_of("a.lan") == "a.lan"


def test_a_failed_dial_of_another_satellite_follows_nothing():
    async def scenario():
        app, _ = _app({"satellite_addresses": [{"address": "kitchen.invalid", "mac": MAC}]})
        app._follow_mac("112233445566")  # found by mDNS, not listed
        assert app._learning == {}
        app._follow_mac(MAC)
        app._follow_mac(MAC)  # its dials keep failing: still one at a time
        assert [str(a) for a in app._learning] == ["kitchen.invalid"]
        _cancel(app)
    asyncio.run(scenario())


def test_known_but_nowhere_to_dial_is_followed_at_start():
    """Its MAC known, but no address for it (none remembered, or forgotten):
    nothing would dial it, so the list does."""
    async def scenario():
        app, _ = _app({"satellite_addresses": [{"address": "kitchen.invalid", "mac": MAC}]})
        app.follow_addresses()
        assert [str(a) for a in app._learning] == ["kitchen.invalid"]
        _cancel(app)
    asyncio.run(scenario())


def test_the_session_follows_its_listed_address_after_a_failed_dial():
    """_run_session's connect-failure branch calls _follow_mac (one line in
    the app's own retry loop)."""
    import inspect
    source = inspect.getsource(VoiceIncubatorApp._run_session)
    failed = source.index("except OSError as err:")
    assert "self._follow_mac(session.key)" in source[failed:source.index("continue", failed)]


def test_a_stale_remembered_ip_is_replaced_at_start_without_waiting_for_a_dial():
    """Remembered at an IP gone stale while the app was off: at start the
    name is resolved and the satellite served where it answers, before a dial
    to the stale IP has timed out (about two minutes)."""
    async def scenario():
        server, port, seen = await _satellite({"mac": MAC})
        app, started = _app({"satellite_addresses": [{"address": f"localhost:{port}", "mac": MAC}]})
        app._found[MAC] = Device(MAC, "192.0.2.77", port, REMEMBERED)
        app._sessions[MAC] = asyncio.ensure_future(asyncio.Event().wait())
        await _followed(app)
        assert [(d.host, d.port) for d in started] == [("127.0.0.1", port)] and len(seen) == 1
        _cancel(app)
        server.close()
    asyncio.run(scenario())


def test_served_where_the_name_points_start_costs_no_connection():
    async def scenario():
        server, port, seen = await _satellite({"mac": MAC})
        app, started = _app({"satellite_addresses": [{"address": f"localhost:{port}", "mac": MAC}]})
        app._found[MAC] = Device(MAC, "127.0.0.1", port, REMEMBERED)
        app._sessions[MAC] = asyncio.ensure_future(asyncio.Event().wait())
        await _followed(app)
        assert seen == [] and started == []
        _cancel(app)
        server.close()
    asyncio.run(scenario())
