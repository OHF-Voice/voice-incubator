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

import os
import random
import subprocess
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


# ------------------------------------------------------------------ the FST


def write_fst(graph: WordGraph, vocab: Vocabulary, directory: Path, fst_bin: Path | None = None) -> Path:
    """The graph as sherpa-onnx's CTC decoding graph: each word spelled with
    every sequence of the model's pieces that spells it (graph.PhraseGraph's
    lattices), composed with a CTC topology in which punctuation is blank.
    Input labels are piece ids + 1, output labels word ids (``words.txt``:
    id, text, mode).
    Needs OpenFst's fstcompile, fstarcsort and fstcompose (``fst_bin``)."""
    directory.mkdir(parents=True, exist_ok=True)
    spelling = PhraseGraph(vocab)
    words = graph.words
    word_ids = {w: i + 1 for i, w in enumerate(words)}
    (directory / "words.txt").write_text(
        "".join(f"{i}\t{w.text}\t{w.mode}\n" for w, i in word_ids.items()), encoding="utf-8"
    )
    next_state = graph.num_states
    unspeakable = set()
    with open(directory / "G.txt", "w", encoding="utf-8") as out:
        anything = [t for t in sorted(vocab.id2piece) if t != vocab.blank_id and not vocab.is_punctuation(t)]
        for src, dst, word, cost in graph.arcs:
            if word is None:
                out.write(f"{src} {dst} 0 0 {cost:g}\n")
                continue
            if word == ANY:
                out.write("".join(f"{src} {dst} {t + 1} 0 {cost:g}\n" for t in anything))
                continue
            lattice = spelling._lattice((word.text, _spelled(word.mode)))  # pylint: disable=protected-access
            if lattice is None:
                unspeakable.add(word.text)
                continue
            num_nodes, edges = lattice
            mapping = {0: src, 1: dst}
            for node in range(2, num_nodes):
                mapping[node] = next_state
                next_state += 1
            for a, b, token in edges:
                label = word_ids[word] if a == 0 else 0
                out.write(f"{mapping[a]} {mapping[b]} {token + 1} {label} {cost if a == 0 else 0:g}\n")
        for state in sorted(graph.finals):
            out.write(f"{state}\n")
    skip = sorted({vocab.blank_id, *vocab.punctuation_ids})
    tokens = [t for t in sorted(vocab.id2piece) if t not in skip]
    with open(directory / "H.txt", "w", encoding="utf-8") as out:
        # State 0 is blank (or punctuation); each piece a state of its own,
        # held for as many frames as it lasts, then back to blank.
        for token in skip:
            out.write(f"0 0 {token + 1} 0\n")
        for index, token in enumerate(tokens, 1):
            out.write(f"0 {index} {token + 1} {token + 1}\n{index} {index} {token + 1} 0\n{index} 0 0 0\n")
        out.write("0\n" + "".join(f"{index}\n" for index in range(1, len(tokens) + 1)))
    tools = Path(fst_bin).resolve() if fst_bin else None
    env = dict(os.environ)
    if tools is not None and (tools.parent / "lib").is_dir():
        env["LD_LIBRARY_PATH"] = f"{tools.parent / 'lib'}:{env.get('LD_LIBRARY_PATH', '')}"

    def tool(name: str) -> str:
        return str(tools / name) if tools else name

    def run(command: str) -> None:
        subprocess.run(["bash", "-o", "pipefail", "-c", command], check=True, cwd=directory, env=env)

    run(f"{tool('fstcompile')} H.txt | {tool('fstarcsort')} --sort_type=olabel > H.fst")
    run(f"{tool('fstcompile')} G.txt | {tool('fstarcsort')} --sort_type=ilabel > G.fst")
    run(f"{tool('fstcompose')} H.fst G.fst > HG.fst")
    for name in ("H.txt", "G.txt", "H.fst", "G.fst"):
        (directory / name).unlink()
    if unspeakable:
        (directory / "unspeakable.txt").write_text("\n".join(sorted(unspeakable)) + "\n", encoding="utf-8")
    return directory / "HG.fst"


def _spelled(mode: str) -> str:
    return "strict" if mode == "wake" else mode


# ------------------------------------------------------------------ decoding


class GrammarDecoder:
    """Parakeet's CTC head held to a ``write_fst`` graph (sherpa-onnx's FST
    decoder): a call transcribed as one of the sentences, or None when no
    sentence reaches its end."""

    def __init__(self, model_dir: Path, graph_dir: Path, threads: int = 1, max_active: int = 3000) -> None:
        import sherpa_onnx  # pylint: disable=import-outside-toplevel
        from sherpa_onnx.offline_recognizer import _Recognizer  # pylint: disable=import-outside-toplevel

        config = sherpa_onnx.OfflineRecognizerConfig(
            feat_config=sherpa_onnx.FeatureExtractorConfig(sampling_rate=16000, feature_dim=80),
            model_config=sherpa_onnx.OfflineModelConfig(
                nemo_ctc=sherpa_onnx.OfflineNemoEncDecCtcModelConfig(model=str(model_dir / "model.onnx")),
                tokens=str(model_dir / "tokens.txt"), num_threads=threads, model_type="nemo_ctc",
            ),
            ctc_fst_decoder_config=sherpa_onnx.OfflineCtcFstDecoderConfig(
                graph=str(graph_dir / "HG.fst"), max_active=max_active,
            ),
            decoding_method="greedy_search",
        )
        self._recognizer = _Recognizer(config)
        self.words: dict[int, tuple[str, str]] = {}
        for line in (graph_dir / "words.txt").read_text(encoding="utf-8").splitlines():
            index, text, mode = line.split("\t")
            self.words[int(index)] = (text, mode)

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000) -> list[tuple[str, str]] | None:
        """The sentence's words, each ``(text, mode)``."""
        stream = self._recognizer.create_stream()
        stream.accept_waveform(sample_rate, np.ascontiguousarray(audio, dtype=np.float32))
        self._recognizer.decode_stream(stream)
        ids = list(stream.result.words)
        if not ids and not stream.result.tokens:
            return None
        return [self.words[i] for i in ids]


def path_cost(
    log_probs: np.ndarray, vocab: Vocabulary, words: list[tuple[str, str]], free: int = 0, any_cost: float = ANY_COST,
) -> float:
    """How much likelier, in nats, the model's own best reading of the frames
    is than ``words`` (each ``(text, mode)``) said in them: 0 when the words
    are what the model heard, more the further from it. Before them and after
    them is anything, as the graph has it, at ``any_cost`` a frame that isn't
    blank -- but the first ``free`` frames (the wake word) are free.
    Punctuation counts as blank."""
    spelling = PhraseGraph(vocab)
    blank = log_probs[:, vocab.blank_id]
    if vocab.punctuation_ids:
        blank = np.maximum(blank, log_probs[:, vocab.punctuation_ids].max(axis=1))
    # Each frame's best, whatever it is; and as anything around the words.
    piece = np.delete(log_probs, [vocab.blank_id, *vocab.punctuation_ids], axis=1).max(axis=1)
    best = np.maximum(blank, piece)
    around = np.maximum(blank, piece - any_cost)
    around[:free] = best[:free]
    before = np.concatenate([[0.0], np.cumsum(around)])  # frames [0, t)
    # The words' lattices end to end: arcs between nodes.
    arcs: list[tuple[int, int, int]] = []
    node, num_nodes = 0, 1
    for text, mode in words:
        lattice = spelling._lattice((text, _spelled(mode)))  # pylint: disable=protected-access
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
    return float(best.sum() - ended)
