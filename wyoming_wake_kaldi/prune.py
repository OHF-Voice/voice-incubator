"""Keep only what the verifier uses of a Kaldi tool tree (kaldi/ and
openfst/): its programs (kaldi.PROGRAMS), the libraries they and the resident
decoder link, and the scripts -- not the rest of Kaldi's 500 programs, nor
the headers the decoder is compiled with. For an image, once the decoder is
built:

    python3 -m wyoming_wake_kaldi.prune TOOLS_DIR [LINKED ...]

``LINKED``: other programs whose libraries are kept (the decoder daemon).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .kaldi import PROGRAMS, SpeechTools

_LINKED = re.compile(r"=>\s+(\S+)")


def linked_libraries(program: Path, root: Path, env: dict[str, str]) -> set[str]:
    """The libraries under ``root`` that ``program`` loads -- as named, and
    what each names -- by ldd, with the verifier's environment (its library
    path is the tree's: SpeechTools.extended_env). Raises SystemExit for one
    not found."""
    out = subprocess.run(["ldd", str(program)], capture_output=True, text=True, check=True, env=env).stdout
    missing = [line.split()[0] for line in out.splitlines() if "not found" in line]
    if missing:
        raise SystemExit(f"{program}: no {', '.join(missing)}")
    found: set[str] = set()
    for match in _LINKED.finditer(out):
        path = os.path.abspath(match.group(1))
        real = os.path.realpath(path)
        if real.startswith(str(root) + os.sep):
            found |= {path, real}
    return found


def prune(tools_dir: Path, linked: list[Path]) -> tuple[int, int]:
    """Prune ``tools_dir`` in place. How many programs and libraries remain."""
    root = Path(os.path.realpath(tools_dir))
    programs = []
    for bin_dir in (root / "kaldi" / "bin", root / "openfst" / "bin"):
        for path in sorted(bin_dir.iterdir()):
            if path.name in PROGRAMS:
                programs.append(path)
            else:
                path.unlink()
    missing = set(PROGRAMS) - {p.name for p in programs}
    if missing:
        raise SystemExit(f"Not in {tools_dir}: {', '.join(sorted(missing))}")
    env = SpeechTools.from_dir(root).extended_env
    keep: set[str] = set()
    for program in [*programs, *linked]:
        keep |= linked_libraries(program, root, env)
    libraries = 0
    for lib_dir in (root / "kaldi" / "lib", root / "openfst" / "lib"):
        for path in sorted(lib_dir.iterdir()):
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            elif str(path) in keep:
                libraries += 1
            else:
                path.unlink()
    for headers in (root / "kaldi" / "include", root / "openfst" / "include"):
        shutil.rmtree(headers, ignore_errors=True)
    return len(programs), libraries


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    programs, libraries = prune(Path(sys.argv[1]), [Path(p) for p in sys.argv[2:]])
    print(f"Kept {programs} programs and {libraries} libraries in {sys.argv[1]}")


if __name__ == "__main__":
    main()
