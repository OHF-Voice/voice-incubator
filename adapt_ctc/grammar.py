"""A home's sentences: every way of saying its commands, as one word graph.

A keyword intent says its words in any order, any of them again: one phrase
of each required group at least, any of its optional keywords, its names and
its number (those it needs at least once) -- what its keyword match would
take with nothing left unexplained -- with fillers between them ("the",
"please"). A split phrase ("turn ... on") keeps its parts in order, with
anything between. Counting more than what is still needed (each optional
word at most once, no more names than the intent takes) copies every name
and number into a state for each count, and the graph grows past what a
device can hold. An intent with example sentences also says each of them,
as written. Free text can't be listed: an intent that
needs it (``wildcard: required``) has no sentences, and an optional one is
left out.

The graph is a finite-state acceptor of words (``WordGraph``), the wake
phrase first when given. ``write_fst`` spells each word with the acoustic
model's pieces and composes that with a CTC topology, for sherpa-onnx's
FST decoder (``GrammarDecoder``): the call is then transcribed as one of the
sentences, and the transcript matched as typed text.
"""

from __future__ import annotations

import random
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .acoustic import Vocabulary
from .config import NAME_TYPES, Config, Intent, NameEntry, phrase_parts
from .graph import PhraseGraph
from .numbers import spoken_forms, unit_forms, unit_words, value_grid
from .text import name_units, spoken_words

try:
    from . import _grammar
except ImportError:  # pragma: no cover - without a compiler
    _grammar = None

NATIVE = _grammar is not None
"""Whether the native search is built (setup.py): without it, ``decode`` is
Python's, a second or so a call."""

FILLER_COST = 1.0
"""Each filler word, in nats: dearer than none, so fillers don't explain
what the command's own words could."""

EXTRA_COST = 1.0
"""Each of the language's polite extras before or after a sentence."""

ANY_COST = 3.0
"""Each piece of anything before the sentence or after it: the wake word as
the model hears it ("okay, now we" for "okay Nabu"), a word cut off at the
end. Dearer than a word of the sentence, which is a piece or two."""

EXTRAS = {
    "en": (("um", "uh", "so", "hey", "okay"), ("thanks", "thank you", "for me", "please")),
}
"""Words said around a command that no config lists, by language: (before,
after). "Could you turn on the lights for me please, thanks"."""


@dataclass(frozen=True)
class Word:
    text: str
    mode: str = "strict"
    """How the acoustic model may spell it (graph.PhraseGraph): ``strict``,
    one word; ``flex-first`` and ``flex``, a name's units, which break into
    words anywhere; ``wake``, a word of the wake phrase, spelled as strict
    and left out of the transcript; ``any``, any one piece (not a word)."""


Phrase = tuple[Word, ...]
ANY = Word("", "any")


