"""The call's sounds, played from a SoundFont through FluidSynth's library on
the instrument chosen, from the pitch chosen -- dry, at the satellite's rate,
when first wanted:

- ``heard``: the command ended, and is being worked on -- up a major third;
- ``acknowledge``: done, with nothing to say -- on up a fourth, finishing the
  chord ``heard`` starts;
- ``not_understood``: nothing understood it, or it went wrong -- down a minor
  third, lower and slower;
- ``asking``: the conversation agent is asked -- up the major triad.

The SoundFont is MuseScore General's (Debian's
musescore-general-soundfont-small; MIT): its melodic presets are the
instruments, by name. Its samples are loaded only as a preset needs them --
about 10 MB to list them all, where loading it whole is over 200 MB.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

_LOGGER = logging.getLogger(__name__)

SOUNDFONT = "MuseScore_General_Lite.sf3"
SOUNDFONT_DIRS = (Path("/usr/share/sounds/sf3"),)
"""Where the SoundFont is looked for (Debian's package puts it here), before
the app's models/soundfonts/."""

RATE = 16000
SOUNDS = ("heard", "acknowledge", "not_understood", "asking")
"""The call's sounds: the command ended; done, with nothing to say; not
understood, or it went wrong; the conversation agent is asked."""

INSTRUMENT = "tine_electric_piano"
"""What the sounds are played on unless another is chosen."""
ALIASES = {"electric_piano": INSTRUMENT}
"""Instruments as settings named them before (sounds/<instrument>/)."""
PITCH = 72
"""The key the sounds start from (C5), unless one is chosen or the
instrument has its own in PITCHES."""
PITCHES = {INSTRUMENT: 79}
"""Instruments' own pitches: the electric piano from G5."""
LOWEST, HIGHEST = 36, 96
"""The pitches that may be chosen: C2 to C7."""

FAMILIES = (
    "Piano", "Chromatic percussion", "Organ", "Guitar", "Bass", "Strings", "Ensemble", "Brass",
    "Reed", "Pipe", "Synth lead", "Synth pad", "Synth effects", "Ethnic", "Percussive", "Sound effects",
)
"""General MIDI's families, eight programs each."""

LOUDNESS = -19.0
"""Where it sounds, in dB of full scale: as loud as the acknowledgement was."""
PEAK = 0.7

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def note_name(key: int) -> str:
    """A MIDI key as a note: 60 is C4."""
    return f"{NOTE_NAMES[key % 12]}{key // 12 - 1}"


def key_of(note: str) -> int:
    """A note (``note_name``'s) as its MIDI key, between LOWEST and HIGHEST.
    Raises ValueError."""
    match = re.fullmatch(r"([A-G]#?)(-?\d)", str(note).strip().upper())
    if match is None or match[1] not in NOTE_NAMES:
        raise ValueError(f"{note!r} is not a note such as C5 or F#4")
    key = NOTE_NAMES.index(match[1]) + (int(match[2]) + 1) * 12
    if not LOWEST <= key <= HIGHEST:
        raise ValueError(f"{note} is not between {note_name(LOWEST)} and {note_name(HIGHEST)}")
    return key


def notes(root: int) -> dict[str, list[tuple[float, int, float, int]]]:
    """Each sound's notes from ``root``: (start s, key, held s, velocity)."""
    return {
        "heard": [(0.0, root, 0.09, 100), (0.085, root + 4, 0.16, 105)],
        "acknowledge": [(0.0, root + 7, 0.09, 98), (0.09, root + 12, 0.2, 102)],
        "not_understood": [(0.0, root - 2, 0.15, 100), (0.16, root - 5, 0.26, 95)],
        "asking": [(0.0, root, 0.09, 100), (0.085, root + 4, 0.09, 102), (0.17, root + 7, 0.22, 108)],
    }


@dataclass(frozen=True)
class Preset:
    """One of the SoundFont's instruments."""

    id: str
    name: str
    bank: int
    program: int

    @property
    def family(self) -> str:
        return FAMILIES[self.program // 8]


class Instruments:
    """The SoundFont's instruments, and the call's sounds played on them."""

    def __init__(self, models_dir: Path | None = None) -> None:
        dirs = [*SOUNDFONT_DIRS, *([Path(models_dir) / "soundfonts"] if models_dir else [])]
        self.soundfont = next((d / SOUNDFONT for d in dirs if (d / SOUNDFONT).is_file()), None)
        self.presets: dict[str, Preset] = {}
        """By id: the name, lower case, in underscores."""
        self._lock = threading.Lock()
        """One synth at a time."""

    def load(self) -> None:
        """List the instruments (none without the SoundFont or FluidSynth)."""
        if self.soundfont is None:
            _LOGGER.warning("No %s: the call has no sounds", SOUNDFONT)
            return
        try:
            with self._lock, _Synth(self.soundfont) as synth:
                listed = synth.presets()
        except OSError as err:
            _LOGGER.warning("FluidSynth can't play %s (%s): the call has no sounds", self.soundfont, err)
            return
        for bank, program, name in sorted(listed):
            # Drum kits, and presets whose loudness a controller sets
            # (MuseScore's "Expr."), aren't instruments here.
            if bank >= 128 or name.endswith("Expr."):
                continue
            slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
            if slug in self.presets:
                slug = f"{slug}_{bank}"
            self.presets[slug] = Preset(slug, name, bank, program)
        _LOGGER.info("%d instruments in %s", len(self.presets), self.soundfont)

    def resolve(self, instrument: str | None) -> str | None:
        """The instrument ``instrument`` names (an alias's, or INSTRUMENT for
        none), if there is one."""
        wanted = ALIASES.get(instrument or INSTRUMENT, instrument or INSTRUMENT)
        return wanted if wanted in self.presets else None

    @staticmethod
    def pitch(instrument: str | None) -> int:
        """The key ``instrument``'s sounds start from unless one is chosen."""
        return PITCHES.get(ALIASES.get(instrument or INSTRUMENT, instrument or INSTRUMENT), PITCH)

    def render(self, instrument: str, key: int) -> dict[str, np.ndarray]:
        """SOUNDS on ``instrument`` (resolved) from ``key``, leveled."""
        preset = self.presets[instrument]
        assert self.soundfont is not None
        with self._lock, _Synth(self.soundfont) as synth:
            synth.select(preset.bank, preset.program)
            return {name: _leveled(synth.play(played)) for name, played in notes(key).items()}


def _leveled(x: np.ndarray) -> np.ndarray:
    """At LOUDNESS where it sounds, its peak at most PEAK."""
    sounding = x[np.abs(x) > 0.003]
    if not len(sounding):
        return x
    loudness = 20 * np.log10(np.sqrt(np.mean(sounding**2)))
    return x * min(10 ** ((LOUDNESS - loudness) / 20), PEAK / np.abs(x).max())


_LIBRARY: ctypes.CDLL | None = None


def _library() -> ctypes.CDLL:
    """FluidSynth's library, loaded once. Raises OSError."""
    global _LIBRARY  # pylint: disable=global-statement
    if _LIBRARY is not None:
        return _LIBRARY
    fs = ctypes.CDLL(ctypes.util.find_library("fluidsynth") or "libfluidsynth.so.3")
    p, i, d, s = ctypes.c_void_p, ctypes.c_int, ctypes.c_double, ctypes.c_char_p
    for name, restype, argtypes in (
        ("new_fluid_settings", p, []),
        ("delete_fluid_settings", None, [p]),
        ("fluid_settings_setnum", i, [p, s, d]),
        ("fluid_settings_setint", i, [p, s, i]),
        ("new_fluid_synth", p, [p]),
        ("delete_fluid_synth", None, [p]),
        ("fluid_synth_sfload", i, [p, s, i]),
        ("fluid_synth_get_sfont_by_id", p, [p, i]),
        ("fluid_sfont_iteration_start", None, [p]),
        ("fluid_sfont_iteration_next", p, [p]),
        ("fluid_preset_get_name", s, [p]),
        ("fluid_preset_get_banknum", i, [p]),
        ("fluid_preset_get_num", i, [p]),
        ("fluid_synth_program_select", i, [p, i, i, i, i]),
        ("fluid_synth_noteon", i, [p, i, i, i]),
        ("fluid_synth_noteoff", i, [p, i, i]),
        ("fluid_synth_all_sounds_off", i, [p, i]),
        ("fluid_synth_write_float", i, [p, i, p, i, i, p, i, i]),
    ):
        function = getattr(fs, name)
        function.restype, function.argtypes = restype, argtypes
    _LIBRARY = fs
    return fs


class _Synth:
    """A FluidSynth synth playing one channel, the SoundFont loaded -- its
    samples only as the preset selected needs them -- and let go after."""

    def __init__(self, soundfont: Path) -> None:
        fs = self.fs = _library()
        self.settings = fs.new_fluid_settings()
        fs.fluid_settings_setnum(self.settings, b"synth.sample-rate", float(RATE))
        fs.fluid_settings_setint(self.settings, b"synth.dynamic-sample-loading", 1)
        fs.fluid_settings_setint(self.settings, b"synth.reverb.active", 0)  # dry: it ends when it ends
        fs.fluid_settings_setint(self.settings, b"synth.chorus.active", 0)
        self.synth = fs.new_fluid_synth(self.settings)
        self.font = fs.fluid_synth_sfload(self.synth, str(soundfont).encode(), 0)
        if self.font < 0:
            self.close()
            raise OSError(f"can't load {soundfont}")

    def __enter__(self) -> _Synth:
        return self

    def __exit__(self, *_) -> None:
        self.close()

    def close(self) -> None:
        if self.synth:
            self.fs.delete_fluid_synth(self.synth)
            self.synth = None
        if self.settings:
            self.fs.delete_fluid_settings(self.settings)
            self.settings = None

    def presets(self) -> list[tuple[int, int, str]]:
        """(bank, program, name) of each."""
        fs = self.fs
        font = fs.fluid_synth_get_sfont_by_id(self.synth, self.font)
        fs.fluid_sfont_iteration_start(font)
        listed = []
        while preset := fs.fluid_sfont_iteration_next(font):
            listed.append((fs.fluid_preset_get_banknum(preset), fs.fluid_preset_get_num(preset),
                           fs.fluid_preset_get_name(preset).decode(errors="replace").strip()))
        return listed

    def select(self, bank: int, program: int) -> None:
        if self.fs.fluid_synth_program_select(self.synth, 0, self.font, bank, program) != 0:
            raise OSError(f"no preset {bank}:{program}")

    def play(self, played: list[tuple[float, int, float, int]], tail: float = 0.8) -> np.ndarray:
        """``played`` (``notes``'), up to where it has died away."""
        fs, synth = self.fs, self.synth
        fs.fluid_synth_all_sounds_off(synth, 0)
        self._samples(0.05)
        events = sorted([(s, 1, k, v) for s, k, _, v in played] + [(s + d, 0, k, 0) for s, k, d, _ in played])
        out, now = [], 0.0
        for when, on, key, velocity in events:
            out.append(self._samples(when - now))
            now = when
            if on:
                fs.fluid_synth_noteon(synth, 0, key, velocity)
            else:
                fs.fluid_synth_noteoff(synth, 0, key)
        out.append(self._samples(tail))
        x = np.concatenate(out)
        # Up to where it has died away, faded at the very end.
        heard = np.nonzero(np.abs(x) > 0.002)[0]
        if not len(heard):
            return np.zeros(0)
        x = x[: heard[-1] + int(0.02 * RATE)]
        fade = min(int(0.015 * RATE), len(x))
        x[len(x) - fade :] *= np.linspace(1.0, 0.0, fade)
        return x

    def _samples(self, seconds: float) -> np.ndarray:
        n = int(round(seconds * RATE))
        if n <= 0:
            return np.zeros(0)
        left, right = (ctypes.c_float * n)(), (ctypes.c_float * n)()
        self.fs.fluid_synth_write_float(self.synth, n, left, 0, 1, right, 0, 1)
        return (np.frombuffer(left, dtype=np.float32) + np.frombuffer(right, dtype=np.float32)).astype(np.float64) / 2
