"""The YAML configuration: intents, their keywords, names, and numbers."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, fields
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from . import morphology
from .numbers import DURATION_UNITS, on_grid, unit_words
from .template import TemplateError, covering, expand
from .text import sentence_key, spoken_words

NAME_TYPES = ("entity", "area", "floor", "media")
_SHARED_KEYS = {
    "entities": "entity",
    "areas": "area",
    "floors": "floor",
    "media": "media",
}

DEFAULT_FILLER = [
    "the",
    "a",
    "an",
    "please",
    "my",
    "can you",
    "could you",
    "would you",
]

# Words a name usually follows. The word right after one is where a name goes
# ("turn on the garage lights"); see Config.name_cues.
DEFAULT_NAME_CUES = ["the", "my"]

# Joins the parts of a required phrase that other words may separate:
# "turn ... on" also hears "turn the kitchen lights on".
SPLIT = " ... "


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    span_gate: float = 2.5
    """Largest acoustic cost per token (nats) at which a phrase counts as heard."""

    contest_margin: float = 2.0
    """A phrase is not heard when another phrase explains the same audio better
    by at least this many nats ("turn off" on audio that says "turn on")."""

    unexplained_important_cost: float = 4.0
    """Cost of a heard word that some other phrase recognized but this intent
    could not use: a name, a number, another intent's keyword."""

    unexplained_other_cost: float = 1.5
    """Cost of a heard word that nothing recognized."""

    max_unexplained_important: int = 1
    max_unexplained_other: int = 2

    places_must_fit: bool = True
    """An area or floor name heard and left unexplained rejects an intent that
    takes no name of that type, keywords or example sentence: "play music in
    the office" is not "play music" here. The same for a device the sentence
    ends on as the place ("play jazz on the Mark 2"), swallowed by free text
    of an intent that cannot take it; one that can stops its free text
    before it. Off, it is one more unexplained word."""

    lead_in: float = 0.35
    """Seconds. An unrecognized first word that ends this early is taken for
    the tail of the wake word ("So", "We" from "...Nabu") and ignored."""

    max_cost: float = 10.0
    """Global rejection threshold: an intent costing more than this is not a match."""

    ambiguity_margin: float = 1.0
    """Matches within this cost of the best are reported as ambiguous with it."""

    wildcard_cost: float = 0.5
    """Cost per word of free text taken by an intent's wildcard ("the pizza
    timer"): below an unexplained word, so a name is worth taking, but above a
    keyword, name or number, which explain their words first."""

    example_max_cost: float = 10.0
    """An intent's example sentence, searched for in the CTC evidence when no
    keyword intent matched, matches at no more than this cost: the sentence's
    acoustic cost plus the words it leaves unexplained."""

    example_margin: float = 2.0
    """...and only when every other intent's examples cost at least this much
    more. Otherwise the TDT transcript decides."""

    embedding_margin: float = 0.08
    """Examples said in other words: the intent whose example is most similar
    to a transcript (cosine, by embedding) matches when it is at least this
    much more similar than the next intent's examples and every built-in
    everyday request."""

    embedding_distractor_cutoff: float = 0.85
    """Built-in everyday requests at least this similar to one of the
    config's examples are dropped, so they don't stand in for that intent."""

    search: str = "live"
    """``live`` visits only states that could still end within ``span_gate``;
    ``full`` scores every phrase everywhere, so rejection reasons can quote
    how close even a far-off phrase came. Both find the same hits."""

    examples_first: bool = False
    """Search the CTC evidence for the example sentences on every command,
    not only when no keyword intent matched, and let a sentence that matches
    (within ``example_max_cost`` and ``example_margin``) win over keyword
    matches. For examples that must always match when said word for word."""

    examples_whole: bool = False
    """An example sentence found in the CTC evidence matches only when it is
    the whole of what was said: no heard word left unexplained (but a lead-in,
    the wake word's tail). Else "pause" is found in "pause the timer", and --
    with ``examples_first`` -- wins over the timer's keywords."""

    embedding_accept: bool = True
    """Accept an example intent whose example is closest in meaning to the
    transcript (``embedding_margin``). When false, the embedding is only used
    to name the closest intent (``Recognizer.closest_by_meaning``), never to
    match one."""


@dataclass(frozen=True)
class NameEntry:
    type: str
    id: str
    name: str
    aliases: tuple[str, ...] = ()

    @property
    def phrases(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((self.name, *self.aliases)))


@dataclass(frozen=True)
class NameRequirement:
    count: int = 0
    ids: frozenset[str] | None = None
    """Subset of the shared list this intent may target (all when None)."""

    items: dict[str, NameEntry] | None = None
    """This intent's own list, replacing the shared one."""

    minimum: int | None = None
    """The fewest names: none for "mute" as well as "mute the TV". ``count``
    (exactly) when not given."""

    domains: frozenset[str] | None = None
    """Only shared entities whose id is in one of these domains (``lock`` for
    ``lock.front_door``): a verb names what it acts on."""

    @property
    def least(self) -> int:
        return self.count if self.minimum is None else self.minimum


