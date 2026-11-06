"""Only what the wake verifier runs of Kaldi's tools (wyoming_wake_kaldi/
prune.py): an image keeps its programs and the libraries they load, and none
of the rest. Needs the tools (models/wake-kaldi-tools); skipped without them."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from wyoming_wake_kaldi.kaldi import PROGRAMS, SpeechTools
from wyoming_wake_kaldi.prune import prune

TOOLS = Path(__file__).resolve().parent.parent / "models/wake-kaldi-tools"


@pytest.mark.skipif(not TOOLS.is_dir(), reason="needs the Kaldi tools")
def test_only_what_the_verifier_runs_is_kept(tmp_path):
    tools = tmp_path / "tools"
    shutil.copytree(TOOLS, tools, symlinks=True)
    programs, libraries = prune(tools, [])
    kept = sorted(p.name for d in ("kaldi/bin", "openfst/bin") for p in (tools / d).iterdir())
    assert programs == len(PROGRAMS) and kept == sorted(PROGRAMS)
    assert 0 < libraries < len(list((TOOLS / "kaldi/lib").iterdir())) + len(list((TOOLS / "openfst/lib").iterdir()))
    assert not (tools / "kaldi/include").exists() and (tools / "kaldi/utils/mkgraph.sh").is_file()
    SpeechTools.from_dir(tools).check()
    # Each loads with what is left -- the verifier's library path, nothing else.
    env = {**SpeechTools.from_dir(tools).extended_env, "LD_LIBRARY_PATH": f"{tools}/kaldi/lib:{tools}/openfst/lib"}
    for name in PROGRAMS:
        program = next(p for p in (tools / "kaldi/bin" / name, tools / "openfst/bin" / name) if p.exists())
        out = subprocess.run(["ldd", str(program)], capture_output=True, text=True, env=env, check=False).stdout
        assert "not found" not in out, (name, out)


def test_libraries_next_to_the_programs(tmp_path, monkeypatch):
    """The aarch64 tree: its libraries in kaldi/bin, which kaldi/lib and
    openfst/ link to."""
    tools = tmp_path / "tools"
    bin_dir = tools / "kaldi/bin"
    bin_dir.mkdir(parents=True)
    for name in (*PROGRAMS, "acc-lda", "libkaldi-base.so", "libfst.so.10.0.0", "libkaldi-unused.so"):
        (bin_dir / name).write_text(name)
    (bin_dir / "libfst.so.10").symlink_to("libfst.so.10.0.0")
    (bin_dir / "libfst.so").symlink_to("libfst.so.10.0.0")
    (tools / "kaldi/include/openfst").mkdir(parents=True)
    (tools / "kaldi/utils").mkdir()
    (tools / "kaldi/lib").symlink_to("bin")
    (tools / "openfst").mkdir()
    for name, target in (("bin", "../kaldi/bin"), ("lib", "../kaldi/bin"), ("include", "../kaldi/include/openfst")):
        (tools / "openfst" / name).symlink_to(target)
    loads = {"libkaldi-base.so", "libfst.so.10", "libfst.so.10.0.0"}
    monkeypatch.setattr(
        "wyoming_wake_kaldi.prune.linked_libraries",
        lambda program, root, env: {str(root / "kaldi/bin" / name) for name in loads},
    )
    programs, libraries = prune(tools, [])
    assert programs == len(PROGRAMS) and libraries == len(loads)
    assert sorted(p.name for p in bin_dir.iterdir()) == sorted({*PROGRAMS, *loads})
    assert not (tools / "kaldi/include").exists() and not (tools / "openfst/include").is_symlink()
    assert (tools / "openfst/bin/fstcompile").is_file() and (tools / "kaldi/lib/libkaldi-base.so").is_file()
