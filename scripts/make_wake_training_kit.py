"""Make the kit the app trains wake words with (voice_labs/wake_training.py).

What a new wake word is trained against is the same for every word:
negatives -- speech, music, noise and confusable phrases, as microWakeWord
features -- to learn what it is not. This puts them in one directory::

    <out>/kit.yaml
    <out>/negatives/<set>/training/<set>_mmap     (--clips of each, at random)
    <out>/negatives/<set>/validation/<set>_mmap   (--validation, when it has some)
    <out>/rirs/*.wav, <out>/background/*.wav       (optional: --rirs, --background)

Random 5,000 spectrograms of each set cost about 4 points of recall against
the whole 54.5 GB pool, in about 2.9 GB; 1,250 cost about 13
(ON_DEVICE_WARM_START.md). The sets and their weights are that recipe's: the
default, with the pool's two roots, is::

    .venv/bin/python scripts/make_wake_training_kit.py --out models/wake-training \\
        --features /media/cache/mww-v30/negative_features \\
        --datasets /media/cache/mww-v30/negative_datasets

or name each set (``--set name=dir:sampling_weight:penalty_weight``). Put the
kit at models/wake-training (a working copy's) or in the app's config
directory as wake_training/ (the image doesn't carry it).

Needs mmap_ninja (and numpy, PyYAML).
"""

from __future__ import annotations

import argparse
import random
import shutil
import sys
from pathlib import Path

import numpy as np
import yaml

SCALE = 25.6
"""Float features are the frontend's uint16 output divided by this."""

RECIPE = [
    # (set, root, sampling weight, penalty weight) -- ON_DEVICE_WARM_START.md's.
    ("confusables-qwen", "features", 1.0, 8.0),
    ("edge", "features", 0.25, 8.0),
    *((f"synthetic-negatives-{code}", "features", 1.5, 8.0) for code in ("ca", "de", "en", "es", "fr", "it", "nl")),
    ("dinner_party", "datasets", 15.0, 100.0),
    ("no_speech", "datasets", 3.0, 50.0),
    ("speech", "datasets", 25.0, 50.0),
]
POSITIVE = {"sampling_weight": 14.0, "penalty_weight": 3.0}


def _mmaps(directory: Path) -> list[Path]:
    return sorted(p for p in directory.glob("**/*_mmap") if p.is_dir())


def _as_uint16(spectrogram: np.ndarray) -> np.ndarray | None:
    """Float features back to the frontend's uint16, when that is exact."""
    if np.issubdtype(spectrogram.dtype, np.uint16):
        return spectrogram
    values = np.rint(spectrogram.astype(np.float64) * SCALE)
    if values.min() < 0 or values.max() > 65535 or np.abs(values / SCALE - spectrogram).max() > 1e-3:
        return None
    return values.astype(np.uint16)


def materialise(sources: list[Path], out: Path, count: int, rng: random.Random) -> int:
    """``count`` spectrograms of the sources' mmaps, at random, as one mmap."""
    from mmap_ninja.ragged import RaggedMmap  # pylint: disable=import-outside-toplevel

    mmaps = [RaggedMmap(str(path)) for path in sources]
    every = [(m, i) for m, found in enumerate(mmaps) for i in range(len(found))]
    picked = sorted(rng.sample(every, min(count, len(every))))
    if not picked:
        return 0
    # uint16 where the set allows it (half the size of float32), else float32.
    sample = [mmaps[m][i] for m, i in picked[:: max(1, len(picked) // 50)]]
    exact = all(_as_uint16(s) is not None for s in sample)

    def spectrograms():
        for m, i in picked:
            spectrogram = mmaps[m][i]
            if exact:
                converted = _as_uint16(spectrogram)
                yield converted if converted is not None else np.rint(spectrogram * SCALE).astype(np.uint16)
            elif np.issubdtype(spectrogram.dtype, np.uint16):
                yield spectrogram.astype(np.float32) / SCALE
            else:
                yield spectrogram.astype(np.float32)

    if out.exists():
        shutil.rmtree(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    RaggedMmap.from_generator(out_dir=str(out), sample_generator=spectrograms(), batch_size=500, verbose=False)
    return len(picked)


def _copy_wavs(source: Path | None, target: Path) -> int:
    if source is None:
        return 0
    files = sorted(source.rglob("*.wav"))
    target.mkdir(parents=True, exist_ok=True)
    for index, path in enumerate(files):
        shutil.copy2(path, target / f"{index:05d}_{path.name}")
    return len(files)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--features", type=Path, help="Root of the negative feature sets (the recipe's)")
    parser.add_argument("--datasets", type=Path, help="Root of the negative datasets (the recipe's)")
    parser.add_argument("--set", action="append", default=[], metavar="NAME=DIR:SAMPLING:PENALTY",
                        help="A negative set of your own; instead of the recipe's when given")
    parser.add_argument("--clips", type=int, default=5000, help="Training spectrograms of each set")
    parser.add_argument("--validation", type=int, default=500, help="Validation spectrograms of each set")
    parser.add_argument("--rirs", type=Path, help="Room impulse responses (.wav) for the positives")
    parser.add_argument("--background", type=Path, help="Background recordings (.wav) for the positives")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    sets: list[tuple[str, Path, float, float]] = []
    for given in args.set:
        name, _, rest = given.partition("=")
        directory, sampling, penalty = rest.rsplit(":", 2)
        sets.append((name, Path(directory).expanduser(), float(sampling), float(penalty)))
    if not sets:
        roots = {"features": args.features, "datasets": args.datasets}
        for name, root, sampling, penalty in RECIPE:
            if roots[root] is None:
                parser.error(f"--{root} is needed for {name} (or name the sets with --set)")
            sets.append((name, roots[root] / name, sampling, penalty))

    rng = random.Random(args.seed)
    args.out.mkdir(parents=True, exist_ok=True)
    negatives = []
    for name, directory, sampling, penalty in sets:
        training = _mmaps(directory / "training")
        if not training:
            print(f"skipped {name}: no training mmaps under {directory}", file=sys.stderr)
            continue
        target = args.out / "negatives" / name
        kept = materialise(training, target / "training" / f"{name}_mmap", args.clips, rng)
        validation = _mmaps(directory / "validation")
        held = materialise(validation, target / "validation" / f"{name}_mmap", args.validation, rng) if validation else 0
        print(f"{name}: {kept} training, {held} validation")
        negatives.append({"name": name, "sampling_weight": sampling, "penalty_weight": penalty})
    if not negatives:
        parser.error("no negative sets were found")
    kit = {
        "version": 1,
        "positive": POSITIVE,
        "negatives": negatives,
    }
    if _copy_wavs(args.rirs, args.out / "rirs"):
        kit["rirs"] = "rirs"
    if _copy_wavs(args.background, args.out / "background"):
        kit["background"] = "background"
    (args.out / "kit.yaml").write_text(
        "# The wake word training kit (scripts/make_wake_training_kit.py).\n" + yaml.safe_dump(kit, sort_keys=False)
    )
    size = sum(p.stat().st_size for p in args.out.rglob("*") if p.is_file())
    print(f"{args.out}: {len(negatives)} negative sets, {size / 1e9:.2f} GB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
