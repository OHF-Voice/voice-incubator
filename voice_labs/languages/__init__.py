"""Languages: what voice_labs hears and says in one, as a pack.

The app runs in one language, set when it starts (the app's ``language``
option, ``--language``). A language's pack (``languages/<code>``, its
``PACK``) says:

- which speech models it uses: the CTC model commands are matched with, and
  the transducer that writes free text, example sentences and hints (each
  under ``models/``, its int8 build preferred), and the Piper voice;
- the language Home Assistant's speech-to-text, text-to-speech and
  conversation agent are asked in;
- the matcher's base config: filler words, name cues, compounds;
- what the app says itself (``speech.py``) and reads itself, past the
  matcher (``reading.py``): English's say what every language's has;
- its command library (``library/``);
- which skills it has, and how they read questions and answer them
  (``skills/<skill>.py``, with ``skills/numbers.py`` and ``skills/live.py``:
  English's say what every language's has), and whether GLiNER (the
  fallback's model step) understands it.

English ships with the app; another language's models are fetched for it.
The web UI stays in English whatever the language.
"""

from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from types import ModuleType
from typing import Any

DEFAULT = "en"


@dataclass(frozen=True)
class Pack:
    code: str
    """ISO 639-1, as Home Assistant and adapt-ctc take it: "en", "de"."""
    name: str
    """In English, for the UI."""
    ctc: str
    """The CTC model, under models/: ``<ctc>-int8`` when it has been made."""
    transducer: str | None
    """The transducer under models/ (``-int8`` preferred), or None."""
    transducer_about: str
    """What the transducer is, for the UI."""
    piper: str
    """The Piper voice replies are said in unless another is picked, under
    models/piper/."""
    piper_languages: tuple[str, ...] = ()
    """The Piper voices that may be picked (piper_voices): their languages."""
    adapt: dict[str, Any] = field(default_factory=dict)
    """The matcher's base config for every home: ``filler``, ``name_cues``,
    ``compounds``, ``name_endings`` (commands.base_config)."""
    skills: tuple[str, ...] = ()
    """The skills it has (voice_labs/skills/), each read and answered by its
    ``skills/<skill>.py``."""
    gliner: bool = False
    """GLiNER, the fallback's model step, understands it."""
    wake_ratio: bool = False
    """The wake word's second opinion (voice_labs/wake_ratio.py) is
    calibrated for its CTC model; without, Kaldi alone decides."""
    wake_voice: str | None = None
    """The Piper voice new wake words are trained with, under models/piper/:
    one with many speakers. None: none can be trained in this language yet."""
    directory: Path = Path(__file__).parent

    @property
    def speech(self) -> ModuleType:
        """What it says: its ``speech`` module."""
        return importlib.import_module(f"{__name__}.{self.code}.speech")

    @property
    def reading(self) -> ModuleType:
        """What it reads: its ``reading`` module."""
        return importlib.import_module(f"{__name__}.{self.code}.reading")

    def grammar(self, name: str) -> ModuleType:
        """How it reads and answers a skill's questions: ``skills/<name>.py``
        (``numbers``, ``live``, or a skill's id)."""
        return importlib.import_module(f"{__name__}.{self.code}.skills.{name}")

    @property
    def library(self) -> Path:
        """Its command library: one YAML file per section."""
        return self.directory / "library"

    @property
    def calendar(self) -> ModuleType | None:
        """Its ``calendar`` module -- when alarms and reminders are, and what
        is said about them -- if it keeps them on the calendar
        (voice_labs/alarms.py); None if its alarms are timers."""
        if not (self.directory / "calendar.py").is_file():
            return None
        return importlib.import_module(f"{__name__}.{self.code}.calendar")

    @property
    def lists(self) -> dict[str, Any]:
        """The value lists every home has in it, by name (``lists.yaml``):
        colors, on_off, cover_classes ... One of a home's own by the same
        name, in this language, takes a list's place."""
        return _lists(self.directory / "lists.yaml")

    def ctc_dir(self, root: Path) -> Path:
        """The CTC model's directory, its int8 build when there is one -- which
        may hold only its tokens, where the transducer has its head beside it
        (adapt_ctc.hybrid: the app's download)."""
        quantized = root / "models" / f"{self.ctc}-int8"
        return quantized if (quantized / "tokens.txt").exists() else root / "models" / self.ctc

    def transducer_dir(self, root: Path) -> Path | None:
        """The transducer's directory, its int8 build when there is one."""
        if self.transducer is None:
            return None
        from adapt_ctc.tdt import has_model  # pylint: disable=import-outside-toplevel

        quantized = root / "models" / f"{self.transducer}-int8"
        return quantized if has_model(quantized) else root / "models" / self.transducer

    def missing(self, root: Path) -> list[str]:
        """Its models not under ``root``/models, which it can't run without:
        the CTC model -- or its head beside the transducer -- and, when Piper's
        catalog hasn't it to download (piper_voices), the Piper voice.
        (Without its transducer, free text comes from the multilingual one,
        or the CTC model.)"""
        from adapt_ctc.hybrid import has_shared_model  # pylint: disable=import-outside-toplevel

        missing = []
        ctc = self.ctc_dir(root)
        if not (ctc / "model.onnx").is_file() and not has_shared_model(self.transducer_dir(root), ctc):
            missing.append(f"models/{self.ctc}")
        from voice_labs.piper_voices import catalog  # pylint: disable=import-outside-toplevel

        if self.piper.removesuffix(".onnx") not in catalog() and not (root / "models/piper" / self.piper).is_file():
            missing.append(f"models/piper/{self.piper}")
        return missing

    def to_json(self) -> dict[str, Any]:
        return {"code": self.code, "name": self.name, "skills": list(self.skills), "gliner": self.gliner}


def codes() -> list[str]:
    """Every language with a pack."""
    return sorted(m.name for m in pkgutil.iter_modules(__path__) if m.ispkg)


def load(code: str) -> Pack:
    """``code``'s pack. Raises ValueError for a language without one."""
    if code not in codes():
        raise ValueError(f"no language {code!r} (one of {', '.join(codes())})")
    return importlib.import_module(f"{__name__}.{code}").PACK


@cache
def _lists(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    from ruamel.yaml import YAML  # pylint: disable=import-outside-toplevel

    data = YAML(typ="safe").load(path.read_text("utf-8")) or {}  # YAML 1.2: "on" and "off" stay words
    return {str(name): values for name, values in data.items()}


_current: Pack | None = None


def current() -> Pack:
    """The app's language: the one it started in (``use``), else English."""
    return _current or load(DEFAULT)


def speech() -> ModuleType:
    """What the app's language says (``current().speech``): read when it is
    said, not when a module is imported, which is before the app has its
    language."""
    return current().speech


def reading() -> ModuleType:
    """What the app's language reads (``current().reading``), read when it
    is needed, as ``speech``."""
    return current().reading


def grammar(name: str) -> ModuleType:
    """The app's language's grammar for a skill (``current().grammar``), read
    when it is asked, as ``speech``."""
    return current().grammar(name)


def use(code: str) -> Pack:
    """Run in ``code`` from now on: the app does this once, when it starts."""
    global _current  # pylint: disable=global-statement  # one language per app
    _current = load(code)
    return _current
