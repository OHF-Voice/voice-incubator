"""Training a new wake word in the app, from the phrase typed on the Wake
words page.

Two steps, each a process of its own below the app's priority (the app keeps
answering satellites; training takes every core for a quarter of an hour on a
Raspberry Pi 5, and about 2 GB):

1. **Positives** (voice_labs/wake_positives.py, the app's Python): Piper --
   the language's training voice, one with many speakers -- says the phrase
   ``POSITIVE_CLIPS`` ways, each given a room and noise, made into the
   features the satellite computes.
2. **Model** (voice_labs/wake_trainer.py, a Python with TensorFlow and
   microWakeWord): trained from scratch against the kit's negatives,
   temperature-scaled and exported as the quantized streaming model the
   satellite runs.

The speakers' own recordings of the phrase (voice_labs/wake_recordings.py),
of whoever is ticked, join Piper's clips: a model that hears them better.

The model and its manifest then go in the custom wake word directory, like an
uploaded one: unverified, at ``CUTOFF``, until given confusables. A word
trained here can be trained again (with more recordings, say): the new model
replaces it when it is done. A built-in word's phrase ("okay nabu") becomes a
word of its own beside it (okay_nabu_trained, wake_words.TRAINED_SUFFIX):
several people's recordings make a model that beats the shipped one.

What training needs besides the phrase is the **kit** (made by
scripts/make_wake_training_kit.py): the negative pool, in a directory with a
``kit.yaml``, and background recordings (music, noise) to put behind the
positives. The home's own room audio, when there is some
(``<data>/wake_training/room/<set>/training/<set>_mmap``: microWakeWord
features, never audio), joins the negatives: the word learns not to fire on
what that home sounds like, and can listen more eagerly. It stays out of the
kit, which an image ships.

On a household's real far-field wake words, music behind the positives alone
traded clean recall for recall over music (more false wakes, so a higher
cutoff); with 36 h of the home's audio as negatives as well, 25 recordings
from each of four people beat the shipped okay_nabu at one false wake an hour
on ten other days of that home: 76% against 73% clean, 60% against 49% with
music 5 dB under the speech, 41% against 31% with music as loud. How well a word does is not known before it is
tried: the recipe's reference is about 60% of wake words at one false wake an
hour (on "hey mycroft", one of the base model's own words), and one run can
differ from the next by 6-11 points.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
import signal
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .wake_words import TRAINED_SUFFIX

_LOGGER = logging.getLogger(__name__)

POSITIVE_CLIPS = 2000
"""Piper clips of the phrase; a tenth are held out for validation."""
TRAINING_STEPS = [8000]
LEARNING_RATES = [5e-4]
"""Where the pooled model peaks at a constant rate: 16,000 steps do worse."""
BATCH_SIZE = 128
TEMPERATURE = 3.0
"""The head is divided by it before conversion (any T >= 2 does)."""
FRAMES = 204
"""Feature frames the base architecture looks at: 1.5 s clips (50 at the last
layer, stride 3) and the 154 its valid convolutions drop."""
CUTOFF = 0.8
"""The manifest's cutoff. Temperature-scaled models of this recipe reached 0.5
to 1 false wake an hour at about 0.70-0.83; the Wake words page tunes it."""
NICE = 10
THREADS = 4
"""TensorFlow's threads: a Pi 5's cores (more is no faster on this model)."""

ROOM_WEIGHTS = {"sampling_weight": 15.0, "penalty_weight": 100.0}
"""The home's audio among the negatives: as the kit's dinner parties."""
ROOM_FRAMES_PER_HOUR = 360_000

STAGES = {"positives": (0.0, 0.2), "preparing": (0.2, 0.22), "training": (0.22, 0.97), "converting": (0.97, 1.0)}
"""Each stage's share of the whole, for the progress bar."""

TRAINER_MODULES = ("tensorflow", "microwakeword", "mmap_ninja", "absl", "yaml")


def _below_the_app() -> None:
    """In the child, before it runs: a lower priority than the app's, and an
    end with it (it is in a session of its own, which a stop of the app's
    process group doesn't reach)."""
    os.nice(NICE)
    with contextlib.suppress(OSError, AttributeError):
        import ctypes  # pylint: disable=import-outside-toplevel

        ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG


class TrainingError(Exception):
    """Why a wake word can't be trained (shown on the page)."""