@dataclass(frozen=True)
class PriorRule:
    cost: float
    when: frozenset[str] = frozenset()
    multiple_of: Decimal | None = None
    values: frozenset[Decimal] | None = None
    minimum: Decimal | None = None
    maximum: Decimal | None = None

    def matches(self, value: Decimal, phrases: frozenset[str]) -> bool:
        if self.when and not self.when & phrases:
            return False
        if self.multiple_of is not None and value % self.multiple_of != 0:
            return False
        if self.values is not None and value not in self.values:
            return False
        if self.minimum is not None and value < self.minimum:
            return False
        if self.maximum is not None and value > self.maximum:
            return False
        return True


@dataclass(frozen=True)
class NumberSpec:
    minimum: Decimal
    maximum: Decimal
    step: Decimal = Decimal(1)
    prior: tuple[PriorRule, ...] = ()
    units: tuple[str, ...] = ()
    """A duration's units. Each number is said with one ("ten minutes", or "a
    minute and a half" as one span that counts as both), and is reported by
    it: ``{"minutes": 10}`` rather than ``{"number": 10}``."""

    optional: bool = False
    """The intent also matches without a number: "how long is left on my
    timer" as well as "...on my five minute timer"."""

    parts: int = 1
    """Up to this many numbers, each with its own unit, largest unit first:
    "one hour five minutes and thirty seconds". The range and prior apply to
    each part."""

    def accepts(self, value: Decimal) -> bool:
        return on_grid(value, self.minimum, self.maximum, self.step)

    def prior_cost(self, value: Decimal, phrases: frozenset[str]) -> float:
        """Cost of the first rule that matches; 0 when none does."""
        for rule in self.prior:
            if rule.matches(value, phrases):
                return rule.cost
        return 0.0

    def describe(self) -> str:
        step = "" if self.step == 1 else f" step {_fmt(self.step)}"
        return f"{_fmt(self.minimum)}-{_fmt(self.maximum)}{step}"


@dataclass(frozen=True)
class Wildcard:
    """Free text: 1 to ``max_words`` words of the transcript, right before one
    of ``before`` ("the pizza timer") or right after one of ``after`` ("a
    timer named pizza"). The slot is the words as heard."""

    slot: str
    before: tuple[str, ...] = ()
    after: tuple[str, ...] = ()
    max_words: int = 3
    required: bool = False
    """The intent matches only with the text: "add milk to my shopping list",
    not "add to my shopping list"."""

    bounded: bool = False
    """The text needs both edges: right after an ``after`` keyword and right
    before a ``before`` keyword ("add <milk> to the shopping list"), fillers
    allowed between. Fillers and name cues may then be inside the text ("add
    a bag of rice to...") but not at its edges."""

    to_end: bool = False
    """The text runs from right after an ``after`` keyword to the last word
    ("remind me to <take out the trash>"). Fillers may be inside it, as for
    ``bounded``."""

    exclude_inside: bool = False
    """The intent's excluded keywords rule out text that holds them, as well
    as the words around it: Spanish "pon" plays music but also sets timers,
    so "pon un temporizador" is no song called "temporizador"."""


@dataclass(frozen=True)
class ListSlot:
    """A keyword group whose alternatives carry values: "set the lights to
    (red | crimson | blue)" reports ``{"color": "red"}``. The group is one of
    the intent's required groups, or an optional one."""

    slot: str
    values: tuple[tuple[Any, tuple[str, ...]], ...]
    """(value, phrases that say it)."""

    optional: bool = False

    @property
    def phrases(self) -> dict[str, Any]:
        return {phrase: value for value, phrases in self.values for phrase in phrases}


@dataclass(frozen=True)
class Intent:
    name: str
    require: tuple[tuple[str, ...], ...] = ()
    """Groups of alternatives; every group must be heard (any one alternative).
    An alternative may be split ("turn ... on"): its parts in order, with
    anything between them."""

    optional: tuple[tuple[str, ...], ...] = ()
    exclude: tuple[str, ...] = ()
    names: dict[str, NameRequirement] = field(default_factory=dict)
    number: NumberSpec | None = None
    wildcard: Wildcard | None = None
    examples: tuple[str, ...] = ()
    """Sentences matched word for word, in the CTC evidence and the TDT
    transcript: every one the templates as written stand for."""

    description: str = ""
    """What the intent does, as a verb phrase ("set a timer"). Used to say
    what a command that almost matched looks like it was trying to do."""

    lists: tuple[ListSlot, ...] = ()
    """Value lists; each is also one of ``require`` (or ``optional``)."""

    templates: tuple[str, ...] = ()
    """The examples as written (adapt_ctc.template: ``(a|b)``, ``[a]``)."""

    meaning_examples: tuple[str, ...] = ()
    """A few of ``examples`` that say every alternative of a template at least
    once (template.covering): what a sentence is compared with by meaning.
    Empty: all of them."""

    @property
    def list_values(self) -> dict[str, tuple[str, Any]]:
        """Phrase -> (slot, value) for every list alternative."""
        return {
            phrase: (lst.slot, value)
            for lst in self.lists
            for value, phrases in lst.values
            for phrase in phrases
        }

    @property
    def keywords(self) -> bool:
        """Whether the intent is matched on keywords; one with only examples
        is not."""
        return bool(self.require or self.names or self.number is not None)

    def name_count(self, name_type: str) -> int:
        """The most names of this type the intent takes."""
        requirement = self.names.get(name_type)
        return requirement.count if requirement else 0

    def name_minimum(self, name_type: str) -> int:
        requirement = self.names.get(name_type)
        return requirement.least if requirement else 0


