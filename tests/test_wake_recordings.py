"""A speaker's takes of a wake phrase (voice_labs/wake_recordings.py): kept
with their voice, and brought in from elsewhere on the command line."""

from __future__ import annotations

import json
import subprocess
import sys
import wave
from pathlib import Path

from voice_labs.wake_recordings import WakeTakes

ROOT = Path(__file__).resolve().parent.parent


def take(path: Path, seconds: float = 1.0) -> Path:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x01\x00" * int(16000 * seconds))
    return path


def test_takes_with_the_voice(tmp_path):
    speakers = {"Jenn": {"centroid": [1.0]}, "Mike": {"centroid": [0.0]}}
    takes = WakeTakes(tmp_path / "wake_recordings", speakers)
    first = takes.add("Jenn", "okay_nabu", "okay nabu", b"\x00\x00" * 16000, satellite="Kitchen")
    takes.add("Jenn", "okay_nabu", "okay nabu", b"\x00\x00" * 8000)
    takes.add("Mike", "okay_nabu", "Okay Nabu", b"\x00\x00" * 8000)
    assert takes.path(first).is_file() and first["seconds"] == 1.0 and first["satellite"] == "Kitchen"
    assert takes.phrases() == {"okay_nabu": {"phrase": "okay nabu", "speakers": {"Jenn": 2, "Mike": 1}}}
    assert [len(v) for v in takes.of("okay_nabu", ["Jenn"]).values()] == [2]
    assert takes.find("Jenn", "okay_nabu", first["id"]) == takes.path(first)
    assert takes.remove("Jenn", "okay_nabu", first["id"]) == 1 and not takes.path(first).exists()
    takes.forget(speakers["Mike"])
    assert not takes.of("okay_nabu", ["Mike"])


def test_brought_in_on_the_command_line(tmp_path):
    profiles = tmp_path / "speakers.json"
    profiles.write_text(json.dumps({"model": "redimnet-b2", "channel": "aec", "speakers": {
        "Jenn": {"centroid": [1.0], "n_windows": 1, "speech_s": 1.0, "color": [0, 255, 64]},
    }}))
    files = [take(tmp_path / f"t{i}.wav") for i in range(3)]

    def run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", "voice_labs.wake_recordings", "--profiles", str(profiles), *args],
                              cwd=ROOT, capture_output=True, text=True, check=False)

    added = run("add", "jenn", "Okay, Nabu!", *map(str, files), "--source", "office-multicap")
    assert added.returncode == 0, added.stderr
    assert "Jenn: 3 takes of 'Okay Nabu' added" in added.stdout
    saved = json.loads(profiles.read_text())["speakers"]["Jenn"]["wake"]["okay_nabu"]
    assert saved["phrase"] == "Okay Nabu" and {t["satellite"] for t in saved["takes"]} == {"office-multicap"}
    assert len(list((tmp_path / "wake_recordings").iterdir())) == 3
    assert "Okay Nabu" in run("list").stdout
    assert run("add", "Bob", "okay nabu", str(files[0])).returncode != 0
    assert "deleted 3" in run("forget", "Jenn", "okay nabu").stdout
    assert not any((tmp_path / "wake_recordings").iterdir())