@dataclass
class WordGraph:
    """Arcs between states, each a word (None: an epsilon) and a cost."""

    arcs: list[tuple[int, int, Word | None, float]] = field(default_factory=list)
    finals: set[int] = field(default_factory=set)
    num_states: int = 1

    def state(self) -> int:
        self.num_states += 1
        return self.num_states - 1

    def add(self, src: int, dst: int, word: Word | None = None, cost: float = 0.0) -> None:
        self.arcs.append((src, dst, word, cost))

    def add_phrases(self, src: int, dst: int, phrases: "PhraseSet", cost: float = 0.0) -> None:
        """Any one of ``phrases`` from ``src`` to ``dst`` (a copy of its graph)."""
        nodes = {phrases.root: src}
        for node in phrases.order:
            here = nodes.setdefault(node, self.state())
            if node in phrases.accepting:
                self.add(here, dst)
            for word, child in phrases.children[node]:
                target = nodes.setdefault(child, self.state())
                self.add(here, target, word, cost if node == phrases.root else 0.0)

    @property
    def words(self) -> list[Word]:
        return sorted({w for _, _, w, _ in self.arcs if w is not None and w != ANY}, key=lambda w: (w.text, w.mode))

    def accepts(self, words: Iterable[str]) -> float | None:
        """The cheapest way to say ``words`` (lowercase), or None."""
        out: dict[int, list[tuple[Word | None, int, float]]] = defaultdict(list)
        for src, dst, word, cost in self.arcs:
            out[src].append((word, dst, cost))

        def closure(costs: dict[int, float]) -> dict[int, float]:
            stack = list(costs)
            while stack:
                state = stack.pop()
                for word, dst, cost in out[state]:
                    if word is None and costs[state] + cost < costs.get(dst, float("inf")):
                        costs[dst] = costs[state] + cost
                        stack.append(dst)
            return costs

        costs = closure({0: 0.0})
        for text in words:
            following: dict[int, float] = {}
            for state, so_far in costs.items():
                for word, dst, cost in out[state]:
                    if word is not None and word.text == text and so_far + cost < following.get(dst, float("inf")):
                        following[dst] = so_far + cost
            costs = closure(following)
            if not costs:
                return None
        ends = [cost for state, cost in costs.items() if state in self.finals]
        return min(ends) if ends else None

    def sample(self, count: int, seed: int = 0, longest: int = 30) -> list[str]:
        """Random sentences, for a look at what the graph says."""
        out: dict[int, list[tuple[Word | None, int]]] = defaultdict(list)
        for src, dst, word, _cost in self.arcs:
            out[src].append((word, dst))
        rng = random.Random(seed)
        sentences: list[str] = []
        for _ in range(count * 20):
            state, words = 0, []
            while len(words) < longest:
                options = out[state]
                if state in self.finals and (not options or rng.random() < 0.3):
                    sentences.append(" ".join(words))
                    break
                if not options:
                    break
                word, state = rng.choice(options)
                if word is not None:
                    words.append(word.text)
            if len(set(sentences)) >= count:
                break
        return list(dict.fromkeys(sentences))[:count]


@dataclass
class PhraseSet:
    """Phrases as a minimal acyclic graph: shared starts and ends."""

    root: int
    children: dict[int, list[tuple[Word, int]]]
    accepting: set[int]
    order: list[int]

    @classmethod
    def of(cls, phrases: Iterable[Phrase]) -> "PhraseSet":
        trie: list[dict[Word, int]] = [{}]
        ends: set[int] = set()
        for phrase in phrases:
            node = 0
            for word in phrase:
                if word not in trie[node]:
                    trie[node][word] = len(trie)
                    trie.append({})
                node = trie[node][word]
            ends.add(node)
        # Minimize: nodes with the same ending and the same arcs are one.
        ids: dict[tuple, int] = {}
        canonical: dict[int, int] = {}
        for node in reversed(range(len(trie))):  # children come after parents
            key = (node in ends, tuple(sorted((w.text, w.mode, canonical[c]) for w, c in trie[node].items())))
            canonical[node] = ids.setdefault(key, len(ids))
        children: dict[int, list[tuple[Word, int]]] = {}
        for node in range(len(trie)):
            children.setdefault(canonical[node], [(w, canonical[c]) for w, c in trie[node].items()])
        accepting = {canonical[n] for n in ends}
        # Parents before children, from the root.
        order, seen, stack = [], set(), [canonical[0]]
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            order.append(node)
            stack.extend(child for _, child in children[node])
        return cls(canonical[0], children, accepting, order)

    @property
    def empty(self) -> bool:
        return not self.accepting


# ------------------------------------------------------------------ the words


def keyword_phrases(phrase: str, language: str) -> list[Phrase]:
    return [tuple(Word(w) for w in words) for words in spoken_words(phrase, language)]


def name_phrases(entries: Iterable[NameEntry], language: str) -> list[Phrase]:
    return [
        tuple(Word(unit, "flex" if index else "flex-first") for index, unit in enumerate(units))
        for entry in entries
        for alias in entry.phrases
        for units in name_units(alias, language)
        if units
    ]


def number_phrases(intent: Intent, config: Config) -> list[Phrase]:
    """Every number the intent's number may be said as: each value on its
    grid, and any the config listens for regardless (``numbers.detect``),
    which the matcher then refuses ("one hundred fifty percent") -- with its
    unit, for a duration."""
    spec, language = intent.number, config.language
    assert spec is not None
    values = value_grid(spec.minimum, spec.maximum, spec.step)
    if not spec.units and config.detect_numbers is not None:
        detect = config.detect_numbers
        values = sorted({*values, *value_grid(detect.minimum, detect.maximum, detect.step)})
    phrases = []
    for value in values:
        for form in spoken_forms(value, language):
            words = form.split()
            if not spec.units:
                phrases.append(words)
                continue
            for unit in spec.units:
                # Any number with the singular too: "a five minute timer".
                phrases.extend([*words, form] for form in unit_words(unit, language))
    for unit in spec.units:
        phrases.extend(form.split() for form, value in unit_forms(unit, language)
                       if spec.minimum <= value <= spec.maximum)
    return [tuple(Word(w) for w in words) for words in phrases]