@dataclass(frozen=True)
class WakeConfig:
    """Second-stage wake word verification on the call's first seconds."""

    phrases: tuple[str, ...]
    """Every way the model writes the wake word ("okay nabu", "okay naboo")."""

    confusables: tuple[str, ...] = ()
    """Phrases the device's detector also fires on; the wake word must beat them."""

    window: float = 1.5
    """Seconds of audio verified: the pre-roll, which ends at the detection."""

    scoring: str = "sum"
    """``sum``: evidence summed over every alignment and spelling (the CTC
    forward score), with the wake word's spellings pooled. ``best``: the single
    best path, as the command recognizer scores phrases."""

    min_margin: float = -0.5
    """Reject when the strongest confusable has more evidence than the wake
    phrase by more than this many nats."""

    max_cost_per_token: float | None = None
    """Optionally also require the wake phrase's best path to fit this well
    (nats per piece). Off by default: on the recordings so far it rejects a
    genuine wake and catches no false one the margin misses."""


@dataclass(frozen=True)
class Contrast:
    """Phrases heard only where they explain the audio better than every one
    of ``against`` by ``margin`` nats: Ukrainian's "вимкни" (off) on audio
    of "ввімкни" (on) can score better than "ввімкни" itself, and turning
    off what was to be turned on is worse than doing nothing."""

    phrases: tuple[str, ...]
    against: tuple[str, ...]
    margin: float = 10.0


@dataclass(frozen=True)
class Config:
    language: str
    settings: Settings
    filler: tuple[str, ...]
    detect_numbers: NumberSpec | None
    names: dict[str, dict[str, NameEntry]]
    intents: dict[str, Intent]
    wake: WakeConfig | None = None
    name_cues: tuple[str, ...] = ()
    """Words a name follows. The word after one, when nothing recognized it or
    only as a name, rejects any intent that leaves it unexplained: a name the
    intent cannot take, or one the config does not have."""

    lists: dict[str, tuple[tuple[Any, tuple[str, ...]], ...]] = field(default_factory=dict)
    """Shared value lists, by name, that intents' ``lists`` may refer to."""

    compounds: tuple[str, ...] = ()
    """Keywords that may end a compound after an area or floor name, as one
    word: "Küchenlicht" is the light in the kitchen (``compounds: [licht]``)."""

    name_endings: tuple[str, ...] = ()
    """Endings a word of a name may be heard with, added or in place of one
    it has (morphology.word_forms): the language's (morphology.ENDINGS)
    unless the config gives its own (``name_endings``)."""

    contrasts: tuple[Contrast, ...] = ()
    """Keywords heard only when they clearly beat their opposites (``Contrast``)."""


class _Loader(yaml.SafeLoader):
    """YAML 1.2 booleans: only true/false.

    YAML 1.1 reads on/off/yes/no as booleans, so ``exclude: [off]`` would
    exclude the phrase "false". In a file full of spoken phrases, those are
    words.
    """


_Loader.yaml_implicit_resolvers = {
    first: [(tag, regexp) for tag, regexp in resolvers if tag != "tag:yaml.org,2002:bool"]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_Loader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
    list("tTfF"),
)


def load_config(path: str | Path) -> Config:
    return parse_config(_read(Path(path), ()))


def _read(path: Path, including: tuple[Path, ...]) -> dict:
    """A config file with its ``include`` files merged in, in order, and then
    its own keys on top: an intent of the same name replaces the included one,
    names and settings merge per entry, and anything else is replaced."""
    path = path.resolve()
    if path in including:
        raise ConfigError(f"{path}: includes itself")
    with open(path, encoding="utf-8") as config_file:
        data = yaml.load(config_file, Loader=_Loader)  # noqa: S506 - a SafeLoader
    data = _mapping(data, str(path))
    merged: dict = {}
    for item in _list(data.get("include") or [], f"{path}: include"):
        merged = _merge(merged, _read(path.parent / str(item), (*including, path)))
    return _merge(merged, {key: value for key, value in data.items() if key != "include"})


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for key, value in over.items():
        if key in ("settings", "intents", "lists") and isinstance(out.get(key), dict) and isinstance(value, dict):
            out[key] = {**out[key], **value}
        elif key == "names" and isinstance(out.get(key), dict) and isinstance(value, dict):
            out[key] = {
                kind: {**(out[key].get(kind) or {}), **(value.get(kind) or {})}
                for kind in {**out[key], **value}
            }
        else:
            out[key] = value
    return out


