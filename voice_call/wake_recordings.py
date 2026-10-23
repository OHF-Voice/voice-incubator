"""A speaker's own wake words: them saying a wake phrase at a satellite, kept
with their voice, for training that wake word (voice_call/wake_training.py).

Ten recordings of one person take a trained model from about 62% of their
wake words to 90%, twenty-five to 96%, and do little for anyone else's
(micro-wake-word's POOLING_AND_PERSONALIZATION.md: one utterance from one
microphone each, beside 2,000 of Piper's). So a household records each of
its voices, and a word is trained with the recordings of whoever is ticked.

Recording, the satellite streams what speakers are enrolled on (the second
capture, while monitored: a wake word then starts no call) and reports what
its wake words heard. Silero cuts the stream into utterances; each one of a
wake word's length is a take, kept when it sounds like the speaker: it scores
TAKE_SCORE or more against their voice, and no one else's scores higher.
Whether the satellite's own model heard it is noted: how well the word does
for them now.

A take is a WAV beside the speakers' profiles (``wake_recordings/``); the
profile lists them under ``wake``, by the word's id, so they follow a rename
and go with a delete.
"""

from __future__ import annotations

import time
import uuid
import wave
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .protocol import BYTE_RATE, SAMPLE_RATE

TARGET_TAKES = 10
"""Enough to count: about 62% -> 90% for them."""
GOOD_TAKES = 25
"""About 96%."""
MAX_TAKES = 60
"""A word's takes per speaker (the oldest go)."""
MAX_SECONDS = 300.0
"""Listening stops after this."""
MIN_SPEECH, MAX_SPEECH = 0.4, 2.5
"""An utterance's speech, for a take: shorter is a cough, longer more than the phrase."""
GAP = 0.4
"""Silence that ends an utterance: longer than the pause inside a phrase."""
BEFORE, AFTER = 0.25, 0.15
"""Audio kept around the speech."""
TAKE_SCORE = 0.35
"""A take against the speaker's voice: Piper's speakers score about 0.65
against their own and 0.25 against another's. Stricter than a command's
"someone else" (endpoint.OTHER_SCORE): a take refused costs only another."""
_CHUNK = 1024
"""Silero's 512 samples."""


@dataclass
class Report:
    at: float
    """Seconds into the recording when it came."""
    window: float
    cutoff: float
    detected: bool


