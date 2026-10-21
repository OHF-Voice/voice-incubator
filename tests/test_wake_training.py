"""Training a wake word in the app (voice_call/wake_training.py): the kit, the
positives Piper says, and a job from the phrase to the saved model -- with a
stand-in for the TensorFlow step, which is not in the app's test environment."""

from __future__ import annotations

import asyncio
import json
import sys
import textwrap
from pathlib import Path

import numpy as np
import pytest

from voice_call import wake_positives, wake_training
from voice_call.mixednet import load as load_wake_model
from voice_call.wake_training import Job, Kit, TrainingError, WakeTraining

ROOT = Path(__file__).resolve().parent.parent
VOICE = ROOT / "models/piper/en_GB-jenny_dioco-medium.onnx"
MODEL = ROOT / "models/wake/okay_nabu.tflite"


def make_kit(directory: Path, backbone: bool = True) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "kit.yaml").write_text(textwrap.dedent("""\
        version: 1
        backbone: base_backbone.weights.h5
        positive: {sampling_weight: 14.0, penalty_weight: 3.0}
        negatives:
        - {name: speech, sampling_weight: 25.0, penalty_weight: 50.0}
        - {name: missing, sampling_weight: 1.0, penalty_weight: 8.0}
    """))
    if backbone:
        (directory / "base_backbone.weights.h5").write_bytes(b"weights")
    (directory / "negatives/speech/training/speech_mmap").mkdir(parents=True)
    return directory


def test_phrases_and_ids():
    assert wake_training.phrase_of("  Hey, Computer! ") == "Hey Computer"
    assert wake_training.word_id("Hey Computer") == "hey_computer"
    manifest = wake_training.manifest("hey computer", "hey_computer", "en")
    assert manifest["wake_word"] == "Hey Computer" and manifest["model"] == "hey_computer.tflite"
    assert manifest["type"] == "micro" and manifest["version"] == 2 and manifest["trained_languages"] == ["en"]
    assert manifest["micro"]["probability_cutoff"] == wake_training.CUTOFF
    assert manifest["micro"]["feature_step_size"] == 10


def test_the_kit(tmp_path):
    kit = Kit.load(make_kit(tmp_path / "kit"))
    # Sets without their mmaps are left out.
    assert [n["name"] for n in kit.negatives] == ["speech"]
    assert kit.negatives[0]["dir"] == str(tmp_path / "kit/negatives/speech")
    assert kit.positive == {"sampling_weight": 14.0, "penalty_weight": 3.0} and kit.rirs is None
    with pytest.raises(TrainingError, match="backbone"):
        Kit.load(make_kit(tmp_path / "no-backbone", backbone=False))
    with pytest.raises(TrainingError, match="No training kit"):
        Kit.load(tmp_path / "nothing")


def test_the_frames_are_the_models():
    # 1.5 s at 10 ms with stride 3 is 50 at the last layer; the valid
    # convolutions drop 4 + 3 * (4 + 10 + 14 + 22).
    assert wake_training.FRAMES == 50 + 4 + 3 * (4 + 10 + 14 + 22)


@pytest.mark.skipif(not VOICE.exists(), reason="no Piper voice")
def test_positives(tmp_path, capsys):
    pytest.importorskip("pymicro_features")
    out = tmp_path / "positives"
    assert wake_positives.main([
        "--phrase", "hey computer", "--voice", str(VOICE), "--out", str(out), "--clips", "10", "--slides", "3",
    ]) == 0
    training = np.load(out / "training.npy")
    validation = np.load(out / "validation.npy")
    # One clip held out; each of the others twice, three slides each.
    assert training.shape == (9 * 2 * 3, 204, 40) and validation.shape == (1, 204, 40)
    assert training.dtype == np.uint16
    # The phrase is there: the last second is louder than the quiet first.
    assert training[:, -100:].mean() > training[:, :50].mean()
    reports = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert reports[-1]["finished"] and reports[-1]["training"] == 54


def test_rooms_and_noise_keep_the_clip():
    rng = np.random.default_rng(1)
    clip = np.zeros(16000, dtype=np.float32)
    clip[8000:8400] = 0.5
    out = wake_positives.Augmenter(rng, [], [])(clip)
    assert out.shape == clip.shape and out.dtype == np.float32 and 0 < np.abs(out).max() <= 1.0
    assert wake_positives.variants("hey computer")[-1] == "hey, computer!"


FAKE_TRAINER = """\
#!{python}
# Stands in for a Python with TensorFlow: answers the module check, and
# "trains" by copying a model.
import json, shutil, sys, time
if sys.argv[1] == "-c":
    print("")
    sys.exit(0)
job = json.load(open(sys.argv[2]))
mode = {mode!r}
print(json.dumps({{"stage": "training", "done": 5, "total": 10}}), flush=True)
if mode == "fail":
    print(json.dumps({{"error": "boom"}}), flush=True)
    sys.exit(1)
if mode == "slow":
    time.sleep(60)
assert job["negatives"][0]["name"] == "speech" and job["frames"] == 204 and job["temperature"] == 3.0
assert len(open(job["positives"]["training"], "rb").read()) > 0
shutil.copyfile({model!r}, job["out"])
print(json.dumps({{"stage": "done"}}), flush=True)
"""