def parse_config(data: Any) -> Config:
    data = _mapping(data, "config")
    _known(
        data,
        {
            "language", "settings", "filler", "name_cues", "numbers", "names", "intents",
            "wake", "lists", "name_endings", "compounds", "contrasts",
        },
        "config",
    )
    language = str(data.get("language", "en"))
    name_endings = morphology.endings(language)
    if "name_endings" in data:
        name_endings = tuple(
            dict.fromkeys(str(e).strip().lower() for e in _list(data["name_endings"] or [], "name_endings"))
        )
        if not all(e.isalpha() for e in name_endings):
            raise ConfigError("name_endings: each ending is letters only")

    settings = _settings(data.get("settings") or {})
    filler = tuple(
        _phrase(text, language, "filler")
        for text in _list(data.get("filler", DEFAULT_FILLER), "filler")
    )
    name_cues = tuple(
        dict.fromkeys(
            _phrase(text, language, "name_cues")
            for text in _list(data.get("name_cues", DEFAULT_NAME_CUES) or [], "name_cues")
        )
    )
    if any(" " in cue for cue in name_cues):
        raise ConfigError("name_cues: each cue must be a single word")

    numbers = _mapping(data.get("numbers") or {}, "numbers")
    _known(numbers, {"detect"}, "numbers")
    detect = _number_spec(
        numbers.get("detect") or {"min": 0, "max": 100}, "numbers.detect", language, False
    )

    names: dict[str, dict[str, NameEntry]] = {name_type: {} for name_type in NAME_TYPES}
    shared = _mapping(data.get("names") or {}, "names")
    for key, items in shared.items():
        name_type = _SHARED_KEYS.get(key)
        if name_type is None:
            raise ConfigError(f"names: unknown type {key!r} (use {sorted(_SHARED_KEYS)})")
        names[name_type] = _name_items(items, name_type, f"names.{key}")

    lists = {
        str(name): _list_values(values, language, f"lists.{name}")
        for name, values in _mapping(data.get("lists") or {}, "lists").items()
    }

    intents_data = _mapping(data.get("intents"), "intents")
    if not intents_data:
        raise ConfigError("intents: at least one intent is required")
    intents = {
        str(name): _intent(str(name), spec, language, names, lists)
        for name, spec in intents_data.items()
    }
    _check_examples(intents, language)
    keywords = {
        part
        for intent in intents.values()
        for phrase in {p for group in intent.require + intent.optional for p in group}
        for part in phrase_parts(phrase)
    }
    compounds = tuple(
        dict.fromkeys(_phrase(text, language, "compounds") for text in _list(data.get("compounds") or [], "compounds"))
    )
    for word in compounds:
        if " " in word:
            raise ConfigError(f"compounds: {word!r}: one word each")
        if word not in keywords:
            raise ConfigError(f"compounds: {word!r} is no intent's keyword")
    wake = _wake(data["wake"], language) if data.get("wake") else None
    contrasts = tuple(
        _contrast(spec, language, f"contrasts[{index}]")
        for index, spec in enumerate(_list(data.get("contrasts") or [], "contrasts"))
    )
    return Config(
        language, settings, filler, detect, names, intents, wake, name_cues, lists, compounds, name_endings,
        contrasts,
    )


def _contrast(spec: Any, language: str, where: str) -> Contrast:
    """``{phrases, against, margin}``."""
    spec = _mapping(spec, where)
    _known(spec, {"phrases", "against", "margin"}, where)
    sides = {
        key: tuple(dict.fromkeys(_phrase(p, language, f"{where}.{key}") for p in _list(spec.get(key) or [], f"{where}.{key}")))
        for key in ("phrases", "against")
    }
    if not sides["phrases"] or not sides["against"]:
        raise ConfigError(f"{where}: needs both 'phrases' and 'against'")
    if set(sides["phrases"]) & set(sides["against"]):
        raise ConfigError(f"{where}: a phrase can't be against itself")
    margin = spec.get("margin", Contrast.margin)
    if not isinstance(margin, (int, float)) or isinstance(margin, bool) or margin < 0:
        raise ConfigError(f"{where}.margin: expected a number of nats, 0 or more")
    return Contrast(sides["phrases"], sides["against"], float(margin))


