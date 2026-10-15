"""Manifest resolution and outcome classification for adapt-ctc-score."""

import copy
import json

import pytest

from adapt_ctc.config import ConfigError, parse_config
from adapt_ctc.matcher import Recognizer
from adapt_ctc.score import (
    CORRECT,
    FALSE_ACCEPT,
    MISSED,
    UNKNOWN,
    WRONG_INTENT,
    WRONG_SLOTS,
    Expectation,
    check,
    judge,
    load_manifest,
)

from .conftest import toy_vocab, utterance
from .test_matcher import CONFIG


@pytest.fixture
def recordings(tmp_path):
    for name in [
        "close/turn on the lights/a.wav",
        "close/turn on the lights/b.wav",
        "close/turn up the music/c.wav",
        "close/something new/d.wav",
        "oov/blah.wav",
    ]:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
    return tmp_path


def test_manifest_resolves_sentences_and_tags(recordings):
    (recordings / "manifest.yaml").write_text(
        """
config: home.yaml
sentences:
  Turn On The Lights: {expect: HassTurnOn}
  turn up the music: {expect: none}
  blah: {expect: no}
recordings:
  - {path: "close/*/*.wav", tags: [close]}
  - {path: "close/turn on the lights/b.wav", tags: [music]}
  - oov/blah.wav
"""
    )
    manifest = load_manifest(recordings / "manifest.yaml")
    assert manifest.config == recordings / "home.yaml"
    assert manifest.sentences["turn on the lights"].intent == "HassTurnOn"
    assert manifest.sentences["turn up the music"].intent is None

    found = {r.name: (r.sentence, r.tags) for r in manifest.recordings}
    assert found == {
        "close/something new/d.wav": (None, ["close"]),
        "close/turn on the lights/a.wav": ("turn on the lights", ["close"]),
        "close/turn on the lights/b.wav": ("turn on the lights", ["close", "music"]),
        "close/turn up the music/c.wav": ("turn up the music", ["close"]),
        "oov/blah.wav": ("blah", []),
    }


def test_explicit_sentence_wins(recordings):
    (recordings / "manifest.yaml").write_text(
        """
sentences:
  turn on the lights: {expect: HassTurnOn}
  switch on the lights: {expect: HassTurnOn}
recordings:
  - {path: "close/turn on the lights/a.wav", sentence: switch on the lights}
"""
    )
    (recording,) = load_manifest(recordings / "manifest.yaml").recordings
    assert recording.sentence == "switch on the lights"


def test_manifest_errors(recordings):
    (recordings / "manifest.yaml").write_text("sentences: {x: {slots: {}}}\n")
    with pytest.raises(ConfigError, match="needs 'expect'"):
        load_manifest(recordings / "manifest.yaml")

    (recordings / "manifest.yaml").write_text("sentences: {x: {expect: no}}\n")
    manifest = load_manifest(recordings / "manifest.yaml")
    with pytest.raises(ConfigError, match="not an intent in the config"):
        check(manifest, {"HassTurnOn"})


def _recognize(script):
    vocab = toy_vocab()
    recognizer = Recognizer(parse_config(copy.deepcopy(CONFIG)), vocab=vocab)
    return recognizer.recognize_log_probs(utterance(vocab, script), 0.08)


@pytest.mark.parametrize(
    ("expect", "script", "outcome"),
    [
        (Expectation("TurnOn"), ["turn", "on", "the", "lights"], CORRECT),
        (Expectation(None), ["what", "weather", "tomorrow"], CORRECT),
        (Expectation(None), ["turn", "on", "the", "lights"], FALSE_ACCEPT),
        (Expectation("TurnOff"), ["turn", "on", "the", "lights"], WRONG_INTENT),
        (Expectation("TurnOn"), ["what", "weather", "tomorrow"], MISSED),
        (Expectation("TurnOnArea", {"area": ["kitchen"]}), ["turn", "on", "the", "den", "lights"], WRONG_SLOTS),
        (Expectation("TurnOnArea", {"area": ["living_room"]}), ["turn", "on", "the", "den", "lights"], CORRECT),
        (
            Expectation("Brightness", {"area": ["kitchen"], "number": 50.0}),
            ["set", "the", "kitchen", "brightness", "to", "fifty"],
            CORRECT,
        ),
        (None, ["turn", "on", "the", "lights"], UNKNOWN),
    ],
)
def test_judge(expect, script, outcome):
    assert judge(expect, _recognize(script))[0] == outcome


def test_absolute_glob(recordings):
    (recordings / "manifest.yaml").write_text(
        f"""
sentences:
  turn on the lights: {{expect: HassTurnOn}}
recordings:
  - {{path: "{recordings}/close/turn on the lights/*.wav"}}
"""
    )
    manifest = load_manifest(recordings / "manifest.yaml")
    assert [r.sentence for r in manifest.recordings] == ["turn on the lights"] * 2


def test_corpus_prompts(tmp_path):
    """A mic_tap session: prompts from index.json, the wake word's end from the
    alignments, and what the listener heard where it differs."""
    prompts = tmp_path / "jenn-merged" / "prompts"
    prompts.mkdir(parents=True)
    files = lambda n: {"pc-vpe.agc": f"pc-vpe/agc/{n:04d}.wav", "pc-brio.mic": f"pc-brio/mic/{n:04d}.wav"}
    phrases = ["okay nabu, turn on the lights", "okay nabu, Mute the TV", "okay nabu, blah blah"]
    (prompts / "index.json").write_text(
        json.dumps(
            {"phrases": [{"index": i, "phrase": p, "files": files(i)} for i, p in enumerate(phrases, 1)]}
        )
    )
    alignments = tmp_path / "alignments"
    alignments.mkdir()
    (alignments / "jenn-merged.jsonl").write_text(
        "\n".join(
            json.dumps({"prompt": i, "wake": {"start": 100, "end": 16000 + i} if i != 2 else None})
            for i in (1, 2, 3)
        )
    )
    (tmp_path / "corrections.csv").write_text(
        "session,index,verdict,corrected\n"
        "jenn,1,fix,\"okay nabu, turn off the lights\"\n"
        "jenn,3,segment,\n"
    )
    (tmp_path / "manifest.yaml").write_text(
        """
sentences:
  turn off the lights: {expect: HassTurnOff}
recordings:
  - corpus: "*-merged/prompts"
    alignments: alignments
    corrections: corrections.csv
    channels: [pc-vpe.agc]
    strip: "okay nabu,"
    tags: [corpus]
"""
    )
    manifest = load_manifest(tmp_path / "manifest.yaml")
    assert [(r.name, r.sentence, r.start, r.split) for r in manifest.recordings] == [
        ("jenn/pc-vpe.agc/0001", "turn off the lights", 16001, "wake"),
        ("jenn/pc-vpe.agc/0002", "mute the tv", None, "wake"),
    ]
    assert manifest.recordings[0].tags == ["corpus", "jenn", "pc-vpe", "pc-vpe.agc"]
    assert manifest.recordings[0].path == prompts / "pc-vpe/agc/0001.wav"
