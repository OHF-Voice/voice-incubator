"""The Sessions list (voice_incubator/calls.py)."""

from __future__ import annotations

import json

from voice_incubator.calls import CallLog


def test_clearing_sessions(tmp_path):
    log = CallLog(tmp_path, {"keep_audio": True, "max_saved_calls": 100})
    kitchen, office = {"id": "kitchen-vpe"}, {"id": "office-vpe"}
    for satellite in (kitchen, office, kitchen):
        log.finish(log.begin("call", satellite), "done", "turned on the lights", {"call": b"\0\0" * 160})
    going = log.begin("call", kitchen)  # still being said
    # Saved long ago: no longer among the recent, but on disk.
    old = tmp_path / "20200101-000000-0001-office"
    old.mkdir()
    (old / "trace.json").write_text(json.dumps({"satellite": office}))

    # A satellite's: only its own, saved ones too.
    assert log.clear("office-vpe") == 2
    assert not old.exists()
    assert [c["satellite"]["id"] for c in log.list()] == ["kitchen-vpe"] * 3

    # Everyone's, but not the one still going: it finishes, and is saved.
    assert log.clear() == 2
    assert [c["id"] for c in log.list()] == [going.id]
    assert [p.name for p in tmp_path.iterdir()] == []
    log.finish(going, "done", "", {"call": b"\0\0" * 160})
    assert (tmp_path / going.id / "trace.json").is_file()