@dataclass
class WakeRecording:
    """A speaker saying a wake phrase, again and again, at a satellite."""

    satellite: str
    satellite_name: str
    speaker: str
    phrase: str
    word: str
    model: str | None = None
    """The satellite's model of this word, if it runs one: whether it heard each take."""
    started: float = field(default_factory=time.time)
    state: str = "listening"
    """``listening``, ``done`` or ``failed``."""
    error: str | None = None
    takes: list[dict[str, Any]] = field(default_factory=list)
    """This recording's, newest first: kept or not, and why."""
    checking: int = 0
    level: float | None = None
    audio: bytearray = field(default_factory=bytearray)
    _vad: Any = None
    _leftover: bytes = b""
    _start: float | None = None
    _end: float = 0.0
    _reports: deque = field(default_factory=lambda: deque(maxlen=600))

    def __post_init__(self) -> None:
        from pysilero_vad import SileroVoiceActivityDetector  # pylint: disable=import-outside-toplevel

        self._vad = SileroVoiceActivityDetector()

    @property
    def seconds(self) -> float:
        return len(self.audio) / BYTE_RATE

    @property
    def listening(self) -> bool:
        return self.state == "listening" and self.seconds < MAX_SECONDS

    @property
    def kept(self) -> int:
        return sum(1 for t in self.takes if t["kept"])

    def add(self, pcm: bytes) -> list[tuple[bytes, float, float]]:
        """The satellite's audio; the utterances it ended, as ``(audio, start,
        end)`` (seconds into the recording, of the speech)."""
        if not self.listening:
            return []
        self.audio += pcm
        samples = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2")
        if samples.size:
            tail = samples[-1600:].astype(np.float64) / 32768.0
            self.level = round(float(10 * np.log10(np.mean(tail**2) + 1e-12)), 1)
        ended = []
        self._leftover += pcm
        at = self.seconds - len(self._leftover) / BYTE_RATE
        while len(self._leftover) >= _CHUNK:
            chunk, self._leftover = self._leftover[:_CHUNK], self._leftover[_CHUNK:]
            step = _CHUNK / BYTE_RATE
            if self._vad.process_chunk(chunk) >= 0.5:
                if self._start is None:
                    self._start = at
                self._end = at + step
            elif self._start is not None and at - self._end >= GAP:
                ended.extend(self._utterance(self._start, self._end))
                self._start = None
            at += step
        return ended

    def _utterance(self, start: float, end: float) -> list[tuple[bytes, float, float]]:
        speech = end - start
        if speech < MIN_SPEECH:
            return []
        if speech > MAX_SPEECH:
            self.taken(False, start, end, why="Too long: say just the phrase")
            return []
        lo = max(0, int((start - BEFORE) * SAMPLE_RATE)) * 2
        hi = min(len(self.audio) // 2, int((end + AFTER) * SAMPLE_RATE)) * 2
        self.checking += 1
        return [(bytes(self.audio[lo:hi]), start, end)]

    def report(self, report: dict[str, Any]) -> None:
        """A WAKE_REPORT from the satellite (as the device sends it)."""
        if self.model is None:
            return
        detected = self.model in [str(d) for d in report.get("detected") or []]
        for model in report.get("models") or []:
            if str(model.get("id") or "") == self.model:
                self._reports.append(Report(
                    self.seconds, int(model.get("window") or 0) / 255, int(model.get("cutoff") or 0) / 255, detected,
                ))

    def heard(self, start: float, end: float) -> bool | None:
        """Whether the satellite's model fired on the utterance."""
        if self.model is None:
            return None
        return any(
            r.detected or (r.cutoff and r.window >= r.cutoff)
            for r in self._reports if start - 0.2 <= r.at <= end + GAP + 0.1
        )

    def taken(self, kept: bool, start: float, end: float, *, why: str | None = None,
              score: float | None = None, take: dict[str, Any] | None = None) -> None:
        self.takes.insert(0, {
            "kept": kept, "why": why, "seconds": round(end - start, 2),
            "score": None if score is None else round(score, 3), "heard": self.heard(start, end),
            "id": take["id"] if take else None,
        })

    def to_json(self) -> dict[str, Any]:
        heard = [t["heard"] for t in self.takes if t["kept"] and t["heard"] is not None]
        return {
            "satellite": self.satellite,
            "satellite_name": self.satellite_name,
            "speaker": self.speaker,
            "phrase": self.phrase,
            "word": self.word,
            "model": self.model,
            "started": self.started,
            "seconds": round(self.seconds, 1),
            "state": self.state if self.state != "listening" or self.listening else "done",
            "error": self.error,
            "kept": self.kept,
            "heard": sum(heard) if heard else None,
            "checking": self.checking,
            "target": TARGET_TAKES,
            "good": GOOD_TAKES,
            "level": self.level,
            "takes": self.takes[:30],
        }


class WakeTakes:
    """The takes kept with the speakers' profiles (a speakers.json's ``wake``)."""

    def __init__(self, directory: Path, speakers: dict[str, dict]) -> None:
        self.directory = directory
        self.speakers = speakers
        """The profiles, by name (Profiles.speakers)."""

    def path(self, take: dict[str, Any]) -> Path:
        return self.directory / f"{take['id']}.wav"

    def words(self, name: str) -> dict[str, dict[str, Any]]:
        return self.speakers[name].setdefault("wake", {})

    def add(self, name: str, word: str, phrase: str, pcm: bytes, **info: Any) -> dict[str, Any]:
        """Keep a take of ``name`` saying ``phrase``; the oldest beyond MAX_TAKES go."""
        self.directory.mkdir(parents=True, exist_ok=True)
        take = {"id": uuid.uuid4().hex[:16], "at": time.time(), "seconds": round(len(pcm) / BYTE_RATE, 2), **info}
        with wave.open(str(self.path(take)), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(SAMPLE_RATE)
            wav.writeframes(pcm)
        entry = self.words(name).setdefault(word, {"phrase": phrase, "takes": []})
        entry["phrase"] = phrase
        entry["takes"].append(take)
        while len(entry["takes"]) > MAX_TAKES:
            self.path(entry["takes"].pop(0)).unlink(missing_ok=True)
        return take

    def remove(self, name: str, word: str, take_id: str | None = None) -> int:
        """Delete one take, or all of a word's; how many went."""
        entry = self.words(name).get(word)
        if entry is None:
            return 0
        gone = [t for t in entry["takes"] if take_id is None or t["id"] == take_id]
        for take in gone:
            self.path(take).unlink(missing_ok=True)
        entry["takes"] = [t for t in entry["takes"] if t not in gone]
        if not entry["takes"]:
            del self.words(name)[word]
        return len(gone)

    def forget(self, profile: dict[str, Any]) -> None:
        """A deleted speaker's takes."""
        for entry in (profile.get("wake") or {}).values():
            for take in entry.get("takes") or []:
                self.path(take).unlink(missing_ok=True)

    def find(self, name: str, word: str, take_id: str) -> Path | None:
        entry = (self.speakers.get(name) or {}).get("wake", {}).get(word)
        take = next((t for t in (entry or {}).get("takes", []) if t["id"] == take_id), None)
        return self.path(take) if take is not None else None

    def of(self, word: str, names: list[str] | None = None) -> dict[str, list[Path]]:
        """Each speaker's takes of ``word`` (of ``names``, or everyone's) that are there."""
        out = {}
        for name, profile in sorted(self.speakers.items()):
            if names is not None and name not in names:
                continue
            entry = (profile.get("wake") or {}).get(word)
            paths = [p for p in (self.path(t) for t in (entry or {}).get("takes", [])) if p.is_file()]
            if paths:
                out[name] = paths
        return out

    def phrases(self) -> dict[str, dict[str, Any]]:
        """Every recorded word: its phrase, and how many takes whose."""
        out: dict[str, dict[str, Any]] = {}
        for name, profile in sorted(self.speakers.items()):
            for word, entry in (profile.get("wake") or {}).items():
                found = out.setdefault(word, {"phrase": entry.get("phrase") or word, "speakers": {}})
                found["speakers"][name] = len(entry.get("takes") or [])
        return out


def summary(profile: dict[str, Any]) -> list[dict[str, Any]]:
    """A profile's recorded words, for the page."""
    out = []
    for word, entry in sorted((profile.get("wake") or {}).items()):
        takes = entry.get("takes") or []
        heard = [t["heard"] for t in takes if t.get("heard") is not None]
        out.append({
            "word": word,
            "phrase": entry.get("phrase") or word,
            "count": len(takes),
            "heard": sum(heard) if heard else None,
            "checked": len(heard),
            "takes": [{k: t.get(k) for k in ("id", "at", "seconds", "satellite", "score", "heard")} for t in takes],
        })
    return out
