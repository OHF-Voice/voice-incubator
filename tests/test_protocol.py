"""What satellites say they are (protocol.Hello), and finding them under
either name (discover.Discovery)."""

from __future__ import annotations

from voice_labs.discover import Device, Discovery
from voice_labs.protocol import SERVICE_TYPES, Hello

NEW, OLD = SERVICE_TYPES


def test_protocol_1_is_a_vpe():
    """The Voice Labs firmware says no protocol 2 fields: both captures, and
    mixednet wake words."""
    hello = Hello.parse({"protocol": 1, "name": "voice-labs-vpe", "mac": "AA:BB", "model_slots": 6, "listen": True})
    assert (hello.protocol, hello.model, hello.firmware) == (1, "voice-pe", None)
    assert hello.aec and hello.wake_format == "mixednet" and hello.aec_lead_samples is None
    assert hello.model_slots == 6 and hello.listen and not hello.intercom
    assert Hello.parse({}).protocol == 1 and Hello.parse({}).aec


def test_protocol_2_says_what_it_has():
    vpe = Hello.parse({
        "protocol": 2, "name": "voice-incubator-vpe", "model": "voice-pe", "firmware": "2026.11.9.3",
        "aec": True, "wake_format": "mixednet", "aec_lead_samples": 905,
    })
    assert (vpe.model, vpe.firmware, vpe.aec, vpe.wake_format, vpe.aec_lead_samples) == (
        "voice-pe", "2026.11.9.3", True, "mixednet", 905,
    )
    # Not said: not there.
    bare = Hello.parse({"protocol": 2, "name": "kitchen"})
    assert (bare.model, bare.aec, bare.wake_format, bare.model_slots) == (None, False, None, None)


def _events(discovery: Discovery) -> list[tuple[str, str, str]]:
    out = []
    while not discovery.events.empty():
        change, device = discovery.events.get_nowait()
        out.append((change, device.host, device.service_type))
    return out


def _found(discovery: Discovery, device: Device) -> None:
    discovery._by_service[device.service] = device  # as resolve() does
    discovery._settle(device.key)


def _gone(discovery: Discovery, device: Device) -> None:
    del discovery._by_service[device.service]  # as a removal does
    discovery._settle(device.key)


def test_one_satellite_under_two_names():
    """Protocol 2 firmware answers to protocol 1's name too: one satellite,
    not two, and losing one name loses nothing."""
    discovery = Discovery()
    old = Device("aabbcc", "10.0.0.5", 6055, f"vpe.{OLD}", OLD)
    new = Device("aabbcc", "10.0.0.5", 6055, f"vpe.{NEW}", NEW)
    _found(discovery, old)
    _found(discovery, new)
    assert _events(discovery) == [("added", "10.0.0.5", OLD)]
    _gone(discovery, old)
    assert _events(discovery) == []
    _gone(discovery, new)
    assert _events(discovery) == [("removed", "10.0.0.5", NEW)]


def test_a_new_address():
    discovery = Discovery()
    _found(discovery, Device("aabbcc", "10.0.0.5", 6055, f"vpe.{NEW}", NEW))
    _found(discovery, Device("aabbcc", "10.0.0.9", 6055, f"vpe.{NEW}", NEW))
    assert _events(discovery) == [("added", "10.0.0.5", NEW), ("removed", "10.0.0.5", NEW), ("added", "10.0.0.9", NEW)]
    # Another satellite is another satellite.
    _found(discovery, Device("ddeeff", "10.0.0.7", 6055, f"other.{OLD}", OLD))
    assert _events(discovery) == [("added", "10.0.0.7", OLD)]