def _wake(spec: Any, language: str) -> WakeConfig:
    spec = _mapping(spec, "wake")
    _known(
        spec, {"phrases", "confusables", "window", "scoring", "max_cost_per_token", "min_margin"}, "wake"
    )
    phrases = tuple(
        dict.fromkeys(_phrase(p, language, "wake.phrases") for p in _list(spec.get("phrases") or [], "wake.phrases"))
    )
    if not phrases:
        raise ConfigError("wake: needs at least one phrase")
    confusables = tuple(
        dict.fromkeys(
            _phrase(p, language, "wake.confusables")
            for p in _list(spec.get("confusables") or [], "wake.confusables")
        )
    )
    overlap = set(phrases) & set(confusables)
    if overlap:
        raise ConfigError(f"wake: {sorted(overlap)} are both phrases and confusables")
    values: dict[str, Any] = {}
    for key in ("window", "max_cost_per_token", "min_margin"):
        if key in spec:
            if key == "max_cost_per_token" and spec[key] is None:
                values[key] = None
                continue
            try:
                values[key] = float(spec[key])
            except (TypeError, ValueError) as err:
                raise ConfigError(f"wake.{key}: expected a number") from err
    if "scoring" in spec:
        if spec["scoring"] not in ("sum", "best"):
            raise ConfigError("wake.scoring: expected 'sum' or 'best'")
        values["scoring"] = spec["scoring"]
    return WakeConfig(phrases, confusables, **values)


def _intent(
    name: str,
    spec: Any,
    language: str,
    shared: dict[str, dict[str, NameEntry]],
    shared_lists: dict[str, tuple[tuple[Any, tuple[str, ...]], ...]] | None = None,
) -> Intent:
    where = f"intents.{name}"
    spec = _mapping(spec or {}, where)
    _known(
        spec,
        {
            "require", "optional", "exclude", "names", "number", "wildcard", "examples",
            "description", "lists",
        },
        where,
    )

    require = _groups(spec.get("require"), language, f"{where}.require", split=True)
    optional = _groups(spec.get("optional"), language, f"{where}.optional")
    lists = _intent_lists(spec.get("lists"), language, shared_lists or {}, f"{where}.lists")
    keywords = {phrase for group in require + optional for phrase in group}
    seen: dict[str, str] = {}
    for lst in lists:
        for phrase in lst.phrases:
            if phrase in keywords:
                raise ConfigError(
                    f"{where}.lists.{lst.slot}: {phrase!r} is also a keyword of this intent"
                )
            if phrase in seen:
                raise ConfigError(
                    f"{where}.lists.{lst.slot}: {phrase!r} is also in list {seen[phrase]!r}"
                )
            seen[phrase] = lst.slot
    require += tuple(tuple(lst.phrases) for lst in lists if not lst.optional)
    optional += tuple(tuple(lst.phrases) for lst in lists if lst.optional)
    exclude = tuple(
        phrase
        for group in _groups(spec.get("exclude"), language, f"{where}.exclude")
        for phrase in group
    )

    names: dict[str, NameRequirement] = {}
    for key, value in _mapping(spec.get("names") or {}, f"{where}.names").items():
        name_type = _SHARED_KEYS.get(key, key)
        if name_type not in NAME_TYPES:
            raise ConfigError(f"{where}.names: unknown type {key!r} (use {list(NAME_TYPES)})")
        names[name_type] = _name_requirement(
            value, name_type, shared[name_type], f"{where}.names.{key}"
        )

    number = None
    if spec.get("number") not in (None, False, "none"):
        number = _number_spec(spec["number"], f"{where}.number", language, True)
        keywords = {phrase for group in require + optional for phrase in group}
        for rule in number.prior:
            unknown = rule.when - keywords
            if unknown:
                raise ConfigError(
                    f"{where}.number.prior: 'when' phrases {sorted(unknown)} are not "
                    "required or optional keywords of this intent"
                )
        # A unit is a keyword by its name or its words ("minuten").
        unknown_units = sorted(
            unit for unit in number.units if not {unit, *unit_words(unit, language)} & keywords
        )
        if unknown_units:
            raise ConfigError(
                f"{where}.number.units: {unknown_units} are not required or optional "
                "keywords of this intent"
            )

    wildcard = None
    if spec.get("wildcard"):
        wildcard = _wildcard(spec["wildcard"], require + optional, language, f"{where}.wildcard")

    templates = tuple(
        dict.fromkeys(str(text).strip() for text in _list(spec.get("examples") or [], f"{where}.examples"))
    )
    sentences: list[str] = []
    meaning: list[str] = []
    for template in templates:
        try:
            said = [s for s in expand(template) if sentence_key(s, language)]
            meaning.extend(s for s in covering(template) if sentence_key(s, language))
        except TemplateError as err:
            raise ConfigError(f"{where}.examples: {template!r}: {err}") from err
        if not said:
            raise ConfigError(f"{where}.examples: {template!r} has no speakable words")
        sentences.extend(said)
    examples = tuple(dict.fromkeys(sentences))

    description = str(spec.get("description") or "").strip()
    intent = Intent(
        name, require, optional, exclude, names, number, wildcard, examples, description, lists,
        templates=templates, meaning_examples=tuple(dict.fromkeys(meaning)),
    )
    if set(seen) & set(exclude):
        raise ConfigError(f"{where}.lists: {sorted(set(seen) & set(exclude))} are also excluded")
    if examples and not intent.keywords:
        if optional or exclude:
            raise ConfigError(
                f"{where}: optional and excluded keywords need required ones; "
                "an intent with only examples takes none"
            )
        return intent
    if not (
        require
        or any(r.least for r in names.values())
        or (number is not None and not number.optional)
    ):
        raise ConfigError(f"{where}: needs a required keyword, name, number, or examples")
    return intent