@dataclass
class Kit:
    """The negatives every wake word is trained with."""

    directory: Path
    positive: dict[str, float]
    negatives: list[dict[str, Any]]
    rirs: Path | None = None
    background: Path | None = None

    @classmethod
    def load(cls, directory: Path) -> Kit:
        path = directory / "kit.yaml"
        try:
            spec = yaml.safe_load(path.read_text("utf-8")) or {}
        except OSError as err:
            raise TrainingError(f"No training kit in {directory}") from err
        except yaml.YAMLError as err:
            raise TrainingError(f"{path}: {err}") from err
        negatives = []
        for entry in spec.get("negatives") or []:
            where = directory / "negatives" / str(entry["name"])
            if any(where.glob("training/*_mmap")):
                negatives.append({**entry, "dir": str(where)})
        if not negatives:
            raise TrainingError("The training kit has no negatives")

        def optional(key: str) -> Path | None:
            found = directory / str(spec[key]) if spec.get(key) else None
            return found if found is not None and found.is_dir() else None

        return cls(
            directory=directory,
            positive={"sampling_weight": 14.0, "penalty_weight": 3.0, **(spec.get("positive") or {})},
            negatives=negatives,
            rirs=optional("rirs"),
            background=optional("background"),
        )


def word_id(phrase: str) -> str:
    """The id a phrase's wake word gets: "Hey, Computer!" -> hey_computer."""
    return re.sub(r"[^a-z0-9]+", "_", phrase.lower()).strip("_")


def phrase_of(text: Any) -> str:
    """The phrase as typed, tidied: words, apostrophes and spaces."""
    return " ".join(re.sub(r"[^\w' ]+", " ", str(text or "")).split())


def titled(phrase: str) -> str:
    """A phrase as a wake word's name: "okay nabu" -> "Okay Nabu"."""
    return " ".join(w.capitalize() for w in phrase.split())


