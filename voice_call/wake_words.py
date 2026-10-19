"""The wake words a satellite can listen for.

Two kinds:

* **Built in** -- ``models/wake``: "Okay Nabu", "Hey Jarvis", "Hey Mycroft".
  Each has a verifier entry with its confusables (``wake.yaml``), so the
  on-device detector runs at a low cutoff and the verifier refuses what that
  lets through. Across a room the manifests' own cutoffs cost most of the
  recall.
* **Custom** -- a ``wake_words`` directory: a microWakeWord manifest (``.json``)
  and its model (``.tflite``), as ESPHome and Tater ship them. They run at
  their manifest's cutoff, the one they were trained for, and have no
  verifier until given confusables on the Wake words page.

The Wake words page may set any wake word's cutoff (``tuning``).

Either way the command is placed after the wake phrase found again in the CTC
evidence (``phrases``): for a custom word, its manifest's ``wake_word`` text.

``vad`` and ``stop`` live in ``models/wake`` too but are not wake words: the
VAD gates the others, and "stop" is pushed only while a timer rings.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

from .mixednet import WakeModel, load as load_wake_model

_LOGGER = logging.getLogger(__name__)

VAD = "vad"
STOP = "stop"
SPECIAL = frozenset({VAD, STOP})
DEFAULT_WAKE_WORDS = ("okay_nabu",)

MAX_WAKE_WORDS = 1
"""Wake words a satellite listens for at once. The device has room for more
(``model_slots``), but for now each satellite has one: the home's preferred
wake word, or its own."""

PHRASES: dict[str, tuple[str, ...]] = {
    # How Parakeet writes them. Placement only -- the verifier has already
    # decided it was the wake word -- so these are the readings it prefers,
    # not confusables. It has no word "Nabu".
    "okay_nabu": ("okay nabu", "okay naboo", "ok nabu", "ok naboo", "okay now"),
    "hey_jarvis": ("hey jarvis", "hi jarvis", "hey jervis", "a jarvis"),
    "hey_mycroft": ("hey mycroft", "hey my craft", "hey microsoft", "hi mycroft", "hey my croft"),
}

VERIFIED_CUTOFF: dict[str, float] = {
    # The first stage's cutoff when the verifier stands behind it, per word
    # (README "Wake words"): low enough to keep the wake words the manifest's
    # cutoff refuses, high enough that the detector rarely fires on ordinary
    # speech and music -- every firing lights the ring and ducks the music
    # until the verifier refuses it. Firings per hour over 12 hours of speech
    # and music at the Voice PE:
    #
    #   cutoff       0.2   0.35   0.5   0.7   0.95
    #   okay_nabu    0.25
    #   hey_jarvis   2.6   1.5    0.8   0.2   0
    #   hey_mycroft  16.7  9.4    5.1   2.7   0.9
    #
    # The verifier accepted none of them at any cutoff. "Hey Mycroft" at 0.7
    # still finds 95% of synthetic wake words against 90% at 0.9.
    "okay_nabu": 0.2,
    "hey_jarvis": 0.5,
    "hey_mycroft": 0.7,
}
DEFAULT_VERIFIED_CUTOFF = 0.5

RATIO_THRESHOLD: dict[str, float] = {
    # The second opinion (voice_call/wake_ratio.py) per word: calibrated for
    # "okay nabu"; tighter for "hey jarvis", which the confusables near it
    # (synthetic) otherwise get through more often at no gain in recall.
    "okay_nabu": 1.3,
    "hey_jarvis": 1.0,
    "hey_mycroft": 1.2,
}

MAX_CUTOFF = 0.99
"""The device fires when the window's average is above the cutoff, so a
manifest's 1.0 would never fire (one in the Tater catalog has it)."""

RESERVED_SLOTS = 2
"""Device model slots that are not wake words: the VAD gate and "stop"."""
DEFAULT_MODEL_SLOTS = 4
"""What firmware that does not say has."""


