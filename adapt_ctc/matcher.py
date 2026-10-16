"""Intents matched against the spans mined from the audio.

For each intent, the search picks the set of non-overlapping spans that best
explains the audio while meeting the intent's constraints: every required
keyword group heard, exactly the declared number of each kind of name, and a
number in range if (and only if) the intent takes one. A parse costs

* the acoustic cost of each span it uses, plus
* a number prior, for values the intent considers unlikely, plus
* the cost of every heard word it leaves unexplained -- more for a word some
  other phrase recognized (a name, a number, another intent's keyword) than
  for one nothing recognized.

Excluded keywords veto a parse only when they land on audio the parse leaves
unexplained: a phrase the parse already explains better is not "heard".
Intents are ranked on that cost, so they are comparable with one another and
with a global rejection threshold.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any

import numpy as np

from .acoustic import CtcModel, Vocabulary
from .config import NAME_TYPES, Config, Intent, Wildcard, phrase_parts
from .embedding import distractors
from .graph import CompiledGraph, Meaning, PhraseGraph
from .numbers import DURATION_UNITS, spoken_forms, to_python, unit_forms, value_grid
from .spotter import Hit, Spotting, spot
from .text import name_units, sentence_key, spoken_words

_INF = float("inf")
PLACE_TYPES = ("area", "floor")
"""Names that say where: heard, an intent must take them (``places_must_fit``)."""
PLACE_WORDS = ("in", "on", "at", "to")
"""What says a place follows: "play jazz *in the* kitchen"."""
BARE_PLACES = ("upstairs", "downstairs")
"""Places said without one: "play jazz upstairs"."""


@dataclass
class SpanUse:
    role: str
    """``require``, ``optional``, ``filler``, ``name`` or ``number``."""

    hit: Hit
    meaning: Meaning
    group: int | None = None
    part: tuple[int, int, int] | None = None
    """For a part of a split phrase ("turn" of "turn ... on"): which split
    phrase of the intent, which part, and how many parts it has."""


@dataclass
class UnexplainedWord:
    text: str
    start: int
    end: int
    important: bool
    heard_as: list[str] = field(default_factory=list)
    """What recognized phrases overlap it, e.g. ``number 50``."""

    name_cue: str | None = None
    """The name cue it follows ("the"), when it stands where a name goes: a
    name this intent cannot take, or one the config does not have."""


@dataclass
class IntentResult:
    intent: str
    matched: bool
    cost: float = _INF
    acoustic: float = 0.0
    prior: float = 0.0
    unexplained_cost: float = 0.0
    spans: list[SpanUse] = field(default_factory=list)
    unexplained: list[UnexplainedWord] = field(default_factory=list)
    slots: dict = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    """Why the intent was rejected."""

    notes: list[str] = field(default_factory=list)
    ambiguous_with: list[str] = field(default_factory=list)
    example: str | None = None
    """The example sentence matched, when the intent was matched on its
    examples rather than its keywords."""

    example_source: str | None = None
    """Where the example was found: ``CTC`` (searched for in the evidence,
    with spans and a cost), ``TDT`` (the transducer's transcript, word for
    word) or ``embedding`` (a transcript close in meaning)."""

    similarity: float | None = None
    """For ``embedding``: the cosine similarity of the closest transcript
    and example."""

    missing: list[dict] = field(default_factory=list)
    """What a rejected intent lacked, for saying so: ``{"kind": "require",
    "phrases": [...]}`` (with ``"slot"`` for a value list), ``{"kind":
    "name", "type": "area", "count": 1, "heard": [...]}``, ``{"kind":
    "number", "range": "0-100", "heard": [...]}``, ``{"kind": "text",
    "slot": "item"}``, ``{"kind": "unexplained", "words": [...]}``,
    ``{"kind": "excluded", "phrase": ...}`` or ``{"kind": "cost"}``."""

    heard_groups: int = 0
    """How many of the intent's required groups were heard: how close a
    rejected intent came."""


@dataclass
class Recognition:
    transcript: str
    duration: float
    spotting: Spotting
    matches: list[IntentResult]
    """Matched intents, best first."""

    rejected: list[IntentResult]
    timings: dict[str, float] = field(default_factory=dict)
    lead_in: str | None = None
    """An unrecognized first word ignored as the tail of the wake word."""

    tdt_transcript: str | None = None
    """The TDT head's transcript, when example sentences were tried."""

    @property
    def best(self) -> IntentResult | None:
        return self.matches[0] if self.matches else None


@dataclass(frozen=True)
class _Item:
    use: SpanUse
    cost: float
    cond_bit: int = 0
    groups: int = 0
    """Required groups it also meets: a number said with its unit."""

    led: bool = False
    """Free text that follows an article, a name cue or a unit ("a pizza
    timer", "a twenty minute rice timer"): only that may go before a keyword."""

    @property
    def start(self) -> int:
        return self.use.hit.start

    @property
    def end(self) -> int:
        return self.use.hit.end


# Parse state: (required groups heard, chosen name ids per type, number, prior
# conditions, split phrases begun: sorted (group, (split phrase, next part))).
# A compound number's parts so far, (unit, value, the phrase that said the
# unit), and a number still waiting for its unit.
_Parts = tuple[tuple[tuple[str, Decimal, str], ...], Decimal | None]
# The wildcard's progress: "" none yet, "after" right after one of its `after`
# keywords, "waiting" taken and needing a `before` keyword next, "done".
_State = tuple[
    int,
    tuple[tuple[str, ...], ...],
    Decimal | _Parts | None,
    int,
    tuple[tuple[int, tuple[int, int]], ...],
    str,
]
_INITIAL: _State = (0, tuple(() for _ in NAME_TYPES), None, 0, (), "")


