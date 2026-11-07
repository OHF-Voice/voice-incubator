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

**Value lists.** A slot named as one of the home's value lists (``start the
{program} cycle``, with a list ``program``) is that list's: heard as one of
its phrases -- in the CTC evidence when the template then stands for at most
``MAX_SENTENCES`` sentences, else on the transcript -- and Home Assistant,
which takes every list as free text, is sent the template said with the
value in its place ("start the economy cycle" as "start the eco cycle"),
when it reads that back so (``heard_as``, ``said_with``).

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
import functools
import itertools
import logging
import re
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import Any

from hassil import Intents, recognize_all
from hassil.expression import Alternative, Expression, Group, ListReference, Permutation, RuleReference, TextChunk
from hassil.intents import TextSlotList, WildcardSlotList
from hassil.parse_expression import parse_sentence

from adapt_ctc.config import ConfigError, parse_config
from adapt_ctc.template import MAX_SENTENCES
from adapt_ctc.text import sentence_key

from . import languages
from .commands import COMMAND_ID, FALLBACK_STEPS, Behavior, Command, area_ids, base_config
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
    about: str = ""
    """Its automation's description, as Home Assistant has it."""
    examples: tuple[str, ...] = ()
    """The sentences it stands for, listened for in the CTC evidence."""
    transcript: tuple[str, ...] = ()
    """The templates matched on the transcript instead (free text, too many
    sentences, or ones adapt-ctc can't spell)."""
    description: str = ""
    """What it is, for hints: saying its first sentence."""
    free: tuple[tuple[str, ...], ...] = ()
    """The slots its free text fills together, one set for each way of
    saying it (its transcript templates, optional parts in or out):
    ``(("album", "artist"),)`` for ``play {album} by {artist}``; ``()`` for
    one said with none. A value list's slot is not free text."""
    lists: tuple[str, ...] = ()
    """The home's value lists its slots take: a slot named as one
    (``{program}``, the list ``program``) is heard as one of its phrases,
    and Home Assistant is sent its value."""
    values: tuple[tuple[str, str, tuple[tuple[str, str], ...]], ...] = ()
    """Each of its examples said with a value list's phrase: the example,
    the sentence Home Assistant is sent (the value in its place), and the
    value each of those slots took."""
    problems: tuple[str, ...] = ()
    """Sentences that couldn't be read, and why: never heard."""

    @property
    def label(self) -> str:
        """What the fallback's steps know it by: its automation's
        description, else its name."""
        return self.about or self.title

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
            " ".join(str(config.get("description") or "").split()),
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
    made = (result or {}).get("substituted_config") or config
    # Its description is the automation's own, not the blueprint's.
    return {**made, "description": config.get("description")} if config.get("description") else made


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


def prepare(
    triggers: Iterable[Trigger], language: str | None = None, lists: dict[str, Any] | None = None
) -> list[Trigger]:
    """Each trigger with what it is heard by: its examples (the sentences its
    templates stand for, each once), the templates matched on the
    transcript, and its description. ``lists``: the home's value lists, as
    commands have them (``{name: {value: [phrases]}}``), which a slot named
    as one takes."""
    language = language or languages.current().code
    lists = lists or {}
    out = []
    for trigger in triggers:
        taken: set[str] = set()
        examples: list[str] = []
        values: list[tuple[str, str, tuple[tuple[str, str], ...]]] = []
        transcript: list[str] = []
        problems: list[str] = []
        readable: list[str] = []
        free: list[tuple[str, ...]] = []
        used: list[str] = []
        for template in trigger.sentences:
            try:
                parsed = parse_sentence(template)
            except Exception as err:  # noqa: BLE001 - hassil raises a variety
                problems.append(f"“{template}”: {err}")
                continue
            readable.append(template)
            valued = {ref.list_name: _phrases(lists[ref.list_name]) for ref in _references(parsed.expression) if ref.list_name in lists}
            used += [name for name in valued if name not in used]
            ways = _sentences(parsed.expression, valued)
            if ways is None or not _speakable([said for said, _sent, _slots in ways], language):
                transcript.append(template)
                if not _has_rule(parsed.expression):
                    free += [s for s in _slot_sets(parsed.expression, set(valued)) if s not in free]
                continue
            for said, sent, slots in ways:
                key = sentence_key(said, language)
                if key and key not in taken:
                    taken.add(key)
                    examples.append(said)
                    if slots:
                        values.append((said, sent, slots))
        description = languages.speech().saying(_first_form(readable[0])) if readable else trigger.title
        out.append(dataclasses.replace(
            trigger, examples=tuple(examples), transcript=tuple(transcript), description=description,
            free=tuple(free), lists=tuple(used), values=tuple(values), problems=tuple(problems),
        ))
    return out