def allowed_names(intent: Intent, config: Config, name_type: str) -> list[NameEntry]:
    requirement = intent.names[name_type]
    if requirement.items is not None:
        return list(requirement.items.values())
    return [
        entry
        for entry in config.names.get(name_type, {}).values()
        if (requirement.ids is None or entry.id in requirement.ids)
        and (requirement.domains is None or entry.id.split(".", 1)[0] in requirement.domains)
    ]


# ------------------------------------------------------------------ the graph


@dataclass
class _Piece:
    """What a sentence says some of: a keyword group, a name, a number."""

    least: int
    whole: PhraseSet | None
    """The alternatives said in one go."""

    split: list[list[PhraseSet]] = field(default_factory=list)
    """The split alternatives ("turn ... on"), each its parts."""


def build(
    config: Config,
    wake: Iterable[str] = (),
    filler_cost: float = FILLER_COST,
    extra_cost: float = EXTRA_COST,
    any_cost: float | None = ANY_COST,
    intents: Iterable[str] | None = None,
) -> WordGraph:
    """The word graph of every intent's sentences, after one of the ``wake``
    phrases when given; with ``any_cost``, anything at all before and after
    them, at that cost a piece (``ANY``)."""
    language = config.language
    graph = WordGraph()
    start = 0
    wakes = [tuple(Word(w.text, "wake") for w in p) for phrase in wake for p in keyword_phrases(phrase, language)]
    if wakes or any_cost is not None:
        start = graph.state()
    if wakes:
        graph.add_phrases(0, start, PhraseSet.of(wakes))
    if any_cost is not None:
        graph.add(0, 0, ANY, any_cost)
        graph.add(0, start)
    before, after = EXTRAS.get(language.split("-")[0].split("_")[0], ((), ()))
    lead = PhraseSet.of(p for phrase in before for p in keyword_phrases(phrase, language))
    if not lead.empty:
        graph.add_phrases(start, start, lead, extra_cost)
    end = graph.state()
    graph.finals.add(end)
    trail = PhraseSet.of(p for phrase in after for p in keyword_phrases(phrase, language))
    if not trail.empty:
        graph.add_phrases(end, end, trail, extra_cost)
    if any_cost is not None:
        graph.add(end, end, ANY, any_cost)
    fillers = PhraseSet.of(
        p for phrase in dict.fromkeys((*config.filler, *config.name_cues)) for p in keyword_phrases(phrase, language)
    )
    wanted = None if intents is None else set(intents)
    for intent in config.intents.values():
        if wanted is not None and intent.name not in wanted:
            continue
        if intent.examples:
            first = graph.state()
            graph.add(start, first)
            if not fillers.empty:
                graph.add_phrases(first, first, fillers, filler_cost)
            last = graph.state()
            graph.add(last, end)
            graph.add_phrases(first, last, PhraseSet.of(
                p for sentence in intent.examples for p in keyword_phrases(sentence, language)
            ))
        if intent.keywords and not (intent.wildcard is not None and intent.wildcard.required):
            _add_keywords(graph, intent, config, start, end, fillers, filler_cost)
    return graph