class Recognizer:
    def __init__(
        self,
        config: Config,
        model: CtcModel | None = None,
        vocab: Vocabulary | None = None,
        transcriber=None,
        embedder=None,
    ) -> None:
        if model is None and vocab is None:
            raise ValueError("Need an acoustic model or at least its vocabulary")
        self.config = config
        self.model = model
        self.vocab = model.vocab if model is not None else vocab
        self.transcriber = transcriber
        """Anything with ``transcribe(audio) -> str``: a ``TdtTranscriber``.
        Without one, examples are only searched for in the CTC evidence."""

        self.embedder = embedder
        """Anything with ``embed(texts, document) -> unit vectors``: an
        ``Embedder``. Without one, examples must be said word for word."""

        self.examples: dict[str, tuple[str, str]] = {
            sentence_key(text, config.language): (intent.name, text)
            for intent in config.intents.values()
            for text in intent.examples
        }
        # Spoken phrase -> (intent, example as written).
        self._example_phrases: dict[str, tuple[str, str]] = {}
        self._example_recognizer = self._build_example_recognizer()
        self._embed_examples()
        started = time.perf_counter()
        self.graph, self.unspeakable, self.graph_stats = self._build_graph()
        self.build_seconds = time.perf_counter() - started

        self._terminals_by_meaning: dict[tuple, list[int]] = defaultdict(list)
        self._names_by_type: dict[str, list[Meaning]] = defaultdict(list)
        for terminal, meanings in enumerate(self.graph.meanings):
            for meaning in meanings:
                key = _meaning_key(meaning)
                if meaning.kind == "name" and key not in self._terminals_by_meaning:
                    self._names_by_type[meaning.name_type].append(meaning)
                self._terminals_by_meaning[key].append(terminal)

        # Where to look for each intent's missing pieces, for rejection reasons.
        self._group_terminals: dict[str, list[np.ndarray]] = {}
        self._name_terminals: dict[tuple[str, str], tuple[np.ndarray, list[str]]] = {}
        for intent in self.keyword_intents:
            self._group_terminals[intent.name] = [
                self._terminal_array([("keyword", phrase) for phrase in group])
                for group in intent.require
            ]
            for name_type in NAME_TYPES:
                if not intent.name_count(name_type):
                    continue
                pairs = sorted(
                    {
                        (terminal, meaning.name_id)
                        for meaning in self._names_by_type[name_type]
                        if _name_allowed(intent, meaning)
                        for terminal in self._terminals_by_meaning[_meaning_key(meaning)]
                    }
                )
                self._name_terminals[(intent.name, name_type)] = (
                    np.asarray([t for t, _ in pairs], dtype=np.int64),
                    [name_id for _, name_id in pairs],
                )

    @property
    def keyword_intents(self) -> list[Intent]:
        return [intent for intent in self.config.intents.values() if intent.keywords]

    # -- building ----------------------------------------------------------

    def _embed_examples(self) -> None:
        """The examples' embeddings, and the built-in everyday requests not
        too close to any of them."""
        self._example_owners = [
            (intent.name, text) for intent in self.config.intents.values() for text in intent.examples
        ]
        # Descriptions too, for naming the closest intent (closest_by_meaning).
        self._hint_owners = self._example_owners + [
            (intent.name, intent.description)
            for intent in self.config.intents.values()
            if intent.description
        ]
        self._distractors: list[str] = []
        self.embed_seconds = 0.0
        if self.embedder is None or not self._hint_owners:
            return
        started = time.perf_counter()
        self._hint_vectors = self.embedder.embed(
            [text for _, text in self._hint_owners], document=True
        )
        self._example_vectors = self._hint_vectors[: len(self._example_owners)]
        candidates = distractors(self.config.language)
        vectors = self.embedder.embed(candidates, document=True)
        cutoff = self.config.settings.embedding_distractor_cutoff
        keep = np.zeros(len(candidates), dtype=bool)
        if candidates:
            keep = (vectors @ self._hint_vectors.T).max(axis=1) < cutoff
        self._distractors = [text for text, kept in zip(candidates, keep) if kept]
        self._distractor_vectors = vectors[keep]
        self.embed_seconds = time.perf_counter() - started

    def _build_example_recognizer(self) -> "Recognizer | None":
        """The example sentences as intents of their own, each requiring one
        of its sentences as a single phrase. A separate graph, so the
        sentences neither contest keywords nor count as recognizing the
        words a keyword intent leaves unexplained. Nothing is contested and
        nothing is too costly here: ``_match_ctc_examples`` ranks them.

        Words a sentence leaves unexplained cost ``unexplained_important_cost``
        when another example recognized them, ``unexplained_other_cost``
        otherwise."""
        config = self.config
        intents = {}
        for intent in config.intents.values():
            phrases = []
            for text in intent.examples:
                phrase = " ".join(spoken_words(text, config.language)[0])
                self._example_phrases.setdefault(phrase, (intent.name, text))
                phrases.append(phrase)
            if phrases:
                intents[intent.name] = Intent(intent.name, require=(tuple(dict.fromkeys(phrases)),))
        if not intents:
            return None
        settings = config.settings
        settings = replace(
            settings,
            # Every sentence that could match, or come within the margin of
            # one that does, passes the gate.
            span_gate=max(settings.span_gate, settings.example_max_cost + settings.example_margin),
            contest_margin=_INF,
            max_cost=_INF,
            max_unexplained_important=1 << 30,
            max_unexplained_other=1 << 30,
        )
        # Only the sentences: names and numbers would only make the words
        # around a sentence costlier to leave unexplained, and triple the
        # graph.
        examples = replace(
            config,
            settings=settings,
            filler=(),
            name_cues=(),
            detect_numbers=None,
            names={name_type: {} for name_type in NAME_TYPES},
            intents=intents,
            wake=None,
        )
        return Recognizer(examples, vocab=self.vocab)

    def _build_graph(self) -> tuple[CompiledGraph, list[str], dict[str, int]]:
        config = self.config
        language = config.language
        graph = PhraseGraph(self.vocab)

        def add_phrase(phrase: str, meaning: Meaning, flexible: bool = False) -> None:
            for words in spoken_words(phrase, language) or [phrase.split()]:
                graph.add(words, meaning, flexible)

        for intent in config.intents.values():
            for phrase in {p for g in intent.require + intent.optional for p in g} | set(
                intent.exclude
            ):
                for part in phrase_parts(phrase):
                    add_phrase(part, Meaning("keyword", part))
        for phrase in config.filler:
            add_phrase(phrase, Meaning("filler", phrase))

        def add_names(entries, scope: str | None) -> None:
            for entry in entries:
                for alias in entry.phrases:
                    for units in name_units(alias, language):
                        meaning = Meaning(
                            "name", " ".join(units), entry.type, entry.id, scope
                        )
                        graph.add(units, meaning, flexible=True)

        for items in config.names.values():
            add_names(items.values(), None)
        for intent in config.intents.values():
            for requirement in intent.names.values():
                if requirement.items is not None:
                    add_names(requirement.items.values(), intent.name)

        detect = config.detect_numbers
        values = set()
        if detect is not None:
            values.update(value_grid(detect.minimum, detect.maximum, detect.step))
        for intent in config.intents.values():
            if intent.number is not None:
                spec = intent.number
                values.update(value_grid(spec.minimum, spec.maximum, spec.step))
        for value in sorted(values):
            for form in spoken_forms(value, language):
                graph.add(form.split(), Meaning("number", form, value=value.normalize()))

        with_units = set()
        for intent in config.intents.values():
            spec = intent.number
            for unit in spec.units if spec is not None else ():
                for form, value in unit_forms(unit, language):
                    if spec.accepts(value):
                        with_units.add((form, value.normalize(), unit))
        for form, value, unit in sorted(with_units):
            graph.add(form.split(), Meaning("number", form, value=value, unit=unit))

        stats = graph.stats()
        return graph.compile(), graph.unspeakable, stats

    # -- recognition -------------------------------------------------------

    def recognize(self, audio: np.ndarray) -> Recognition:
        if self.model is None:
            raise RuntimeError("No acoustic model loaded")
        started = time.perf_counter()
        log_probs, frame_seconds = self.model.log_probs(audio)
        forward = time.perf_counter() - started
        result = self.recognize_log_probs(log_probs, frame_seconds)
        result.timings = {"acoustic": forward, **result.timings}
        return self.match_examples(result, audio)

    def match_examples(
        self, recognition: Recognition, audio: np.ndarray, transcriber: Any = None
    ) -> Recognition:
        """When neither the keyword intents nor the example sentences matched
        in the CTC evidence, transcribe ``audio`` (the same command) with the
        TDT head and look the transcript up among the examples; then compare
        the transcripts with the examples by embedding. ``recognize`` does
        this itself; callers of ``recognize_log_probs`` pass the audio here.
        ``transcriber`` stands in for the TDT head for this call (voice_call:
        a Home Assistant STT entity); its ``source`` names it."""
        transcriber = transcriber or self.transcriber
        source = getattr(transcriber, "source", "TDT")
        with_examples = [
            r for r in recognition.rejected if self.config.intents[r.intent].examples
        ]
        if recognition.matches or not with_examples:
            return recognition
        if transcriber is None and (
            self.embedder is None or not self.config.settings.embedding_accept
        ):
            for result in with_examples:
                result.reasons.append("examples not tried: no TDT or embedding model loaded")
            return recognition

        if transcriber is not None:
            started = time.perf_counter()
            transcript = transcriber.transcribe(audio)
            label = "tdt" if source == "TDT" else "typed" if source == "typed" else "stt"
            recognition.timings[label] = time.perf_counter() - started
            recognition.tdt_transcript = transcript
            found = self._find_example(transcript, skip_first=recognition.lead_in is not None)
            if found is not None:
                self._accept(
                    recognition,
                    IntentResult(found[0], True, cost=0.0, example=found[1], example_source=source),
                )
                return recognition
            for result in with_examples:
                result.reasons.append(f"{source} heard '{transcript}', none of its examples")
        if self.embedder is not None and self.config.settings.embedding_accept:
            self._match_embedding(recognition, with_examples)
        return recognition

    def example_first(self, recognition: Recognition, text: str, source: str = "typed") -> bool:
        """``text`` (typed, or a transcript) is one of the example sentences,
        word for word: that intent goes first, ahead of keyword matches, as
        an example found in the CTC evidence does. For evidence made from
        text, where example search would take a short example with words
        around it (``recognize_log_probs(examples=False)``)."""
        found = self._find_example(text, skip_first=False)
        if found is None:
            return False
        result = IntentResult(found[0], True, cost=0.0, example=found[1], example_source=source)
        recognition.rejected = [r for r in recognition.rejected if r.intent != result.intent]
        recognition.matches = [m for m in recognition.matches if m.intent != result.intent]
        for other in recognition.matches:
            other.ambiguous_with = [i for i in other.ambiguous_with if i != result.intent]
        result.notes.extend(f"keyword match {m.intent} ({m.cost:.2f}) comes after the example" for m in recognition.matches)
        recognition.matches.insert(0, result)
        return True

    @staticmethod
    def _accept(recognition: Recognition, result: IntentResult) -> None:
        recognition.rejected = [r for r in recognition.rejected if r.intent != result.intent]
        recognition.matches.append(result)

    def _match_embedding(self, recognition: Recognition, with_examples: list[IntentResult]) -> None:
        """The intent with the example closest in meaning to a transcript (the
        TDT one or the CTC one, whichever is closer) matches when it beats
        the next intent and every everyday request by ``embedding_margin``."""
        texts = [t for t in (recognition.tdt_transcript, recognition.transcript) if t and t.strip()]
        if not texts:
            return
        started = time.perf_counter()
        queries = self.embedder.embed(texts)
        recognition.timings["embedding"] = time.perf_counter() - started
        to_examples = (queries @ self._example_vectors.T).max(axis=0)
        closest: dict[str, tuple[float, str]] = {}
        for (intent, text), similarity in zip(self._example_owners, to_examples):
            if intent not in closest or similarity > closest[intent][0]:
                closest[intent] = (float(similarity), text)
        ranked = sorted(closest.items(), key=lambda item: -item[1][0])
        rivals = [(similarity, other) for other, (similarity, _) in ranked[1:2]]
        if self._distractors:
            to_distractors = (queries @ self._distractor_vectors.T).max(axis=0)
            best = int(np.argmax(to_distractors))
            rivals.append((float(to_distractors[best]), f"'{self._distractors[best]}' (no example)"))
        intent, (similarity, text) = ranked[0]
        rival = max(rivals, default=None)
        margin = self.config.settings.embedding_margin
        if rival is None or similarity - rival[0] >= margin:
            result = IntentResult(
                intent, True, cost=0.0, example=text, example_source="embedding", similarity=similarity
            )
            if rival is not None:
                result.notes.append(f"next: {rival[1]} at {rival[0]:.2f}")
            self._accept(recognition, result)
            return
        for result in with_examples:
            if result.intent not in closest:
                continue
            own, own_text = closest[result.intent]
            reason = f"closest example by embedding '{own_text}', similarity {own:.2f}"
            if result.intent == intent:
                reason += f", within {margin:g} of {rival[1]} at {rival[0]:.2f}"
            result.reasons.append(reason)

    def closest_by_meaning(self, texts: list[str]) -> dict | None:
        """The intent whose examples or description are closest in meaning to
        any of ``texts`` (transcripts), for saying what a command that matched
        nothing looks like it was trying to do. Never a match: returns
        ``{"intent", "text", "similarity", "next", "next_similarity"}``, where
        ``next`` is the runner-up intent or built-in everyday request. None
        without an embedder, descriptions or examples."""
        texts = [t for t in texts if t and t.strip()]
        if self.embedder is None or not self._hint_owners or not texts:
            return None
        queries = self.embedder.embed(texts)
        similar = (queries @ self._hint_vectors.T).max(axis=0)
        closest: dict[str, tuple[float, str]] = {}
        for (intent, text), similarity in zip(self._hint_owners, similar):
            if intent not in closest or similarity > closest[intent][0]:
                closest[intent] = (float(similarity), text)
        ranked = sorted(closest.items(), key=lambda item: -item[1][0])
        intent, (similarity, text) = ranked[0]
        rivals = [(s, other) for other, (s, _) in ranked[1:2]]
        if self._distractors:
            to_distractors = (queries @ self._distractor_vectors.T).max(axis=0)
            best = int(np.argmax(to_distractors))
            rivals.append((float(to_distractors[best]), f"'{self._distractors[best]}'"))
        rival = max(rivals, default=(None, None))
        return {
            "intent": intent,
            "text": text,
            "similarity": similarity,
            "next": rival[1],
            "next_similarity": rival[0],
        }

    def rank_by_meaning(self, texts: list[str]) -> list[tuple[str, float]]:
        """Every intent with examples or a description, closest in meaning to
        any of ``texts`` first: ``[(intent, similarity)]``. Empty without an
        embedder. (For narrowing what something else chooses from; never a
        match.)"""
        texts = [t for t in texts if t and t.strip()]
        if self.embedder is None or not self._hint_owners or not texts:
            return []
        queries = self.embedder.embed(texts)
        similar = (queries @ self._hint_vectors.T).max(axis=0)
        best: dict[str, float] = {}
        for (intent, _text), similarity in zip(self._hint_owners, similar):
            best[intent] = max(best.get(intent, -1.0), float(similarity))
        return sorted(best.items(), key=lambda item: -item[1])

    def _find_example(self, transcript: str, skip_first: bool) -> tuple[str, str] | None:
        """``(intent, example)`` for a transcript; also without its first word
        when the keyword pass took that for the tail of the wake word."""
        language = self.config.language
        found = self.examples.get(sentence_key(transcript, language))
        words = transcript.split()
        if found is None and skip_first and len(words) > 1:
            found = self.examples.get(sentence_key(" ".join(words[1:]), language))
        return found

    def recognize_log_probs(
        self, log_probs: np.ndarray, frame_seconds: float, examples: bool = True
    ) -> Recognition:
        """Every intent against the evidence. ``examples``: also search it for
        the example sentences (off for evidence made from a transcript that
        was already looked up among them whole)."""
        settings = self.config.settings
        started = time.perf_counter()
        spotting = spot(
            self.graph,
            self.vocab,
            log_probs,
            frame_seconds,
            settings.span_gate,
            settings.contest_margin,
            live=settings.search == "live",
        )
        spotted = time.perf_counter()

        heard = [hit for hit in spotting.hits if hit.heard]
        word_costs, important, lead_in = self._word_costs(spotting, heard)
        name_positions = self._name_positions(spotting, heard)
        trailing = self._trailing_places(spotting, heard) if settings.places_must_fit else []

        results = [
            self._match_intent(
                intent, spotting, heard, word_costs, important, lead_in, name_positions, trailing
            )
            if intent.keywords
            else IntentResult(
                intent.name, False, reasons=["examples only: tried when no keyword intent matches"]
            )
            for intent in self.config.intents.values()
        ]
        matches = sorted((r for r in results if r.matched), key=lambda r: r.cost)
        rejected = [r for r in results if not r.matched]
        if matches:
            best = matches[0]
            for other in matches[1:]:
                if other.cost - best.cost < settings.ambiguity_margin:
                    best.ambiguous_with.append(other.intent)
                    other.ambiguous_with.append(best.intent)
        finished = time.perf_counter()

        recognition = Recognition(
            transcript=spotting.transcript,
            duration=spotting.num_frames * frame_seconds,
            spotting=spotting,
            matches=matches,
            rejected=rejected,
            timings={"spotting": spotted - started, "matching": finished - spotted},
            lead_in=None if lead_in is None else spotting.words[lead_in].text,
        )
        if examples and self._example_recognizer is not None and (
            not matches or settings.examples_first
        ):
            self._match_ctc_examples(recognition, log_probs, frame_seconds)
        return recognition

    def _match_ctc_examples(
        self, recognition: Recognition, log_probs: np.ndarray, frame_seconds: float
    ) -> None:
        """Search the CTC evidence for the example sentences. The cheapest
        matches if it costs at most ``example_max_cost`` and beats every other
        intent's by ``example_margin``."""
        settings = self.config.settings
        started = time.perf_counter()
        ranked = self._example_recognizer.recognize_log_probs(log_probs, frame_seconds).matches
        recognition.timings["examples"] = time.perf_counter() - started
        spotting = recognition.spotting
        trailing = self._trailing_places(spotting, [h for h in spotting.hits if h.heard]) if settings.places_must_fit else []

        rejected = {r.intent: r for r in recognition.rejected}
        for result in rejected.values():
            if not self.config.intents[result.intent].keywords:
                result.reasons.clear()
        if settings.places_must_fit:
            # A sentence said with a room it cannot take is not that sentence.
            fitting = []
            for result in ranked:
                foreign = self._foreign_places(recognition.spotting, result.spans, result.intent, trailing=trailing)
                if not foreign:
                    fitting.append(result)
                elif result.intent in rejected:
                    rejected[result.intent].reasons.append(
                        f"example '{self._example_text(result)}' leaves {foreign[0][0]} {foreign[0][1]}, "
                        "which this intent cannot take"
                    )
                    rejected[result.intent].missing.append({"kind": "place", "type": foreign[0][0], "id": foreign[0][1]})
            ranked = fitting
        best = ranked[0] if ranked else None
        runner_up = ranked[1] if len(ranked) > 1 else None
        if (
            best is not None
            and best.cost <= settings.example_max_cost
            and (runner_up is None or runner_up.cost - best.cost >= settings.example_margin)
        ):
            # A sentence said word for word wins over keyword matches, which
            # stay behind it (with examples_first; otherwise there are none).
            if best.intent in rejected:
                recognition.rejected.remove(rejected[best.intent])
            recognition.matches = [m for m in recognition.matches if m.intent != best.intent]
            for other in recognition.matches:
                other.ambiguous_with = [i for i in other.ambiguous_with if i != best.intent]
            best.example = self._example_text(best)
            best.example_source = "CTC"
            best.ambiguous_with = []
            best.notes.extend(
                f"keyword match {m.intent} ({m.cost:.2f}) comes after the example"
                for m in recognition.matches
            )
            recognition.matches.insert(0, best)
            return

        found = {r.intent for r in ranked}
        for result in ranked:
            if result.intent not in rejected:
                continue  # matched on its keywords
            text = self._example_text(result)
            if result.cost > settings.example_max_cost:
                why = f"limit {settings.example_max_cost:g}"
            else:
                other = runner_up if result is best else best
                why = f"within {settings.example_margin:g} of {other.intent} ({other.cost:.2f})"
            rejected[result.intent].reasons.append(
                f"example '{text}' costs {result.cost:.2f} in the CTC evidence, {why}"
            )
        for name, result in rejected.items():
            if self.config.intents[name].examples and name not in found:
                result.reasons.append("no example close in the CTC evidence")
                if not self.config.intents[name].keywords:
                    result.missing.append({"kind": "example"})

    def _foreign_places(
        self,
        spotting: Spotting,
        spans: list[SpanUse],
        intent_name: str,
        hits: bool = False,
        trailing: list[tuple[int, Hit, Meaning]] | None = None,
    ) -> list[Any]:
        """Areas and floors heard where ``spans`` explain nothing -- or only as
        free text, when the sentence ends on them ("play music *in the
        office*") -- of a type the intent takes none of: ``[(type, id)]``, or
        ``[(type, id, hit)]``."""
        intent = self.config.intents.get(intent_name)
        if intent is None:
            return []
        said_last = {id(hit) for _, hit, _ in trailing or []}
        out: dict[tuple[str, str], Hit] = {}
        for hit in spotting.hits:
            if not (hit.heard and hit.meaningful):
                continue
            covering = [use for use in spans if use.hit.start <= hit.center <= use.hit.end]
            if covering and not (id(hit) in said_last and all(use.role == "wildcard" for use in covering)):
                continue
            for meaning in hit.meanings:
                if meaning.kind == "name" and meaning.name_type in PLACE_TYPES and not intent.name_count(meaning.name_type):
                    out.setdefault((meaning.name_type, meaning.name_id), hit)
        return [(*key, hit) for key, hit in out.items()] if hits else list(out)

    def _trailing_places(self, spotting: Spotting, heard: list[Hit]) -> list[tuple[int, Hit, Meaning]]:
        """Areas and floors the sentence ends on, said as a place: after "in",
        "on", "at" or "to" (and "the"), or "upstairs", "downstairs" alone.
        ``[(first frame of the place clause, hit, meaning)]``."""
        filler = {f for f in self.config.filler if " " not in f}
        words = [(w, w.text.lower().strip(".,?!;:\"'")) for w in spotting.words if w.frames]
        while words and words[-1][1] in filler:
            words.pop()
        if not words:
            return []
        last = words[-1][0]
        out = []
        for hit in heard:
            if not hit.meaningful or hit.end < last.start:
                continue
            for meaning in hit.meanings:
                if meaning.kind != "name" or meaning.name_type not in PLACE_TYPES:
                    continue
                first = next((i for i, (w, _) in enumerate(words) if w.end >= hit.start), None)
                if first is None:
                    continue
                before = first - 1
                if before >= 0 and words[before][1] == "the":
                    before -= 1
                if before >= 0 and words[before][1] in PLACE_WORDS:
                    out.append((words[before][0].start, hit, meaning))
                elif words[first][1] in BARE_PLACES:
                    out.append((words[first][0].start, hit, meaning))
        return out

    def _example_text(self, result: IntentResult) -> str:
        phrase = next(use.meaning.phrase for use in result.spans if use.role == "require")
        return self._example_phrases[phrase][1]

    def _word_costs(
        self, spotting: Spotting, heard: list[Hit]
    ) -> tuple[np.ndarray, list[bool], int | None]:
        """Cost per frame of leaving the greedy transcript's words unexplained.

        A word's cost is spread over the frames its pieces fired on, so a parse
        that explains part of a word ("light" of "lights") pays for the rest.
        Also returns the index of a lead-in word (see ``Settings.lead_in``),
        which costs nothing.
        """
        settings = self.config.settings
        costs = np.zeros(spotting.num_frames + 1, dtype=np.float64)
        important = []
        meaningful = [hit for hit in heard if hit.meaningful]
        lead_in = None
        for index, word in enumerate(spotting.words):
            is_important = any(
                hit.start <= frame <= hit.end for hit in meaningful for frame in word.frames
            )
            important.append(is_important)
            if (
                index == 0
                and not is_important
                and (word.end + 1) * spotting.frame_seconds <= settings.lead_in
            ):
                lead_in = index
                continue
            weight = (
                settings.unexplained_important_cost
                if is_important
                else settings.unexplained_other_cost
            )
            for frame in word.frames:
                costs[frame] += weight / len(word.frames)
        return costs, important, lead_in

    def _name_positions(self, spotting: Spotting, heard: list[Hit]) -> dict[int, str]:
        """Words where a name goes: right after a name cue ("the garage"), and
        either unrecognized or recognized only as a name. An intent that leaves
        one unexplained would act on something it was not asked about. Maps
        word index to the cue."""
        cues = set(self.config.name_cues)
        if not cues:
            return {}
        filler = {phrase for phrase in self.config.filler if " " not in phrase}
        words = [word.text.lower().strip(".,?!'") for word in spotting.words]
        out = {}
        for index in range(1, len(words)):
            if words[index - 1] not in cues or words[index] in filler | cues:
                continue
            frames = spotting.words[index].frames
            kinds = {
                meaning.kind
                for hit in heard
                if hit.meaningful and any(hit.start <= f <= hit.end for f in frames)
                for meaning in hit.meanings
                if meaning.meaningful
            }
            if kinds <= {"name"}:
                out[index] = words[index - 1]
        return out

    # -- per intent --------------------------------------------------------

    def _items(self, intent: Intent, heard: list[Hit]) -> tuple[list[_Item], dict[str, int]]:
        require_groups: dict[str, list[int]] = defaultdict(list)
        # Part phrase -> (group, (split phrase, part index, part count)).
        split_parts: dict[str, list[tuple[int, tuple[int, int, int]]]] = defaultdict(list)
        splits = 0
        for index, group in enumerate(intent.require):
            for phrase in group:
                parts = phrase_parts(phrase)
                if len(parts) == 1:
                    require_groups[phrase].append(index)
                    continue
                for k, part in enumerate(parts):
                    split_parts[part].append((index, (splits, k, len(parts))))
                splits += 1
        optional = {phrase for group in intent.optional for phrase in group}
        cond_bits: dict[str, int] = {}
        if intent.number is not None:
            for rule in intent.number.prior:
                for phrase in sorted(rule.when):
                    cond_bits.setdefault(phrase, 1 << len(cond_bits))

        items: list[_Item] = []
        seen: set[tuple] = set()

        def add(
            hit: Hit,
            role: str,
            meaning: Meaning,
            group: int | None = None,
            part: tuple[int, int, int] | None = None,
        ) -> None:
            key = (
                id(hit), role, group, part, meaning.name_type, meaning.name_id, meaning.value, meaning.unit
            )
            if key in seen:
                return
            seen.add(key)
            bit = groups = 0
            if role in ("require", "optional") and part is None:
                bit = cond_bits.get(meaning.phrase, 0)
            elif role == "number" and meaning.unit is not None:
                # The unit inside counts as that keyword: it meets its group
                # and its prior conditions.
                bit = cond_bits.get(meaning.unit, 0)
                for index in require_groups.get(meaning.unit, ()):
                    groups |= 1 << index
            items.append(
                _Item(SpanUse(role, hit, meaning, group, part), hit.penalty, bit, groups)
            )

        for hit in heard:
            for meaning in hit.meanings:
                if meaning.kind == "keyword":
                    for group in require_groups.get(meaning.phrase, ()):
                        add(hit, "require", meaning, group)
                    for group, part in split_parts.get(meaning.phrase, ()):
                        add(hit, "require", meaning, group, part)
                    if meaning.phrase in optional:
                        add(hit, "optional", meaning)
                elif meaning.kind == "filler":
                    add(hit, "filler", meaning)
                elif meaning.kind == "name":
                    if _name_allowed(intent, meaning):
                        add(hit, "name", meaning)
                elif meaning.kind == "number":
                    spec = intent.number
                    if (
                        spec is not None
                        and spec.accepts(meaning.value)
                        and (meaning.unit is None or meaning.unit in spec.units)
                    ):
                        add(hit, "number", meaning)
        items.sort(key=lambda item: (item.end, item.start))
        return items, cond_bits

    def _match_intent(
        self,
        intent: Intent,
        spotting: Spotting,
        heard: list[Hit],
        word_costs: np.ndarray,
        important: list[bool],
        lead_in: int | None,
        name_positions: dict[int, str],
        trailing: list[tuple[int, Hit, Meaning]] | None = None,
    ) -> IntentResult:
        settings = self.config.settings
        items, cond_bits = self._items(intent, heard)
        num_frames = spotting.num_frames
        wild = intent.wildcard
        if wild is not None:
            stop = {p for p in self.config.filler if " " not in p} | set(self.config.name_cues)
            units = set(intent.number.units) if intent.number else set()
            lead = stop | units | {unit + "s" for unit in units}
            # Free text to the end may stop before a place this intent takes:
            # "play jazz | in the kitchen".
            cuts = {start for start, _, meaning in trailing or [] if intent.name_count(meaning.name_type)}
            items += _wildcard_items(wild, spotting, settings.wildcard_cost, stop, lead, cuts)
            items.sort(key=lambda item: (item.end, item.start))
        # Free text with both edges (or running to the end) may have fillers
        # between it and its keywords: "add [the] milk", "remind me [to] ...".
        free_edges = wild is not None and (wild.bounded or wild.to_end)

        excluded = [
            hit
            for hit in heard
            if any(m.kind == "keyword" and m.phrase in intent.exclude for m in hit.meanings)
        ]
        excluded_at = np.zeros(num_frames + 1, dtype=np.int64)
        for hit in excluded:
            excluded_at[hit.center] += 1
        # A word where a name goes, left unexplained, rejects the intent; as a
        # veto here, the search prefers a poorly heard name to leaving it.
        for index in name_positions:
            frames = spotting.words[index].frames
            if frames:
                excluded_at[frames[len(frames) // 2]] += 1
        cost_sum = np.concatenate(([0.0], np.cumsum(word_costs)))
        excluded_sum = np.concatenate(([0], np.cumsum(excluded_at)))

        def gap(after: int, before: int) -> tuple[float, int]:
            """Cost and vetoes of the frames strictly between two spans."""
            lo, hi = after + 1, before
            if hi <= lo:
                return 0.0, 0
            return float(cost_sum[hi] - cost_sum[lo]), int(excluded_sum[hi] - excluded_sum[lo])

        all_groups = (1 << len(intent.require)) - 1
        counts = tuple(intent.name_count(t) for t in NAME_TYPES)
        minimums = tuple(intent.name_minimum(t) for t in NAME_TYPES)
        spec = intent.number
        number_needed = spec is not None and not spec.optional
        when_by_bit = {bit: phrase for phrase, bit in cond_bits.items()}

        # A duration: each number is followed by one of these keywords ("five
        # minutes"), or said with its unit ("an hour"), and is reported by its
        # unit. More than one part ("an hour and ten minutes") with `parts`.
        compound = spec is not None and bool(spec.units)
        unit_of: dict[str, str] = {}
        if compound:
            keywords = {phrase for group in intent.require + intent.optional for phrase in group}
            for unit in spec.units:
                for form in (unit, unit + "s"):
                    if form in keywords:
                        unit_of[form] = unit
        unit_phrases = set(unit_of) | set(spec.units if spec else ())

        def add_part(parts: _Parts, unit: str, value: Decimal, phrase: str) -> _Parts | None:
            done = parts[0]
            if len(done) >= spec.parts or (
                done and DURATION_UNITS[unit] >= DURATION_UNITS[done[-1][0]]
            ):
                return None  # one more part than allowed, or not largest first
            return done + ((unit, value, phrase),), None

        def advance(state: _State, item: _Item) -> _State | None:
            groups, names, number, conds, begun, taken = state
            use = item.use
            if wild is not None:
                cue = use.role in ("require", "optional") and use.part is None
                if taken == "waiting":
                    # Free text placed before a keyword must get it next.
                    if free_edges and use.role == "filler":
                        pass
                    elif not (cue and use.meaning.phrase in wild.before):
                        return None
                    else:
                        taken = "done"
                elif use.role == "wildcard":
                    if taken == "after":
                        taken = "waiting" if wild.bounded else "done"
                    elif taken == "" and wild.before and item.led and not free_edges:
                        taken = "waiting"
                    else:
                        return None
                elif taken == "after" and free_edges and use.role == "filler":
                    pass
                elif taken != "done":
                    taken = "after" if cue and use.meaning.phrase in wild.after else ""
            if use.role == "require" and use.part is None:
                groups |= 1 << use.group
            elif use.role == "require":
                # A part of a split phrase: the first begins it, each next one
                # must follow the part before, and the last meets the group.
                split, k, count = use.part
                if groups >> use.group & 1:
                    return None
                waiting = dict(begun)
                if k == 0:
                    if use.group in waiting:
                        return None
                    waiting[use.group] = (split, 1)
                elif waiting.get(use.group) != (split, k):
                    return None
                elif k + 1 == count:
                    del waiting[use.group]
                    groups |= 1 << use.group
                else:
                    waiting[use.group] = (split, k + 1)
                begun = tuple(sorted(waiting.items()))
            elif use.role == "name":
                slot = NAME_TYPES.index(use.meaning.name_type)
                chosen = names[slot]
                if use.meaning.name_id in chosen or len(chosen) >= counts[slot]:
                    return None
                names = (
                    names[:slot] + (tuple(sorted(chosen + (use.meaning.name_id,))),) + names[slot + 1 :]
                )
            elif use.role == "number" and not compound:
                if number is not None:
                    return None
                number = use.meaning.value
            if compound:
                parts = number or ((), None)
                unit = unit_of.get(use.meaning.phrase) if use.role in ("require", "optional") else None
                if use.role == "number":
                    if parts[1] is not None:
                        return None
                    if use.meaning.unit is None:
                        parts = parts[0], use.meaning.value
                    else:
                        parts = add_part(parts, use.meaning.unit, use.meaning.value, use.meaning.unit)
                elif parts[1] is not None:
                    # The number waiting for its unit must get it next.
                    if unit is None or use.part is not None:
                        return None
                    parts = add_part(parts, unit, parts[1], use.meaning.phrase)
                if parts is None:
                    return None
                number = parts if parts != ((), None) else None
            return groups | item.groups, names, number, conds | item.cond_bit, begun, taken

        text_needed = wild is not None and wild.required

        def complete(state: _State) -> bool:
            groups, names, number, _, begun, taken = state
            return (
                taken != "waiting"
                and (taken == "done" or not text_needed)
                and groups == all_groups
                and not begun
                and all(low <= len(chosen) for chosen, low in zip(names, minimums))
                and (number is not None or not number_needed)
                and not (compound and number is not None and number[1] is not None)
            )

        def prior(state: _State) -> float:
            number, conds = state[2], state[3]
            if spec is None or number is None:
                return 0.0
            phrases = frozenset(p for bit, p in when_by_bit.items() if conds & bit)
            if not compound:
                return spec.prior_cost(number, phrases)
            # Each part on its own unit: "when: [seconds]" is about the seconds.
            others = phrases - unit_phrases
            return sum(
                spec.prior_cost(value, others | {phrase}) for _, value, phrase in number[0]
            )

        # Weighted interval scheduling: table[j] maps a state to the best parse
        # whose last span is items[j].
        table: list[dict[_State, tuple[int, float, tuple[int, _State] | None]]] = [
            {} for _ in items
        ]

        def relax(j: int, state: _State, vetoes: int, cost: float, back) -> None:
            current = table[j].get(state)
            if current is None or (vetoes, cost) < current[:2]:
                table[j][state] = (vetoes, cost, back)

        for j, item in enumerate(items):
            state = advance(_INITIAL, item)
            if state is not None:
                gap_cost, vetoes = gap(-1, item.start)
                relax(j, state, vetoes, gap_cost + item.cost, None)
            for i in range(j):
                previous = items[i]
                if previous.end >= item.start or not table[i]:
                    continue
                gap_cost, vetoes = gap(previous.end, item.start)
                for prior_state, (prior_vetoes, prior_cost, _) in table[i].items():
                    state = advance(prior_state, item)
                    if state is not None:
                        relax(
                            j,
                            state,
                            prior_vetoes + vetoes,
                            prior_cost + gap_cost + item.cost,
                            (i, prior_state),
                        )

        best: tuple[int, float, int | None, _State] | None = None
        if complete(_INITIAL):
            gap_cost, vetoes = gap(-1, num_frames)
            best = (vetoes, gap_cost, None, _INITIAL)
        for j, states in enumerate(table):
            for state, (vetoes, cost, _) in states.items():
                if not complete(state):
                    continue
                gap_cost, more = gap(items[j].end, num_frames)
                total = (vetoes + more, cost + gap_cost + prior(state))
                if best is None or total < best[:2]:
                    best = (*total, j, state)

        if best is None:
            result = IntentResult(intent.name, False)
            result.reasons = self._missing(intent, items, spotting, heard, result)
            return result

        vetoes, cost, last, state = best
        chosen: list[SpanUse] = []
        cursor = None if last is None else (last, state)
        while cursor is not None:
            j, at = cursor
            chosen.append(items[j].use)
            cursor = table[j][at][2]
        chosen.reverse()

        result = IntentResult(intent.name, True, cost=cost, spans=chosen)
        result.acoustic = sum(use.hit.penalty for use in chosen)
        result.prior = prior(state)
        result.unexplained_cost = cost - result.acoustic - result.prior
        result.slots = _slots(chosen)
        if compound:
            result.slots.pop("number", None)
            for unit, value, _ in state[2][0] if state[2] else ():
                result.slots[unit + "s"] = to_python(value)
        list_values = intent.list_values
        for use in chosen:
            if use.role == "wildcard":
                result.slots[wild.slot] = use.meaning.phrase
            elif (
                use.role in ("require", "optional")
                and use.part is None
                and use.meaning.phrase in list_values
            ):
                slot, value = list_values[use.meaning.phrase]
                result.slots[slot] = value
        result.heard_groups = len(intent.require)
        result.unexplained = self._unexplained(
            spotting, heard, chosen, important, lead_in, name_positions
        )
        self._judge(intent, result, excluded, spotting, trailing)
        return result

    def _judge(
        self,
        intent: Intent,
        result: IntentResult,
        excluded: list[Hit],
        spotting: Spotting,
        trailing: list[tuple[int, Hit, Meaning]] | None = None,
    ) -> None:
        settings = self.config.settings
        fmt = _Format(spotting.frame_seconds)

        for hit in excluded:
            if not any(use.hit.start <= hit.center <= use.hit.end for use in result.spans):
                result.reasons.append(f"excluded {fmt.hit(hit)} heard")
                result.missing.append(
                    {
                        "kind": "excluded",
                        "phrase": next(
                            m.phrase for m in hit.meanings
                            if m.kind == "keyword" and m.phrase in intent.exclude
                        ),
                    }
                )
        for word in result.unexplained:
            if word.name_cue is None:
                continue
            what = (
                f"is {', '.join(word.heard_as)}, which this intent cannot take"
                if word.heard_as
                else "sounds like a name the config does not have"
            )
            result.reasons.append(
                f"'{word.text}' ({fmt.span(word.start, word.end)}) after '{word.name_cue}' {what}"
            )
            result.missing.append({"kind": "unknown_name", "words": [word.text]})
        if settings.places_must_fit:
            cued = [w for w in result.unexplained if w.name_cue is not None]
            for kind, name_id, hit in self._foreign_places(spotting, result.spans, intent.name, hits=True, trailing=trailing):
                # A room the intent cannot take is what was missed, more than a name.
                result.missing.insert(0, {"kind": "place", "type": kind, "id": name_id})
                if not any(w.start <= hit.center <= w.end for w in cued):
                    result.reasons.append(f"heard {kind} {name_id}, which this intent cannot take")

        unused = [w for w in result.unexplained if w.important]
        other = [w for w in result.unexplained if not w.important]
        too_many = False
        if len(unused) > settings.max_unexplained_important:
            too_many = True
            result.reasons.append(
                f"{len(unused)} recognized word(s) left unused, limit "
                f"{settings.max_unexplained_important}: {fmt.words(unused)}"
            )
        if len(other) > settings.max_unexplained_other:
            too_many = True
            result.reasons.append(
                f"{len(other)} unrecognized word(s), limit "
                f"{settings.max_unexplained_other}: {fmt.words(other)}"
            )
        if too_many:
            result.missing.append(
                {"kind": "unexplained", "words": [w.text for w in result.unexplained]}
            )
        if result.cost > settings.max_cost:
            result.reasons.append(
                f"cost {result.cost:.2f} is above the rejection threshold {settings.max_cost:g}"
            )
            result.missing.append({"kind": "cost", "cost": round(result.cost, 2)})
        if intent.number is None:
            for word in unused:
                numbers = [h for h in word.heard_as if h.startswith("number ")]
                if numbers:
                    result.notes.append(
                        f"heard {numbers[0]} ({fmt.span(word.start, word.end)}) "
                        "but this intent takes no number"
                    )
        if result.reasons:
            result.matched = False

    def _unexplained(
        self,
        spotting: Spotting,
        heard: list[Hit],
        chosen: list[SpanUse],
        important: list[bool],
        lead_in: int | None,
        name_positions: dict[int, str],
    ) -> list[UnexplainedWord]:
        out = []
        for index, (word, is_important) in enumerate(zip(spotting.words, important)):
            if index == lead_in:
                continue
            covered = sum(
                any(use.hit.start <= f <= use.hit.end for use in chosen) for f in word.frames
            )
            if 2 * covered >= len(word.frames):
                continue
            labels = []
            for hit in heard:
                if hit.meaningful and any(hit.start <= f <= hit.end for f in word.frames):
                    labels.extend(m.label() for m in hit.meanings if m.meaningful)
            out.append(
                UnexplainedWord(
                    word.text,
                    word.start,
                    word.end,
                    is_important,
                    list(dict.fromkeys(labels)),
                    name_positions.get(index),
                )
            )
        return out

    # -- explaining a rejection -------------------------------------------

    def _terminal_array(self, keys: list[tuple]) -> np.ndarray:
        return np.asarray(
            sorted({t for key in keys for t in self._terminals_by_meaning.get(key, ())}),
            dtype=np.int64,
        )

    def _missing(
        self,
        intent: Intent,
        items: list[_Item],
        spotting: Spotting,
        heard: list[Hit],
        result: IntentResult | None = None,
    ) -> list[str]:
        fmt = _Format(spotting.frame_seconds)
        reasons = []
        missing: list[dict] = []
        list_slots = {tuple(lst.phrases): lst.slot for lst in intent.lists}
        heard_groups = 0
        for index, group in enumerate(intent.require):
            if _group_heard(items, index):
                heard_groups += 1
                continue
            terminals = self._group_terminals[intent.name][index]
            slot = list_slots.get(group)
            what = f"{slot} [{' | '.join(group)}]" if slot else f"[{' | '.join(group)}]"
            reasons.append(
                f"required {what} not heard" + self._evidence(terminals, spotting, fmt)
            )
            missing.append(
                {"kind": "require", "phrases": list(group), **({"slot": slot} if slot else {})}
            )

        for name_type in NAME_TYPES:
            count = intent.name_minimum(name_type)
            if not count:
                continue
            found = sorted(
                {
                    i.use.meaning.name_id
                    for i in items
                    if i.use.role == "name" and i.use.meaning.name_type == name_type
                }
            )
            if len(found) >= count:
                continue
            terminals, ids = self._name_terminals[(intent.name, name_type)]
            if found:
                terminals = terminals[np.asarray([i not in found for i in ids], dtype=bool)]
            heard_text = f": {', '.join(found)}" if found else ""
            reasons.append(
                f"expected {count} {name_type}(s), heard {len(found)}{heard_text}"
                + self._evidence(terminals, spotting, fmt)
            )
            missing.append({"kind": "name", "type": name_type, "count": count, "heard": found})

        if (
            intent.number is not None
            and not intent.number.optional
            and not any(i.use.role == "number" for i in items)
        ):
            spec = intent.number
            numbers = sorted(
                {m.value for h in heard for m in h.meanings if m.kind == "number"}
            )
            if numbers:
                values = ", ".join(str(to_python(v)) for v in numbers)
                reasons.append(f"heard number(s) {values}, outside {spec.describe()}")
            else:
                reasons.append(f"no number heard (expected {spec.describe()})")
            missing.append(
                {
                    "kind": "number",
                    "range": spec.describe(),
                    "units": list(spec.units),
                    "heard": [to_python(v) for v in numbers],
                }
            )

        wild = intent.wildcard
        if wild is not None and wild.required and not missing:
            where = []
            if wild.after:
                where.append(f"after [{' | '.join(wild.after)}]")
            if wild.before:
                where.append(f"before [{' | '.join(wild.before)}]")
            joiner = " and " if wild.bounded else " or "
            reasons.append(f"no {wild.slot} heard {joiner.join(where)}")
            missing.append({"kind": "text", "slot": wild.slot})

        if not reasons:
            reasons.append("the required phrases overlap; no combination of them fits the audio")
            missing.append({"kind": "overlap"})
        if result is not None:
            result.missing = missing
            result.heard_groups = heard_groups
        return reasons

    def _evidence(self, terminals: np.ndarray, spotting: Spotting, fmt: "_Format") -> str:
        """The closest the audio came to any of these phrases."""
        if terminals.size == 0:
            return ""
        wanted = set(terminals.tolist())
        contested = [h for h in spotting.hits if h.dominated_by and h.terminal in wanted]
        if contested:
            hit = min(contested, key=lambda h: h.penalty)
            return f"; {fmt.hit(hit)} was outscored by {fmt.hit(hit.dominated_by)}"
        closest = spotting.closest_of(terminals)
        if closest is None:
            if spotting.live:
                return "; nothing close (--full-search shows the closest placement)"
            return ""
        per_token, terminal, first, last = closest
        phrase = spotting.graph.meanings[terminal][0].phrase
        return (
            f"; closest '{phrase}' at {fmt.span(first, last)}, "
            f"{per_token:.1f}/token (gate {self.config.settings.span_gate:g})"
        )


def _meaning_key(meaning: Meaning) -> tuple:
    if meaning.kind == "name":
        return ("name", meaning.phrase, meaning.name_type, meaning.name_id, meaning.scope)
    if meaning.kind == "number":
        return ("number", meaning.value, meaning.unit)
    return (meaning.kind, meaning.phrase)


def _group_heard(items: list[_Item], group: int) -> bool:
    """Whether some phrase of a required group was heard: a whole phrase,
    every part of a split one, or a unit inside a number."""
    parts: dict[int, set[int]] = defaultdict(set)
    counts: dict[int, int] = {}
    for item in items:
        use = item.use
        if item.groups >> group & 1:
            return True
        if use.role != "require" or use.group != group:
            continue
        if use.part is None:
            return True
        split, k, count = use.part
        parts[split].add(k)
        counts[split] = count
    return any(len(heard) == counts[split] for split, heard in parts.items())


def _name_allowed(intent: Intent, meaning: Meaning) -> bool:
    requirement = intent.names.get(meaning.name_type)
    if requirement is None or requirement.count == 0:
        return False
    if requirement.items is not None:
        return meaning.scope == intent.name
    return (
        meaning.scope is None
        and (requirement.ids is None or meaning.name_id in requirement.ids)
        and (
            requirement.domains is None
            or meaning.name_id.split(".", 1)[0] in requirement.domains
        )
    )


def _wildcard_items(
    wild: Wildcard, spotting: Spotting, cost: float, stop: set[str], lead: set[str], cuts: set[int] | None = None
) -> list[_Item]:
    """Every run of 1 to ``max_words`` transcript words, as free text. Fillers
    and name cues are not part of it: "set a bread timer" misheard as "setet a
    bread timer" names the timer "bread", not "setet a bread". A run is `led`
    when the word before it is in ``lead``: "a", "the", "minute". Text that
    runs to the end may instead end before a word starting at one of
    ``cuts`` (frames): a place the sentence ends on."""
    words = [
        (word, word.text.lower().strip(".,?!;:\"'").replace("'", ""))
        for word in spotting.words
        if word.frames
    ]
    items = []
    inner = wild.bounded or wild.to_end
    for i, (first, _) in enumerate(words):
        led = i > 0 and words[i - 1][1] in lead
        texts: list[str] = []
        for offset, (word, text) in enumerate(words[i : i + wild.max_words]):
            if not text or (text in stop and not (inner and texts)):
                break
            texts.append(text)
            at = i + offset
            ends = at == len(words) - 1 or bool(cuts and words[at + 1][0].start in cuts)
            if text in stop or (wild.to_end and not ends):
                continue  # a filler may be inside the text, not at its edge
            meaning = Meaning("wildcard", " ".join(texts))
            hit = Hit(-1, [meaning], first.start, word.end, cost * len(texts), len(texts))
            items.append(_Item(SpanUse("wildcard", hit, meaning), hit.penalty, led=led))
    return items


def _slots(chosen: list[SpanUse]) -> dict:
    slots: dict = {}
    for use in chosen:
        if use.role == "name":
            slots.setdefault(use.meaning.name_type, []).append(use.meaning.name_id)
        elif use.role == "number":
            slots["number"] = to_python(use.meaning.value)
    return slots


class _Format:
    def __init__(self, frame_seconds: float) -> None:
        self.frame_seconds = frame_seconds

    def span(self, start: int, end: int) -> str:
        return f"{start * self.frame_seconds:.2f}-{(end + 1) * self.frame_seconds:.2f}s"

    def hit(self, hit: Hit) -> str:
        return f"'{hit.phrase}' ({self.span(hit.start, hit.end)}, cost {hit.penalty:.2f})"

    def words(self, words: list[UnexplainedWord]) -> str:
        parts = []
        for word in words:
            label = f" [{', '.join(word.heard_as)}]" if word.heard_as else ""
            parts.append(f"'{word.text}'{label}")
        return ", ".join(parts)
