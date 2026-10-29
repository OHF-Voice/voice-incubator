"""What is said, and in what pieces (voice_call/tts.py): sentences without a
conversation agent's markdown, a list's items apart, synthesized one at a
time."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from voice_call.tts import Speech, sentences_of

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
def test_said_whole_or_acknowledged():
    speech = Speech(VOICE, ROOT / "sounds/acknowledge.flac")
    asyncio.run(speech.start())
    whole = asyncio.run(speech.say("**Lift:** it rises. *Then* it flies."))
    parts = b"".join(speech.synthesize(piece) for piece in sentences_of("**Lift:** it rises. *Then* it flies."))
    # Piper draws noise for each synthesis: the same length, near enough.
    assert len(whole) > 16000 and abs(len(whole) - len(parts)) < 0.25 * len(parts)
    assert asyncio.run(speech.say("**")) == speech.acknowledge