def _pieces(intent: Intent, config: Config) -> list[_Piece]:
    language = config.language
    units: set[str] = set()
    if intent.number is not None:
        for unit in intent.number.units:
            units.update(unit_words(unit, language))
            units.add(unit)

    def group(phrases: tuple[str, ...], least: int) -> _Piece | None:
        # A group of the number's units is said with the number.
        if units and all(p in units for p in phrases):
            return None
        whole = [p for phrase in phrases if len(phrase_parts(phrase)) == 1 for p in keyword_phrases(phrase, language)]
        split = [
            [PhraseSet.of(keyword_phrases(part.strip(), language)) for part in phrase_parts(phrase)]
            for phrase in phrases
            if len(phrase_parts(phrase)) > 1
        ]
        return _Piece(least, PhraseSet.of(whole) if whole else None, split)

    pieces = [g for phrases in intent.require if (g := group(phrases, 1)) is not None]
    pieces += [g for phrases in intent.optional if (g := group(phrases, 0)) is not None]
    for name_type in NAME_TYPES:
        requirement = intent.names.get(name_type)
        if requirement is None or requirement.count == 0:
            continue
        names = PhraseSet.of(name_phrases(allowed_names(intent, config, name_type), language))
        if names.empty:
            if requirement.least:
                return []  # it needs a name the home doesn't have
            continue
        pieces.append(_Piece(requirement.least, names))
    if intent.number is not None:
        pieces.append(_Piece(0 if intent.number.optional else 1, PhraseSet.of(number_phrases(intent, config))))
    return pieces


def _add_keywords(
    graph: WordGraph, intent: Intent, config: Config, start: int, end: int,
    fillers: PhraseSet, filler_cost: float,
) -> None:
    """The intent's words in any order, any of them again: a state for each
    set of the pieces it needs that it has said, and the split phrase begun,
    if any."""
    pieces = _pieces(intent, config)
    if not pieces:
        return
    states: dict[tuple, int] = {}
    first = graph.state()
    graph.add(start, first)
    initial = (tuple(0 for _ in pieces), None)
    states[initial] = first
    pending = [initial]

    def to(key: tuple) -> int:
        if key not in states:
            states[key] = graph.state()
            pending.append(key)
        return states[key]

    def said(counts: tuple, index: int) -> tuple:
        return tuple(min(c + (i == index), piece.least) for i, (c, piece) in enumerate(zip(counts, pieces)))

    while pending:
        key = pending.pop()
        counts, begun = key
        here = states[key]
        if not fillers.empty:
            graph.add_phrases(here, here, fillers, filler_cost)
        if begun is None and all(c >= p.least for c, p in zip(counts, pieces)):
            graph.add(here, end)
        if begun is not None:
            index, alternative, part = begun
            parts = pieces[index].split[alternative]
            if part + 1 == len(parts):
                graph.add_phrases(here, to((said(counts, index), None)), parts[part])
            else:
                graph.add_phrases(here, to((counts, (index, alternative, part + 1))), parts[part])
        for index, piece in enumerate(pieces):
            if piece.whole is not None:
                graph.add_phrases(here, to((said(counts, index), begun)), piece.whole)
            if begun is None:
                for alternative, parts in enumerate(piece.split):
                    graph.add_phrases(here, to((counts, (index, alternative, 1))), parts[0])


# ------------------------------------------------------------------ the pieces


@dataclass
class TokenGraph:
    """A word graph spelled in the acoustic model's pieces, for ``decode``:
    each state's arcs together (``out_ptr``), each arc a piece (-1: none), a
    cost, and the word it starts (index into ``words``, + 1; 0: none; one
    past the words: anything after the sentence, which ends its last word)."""

    out_ptr: np.ndarray
    arc_dst: np.ndarray
    arc_token: np.ndarray
    arc_cost: np.ndarray
    arc_label: np.ndarray
    final: np.ndarray
    words: list[Word]
    unspeakable: list[str]

    @property
    def num_states(self) -> int:
        return int(self.final.shape[0])

    @property
    def num_arcs(self) -> int:
        return int(self.arc_dst.shape[0])

    @property
    def nbytes(self) -> int:
        return sum(a.nbytes for a in (self.out_ptr, self.arc_dst, self.arc_token, self.arc_cost, self.arc_label, self.final))


