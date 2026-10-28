"""Who may reach the web UI when it runs as an app: Home Assistant's ingress
proxy, and this machine only for the container's health check."""

from __future__ import annotations

import asyncio
import socket
from types import SimpleNamespace

import aiohttp

from voice_call.web import HEALTH, start_web


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_only_the_health_check_from_this_machine():
    async def scenario():
        port = _free_port()
        options = SimpleNamespace(http_allow=("172.30.32.2",), http_host="127.0.0.1", http_port=port)
        runner = await start_web(SimpleNamespace(options=options))
        try:
            async with aiohttp.ClientSession() as http:
                async with http.get(f"http://127.0.0.1:{port}{HEALTH}") as response:
                    assert (response.status, await response.text()) == (200, "ok")
                for path in ("/api/status", "/", "/api/store"):
                    async with http.get(f"http://127.0.0.1:{port}{path}") as response:
                        assert response.status == 403, path
        finally:
            await runner.cleanup()

    asyncio.run(scenario())
