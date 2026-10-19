"""What happened on each call, for the web UI's call log and debugging.

Every call leaves a record: each stage the pipeline ran, with its timing and
result -- wake word verification (Kaldi, the retry, the second opinion), where
the command was cut, the speaker, the recognition (every command that matched
or was rejected, and why), the command chosen and the scope that decided what
happens, the action sequence and variables, and what was said. The last
records are kept in memory.

With debugging on (globally, or in a debug session on one satellite) the
record is also written to disk with the call's audio: the pre-roll from before
"okay nabu" through to the end of the command, on both captures, plus the cut
command. A debug session also chooses whether commands really run or are only
shown (dry run).
"""

from __future__ import annotations

import json
import logging
import shutil
import time
import wave
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .protocol import BYTES_PER_SAMPLE, SAMPLE_RATE

_LOGGER = logging.getLogger(__name__)

RECENT = 100


@dataclass
class DebugSession:
    satellite: str
    started: float = field(default_factory=time.time)
    dry_run: bool = True
    calls: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "satellite": self.satellite,
            "started": self.started,
            "dry_run": self.dry_run,
            "calls": self.calls,
        }


@dataclass
class CallRecord:
    id: str
    kind: str
    """``call`` from a satellite, ``try`` (typed), or ``replay``."""

    satellite: dict[str, Any]
    started: float = field(default_factory=time.time)
    stages: list[dict[str, Any]] = field(default_factory=list)
    outcome: str = "in progress"
    summary: str = ""
    audio: dict[str, str] = field(default_factory=dict)
    """Saved audio: name -> file name in the record's directory."""

    marks: dict[str, float] = field(default_factory=dict)
    """Moments in the call audio, in seconds: detection, wake word, cut..."""

    kept: bool = False
    debug_session: bool = False
    dry_run: bool = False
    finished: float | None = None

    def stage(self, stage: str, /, **data: Any) -> dict[str, Any]:
        entry = {"stage": stage, "at": round(time.time() - self.started, 3), **data}
        self.stages.append(entry)
        return entry

    def to_json(self, full: bool = True) -> dict[str, Any]:
        out = {
            "id": self.id,
            "kind": self.kind,
            "satellite": self.satellite,
            "started": self.started,
            "finished": self.finished,
            "outcome": self.outcome,
            "summary": self.summary,
            "audio": self.audio,
            "kept": self.kept,
            "debug_session": self.debug_session,
            "dry_run": self.dry_run,
        }
        if full:
            out["stages"] = self.stages
            out["marks"] = self.marks
        else:
            # For the list: the wake word, and what it came in with.
            start = next((s for s in self.stages if s.get("stage") == "call_start"), None)
            if start is not None:
                out["wake"] = {
                    k: start[k] for k in ("wake_word", "name", "probability", "max_probability", "cutoff") if k in start
                }
        return out

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "CallRecord":
        record = cls(data["id"], data.get("kind", "call"), data.get("satellite") or {})
        for key in (
            "started", "stages", "outcome", "summary", "audio", "marks", "kept",
            "debug_session", "dry_run", "finished",
        ):
            if key in data:
                setattr(record, key, data[key])
        return record


