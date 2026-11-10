"""Wire protocol spoken with satellites (the Voice Incubator firmware's
PROTOCOL.md; the ESPHome component voice_incubator, voice_labs before it).

Frames are ``uint8 type | uint16 big-endian length | payload``. Audio payloads
are raw 16 kHz, 16-bit, mono PCM; control payloads are JSON.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import Any

HEADER = struct.Struct(">BH")
MAX_PAYLOAD = 4096

SAMPLE_RATE = 16000
BYTES_PER_SAMPLE = 2
BYTE_RATE = SAMPLE_RATE * BYTES_PER_SAMPLE


PROTOCOL = 2
"""What this app speaks. Protocol 2 added HELLO's ``model``, ``firmware``,
``aec`` and ``wake_format``, and the service name ``_voice-incubator``; a
protocol 1 satellite (the Voice Labs firmware) is a VPE: both captures, and
mixednet wake words."""

SERVICE_TYPES = ("_voice-incubator._tcp.local.", "_voice-labs._tcp.local.")
"""Where satellites are found: protocol 2's name first, then protocol 1's
(the Voice Labs firmware), which protocol 2 firmware also answers to for apps
from before the rename."""


class Message(IntEnum):
    """Device to app."""

    HELLO = 0x01
    """What the satellite is and can do (``Hello``)."""
    CALL_START = 0x02
    """A wake word was detected: {"wake_word": id, "preroll_bytes",
    "aec_preroll_bytes", "probability", "max_probability", "cutoff"} -- the
    last three (0-255, as in WAKE_REPORT; firmware from before them leaves
    them out) are what it came in with: its sliding window's mean, which met
    the cutoff, and the highest single inference in that window. The wake
    word "follow_up", with no pre-roll: the microphone opened for an answer
    the app asked for (HANGUP ``listen``)."""
    AUDIO_IN = 0x03
    CALL_END = 0x04
    STATUS = 0x05
    PLAYBACK_DRAINED = 0x06
    MODEL_RESULT = 0x07
    AUDIO_IN_AEC = 0x08
    DEVICE_EVENT = 0x09
    """Something happened on the device: {"type": "announcing" | "announced"}."""
    AUDIO_MONITOR = 0x0A
    """The microphone outside a call, while monitored (MONITOR)."""
    WAKE_REPORT = 0x0B
    """While monitored, about ten times a second: {"models": [{"id", "vad",
    "cutoff", "peak", "window"}], "detected": [ids]} -- each model's highest
    probability (0-255) since the last report, of an inference and of its
    window's mean (what meets the cutoff), and wake words detected, which a
    monitored device does not answer."""
    AUDIO_MONITOR_AEC = 0x0C
    """The second capture outside a call, while monitored: what speakers are
    enrolled on. Firmware that sends it says ``"monitor_aec": true`` in HELLO."""

    """App to device."""
    AUDIO_OUT = 0x10
    AUDIO_OUT_END = 0x11
    EVENT = 0x12
    HANGUP = 0x13
    """{"reason": ..., "listen": true}: end the call -- and, ``listen``, start
    the one that hears the answer to what its reply asked (wake word
    "follow_up", no pre-roll), unless muted. Firmware that can says
    ``"listen": true`` in HELLO."""
    MONITOR = 0x14
    """{"active": true | false}: stream the microphone outside calls, for
    someone listening live, with WAKE_REPORTs; meanwhile a wake word does not
    start a call. The device shows it (the VPE's ring blinks red). Firmware
    that can says ``"monitor": true`` in HELLO."""
    MODEL_START = 0x20
    """A model's header; its blob follows (MODEL_DATA), then MODEL_END.
    Firmware that runs "stop" only while it arms it (a reply, a ringing
    timer), and ends a call on it, says ``"stop_word": true`` in HELLO: it
    is pushed with the wake words and stays."""
    MODEL_DATA = 0x21
    MODEL_END = 0x22
    MODEL_CLEAR = 0x23


@dataclass(frozen=True)
class Hello:
    """A satellite's HELLO, read the same for every protocol."""

    protocol: int
    name: str
    mac: str
    model: str | None
    """What it is: voice-pe, box-3, linux, ... (protocol 1: a VPE)."""
    firmware: str | None
    """Its firmware's version, when it says."""
    aec: bool
    """It sends a second capture of each call (AUDIO_IN_AEC): echo-cancelled,
    before gain control, what speakers are identified on."""
    aec_lead_samples: int | None
    """How far the second capture runs ahead of the first; None: the VPE's."""
    monitor_aec: bool
    """It sends the second capture while monitored too (AUDIO_MONITOR_AEC):
    what speakers are enrolled on."""
    wake_format: str | None
    """The wake word models it runs, pushed by the app; None: it runs none
    the app can push (press to talk, or its own)."""
    model_slots: int | None
    max_pool: bool
    stop_word: bool
    monitor: bool
    intercom: bool
    listen: bool

    @classmethod
    def parse(cls, info: dict[str, Any]) -> Hello:
        protocol = int(info.get("protocol") or 1)
        first = protocol < 2
        slots = info.get("model_slots")
        lead = info.get("aec_lead_samples")
        return cls(
            protocol=protocol,
            name=str(info.get("name") or ""),
            mac=str(info.get("mac") or ""),
            model=str(info.get("model") or "") or ("voice-pe" if first else None),
            firmware=str(info.get("firmware") or "") or None,
            aec=first or bool(info.get("aec")),
            aec_lead_samples=int(lead) if isinstance(lead, (int, float)) and lead >= 0 else None,
            monitor_aec=bool(info.get("monitor_aec")),
            wake_format=str(info.get("wake_format") or "") or ("mixednet" if first else None),
            model_slots=int(slots) if isinstance(slots, (int, float)) and slots > 0 else None,
            max_pool=bool(info.get("max_pool")),
            stop_word=bool(info.get("stop_word")),
            monitor=bool(info.get("monitor")),
            intercom=bool(info.get("intercom")),
            listen=bool(info.get("listen")),
        )


def encode(msg_type: Message, payload: bytes = b"") -> bytes:
    if len(payload) > MAX_PAYLOAD:
        raise ValueError(f"payload {len(payload)} exceeds {MAX_PAYLOAD}")
    return HEADER.pack(msg_type, len(payload)) + payload


def encode_json(msg_type: Message, obj: dict) -> bytes:
    return encode(msg_type, json.dumps(obj).encode())


def ms_to_bytes(ms: float) -> int:
    """Byte offset of a timestamp, rounded down to a whole sample."""
    return int(ms * BYTE_RATE / 1000) // BYTES_PER_SAMPLE * BYTES_PER_SAMPLE