def manifest(
    phrase: str, word: str, language: str, extra: dict[str, Any] | None = None, name: str | None = None
) -> dict[str, Any]:
    """ESPHome's microWakeWord manifest (version 2) for a trained word,
    called ``name`` (None: its phrase); the phrase is ``trained.phrase``."""
    return {
        "type": "micro",
        "wake_word": name or titled(phrase),
        "author": "Voice Labs",
        "model": f"{word}.tflite",
        "trained_languages": [language],
        "version": 2,
        "micro": {
            "probability_cutoff": CUTOFF,
            "sliding_window_size": 5,
            "feature_step_size": 10,
            "tensor_arena_size": 30000,
            "minimum_esphome_version": "2024.7.0",
        },
        "trained": {"by": "voice_labs", "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "phrase": phrase, **(extra or {})},
    }


@dataclass
class Job:
    """One wake word being trained, or the last one."""

    id: str
    phrase: str
    word: str
    name: str | None = None
    """What it is called, when not its phrase: "Okay Nabu (Mike)"."""
    state: str = "running"
    """running, done, failed, cancelled, or interrupted (the app stopped)."""
    stage: str = "positives"
    done: int = 0
    total: int = 0
    started: float = field(default_factory=time.time)
    finished: float | None = None
    error: str | None = None
    recordings: dict[str, int] = field(default_factory=dict)
    """Whose recordings it is trained with, and how many."""
    again: bool = False
    """It replaces a word trained here before."""

    @property
    def progress(self) -> float:
        start, end = STAGES.get(self.stage, (0.0, 1.0))
        share = self.done / self.total if self.total else 0.0
        return 1.0 if self.state == "done" else round(start + (end - start) * min(1.0, share), 4)

    def to_json(self) -> dict[str, Any]:
        out = {
            "id": self.id, "phrase": self.phrase, "word": self.word, "name": self.name or titled(self.phrase),
            "state": self.state, "stage": self.stage,
            "done": self.done, "total": self.total, "started": self.started, "finished": self.finished,
            "error": self.error, "progress": self.progress, "recordings": self.recordings, "again": self.again,
        }
        if self.state == "running" and 0.02 < self.progress < 1.0:
            elapsed = time.time() - self.started
            out["seconds_left"] = round(elapsed / self.progress * (1.0 - self.progress))
        return out


class WakeTraining:
    """Trains one wake word at a time; keeps what became of the last."""

    def __init__(
        self,
        work_dir: Path,
        custom_dir: Path | None,
        kit_dirs: list[Path],
        voice: Path | None,
        language: str,
        trainer_python: str | None = None,
        micro_wake_word: Path | None = None,
        check_model: Callable[[str], str | None] | None = None,
        on_installed: Callable[[str], Awaitable[None] | None] | None = None,
        exists: Callable[[str], bool] | None = None,
        retrainable: Callable[[str], bool] | None = None,
        builtin: Callable[[str], bool] | None = None,
    ) -> None:
        self.work_dir = Path(work_dir)
        self.custom_dir = custom_dir
        self.kit_dirs = kit_dirs
        self.voice = voice
        self.language = language
        self.trainer_python = trainer_python or sys.executable
        self.micro_wake_word = micro_wake_word
        self.check_model = check_model or (lambda _word: None)
        """None when a saved word runs on the satellite, else why not."""
        self.on_installed = on_installed
        self.exists = exists or (lambda _word: False)
        """Whether a wake word of that id is there already."""
        self.retrainable = retrainable or (lambda _word: False)
        """Whether it is one trained here, which may be trained again."""
        self.builtin = builtin or (lambda _word: False)
        """Whether it is a built-in word (its phrase trains as another)."""
        self.positive_clips = POSITIVE_CLIPS
        self.training_steps = list(TRAINING_STEPS)
        self.job: Job | None = self._load_job()
        self._task: asyncio.Task | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._trainer_checked: tuple[float, str | None] | None = None
        self._recordings: dict[str, list[Path]] = {}
        self.room_dir = self.work_dir / "room"
        """The home's own room audio, as negative feature sets."""

    # -------------------------------------------------------------- state

    @property
    def _job_path(self) -> Path:
        return self.work_dir / "job.json"

    def _load_job(self) -> Job | None:
        try:
            raw = json.loads(self._job_path.read_text("utf-8"))
            job = Job(**{k: raw[k] for k in Job.__dataclass_fields__ if k in raw})
        except (OSError, ValueError, TypeError):
            return None
        if job.state == "running":
            job.state, job.error, job.finished = "interrupted", "The app stopped while it was training.", time.time()
        return job

    def _save_job(self) -> None:
        if self.job is None:
            return
        self.work_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._job_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.job.to_json()), "utf-8")
        tmp.replace(self._job_path)

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def kit(self) -> Kit:
        """The first kit found; raises why there is none."""
        error: TrainingError | None = None
        for directory in self.kit_dirs:
            if not (directory / "kit.yaml").exists():
                continue
            try:
                return Kit.load(directory)
            except TrainingError as err:
                error = error or err
        raise error or TrainingError(
            "No training kit (scripts/make_wake_training_kit.py) in " + " or ".join(str(d) for d in self.kit_dirs)
        )

    def _env(self) -> dict[str, str]:
        env = {**os.environ, "CUDA_VISIBLE_DEVICES": "", "TF_NUM_INTRAOP_THREADS": str(THREADS)}
        if self.micro_wake_word is not None:
            env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(self.micro_wake_word), env.get("PYTHONPATH")]))
        return env

    async def trainer_missing(self) -> str | None:
        """What the trainer's Python lacks, if anything (checked once a minute)."""
        now = time.monotonic()
        if self._trainer_checked is not None and now - self._trainer_checked[0] < 60:
            return self._trainer_checked[1]
        code = (
            "import importlib.util as u; "
            f"print(','.join(m for m in {TRAINER_MODULES!r} if u.find_spec(m) is None))"
        )
        try:
            process = await asyncio.create_subprocess_exec(
                self.trainer_python, "-c", code, env=self._env(),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await asyncio.wait_for(process.communicate(), 30)
            missing = out.decode().strip() or None
        except (OSError, asyncio.TimeoutError) as err:
            missing = f"python ({err})"
        self._trainer_checked = (now, missing)
        return missing

    async def unavailable(self) -> str | None:
        """Why no wake word can be trained here now, if so."""
        if self.voice is None:
            return "Training a wake word isn't available in this language yet."
        if not self.voice.is_file():
            return f"The training voice is missing ({self.voice.name})."
        if self.custom_dir is None:
            return "There is no directory for custom wake words."
        try:
            self.kit()
        except TrainingError as err:
            return str(err)
        missing = await self.trainer_missing()
        if missing:
            return f"The trainer's Python lacks {missing.replace(',', ', ')}."
        return None

    def room_negatives(self) -> list[dict[str, Any]]:
        """The home's audio, as the trainer's negatives (``hours`` each)."""
        found = []
        for where in sorted(p for p in self.room_dir.glob("*") if p.is_dir()):
            mmaps = sorted(where.glob("training/*_mmap"))
            if not mmaps:
                continue
            frames = 0
            for mmap in mmaps:
                # A RaggedMmap's values, flat: 40 features a frame.
                with contextlib.suppress(OSError, TypeError):
                    import numpy as np  # pylint: disable=import-outside-toplevel

                    size = np.dtype((mmap / "dtype.ninja").read_text().strip()).itemsize
                    frames += (mmap / "data.ninja").stat().st_size // (40 * size)
            found.append({"name": f"room-{where.name}", "dir": str(where), **ROOM_WEIGHTS,
                          "hours": round(frames / ROOM_FRAMES_PER_HOUR, 1)})
        return found

    def log(self) -> str:
        """The last job's log (the steps' output)."""
        if self.job is None:
            return ""
        try:
            return (self.work_dir / f"{self.job.word}.log").read_text("utf-8", errors="replace")
        except OSError:
            return ""

    async def status(self) -> dict[str, Any]:
        return {
            "unavailable": None if self.running else await self.unavailable(),
            "job": self.job.to_json() if self.job is not None else None,
            "clips": self.positive_clips,
            "steps": sum(self.training_steps),
            "room_hours": round(sum(r["hours"] for r in self.room_negatives()), 1),
        }

    # ---------------------------------------------------------------- run

    async def start(self, text: Any, recordings: dict[str, list[Path]] | None = None, name: Any = None) -> Job:
        """Train ``text``'s wake word, with ``recordings`` of it (by speaker),
        called ``name`` (None: the phrase). Its id is its name's."""
        phrase = phrase_of(text)
        called = " ".join(str(name or "").split()) or None
        word = word_id(called or phrase)
        if self.running:
            raise TrainingError("A wake word is being trained already.")
        if len(phrase.split()) < 1 or len(word_id(phrase)) < 3:
            raise TrainingError("Type the wake phrase.")
        if len(phrase) > 40:
            raise TrainingError("That's too long for a wake word.")
        if called is not None and len(word) < 3:
            raise TrainingError("Give it a name with letters in it.")
        if called is not None and len(called) > 60:
            raise TrainingError("That name is too long.")
        if self.builtin(word):
            word += TRAINED_SUFFIX
        again = self.exists(word)
        if again and not self.retrainable(word):
            raise TrainingError(f"There is a wake word {word} already that wasn't trained here.")
        why = await self.unavailable()
        if why:
            raise TrainingError(why)
        self._recordings = {name: list(paths) for name, paths in (recordings or {}).items() if paths}
        self.job = Job(
            id=uuid.uuid4().hex[:12], phrase=phrase, word=word, name=called, again=again,
            recordings={name: len(paths) for name, paths in self._recordings.items()},
        )
        self._save_job()
        self._task = asyncio.ensure_future(self._run(self.job))
        return self.job

    async def cancel(self) -> None:
        if not self.running:
            return
        assert self._task is not None
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task

    async def stop(self) -> None:
        """The app is stopping: so is training (it says interrupted next time)."""
        if self.running:
            assert self._task is not None
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            if self.job is not None:
                self.job.state, self.job.error = "interrupted", "The app stopped while it was training."
                self._save_job()

    async def _run(self, job: Job) -> None:
        work = self.work_dir / job.id
        log = self.work_dir / f"{job.word}.log"
        try:
            kit = self.kit()
            assert self.voice is not None and self.custom_dir is not None
            work.mkdir(parents=True, exist_ok=True)
            recorded = work / "recordings"
            recorded.mkdir()
            for name, paths in self._recordings.items():
                for index, path in enumerate(paths):
                    shutil.copyfile(path, recorded / f"{word_id(name)}_{index:03d}.wav")
            room = self.room_negatives()
            with log.open("w", encoding="utf-8") as log_file:
                log_file.write(f"Training {job.phrase!r} as {job.word} with the kit in {kit.directory}\n")
                if room:
                    log_file.write("With the home's audio: " + ", ".join(f"{r['name']} ({r['hours']} h)" for r in room) + "\n")
                if job.recordings:
                    log_file.write("With the recordings of " + ", ".join(f"{n} ({c})" for n, c in job.recordings.items()) + "\n")
                log_file.flush()
                await self._step(job, log_file, [
                    sys.executable, "-m", "voice_labs.wake_positives",
                    "--phrase", job.phrase, "--voice", str(self.voice), "--out", str(work / "features"),
                    "--clips", str(self.positive_clips), "--frames", str(FRAMES),
                    *(["--rirs", str(kit.rirs)] if kit.rirs else []),
                    *(["--background", str(kit.background)] if kit.background else []),
                    *(["--real", str(recorded)] if job.recordings else []),
                ])
                tflite = work / f"{job.word}.tflite"
                (work / "job.json").write_text(json.dumps({
                    "work": str(work),
                    "positives": {s: str(work / "features" / f"{s}.npy") for s in ("training", "validation")},
                    "positive": kit.positive,
                    "negatives": [*kit.negatives, *({k: v for k, v in r.items() if k != "hours"} for r in room)],
                    "batch_size": BATCH_SIZE,
                    "training_steps": self.training_steps,
                    "learning_rates": LEARNING_RATES[: len(self.training_steps)],
                    "temperature": TEMPERATURE,
                    "frames": FRAMES,
                    "threads": THREADS,
                    "out": str(tflite),
                }))
                await self._step(job, log_file, [
                    self.trainer_python, str(Path(__file__).with_name("wake_trainer.py")), str(work / "job.json"),
                ])
            if not tflite.is_file():
                raise TrainingError("The trainer made no model.")
            self._install(job, tflite)
            if self.on_installed is not None:
                result = self.on_installed(job.word)
                if asyncio.iscoroutine(result):
                    await result
            job.state, job.stage = "done", "converting"
            _LOGGER.info("Trained the wake word %s", job.word)
        except asyncio.CancelledError:
            job.state, job.error = "cancelled", None
            raise
        except (TrainingError, OSError) as err:
            job.state, job.error = "failed", str(err)
            _LOGGER.error("Training %s failed: %s", job.word, err)
        except Exception as err:
            job.state, job.error = "failed", f"{type(err).__name__}: {err}"
            _LOGGER.exception("Training %s failed", job.word)
        finally:
            job.finished = time.time()
            self._save_job()
            shutil.rmtree(work, ignore_errors=True)

    async def _step(self, job: Job, log_file, command: list[str]) -> None:
        """Run one step, its progress (JSON lines) into the job, the rest into the log."""
        _LOGGER.info("Wake word training: %s", " ".join(command[:3]))
        self._process = await asyncio.create_subprocess_exec(
            *command, cwd=str(Path(__file__).resolve().parent.parent), env=self._env(),
            stdout=asyncio.subprocess.PIPE, stderr=log_file, preexec_fn=_below_the_app,
            start_new_session=True,
        )
        process = self._process
        error = None
        try:
            assert process.stdout is not None
            async for raw in process.stdout:
                try:
                    progress = json.loads(raw)
                except ValueError:
                    continue
                if progress.get("error"):
                    error = str(progress["error"])
                if progress.get("stage") in STAGES:
                    job.stage = progress["stage"]
                    job.done, job.total = int(progress.get("done") or 0), int(progress.get("total") or 0)
                    self._save_job()
            code = await process.wait()
        except asyncio.CancelledError:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
            with contextlib.suppress(asyncio.TimeoutError, ProcessLookupError):
                await asyncio.wait_for(process.wait(), 10)
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            raise
        finally:
            self._process = None
        if code != 0:
            raise TrainingError(error or f"Training stopped ({'killed: out of memory?' if code == -9 else f'exit {code}'}); see {log_file.name}")

    def _install(self, job: Job, tflite: Path) -> None:
        """Into the custom wake words, as an upload would be, in place of the
        one trained before; that one back if the satellite can't run it."""
        assert self.custom_dir is not None
        self.custom_dir.mkdir(parents=True, exist_ok=True)
        model = self.custom_dir / f"{job.word}.tflite"
        manifest_path = self.custom_dir / f"{job.word}.json"
        kept = [(path, path.with_name(path.name + ".before")) for path in (model, manifest_path) if path.exists()]
        for path, before in kept:
            path.replace(before)
        shutil.copyfile(tflite, model)
        extra = {
            "clips": self.positive_clips, "steps": sum(self.training_steps), "temperature": TEMPERATURE,
            "recordings": job.recordings,
            "room_hours": round(sum(r["hours"] for r in self.room_negatives()), 1),
        }
        manifest_path.write_text(json.dumps(manifest(job.phrase, job.word, self.language, extra, job.name), indent=2), "utf-8")
        error = self.check_model(job.word)
        if error:
            model.unlink(missing_ok=True)
            manifest_path.unlink(missing_ok=True)
            for path, before in kept:
                before.replace(path)
            raise TrainingError(f"The trained model can't run on the satellite: {error}")
        for _, before in kept:
            before.unlink(missing_ok=True)
