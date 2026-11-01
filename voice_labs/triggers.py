"""Home Assistant's sentence triggers, heard here and run there.

An automation that starts on a sentence -- its conversation trigger,
``trigger: conversation`` with ``command:`` -- is a voice command written in
Home Assistant. The app listens for it as for its own commands and sends
what was said to Home Assistant's own agent (``conversation.home_assistant``),
which runs the automation and answers with its response
(``set_conversation_response``). It is sent with the satellite's device, so
the automation's ``trigger.device_id`` is the satellite (Home Assistant
2026.10 and later). Home Assistant answers "Done" for an automation that
sets no response: that is the acknowledgement sound here (``done_said``).

**Reading them** (``Reader``). ``conversation/sentences/list`` has the
sentences of every trigger Home Assistant listens for -- none of an
automation turned off. Each automation's own (``automation/config``; one
from a blueprint as the blueprint makes it, ``blueprint/substitute``) groups
them, one command to an automation, called by its name. One an automation
has, listed again, is left out: Home Assistant's automation editor listens
for a copy of each trigger it shows, while it is open (to show it firing),
and so do two automations that share a sentence -- Home Assistant runs both
on it. A sentence no automation has (a template entity's) is one of its
own. They are read again when automations are reloaded (saved in the
editor) and when one is turned on or off.

**Hearing them** (``prepare``). A sentence is a hassil template, read as Home
Assistant reads it. One that stands for at most ``MAX_SENTENCES`` sentences,
with no free text in it, is listened for as example sentences in the CTC
evidence, as the app's own are. One with free text (a wildcard: ``play
{album} by {artist}``), or too many, or one adapt-ctc can't spell, is matched
on the transcript with hassil (``match``), as Home Assistant matches it --
the CTC head's transcript before anything else is, the TDT head's when
nothing matched.

**Triggers win.** Home Assistant runs a trigger before its intents, so a
sentence that is both a trigger and one of the app's commands runs the
trigger: its example sentences are the trigger's (commands.shared_examples),
and the command is its twin there, run where the trigger is not. Where the
same sentence is two triggers', Home Assistant runs both.

Each is heard on every satellite unless turned off -- everywhere
(``disabled``) or in some areas (``off``): ``commands.json``'s ``triggers``,
by its key (``settings_of``).
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
import re
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any

from hassil import Intents, recognize_all
from hassil.expression import Alternative, Expression, Group, ListReference, Permutation, RuleReference, TextChunk
from hassil.intents import WildcardSlotList
from hassil.parse_expression import parse_sentence
from hassil.sample import sample_expression

from adapt_ctc.config import ConfigError, parse_config
from adapt_ctc.template import MAX_SENTENCES
from adapt_ctc.text import sentence_key

from . import languages
from .commands import COMMAND_ID, Behavior, Command, area_ids, base_config
from .hass import HassError, HomeAssistant

_LOGGER = logging.getLogger(__name__)

ICON = "mdi:home-assistant"
ID_PREFIX = "trigger_"
"""Its command's id: the prefix, then its key's words."""
SENTENCE = "sentence"
"""The slot with what is sent to Home Assistant: the example heard, or the
transcript a trigger with free text matched."""
DEBOUNCE = 1.0
"""Automations come in bursts (a reload turns each off and on): read once they settle."""


@dataclass(frozen=True)
class Trigger:
    """One automation's sentences (or one sentence of no automation's)."""

    key: str
    """Where its settings are kept: ``automation:<id>`` (else its entity
    id), or ``sentence:<the sentence>``."""
    title: str
    sentences: tuple[str, ...]
    """As Home Assistant has them: hassil templates."""
    automation: str | None = None
    """Its automation entity."""
    automation_id: str | None = None
    """Its automation's id: its page in Home Assistant's editor."""
    examples: tuple[str, ...] = ()
    """The sentences it stands for, listened for in the CTC evidence."""
    transcript: tuple[str, ...] = ()
    """The templates matched on the transcript instead (free text, too many
    sentences, or ones adapt-ctc can't spell)."""
    description: str = ""
    """What it is, for hints: saying its first sentence."""
    problems: tuple[str, ...] = ()
    """Sentences that couldn't be read, and why: never heard."""

    @property
    def command_id(self) -> str:
        words = re.sub(r"[^a-z0-9]+", "_", self.key.split(":", 1)[-1].lower()).strip("_")
        return ID_PREFIX + (words[:48].rstrip("_") or "sentence")


# ------------------------------------------------------------------ reading


async def read(hass: HomeAssistant) -> tuple[list[Trigger], list[str]]:
    """The triggers Home Assistant listens for, grouped by automation, and
    every automation entity (each may turn on and change them)."""
    listed = (await hass.call({"type": "conversation/sentences/list"}) or {}).get("trigger_sentences") or []
    states = await hass.call({"type": "get_states"}) or []
    automations = [s for s in states if str(s.get("entity_id", "")).startswith("automation.")]
    if not listed:
        return [], [str(s["entity_id"]) for s in automations]
    configs = await asyncio.gather(*(_config(hass, str(s["entity_id"])) for s in automations))
    left = Counter(str(s) for s in listed)
    found: list[Trigger] = []
    for state, config in zip(automations, configs):
        if state.get("state") != "on":
            continue
        theirs = []
        for sentence in sentences_in(config):
            if left[sentence] > 0:
                left[sentence] -= 1
                theirs.append(sentence)
        if not theirs:
            continue
        entity_id = str(state["entity_id"])
        attributes = state.get("attributes") or {}
        automation_id = str(attributes["id"]) if attributes.get("id") else None
        found.append(Trigger(
            f"automation:{automation_id or entity_id}",
            str(attributes.get("friendly_name") or entity_id),
            tuple(dict.fromkeys(theirs)),
            entity_id,
            automation_id,
        ))
    # An automation's, again (the editor's copy, or two sharing it), or
    # one's that is off (its copy, the editor open on it): not one of its own.
    had = {sentence for config in configs for sentence in sentences_in(config)}
    for sentence in dict.fromkeys(left.elements()):
        if sentence not in had:
            found.append(Trigger(f"sentence:{sentence}", sentence, (sentence,)))
    return found, [str(s["entity_id"]) for s in automations]


async def _config(hass: HomeAssistant, entity_id: str) -> dict[str, Any]:
    """An automation's config; one from a blueprint, as the blueprint makes it."""
    try:
        config = (await hass.call({"type": "automation/config", "entity_id": entity_id}) or {}).get("config") or {}
    except HassError:
        return {}  # gone since, or not ours to read: its sentences stand alone
    blueprint = config.get("use_blueprint")
    if not isinstance(blueprint, dict) or not blueprint.get("path"):
        return config
    try:
        result = await hass.call({
            "type": "blueprint/substitute", "domain": "automation", "path": blueprint["path"], "input": blueprint.get("input") or {},
        })
    except HassError:
        return config  # a blueprint gone, or an input missing: its sentences stand alone
    return (result or {}).get("substituted_config") or config


def sentences_in(config: dict[str, Any]) -> list[str]:
    """An automation's conversation triggers' sentences, as written."""
    items = config.get("triggers", config.get("trigger")) or []
    out: list[str] = []
    for item in items if isinstance(items, list) else [items]:
        if not isinstance(item, dict) or item.get("enabled") is False:
            continue
        if (item.get("trigger") or item.get("platform")) != "conversation":
            continue
        command = item.get("command")
        out += [command] if isinstance(command, str) else [str(c) for c in command or []]
    return out


# ---------------------------------------------------------------- preparing


def prepare(triggers: Iterable[Trigger], language: str | None = None) -> list[Trigger]:
    """Each trigger with what it is heard by: its examples (the sentences its
    templates stand for, each once), the templates matched on the
    transcript, and its description."""
    language = language or languages.current().code
    out = []
    for trigger in triggers:
        taken: set[str] = set()
        examples: list[str] = []
        transcript: list[str] = []
        problems: list[str] = []
        readable: list[str] = []
        for template in trigger.sentences:
            try:
                parsed = parse_sentence(template)
            except Exception as err:  # noqa: BLE001 - hassil raises a variety
                problems.append(f"“{template}”: {err}")
                continue
            readable.append(template)
            sentences = _sentences(parsed.expression)
            if sentences is None or not _speakable(sentences, language):
                transcript.append(template)
                continue
            for sentence in sentences:
                key = sentence_key(sentence, language)
                if key and key not in taken:
                    taken.add(key)
                    examples.append(sentence)
        description = languages.speech().saying(_first_form(readable[0])) if readable else trigger.title
        out.append(dataclasses.replace(
            trigger, examples=tuple(examples), transcript=tuple(transcript), description=description, problems=tuple(problems)
        ))
    return out


def _sentences(expression: Expression) -> list[str] | None:
    """Every sentence a template stands for; None with free text or a rule
    in it, or more than MAX_SENTENCES."""
    if _has_reference(expression):
        return None
    out: list[str] = []
    for text in sample_expression(expression):
        sentence = " ".join(text.split())
        if sentence and sentence not in out:
            out.append(sentence)
            if len(out) > MAX_SENTENCES:
                return None
    return out


def _has_reference(expression: Expression) -> bool:
    if isinstance(expression, (ListReference, RuleReference)):
        return True
    return isinstance(expression, Group) and any(_has_reference(item) for item in expression.items)


def _speakable(sentences: list[str], language: str) -> bool:
    """Whether adapt-ctc takes them as examples (it spells every word)."""
    config = base_config()
    config["intents"] = {"trigger": {"description": "trigger", "examples": sentences}}
    try:
        parse_config(config)
    except ConfigError:
        return False
    return True


def _first_form(template: str) -> str:
    """A template said its shortest first way: the first alternative, no
    optional words, free text as "…"."""
    try:
        expression = parse_sentence(template).expression
    except Exception:  # noqa: BLE001
        return template
    return " ".join(_render(expression).split())


def _render(expression: Expression) -> str:
    if isinstance(expression, TextChunk):
        return expression.text
    if isinstance(expression, (ListReference, RuleReference)):
        return " … "
    if isinstance(expression, Alternative):
        return "" if expression.is_optional else _render(expression.items[0]) if expression.items else ""
    if isinstance(expression, Permutation):
        return " ".join(_render(item) for item in expression.items)
    if isinstance(expression, Group):
        return "".join(_render(item) for item in expression.items)
    return ""


# ---------------------------------------------------------------- compiling


@dataclass
class Compiled:
    """The triggers as commands."""

    commands: dict[str, Command]
    """By command id; examples-only, or nothing to listen for (matched on the transcript)."""
    triggers: dict[str, Trigger]
    """By command id."""
    heard: Intents | None = None
    """The templates matched on the transcript, an intent to a command (by id)."""


def settings_of(saved: Any) -> dict[str, dict[str, Any]]:
    """``commands.json``'s ``triggers``: by key, ``disabled`` and ``off``.
    Raises ConfigError."""
    if saved is None:
        return {}
    if not isinstance(saved, dict):
        raise ConfigError("triggers: a mapping of trigger keys to their settings")
    out = {}
    for key, value in saved.items():
        where = f"triggers.{key}"
        if not isinstance(value, dict):
            raise ConfigError(f"{where}: expected a mapping (disabled, off)")
        unknown = set(value) - {"disabled", "off"}
        if unknown:
            raise ConfigError(f"{where}: unknown key(s) {sorted(unknown)}")
        out[str(key)] = {"disabled": bool(value.get("disabled", False)), "off": area_ids(value.get("off"), f"{where}.off")}
    return out


def compile_triggers(triggers: Iterable[Trigger], settings: dict[str, dict[str, Any]], taken: Iterable[str]) -> Compiled:
    """Commands for the triggers, with their settings; ids clear of ``taken``
    (the app's own commands')."""
    used = set(taken)
    commands: dict[str, Command] = {}
    by_id: dict[str, Trigger] = {}
    heard: dict[str, list[str]] = {}
    for trigger in triggers:
        if not trigger.examples and not trigger.transcript:
            continue
        command_id, n = trigger.command_id, 2
        while command_id in used or not COMMAND_ID.match(command_id):
            command_id, n = f"{trigger.command_id}_{n}", n + 1
        used.add(command_id)
        own = settings.get(trigger.key) or {}
        intent = {"description": trigger.description}
        if trigger.examples:
            intent["examples"] = list(trigger.examples)
        commands[command_id] = Command(
            command_id,
            trigger.description,
            Behavior(trigger=trigger.key),
            intent,
            disabled=bool(own.get("disabled")),
            title=trigger.title,
            off=tuple(own.get("off") or ()),
            intents={command_id: intent} if trigger.examples else {},
        )
        by_id[command_id] = trigger
        if trigger.transcript and not own.get("disabled"):
            heard[command_id] = list(trigger.transcript)
    return Compiled(commands, by_id, _intents(heard))


def _intents(heard: dict[str, list[str]]) -> Intents | None:
    if not heard:
        return None
    intents = Intents.from_dict({
        "language": languages.current().code,
        "intents": {command_id: {"data": [{"sentences": sentences}]} for command_id, sentences in heard.items()},
    })
    # Home Assistant takes every list a trigger names as free text.
    for intent in intents.intents.values():
        for data in intent.data:
            for sentence in data.sentences:
                for name in _list_names(sentence.expression):
                    intents.slot_lists[name] = WildcardSlotList(name)
    return intents


def _list_names(expression: Expression) -> set[str]:
    if isinstance(expression, ListReference):
        return {expression.list_name, expression.slot_name}
    if isinstance(expression, Group):
        return set().union(*(_list_names(item) for item in expression.items)) if expression.items else set()
    return set()


def match(heard: Intents | None, text: str) -> list[tuple[str, dict[str, Any]]]:
    """The triggers matched on the transcript that ``text`` is, as Home
    Assistant matches them: by command id, with what each slot took."""
    if heard is None or not text.strip():
        return []
    found: dict[str, dict[str, Any]] = {}
    for result in recognize_all(text, heard):
        found.setdefault(result.intent.name, {name: entity.value for name, entity in result.entities.items()})
    return list(found.items())


# ------------------------------------------------------------------ running


_DONE: dict[str, str] = {}
"""Home Assistant's "Done", by language."""


async def done_said(hass: HomeAssistant, language: str) -> str:
    """What Home Assistant's own agent answers when a trigger's automation
    set no response, in ``language``."""
    if language not in _DONE:
        said = "Done"
        with contextlib.suppress(HassError, ConnectionError, OSError, TimeoutError):
            result = await hass.call({
                "type": "frontend/get_translations", "language": language, "category": "conversation",
                "integration": ["conversation"],
            })
            said = str(((result or {}).get("resources") or {}).get("component.conversation.conversation.agent.done") or said)
        _DONE[language] = said
    return _DONE[language]


# ------------------------------------------------------------------ keeping


class Reader:
    """Keeps the triggers current: read on connecting, again when
    automations are reloaded or one is turned on or off. ``on_change`` gets
    them, prepared, when they changed."""

    def __init__(self, hass: HomeAssistant, on_change: Callable[[list[Trigger]], Awaitable[None]]) -> None:
        self.hass = hass
        self.on_change = on_change
        self.triggers: list[Trigger] = []
        self.error: str | None = None
        """Why none could be read (an older Home Assistant, a user who isn't an admin)."""
        self._automations: list[str] = []
        self._watching: int | None = None
        self._soon: asyncio.Task | None = None
        hass.on_connect(self._connected)

    async def _connected(self) -> None:
        self._automations, self._watching = [], None  # subscriptions died with the connection
        await self.hass.subscribe({"type": "subscribe_events", "event_type": "automation_reloaded"}, lambda _e: self.soon())
        await self.refresh()

    def soon(self) -> None:
        if self._soon is not None and not self._soon.done():
            self._soon.cancel()

        async def later() -> None:
            await asyncio.sleep(DEBOUNCE)
            await self.refresh()

        self._soon = asyncio.ensure_future(later())

    async def refresh(self) -> None:
        try:
            found, automations = await read(self.hass)
        except (HassError, ConnectionError, OSError, TimeoutError) as err:
            if self.error != str(err):
                _LOGGER.warning("Could not read Home Assistant's sentence triggers: %s", err)
            self.error = str(err)
            return
        self.error = None
        if automations != self._automations:
            await self._watch(automations)
        prepared = prepare(found)
        if prepared == self.triggers:
            return
        self.triggers = prepared
        _LOGGER.info(
            "Home Assistant's sentence triggers: %d (%d sentence(s) heard as examples, %d on the transcript)",
            len(prepared), sum(len(t.examples) for t in prepared), sum(len(t.transcript) for t in prepared),
        )
        await self.on_change(prepared)

    async def _watch(self, automations: list[str]) -> None:
        """Every automation turned on or off reads them again (its triggers
        come and go with it)."""
        if self._watching is not None:
            await self.hass.unsubscribe(self._watching)
            self._watching = None
        self._automations = automations
        if not automations:
            return
        first = True

        def changed(event: dict[str, Any]) -> None:
            nonlocal first
            if first:
                first = False  # what they are now, as subscribing says
                return
            turned = any("s" in (diff.get("+") or {}) for diff in (event.get("c") or {}).values())
            if turned or event.get("r") or event.get("a"):
                self.soon()

        self._watching = await self.hass.subscribe({"type": "subscribe_entities", "entity_ids": automations}, changed)