def with_lists(triggers: Iterable[Trigger], lists: dict[str, Any]) -> list[Trigger]:
    """The triggers, those with a slot named as one of the home's value
    ``lists`` prepared again to take it (``prepare``)."""
    out = []
    for trigger in triggers:
        names = {ref.list_name for template in trigger.sentences for ref in _references(_parsed(template))}
        out.append(prepare([trigger], lists=lists)[0] if names & set(lists) or trigger.lists else trigger)
    return out


def _parsed(template: str) -> Expression | None:
    try:
        return parse_sentence(template).expression
    except Exception:  # noqa: BLE001 - hassil raises a variety
        return None


def _references(expression: Expression) -> list[ListReference]:
    """Every slot a template has (``{name}``, ``{list:slot}``)."""
    if isinstance(expression, ListReference):
        return [expression]
    if isinstance(expression, Group):
        return [ref for item in expression.items for ref in _references(item)]
    return []


def _phrases(values: Any) -> dict[str, list[str]]:
    """A value list's phrases, by value: ``[red, blue]`` is each its own."""
    if isinstance(values, list):
        return {str(v): [str(v)] for v in values}
    return {str(v): [str(p) for p in (said if isinstance(said, list) else [said])] for v, said in (values or {}).items()}


def _slot_sets(expression: Expression, valued: set[str] = frozenset()) -> list[tuple[str, ...]]:
    """The slots a template's free text fills together, each way of saying
    it: an alternative is one of its items, an optional part in or out. A
    slot of one of the ``valued`` lists is not free text."""
    if isinstance(expression, ListReference):
        return [()] if expression.list_name in valued else [(expression.slot_name,)]
    if isinstance(expression, Alternative):
        return list(dict.fromkeys(s for item in expression.items for s in _slot_sets(item, valued)))
    if isinstance(expression, Group):
        sets: list[tuple[str, ...]] = [()]
        for item in expression.items:
            sets = list(dict.fromkeys(tuple(sorted({*a, *b})) for a in sets for b in _slot_sets(item, valued)))
        return sets
    return [()]


def _has_rule(expression: Expression) -> bool:
    if isinstance(expression, RuleReference):
        return True
    return isinstance(expression, Group) and any(_has_rule(item) for item in expression.items)


def _sentences(
    expression: Expression, lists: dict[str, dict[str, list[str]]] | None = None
) -> list[tuple[str, str, tuple[tuple[str, str], ...]]] | None:
    """Every sentence a template stands for -- as said, as Home Assistant is
    sent it (a value list's value in place of its phrase), and the value
    each of those slots took; None with free text or a rule in it, or more
    than MAX_SENTENCES. ``lists``: the value lists its slots take."""
    lists = lists or {}
    if _has_reference(expression, lists):
        return None
    out: dict[str, tuple[str, str, tuple[tuple[str, str], ...]]] = {}
    for said, sent, slots in _ways(expression, lists):
        sentence = " ".join(said.split())
        if sentence and sentence not in out:
            out[sentence] = (sentence, " ".join(sent.split()), slots)
            if len(out) > MAX_SENTENCES:
                return None
    return list(out.values())


def _ways(expression: Expression, lists: dict[str, dict[str, list[str]]]) -> Iterator[tuple[str, str, tuple]]:
    """Each way of saying a template with no free text, as hassil samples
    it: what is said, what is sent, and the values its value lists' slots took."""
    if isinstance(expression, TextChunk):
        yield expression.original_text, expression.original_text, ()
    elif isinstance(expression, ListReference):
        for value, phrases in lists[expression.list_name].items():
            for phrase in phrases:
                yield phrase, value, ((expression.slot_name, value),)
    elif isinstance(expression, Alternative):
        for item in expression.items:
            yield from _ways(item, lists)
    elif isinstance(expression, Permutation):
        parts = [list(_ways(item, lists)) for item in expression.items]
        for order in itertools.permutations(parts):
            for combination in itertools.product(*order):
                said, sent, slots = _joined(combination)
                yield said.strip(), sent.strip(), slots
    elif isinstance(expression, Group):
        yield from map(_joined, itertools.product(*(list(_ways(item, lists)) for item in expression.items)))


def _joined(parts: Iterable[tuple[str, str, tuple]]) -> tuple[str, str, tuple]:
    parts = list(parts)
    return "".join(p[0] for p in parts), "".join(p[1] for p in parts), tuple(s for p in parts for s in p[2])


