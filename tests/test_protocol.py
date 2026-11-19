"""What satellites say they are (protocol.Hello), and finding them under
either name (discover.Discovery)."""

from __future__ import annotations

import asyncio

from voice_incubator.discover import Device, Discovery
from voice_incubator.protocol import SERVICE_TYPES, Hello, Message

NEW, OLD = SERVICE_TYPES


def test_protocol_1_is_a_vpe():
    """The Voice Labs firmware says no protocol 2 fields: both captures, and
    mixednet wake words."""
    hello = Hello.parse({"protocol": 1, "name": "voice-incubator-vpe", "mac": "AA:BB", "model_slots": 6, "listen": True})
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


def test_audio_cut_mid_sample_is_realigned():
    """Firmware could cut an audio frame mid-sample when its outgoing buffer
    was nearly full; each stream carries the half sample to its next frame."""
    import numpy as np

    from voice_incubator.server import Session, _whole_samples

    session = Session(key="k", host="h", port=1)
    samples = np.arange(-500, 500, dtype="<i2")
    stream = samples.tobytes()
    cuts = [0, 7, 8, 301, 1001, 1999, len(stream)]
    got = b"".join(
        _whole_samples(session, Message.AUDIO_MONITOR_AEC, stream[a:b])
        for a, b in zip(cuts, cuts[1:])
    )
    assert np.array_equal(np.frombuffer(got, dtype="<i2"), samples)
    assert not session.split_samples

    # Streams are realigned apart.
    assert _whole_samples(session, Message.AUDIO_IN, b"\x01\x02\x03") == b"\x01\x02"
    assert _whole_samples(session, Message.AUDIO_IN_AEC, b"\x04\x05") == b"\x04\x05"
    assert _whole_samples(session, Message.AUDIO_IN, b"\x06") == b"\x03\x06"


class _Info:
    """AsyncServiceInfo, answering only from the given attempt on."""

    attempts = 0
    answers_from = 3

    def __init__(self, service_type: str, name: str) -> None:
        self.properties = {b"mac": b"aa:bb:cc:dd:ee:ff"}
        self.port = None

    async def async_request(self, zeroconf, timeout: float) -> bool:
        type(self).attempts += 1
        if type(self).attempts >= self.answers_from:
            self.port = 6055
        return self.port is not None

    def parsed_addresses(self) -> list[str]:
        return ["10.0.0.5"] if self.port is not None else []


def test_a_lost_answer_is_asked_again(monkeypatch):
    """A weak link loses the answer to the first ask, and the browser says a
    name once: asked again until it comes (issue #18, after a restart)."""
    from voice_incubator import discover

    monkeypatch.setattr(discover, "AsyncServiceInfo", _Info)
    monkeypatch.setattr(discover, "RESOLVE_RETRY_SECONDS", 0.01)
    _Info.attempts = 0

    async def scenario() -> list:
        discovery = Discovery()
        discovery._on_change(None, NEW, f"vpe.{NEW}", discover.ServiceStateChange.Added)
        change, device = await asyncio.wait_for(discovery.events.get(), 5)
        await asyncio.sleep(0)
        assert not discovery._resolving
        return [change, device.key, device.host, _Info.attempts]

    assert asyncio.run(scenario()) == ["added", "aabbccddeeff", "10.0.0.5", 3]


def test_a_name_gone_is_not_asked_after(monkeypatch):
    from voice_incubator import discover

    monkeypatch.setattr(discover, "AsyncServiceInfo", _Info)
    monkeypatch.setattr(discover, "RESOLVE_RETRY_SECONDS", 0.01)
    _Info.attempts = 0
    monkeypatch.setattr(_Info, "answers_from", 10**6)

    async def scenario() -> None:
        discovery = Discovery()
        discovery._on_change(None, NEW, f"vpe.{NEW}", discover.ServiceStateChange.Added)
        await asyncio.sleep(0.05)
        task = discovery._resolving[f"vpe.{NEW}"]
        discovery._on_change(None, NEW, f"vpe.{NEW}", discover.ServiceStateChange.Removed)
        await asyncio.sleep(0)
        assert task.cancelled() and not discovery._resolving and discovery.events.empty()

    asyncio.run(scenario())