class CallLog:
    def __init__(self, calls_dir: Path, settings: dict[str, Any]) -> None:
        self.calls_dir = Path(calls_dir)
        self.settings = settings
        """The store's settings: ``keep_audio`` and ``max_saved_calls``."""

        self.recent: deque[CallRecord] = deque(maxlen=RECENT)
        self.sessions: dict[str, DebugSession] = {}
        self._counter = 0
        self._load_saved()

    def _load_saved(self) -> None:
        if not self.calls_dir.is_dir():
            return
        records = []
        for path in sorted(self.calls_dir.glob("*/trace.json")):
            try:
                records.append(CallRecord.from_json(json.loads(path.read_text("utf-8"))))
            except (OSError, ValueError, KeyError) as err:
                _LOGGER.warning("Unreadable call record %s: %s", path, err)
        for record in sorted(records, key=lambda r: r.started)[-RECENT:]:
            self.recent.append(record)

    # -------------------------------------------------------------- records

    def begin(self, kind: str, satellite: dict[str, Any]) -> CallRecord:
        self._counter += 1
        stamp = time.strftime("%Y%m%d-%H%M%S")
        key = str(satellite.get("id") or "app")[-6:]
        record = CallRecord(f"{stamp}-{self._counter:04d}-{key}", kind, satellite)
        session = self.sessions.get(str(satellite.get("id")))
        if session is not None and kind == "call":
            record.debug_session = True
            record.dry_run = session.dry_run
            session.calls.append(record.id)
        self.recent.append(record)
        return record

    def keeps_audio(self, record: CallRecord) -> bool:
        return bool(self.settings.get("keep_audio")) or record.debug_session

    def finish(
        self,
        record: CallRecord,
        outcome: str,
        summary: str = "",
        audio: dict[str, bytes] | None = None,
    ) -> None:
        record.outcome = outcome
        record.summary = summary
        record.finished = time.time()
        if not (audio and self.keeps_audio(record)):
            return
        directory = self.calls_dir / record.id
        try:
            directory.mkdir(parents=True, exist_ok=True)
            for name, pcm in audio.items():
                if not pcm:
                    continue
                file_name = f"{name}.wav"
                with wave.open(str(directory / file_name), "wb") as wav:
                    wav.setnchannels(1)
                    wav.setsampwidth(BYTES_PER_SAMPLE)
                    wav.setframerate(SAMPLE_RATE)
                    wav.writeframes(pcm)
                record.audio[name] = file_name
            record.kept = True
            (directory / "trace.json").write_text(
                json.dumps(record.to_json(), indent=1, default=str), "utf-8"
            )
        except OSError as err:
            _LOGGER.error("Could not save call %s: %s", record.id, err)
        self._prune()

    def _prune(self) -> None:
        limit = int(self.settings.get("max_saved_calls") or 0)
        if limit <= 0 or not self.calls_dir.is_dir():
            return
        saved = sorted(p for p in self.calls_dir.iterdir() if p.is_dir())
        for path in saved[: max(0, len(saved) - limit)]:
            shutil.rmtree(path, ignore_errors=True)
            for record in self.recent:
                if record.id == path.name:
                    record.kept = False
                    record.audio = {}

    def get(self, call_id: str) -> CallRecord | None:
        for record in self.recent:
            if record.id == call_id:
                return record
        path = self.calls_dir / call_id / "trace.json"
        if path.is_file():
            return CallRecord.from_json(json.loads(path.read_text("utf-8")))
        return None

    def audio_path(self, call_id: str, name: str) -> Path | None:
        record = self.get(call_id)
        if record is None or name not in record.audio:
            return None
        path = self.calls_dir / call_id / record.audio[name]
        return path if path.is_file() else None

    def delete(self, call_id: str) -> None:
        shutil.rmtree(self.calls_dir / call_id, ignore_errors=True)
        self.recent = deque((r for r in self.recent if r.id != call_id), maxlen=RECENT)

    def list(self, satellite: str | None = None) -> list[dict[str, Any]]:
        records = [
            r for r in self.recent if satellite is None or str(r.satellite.get("id")) == satellite
        ]
        return [r.to_json(full=False) for r in reversed(records)]

    # ------------------------------------------------------- debug sessions

    def start_session(self, satellite: str, dry_run: bool = True) -> DebugSession:
        session = self.sessions.get(satellite)
        if session is None:
            session = self.sessions[satellite] = DebugSession(satellite, dry_run=dry_run)
        session.dry_run = dry_run
        return session

    def stop_session(self, satellite: str) -> None:
        self.sessions.pop(satellite, None)