def _has_reference(expression: Expression, lists: dict[str, Any] | None = None) -> bool:
    """Whether a template has free text (a slot of none of ``lists``) or a rule."""
    if isinstance(expression, ListReference):
        return expression.list_name not in (lists or {})
    if isinstance(expression, RuleReference):
        return True
    return isinstance(expression, Group) and any(_has_reference(item, lists) for item in expression.items)


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
    """``commands.json``'s ``triggers``: by key, ``disabled``, ``off`` and
    ``fallback`` (``meaning``, ``llm``: the steps that may run it). Raises
    ConfigError."""
    if saved is None:
        return {}
    if not isinstance(saved, dict):
        raise ConfigError("triggers: a mapping of trigger keys to their settings")
    out = {}
    for key, value in saved.items():
        where = f"triggers.{key}"
        if not isinstance(value, dict):
            raise ConfigError(f"{where}: expected a mapping (disabled, off, fallback)")
        unknown = set(value) - {"disabled", "off", "fallback"}
        if unknown:
            raise ConfigError(f"{where}: unknown key(s) {sorted(unknown)}")
        steps = value.get("fallback") or []
        if not isinstance(steps, list) or set(map(str, steps)) - set(FALLBACK_STEPS):
            raise ConfigError(f"{where}.fallback: a list of {', '.join(FALLBACK_STEPS)}")
        out[str(key)] = {
            "disabled": bool(value.get("disabled", False)), "off": area_ids(value.get("off"), f"{where}.off"),
            "fallback": [str(s) for s in steps],
        }
    return out


def compile_triggers(
    triggers: Iterable[Trigger], settings: dict[str, dict[str, Any]], taken: Iterable[str], lists: dict[str, Any] | None = None
) -> Compiled:
    """Commands for the triggers, with their settings; ids clear of ``taken``
    (the app's own commands'). ``lists``: the home's value lists, which a
    slot named as one takes on the transcript (as ``prepare``)."""
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
            # Meaning sends one of its sentences, and the model one of its
            # templates said with what it heard (``sentence_for``).
            fallback=tuple(
                step for step in own.get("fallback") or ()
                if (step == "meaning" and trigger.examples) or (step == "llm" and any(trigger.free))
            ),
            label=trigger.label,
            combinations=tuple(dict.fromkeys(([()] if trigger.examples else []) + list(trigger.free))) or ((),),
            intents={command_id: intent} if trigger.examples else {},
        )
        by_id[command_id] = trigger
        if trigger.transcript and not own.get("disabled"):
            heard[command_id] = list(trigger.transcript)
    return Compiled(commands, by_id, _intents(heard, lists))


def _intents(heard: dict[str, list[str]], lists: dict[str, Any] | None = None) -> Intents | None:
    """The templates as hassil matches them, an intent each: Home Assistant
    takes every list a trigger names as free text; here, a slot named as one
    of the home's value ``lists`` is one of its phrases, its value what it
    takes."""
    if not heard:
        return None
    intents = Intents.from_dict({
        "language": languages.current().code,
        "intents": {command_id: {"data": [{"sentences": sentences}]} for command_id, sentences in heard.items()},
    })
    lists = lists or {}
    for intent in intents.intents.values():
        for data in intent.data:
            for sentence in data.sentences:
                for ref in _references(sentence.expression):
                    if ref.list_name in lists:
                        intents.slot_lists[ref.list_name] = TextSlotList.from_tuples(
                            [(phrase, value) for value, phrases in _phrases(lists[ref.list_name]).items() for phrase in phrases],
                            allow_template=False, name=ref.list_name,
                        )
                        continue
                    for name in (ref.list_name, ref.slot_name):
                        intents.slot_lists.setdefault(name, WildcardSlotList(name))
    return intents


@functools.lru_cache(maxsize=256)
def _home_assistants(sentences: tuple[str, ...], language: str) -> Intents | None:
    """A trigger's templates as Home Assistant reads them: every list free text."""
    readable = [s for s in sentences if _parsed(s) is not None]
    return _intents({"trigger": readable}) if readable else None


def _reads_back(trigger: Trigger, sentence: str, values: dict[str, Any]) -> bool:
    """Whether Home Assistant takes ``sentence`` as this trigger's, each slot
    taking these words."""
    heard = _home_assistants(trigger.sentences, languages.current().code)
    wanted = {k: _same(v) for k, v in values.items()}
    return heard is not None and any(
        {k: _same(e.value) for k, e in result.entities.items()} == wanted for result in recognize_all(sentence, heard)
    )


def heard_as(trigger: Trigger | None, example: str) -> tuple[str, dict[str, str]]:
    """What Home Assistant is sent for one of a trigger's examples, and the
    value each value list's slot took: the example, with each value in place
    of its phrase when Home Assistant reads that back so."""
    for said, sent, slots in trigger.values if trigger is not None else ():
        if said == example:
            values = dict(slots)
            return (sent if _reads_back(trigger, sent, values) else example), values
    return example, {}