@dataclass(frozen=True)
class WakeWord:
    """One wake word a satellite can be given."""

    id: str
    name: str
    tflite: Path
    manifest_path: Path
    manifest: dict[str, Any]
    custom: bool
    verified: bool
    cutoff: float
    """The first stage's cutoff, as the device will run it."""
    default_cutoff: float
    """The cutoff it runs at unless the Wake words page sets one."""
    phrases: tuple[str, ...]
    """How the wake phrase is found again in the CTC evidence."""

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "custom": self.custom,
            "verified": self.verified,
            "cutoff": round(self.cutoff, 3),
            "default_cutoff": round(self.default_cutoff, 3),
            "manifest_cutoff": _manifest_cutoff(self.manifest),
            "phrases": list(self.phrases),
            "languages": self.manifest.get("trained_languages") or [],
            "author": self.manifest.get("author"),
        }


def spoken(text: str) -> str:
    """A manifest's wake word as words: "Hey_Tater!" -> "hey tater"."""
    return " ".join(re.sub(r"[^\w']+|_", " ", text.lower()).split())


def _manifest_cutoff(manifest: dict[str, Any]) -> float:
    return float((manifest.get("micro") or {}).get("probability_cutoff", 0.85))


def read_manifest(path: Path) -> tuple[dict[str, Any], Path] | None:
    """A microWakeWord manifest and its model, or None if this is some other
    JSON (Tater's openWakeWord and bundle files sit beside them)."""
    try:
        manifest = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(manifest, dict) or manifest.get("type") != "micro":
        return None
    model = manifest.get("model")
    if not isinstance(model, str) or not model.endswith(".tflite"):
        return None
    tflite = path.parent / Path(model).name
    if not tflite.is_file():
        return None
    return manifest, tflite


