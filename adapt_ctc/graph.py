"""Every phrase the recognizer listens for, compiled into one token graph.

Phrases are inserted into a trie of *units*, so "seventy two", "seventy two
and a half" and "seventy two point five" share their first two words. Each
unit expands into a lattice of every sequence of vocabulary pieces that spells
it, in any letter case:

* a **strict** unit is one word. It must start a word (its first piece carries
  the SentencePiece word marker) and may not break inside. Keywords, fillers
  and numbers are strict.
* a **flexible** unit is one written word of a name, compounds joined
  ("EcoBee" and "Eco-Bee" are both "ecobee"). It may break into words
  anywhere, and only the name's first unit has to start a word, because where
  the model puts word breaks in a name is arbitrary: "EcoBee", "Eco-Bee",
  "Ecobee", "eco bee" and "e co bee" all match the same name.

A unit may have several forms -- a word of a name with its language's
endings, "wohnzimmer" and "wohnzimmers" (morphology.word_forms) -- spelled by
one lattice over their letters, so they share what they have in common.

The compiled graph is a set of arcs (one per piece) between positions; the
positions where phrases end carry what those phrases mean.
"""

from __future__ import annotations

import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

import numpy as np

from .acoustic import Vocabulary
from .numbers import to_python

ROOT = 0


@dataclass(frozen=True)
class Meaning:
    kind: str
    """``keyword``, ``filler``, ``name`` or ``number``."""

    phrase: str
    """The spoken phrase, lowercase words."""

    name_type: str | None = None
    name_id: str | None = None
    scope: str | None = None
    """For a name from an intent's own list, that intent; None if shared."""

    value: Decimal | None = None
    unit: str | None = None
    """For a number said with its unit ("a minute and a half"), the unit."""

    @property
    def meaningful(self) -> bool:
        return self.kind != "filler"

    def label(self) -> str:
        if self.kind == "name":
            return f"{self.name_type} {self.name_id}"
        if self.kind == "number":
            unit = f" {self.unit}" if self.unit else ""
            return f"number {to_python(self.value)}{unit}"
        return f"{self.kind} '{self.phrase}'"


@dataclass
class CompiledGraph:
    arc_token: np.ndarray
    """``[A]`` piece id of each arc."""

    arc_src: np.ndarray
    """``[A]`` position each arc leaves."""

    from_root: np.ndarray
    """``[A]`` arcs that start a phrase."""

    pos_in: np.ndarray
    """``[P, K]`` arcs entering each position, padded with the sentinel ``A``."""

    pos_in_token: np.ndarray
    """``[P, K]`` piece id of each of those arcs, ``-1`` for padding."""

    in_ptr: np.ndarray
    """``[P + 1]`` the same incoming arcs without padding: position ``p``'s
    are ``in_arcs[in_ptr[p]:in_ptr[p + 1]]`` (for the native module)."""

    in_arcs: np.ndarray

    terminals: np.ndarray
    """``[M]`` positions where at least one phrase ends."""

    meanings: list[list[Meaning]]
    """What the phrases ending at each terminal mean."""

    # For the live-state search, which only visits arcs near a live path.

    arc_dst: np.ndarray
    """``[A]`` position each arc enters."""

    out_ptr: np.ndarray
    """``[P + 1]`` arcs leaving position ``p`` are ``out_arcs[out_ptr[p]:out_ptr[p + 1]]``."""

    out_arcs: np.ndarray

    remaining: np.ndarray
    """``[P]`` most pieces any path from the position needs to finish a phrase.
    A partial path costing more than ``gate * (pieces so far + remaining)``
    cannot become a hit, however well the rest of it fits."""

    terminal_of: np.ndarray
    """``[P]`` index into ``terminals``, or -1."""

    root_ptr: np.ndarray
    """``[V + 1]`` arcs that start a phrase, grouped by piece:
    ``root_arcs[root_ptr[v]:root_ptr[v + 1]]`` start with piece ``v``."""

    root_arcs: np.ndarray
    """Sorted by piece, then by ``remaining`` descending."""

    root_reach: np.ndarray
    """``[V]`` most pieces a phrase starting with piece ``v`` can have."""

    @property
    def num_arcs(self) -> int:
        return int(self.arc_token.shape[0])

    @property
    def num_positions(self) -> int:
        return int(self.pos_in.shape[0])


