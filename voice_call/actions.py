"""Doing what a command says, in Home Assistant.

Every kind of command -- a spoken response, a script, a scene, an action -- is
run the same way: as an action sequence through ``execute_script``, with these
variables, which the sequence and the response template can both use:

==============  ============================================================
``slots``       what was heard, plus the command's fixed slots: ``entity``,
                ``area``, ``floor`` (lists of ids), ``number`` (or the
                preset's name: ``percentage``, ``duration`` ...), value
                lists, free text
``satellite``   ``id``, ``name``, ``area_id``, ``area_name``, ``floor_id``,
                ``floor_name``, ``device_id``, ``timer_list``
``speaker``     who is speaking (voice_call/people.py): ``name`` (None when
                nobody knows), ``score`` (by their voice), ``by`` (``voice``,
                ``wake_word``, ``only_person``), and their own
                ``todo_list``, ``calendar`` and ``playlist``
``wake_word``   what started the call: ``id`` (``okay_nabu_mike``), ``name``
                ("Okay Nabu (Mike)") and ``phrase`` ("okay nabu"); None
                each when typed on the web page
``target``      ``entity_id`` from the slots (a room said with a device only
                picks it out), else ``area_id``/``floor_id``, else the
                satellite's area: ``target: "{{ target }}"``
``command``     ``id``, ``description``, ``text`` (what was heard)
``place``       where it acts: ``here`` (only the satellite's area),
                ``said`` ("in the Kitchen", "upstairs"; empty when unknown),
                ``areas`` (ids; a floor's every area)
``result``      in the response only: what the sequence returned, plus
                ``outcome`` and ``speech`` (what would be said without the
                response); ``error`` when it failed
``timer``       for a timer command, in the response only: the timer it was
                about; ``timers``: the ones it listed
``items``       for a command on a to-do list's items (``on_list``): each
                item said, as the list has it; the slot is them all, said
                ("Apples and Bananas")
==============  ============================================================

A timer command (``timer: cancel`` ...) is carried out by the app on the
satellite's voice_timer_list (voice_call/timers.py), and says what it did unless
the command has a response of its own. A music command (``music: play`` ...)
is carried out on a Music Assistant player (voice_call/music.py); its response
also sees ``player`` (the one it acted on), ``players``, when it searched
``media`` (what it found), and when it moved the music ``source`` (the player
it came from).

How it went is the outcome: ``done``, ``failed`` (Home Assistant refused), a
timer operation's own (``no_timers``, ``not_found`` ...), or the ``outcome`` a
script returned. A command that needs its target able to do something
(``supports``; voice_call/capabilities.py) acts only on what can, and with
nothing that can, is ``not_supported``; what it left out is said at the
verbose level (``result.skipped``). The command's response for that outcome is spoken, or else
the built-in sentence: what the timer operation says, the script's
``speech``, or that it failed (the language's FAILED).

How much is said when it is done is the command's verbosity
(commands.VERBOSITY): nothing at all, a beep, or its response for that level
-- or the closest written -- else the built-in sentence for it. A beep is for what the
satellite's own area can see happen: a command that acted elsewhere says the
``elsewhere`` level instead (settings.json ``beep_elsewhere``), and a
question always answers.

Every command that runs also fires a ``voice_call_command`` event with the
same variables plus ``outcome`` and ``response`` (what was said), so
automations can follow along.

When nothing understood a sentence, the Unknown command's response (the
store's ``unknown``) is said instead of the built-in one, rendered with
``unknown_variables``: ``satellite``, ``speaker``, ``wake_word``, ``place``, ``target`` and
``slots`` (none) as above, and ``transcript`` (what the speech model heard),
``heard`` (what the keyword matcher heard), ``hint`` (the near miss, "It
looks like you're trying to …, but …"; empty when nothing came close),
``closest`` (that command's ``id`` and ``description``, or none) and
``response`` (what would be said without it).
"""

from __future__ import annotations

import difflib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from adapt_ctc.text import spoken_words

from . import alarms, capabilities, languages, timers
from . import music as music_ops
from . import people as people_ops
from .commands import QUIET, SPOKEN, Behavior, Command, closest_text
from .engine import Decision, Satellite
from .hass import HassError, HomeAssistant

_LOGGER = logging.getLogger(__name__)

EVENT_TYPE = "voice_call_command"
BEEP_ELSEWHERE = "specific"
"""What a command that beeps says when it acted out of the satellite's area."""


@dataclass
class Outcome:
    variables: dict[str, Any]
    sequence: list[dict]
    dry_run: bool
    response: Any = None
    """What the sequence returned (``stop`` with ``response_variable``)."""

    speech: str = ""
    name: str = "done"
    """How it went: ``done``, ``failed``, a timer operation's or a script's own."""

    error: str | None = None
    timings: dict[str, float] = field(default_factory=dict)
    timer: dict[str, Any] | None = None
    """For a timer command: what it planned (voice_call/timers.py)."""

    elsewhere: str = BEEP_ELSEWHERE
    """The level for a beep when it acted out of the satellite's area."""

    music: dict[str, Any] | None = None
    """For a music command: the players, and what it searched and found."""

    verbosity: dict[str, Any] | None = None
    """When done: the level said at, and why."""

    silent: bool = False
    """Done, and nothing said: not even the acknowledgement sound."""

    skipped: list[str] = field(default_factory=list)
    """What it left out, unable to do what the command needs (names)."""

    skipped_said: str = ""
    """That, said: at the verbose level, after what was done."""

    also_said: str = ""
    """Said after what was done at any level (but silent): what of what was
    asked for it couldn't find."""

    def to_json(self) -> dict[str, Any]:
        return {
            "outcome": self.name,
            "verbosity": self.verbosity,
            "timer": self.timer,
            "music": self.music,
            "dry_run": self.dry_run,
            "variables": {k: v for k, v in self.variables.items() if k not in PRIVATE},
            "sequence": self.sequence,
            "response": self.response,
            "speech": self.speech,
            "silent": self.silent,
            "skipped": self.skipped,
            "error": self.error,
            "timings": {k: round(v, 4) for k, v in self.timings.items()},
        }