def compile_graph(graph: WordGraph, vocab: Vocabulary) -> TokenGraph:
    """Each word spelled with every sequence of the model's pieces that spells
    it, in any case (graph.PhraseGraph's lattices), the word's arc and cost
    on the first piece; ``ANY`` as one arc for each piece -- after a sentence
    (from a final state), each with the label that ends its last word."""
    spelling = PhraseGraph(vocab)
    words = graph.words
    ids = {w: i + 1 for i, w in enumerate(words)}
    by_word: dict[Word, list[tuple[int, int, float]]] = defaultdict(list)
    eps: list[tuple[int, int, float]] = []
    anys: list[tuple[int, int, float]] = []
    for src, dst, word, cost in graph.arcs:
        if word is None:
            eps.append((src, dst, cost))
        elif word == ANY:
            anys.append((src, dst, cost))
        else:
            by_word[word].append((src, dst, cost))
    i32 = np.int32
    parts: list[tuple[np.ndarray, ...]] = []

    def add(src, dst, token, cost, label) -> None:
        parts.append(tuple(np.asarray(x).ravel() for x in (src, dst, token, cost, label)))

    if eps:
        e = np.array(eps)
        add(e[:, 0], e[:, 1], np.full(len(eps), -1), e[:, 2], np.zeros(len(eps)))
    if anys:
        tokens = np.array([t for t in sorted(vocab.id2piece) if t != vocab.blank_id and not vocab.is_punctuation(t)])
        e = np.array(anys)
        after = np.array([len(words) + 1 if int(src) in graph.finals else 0 for src in e[:, 0]])
        add(np.repeat(e[:, 0], len(tokens)), np.repeat(e[:, 1], len(tokens)), np.tile(tokens, len(anys)),
            np.repeat(e[:, 2], len(tokens)), np.repeat(after, len(tokens)))
    next_state = graph.num_states
    unspeakable = []
    for word, arcs in by_word.items():
        lattice = spelling._lattice((word.text, _spelled(word.mode)))  # pylint: disable=protected-access
        if lattice is None:
            unspeakable.append(word.text)
            continue
        size, edges = lattice
        e = np.array(edges)
        a = np.array(arcs)
        # Lattice node 0 is the arc's source, 1 its end; the rest are new.
        mapping = np.empty((len(arcs), size), dtype=np.int64)
        mapping[:, 0], mapping[:, 1] = a[:, 0], a[:, 1]
        mapping[:, 2:] = next_state + np.arange(len(arcs) * (size - 2)).reshape(len(arcs), size - 2)
        next_state += len(arcs) * (size - 2)
        first = e[:, 0] == 0
        add(mapping[:, e[:, 0]], mapping[:, e[:, 1]], np.broadcast_to(e[:, 2], (len(arcs), len(e))),
            a[:, 2:3] * first, np.broadcast_to(np.where(first, ids[word], 0), (len(arcs), len(e))))
    src, dst, token, cost, label = (np.concatenate([p[i] for p in parts]) for i in range(5))
    order = np.argsort(src, kind="stable")
    out_ptr = np.zeros(next_state + 1, dtype=i32)
    np.cumsum(np.bincount(src.astype(np.int64), minlength=next_state), out=out_ptr[1:])
    final = np.zeros(next_state, dtype=np.uint8)
    final[sorted(graph.finals)] = 1
    return TokenGraph(
        out_ptr, dst[order].astype(i32), token[order].astype(i32), cost[order].astype(np.float32),
        label[order].astype(i32), final, words, sorted(unspeakable),
    )


def _spelled(mode: str) -> str:
    return "strict" if mode == "wake" else mode


# ------------------------------------------------------------------ decoding

BEAM = 16.0
"""Paths further than this, in nats, behind the best at a frame are dropped."""

MAX_ACTIVE = 4000
"""The most paths kept at a frame."""


@dataclass
class Heard:
    """The sentence the call was heard as, and its score (log-probability,
    less the graph's costs)."""

    words: list[Word]
    score: float
    frames: list[tuple[int, int]] = field(default_factory=list)
    """Each word's first and last frame (blank after it counts as its)."""

    @property
    def said(self) -> list[Word]:
        """Its words, without the wake phrase."""
        return [w for w in self.words if w.mode != "wake"]

    @property
    def said_frames(self) -> list[tuple[int, int]]:
        return [f for w, f in zip(self.words, self.frames) if w.mode != "wake"]

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.said)


def blank_of(log_probs: np.ndarray, vocab: Vocabulary) -> np.ndarray:
    """Each frame's blank, punctuation counted as blank."""
    blank = log_probs[:, vocab.blank_id]
    if vocab.punctuation_ids:
        blank = np.maximum(blank, log_probs[:, vocab.punctuation_ids].max(axis=1))
    return np.ascontiguousarray(blank, dtype=np.float32)