class PhraseGraph:
    def __init__(self, vocab: Vocabulary) -> None:
        self.vocab = vocab
        self._src: list[int] = []
        self._dst: list[int] = []
        self._tok: list[int] = []
        self._num_positions = 1
        self._children: dict[tuple[int, tuple[str, str]], int] = {}
        self._lattices: dict[tuple[str, str], tuple[int, list] | None] = {}
        self._meanings: dict[int, list[Meaning]] = defaultdict(list)
        self.unspeakable: list[str] = []
        """Phrases dropped because no sequence of pieces spells them."""

    def add(self, words: list[str | tuple[str, ...]], meaning: Meaning, flexible: bool = False) -> bool:
        """Listen for ``words`` and report hits on them as ``meaning``.

        Strict words are single words. Flexible words are the units of a name
        (see :func:`text.name_units`). A word may be a tuple of its forms, the
        word as written first: any one of them is heard as it.
        """
        if flexible:
            units = [(word, "flex" if index else "flex-first") for index, word in enumerate(words)]
        else:
            units = [(word, "strict") for word in words]
        lattices = [self._lattice(unit) for unit in units]
        if not units or any(lattice is None for lattice in lattices):
            self.unspeakable.append(" ".join(w if isinstance(w, str) else w[0] for w in words))
            return False

        position = ROOT
        for unit, lattice in zip(units, lattices):
            key = (position, unit)
            child = self._children.get(key)
            if child is None:
                child = self._instantiate(position, lattice)
                self._children[key] = child
            position = child
        if meaning not in self._meanings[position]:
            self._meanings[position].append(meaning)
        return True

    def compile(self) -> CompiledGraph:
        num_arcs = len(self._tok)
        arc_token = np.ascontiguousarray(self._tok, dtype=np.int32)
        arc_src = np.ascontiguousarray(self._src, dtype=np.int32)

        incoming: list[list[int]] = [[] for _ in range(self._num_positions)]
        for arc, dst in enumerate(self._dst):
            incoming[dst].append(arc)
        width = max(1, max((len(arcs) for arcs in incoming), default=1))
        pos_in = np.full((self._num_positions, width), num_arcs, dtype=np.int32)
        for position, arcs in enumerate(incoming):
            pos_in[position, : len(arcs)] = arcs
        token_ext = np.append(arc_token, np.int32(-1)).astype(np.int32)

        in_ptr = np.zeros(self._num_positions + 1, dtype=np.int32)
        np.cumsum([len(arcs) for arcs in incoming], out=in_ptr[1:])
        in_arcs = np.asarray([a for arcs in incoming for a in arcs], dtype=np.int32)

        terminals = sorted(self._meanings)
        terminal_of = np.full(self._num_positions, -1, dtype=np.int32)
        terminal_of[terminals] = np.arange(len(terminals), dtype=np.int32)

        arc_dst = np.ascontiguousarray(self._dst, dtype=np.int32)
        out_order = np.argsort(arc_src, kind="stable").astype(np.int32)
        out_ptr = np.zeros(self._num_positions + 1, dtype=np.int32)
        np.cumsum(np.bincount(arc_src, minlength=self._num_positions), out=out_ptr[1:])

        remaining = self._remaining(incoming, terminal_of >= 0)

        vocab_size = self.vocab.size
        roots = np.nonzero(arc_src == ROOT)[0]
        # By piece, then longest phrase first (the native search stops early).
        roots = roots[np.lexsort((-remaining[arc_dst[roots]], arc_token[roots]))].astype(np.int32)
        root_ptr = np.zeros(vocab_size + 1, dtype=np.int32)
        np.cumsum(np.bincount(arc_token[roots], minlength=vocab_size), out=root_ptr[1:])
        root_reach = np.zeros(vocab_size, dtype=np.int32)
        np.maximum.at(root_reach, arc_token[roots], 1 + remaining[arc_dst[roots]])

        return CompiledGraph(
            arc_token=arc_token,
            arc_src=arc_src,
            from_root=arc_src == ROOT,
            pos_in=pos_in,
            pos_in_token=np.ascontiguousarray(token_ext[pos_in]),
            in_ptr=in_ptr,
            in_arcs=in_arcs,
            terminals=np.asarray(terminals, dtype=np.int32),
            meanings=[self._meanings[t] for t in terminals],
            arc_dst=arc_dst,
            out_ptr=out_ptr,
            out_arcs=out_order,
            remaining=remaining,
            terminal_of=terminal_of,
            root_ptr=root_ptr,
            root_arcs=roots,
            root_reach=root_reach,
        )

    def _remaining(self, incoming: list[list[int]], terminal: np.ndarray) -> np.ndarray:
        """Longest piece count from each position to a phrase end, in reverse
        topological order (positions form a DAG; every leaf is a terminal)."""
        pending = np.bincount(self._src, minlength=self._num_positions).tolist()
        remaining = [0 if is_end else -1 for is_end in terminal.tolist()]
        ready = [p for p, count in enumerate(pending) if count == 0]
        while ready:
            position = ready.pop()
            here = remaining[position]
            for arc in incoming[position]:
                src = self._src[arc]
                if here >= 0 and here + 1 > remaining[src]:
                    remaining[src] = here + 1
                pending[src] -= 1
                if pending[src] == 0:
                    ready.append(src)
        return np.maximum(np.asarray(remaining, dtype=np.int32), 0)

    def _instantiate(self, start: int, lattice: tuple[int, list]) -> int:
        num_nodes, edges = lattice
        # Lattice node 0 is the unit's start, node 1 its end.
        mapping = {0: start}
        for node in range(1, num_nodes):
            mapping[node] = self._num_positions
            self._num_positions += 1
        for src, dst, token in edges:
            self._src.append(mapping[src])
            self._dst.append(mapping[dst])
            self._tok.append(token)
        return mapping[1]

    def _lattice(self, unit: tuple[str | tuple[str, ...], str]) -> tuple[int, list] | None:
        if unit not in self._lattices:
            self._lattices[unit] = self._build_lattice(*unit)
        return self._lattices[unit]

    def _build_lattice(self, spelling: str | tuple[str, ...], mode: str) -> tuple[int, list] | None:
        # The forms' letters as a trie: node 0 starts the unit, and every
        # form's last letter also leads to its end (``end``).
        children: list[dict[str, int]] = [{}]
        complete: set[int] = set()
        for form in (spelling,) if isinstance(spelling, str) else spelling:
            node = 0
            for letter in self._fold(form):
                following = children[node].get(letter)
                if following is None:
                    following = len(children)
                    children.append({})
                    children[node][letter] = following
                node = following
            if node:
                complete.add(node)
        if not complete:
            return None
        end = len(children)
        pieces = self.vocab.pieces
        space_id = self.vocab.space_id
        longest = self.vocab.max_piece_length

        def spelled(node: int, chunk: str = ""):
            """Every node a chunk of up to ``longest`` letters leads to."""
            for letter, following in children[node].items():
                yield chunk + letter, following
                if len(chunk) + 1 < longest:
                    yield from spelled(following, chunk + letter)

        edges: list[tuple[int, int, int]] = []
        next_node = end + 1
        for node in range(len(children)):
            if node == 0:
                starts, continues = True, mode == "flex"
            else:
                starts, continues = mode != "strict", True
            # A bare word marker followed by a continuation piece also starts
            # a word ("▁" "E" "co"); only where a unit begins, to keep names
            # from doubling in size for a rare way of spelling.
            marker = None
            if node == 0 and space_id is not None:
                marker = next_node
                next_node += 1
                edges.append((node, marker, space_id))
            for chunk, following in spelled(node):
                targets = (following, end) if following in complete else (following,)
                if starts:
                    for token in pieces.get((chunk, True), ()):
                        edges.extend((node, target, token) for target in targets)
                for token in pieces.get((chunk, False), ()):
                    for target in targets:
                        if continues:
                            edges.append((node, target, token))
                        if marker is not None:
                            edges.append((marker, target, token))

        # Keep only arcs on some path from the unit's start to its end.
        forward = _reachable(0, [(s, d) for s, d, _ in edges])
        if end not in forward:
            return None
        backward = _reachable(end, [(d, s) for s, d, _ in edges])
        live = [e for e in edges if e[0] in forward and e[1] in backward]

        # Renumber: start 0, end 1, everything else after.
        order = {0: 0, end: 1}
        for src, dst, _ in live:
            for node in (src, dst):
                if node not in order:
                    order[node] = len(order)
        return len(order), [(order[s], order[d], t) for s, d, t in live]

    def stats(self) -> dict[str, int]:
        return {
            "arcs": len(self._tok),
            "positions": self._num_positions,
            "terminals": len(self._meanings),
        }

    def _fold(self, text: str) -> str:
        """Letters the vocabulary can spell, accents folded where it cannot."""
        known = self.vocab.characters
        out = []
        for ch in text.lower():
            if ch in known:
                out.append(ch)
                continue
            out.extend(c for c in unicodedata.normalize("NFD", ch) if c in known)
        return "".join(out)


def _reachable(start: int, pairs: list[tuple[int, int]]) -> set[int]:
    following: dict[int, list[int]] = defaultdict(list)
    for src, dst in pairs:
        following[src].append(dst)
    seen = {start}
    stack = [start]
    while stack:
        for nxt in following[stack.pop()]:
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen
