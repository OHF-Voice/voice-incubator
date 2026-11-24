"""What is said, and in what pieces (voice_incubator/tts.py): sentences without a
conversation agent's markdown, a list's items apart, synthesized one at a
time."""

from __future__ import annotations

import asyncio
import struct
import wave
from io import BytesIO
from pathlib import Path

import pytest

from voice_incubator.tts import (
    Speech,
    audio_to_pcm,
    said_as,
    sentences_of,
    streamed_wav,
)

ROOT = Path(__file__).resolve().parent.parent
VOICE = ROOT / "models/piper/en_US-ellie-medium.onnx"

AGENT = (
    "An airplane flies due to a combination of factors:\n\n"
    "*   **Lift:** The wings are shaped to make air flow faster over the top than the bottom. "
    "This creates lower pressure above the wing.\n"
    "*   **Thrust:** Engines push air backward.\n"
    "*   **Gravity:** The plane's weight pulls it downwards.\n\n"
    "Lift must be greater than or equal to gravity for sustained flight."
)


def test_sentences_without_markup():
    pieces = sentences_of(AGENT)
    assert not any("*" in piece for piece in pieces)
    assert pieces == [
        "An airplane flies due to a combination of factors:",
        "Lift: The wings are shaped to make air flow faster over the top than the bottom.",
        "This creates lower pressure above the wing.",
        "Thrust: Engines push air backward.",
        "Gravity: The plane's weight pulls it downwards.",
        "Lift must be greater than or equal to gravity for sustained flight.",
    ]
    assert sentences_of("Turned on the lights.") == ["Turned on the lights."]
    assert sentences_of("") == [] and sentences_of("  **  ") == []
    assert sentences_of("2*3*4 = 24.") == ["2*3*4 = 24."]  # arithmetic, not markup
    # Emoji aren't said: a shopping list's, an agent's.
    assert sentences_of("On the shopping list: 🍎 Apples, 🥚 Eggs and Milk 🥛.") == ["On the shopping list: Apples, Eggs and Milk."]
    assert sentences_of("👨‍👩‍👧 Snacks ✅") == ["Snacks"] and sentences_of("🇺🇸") == []
    assert sentences_of("It's 72°F outside.") == ["It's 72°F outside."]


@pytest.mark.skipif(not VOICE.exists(), reason="needs the English Piper voice")
def test_clock_times_said_as_english_voices_read_them():
    """espeak-ng reads "5:00 PM" as "five zero zero P M" and "5:07" as "five
    zero seven"."""
    assert said_as("It's 5:00 PM.", "en-us") == "It's 5 PM."
    assert said_as("Set an alarm for 5:07 PM.", "en-us") == "Set an alarm for 5 oh 7 PM."
    assert said_as("Wake up at 7:00 a.m.", "en-gb-x-rp") == "Wake up at 7 a.m."
    assert said_as("It's 5:30 PM, the score 10:00.", "en-us") == "It's 5:30 PM, the score 10:00."
    assert said_as("Es ist 17:05 Uhr.", "de") == "Es ist 17:05 Uhr."


def test_said_whole_or_acknowledged():
    speech = Speech(VOICE, ROOT / "models")
    asyncio.run(speech.start())
    whole = asyncio.run(speech.say("**Lift:** it rises. *Then* it flies."))
    parts = b"".join(speech.synthesize(piece) for piece in sentences_of("**Lift:** it rises. *Then* it flies."))
    # Piper draws noise for each synthesis: the same length, near enough.
    assert len(whole) > 16000 and abs(len(whole) - len(parts)) < 0.25 * len(parts)
    assert asyncio.run(speech.say("**")) == asyncio.run(speech.sound("acknowledge"))


def _header(frames: int = 0, extra: bytes = b"") -> bytes:
    """Python's wave header (Wyoming's, Home Assistant Cloud's), with any
    chunk before the samples."""
    wav = BytesIO()
    with wave.open(wav, "wb") as file:
        file.setparams((1, 2, 22050, frames, "NONE", "not compressed"))
    head = wav.getvalue()
    if extra:
        head = head[:4] + struct.pack("<I", 36 + len(extra)) + head[8:36] + extra + head[36:]
    return head


def test_a_streamed_wav_is_read_to_its_end():
    # Home Assistant passes a streaming TTS's WAV on as it was made: a header
    # saying 0 frames, then the samples. Every sample is read, also with a
    # chunk (odd-sized, padded) before them.
    samples = struct.pack("<h", 16000) * 2205
    for extra in (b"", b"LIST\x03\x00\x00\x00abc\x00"):
        pcm = audio_to_pcm(_header(extra=extra) + samples, 22050)
        assert len(pcm) == len(samples) and pcm[:2] == struct.pack("<h", 15999)
    # A whole WAV, one with no samples yet, and anything else, as they are.
    whole = BytesIO()
    with wave.open(whole, "wb") as file:
        file.setparams((1, 2, 22050, 0, "NONE", "not compressed"))
        file.writeframes(samples)
    for audio in (whole.getvalue(), _header(), b"ID3 not a wav"):
        assert streamed_wav(audio) == audio