def decode(
    graph: TokenGraph, log_probs: np.ndarray, vocab: Vocabulary, beam: float = BEAM, max_active: int = MAX_ACTIVE,
    native: bool = True,
) -> Heard | None:
    """The best path through ``graph`` over ``log_probs`` (``[T, V]``): CTC's
    pieces, each held for as many frames as it lasts, blank between them and
    before and after, the same piece twice only with a blank between. None
    when no path reaches a final state."""
    log_probs = np.ascontiguousarray(log_probs, dtype=np.float32)
    blank = blank_of(log_probs, vocab)
    if native and _grammar is not None:
        result = _grammar.decode(
            graph.out_ptr, graph.arc_dst, graph.arc_token, graph.arc_cost, graph.arc_label, graph.final,
            log_probs, blank, graph.num_states, graph.num_arcs, log_probs.shape[0], log_probs.shape[1],
            float(beam), int(max_active),
        )
    else:
        result = _decode_python(graph, log_probs, blank, beam, max_active)
    if result is None:
        return None
    score, labels, starts = result
    # A word lasts until the next begins, or anything after the sentence.
    ends = [start - 1 for start in starts[1:]] + [log_probs.shape[0] - 1]
    kept = [(label, start, end) for label, start, end in zip(labels, starts, ends)]
    words = [(graph.words[label - 1], (start, end)) for label, start, end in kept if label <= len(graph.words)]
    return Heard([w for w, _ in words], float(score), [f for _, f in words])


def _decode_python(
    graph: TokenGraph, log_probs: np.ndarray, blank: np.ndarray, beam: float, max_active: int,
) -> tuple[float, list[int], list[int]] | None:
    """``decode``'s search, as _grammar.c has it, for tests and for want of
    a compiler (much slower)."""
    out_ptr, dst, token, cost, label = (
        graph.out_ptr.tolist(), graph.arc_dst.tolist(), graph.arc_token.tolist(),
        graph.arc_cost.tolist(), graph.arc_label.tolist(),
    )
    history: list[tuple[int, int, int]] = []  # (label, parent, frame)

    def arrivals(paths_e: dict, paths_b: dict, first: bool) -> dict[int, list[tuple[float, int, int]]]:
        """Where paths are between frames, each state's best path and its best
        that last said another piece (the next may not repeat it): (score,
        history, last piece or -1), through arcs without pieces too."""
        arrived: dict[int, list[tuple[float, int, int]]] = {}
        stack: list[int] = []

        def arrive(state: int, score: float, hist: int, last: int) -> None:
            best = arrived.get(state)
            if best is None:
                arrived[state] = [(score, hist, last)]
            else:
                updated = _top_two(best, (score, hist, last))
                if updated is None:
                    return
                arrived[state] = updated
            stack.append(state)

        if first:
            arrive(0, 0.0, -1, -1)
        for arc, (score, hist) in paths_e.items():
            arrive(dst[arc], score, hist, token[arc])
        for state, (score, hist) in paths_b.items():
            arrive(state, score, hist, -1)
        while stack:
            state = stack.pop()
            for arc in range(out_ptr[state], out_ptr[state + 1]):
                if token[arc] < 0:
                    for score, hist, last in list(arrived[state]):
                        arrive(dst[arc], score - cost[arc], hist, last)
        return arrived

    paths_e: dict[int, tuple[float, int]] = {}
    paths_b: dict[int, tuple[float, int]] = {}
    for t in range(log_probs.shape[0]):
        frame = log_probs[t].tolist()
        arrived = arrivals(paths_e, paths_b, t == 0)
        new_e: dict[int, tuple[float, int, int]] = {}
        new_b: dict[int, tuple[float, int]] = {}
        for arc, (score, hist) in paths_e.items():
            new_e[arc] = (score + frame[token[arc]], hist, 0)
        for state, best in arrived.items():
            score, hist, _ = best[0]
            if score + blank[t] > new_b.get(state, (-np.inf,))[0]:
                new_b[state] = (score + float(blank[t]), hist)
            for arc in range(out_ptr[state], out_ptr[state + 1]):
                piece = token[arc]
                if piece < 0:
                    continue
                base = next((b for b in best if b[2] != piece), None)
                if base is None:
                    continue
                score = base[0] - cost[arc] + frame[piece]
                if score > new_e.get(arc, (-np.inf,))[0]:
                    new_e[arc] = (score, base[1], label[arc])
        scores = sorted([v[0] for v in new_e.values()] + [v[0] for v in new_b.values()], reverse=True)
        if not scores:
            return None
        bound = scores[0] - beam
        if len(scores) > max_active:
            bound = max(bound, scores[max_active - 1])
        paths_e = {}
        for arc, (score, hist, said) in new_e.items():
            if score >= bound:
                if said:
                    history.append((said, hist, t))
                    hist = len(history) - 1
                paths_e[arc] = (score, hist)
        paths_b = {state: v for state, v in new_b.items() if v[0] >= bound}
    arrived = arrivals(paths_e, paths_b, log_probs.shape[0] == 0)
    ends = [(best[0][0], best[0][1]) for state, best in arrived.items() if graph.final[state]]
    if not ends:
        return None
    score, hist = max(ends)
    labels, frames = [], []
    while hist >= 0:
        said, hist, t = history[hist]
        labels.append(said)
        frames.append(t)
    return score, labels[::-1], frames[::-1]