class WakeWords:
    """What is on disk, scanned on demand, and the lowered models, cached."""

    def __init__(
        self,
        builtin_dir: Path,
        custom_dir: Path | None,
        verified: Callable[[str], bool],
        cutoff_override: float | None = None,
        tuning: Callable[[], dict[str, dict[str, Any]]] | None = None,
    ) -> None:
        self.builtin_dir = Path(builtin_dir)
        self.custom_dir = Path(custom_dir) if custom_dir is not None else None
        self.verified = verified
        self.cutoff_override = cutoff_override
        """Replaces every verified word's default first-stage cutoff
        (``--wake-cutoff``); a cutoff set on the Wake words page still wins."""
        self.tuning = tuning or dict
        """What the Wake words page set, per wake word (``wake_words.yaml``)."""
        self._words: dict[str, WakeWord] = {}
        self._signature: tuple = ()
        self._models: dict[tuple, WakeModel] = {}
        self.errors: dict[str, str] = {}
        """Custom models that could not be lowered for the device, by id."""

    # ------------------------------------------------------------- catalog

    def _files(self) -> list[tuple[Path, bool]]:
        files = [(p, False) for p in sorted(self.builtin_dir.glob("*.json"))]
        if self.custom_dir is not None and self.custom_dir.is_dir():
            files += [(p, True) for p in sorted(self.custom_dir.rglob("*.json"))]
        return files

    def scan(self) -> dict[str, WakeWord]:
        """Every wake word, by id. Re-read only when a file changed."""
        files = self._files()
        signature = tuple((str(p), _mtime(p)) for p, _ in files)
        if signature == self._signature:
            return self._words
        tuning = self.tuning()
        words: dict[str, WakeWord] = {}
        for path, custom in files:
            found = read_manifest(path)
            if found is None:
                continue
            manifest, tflite = found
            word_id = tflite.stem
            if word_id in SPECIAL or (custom and word_id in words and not words[word_id].custom):
                continue  # never shadows "stop", the VAD or a built-in word
            if word_id in words and words[word_id].custom == custom:
                if words[word_id].tflite.resolve() != tflite.resolve():
                    _LOGGER.warning("Wake word %s: %s is used, not %s", word_id, words[word_id].tflite, tflite)
                continue  # Tater ships an .esphome.json twin beside the .json
            verified = self.verified(word_id)
            name = str(manifest.get("wake_word") or manifest.get("label") or word_id)
            if "_" in name and " " not in name:
                name = spoken(name).title()  # "hey_marcel": Hey Marcel
            default = self._default_cutoff(word_id, manifest, custom, verified)
            tuned = (tuning.get(word_id) or {}).get("cutoff")
            words[word_id] = WakeWord(
                id=word_id,
                name=name,
                tflite=tflite,
                manifest_path=path,
                manifest=manifest,
                custom=custom,
                verified=verified,
                cutoff=min(float(tuned), MAX_CUTOFF) if tuned is not None else default,
                default_cutoff=default,
                phrases=PHRASES.get(word_id) or ((spoken(name),) if spoken(name) else ()),
            )
        self._words = dict(sorted(words.items(), key=lambda kv: (kv[1].custom, kv[1].name.lower())))
        self._signature = signature
        return self._words

    def _default_cutoff(self, word_id: str, manifest: dict[str, Any], custom: bool, verified: bool) -> float:
        """A verified built-in word's low cutoff; a custom word's own, from
        its manifest, even when it is verified: lowering it is the page's to
        do (``--wake-cutoff`` aside)."""
        if verified and self.cutoff_override is not None:
            return self.cutoff_override
        if not verified or custom:
            return min(_manifest_cutoff(manifest), MAX_CUTOFF)
        return VERIFIED_CUTOFF.get(word_id, DEFAULT_VERIFIED_CUTOFF)

    def forget(self) -> None:
        """Scan again next time: what is verified, or a cutoff, changed."""
        self._signature = ()

    def get(self, word_id: str) -> WakeWord | None:
        return self.scan().get(word_id)

    def resolve(self, heard: str) -> str | None:
        """The id of what a device reported: an id, or (older firmware) the
        manifest's name -- "Okay Nabu"."""
        if not heard:
            return None
        if heard in SPECIAL:
            return heard
        words = self.scan()
        if heard in words:
            return heard
        key = "_".join(spoken(heard).split())
        if key in words or key in SPECIAL:
            return key
        return next((w.id for w in words.values() if spoken(w.name) == spoken(heard)), None)

    def phrases(self, word_id: str) -> tuple[str, ...]:
        """How a wake word is found again in the CTC evidence."""
        word = self.get(word_id)
        if word is not None and word.phrases:
            return word.phrases
        return PHRASES.get(word_id) or PHRASES[DEFAULT_WAKE_WORDS[0]]

    def chosen(self, settings: dict[str, Any], stored: dict[str, Any], default: tuple[str, ...]) -> list[str]:
        """A satellite's wake words: its own, else the home's preferred one,
        else the app's default. Unknown ids are left out, and only the first
        ``MAX_WAKE_WORDS`` are kept."""
        words = self.scan()
        for source in (stored.get("wake_words"), settings.get("wake_words"), default):
            if source:
                picked = [w for w in dict.fromkeys(source) if w in words]
                if picked:
                    return picked[:MAX_WAKE_WORDS]
        return ([w for w in DEFAULT_WAKE_WORDS if w in words] or list(words))[:MAX_WAKE_WORDS]

    # -------------------------------------------------------------- models

    def model(self, word_id: str) -> WakeModel | None:
        """The lowered model for the device, at the cutoff it runs at. Its
        ``wake_word`` is the id: that is what the device reports back."""
        if word_id in SPECIAL:
            path = self.builtin_dir / f"{word_id}.tflite"
            if not path.is_file():
                return None
            return self._lower(path, path.with_suffix(".json"), word_id, None)
        word = self.get(word_id)
        if word is None:
            return None
        return self._lower(word.tflite, word.manifest_path, word_id, word.cutoff)

    def _lower(self, tflite: Path, manifest: Path, word_id: str, cutoff: float | None) -> WakeModel | None:
        key = (str(tflite), _mtime(tflite), cutoff)
        model = self._models.get(key)
        if model is not None:
            return model
        try:
            model = load_wake_model(tflite, manifest)
        except Exception as err:  # noqa: BLE001 - a bad custom model must not stop the app
            self.errors[word_id] = f"{type(err).__name__}: {err}"
            _LOGGER.error("Wake word %s cannot run on the device: %s", word_id, self.errors[word_id])
            return None
        self.errors.pop(word_id, None)
        model = replace(model, model_id=word_id, wake_word=word_id)
        if cutoff is not None:
            model = replace(model, probability_cutoff=cutoff)
        self._models[key] = model
        return model


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0
