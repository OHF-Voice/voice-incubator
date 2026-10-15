"""The real model on recorded and synthesized commands (skipped without it)."""

import os
from dataclasses import replace
from pathlib import Path

import pytest

from adapt_ctc import CtcModel, Recognizer, load_config, load_wav

from .conftest import FIXTURES, model_dir

MODEL = model_dir()
pytestmark = pytest.mark.skipif(MODEL is None, reason="Parakeet CTC model not found")

EXPECTED = {
    # Recorded on a Voice PE; the second is greedy-decoded as "turn all the lights".
    "vpe_turn_on_the_lights": ("TurnOnLights", {}),
    "vpe_turn_all_the_lights": ("TurnOnLights", {}),
    # Piper, en_GB-jenny_dioco (tests/fixtures/make_fixtures.py).
    "turn_on_lights_living_room": ("TurnOnLights", {"area": ["living_room"]}),
    "turn_on_den_lights": ("TurnOnLights", {"area": ["living_room"]}),
    "turn_on_kitchen_and_bedroom": ("TurnOnLightsAreas", {"area": ["bedroom", "kitchen"]}),
    "turn_off_lights": ("TurnOffLights", {}),
    "turn_on_tv": ("TurnOnEntity", {"entity": ["media_player.tv"]}),
    "turn_on_ecobee": ("TurnOnEntity", {"entity": ["climate.ecobee"]}),
    "brightness_kitchen_50": ("SetBrightness", {"area": ["kitchen"], "number": 50}),
    "brightness_bedroom_15": ("SetBrightness", {"area": ["bedroom"], "number": 15}),
    "brightness_office_75": ("SetBrightness", {"area": ["office"], "number": 75}),
    "thermostat_72_5": ("SetTemperature", {"entity": ["climate.ecobee"], "number": 72.5}),
    "thermostat_68": ("SetTemperature", {"entity": ["climate.ecobee"], "number": 68}),
    "timer_13_minutes": ("StartTimer", {"minutes": 13}),
    "timer_30_seconds": ("StartTimer", {"seconds": 30}),
    "timer_1_5_hours": ("StartTimer", {"hours": 1.5}),
    "play_music": ("Play", {}),
    # Timer names are free text, as heard.
    "timer_named_popcorn": ("StartTimer", {"minutes": 10, "timer": "popcorn"}),
    "timer_cookie": ("StartTimer", {"minutes": 12, "timer": "cookie"}),
    "cancel_pizza_timer": ("CancelTimer", {"timer": "pizza"}),
    "weather": (None, None),
    "lights_and_brightness": (None, None),
}


@pytest.fixture(scope="module")
def recognizer():
    config = load_config(Path(__file__).resolve().parents[2] / "adapt_ctc" / "examples" / "home-extras.yaml")
    return Recognizer(config, CtcModel(MODEL))


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_command(recognizer, name):
    intent, slots = EXPECTED[name]
    result = recognizer.recognize(load_wav(FIXTURES / "audio" / f"{name}.wav"))
    best = result.best
    if intent is None:
        assert best is None, f"{result.transcript!r} matched {best and best.intent}"
        return
    assert best is not None, [r.reasons for r in result.rejected if r.intent == intent]
    assert best.intent == intent, result.transcript
    found = {k: sorted(v) if isinstance(v, list) else v for k, v in best.slots.items()}
    assert found == slots
    assert not best.ambiguous_with


def test_live_and_full_search_agree(recognizer):
    """Same hits, same ranking, on every fixture -- for far fewer updates."""
    from dataclasses import replace

    from adapt_ctc.matcher import Recognizer as _Recognizer

    full_config = replace(
        recognizer.config, settings=replace(recognizer.config.settings, search="full")
    )
    full = _Recognizer(full_config, recognizer.model)
    for path in sorted((FIXTURES / "audio").glob("*.wav")):
        log_probs, seconds = recognizer.model.log_probs(load_wav(path))
        a = recognizer.recognize_log_probs(log_probs, seconds)
        b = full.recognize_log_probs(log_probs, seconds)

        def hits(r):
            return sorted(
                (h.terminal, h.start, h.end, round(h.penalty, 4), h.tokens, h.heard)
                for h in r.spotting.hits
            )

        assert hits(a) == hits(b), path.name
        assert [(m.intent, m.slots, round(m.cost, 4)) for m in a.matches] == [
            (m.intent, m.slots, round(m.cost, 4)) for m in b.matches
        ], path.name
        assert a.spotting.visits * 2 < b.spotting.visits, path.name


