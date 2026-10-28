"""Shared pieces for the corpus scripts: the take audit's ground truth
(tests/corpus_expected.tsv), listener corrections, levels, and the on-device
wake word detector.

The bench's own expectations are commands and slots, in
tests/corpus_expected.yaml (see bench_corpus.py).
"""

from __future__ import annotations

import json
import subprocess
import sys
import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SAMPLE_RATE = 16000
FRAME_SAMPLES = 160  # 10 ms, one frontend frame
FEATURES_PER_FRAME = 40


@dataclass
class Expectation:
    phrase: str
    kind: str  # grammar | wildcard | oov
    accepted: list[str] = field(default_factory=list)


def ground_truth(path: Path) -> dict[str, Expectation]:
    out: dict[str, Expectation] = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        phrase, kind = parts[0], parts[1]
        accepted = parts[2].split("|") if len(parts) > 2 and parts[2] else []
        out[phrase.lower()] = Expectation(phrase, kind, accepted)
    return out


@dataclass
class Correction:
    """What a listener said about one recording, against what it was labelled.

    The prompt list says what the speaker was *asked* to say. These say what
    came out. The difference is not a detail: a recording labelled "turn on the
    lights" where "turn off the lights" was said scores a correct recognition
    as a failure, and one whose real words are outside the grammar scores a
    forced match as a success.
    """

    verdict: str  # ok | fix | segment
    phrase: str = ""

    @property
    def usable(self) -> bool:
        """`segment` means the recorded window is wrong, so nothing in it can
        be scored -- not that the speaker said the wrong thing."""
        return self.verdict != "segment"


def corrections(path: Path) -> dict[tuple[str, int], Correction]:
    """Reviewed prompt text, keyed by (session, prompt index)."""
    import csv

    if not path.is_file():
        return {}
    out: dict[tuple[str, int], Correction] = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                index = int(row["index"])
            except (KeyError, TypeError, ValueError):
                continue
            session = (row.get("session") or "").strip()
            verdict = (row.get("verdict") or "").strip()
            if not session or verdict not in {"ok", "fix", "segment"}:
                continue
            out[(session, index)] = Correction(
                verdict, (row.get("corrected") or "").strip()
            )
    return out


def session_key(session_dir: Path) -> str:
    """How the corrections name a session: 'jenn-merged' is 'jenn' to them."""
    name = session_dir.name
    return name[: -len("-merged")] if name.endswith("-merged") else name


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wav:
        assert wav.getframerate() == SAMPLE_RATE and wav.getsampwidth() == 2
        audio = np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16)
    if wav.getnchannels() > 1:
        audio = audio.reshape(-1, wav.getnchannels())[:, 0].copy()
    return audio


def apply_gain(audio: np.ndarray, gain: float) -> np.ndarray:
    if gain == 1.0:
        return audio
    return np.clip(audio.astype(np.float32) * gain, -32768, 32767).astype(np.int16)


def features(audio: np.ndarray) -> np.ndarray:
    """The int8 feature stream micro_wake_word sees, 40 per 10 ms frame."""
    from pymicro_features import MicroFrontend

    frontend = MicroFrontend()
    raw = audio.tobytes()
    rows, index = [], 0
    chunk = FRAME_SAMPLES * 2
    while index + chunk <= len(raw):
        result = frontend.process_samples(raw[index : index + chunk])
        index += result.samples_read * 2
        if result.features:
            rows.append(result.features)
    if not rows:
        return np.zeros((0, FEATURES_PER_FRAME), dtype=np.int8)
    # pymicro-features divides the frontend output by 25.6; the device sees the
    # undivided value, so undo that before quantizing as micro_wake_word does.
    values = np.rint(np.array(rows) * 25.6).astype(np.int32)
    return np.clip((values * 256 + 333) // 666 - 128, -128, 127).astype(np.int8)


class WakeWord:
    """The on-device detector: mixednet-executor plus ``determine_detected``."""

    def __init__(self, binary: Path, manifest: Path, scratch: Path) -> None:
        micro = json.loads(manifest.read_text())["micro"]
        self.binary = Path(binary)
        self.cutoff = int(micro["probability_cutoff"] * 255)
        self.window = int(micro["sliding_window_size"])
        self.step_frames = 3  # MixedNetModel::input_frames for these models
        self.scratch = Path(scratch)

    def probabilities(self, audio: np.ndarray) -> np.ndarray:
        self.scratch.write_bytes(features(audio).tobytes())
        result = subprocess.run(
            [str(self.binary), str(self.scratch)],
            capture_output=True,
            text=True,
            check=True,
        )
        return np.array(
            [int(line.split()[1]) for line in result.stdout.split("\n") if line],
            dtype=np.int32,
        )

    def detect(self, probabilities: np.ndarray, cutoff: int | None = None) -> int | None:
        """Sample index just past the first detection, or None."""
        cutoff = self.cutoff if cutoff is None else cutoff
        # Warm: a fresh executor has to see 100 sub-cutoff slices before its
        # first detection, which on a device elapsed hours ago. Cold, it would
        # suppress any wake word spoken in the first 3 s of a clip.
        ignore = 0
        recent = np.zeros(self.window, dtype=np.int64)
        for i, probability in enumerate(probabilities):
            recent[i % self.window] = probability
            if recent.sum() > cutoff * self.window and ignore >= 0:
                return (i + 1) * self.step_frames * FRAME_SAMPLES
            if probability < cutoff:
                ignore = min(ignore + 1, 0)
        return None