def variables(
    command: Command,
    slots: dict[str, Any],
    satellite: Satellite,
    speaker: dict[str, Any] | None,
    text: str,
    catalog: Any = None,
    wake_word: dict[str, Any] | None = None,
) -> dict[str, Any]:
    target: dict[str, list[str]] = {}
    # Named devices are the target; a room said with them only picked them
    # out ("the fan in the office"), it is not one more thing to act on.
    named = (("entity", "entity_id"),) if slots.get("entity") else (("area", "area_id"), ("floor", "floor_id"))
    for slot, key in named:
        if slots.get(slot):
            target[key] = list(slots[slot])
    if not target and satellite.area_id:
        target = {"area_id": [satellite.area_id]}
    exposed = getattr(catalog, "exposed", None)
    return {
        "slots": slots,
        "satellite": satellite.as_dict(),
        "speaker": speaker or people_ops.Speaker().as_dict(),
        "wake_word": wake_word or NO_WAKE_WORD,
        "target": target,
        "command": {"id": command.id, "description": command.description, "text": text},
        "place": place(slots, satellite, catalog),
        # What a question about a place counts: what is exposed to Assist
        # (not known: every entity the app knows of).
        "exposed": sorted(exposed if exposed is not None else getattr(catalog, "entities", None) or []),
    }


NO_WAKE_WORD = {"id": None, "name": None, "phrase": None}
"""``wake_word`` when nothing woke the satellite (typed on the web page)."""

PRIVATE = ("exposed",)
"""Variables left out of the command event and the call's record: long, and
the same every time."""


