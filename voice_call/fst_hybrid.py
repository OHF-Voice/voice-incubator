"""Pick a wildcard sentence by decoding it, not by string-matching a transcript.

Every wildcard trigger gets its own grammar: the literal words are constrained
to exactly what the trigger says, and each ``{slot}`` becomes a free loop over
the whole token alphabet. Decoding the command audio against all of them and
taking the cheapest answers "which sentence was this" from acoustics.

That is the difference that matters for keywords. Matching a transcript cannot
tell "play the artist X" from "play the album X" -- the two differ by one word
and a transcript gets that word wrong -- but the two grammars score four times
apart on the same audio.

Two details are load-bearing:

* **Beam.** The slot loop makes the search enormous. Exact search costs ~9 s per
  decode; ``beam=10`` costs ~20 ms for an identical answer.
* **Slot penalty.** A free loop may pick any tokenization, so it fits the audio
  at least as well as a required literal and the most permissive sentence wins
  by default. Charging per slot-emitted token restores the preference for
  sentences whose literals actually explain the audio.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from hassil.expression import (
    Alternative,
    Expression,
    Group,
    ListReference,
    Sequence,
    TextChunk,
)
from hassil.parse_expression import parse_sentence
from speech_to_phrase.grammar import Grammar
from speech_to_phrase.templates import EPS, Fst

from .wildcards import _clean, _Slot, _expand

SPM_SPACE = "\u2581"
"""SentencePiece word-start marker."""

_LOGGER = logging.getLogger(__name__)

DEFAULT_BEAM = 10.0
DEFAULT_SLOT_PENALTY = 6.0


@dataclass
class GrammarMatch:
    sentence: str
    slots: dict[str, str] = field(default_factory=dict)
    text: str = ""
    """What the grammar actually decoded, fragments and all."""

    canonical: str = ""
    """The trigger's own literals with the decoded slot values substituted in.
    Home Assistant re-matches with plain hassil, so this is what to send it."""

    cost: float = 0.0
    slot_tokens: int = 0


class _Builder:
    """Walks a parsed sentence into an Fst, keeping branches as branches.

    Optionals and alternatives stay inside one grammar rather than being
    expanded into several, which is what keeps the build affordable.
    """

    def __init__(self, tokenizer, num_tokens: int) -> None:
        self.tokenizer = tokenizer
        self.num_tokens = num_tokens
        self.fst = Fst()
        self._next = 1
        self.fst.states.add(0)

    def _state(self) -> int:
        state = self._next
        self._next += 1
        self.fst.states.add(state)
        return state

    def build(self, expression: Expression) -> Fst:
        end = self._add(expression, 0)
        self.fst.final_states.add(end)
        return self.fst

    def _add(self, expression: Expression, state: int) -> int:
        if isinstance(expression, TextChunk):
            words = expression.text.strip()
            if not words:
                return state
            for token in self.tokenizer.text_to_ids(words):
                nxt = self._state()
                self.fst.add_edge(state, nxt, in_label=token, out_label=token)
                state = nxt
            return state

        if isinstance(expression, ListReference):
            # One token minimum, then as many more as the audio wants.
            loop = self._state()
            for token in range(self.num_tokens):
                self.fst.add_edge(state, loop, in_label=token, out_label=token)
                self.fst.add_edge(loop, loop, in_label=token, out_label=token)
            return loop

        if isinstance(expression, Alternative):
            end = self._state()
            for item in expression.items:
                branch = self._add(item, state)
                self.fst.add_edge(branch, end, in_label=EPS, out_label=EPS)
            return end

        if isinstance(expression, (Sequence, Group)):
            for item in expression.items:
                state = self._add(item, state)
            return state

        return state


class FstHybrid:
    """One free-loop grammar per wildcard sentence, compiled once and cached."""

    def __init__(
        self,
        cache_dir: Path,
        beam: float = DEFAULT_BEAM,
        slot_penalty: float = DEFAULT_SLOT_PENALTY,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.beam = beam
        self.slot_penalty = slot_penalty
        self.sentences: list[str] = []
        self._grammars: list[tuple[str, Grammar]] = []
        self._patterns: dict[str, list[list[str | _Slot]]] = {}
        self._tokenizer = None

    @property
    def available(self) -> bool:
        return bool(self._grammars)

    def train(self, sentences: list[str], tokenizer, num_tokens: int) -> None:
        self.sentences = list(sentences)
        self._tokenizer = tokenizer
        self._grammars = []
        self._patterns = {}
        if not sentences:
            return

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        built = cached = 0
        for sentence in sentences:
            self._patterns[sentence] = _expand(parse_sentence(sentence).expression)
            key = hashlib.sha1(
                f"{sentence}\x00{num_tokens}\x00{tokenizer.blank_id}".encode()
            ).hexdigest()[:16]
            path = self.cache_dir / f"{key}.fst"
            if path.exists():
                try:
                    self._grammars.append((sentence, Grammar.load(path)))
                    cached += 1
                    continue
                except (RuntimeError, OSError) as err:
                    _LOGGER.warning("Rebuilding unusable cache %s: %s", path.name, err)

            fst = _Builder(tokenizer, num_tokens).build(
                parse_sentence(sentence).expression
            )
            grammar = Grammar.build(num_tokens, tokenizer.blank_id, fst)
            try:
                grammar.save(path)
            except (RuntimeError, OSError) as err:
                _LOGGER.warning("Could not cache %s: %s", path.name, err)
            self._grammars.append((sentence, grammar))
            built += 1

        _LOGGER.info(
            "Wildcard grammars ready: %d built, %d from cache", built, cached
        )

    def select(self, log_probs: np.ndarray) -> GrammarMatch | None:
        """Cheapest sentence for this audio, with its slots filled in."""
        if not self._grammars or self._tokenizer is None:
            return None

        best: tuple[float, GrammarMatch] | None = None
        for sentence, grammar in self._grammars:
            ids, cost, _ = grammar.decode(
                log_probs, self._tokenizer.blank_id, beam=self.beam
            )
            if cost is None or not ids:
                continue
            found = self._extract(sentence, list(ids))
            if found is None:
                continue
            slots, literal_tokens, canonical = found
            if any(not value for value in slots.values()):
                continue
            slot_tokens = max(0, len(ids) - literal_tokens)
            ranked = cost + self.slot_penalty * slot_tokens
            if best is None or ranked < best[0]:
                best = (
                    ranked,
                    GrammarMatch(
                        sentence=sentence,
                        slots=slots,
                        text=self._tokenizer.ids_to_text(ids),
                        canonical=canonical,
                        cost=cost,
                        slot_tokens=slot_tokens,
                    ),
                )
        return None if best is None else best[1]

    def _extract(
        self, sentence: str, ids: list[int]
    ) -> tuple[dict[str, str], int, str] | None:
        """Split a decoded token sequence into literals and slot contents.

        Splitting the decoded *text* on words does not work: the grammar
        constrains "play" but a following slot is free to add "ing", so the
        text reads "playing". The literals are exact at the token level, so the
        split happens there.
        """
        assert self._tokenizer is not None
        for pattern in self._patterns.get(sentence, []):
            result = self._extract_pattern(pattern, ids)
            if result is not None:
                return result
        return None

    def _slot_text(self, ids: list[int]) -> str:
        """Render slot tokens, dropping fragments that belong to the literal.

        The grammar pins "play", but the loop that follows is free to take the
        trailing subwords of what was actually said ("play" -> "playing"), so a
        slot can open mid-word. Anything before the first word boundary is not
        part of the slot value.
        """
        assert self._tokenizer is not None
        pieces = getattr(self._tokenizer, "_id2piece", None)
        if pieces:
            start = 0
            while start < len(ids) and not pieces.get(ids[start], "").startswith(
                SPM_SPACE
            ):
                start += 1
            if start < len(ids):
                ids = ids[start:]
        return _clean(self._tokenizer.ids_to_text(ids).split())

    def _extract_pattern(
        self, pattern: list[str | _Slot], ids: list[int]
    ) -> tuple[dict[str, str], int, str] | None:
        assert self._tokenizer is not None
        slots: dict[str, str] = {}
        position = 0
        literal_tokens = 0
        pending: _Slot | None = None

        for element in pattern:
            if isinstance(element, _Slot):
                pending = element
                continue

            wanted = self._tokenizer.text_to_ids(element)
            literal_tokens += len(wanted)
            if pending is None:
                if ids[position : position + len(wanted)] != wanted:
                    return None
                position += len(wanted)
                continue

            # A slot precedes this literal: the slot runs up to wherever the
            # literal next appears, and must take at least one token.
            found = -1
            for start in range(position + 1, len(ids) - len(wanted) + 1):
                if ids[start : start + len(wanted)] == wanted:
                    found = start
                    break
            if found < 0:
                return None
            slots[pending.name] = self._slot_text(ids[position:found])
            position = found + len(wanted)
            pending = None

        if pending is not None:
            if position >= len(ids):
                return None
            slots[pending.name] = self._slot_text(ids[position:])
            position = len(ids)

        if position != len(ids):
            return None
        canonical = " ".join(
            slots[el.name] if isinstance(el, _Slot) else el for el in pattern
        )
        return slots, literal_tokens, canonical
