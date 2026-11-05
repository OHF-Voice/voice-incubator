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
