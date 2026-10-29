"""The satellite's own sounds, in each instrument (sounds/<instrument>/),
played from a SoundFont through FluidSynth's library, dry, at the
satellite's 16 kHz:

- ``heard``: the command ended, and is being worked on -- up a major third;
- ``acknowledge``: done, with nothing to say -- on up a fourth, finishing the
  chord ``heard`` starts;
- ``not_understood``: nothing understood it, or it went wrong -- down a minor
  third, lower and slower;
- ``asking``: the conversation agent is asked -- up the major triad.

    .venv/bin/python scripts/make_sounds.py [--soundfonts /usr/share/sounds/sf2] [--out sounds]

Needs libfluidsynth and the SoundFonts INSTRUMENTS names (Debian:
libfluidsynth3, fluid-soundfont-gm, musescore-general-soundfont-lossless).
"""

from __future__ import annotations

import argparse
import ctypes
from pathlib import Path

import numpy as np
import soundfile as sf

RATE = 16000
INSTRUMENTS = {
    # (SoundFont, bank, program counted from 0, the key heard starts on)
    "electric_piano": ("FluidR3_GM.sf2", 0, 4, 79),  # General MIDI's Electric Piano 1, from G5
    "koto": ("MuseScore_General_Full.sf2", 0, 107, 72),  # from C5
}
LOUDNESS = -19.0
"""Where it sounds, in dB of full scale: as loud as the acknowledgement was."""
PEAK = 0.7



def sounds(root: int) -> dict[str, list[tuple[float, int, float, int]]]:
    """Each sound's notes from ``root``: (start s, key, held s, velocity)."""
    return {
        "heard": [(0.0, root, 0.09, 100), (0.085, root + 4, 0.16, 105)],
        "acknowledge": [(0.0, root + 7, 0.09, 98), (0.09, root + 12, 0.2, 102)],
        "not_understood": [(0.0, root - 2, 0.15, 100), (0.16, root - 5, 0.26, 95)],
        "asking": [(0.0, root, 0.09, 100), (0.085, root + 4, 0.09, 102), (0.17, root + 7, 0.22, 108)],
    }


class Synth:
    """FluidSynth's library, playing one channel of its SoundFonts."""

    def __init__(self) -> None:
        fs = self.fs = ctypes.CDLL("libfluidsynth.so.3")
        fs.new_fluid_settings.restype = ctypes.c_void_p
        fs.new_fluid_synth.restype = ctypes.c_void_p
        fs.new_fluid_synth.argtypes = [ctypes.c_void_p]
        fs.fluid_settings_setnum.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_double]
        fs.fluid_settings_setint.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        fs.fluid_synth_sfload.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        fs.fluid_synth_program_select.argtypes = [ctypes.c_void_p] + [ctypes.c_int] * 4
        fs.fluid_synth_noteon.argtypes = [ctypes.c_void_p] + [ctypes.c_int] * 3
        fs.fluid_synth_noteoff.argtypes = [ctypes.c_void_p] + [ctypes.c_int] * 2
        fs.fluid_synth_all_sounds_off.argtypes = [ctypes.c_void_p, ctypes.c_int]
        fs.fluid_synth_write_float.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_int,
                                               ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        settings = fs.new_fluid_settings()
        fs.fluid_settings_setnum(settings, b"synth.sample-rate", float(RATE))
        fs.fluid_settings_setint(settings, b"synth.reverb.active", 0)  # dry: it ends when it ends
        fs.fluid_settings_setint(settings, b"synth.chorus.active", 0)
        self.synth = fs.new_fluid_synth(settings)
        self.fonts: dict[Path, int] = {}

    def render(
        self, soundfont: Path, bank: int, program: int, notes: list[tuple[float, int, float, int]], tail: float = 0.8
    ) -> np.ndarray:
        fs, synth = self.fs, self.synth
        if soundfont not in self.fonts:
            self.fonts[soundfont] = fs.fluid_synth_sfload(synth, str(soundfont).encode(), 0)
            if self.fonts[soundfont] < 0:
                raise SystemExit(f"Can't load {soundfont}")
        fs.fluid_synth_all_sounds_off(synth, 0)
        fs.fluid_synth_program_select(synth, 0, self.fonts[soundfont], bank, program)
        self._samples(0.05)
        events = sorted([(s, 1, k, v) for s, k, _, v in notes] + [(s + d, 0, k, 0) for s, k, d, _ in notes])
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
        x = x[: heard[-1] + int(0.02 * RATE)]
        fade = int(0.015 * RATE)
        x[-fade:] *= np.linspace(1.0, 0.0, fade)
        return x

    def _samples(self, seconds: float) -> np.ndarray:
        n = int(round(seconds * RATE))
        if n <= 0:
            return np.zeros(0)
        left, right = (ctypes.c_float * n)(), (ctypes.c_float * n)()
        self.fs.fluid_synth_write_float(self.synth, n, left, 0, 1, right, 0, 1)
        return (np.frombuffer(left, dtype=np.float32) + np.frombuffer(right, dtype=np.float32)).astype(np.float64) / 2


def leveled(x: np.ndarray) -> np.ndarray:
    """At LOUDNESS where it sounds, its peak at most PEAK."""
    sounding = x[np.abs(x) > 0.003]
    loudness = 20 * np.log10(np.sqrt(np.mean(sounding**2)))
    return x * min(10 ** ((LOUDNESS - loudness) / 20), PEAK / np.abs(x).max())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--soundfonts", type=Path, default=Path("/usr/share/sounds/sf2"))
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parent.parent / "sounds")
    args = parser.parse_args()
    synth = Synth()
    for instrument, (soundfont, bank, program, root) in INSTRUMENTS.items():
        (args.out / instrument).mkdir(parents=True, exist_ok=True)
        for name, notes in sounds(root).items():
            x = leveled(synth.render(args.soundfonts / soundfont, bank, program, notes))
            path = args.out / instrument / f"{name}.flac"
            sf.write(path, x, RATE, subtype="PCM_16")
            print(f"{path}: {len(x) / RATE:.2f}s")


if __name__ == "__main__":
    main()