def _top_two(best: list, new: tuple) -> list | None:
    """``best`` (the best, and the best with another last piece) with ``new``
    in it, or None when it is no better."""
    first = best[0]
    second = best[1] if len(best) > 1 else None
    if new[2] == first[2]:
        if new[0] <= first[0]:
            return None
        return [new, second] if second is not None else [new]
    if new[0] > first[0]:
        return [new, first]
    if second is None or new[0] > second[0]:
        return [first, new]
    return None


def path_cost(
    log_probs: np.ndarray, vocab: Vocabulary, words: list[Word], free: int = 0, any_cost: float = ANY_COST,
) -> float:
    """How much likelier, in nats, the model's own best reading of the frames
    is than ``words`` said in them: 0 when the words
    are what the model heard, more the further from it. Before them and after
    them is anything, as the graph has it, at ``any_cost`` a frame that isn't
    blank -- but the first ``free`` frames (the wake word) are free.
    Punctuation counts as blank."""
    spelling = PhraseGraph(vocab)
    blank = blank_of(log_probs, vocab)
    # Each frame's best, whatever it is; and as anything around the words.
    piece = np.delete(log_probs, [vocab.blank_id, *vocab.punctuation_ids], axis=1).max(axis=1)
    best = np.maximum(blank, piece)
    around = np.maximum(blank, piece - any_cost)
    around[:free] = best[:free]
    before = np.concatenate([[0.0], np.cumsum(around)])  # frames [0, t)
    # The words' lattices end to end: arcs between nodes.
    arcs: list[tuple[int, int, int]] = []
    node, num_nodes = 0, 1
    for word in words:
        lattice = spelling._lattice((word.text, _spelled(word.mode)))  # pylint: disable=protected-access
        if lattice is None:
            continue
        size, edges = lattice
        mapping = {0: node, 1: num_nodes, **{n: num_nodes + n - 1 for n in range(2, size)}}
        arcs.extend((mapping[a], mapping[b], t) for a, b, t in edges)
        node, num_nodes = num_nodes, num_nodes + size - 1
    frames = log_probs.shape[0]
    if not arcs or not frames:
        return 0.0
    src = np.array([a for a, _, _ in arcs])
    dst = np.array([b for _, b, _ in arcs])
    tok = np.array([t for _, _, t in arcs])
    # A node's blank, or an arc's piece, held for as many frames as it lasts;
    # the first node also reached by anything before. (A piece repeated across
    # arcs without a blank between is allowed: Parakeet's pieces rarely repeat.)
    blank_at = np.full(num_nodes, -np.inf)
    piece_at = np.full(len(arcs), -np.inf)
    into = np.full(num_nodes, -np.inf)
    ended = -np.inf
    for t in range(frames):
        into = blank_at.copy()
        np.maximum.at(into, dst, piece_at)
        into[0] = max(into[0], before[t])
        # The words ended before frame t; anything after.
        ended = max(ended, into[node] + before[frames] - before[t])
        piece_at = np.maximum(piece_at, into[src]) + log_probs[t, tok]
        blank_at = into + blank[t]
    into = blank_at.copy()
    np.maximum.at(into, dst, piece_at)
    ended = max(ended, into[node])
    return max(0.0, float(best.sum() - ended))
