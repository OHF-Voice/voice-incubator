"""Home Assistant's questions (the integration's ask_question): every way a
question ends gives Home Assistant one result, and only one. Without the
models: the app's request handling on its own (the whole of it on recorded
calls is in test_app.py's follow-up turns)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from voice_incubator.follow_up import FollowUp
from voice_incubator.server import VoiceIncubatorApp


class Results:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str | None, str | None]] = []

    async def request_result(self, request_id, text=None, error=None):
        self.sent.append((request_id, text, error))


def _app(sessions=None) -> VoiceIncubatorApp:
    app = VoiceIncubatorApp.__new__(VoiceIncubatorApp)
    app._asks = {}
    app._tasks = set()
    app.integration = Results()
    app.sessions = sessions or {}
    return app


def _asked(request_id: str = "r1") -> FollowUp:
    return FollowUp("ask", "Close it?", None, None, request_id=request_id)


def test_resolved_once():
    async def run():
        app = _app()
        app._asks["r1"] = "sat"
        asked = _asked()
        app._resolve_ask(asked, text="yes")
        app._resolve_ask(asked, error="failed")  # the call's end, after the answer
        await asyncio.sleep(0)
        assert app.integration.sent == [("r1", "yes", None)] and not app._asks

    asyncio.run(run())


def test_only_home_assistants_questions():
    async def run():
        app = _app()
        app._resolve_ask(FollowUp("agent", "Anything else?", None, None))
        app._resolve_ask(None)
        app._resolve_ask(_asked("unknown"))
        await asyncio.sleep(0)
        assert app.integration.sent == []

    asyncio.run(run())


def test_cancel_stops_listening():
    async def run():
        asked = _asked()
        session = SimpleNamespace(follow_up=asked)
        app = _app({"sat": session})
        app._asks["r1"] = "sat"
        app._on_request({"request_id": "r1", "kind": "cancel"})
        assert session.follow_up is None and not app._asks
        app._resolve_ask(asked, text="yes")  # too late: Home Assistant has stopped waiting
        await asyncio.sleep(0)
        assert app.integration.sent == []

    asyncio.run(run())


def test_satellite_not_connected_or_unable():
    async def run():
        unable = SimpleNamespace(writer=object(), device=SimpleNamespace(listen=False))
        app = _app({"old": unable})
        await app.ask("missing", "r1", "Close it?")
        await app.ask("old", "r2", "Close it?")
        app._on_request({"request_id": "r3", "kind": "dance"})
        await asyncio.sleep(0)
        assert [(r, e) for r, _t, e in app.integration.sent] == [
            ("r1", "satellite not connected"),
            ("r2", "satellite firmware cannot listen for an answer"),
            ("r3", "unknown request: dance"),
        ]
        assert not app._asks

    asyncio.run(run())


def test_a_second_question_waits_for_the_first_answer():
    session = SimpleNamespace(follow_up=_asked())
    assert VoiceIncubatorApp._awaiting(session)
    session.follow_up.told -= 60  # never started listening: no longer waited for
    assert not VoiceIncubatorApp._awaiting(session)
    session.follow_up = None
    assert not VoiceIncubatorApp._awaiting(session)
