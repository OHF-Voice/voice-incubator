"""Matching sentence triggers that capture free text.

A trigger like ``play {song} in the {room}`` cannot be compiled into a finite
speech-to-phrase grammar: Home Assistant treats every list reference in a
conversation trigger as a wildcard, so the slot matches arbitrary speech. Those
sentences are matched with hassil against the unconstrained transcript instead,
exactly the way Home Assistant's own agent does it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from hassil.expression import (
    Alternative,
    Expression,
    Group,
    ListReference,
    Sequence,
    TextChunk,
)
from hassil.intents import Intents, WildcardSlotList
from hassil.parse_expression import parse_sentence
from hassil.recognize import recognize

_LOGGER = logging.getLogger(__name__)


@dataclass
class WildcardMatch:
    sentence: str
    slots: dict[str, str] = field(default_factory=dict)
    substitutions: int = 0
    """Fixed words the transcript got wrong but that were matched anyway."""

    text: str = ""
    """The utterance with the trigger's own fixed words restored.

    Home Assistant re-matches whatever we send it with plain hassil, so sending
    the raw transcript would lose exactly the commands anchoring just rescued.
    This is the repaired form: literals as the trigger spells them, slots as
    they were heard."""


# A pattern element: a literal word, or the name of a slot that eats >= 1 word.
@dataclass(frozen=True)
class _Slot:
    name: str


def _normalize(word: str) -> str:
    return "".join(c for c in word.lower() if c.isalnum() or c == "'")


def _clean(words: list[str]) -> str:
    """Slot text as spoken, without sentence punctuation the decoder added.

    This value is handed straight to Music Assistant's search, where a trailing
    full stop is just a worse query.
    """
    return " ".join(words).strip().strip(".,!?;:")


def _expand(expression: Expression) -> list[list[str | _Slot]]:
    """Every concrete word sequence a parsed sentence can produce."""
    if isinstance(expression, TextChunk):
        words = [w for w in expression.text.strip().split() if w]
        return [list(words)]
    if isinstance(expression, ListReference):
        return [[_Slot(expression.slot_name)]]
    if isinstance(expression, Alternative):
        out: list[list[str | _Slot]] = []
        for item in expression.items:
            out.extend(_expand(item))
        return out or [[]]
    if isinstance(expression, (Sequence, Group)):
        combos: list[list[str | _Slot]] = [[]]
        for item in expression.items:
            branches = _expand(item)
            combos = [prefix + branch for prefix in combos for branch in branches]
        return combos
    return [[]]


def _collect_list_references(expression: Expression, names: set[str]) -> None:
    """Mirror of Home Assistant's own helper of the same name."""
    if isinstance(expression, Group):
        for item in expression.items:
            _collect_list_references(item, names)
    elif isinstance(expression, ListReference):
        names.add(expression.slot_name)


class WildcardMatcher:
    def __init__(self, language: str = "en") -> None:
        self.language = language
        self.sentences: list[str] = []
        self._intents: Intents | None = None
        self._by_intent: dict[str, str] = {}
        self._patterns: dict[str, list[list[str | _Slot]]] = {}

    def train(self, sentences: list[str]) -> None:
        self.sentences = list(sentences)
        self._by_intent = {}
        self._patterns = {
            sentence: _expand(parse_sentence(sentence).expression)
            for sentence in sentences
        }
        if not sentences:
            self._intents = None
            return

        # One intent per sentence so a match names the sentence it matched.
        intents_dict: dict = {"language": self.language, "intents": {}}
        for index, sentence in enumerate(sentences):
            name = f"wildcard_{index}"
            intents_dict["intents"][name] = {"data": [{"sentences": [sentence]}]}
            self._by_intent[name] = sentence

        intents = Intents.from_dict(intents_dict)

        names: set[str] = set()
        for intent in intents.intents.values():
            for data in intent.data:
                for sentence_expression in data.sentences:
                    _collect_list_references(sentence_expression.expression, names)
        for name in names:
            intents.slot_lists[name] = WildcardSlotList(name)

        self._intents = intents
        _LOGGER.info(
            "Wildcard matcher holds %d sentence(s), %d slot(s)",
            len(sentences),
            len(names),
        )

    def match(self, text: str, max_substitutions: int = 1) -> WildcardMatch | None:
        """Match ``text``, exactly if possible, else allowing wrong fixed words.

        Transcription errors land on the *fixed* words far more than on the
        slots -- "play" comes back as "They", "Lay", "played". hassil is
        all-or-nothing, so one wrong function word loses the whole command and
        its slots with it. The fallback keeps the fixed words anchored to what
        the trigger actually says and lets a bounded number of them be wrong,
        which is the matching-layer equivalent of decoding them under a
        grammar.
        """
        if self._intents is None or not text.strip():
            return None

        result = recognize(text, self._intents, language=self.language)
        if result is not None:
            return WildcardMatch(
                sentence=self._by_intent.get(result.intent.name, result.intent.name),
                slots={
                    name: str(entity.value)
                    for name, entity in (result.entities or {}).items()
                },
                text=text,
            )

        return self._match_anchored(text, max_substitutions)

    def _match_anchored(self, text: str, budget: int) -> WildcardMatch | None:
        words = text.split()
        if not words:
            return None
        normalized = [_normalize(w) for w in words]

        best: tuple[tuple[int, int], WildcardMatch] | None = None
        for sentence, patterns in self._patterns.items():
            for pattern in patterns:
                found = _align(pattern, words, normalized, budget)
                if found is None:
                    continue
                rank, slots, text = found
                if best is None or rank < best[0]:
                    best = (rank, WildcardMatch(sentence, slots, rank[0], text))
        if best is None:
            return None
        _LOGGER.info(
            "Anchored match (%d substitution(s)): %r -> %s",
            best[1].substitutions,
            text,
            best[1].sentence,
        )
        return best[1]


def _align(
    pattern: list[str | _Slot],
    words: list[str],
    normalized: list[str],
    budget: int,
) -> tuple[tuple[int, int], dict[str, str], str] | None:
    """Cheapest alignment of ``pattern`` over all the words, or None.

    A literal costs 0 when it matches and 1 when it does not; a slot takes one
    or more words for free. Both ends are anchored: every word is consumed.
    """
    n = len(words)
    best: tuple[tuple[int, int], dict[str, str]] | None = None

    def walk(
        index: int, position: int, cost: int, matched: int, slots: dict[str, str]
    ) -> None:
        nonlocal best
        if cost > budget:
            return
        if index == len(pattern):
            if position != n:
                return
            rank = (cost, -matched)
            if best is None or rank < best[0]:
                best = (rank, dict(slots))
            return

        element = pattern[index]
        if isinstance(element, _Slot):
            # Shortest span first, so a slot does not swallow a later literal.
            for end in range(position + 1, n + 1):
                slots[element.name] = _clean(words[position:end])
                walk(index + 1, end, cost, matched, slots)
            slots.pop(element.name, None)
            return

        if position >= n:
            return
        hit = normalized[position] == _normalize(element)
        walk(index + 1, position + 1, cost + (0 if hit else 1), matched + hit, slots)

    walk(0, 0, 0, 0, {})
    if best is None:
        return None
    rank, slots = best
    # Repaired utterance: the trigger's literals, the heard slot values.
    repaired = " ".join(
        slots[element.name] if isinstance(element, _Slot) else element
        for element in pattern
    )
    return rank, slots, repaired