def unknown_variables(
    decision: Decision,
    satellite: Satellite,
    speaker: dict[str, Any] | None,
    commands: dict[str, Command],
    catalog: Any = None,
    wake_word: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """What the Unknown command's response sees: a command's variables
    where there are any, and what was heard and came close."""
    hint = decision.hint or {}
    closest = commands.get(str(hint.get("command") or ""))
    return {
        "transcript": decision.transcript,
        "heard": str(decision.recognition.get("transcript") or ""),
        "hint": str(hint.get("text") or "") if closest is not None else "",
        "closest": {"id": closest.id, "description": closest.description} if closest is not None else None,
        "response": decision.message or languages.speech().SORRY,
        "slots": {},
        "satellite": satellite.as_dict(),
        "speaker": speaker or people_ops.Speaker().as_dict(),
        "wake_word": wake_word or NO_WAKE_WORD,
        "target": {"area_id": [satellite.area_id]} if satellite.area_id else {},
        "place": place({}, satellite, catalog),
        "exposed": sorted(getattr(catalog, "exposed", None) or getattr(catalog, "entities", None) or []),
    }


async def render_said(
    hass: HomeAssistant, template: str, values: dict[str, Any]
) -> tuple[str | None, str | None]:
    """A response template of yours -- the Unknown command's, a skill's --
    rendered: ``(said, error)``; said is None when it renders to nothing or
    cannot be rendered."""
    try:
        said = _tidy(await hass.render_template(template, values))
    except (HassError, ConnectionError, OSError, TimeoutError) as err:
        return None, str(err) or type(err).__name__
    return (said or None), None


def place(slots: dict[str, Any], satellite: Satellite, catalog: Any = None) -> dict[str, Any]:
    """Where a command acts: the areas, floors and devices it names, or else
    the satellite's area. ``here`` when that is only the satellite's area --
    a device in no known area is not here. ``said`` is where, as the
    language says it ("in the Kitchen")."""
    here, places, areas = _where(slots, satellite, catalog)
    return {"here": here, "said": languages.speech().place(places), "areas": areas}


def _where(slots: dict[str, Any], satellite: Satellite, catalog: Any) -> tuple[bool, list[tuple[str, str]], list[str]]:
    """place(), with where it is said to be as ``("area" | "floor", name)``."""
    own = satellite.area_id
    named_areas = [str(a) for a in slots.get("area") or []]
    floors = [str(f) for f in slots.get("floor") or []]
    entities = [str(e) for e in slots.get("entity") or []]
    if not (named_areas or floors or entities):
        return True, [("area", satellite.area_name)] if satellite.area_name else [], [own] if own else []
    areas = list(named_areas)
    entity_areas: list[str] = []
    if catalog is not None:
        for floor in floors:
            areas += [a.area_id for a in catalog.areas.values() if a.floor_id == floor]
        for entity_id in entities:
            entity = catalog.entities.get(entity_id)
            if entity is not None and entity.area_id:
                entity_areas.append(entity.area_id)
    areas = list(dict.fromkeys(areas + entity_areas))
    unplaced = any(catalog is None or not getattr(catalog.entities.get(e), "area_id", None) for e in entities)
    here = bool(own) and not unplaced and (not floors or areas == [own]) and set(areas) == {own}

    def area_name(area_id: str) -> str:
        if area_id == own and satellite.area_name:
            return satellite.area_name
        area = catalog.areas.get(area_id) if catalog is not None else None
        return area.name if area is not None else area_id.replace("_", " ").title()

    if floors:
        names = [catalog.floors[f].name if catalog is not None and f in catalog.floors else f.replace("_", " ") for f in floors]
        return here, [("floor", name) for name in names], areas
    return here, [("area", area_name(a)) for a in (named_areas or list(dict.fromkeys(entity_areas)))], areas


async def run(
    hass: HomeAssistant,
    decision: Decision,
    satellite: Satellite,
    speaker: dict[str, Any] | None,
    dry_run: bool = False,
    integration=None,
    music: music_ops.Music | None = None,
    elsewhere: str = BEEP_ELSEWHERE,
    calendar: alarms.AlarmCalendar | None = None,
    wake_word: dict[str, Any] | None = None,
    people: dict[str, Any] | None = None,
    home: dict[str, Any] | None = None,
) -> Outcome:
    """Run the decided command (unless ``dry_run``) and render what to say.
    ``elsewhere``: the level a command that beeps says at when it acted out
    of the satellite's area. ``calendar``: where alarms and reminders are, in
    a language that keeps them there. ``people``: everyone's own things, for
    a command on the speaker's (``mine``); ``home``: the home's (Settings),
    for one on the home's without its own (``home``)."""
    command, behavior = decision.command, decision.behavior
    assert command is not None and behavior is not None
    catalog = getattr(hass, "catalog", None)
    known, theirs = (True, {}) if not behavior.mine else people_ops.mine(behavior.mine, speaker, people or {})
    if theirs:
        decision.slots = {**decision.slots, **theirs}
    unset = False
    if behavior.home and not decision.slots.get("entity"):
        entity_id = (home or {}).get(behavior.home)
        if entity_id:
            decision.slots = {**decision.slots, "entity": [entity_id]}
        else:
            unset = True
    checked = capabilities.Check(slots=decision.slots)
    if behavior.supports and known and not unset:
        checked = await _supports(hass, behavior, decision.slots, satellite, catalog)
        decision.slots = checked.slots
    missing: list[str] = []
    found: list[str] = []
    for slot, entity_id in behavior.on_list.items():
        said = str(decision.slots.get(slot) or "")
        if entity_id == "entity":  # the command's to-do list: its target
            entity_id = str((decision.slots.get("entity") or [""])[0])
        if said and entity_id:
            items, lost = await _items_on(hass, entity_id, said)
            found, missing = found + items, missing + lost
            if items:
                decision.slots = {**decision.slots, slot: languages.speech().join(items)}
    values = variables(command, decision.slots, satellite, speaker, decision.transcript, catalog, wake_word)
    if behavior.on_list:
        values["items"] = found
    outcome = Outcome(values, behavior.sequence(), dry_run, elsewhere=elsewhere)
    if not known:
        return await _say(hass, behavior, values, outcome, "no_speaker", languages.speech().NO_SPEAKER)
    if unset:
        return await _say(hass, behavior, values, outcome, "not_set", languages.speech().NOT_SET)
    if missing:
        sentence = _capital(languages.speech().not_on_list(missing))
        if not found:
            return await _say(hass, behavior, values, outcome, "not_on_list", sentence)
        # Done on the others, and said, however little it says.
        outcome.also_said = sentence
    if checked.cannot:
        named = bool(decision.slots.get("entity"))
        capability, value = _lacking(behavior.supports, checked.cannot[0], decision.slots, catalog)
        names = _names(checked.cannot, catalog)
        if checked.nothing:
            sentence = (
                languages.speech().cannot(names, capability, value) if named
                else languages.speech().none_can(str(values["place"].get("said") or ""), capability)
            )
            return await _say(hass, behavior, values, outcome, "not_supported", _capital(sentence), {"cannot": names})
        # Left out of a device list, or of an intent's place: said when verbose.
        if named or behavior.intent:
            outcome.skipped = names
            outcome.skipped_said = _capital(languages.speech().cannot(names, capability, value))
    if behavior.timer in alarms.OPERATIONS and (behavior.timer != "alarm" or _calendar_ready(calendar)):
        return await _run_calendar(hass, behavior, values, satellite, outcome, calendar)
    if behavior.timer:
        return await _run_timer(hass, behavior, values, satellite, outcome, integration)
    if behavior.music:
        return await _run_music(hass, behavior, values, satellite, outcome, music or music_ops.Music(hass))

    if behavior.intent:
        return await _run_intent(hass, behavior, values, satellite, outcome, checked.entities)

    if outcome.sequence and not dry_run:
        started = time.perf_counter()
        try:
            outcome.response = await hass.execute_script(outcome.sequence, values)
        except (HassError, ConnectionError, OSError, TimeoutError) as err:
            outcome.error = str(err)
            if behavior.script and "expected a dictionary" in outcome.error:
                outcome.error += " (a script returns a mapping: {speech: ...}, not text)"
            _LOGGER.error("Command %s failed: %s", command.id, outcome.error)
            return await _say(hass, behavior, values, outcome, "failed", languages.speech().FAILED, {"error": outcome.error})
        finally:
            outcome.timings["execute"] = time.perf_counter() - started

    returned = outcome.response if isinstance(outcome.response, dict) else {}
    name = returned.get("outcome")
    speech = returned.get("speech")
    return await _say(
        hass,
        behavior,
        values,
        outcome,
        name if isinstance(name, str) and name else "done",
        speech if isinstance(speech, str) else "",
        returned,
    )


async def _items_on(hass: HomeAssistant, entity_id: str, said: str) -> tuple[list[str], list[str]]:
    """The items on a to-do list that ``said`` names (``closest_item``), and
    what names none: the whole of it ("macaroni and cheese"), else each part
    ("apples and bananas"). When the list can't be read, each part as it is."""
    try:
        response = await hass.call_service(
            "todo", "get_items", {"status": ["needs_action", "completed"]}, {"entity_id": entity_id}, return_response=True
        )
    except (HassError, ConnectionError, OSError, TimeoutError) as err:
        _LOGGER.warning("Could not read %s: %s", entity_id, err)
        return split_items(said) or [said], []
    listed = (((response or {}).get(entity_id) or {}).get("items")) or []
    names = [str(i.get("summary") or "") for i in listed if isinstance(i, dict)]
    whole = closest_item(said, names)
    if whole is not None:
        return [whole], []
    found, missing = [], []
    for part in split_items(said):
        # Said without a pause between them, several in a row: "eggs milk".
        items = [item] if (item := closest_item(part, names)) is not None else _in_a_row(part, names)
        if not items:
            missing.append(part)
        found += [i for i in items if i not in found]
    return found, missing


def _in_a_row(said: str, names: list[str]) -> list[str]:
    """Items said one after another ("eggs milk"), the longest first at each
    word; none unless every word is one's."""
    words, items, start = said.split(), [], 0
    while start < len(words):
        end = next((end for end in range(len(words), start, -1) if closest_item(" ".join(words[start:end]), names)), None)
        if end is None:
            return []
        items.append(closest_item(" ".join(words[start:end]), names))
        start = end
    return items if len(items) > 1 else []


def split_items(said: str) -> list[str]:
    """Items said together: "apples, bananas and pears" (the language's
    AND)."""
    joins = "|".join(re.escape(word) for word in languages.reading().AND)
    return [part.strip() for part in re.split(rf"\s*,\s*|\s+(?:{joins})\s+", said) if part.strip()]


ITEM_CLOSE = 0.8
"""How alike in spelling an item must be to what was said, when none is the
same words."""


def closest_item(said: str, items: list[str]) -> str | None:
    """The item ``said`` names: the same words as spoken, without a leading
    article ("test three", "the test 3": "test 3"), else the closest in
    spelling, at ``ITEM_CLOSE`` or more."""
    language = languages.current().code
    articles = set(languages.reading().ARTICLES)

    def spoken(text: str) -> str:
        spellings = spoken_words(text, language)
        words = list(spellings[0]) if spellings else text.lower().split()
        while words and words[0] in articles:
            words = words[1:]
        return " ".join(words)

    wanted = spoken(said)
    heard = [(spoken(item), item) for item in items if item.strip()]
    for words, item in heard:
        if words == wanted:
            return item
    scored = [(difflib.SequenceMatcher(None, wanted, words).ratio(), item) for words, item in heard]
    best = max(scored, default=None)
    return best[1] if best is not None and best[0] >= ITEM_CLOSE else None


async def _supports(
    hass: HomeAssistant, behavior: Behavior, slots: dict[str, Any], satellite: Satellite, catalog: Any
) -> capabilities.Check:
    """What of the command's target can do what it needs: the devices named
    (or its own, or the home's), else what its intent would act on in the
    areas and floors named, the satellite's area -- or, for an intent that
    goes there, the whole home. A value said that could mean several of a
    device's is read against what it is set to now."""
    call = behavior.intent
    named = bool(slots.get("entity"))
    placed = bool(slots.get("area") or slots.get("floor"))
    whole = call is not None and call.home and not (named or placed)
    areas = None if whole else _where(slots, satellite, catalog)[2]
    kind = {key: _slot_value(call.slots.get(key), slots) for key in ("domain", "device_class")} if call else {}
    now = await _now(hass, capabilities.unsure(behavior.supports, slots, catalog))
    checked = capabilities.check(behavior.supports, slots, catalog, areas, kind, now)
    if call is not None and call.anywhere and not (named or placed) and not checked.found:
        # Nothing of the kind here: the whole home's, as the intent goes.
        checked = capabilities.check(behavior.supports, slots, catalog, None, kind)
    return checked


async def _now(hass: HomeAssistant, wanted: dict[str, str | None]) -> dict[str, Any]:
    """What devices are set to now, by entity id: the attribute given, else
    the state. Empty when it can't be read."""
    if not wanted:
        return {}
    said = [f"states({e!r})" if attribute is None else f"state_attr({e!r}, {attribute!r})" for e, attribute in wanted.items()]
    try:
        values = await hass.render_value("{{ [" + ", ".join(said) + "] | tojson }}", {})
    except (HassError, ConnectionError, OSError, TimeoutError) as err:
        _LOGGER.warning("Could not read %s: %s", ", ".join(wanted), err)
        return {}
    if isinstance(values, str):  # not parsed, as Home Assistant would
        try:
            values = json.loads(values)
        except ValueError:
            return {}
    return dict(zip(wanted, values)) if isinstance(values, list) else {}


def _slot_value(value: Any, slots: dict[str, Any]) -> Any:
    """An intent's slot as written, or the slot its template is
    (``{{ slots.device_class }}``); None for a template that says more."""
    if not isinstance(value, str) or "{" not in value:
        return value
    match = re.fullmatch(r"\{\{\s*slots\.(\w+)\s*\}\}", value.strip())
    return slots.get(match.group(1)) if match else None


def _lacking(needs: dict[str, str | None], entity_id: str, slots: dict[str, Any], catalog: Any) -> tuple[str | None, str | None]:
    """What a device can't, of what the command needs: the capability, and
    the value it doesn't have ("dry"), if that is all."""
    entity = getattr(catalog, "entities", {}).get(entity_id)
    for capability, slot in needs.items():
        if capabilities.can(entity, capability) is False:
            return capability, None
        value = slots.get(slot) if slot else None
        if value is not None and capabilities.can(entity, capability, value) is False:
            return capability, str(value).replace("_", " ")
    return next(iter(needs), None), None


def _names(entity_ids: list[str], catalog: Any) -> list[str]:
    """Devices as Home Assistant names them."""
    entities = getattr(catalog, "entities", None) or {}
    return [entities[e].name if e in entities else e for e in entity_ids]


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


async def _run_intent(
    hass: HomeAssistant,
    behavior: Behavior,
    values: dict[str, Any],
    satellite: Satellite,
    outcome: Outcome,
    only: list[str] | None = None,
) -> Outcome:
    """Run the command's intent on what was heard -- each device named (by
    entity id, exposed to Assist or not: a name is the Names page's), each
    area or floor named, else the satellite's area (what is exposed to
    Assist there) -- and say how it went: done on what it found (but the
    unavailable), or why it found nothing it could act on. ``only``: the
    devices there that can do what it needs, each by name in place of the
    place. Its response sees ``result``: ``count``, ``names`` and ``ids`` of
    what it acted on, the ``unavailable`` ones, and Home Assistant's own
    ``speech``."""
    call = behavior.intent
    assert call is not None
    say = languages.speech()
    slots = values["slots"]
    named = bool(slots.get("entity"))
    placed = bool(slots.get("area") or slots.get("floor"))
    # A device by name is said as itself ("That isn't exposed"); else where.
    where = "" if named else str(values["place"].get("said") or "")
    if named:
        targets = [{"name": str(e)} for e in slots["entity"]]
    elif only:
        targets = [{"name": e} for e in only]
    elif placed:
        areas, floors = [str(a) for a in slots.get("area") or []], [str(f) for f in slots.get("floor") or []]
        within = {"floor": floors[0]} if len(floors) == 1 else {}
        targets = [{"area": a, **within} for a in areas] if areas else [{"floor": f} for f in floors]
    elif call.home:
        targets = [{}]
    elif satellite.area_id:
        targets = [{"area": satellite.area_id}]
    elif call.anywhere:
        targets = [{}]
    else:
        outcome.error = "the satellite has no area"
        return await _say(hass, behavior, values, outcome, "no_area", say.NO_AREA, {"error": outcome.error})
    preferred = {"preferred_area_id": satellite.area_id} if satellite.area_id else {}
    calls = [{**call.slots, **target, **preferred} for target in targets]
    outcome.sequence = [{"intent": call.name, "slots": c} for c in calls]
    if outcome.dry_run:
        return await _say(hass, behavior, values, outcome, "done", "")
    started = time.perf_counter()
    results: list[dict[str, Any]] = []
    language = languages.current().code
    seen = {k: v for k, v in values.items() if k not in PRIVATE}
    try:
        for slots_of in calls:
            result = await hass.handle_intent(
                call.name, slots_of, seen, language, satellite.device_id, values["command"]["text"],
                exposed_only=not named,
            )
            if (
                call.anywhere and not (named or placed)
                and (result.get("error") or {}).get("reason") == "area"
            ):
                # Nothing of the kind here: the whole home's.
                anywhere = {k: v for k, v in slots_of.items() if k != "area"}
                outcome.sequence.append({"intent": call.name, "slots": anywhere})
                result = await hass.handle_intent(
                    call.name, anywhere, seen, language, satellite.device_id, values["command"]["text"],
                    exposed_only=not named,
                )
            results.append(result)
    except (HassError, ConnectionError, OSError, TimeoutError) as err:
        outcome.error = str(err)
        _LOGGER.error("Intent %s failed: %s", call.name, err)
        return await _say(hass, behavior, values, outcome, "failed", say.FAILED, {"error": str(err)})
    finally:
        outcome.timings["intent"] = time.perf_counter() - started
    outcome.response = {"intents": results}
    acted: list[dict[str, Any]] = []
    unavailable: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    speech = ""
    for result in results:
        if result.get("error"):
            errors.append(result["error"])
            continue
        response = result.get("response") or {}
        gone = {t.get("id") for t in result.get("unavailable") or []}
        for target in (response.get("data") or {}).get("success") or []:
            if target.get("type") == "entity":
                (unavailable if target.get("id") in gone else acted).append(target)
        speech = speech or str(((response.get("speech") or {}).get("plain") or {}).get("speech") or "")
    found = {
        "count": len(acted), "names": [t.get("name") or t.get("id") for t in acted], "ids": [t.get("id") for t in acted],
        "unavailable": [t.get("name") or t.get("id") for t in unavailable], "speech": speech,
    }
    if acted:
        return await _say(hass, behavior, values, outcome, "done", speech, found)
    if unavailable:
        return await _say(hass, behavior, values, outcome, "unavailable", say.unavailable(where), found)
    reasons = {e.get("reason") for e in errors if e.get("code") == "no_match"}
    outcome.error = "; ".join(str(e.get("message") or e.get("reason") or e.get("code")) for e in errors) or None
    if "assistant" in reasons:
        return await _say(hass, behavior, values, outcome, "not_exposed", say.not_exposed(where), found)
    if "feature" in reasons:
        capability = next(iter(behavior.supports), None)
        catalog = getattr(hass, "catalog", None)
        sentence = say.cannot(_names([str(e) for e in slots["entity"]], catalog), capability) if named else say.none_can(where, capability)
        return await _say(hass, behavior, values, outcome, "not_supported", _capital(sentence), found)
    if reasons:
        return await _say(hass, behavior, values, outcome, "no_devices", say.nothing_there(where), found)
    if not errors and not outcome.error:
        # Done, on nothing that is an entity (a scene's area, say): as it says.
        return await _say(hass, behavior, values, outcome, "done", speech, found)
    _LOGGER.error("Intent %s failed: %s", call.name, outcome.error)
    return await _say(hass, behavior, values, outcome, "failed", say.FAILED, {**found, "error": outcome.error})


async def _run_timer(
    hass: HomeAssistant,
    behavior: Behavior,
    values: dict[str, Any],
    satellite: Satellite,
    outcome: Outcome,
    integration,
) -> Outcome:
    """Plan a timer operation against the satellite's running timers, carry
    it out (unless a dry run), and say how it went."""
    if integration is None or not integration.ready or not satellite.timer_list:
        outcome.error = "no timer list for this satellite"
        return await _say(hass, behavior, values, outcome, "failed", languages.speech().TIMERS_NEED_INTEGRATION, {"error": outcome.error})
    started = time.perf_counter()
    try:
        running = await integration.fresh_timers(satellite.key)
    except (HassError, ConnectionError, OSError, TimeoutError) as err:
        outcome.error = str(err)
        return await _say(hass, behavior, values, outcome, "failed", languages.speech().FAILED, {"error": str(err)})
    plan = timers.plan(behavior.timer, values["slots"], running)
    target = {"entity_id": satellite.timer_list}
    calls = ([(plan.service, plan.data)] if plan.service else []) + plan.calls
    outcome.sequence = [
        {"action": f"voice_timer_list.{service}", "target": target, "data": data}
        for service, data in calls
    ]
    outcome.timer = plan.to_json()
    values = {**values, "timer": plan.timer, "timers": plan.timers}
    if not outcome.dry_run:
        try:
            for service, data in calls:
                response = await hass.call_service(
                    "voice_timer_list",
                    service,
                    data,
                    target,
                    return_response=service == "create_timer",
                )
                if service == "create_timer" and plan.timer is not None:
                    created = (response or {}).get(satellite.timer_list) or {}
                    plan.timer["timer_id"] = created.get("timer_id")
        except (HassError, ConnectionError, OSError, TimeoutError) as err:
            outcome.error = str(err)
            _LOGGER.error("Timer command failed: %s", err)
            return await _say(hass, behavior, values, outcome, "failed", languages.speech().FAILED, {"error": str(err)})
    outcome.timings["timer"] = time.perf_counter() - started
    return await _say(hass, behavior, values, outcome, plan.outcome, plan.levels or plan.speech)


def _calendar_ready(calendar: alarms.AlarmCalendar | None) -> bool:
    """Alarms go on the calendar: the language keeps them there, and it is
    set up (else they are timers, as before)."""
    return alarms.words() is not None and calendar is not None and calendar.ready


async def _run_calendar(
    hass: HomeAssistant,
    behavior: Behavior,
    values: dict[str, Any],
    satellite: Satellite,
    outcome: Outcome,
    calendar: alarms.AlarmCalendar | None,
) -> Outcome:
    """Plan an alarm or reminder operation against the satellite's alarms and
    reminders to come, carry it out (unless a dry run), and say how it went."""
    say = alarms.words()
    ready = calendar is not None and calendar.ready

    async def unready() -> Outcome:
        outcome.error = calendar.status if calendar is not None else "no calendar"
        text = say.CALENDAR_NEEDED if say is not None else languages.speech().FAILED
        return await _say(hass, behavior, values, outcome, "failed", text, {"error": outcome.error})

    # Without the calendar, a reminder without a time still goes on the to-do list.
    if say is None or calendar is None or not ready and behavior.timer != "reminder":
        return await unready()
    started = time.perf_counter()
    slots = values["slots"]
    # A reminder without a time goes on the to-do list among the fixed slots.
    todo = next((e for e in slots.get("entity") or [] if str(e).startswith("todo.")), None)
    plan = alarms.plan(
        str(behavior.timer), slots, calendar.now(),
        calendar.upcoming("alarm", satellite.key), calendar.upcoming("reminder", satellite.key), todo=todo is not None,
    )
    if plan.create and not ready:
        return await unready()
    outcome.timer = plan.to_json()
    outcome.sequence = [
        *([{"action": "calendar.create_event", "target": {"entity_id": calendar.entity_id}, "data": outcome.timer["create"]}]
          if plan.create else []),
        *({"action": "calendar.delete_event", "target": {"entity_id": calendar.entity_id}, "data": {"uid": e.uid}}
          for e in plan.delete),
        *([{"action": "todo.add_item", "target": {"entity_id": todo}, "data": {"item": plan.todo}}] if plan.todo else []),
    ]
    if not outcome.dry_run:
        try:
            if plan.create:
                await calendar.create(plan.create["kind"], plan.create["summary"], plan.create["start"],
                                      plan.create["rrule"], satellite.key, satellite.name)
            for event in plan.delete:
                await calendar.delete(event)
            if plan.todo:
                await hass.call_service("todo", "add_item", {"item": plan.todo}, {"entity_id": todo})
        except (HassError, ConnectionError, OSError, TimeoutError) as err:
            outcome.error = str(err)
            _LOGGER.error("Calendar command failed: %s", err)
            return await _say(hass, behavior, values, outcome, "failed", languages.speech().FAILED, {"error": str(err)})
    outcome.timings["calendar"] = time.perf_counter() - started
    return await _say(hass, behavior, values, outcome, plan.outcome, plan.levels or plan.speech)


async def _run_music(
    hass: HomeAssistant,
    behavior: Behavior,
    values: dict[str, Any],
    satellite: Satellite,
    outcome: Outcome,
    music: music_ops.Music,
) -> Outcome:
    """Find the player (and, to play, the music), carry it out (unless a dry
    run), and say how it went."""
    operation = behavior.music
    assert operation is not None
    slots = values["slots"]
    query = slots.get("query")
    if operation == "play" and music_ops.generic(query):
        operation = "resume"  # "play some music": whatever was playing
    named, nearby = _music_players(hass, slots, satellite)
    own = satellite.media_player
    sent = music.sent_by_voice(satellite.key)
    started = time.perf_counter()
    trace: dict[str, Any] = {"operation": operation, "own": own, "named": named, "nearby": nearby, "sent": sent}
    outcome.music = trace
    # Moving needs every player: the music may be playing anywhere.
    everywhere = _all_music_players(hass) if operation == "move" else []
    try:
        players = await music.players(
            list(dict.fromkeys(([own] if own else []) + (named or []) + nearby + sent + everywhere))
        )
    except (HassError, ConnectionError, OSError, TimeoutError, ValueError) as err:
        outcome.error = str(err)
        return await _say(hass, behavior, values, outcome, "failed", languages.speech().FAILED, {"error": str(err)})
    trace["players"] = {k: p.as_dict() for k, p in players.items()}
    if operation == "move":
        return await _move_music(hass, behavior, values, satellite, outcome, music, players, own, named, nearby, sent, started)
    choosing = "play" if operation == "radio" and not music_ops.generic(query) else operation
    choice = music_ops.choose(choosing, players, own, named, nearby, music.paused_by_voice(satellite.key), sent)
    chosen = choice.players
    values = {
        **values,
        "player": chosen[0].as_dict() if chosen else None,
        "players": [p.as_dict() for p in chosen],
        "place": _players_place(chosen, satellite, getattr(hass, "catalog", None)) if chosen else values["place"],
    }
    if choice.outcome:
        return await _say(hass, behavior, values, outcome, choice.outcome, languages.speech().MUSIC_SAID[choice.outcome])

    ids = [p.entity_id for p in chosen]
    calls: list[tuple[str, str, dict]] = []
    try:
        if operation in music_ops.FIND:
            if operation == "radio" and music_ops.generic(query):
                # Like what plays here, even to play it in a room named.
                here = music_ops.choose("status", players, own, None, nearby, []).players if named else chosen
                item = await music.current(here[0].entity_id) if here else None
                if item is None:
                    return await _say(hass, behavior, values, outcome, "nothing_playing", languages.speech().NOTHING_PLAYING)
            elif music_ops.generic(query):
                return await _say(hass, behavior, values, outcome, "missing_query", languages.speech().MUSIC_SAID["missing_query"])
            else:
                item = await music.find(str(query), slots.get("media_type"), ids[0])
                trace["queries"] = [q.__dict__ for q in music_ops.queries(query, slots.get("media_type"))]
                if item is None:
                    return await _say(hass, behavior, values, outcome, "not_found", languages.speech().not_found(music_ops.clean(query)))
            trace["found"] = {**item.as_dict(), "score": round(item.score, 3)}
            values = {**values, "media": item.as_dict()}
            speech: str | dict[str, str] = music_ops.found_said(operation, item, chosen)
            calls = [("music_assistant", "play_media", _play_data(operation, item))]
            name = "done"
        else:
            controls, speech, name = music_ops.control(operation, slots, chosen)
            if name == "done" and not speech:
                speech = music_ops.control_said(operation, slots, chosen)
                if not values["place"].get("here"):
                    # Out of the satellite's area, it says where: "Volume at
                    # 40% on Kitchen Speaker."
                    speech = {**speech, "specific": speech["verbose"]}
            calls = [("media_player", service, data) for service, data in controls]
    except LookupError as err:
        outcome.error = str(err)
        return await _say(hass, behavior, values, outcome, "failed", languages.speech().FAILED, {"error": str(err)})
    except (HassError, ConnectionError, OSError, TimeoutError) as err:
        outcome.error = str(err)
        _LOGGER.error("Music command failed: %s", err)
        return await _say(hass, behavior, values, outcome, "failed", languages.speech().FAILED, {"error": str(err)})

    target = {"entity_id": ids}
    outcome.sequence = [{"action": f"{d}.{s}", "target": target, "data": data} for d, s, data in calls]
    if not outcome.dry_run:
        try:
            try:
                for domain, service, data in calls:
                    await hass.call_service(domain, service, data, target)
            except HassError as err:
                genre = (values.get("media") or {}) if operation in music_ops.FIND else {}
                if genre.get("media_type") != music_ops.GENRE:
                    raise
                # No genre by that name: the closest of anything by it.
                trace["genre"] = {"name": genre["name"], "error": str(err)}
                item = await music.instead(music_ops.Item(genre["name"], genre["uri"], music_ops.GENRE), ids[0])
                if item is None:
                    return await _say(hass, behavior, values, outcome, "not_found", languages.speech().not_found(genre["name"]))
                trace["found"] = {**item.as_dict(), "score": round(item.score, 3)}
                values = {**values, "media": item.as_dict()}
                speech = music_ops.found_said(operation, item, chosen)
                data = _play_data(operation, item)
                outcome.sequence.append({"action": "music_assistant.play_media", "target": target, "data": data})
                await hass.call_service("music_assistant", "play_media", data, target)
        except (HassError, ConnectionError, OSError, TimeoutError, LookupError) as err:
            outcome.error = str(err)
            _LOGGER.error("Music command failed: %s", err)
            return await _say(hass, behavior, values, outcome, "failed", languages.speech().FAILED, {"error": str(err)})
        if operation == "pause":
            music.remember_paused(satellite.key, ids)
        elif operation in ("resume", "play", "stop"):
            music.forget_paused(satellite.key)
        # Where the music is now: what "turn it up" follows when nothing
        # plays here. Played here, it is here again.
        if operation in ("play", "radio") and named:
            music.remember_sent(satellite.key, ids)
        elif operation == "play" and own in ids:
            music.forget_sent(satellite.key)
    outcome.timings["music"] = time.perf_counter() - started
    return await _say(hass, behavior, values, outcome, name, speech)


async def _move_music(
    hass: HomeAssistant,
    behavior: Behavior,
    values: dict[str, Any],
    satellite: Satellite,
    outcome: Outcome,
    music: music_ops.Music,
    players: dict[str, music_ops.Player],
    own: str | None,
    named: list[str] | None,
    nearby: list[str],
    sent: list[str],
    started: float,
) -> Outcome:
    """The music playing here to the player the command names, queue and
    all (Music Assistant's ``transfer_queue``)."""
    choice = music_ops.choose_move(players, own, named, nearby, sent)
    trace = outcome.music or {}
    if choice.source is not None:
        trace["source"] = choice.source.entity_id
        values = {**values, "source": choice.source.as_dict()}
    if choice.outcome:
        return await _say(hass, behavior, values, outcome, choice.outcome, languages.speech().MOVE_SAID[choice.outcome])
    source, target = choice.source, choice.players[0]
    assert source is not None
    catalog = getattr(hass, "catalog", None)
    to = "" if values["slots"].get("entity") else languages.speech().place(_where(values["slots"], satellite, catalog)[1], to=True)
    values = {
        **values,
        "player": target.as_dict(),
        "players": [target.as_dict()],
        "place": _players_place([target], satellite, catalog),
    }
    data = {"source_player": source.entity_id, "auto_play": True}
    target_ids = {"entity_id": [target.entity_id]}
    outcome.sequence = [{"action": "music_assistant.transfer_queue", "target": target_ids, "data": data}]
    if not outcome.dry_run:
        try:
            await hass.call_service("music_assistant", "transfer_queue", data, target_ids)
        except (HassError, ConnectionError, OSError, TimeoutError) as err:
            outcome.error = str(err)
            _LOGGER.error("Moving the music failed: %s", err)
            return await _say(hass, behavior, values, outcome, "failed", languages.speech().FAILED, {"error": str(err)})
        music.forget_paused(satellite.key)
        if target.entity_id == own:
            music.forget_sent(satellite.key)
        else:
            music.remember_sent(satellite.key, [target.entity_id])
    outcome.timings["music"] = time.perf_counter() - started
    return await _say(hass, behavior, values, outcome, "done", music_ops.moved_said(source, target, to))


def _play_data(operation: str, item: music_ops.Item) -> dict[str, Any]:
    """``music_assistant.play_media``'s data for playing (or queueing, or
    playing music like) what was found."""
    data: dict[str, Any] = {"media_id": item.uri, "media_type": item.media_type}
    if operation == "add":
        data["enqueue"] = "add"
    elif operation == "radio":
        data["radio_mode"] = True
    return data


def _all_music_players(hass: HomeAssistant) -> list[str]:
    catalog = getattr(hass, "catalog", None)
    return [p.entity_id for p in catalog.music_players()] if catalog is not None else []


def _players_place(players: list[music_ops.Player], satellite: Satellite, catalog: Any) -> dict[str, Any]:
    """Where music acts: the players' areas (the satellite's own player is
    where it is)."""
    areas = []
    for player in players:
        entity = catalog.entities.get(player.entity_id) if catalog is not None else None
        if player.entity_id == satellite.media_player:
            areas.append(satellite.area_id)
        else:
            areas.append(entity.area_id if entity is not None else None)
    here = bool(areas) and all(a is not None and a == satellite.area_id for a in areas)
    known = [a for a in dict.fromkeys(areas) if a]
    said = place({"area": known}, satellite, catalog)["said"] if known else ""
    return {"here": here, "said": said, "areas": known}


def _music_players(
    hass: HomeAssistant, slots: dict[str, Any], satellite: Satellite
) -> tuple[list[str] | None, list[str]]:
    """The players a command names (entities, or Music Assistant's in the
    areas and floors it names; None when it names none), and Music
    Assistant's others in the satellite's area."""
    catalog = getattr(hass, "catalog", None)
    players = catalog.music_players() if catalog is not None else []
    named: list[str] | None = None
    if slots.get("entity") or slots.get("area") or slots.get("floor"):
        named = [str(e) for e in slots.get("entity") or [] if str(e).startswith("media_player.")]
        areas = set(slots.get("area") or [])
        floors = set(slots.get("floor") or [])
        if floors and catalog is not None:
            areas |= {a.area_id for a in catalog.areas.values() if a.floor_id in floors}
        named += [p.entity_id for p in players if areas and p.area_id in areas and p.entity_id not in named]
    nearby = [
        p.entity_id
        for p in players
        if satellite.area_id and p.area_id == satellite.area_id and p.entity_id != satellite.media_player
    ]
    return named, nearby


def level_for(behavior: Behavior, here: bool, elsewhere: str = BEEP_ELSEWHERE) -> tuple[str, str]:
    """The verbosity a done command says at, and why."""
    level = behavior.level
    why = "the command's" if behavior.verbosity else "the default for what it does"
    if level in QUIET and behavior.question:
        return "brief", "a question always answers"
    if level == "beep" and not here and elsewhere in SPOKEN:
        return elsewhere, "it acted out of the satellite's area"
    return level, why


async def _say(
    hass: HomeAssistant,
    behavior: Behavior,
    values: dict[str, Any],
    outcome: Outcome,
    name: str,
    default: str | dict[str, str],
    result: dict[str, Any] | None = None,
) -> Outcome:
    """What to say for how it went (``name``): the command's response for
    that outcome, rendered, or else ``default`` -- when done, at the level of
    verbosity (``default`` may be a sentence for each). Then tell
    automations."""
    outcome.name = name
    level = None
    if name == "done":
        level, why = level_for(behavior, bool(values["place"]["here"]), outcome.elsewhere)
        outcome.verbosity = {"level": level, "why": why, "place": values["place"]}
    if isinstance(default, dict):
        default = closest_text(default, level or "specific") or ""
    template = behavior.response_for(name, level) if level not in QUIET else None
    if level in QUIET:
        outcome.speech = ""
        outcome.silent = level == "silent"
    elif template:
        started = time.perf_counter()
        try:
            rendered = await hass.render_template(
                template, {**values, "result": {"skipped": outcome.skipped, **(result or {}), "outcome": name, "speech": default}}
            )
            outcome.speech = _tidy(rendered)
        except (HassError, ConnectionError, OSError, TimeoutError) as err:
            outcome.error = outcome.error or f"response template: {err}"
            _LOGGER.error("Could not render the response: %s", err)
            outcome.speech = languages.speech().FAILED
        finally:
            outcome.timings["render"] = time.perf_counter() - started
    else:
        outcome.speech = default
    if level == "verbose" and outcome.skipped_said and "skipped" not in (template or ""):
        outcome.speech = f"{outcome.speech} {outcome.skipped_said}".strip()
    if outcome.also_said and not outcome.silent:
        outcome.speech = f"{outcome.speech} {outcome.also_said}".strip()
    if not outcome.dry_run:
        try:
            event = {k: v for k, v in values.items() if k not in PRIVATE}
            await hass.fire_event(EVENT_TYPE, {**event, "outcome": name, "response": outcome.speech})
        except (HassError, ConnectionError, OSError, TimeoutError) as err:
            _LOGGER.warning("Could not fire %s: %s", EVENT_TYPE, err)
    return outcome


def _tidy(text: Any) -> str:
    """A rendered response without the gaps an empty variable leaves:
    "Turned on the lights ." is "Turned on the lights."."""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return re.sub(r"\s+([.,!?;:])", r"\1", text)