@pytest.mark.parametrize(
    ("name", "intent", "slots"),
    [
        # Parakeet hears "Okay, Nab turn on the lights": the command must start
        # after the "-ab", not at the "Okay now" that fits a little better.
        ("genuine_turn_on_the_lights_nab", "TurnOnLights", {}),
        ("genuine_brightness_fifty", "SetBrightness", {"area": ["kitchen"], "number": 50}),
        ("false_okay_natalie", None, None),
        ("false_okay_neighbor", None, None),
    ],
)
def test_call(recognizer, name, intent, slots):
    from adapt_ctc.wake import WakeVerifier, recognize_call

    verifier = WakeVerifier(recognizer.config.wake, recognizer.vocab)
    call = recognize_call(recognizer, verifier, load_wav(FIXTURES / "calls" / f"{name}.wav"))
    if intent is None:
        assert not call.wake.accepted
        assert call.command is None
        return
    assert call.wake.accepted, call.wake.reason
    if name.endswith("_nab"):
        assert call.command.lead_in is None, call.command.transcript
    assert call.command.best.intent == intent
    assert call.command.best.slots == slots


def tdt_dir():
    from adapt_ctc.tdt import DEFAULT_TDT_MODEL

    path = Path(os.environ.get("ADAPT_CTC_TDT_MODEL", DEFAULT_TDT_MODEL))
    return path if (path / "encoder.onnx").exists() else None


@pytest.mark.skipif(tdt_dir() is None, reason="Parakeet TDT model not found")
@pytest.mark.parametrize(
    ("name", "intent", "source"),
    [
        # The CTC transcript says "to morrow"; the sentence is found anyway.
        ("weather", "Weather", "CTC"),
        # Not an example: the CTC search and then TDT find nothing.
        ("lights_and_brightness", None, None),
        # Keywords first: examples are not searched for.
        ("turn_off_lights", "TurnOffLights", None),
    ],
)
def test_example_sentences(recognizer, name, intent, source):
    pytest.importorskip("sherpa_onnx")
    from adapt_ctc.config import Intent
    from adapt_ctc.tdt import TdtTranscriber

    config = replace(
        recognizer.config,
        intents={
            **recognizer.config.intents,
            "Weather": Intent("Weather", examples=("What's the weather like tomorrow?",)),
        },
    )
    with_examples = Recognizer(config, recognizer.model, transcriber=TdtTranscriber(tdt_dir()))
    result = with_examples.recognize(load_wav(FIXTURES / "audio" / f"{name}.wav"))
    assert (result.best and result.best.intent) == intent, result.tdt_transcript
    assert (result.best and result.best.example_source) == source
    assert ("examples" in result.timings) == (intent != "TurnOffLights")
    assert (result.tdt_transcript is not None) == (intent is None)


def embedding_dir():
    from adapt_ctc.embedding import DEFAULT_EMBEDDING_MODEL

    path = Path(os.environ.get("ADAPT_CTC_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL))
    return path if (path / "onnx" / "model_q4.onnx").exists() else None


@pytest.mark.skipif(embedding_dir() is None, reason="EmbeddingGemma 2 ONNX not found")
@pytest.mark.parametrize(
    ("name", "intent"),
    [
        # "What's the weather like tomorrow?" is none of the examples word for word.
        ("weather", "Weather"),
        # Closest to an everyday request ("turn on the lights"), not an example.
        ("lights_and_brightness", None),
    ],
)
def test_examples_in_other_words(recognizer, name, intent):
    pytest.importorskip("tokenizers")
    from adapt_ctc.config import Intent
    from adapt_ctc.embedding import Embedder

    examples = {
        "Weather": ("will I need an umbrella tomorrow?", "how hot will it be today"),
        "Spell": ("how do you spell necessary",),
    }
    config = replace(
        recognizer.config,
        intents={
            **recognizer.config.intents,
            **{name: Intent(name, examples=texts) for name, texts in examples.items()},
        },
    )
    with_examples = Recognizer(config, recognizer.model, embedder=Embedder(embedding_dir()))
    result = with_examples.recognize(load_wav(FIXTURES / "audio" / f"{name}.wav"))
    assert (result.best and result.best.intent) == intent, [r.reasons for r in result.rejected[-2:]]
    if intent is not None:
        assert result.best.example_source == "embedding"