def _check_examples(intents: dict[str, Intent], language: str) -> None:
    """An example sentence names one intent -- every sentence a template
    stands for."""
    owner: dict[str, tuple[str, str]] = {}
    for intent in intents.values():
        for written in intent.templates or intent.examples:
            for text in expand(written) if intent.templates else [written]:
                key = sentence_key(text, language)
                other = owner.setdefault(key, (intent.name, written))
                if other[0] != intent.name:
                    said = repr(text) if text == written else f"{text!r} (from {written!r})"
                    raise ConfigError(
                        f"intents.{intent.name}.examples: {said} is also an example "
                        f"of {other[0]} ({other[1]!r})"
                    )


def _wildcard(
    spec: Any, groups: tuple[tuple[str, ...], ...], language: str, where: str
) -> Wildcard:
    spec = _mapping(spec, where)
    _known(
        spec, {"slot", "before", "after", "max_words", "required", "bounded", "to_end", "exclude_inside"}, where
    )
    slot = str(spec.get("slot") or "")
    if not slot or slot == "number" or slot in NAME_TYPES:
        raise ConfigError(f"{where}.slot: needs a slot name other than 'number' and the name types")
    keywords = {phrase for group in groups for phrase in group}
    cues = {}
    for key in ("before", "after"):
        cues[key] = tuple(
            _phrase(p, language, f"{where}.{key}") for p in _list(spec.get(key) or [], f"{where}.{key}")
        )
        unknown = sorted(set(cues[key]) - keywords)
        if unknown:
            raise ConfigError(f"{where}.{key}: {unknown} are not required or optional keywords")
    if not (cues["before"] or cues["after"]):
        raise ConfigError(f"{where}: needs 'before' or 'after' keywords to place it")
    max_words = spec.get("max_words", 3)
    if not isinstance(max_words, int) or isinstance(max_words, bool) or max_words < 1:
        raise ConfigError(f"{where}.max_words: expected a positive integer")
    required = _flag(spec.get("required", False), f"{where}.required")
    bounded = _flag(spec.get("bounded", False), f"{where}.bounded")
    to_end = _flag(spec.get("to_end", False), f"{where}.to_end")
    if bounded and to_end:
        raise ConfigError(f"{where}: give either 'bounded' or 'to_end', not both")
    if bounded and not (cues["before"] and cues["after"]):
        raise ConfigError(f"{where}.bounded: needs both 'after' and 'before' keywords")
    if to_end and not cues["after"]:
        raise ConfigError(f"{where}.to_end: needs 'after' keywords")
    exclude_inside = _flag(spec.get("exclude_inside", False), f"{where}.exclude_inside")
    return Wildcard(slot, cues["before"], cues["after"], max_words, required, bounded, to_end, exclude_inside)


def _list_values(
    values: Any, language: str, where: str
) -> tuple[tuple[Any, tuple[str, ...]], ...]:
    """``{value: phrase | [phrases]}``, or ``[phrase, ...]`` where each phrase
    is its own value."""
    if isinstance(values, list):
        values = {value: value for value in values}
    values = _mapping(values, where)
    if not values:
        raise ConfigError(f"{where}: needs at least one value")
    out = []
    for value, phrases in values.items():
        if isinstance(phrases, (str, int, float)) and not isinstance(phrases, bool):
            phrases = [phrases]
        phrases = _list(phrases, f"{where}.{value}")
        if not phrases:
            raise ConfigError(f"{where}.{value}: needs at least one phrase")
        spoken = tuple(dict.fromkeys(_phrase(p, language, f"{where}.{value}") for p in phrases))
        out.append((value, spoken))
    owners: dict[str, Any] = {}
    for value, phrases in out:
        for phrase in phrases:
            if owners.setdefault(phrase, value) != value:
                raise ConfigError(f"{where}: {phrase!r} says both {owners[phrase]!r} and {value!r}")
    return tuple(out)


