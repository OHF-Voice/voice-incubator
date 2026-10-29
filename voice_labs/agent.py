"""What nothing here understood, for a Home Assistant conversation agent.

When no command, skill or fallback step took a sentence, it can go to one of
Home Assistant's conversation agents -- its own (the default agent), or an
LLM's (OpenAI, Google, Ollama ...) -- as the Voice PE sends everything it
hears. Its answer is said instead of "Sorry, I didn't understand that."

A conversation is kept on each satellite: the agent's ``conversation_id`` is
sent back with the next sentence that goes to it, so "what's the tallest
mountain?" ... "how tall is it?" follows on. It is forgotten after
``keep_seconds`` (five minutes, the Voice PE's ``conversation_timeout``)
without an answer from the agent on that satellite.

An answer that is Home Assistant's own "I didn't understand" (the default
agent's ``no_intent_match``) counts as none: the live skill and the Unknown
command come after it as they would without an agent.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from .hass import HassError, HomeAssistant

_LOGGER = logging.getLogger(__name__)

KEEP_SECONDS = 300.0
"""How long a satellite's conversation is kept after the agent's last answer."""

ASK_TIMEOUT = 60.0
"""An LLM agent with tools can take a while; the satellite waits."""

NOT_UNDERSTOOD = "no_intent_match"
"""The error code of an agent that understood nothing (Home Assistant's own)."""


@dataclass
class Conversation:
    id: str
    at: float
    """When the agent last answered in it (time.monotonic)."""


@dataclass
class Reply:
    """What the agent made of a sentence."""

    agent: str
    text: str = ""
    """What it said; empty when it said nothing (or could not be asked)."""
    understood: bool = False
    response_type: str | None = None
    """``action_done``, ``query_answer`` or ``error``."""
    code: str | None = None
    """An error's code (``no_intent_match`` ...)."""
    conversation_id: str | None = None
    continued: bool = False
    """The conversation it was asked in was kept from before."""
    wants_answer: bool = False
    """The agent asked something back (``continue_conversation``)."""
    seconds: float = 0.0
    error: str | None = None
    """Home Assistant could not be asked, or refused."""
    data: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "text": self.text,
            "understood": self.understood,
            "response_type": self.response_type,
            "code": self.code,
            "conversation_id": self.conversation_id,
            "continued": self.continued,
            "wants_answer": self.wants_answer,
            "seconds": round(self.seconds, 3),
            "error": self.error,
        }


class Agent:
    """A Home Assistant conversation agent, and each satellite's conversation
    with it."""

    def __init__(self, hass: HomeAssistant, agent_id: str | None = None, keep_seconds: float = KEEP_SECONDS) -> None:
        self.hass = hass
        self.agent_id = agent_id
        """None: no agent; nothing is sent."""
        self.keep_seconds = keep_seconds
        self.conversations: dict[str, Conversation] = {}
        self._extra_fields = True
        """Whether Home Assistant takes ``device_id`` (2026.10 and later)."""

    def configure(self, agent_id: str | None, keep_seconds: float) -> None:
        if agent_id != self.agent_id:
            self.conversations.clear()  # another agent knows none of them
        self.agent_id = agent_id
        self.keep_seconds = keep_seconds

    def conversation(self, key: str, now: float | None = None) -> Conversation | None:
        """The satellite's conversation, while it is kept."""
        now = time.monotonic() if now is None else now
        conversation = self.conversations.get(key)
        if conversation is not None and now - conversation.at > self.keep_seconds:
            del self.conversations[key]
            return None
        return conversation

    def status(self) -> dict[str, float]:
        """Seconds each satellite's conversation is kept for yet."""
        now = time.monotonic()
        return {
            key: round(self.keep_seconds - (now - c.at), 1)
            for key in list(self.conversations)
            if (c := self.conversation(key, now)) is not None
        }

    async def agents(self) -> list[dict[str, Any]]:
        """Home Assistant's conversation agents: ``[{id, name}]``."""
        result = await self.hass.call({"type": "conversation/agent/list"})
        return [{"id": a["id"], "name": a.get("name") or a["id"]} for a in (result or {}).get("agents") or []]

    async def ask(self, key: str, text: str, device_id: str | None = None, language: str | None = None) -> Reply:
        """``text`` said on satellite ``key``, for the agent, in its
        conversation there."""
        assert self.agent_id is not None
        reply = Reply(self.agent_id)
        kept = self.conversation(key)
        message: dict[str, Any] = {"type": "conversation/process", "text": text, "agent_id": self.agent_id}
        if kept is not None:
            message["conversation_id"] = kept.id
            reply.continued = True
        if language:
            message["language"] = language
        started = time.perf_counter()
        try:
            result = await self._process(message, device_id)
        except (HassError, ConnectionError, OSError, TimeoutError) as err:
            reply.error = str(err) or type(err).__name__
            reply.seconds = time.perf_counter() - started
            _LOGGER.warning("Conversation agent %s could not be asked: %s", self.agent_id, reply.error)
            return reply
        reply.seconds = time.perf_counter() - started
        response = (result or {}).get("response") or {}
        reply.response_type = response.get("response_type")
        reply.code = (response.get("data") or {}).get("code")
        speech = (response.get("speech") or {}).get("plain") or {}
        reply.text = str(speech.get("speech") or "").strip()
        reply.understood = not (reply.response_type == "error" and reply.code == NOT_UNDERSTOOD)
        reply.wants_answer = bool((result or {}).get("continue_conversation"))
        reply.conversation_id = (result or {}).get("conversation_id") or None
        if reply.conversation_id:
            self.conversations[key] = Conversation(reply.conversation_id, time.monotonic())
        return reply

    async def _process(self, message: dict[str, Any], device_id: str | None) -> Any:
        """Send it with the satellite's device, which Home Assistant before
        2026.10 refuses: then without."""
        if device_id and self._extra_fields:
            try:
                return await self.hass.call({**message, "device_id": device_id}, timeout=ASK_TIMEOUT)
            except HassError as err:
                if "extra keys" not in str(err) and "invalid_format" not in str(err):
                    raise
                self._extra_fields = False
        return await self.hass.call(message, timeout=ASK_TIMEOUT)
