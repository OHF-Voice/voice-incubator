"""Home Assistant access: sentence triggers in, conversation responses out."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import websockets

EVENT_AUTOMATION_RELOADED = "automation_reloaded"
EVENT_ENTITY_REGISTRY_UPDATED = "entity_registry_updated"
WATCHED_EVENTS = (EVENT_AUTOMATION_RELOADED, EVENT_ENTITY_REGISTRY_UPDATED)

# A {slot} reference. Ranges like {1..10} are list references too, and Home
# Assistant makes every one of them a wildcard inside a conversation trigger.
WILDCARD_PATTERN = re.compile(r"\{[^}]+\}")

_LOGGER = logging.getLogger(__name__)


@dataclass
class Sentences:
    """Sentence-trigger commands, split by whether they capture free text."""

    plain: list[str] = field(default_factory=list)
    """No ``{slot}`` reference: these become the speech-to-phrase grammar."""

    wildcard: list[str] = field(default_factory=list)
    """Contain a ``{slot}``. Home Assistant treats every list reference in a
    conversation trigger as a wildcard, so these match arbitrary speech and
    cannot be enumerated into a finite grammar. They are matched with hassil
    against the unconstrained transcript instead."""

    source: dict[str, str] = field(default_factory=dict)
    """normalized template -> automation alias, for logging only"""

    @property
    def templates(self) -> list[str]:
        return self.plain + self.wildcard


class HomeAssistant:
    def __init__(self, url: str, token: str) -> None:
        self.url = url.rstrip("/")
        self.token = token

    @property
    def websocket_url(self) -> str:
        scheme = "wss" if self.url.startswith("https://") else "ws"
        host = self.url.split("://", 1)[-1]
        return f"{scheme}://{host}/api/websocket"

    def _request(self, path: str, payload: dict | None = None) -> object:
        request = urllib.request.Request(
            f"{self.url}{path}",
            data=None if payload is None else json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            method="GET" if payload is None else "POST",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)

    async def _call(self, path: str, payload: dict | None = None) -> object:
        return await asyncio.to_thread(self._request, path, payload)

    async def sentence_triggers(self) -> Sentences:
        """Every ``conversation`` trigger command across all automations.

        This is the app's entire vocabulary: speech-to-phrase is trained on
        exactly these, so anything else is rejected rather than misheard.
        """
        states = await self._call("/api/states")
        assert isinstance(states, list)

        automation_ids = [
            state["attributes"]["id"]
            for state in states
            if state["entity_id"].startswith("automation.")
            and state["attributes"].get("id")
        ]

        sentences = Sentences()
        for automation_id in automation_ids:
            try:
                config = await self._call(
                    f"/api/config/automation/config/{automation_id}"
                )
            except (urllib.error.HTTPError, urllib.error.URLError) as err:
                _LOGGER.warning("Could not read automation %s: %s", automation_id, err)
                continue
            assert isinstance(config, dict)

            triggers = config.get("triggers") or config.get("trigger") or []
            if isinstance(triggers, dict):
                triggers = [triggers]

            alias = config.get("alias", automation_id)
            for trigger in triggers:
                if trigger.get("trigger") != "conversation" and (
                    trigger.get("platform") != "conversation"
                ):
                    continue
                commands = trigger.get("command") or []
                if isinstance(commands, str):
                    commands = [commands]
                for command in commands:
                    command = command.strip()
                    if not command:
                        continue
                    if WILDCARD_PATTERN.search(command):
                        sentences.wildcard.append(command)
                    else:
                        sentences.plain.append(command)
                    sentences.source.setdefault(command.lower(), alias)

        _LOGGER.info(
            "Loaded %d sentence trigger(s) from %d automation(s): %d plain, "
            "%d with wildcards",
            len(sentences.templates),
            len(automation_ids),
            len(sentences.plain),
            len(sentences.wildcard),
        )
        return sentences

    async def converse(self, text: str, language: str = "en") -> str:
        """Run ``text`` through HA, firing any matching sentence trigger.

        Returns the automation's conversation response, empty if it set none.
        """
        result = await self._call(
            "/api/services/conversation/process?return_response=true",
            {"text": text, "language": language},
        )
        assert isinstance(result, dict)
        response = result.get("service_response", {}).get("response", {})
        speech = response.get("speech", {}).get("plain", {}).get("speech", "")
        _LOGGER.debug(
            "Conversation %r -> %s / %r",
            text,
            response.get("response_type"),
            speech,
        )
        return speech or ""


    async def render_template(self, template_str: str) -> str:
        """Render a Jinja template through Home Assistant.

        ``render_template`` is a subscription, so this takes the first result
        and unsubscribes. Note that Home Assistant already renders
        ``set_conversation_response``, so this is only a safety net for a
        response that reaches us with template syntax still in it.
        """
        async with websockets.connect(self.websocket_url) as socket:
            await self._authenticate(socket)
            await socket.send(
                json.dumps(
                    {"id": 1, "type": "render_template", "template": template_str}
                )
            )
            rendered = template_str
            while True:
                message = json.loads(await socket.recv())
                if message.get("type") == "result" and not message.get("success"):
                    raise RuntimeError(f"Could not render template: {message}")
                if message.get("type") == "event":
                    rendered = str(message["event"]["result"])
                    break
            await socket.send(json.dumps({"id": 2, "type": "unsubscribe_events",
                                          "subscription": 1}))
            return rendered

    async def _authenticate(self, socket) -> None:
        greeting = json.loads(await socket.recv())
        if greeting.get("type") != "auth_required":
            raise RuntimeError(f"Unexpected greeting: {greeting}")
        await socket.send(json.dumps({"type": "auth", "access_token": self.token}))
        auth = json.loads(await socket.recv())
        if auth.get("type") != "auth_ok":
            raise RuntimeError(f"Authentication failed: {auth}")

    async def watch_automations(
        self,
        on_change: Callable[[], Awaitable[None]],
        reconnect_seconds: float = 5.0,
        debounce_seconds: float = 1.0,
    ) -> None:
        """Call ``on_change`` whenever Home Assistant reloads automations.

        Every edit through the UI or config API ends in an ``automation.reload``,
        so this one event covers adding, editing and deleting a sentence
        trigger. Reconnects for as long as the task is alive.
        """
        while True:
            try:
                await self._watch_once(on_change, debounce_seconds)
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - the watcher must never die
                _LOGGER.warning("Automation watcher disconnected: %s", err)
            await asyncio.sleep(reconnect_seconds)

    async def _watch_once(
        self,
        on_change: Callable[[], Awaitable[None]],
        debounce_seconds: float,
    ) -> None:
        async with websockets.connect(self.websocket_url) as socket:
            await self._authenticate(socket)

            # Editing or creating an automation ends in automation.reload, but
            # deleting one does not -- it only drops the entity from the
            # registry -- so both events are needed to see every change.
            for message_id, event_type in enumerate(WATCHED_EVENTS, start=1):
                await socket.send(
                    json.dumps(
                        {
                            "id": message_id,
                            "type": "subscribe_events",
                            "event_type": event_type,
                        }
                    )
                )
                result = json.loads(await socket.recv())
                if not result.get("success"):
                    raise RuntimeError(f"Could not subscribe to {event_type}: {result}")

            _LOGGER.info("Watching Home Assistant for automation changes")
            async for raw in socket:
                message = json.loads(raw)
                if message.get("type") != "event":
                    continue
                if not _is_automation_change(message["event"]):
                    continue
                # A single edit can produce several events; take the last one.
                await asyncio.sleep(debounce_seconds)
                await _drain(socket)
                await on_change()


def _is_automation_change(event: dict) -> bool:
    if event.get("event_type") == EVENT_AUTOMATION_RELOADED:
        return True
    data = event.get("data", {})
    return str(data.get("entity_id", "")).startswith("automation.") and data.get(
        "action"
    ) in ("create", "remove")


async def _drain(socket: "websockets.ClientConnection") -> None:
    """Discard events that piled up during the debounce."""
    while True:
        try:
            await asyncio.wait_for(socket.recv(), timeout=0.01)
        except (TimeoutError, asyncio.TimeoutError):
            return