def _intent_lists(
    spec: Any,
    language: str,
    shared: dict[str, tuple[tuple[Any, tuple[str, ...]], ...]],
    where: str,
) -> tuple[ListSlot, ...]:
    """``{slot: list_name | {list: name} | {values: ...}, optional: bool}``."""
    out = []
    for slot, value in _mapping(spec or {}, where).items():
        slot = str(slot)
        if slot == "number" or slot in NAME_TYPES:
            raise ConfigError(f"{where}.{slot}: needs a slot name other than 'number' and the name types")
        if isinstance(value, str):
            value = {"list": value}
        value = _mapping(value, f"{where}.{slot}")
        _known(value, {"list", "values", "optional"}, f"{where}.{slot}")
        if ("list" in value) == ("values" in value):
            raise ConfigError(f"{where}.{slot}: give either 'list' or 'values'")
        if "list" in value:
            name = str(value["list"])
            if name not in shared:
                raise ConfigError(f"{where}.{slot}: no shared list {name!r}")
            values = shared[name]
        else:
            values = _list_values(value["values"], language, f"{where}.{slot}.values")
        optional = _flag(value.get("optional", False), f"{where}.{slot}.optional")
        out.append(ListSlot(slot, values, optional))
    return tuple(out)


def _name_requirement(
    value: Any, name_type: str, shared: dict[str, NameEntry], where: str
) -> NameRequirement:
    if isinstance(value, int) and not isinstance(value, bool):
        return NameRequirement(count=_count(value, where))
    value = _mapping(value, where)
    _known(value, {"count", "min", "optional", "ids", "domains", "items"}, where)
    count = _count(value.get("count", 0), where)
    minimum = None
    if "min" in value and "optional" in value:
        raise ConfigError(f"{where}: give either 'min' or 'optional', not both")
    if "min" in value:
        minimum = _count(value["min"], f"{where}.min")
        if minimum > count:
            raise ConfigError(f"{where}.min: more than count ({count})")
    elif _flag(value.get("optional", False), f"{where}.optional"):
        minimum = 0
    ids = items = None
    if "ids" in value and "items" in value:
        raise ConfigError(f"{where}: give either 'ids' or 'items', not both")
    if "ids" in value:
        ids = frozenset(str(i) for i in _list(value["ids"], f"{where}.ids"))
        unknown = ids - set(shared)
        if unknown:
            raise ConfigError(f"{where}.ids: not in the shared list: {sorted(unknown)}")
    if "items" in value:
        items = _name_items(value["items"], name_type, f"{where}.items")
    domains = None
    if "domains" in value:
        if name_type != "entity" or items is not None:
            raise ConfigError(f"{where}.domains: only for shared entities")
        domains = frozenset(str(d) for d in _list(value["domains"], f"{where}.domains"))
        if ids is not None:
            outside = sorted(i for i in ids if i.split(".", 1)[0] not in domains)
            if outside:
                raise ConfigError(f"{where}.ids: {outside} are outside domains {sorted(domains)}")
    return NameRequirement(count, ids, items, minimum, domains)


def _name_items(items: Any, name_type: str, where: str) -> dict[str, NameEntry]:
    out: dict[str, NameEntry] = {}
    for item_id, spec in _mapping(items or {}, where).items():
        item_id = str(item_id)
        if isinstance(spec, str):
            out[item_id] = NameEntry(name_type, item_id, spec)
            continue
        spec = _mapping(spec, f"{where}.{item_id}")
        _known(spec, {"name", "aliases"}, f"{where}.{item_id}")
        aliases = tuple(str(a) for a in _list(spec.get("aliases") or [], f"{where}.{item_id}.aliases"))
        name = spec.get("name") or (aliases[0] if aliases else None)
        if not name:
            raise ConfigError(f"{where}.{item_id}: needs a name")
        out[item_id] = NameEntry(name_type, item_id, str(name), aliases)
    return out


def _number_spec(spec: Any, where: str, language: str, allow_prior: bool) -> NumberSpec:
    spec = _mapping(spec, where)
    _known(
        spec,
        {"min", "max", "step"}
        | ({"prior", "units", "optional", "parts"} if allow_prior else set()),
        where,
    )
    if "min" not in spec or "max" not in spec:
        raise ConfigError(f"{where}: needs 'min' and 'max'")
    minimum = _decimal(spec["min"], f"{where}.min")
    maximum = _decimal(spec["max"], f"{where}.max")
    step = _decimal(spec.get("step", 1), f"{where}.step")
    if step <= 0 or maximum < minimum:
        raise ConfigError(f"{where}: needs min <= max and a positive step")
    if (maximum - minimum) / step > 100_000:
        raise ConfigError(f"{where}: range is too large to enumerate")
    rules = []
    for index, rule in enumerate(_list(spec.get("prior") or [], f"{where}.prior")):
        rules.append(_prior_rule(rule, language, f"{where}.prior[{index}]"))
    units = tuple(
        dict.fromkeys(
            _phrase(unit, language, f"{where}.units")
            for unit in _list(spec.get("units") or [], f"{where}.units")
        )
    )
    optional = _flag(spec.get("optional", False), f"{where}.optional")
    parts = spec.get("parts", 1)
    if not isinstance(parts, int) or isinstance(parts, bool) or parts < 1:
        raise ConfigError(f"{where}.parts: expected a positive integer")
    if parts > 1:
        unknown = sorted(set(units) - set(DURATION_UNITS))
        if not units or unknown:
            raise ConfigError(
                f"{where}.parts: a compound number needs units, each one of "
                f"{sorted(DURATION_UNITS)}" + (f" (not {unknown})" if unknown else "")
            )
    return NumberSpec(minimum, maximum, step, tuple(rules), units, optional, parts)


