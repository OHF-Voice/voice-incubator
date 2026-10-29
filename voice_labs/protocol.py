"""Wire protocol spoken with the ESPHome voice_labs component.

Frames are ``uint8 type | uint16 big-endian length | payload``. Audio payloads
are raw 16 kHz, 16-bit, mono PCM; control payloads are JSON.
"""

from __future__ import annotations

import json
import struct
from enum import IntEnum

HEADER = struct.Struct(">BH")
MAX_PAYLOAD = 4096

SAMPLE_RATE = 16000
BYTES_PER_SAMPLE = 2
BYTE_RATE = SAMPLE_RATE * BYTES_PER_SAMPLE


class Message(IntEnum):
    """Device to app."""

    HELLO = 0x01
    CALL_START = 0x02
    """A wake word was detected: {"wake_word": id, "preroll_bytes",
    "aec_preroll_bytes", "probability", "max_probability", "cutoff"} -- the
    last three (0-255, as in WAKE_REPORT; firmware from before them leaves
    them out) are what it came in with: its sliding window's mean, which met
    the cutoff, and the highest single inference in that window."""
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
    MONITOR = 0x14
    """{"active": true | false}: stream the microphone outside calls, for
    someone listening live, with WAKE_REPORTs; meanwhile a wake word does not
    start a call. The device shows it (the VPE's ring blinks red). Firmware
    that can says ``"monitor": true`` in HELLO."""
    MODEL_START = 0x20
    MODEL_DATA = 0x21
    MODEL_END = 0x22
    MODEL_CLEAR = 0x23


def encode(msg_type: Message, payload: bytes = b"") -> bytes:
    if len(payload) > MAX_PAYLOAD:
        raise ValueError(f"payload {len(payload)} exceeds {MAX_PAYLOAD}")
    return HEADER.pack(msg_type, len(payload)) + payload


def encode_json(msg_type: Message, obj: dict) -> bytes:
    return encode(msg_type, json.dumps(obj).encode())


def ms_to_bytes(ms: float) -> int:
    """Byte offset of a timestamp, rounded down to a whole sample."""
    return int(ms * BYTE_RATE / 1000) // BYTES_PER_SAMPLE * BYTES_PER_SAMPLE

