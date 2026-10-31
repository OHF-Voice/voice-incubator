"""What nothing here understood, for a Home Assistant conversation agent.

When no command, skill or fallback step took a sentence, it can go to one of
Home Assistant's conversation agents -- its own (the default agent), or an
LLM's (OpenAI, Google, Ollama ...) -- as the Voice PE sends everything it
hears. Its answer is said instead of "Sorry, I didn't understand that."

A conversation is kept in each area (``conversation_key``: every satellite
there shares it; one in no area has its own): the agent's
``conversation_id`` is sent back with the next sentence that goes to it, so
"what's the tallest mountain?" ... "how tall is it?" follows on, and Home
Assistant's chat log keeps the whole of it under that id. It is forgotten
after ``keep_seconds`` (five minutes, the Voice PE's
``conversation_timeout``) without an answer from the agent there.

An LLM agent's answer is streamed (the integration's
``voice_labs/conversation/stream``): each piece of it goes to ``on_text`` as
the agent makes it, so it is said a sentence at a time while the rest is
still coming (``Streamed``). Home Assistant's own agent answers at once
through conversation/process, as every agent does when the integration is
from before it streamed.

An answer that is Home Assistant's own "I didn't understand" (the default
agent's ``no_intent_match``) counts as none: the live skill and the Unknown
command come after it as they would without an agent.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from .hass import HassError, HomeAssistant
from .tts import stream_sentences

_LOGGER = logging.getLogger(__name__)

KEEP_SECONDS = 300.0
"""How long a satellite's conversation is kept after the agent's last answer."""

ASK_TIMEOUT = 60.0
"""An LLM agent with tools can take a while; the satellite waits."""

NOT_UNDERSTOOD = "no_intent_match"
"""The error code of an agent that understood nothing (Home Assistant's own)."""

HOME_ASSISTANT_AGENT = "conversation.home_assistant"
"""Home Assistant's own agent: it answers at once, with nothing to stream."""

STREAM = "voice_labs/conversation/stream"


def conversation_key(satellite: Any) -> str:
    """Whose conversation a satellite's sentence is in: its area's, which
    every satellite there shares; else the satellite's own."""
    return satellite.area_id or satellite.key


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
    streamed: bool = False
    """Its answer came a piece at a time (an LLM's), said as it came."""
    first_seconds: float | None = None
    """When its first piece came."""
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
            "streamed": self.streamed,
            "first_seconds": round(self.first_seconds, 3) if self.first_seconds is not None else None,
            "error": self.error,
        }


class Streamed:
    """An answer said as it comes: what the agent has made of it so far,
    made into sentences (``sentences``), until ``end``."""

    def __init__(self) -> None:
        self._chunks: asyncio.Queue[str | None] = asyncio.Queue()
        self.started = asyncio.Event()
        """Set by its first piece."""
        self.task: asyncio.Task | None = None
        """What asks the agent, cancelled by ``cancel`` (the call ended first)."""

    def put(self, text: str) -> None:
        self._chunks.put_nowait(text)
        self.started.set()

    def end(self) -> None:
        self._chunks.put_nowait(None)

    async def _pieces(self) -> AsyncIterator[str]:
        while (chunk := await self._chunks.get()) is not None:
            yield chunk

    def sentences(self) -> AsyncIterator[str]:
        """Its sentences, each as soon as it is whole (read once)."""
        return stream_sentences(self._pieces())

    async def text(self) -> str:
        """All of it, once it has all come."""
        return " ".join([sentence async for sentence in self.sentences()])

    def cancel(self) -> None:
        if self.task is not None and not self.task.done():
            self.task.cancel()


class Agent:
    """A Home Assistant conversation agent, and each satellite's conversation
    with it."""

    def __init__(self, hass: HomeAssistant, agent_id: str | None = None, keep_seconds: float = KEEP_SECONDS) -> None:
        self.hass = hass
        self.agent_id = agent_id
        """None: no agent; nothing is sent."""
        self.keep_seconds = keep_seconds
        self.conversations: dict[str, Conversation] = {}
        """By conversation_key."""
        self._extra_fields = True
        """Whether Home Assistant takes ``device_id`` (2026.10 and later)."""
        self._streams = True
        """Whether the integration streams answers (asked again on reconnecting)."""
        if hasattr(hass, "on_connect"):
            hass.on_connect(self._reconnected)

    async def _reconnected(self) -> None:
        self._streams = True

    def configure(self, agent_id: str | None, keep_seconds: float) -> None:
        if agent_id != self.agent_id:
            self.conversations.clear()  # another agent knows none of them
        self.agent_id = agent_id
        self.keep_seconds = keep_seconds

    def conversation(self, key: str, now: float | None = None) -> Conversation | None:
        """The conversation ``key`` (conversation_key), while it is kept."""
        now = time.monotonic() if now is None else now
        conversation = self.conversations.get(key)
        if conversation is not None and now - conversation.at > self.keep_seconds:
            del self.conversations[key]
            return None
        return conversation

    def status(self) -> dict[str, float]:
        """Seconds each conversation (by conversation_key) is kept for yet."""
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

    async def ask(
        self, key: str, text: str, device_id: str | None = None, language: str | None = None,
        on_text: Callable[[str], None] | None = None,
    ) -> Reply:
        """``text`` said in conversation ``key`` (conversation_key), for the
        agent. An LLM agent's answer goes to ``on_text`` a piece at a time,
        as it is made."""
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
        streaming = on_text is not None and self._streams and self.agent_id != HOME_ASSISTANT_AGENT
        try:
            if streaming:
                assert on_text is not None
                try:
                    result = await self._stream(message, device_id, on_text, reply, started)
                except HassError as err:
                    if "unknown_command" not in str(err):
                        raise
                    self._streams = False
                    _LOGGER.info("The Voice Labs integration doesn't stream answers (update it): asking at once")
                    result = await self._process(message, device_id)
            else:
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

    async def _stream(
        self, message: dict[str, Any], device_id: str | None, on_text: Callable[[str], None], reply: Reply, started: float
    ) -> Any:
        """Ask through the integration, the answer's pieces to ``on_text`` as
        they come -- the assistant's words, and a space between its messages
        (around a tool it called) -- then its result, as conversation/process
        gives it. Given up on (the call ended, too long): its turn ends."""
        done: asyncio.Future = asyncio.get_running_loop().create_future()
        role: str | None = None

        def handler(event: dict[str, Any]) -> None:
            nonlocal role
            kind = event.get("type")
            if kind == "delta":
                delta = event.get("delta") or {}
                if delta.get("role"):
                    role = delta["role"]
                    if role == "assistant" and reply.streamed:
                        on_text(" ")
                content = delta.get("content")
                if role == "assistant" and content:
                    if not reply.streamed:
                        reply.streamed = True
                        reply.first_seconds = time.perf_counter() - started
                    on_text(content)
            elif not done.done():
                if kind == "result":
                    done.set_result(event.get("result"))
                elif kind == "error":
                    done.set_exception(HassError(f"{event.get('code')}: {event.get('message')}"))

        request = {**message, "type": STREAM, **({"device_id": device_id} if device_id else {})}
        msg_id, _ = await self.hass.subscribe_result(request, handler)
        try:
            return await asyncio.wait_for(asyncio.shield(done), ASK_TIMEOUT)
        finally:
            await self.hass.unsubscribe(msg_id, tell=not done.done())

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
