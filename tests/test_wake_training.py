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


def make_kit(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "kit.yaml").write_text(textwrap.dedent("""\
        version: 1
        positive: {sampling_weight: 14.0, penalty_weight: 3.0}
        negatives:
        - {name: speech, sampling_weight: 25.0, penalty_weight: 50.0}
        - {name: missing, sampling_weight: 1.0, penalty_weight: 8.0}
    """))
    (directory / "negatives/speech/training/speech_mmap").mkdir(parents=True)
    return directory


def make_recordings(directory: Path, count: int) -> list[Path]:
    """Someone's takes: a second of the test call each (its wake word)."""
    import wave  # pylint: disable=import-outside-toplevel

    directory.mkdir(parents=True, exist_ok=True)
    with wave.open(str(ROOT / "tests/okay_nabu_turn_on_the_lights.wav"), "rb") as wav:
        audio = wav.readframes(wav.getnframes())
    paths = []
    for index in range(count):
        path = directory / f"take{index}.wav"
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(audio[index * 3200 : index * 3200 + 32000])
        paths.append(path)
    return paths


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

    # Recordings join them one for one, all trained on; and Piper's come out
    # as they did without them.
    takes = make_recordings(tmp_path / "takes", 2)
    assert wake_positives.main([
        "--phrase", "hey computer", "--voice", str(VOICE), "--out", str(tmp_path / "with"), "--clips", "10",
        "--slides", "3", "--real", str(takes[0]), "--real", str(tmp_path / "takes"),
    ]) == 0
    with_takes = np.load(tmp_path / "with/training.npy")
    assert with_takes.shape == ((9 + 3) * 2 * 3, 204, 40)
    assert (with_takes[:54] == training).all()
    reports = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert reports[0]["recordings"] == 3 and reports[-1]["done"] == 13


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
mode = {mode!r}
if sys.argv[1] == "-c":
    print("tensorflow,microwakeword" if mode == "bare" else "")
    sys.exit(0)
job = json.load(open(sys.argv[2]))
print(json.dumps({{"stage": "training", "done": 5, "total": 10}}), flush=True)
if mode == "fail":
    print(json.dumps({{"error": "boom"}}), flush=True)
    sys.exit(1)
if mode == "slow":
    time.sleep(60)
assert job["negatives"][0]["name"] == "speech" and job["frames"] == 204 and job["temperature"] == 3.0
assert job["training_steps"] == [8000] and "backbone" not in job
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
        exists=lambda word: (custom / f"{word}.json").exists(), retrainable=lambda word: word != "okay_nabu",
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
        assert saved["trained"]["recordings"] == {}
        assert (tmp_path / "wake_words/hey_tester.tflite").read_bytes() == MODEL.read_bytes()
        # The work is gone; the log and what became of the job stay.
        assert sorted(p.name for p in (tmp_path / "data/wake_training").iterdir()) == ["hey_tester.log", "job.json"]
        assert "Training 'Hey Tester'" in training.log()
        again = WakeTraining(tmp_path / "data/wake_training", None, [], None, "en")
        assert again.job is not None and again.job.state == "done"

        # Again, with someone's recordings: it replaces the one before.
        takes = make_recordings(tmp_path / "takes", 3)
        job = await training.start("hey tester", {"Alice": takes, "Bob": []})
        assert job.again and job.recordings == {"Alice": 3}
        await asyncio.wait_for(training._task, 120)  # pylint: disable=protected-access
        assert job.state == "done", job.error
        saved = json.loads((tmp_path / "wake_words/hey_tester.json").read_text())
        assert saved["trained"]["recordings"] == {"Alice": 3}
        assert "With the recordings of Alice (3)" in training.log()
        assert sorted(p.name for p in (tmp_path / "wake_words").iterdir()) == ["hey_tester.json", "hey_tester.tflite"]
        assert installed == ["hey_tester", "hey_tester"]

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
        training.trainer_python = str(make_trainer(tmp_path, "bare"))  # a Python without TensorFlow
        training._trainer_checked = None  # pylint: disable=protected-access
        assert (await training.status())["unavailable"] == "The trainer's Python lacks tensorflow, microwakeword."
        training.exists = lambda word: word == "okay_nabu"
        with pytest.raises(TrainingError, match="wasn.t trained here"):
            await training.start("okay nabu")
        # A built-in word's phrase is a word of its own beside it.
        training.builtin = lambda word: word == "okay_nabu"
        training.trainer_python = str(make_trainer(tmp_path, "slow"))
        training._trainer_checked = None  # pylint: disable=protected-access
        job = await training.start("okay nabu")
        assert job.word == "okay_nabu_trained" and not job.again
        await training.cancel()

    asyncio.run(run())


def test_an_interrupted_job_says_so(tmp_path):
    work = tmp_path / "wake_training"
    work.mkdir()
    (work / "job.json").write_text(json.dumps(Job("abc", "hey tester", "hey_tester").to_json()))
    job = WakeTraining(work, None, [], None, "en").job
    assert job is not None and job.state == "interrupted" and "stopped" in job.error


KIT = ROOT / "models/wake-training"


@pytest.mark.skipif(not (KIT / "kit.yaml").exists(), reason="no training kit")
@pytest.mark.skipif(not VOICE.exists(), reason="no Piper voice")
def test_a_real_model(tmp_path):
    """The real trainer, briefly: from scratch, 100 steps, temperature-scaled,
    exported -- and the model, pooled, lowers for the satellite."""
    pytest.importorskip("pymicro_features")
    for module in ("tensorflow", "microwakeword", "mmap_ninja"):
        pytest.importorskip(module)

    async def run() -> None:
        custom = tmp_path / "wake_words"
        training = WakeTraining(
            tmp_path / "data", custom, [KIT], VOICE, "en",
            check_model=lambda word: None if load_wake_model(custom / f"{word}.tflite", custom / f"{word}.json") else "?",
        )
        training.positive_clips = 20
        training.training_steps = [100]
        job = await training.start("hey tester", {"Alice": make_recordings(tmp_path / "takes", 2)})
        await asyncio.wait_for(training._task, 600)  # pylint: disable=protected-access
        assert job.state == "done", training.log()[-2000:]
        model = load_wake_model(custom / "hey_tester.tflite", custom / "hey_tester.json")
        assert model.max_pool

    asyncio.run(run())


def test_the_homes_own_audio(tmp_path):
    """Room audio in the data directory (features, as microWakeWord's
    RaggedMmap stores them) joins the kit's negatives."""

    async def run() -> None:
        training, _ = make_training(tmp_path)
        assert training.room_negatives() == [] and (await training.status())["room_hours"] == 0
        mmap = training.room_dir / "living" / "training" / "living_mmap"
        mmap.mkdir(parents=True)
        (mmap / "dtype.ninja").write_text("uint16")
        (mmap / "data.ninja").write_bytes(b"\0" * (40 * 2 * 180_000))  # half an hour of frames
        (room,) = training.room_negatives()
        assert room["name"] == "room-living" and room["hours"] == 0.5 and room["sampling_weight"] == 15.0
        assert (await training.status())["room_hours"] == 0.5

    asyncio.run(run())