def make_trainer(tmp_path: Path, mode: str = "ok") -> Path:
    script = tmp_path / f"fake-python-{mode}"
    script.write_text(FAKE_TRAINER.format(python=sys.executable, mode=mode, model=str(MODEL)))
    script.chmod(0o755)
    return script


def make_training(tmp_path: Path, mode: str = "ok", **kwargs) -> tuple[WakeTraining, list[str]]:
    installed: list[str] = []
    custom = tmp_path / "wake_words"

    def check(word: str) -> str | None:
        try:
            load_wake_model(custom / f"{word}.tflite", custom / f"{word}.json")
        except Exception as err:  # noqa: BLE001
            return str(err)
        return None

    training = WakeTraining(
        tmp_path / "data/wake_training", custom, [tmp_path / "nowhere", make_kit(tmp_path / "kit")], VOICE, "en",
        trainer_python=str(make_trainer(tmp_path, mode)), check_model=check, on_installed=installed.append,
        **kwargs,
    )
    training.positive_clips = 4
    return training, installed


@pytest.mark.skipif(not VOICE.exists(), reason="no Piper voice")
def test_a_wake_word_from_its_phrase(tmp_path):
    pytest.importorskip("pymicro_features")

    async def run() -> None:
        training, installed = make_training(tmp_path)
        assert (await training.status())["unavailable"] is None
        job = await training.start("Hey, Tester!")
        assert job.word == "hey_tester" and training.running
        with pytest.raises(TrainingError, match="already"):
            await training.start("hey other")
        await asyncio.wait_for(training._task, 120)  # pylint: disable=protected-access
        assert job.state == "done" and job.progress == 1.0, job.error
        assert installed == ["hey_tester"]
        saved = json.loads((tmp_path / "wake_words/hey_tester.json").read_text())
        assert saved["wake_word"] == "Hey Tester" and saved["trained"]["clips"] == 4
        assert (tmp_path / "wake_words/hey_tester.tflite").read_bytes() == MODEL.read_bytes()
        # The work is gone; the log and what became of the job stay.
        assert sorted(p.name for p in (tmp_path / "data/wake_training").iterdir()) == ["hey_tester.log", "job.json"]
        assert "Training 'Hey Tester'" in training.log()
        again = WakeTraining(tmp_path / "data/wake_training", None, [], None, "en")
        assert again.job is not None and again.job.state == "done"

    asyncio.run(run())


@pytest.mark.skipif(not VOICE.exists(), reason="no Piper voice")
def test_a_failed_or_cancelled_training(tmp_path):
    pytest.importorskip("pymicro_features")

    async def run() -> None:
        failing, installed = make_training(tmp_path / "fail", "fail")
        job = await failing.start("hey tester")
        await asyncio.wait_for(failing._task, 120)  # pylint: disable=protected-access
        assert job.state == "failed" and job.error == "boom" and not installed
        assert not (tmp_path / "fail/wake_words/hey_tester.tflite").exists()

        slow, _ = make_training(tmp_path / "slow", "slow")
        job = await slow.start("hey tester")
        while job.stage != "training":
            await asyncio.sleep(0.1)
        await asyncio.wait_for(slow.cancel(), 30)
        assert job.state == "cancelled" and not slow.running
        assert not any(p.is_dir() for p in (tmp_path / "slow/data/wake_training").iterdir())

    asyncio.run(run())


def test_what_keeps_it_from_training(tmp_path):
    async def run() -> None:
        training, _ = make_training(tmp_path)
        training.voice = None
        assert "language" in (await training.status())["unavailable"]
        training.voice = tmp_path / "missing.onnx"
        assert "voice is missing" in (await training.status())["unavailable"]
        training.voice = VOICE
        training.kit_dirs = [tmp_path / "nowhere"]
        assert "No training kit" in (await training.status())["unavailable"]
        training.kit_dirs = [tmp_path / "kit"]
        training.trainer_python = sys.executable  # this one has no TensorFlow
        training._trainer_checked = None  # pylint: disable=protected-access
        missing = (await training.status())["unavailable"]
        if "tensorflow" not in sys.modules:
            assert missing and "tensorflow" in missing
        training.exists = lambda word: word == "okay_nabu"
        with pytest.raises(TrainingError):
            await training.start("okay nabu")

    asyncio.run(run())


def test_an_interrupted_job_says_so(tmp_path):
    work = tmp_path / "wake_training"
    work.mkdir()
    (work / "job.json").write_text(json.dumps(Job("abc", "hey tester", "hey_tester").to_json()))
    job = WakeTraining(work, None, [], None, "en").job
    assert job is not None and job.state == "interrupted" and "stopped" in job.error