def _prior_rule(rule: Any, language: str, where: str) -> PriorRule:
    rule = _mapping(rule, where)
    _known(rule, {"cost", "when", "multiple_of", "values", "min", "max"}, where)
    if "cost" not in rule:
        raise ConfigError(f"{where}: needs a 'cost'")
    when = rule.get("when") or []
    if isinstance(when, str):
        when = [when]
    return PriorRule(
        cost=float(rule["cost"]),
        when=frozenset(_phrase(w, language, f"{where}.when") for w in when),
        multiple_of=_decimal(rule["multiple_of"], f"{where}.multiple_of")
        if "multiple_of" in rule
        else None,
        values=frozenset(_decimal(v, f"{where}.values") for v in _list(rule["values"], where))
        if "values" in rule
        else None,
        minimum=_decimal(rule["min"], f"{where}.min") if "min" in rule else None,
        maximum=_decimal(rule["max"], f"{where}.max") if "max" in rule else None,
    )


def _groups(
    value: Any, language: str, where: str, split: bool = False
) -> tuple[tuple[str, ...], ...]:
    groups = []
    for index, group in enumerate(_list(value or [], where)):
        alternatives = group if isinstance(group, list) else [group]
        if not alternatives:
            raise ConfigError(f"{where}[{index}]: empty group")
        phrases = tuple(
            dict.fromkeys(
                _split_phrase(a, language, f"{where}[{index}]", split) for a in alternatives
            )
        )
        groups.append(phrases)
    return tuple(groups)


def _split_phrase(text: Any, language: str, where: str, allowed: bool) -> str:
    """A phrase whose parts may be separated by other words: "turn ... on"."""
    if not isinstance(text, str) or "..." not in text:
        return _phrase(text, language, where)
    if not allowed:
        raise ConfigError(f"{where}: only required phrases may be split with '...'")
    parts = [part.strip() for part in text.split("...")]
    if len(parts) < 2 or not all(parts):
        raise ConfigError(f"{where}: {text!r} needs words on both sides of '...'")
    return SPLIT.join(_phrase(part, language, where) for part in parts)


def phrase_parts(phrase: str) -> list[str]:
    """The parts of a split phrase ("turn ... on" -> turn, on); one part otherwise."""
    return phrase.split(SPLIT)


def _phrase(text: Any, language: str, where: str) -> str:
    if isinstance(text, bool) or not isinstance(text, (str, int, float)):
        raise ConfigError(f"{where}: expected a phrase, got {text!r} (quote it)")
    spellings = spoken_words(str(text), language)
    if not spellings:
        raise ConfigError(f"{where}: phrase {text!r} has no speakable words")
    return " ".join(spellings[0])


def _settings(data: Any) -> Settings:
    data = _mapping(data, "settings")
    allowed = {f.name: f for f in fields(Settings)}
    _known(data, set(allowed), "settings")
    values: dict[str, Any] = {}
    for key, value in data.items():
        if key == "search":
            if value not in ("live", "full"):
                raise ConfigError("settings.search: expected 'live' or 'full'")
            values[key] = value
            continue
        if allowed[key].type in ("bool", bool):
            values[key] = _flag(value, f"settings.{key}")
            continue
        kind = int if allowed[key].type in ("int", int) else float
        try:
            values[key] = kind(value)
        except (TypeError, ValueError) as err:
            raise ConfigError(f"settings.{key}: expected a number") from err
    return Settings(**values)


def _decimal(value: Any, where: str) -> Decimal:
    try:
        return Decimal(str(value))
    except InvalidOperation as err:
        raise ConfigError(f"{where}: expected a number, got {value!r}") from err


def _count(value: Any, where: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ConfigError(f"{where}: count must be a non-negative integer")
    return value


def _flag(value: Any, where: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{where}: expected true or false")
    return value


def _mapping(value: Any, where: str) -> dict:
    if not isinstance(value, dict):
        raise ConfigError(f"{where}: expected a mapping")
    return value


def _list(value: Any, where: str) -> list:
    if not isinstance(value, list):
        raise ConfigError(f"{where}: expected a list")
    return value


def _known(data: dict, allowed: set[str], where: str) -> None:
    unknown = set(data) - allowed
    if unknown:
        raise ConfigError(f"{where}: unknown key(s) {sorted(unknown)}")


def _fmt(value: Decimal) -> str:
    return format(value.normalize(), "f")
