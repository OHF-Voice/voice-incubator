"""Piper's voices, from rhasspy/piper-voices at one revision: those the
catalog lists (piper_voices.json, which scripts/piper_voices.py writes from
the repository's voices.json), each downloaded into models/piper -- the app's
data, through the image's link -- the first time it is used: the voice
replies are said in (the Settings page's, else the language's), and the one
wake words are trained with. Every file is checked against its MD5.

How a voice speaks is Piper's to tune (``SynthesisConfig``): its speaker,
when it has several, ``length_scale`` (above 1 slower), ``noise_scale`` (how
much the voice varies), ``noise_w_scale`` (how much the phonemes' lengths
vary) and ``volume``. The voice's own defaults are in its .onnx.json.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from .downloads import File

_LOGGER = logging.getLogger(__name__)

CATALOG = Path(__file__).with_name("piper_voices.json")
REPO = "https://huggingface.co/rhasspy/piper-voices/resolve"

SETTINGS = {
    # Piper's name: (lowest, highest, what it is)
    "length_scale": (0.25, 4.0, "how long it takes to say it: above 1 slower, below faster"),
    "noise_scale": (0.0, 2.0, "how much the voice varies"),
    "noise_w_scale": (0.0, 2.0, "how much the phonemes' lengths vary"),
    "volume": (0.1, 4.0, "how loud, as a multiple"),
}
"""The inference settings a voice may be given, and their ranges."""


@dataclass(frozen=True)
class Voice:
    key: str
    """As Piper names it: en_US-kristin-medium."""
    name: str
    language: str
    """en_US, en_GB."""
    quality: str
    speakers: tuple[str, ...]
    """By id; none for a voice of one speaker."""
    files: dict[str, tuple[int, str]]
    """The repository's path -> (size, MD5)."""
    revision: str

    @property
    def size(self) -> int:
        return sum(size for size, _ in self.files.values())

    def local_files(self, directory: Path) -> list[Path]:
        """Where its files are in ``directory`` (or would be)."""
        return [directory / (f"{self.key}.MODEL_CARD" if Path(path).name == "MODEL_CARD" else Path(path).name)
                for path in self.files]

    def model(self, directory: Path) -> Path:
        """Its .onnx in ``directory`` (its .onnx.json beside it)."""
        return directory / f"{self.key}.onnx"

    def missing(self, directory: Path) -> list[tuple[Path, File]]:
        """Its files not in ``directory`` yet, each where it goes (through
        the image's link, into the app's data)."""
        out = []
        for (path, (size, md5)), local in zip(self.files.items(), self.local_files(directory)):
            if not local.is_file():
                url = f"{REPO}/{self.revision}/{path}"
                out.append((local.parent.resolve() / local.name, File(local.name, url, md5, size, "md5")))
        return out

    def sample(self, speaker: str | None = None) -> str:
        """Where the repository's recording of it (of ``speaker``) is."""
        folder = Path(next(iter(self.files))).parent.as_posix()
        number = self.speakers.index(speaker) if speaker in self.speakers else 0
        return f"{REPO}/{self.revision}/{folder}/samples/speaker_{number}.mp3"

    def to_json(self) -> dict[str, object]:
        return {"key": self.key, "name": self.name.replace("_", " ").title(), "language": self.language,
                "quality": self.quality, "speakers": list(self.speakers), "size": self.size, "sample": self.sample()}


@cache
def catalog() -> dict[str, Voice]:
    """Every voice the catalog lists, by key."""
    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    return {
        key: Voice(key, v["name"], v["language"], v["quality"], tuple(v["speakers"]),
                   {path: (size, md5) for path, (size, md5) in v["files"].items()}, data["revision"])
        for key, v in data["voices"].items()
    }


def voices(languages: tuple[str, ...]) -> list[Voice]:
    """Those in ``languages`` (en_US, en_GB), by language, then name."""
    return sorted((v for v in catalog().values() if v.language in languages), key=lambda v: (v.language, v.key))


def synthesis(settings: dict[str, object], speakers: dict[str, int]) -> dict[str, object]:
    """``settings`` (the Settings page's) as Piper's SynthesisConfig takes
    them: the speaker by its id in the voice (``speakers``); what isn't set
    is the voice's own."""
    out: dict[str, object] = {name: float(settings[name]) for name in SETTINGS if settings.get(name) is not None}
    speaker = settings.get("speaker")
    if speaker is not None and str(speaker) in speakers:
        out["speaker_id"] = speakers[str(speaker)]
    return out


UNUSED_DAYS = 30
"""A voice Home Assistant asked for (voice_incubator/wyoming_server.py) is
removed once it has gone unused this long."""


class Uses:
    """When each voice was last asked for by Home Assistant, kept in ``path``
    (JSON: key -> seconds since the epoch). Those not asked for in
    UNUSED_DAYS are removed (``sweep``) -- unless the app speaks with them --
    and only those: a voice the app fetched for itself is its settings' to
    keep."""

    SAVE_EVERY = 3600.0
    """A voice's use is written down again only this long after the last."""

    def __init__(self, path: Path) -> None:
        self.path = path
        try:
            self.last: dict[str, float] = {
                str(k): float(v) for k, v in json.loads(path.read_text(encoding="utf-8")).items()
            }
        except (OSError, ValueError, AttributeError):
            self.last = {}

    def used(self, key: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        if now - self.last.get(key, 0.0) < self.SAVE_EVERY:
            return
        self.last[key] = now
        self._save()

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.last, indent=1, sort_keys=True), encoding="utf-8")
        except OSError as err:
            _LOGGER.warning("Could not write %s: %s", self.path, err)

    def sweep(self, directory: Path, kept: set[str], now: float | None = None) -> list[str]:
        """Remove from ``directory`` the voices last asked for more than
        UNUSED_DAYS ago, but those ``kept`` (by key: the app's own). Their
        keys."""
        now = time.time() if now is None else now
        voices = catalog()
        removed = []
        for key, last in sorted(self.last.items()):
            if key in kept or now - last < UNUSED_DAYS * 86400:
                continue
            voice = voices.get(key)
            for path in voice.local_files(directory) if voice is not None else []:
                path.unlink(missing_ok=True)
            del self.last[key]
            removed.append(key)
        if removed:
            _LOGGER.info("Removed Piper voices unused for %d days: %s", UNUSED_DAYS, ", ".join(removed))
            self._save()
        return removed