def said_with(trigger: Trigger | None, text: str, slots: dict[str, Any]) -> str:
    """What Home Assistant is sent for a trigger matched on the transcript
    ``text``: for one with value lists, one of its templates said with what
    each slot took -- a value list's value -- when Home Assistant reads it
    back so; else ``text``."""
    if trigger is None or not trigger.lists:
        return text
    values = {k: " ".join(str(v).split()) for k, v in slots.items() if k != SENTENCE and v not in (None, "")}
    for template in trigger.transcript:
        said = _said_with(template, values)
        if said is not None and _reads_back(trigger, said, values):
            return said
    return text


def match(heard: Intents | None, text: str) -> list[tuple[str, dict[str, Any]]]:
    """The triggers matched on the transcript that ``text`` is, as Home
    Assistant matches them: by command id, with what each slot took."""
    if heard is None or not text.strip():
        return []
    found: dict[str, dict[str, Any]] = {}
    for result in recognize_all(text, heard):
        found.setdefault(result.intent.name, {name: entity.value for name, entity in result.entities.items()})
    return list(found.items())


def sentence_for(
    command: Command,
    trigger: Trigger | None,
    slots: dict[str, Any],
    closest: str | None = None,
) -> str | None:
    """What Home Assistant is sent for a trigger the fallback ran -- what was
    said ("kill the party", "put on Kind of Blue by Miles Davis") is no
    sentence of its, and would run nothing there. With free text heard
    (``slots``), a template of its said with it, when Home Assistant reads
    that back as this trigger, the same words in each slot; else, of its own
    sentences, the one meaning found closest, else its first (``heard_as``).
    None: nothing to send."""
    free = {k: " ".join(str(v).split()) for k, v in slots.items() if k != SENTENCE and v not in (None, "")}
    if free:
        for template in trigger.transcript if trigger is not None else ():
            said = _said_with(template, free)
            if said is not None and _reads_back(trigger, said, free):
                return said
        return None
    examples = [str(e) for e in command.intent.get("examples") or []]
    example = closest if closest in examples else (examples[0] if examples else None)
    return heard_as(trigger, example)[0] if example is not None else None


def _said_with(template: str, values: dict[str, str]) -> str | None:
    """A template said with these slots' words, all of them: of each
    alternative, the way that takes the most. None when it can't be."""
    try:
        said = _with(parse_sentence(template).expression, values)
    except Exception:  # noqa: BLE001 - hassil raises a variety
        return None
    if said is None or said[1] != set(values):
        return None
    return " ".join(said[0].split())


def _with(expression: Expression, values: dict[str, str]) -> tuple[str, set[str]] | None:
    if isinstance(expression, TextChunk):
        return expression.text, set()
    if isinstance(expression, ListReference):
        return (values[expression.slot_name], {expression.slot_name}) if expression.slot_name in values else None
    if isinstance(expression, RuleReference):
        return None
    if isinstance(expression, Alternative):
        ways = [w for w in (_with(item, values) for item in expression.items) if w is not None]
        return max(ways, key=lambda w: len(w[1]), default=None)
    if isinstance(expression, Group):
        parts = [_with(item, values) for item in expression.items]
        if any(p is None for p in parts):
            return None
        joiner = " " if isinstance(expression, Permutation) else ""
        return joiner.join(p[0] for p in parts if p), set().union(*(p[1] for p in parts if p))
    return "", set()


def _same(text: Any) -> str:
    return " ".join(str(text).split()).casefold().strip(" .,!?")


def meaning_texts(found: dict[str, Trigger], commands: dict[str, Command], intents: Iterable[str]) -> dict[str, list[str]]:
    """What meaning knows the triggers by that listen for nothing (all free
    text: no adapt-ctc intent of their own, in ``intents``), by command id:
    their label and their templates, each free text said by its slot's
    name ("play album by artist"). So the model step is given them among
    the closest commands."""
    listening = set(intents)
    out: dict[str, list[str]] = {}
    for command_id, trigger in found.items():
        command = commands.get(command_id)
        if command is None or command.disabled or command_id in listening:
            continue
        out[command_id] = list(dict.fromkeys([trigger.label, *filter(None, map(_said_by_names, trigger.transcript))]))
    return out


def _said_by_names(template: str) -> str | None:
    """A template with each free text said by its slot's name, the most of
    them it takes together: "play album by artist"."""
    try:
        sets = _slot_sets(parse_sentence(template).expression)
    except Exception:  # noqa: BLE001
        return None
    return _said_with(template, {s: s.replace("_", " ") for s in max(sets, key=len, default=())})


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
