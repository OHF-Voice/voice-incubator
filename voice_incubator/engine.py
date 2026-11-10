"""Command recognition: from a call's audio to the command to run.

The command is recognized with adapt-ctc on Parakeet's CTC output:

1. **Where the command starts.** The call is decoded from half a second
   before the wake word (Kaldi's placement), and the wake phrase is found
   again in the CTC evidence; the command is the frames after it. Cutting at
   Kaldi's own end instead lands 0.3-0.4 s late and eats the first word of
   the command: on the office-multicap corpus that was 76.2% correct against
   89.6% (see adapt_ctc/README.md, "Where the command starts").
2. **Example sentences first.** A command's examples are searched for in the
   CTC evidence on every call, and one said word for word wins over keyword
   matches. Then keyword intents; then, when nothing matched, the TDT
   transcript against the examples, word for word. Home Assistant's sentence
   triggers come before them all (voice_incubator/triggers.py): theirs are
   examples too, and one with free text is matched on the transcript first.
3. **A doubtful match is heard again.** One that costs as much as a word it
   left unexplained ("we resume the chicken pder" as resuming the music,
   "chicken" left over), free text not counted, runs only when the TDT or STT
   transcript agrees: the same matcher on the transcript finds the same
   command. A command it finds
   instead runs in its place; finding none, nothing runs. And a name is
   stronger evidence than its absence: a match that named nothing, with a
   word left over, of a command with a version that takes a device, area or
   floor, gives way to that version when the transcript names one ("set bed
   light to green", heard as "set and light to green").
4. **Where it is available.** Matches are taken best first, skipping commands
   that are not heard here: another area's own, or the whole home's turned
   off in this one. Of matches as good as each other, and of example
   sentences a copy also has, this area's own goes first.
5. **Free text** is re-read from the TDT transcript over the same stretch of
   audio, which spells far better than the CTC head ("lasagna", not "Lasaga").
6. **When nothing matched**, the closest command is named: from the keyword
   evidence (what was heard, and what was missing), or else by meaning, with
   EmbeddingGemma against the commands' descriptions and examples. That is
   only ever a hint; the embedding never runs a command.
"""

from __future__ import annotations

import copy
import difflib
import logging
import re
import threading
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import numpy as np

from adapt_ctc.acoustic import CtcModel
from adapt_ctc.config import WakeConfig, parse_config
from adapt_ctc.matcher import IntentResult, Recognition, Recognizer
from adapt_ctc.morphology import alternated, alternations, elided, elisions, endings, word_forms
from adapt_ctc.report import to_json
from adapt_ctc.text import name_units, sentence_key, spoken_words
from adapt_ctc.wake import LEAD, WakeVerifier

from adapt_ctc.config import ConfigError, _phrase

from . import compound, context, kaldi_mode, languages, skills, triggers
from .commands import (
    DYNAMIC_LISTS, NAME_SLOTS, Behavior, Command, base_config, command_of, match_slots, never_mind, says,
)
from .fallback import kin, label_of
from .store import Compiled, Device, phrase_key
from .voices import TypedText, VoiceChoice

_LOGGER = logging.getLogger(__name__)

SAMPLE_RATE = 16000

# How Parakeet writes "okay nabu": it has no word "Nabu". Placement only, so no
# confusables: Kaldi has already decided it was the wake word.
WAKE_PHRASES = ("okay nabu", "okay naboo", "ok nabu", "ok naboo", "okay now")
SPLIT_BOUND = 0.3
"""Seconds past Kaldi's wake word end that the wake phrase may still end."""
PARTIAL_WAKE_PHRASES = ("okay now",)
"""Wake phrases the CTC head may have heard in only part of the wake word
("okay now" of "okay Na|boo"): the command starts at the later of where it
ends and Kaldi's end. Half the corpus's woken calls place "okay now", Kaldi's
end a median 0.29 s later; starting there, 88.6% of them came out right
rather than 87.2%, none worse (DESIGN.md)."""

WORD_SECONDS = 0.5
"""How long a transducer's word is taken to be when it is placed in free
text: its end may run on through the silence after it."""

TDT_LEAD_IN = 0.5
"""The TDT head on a call's command starts this long before the cut, on the
encoder run the CTC evidence came from (``CallTdt``): started at the cut,
with nothing before it, the first word is the one it gets wrong ("Get
groceries" for "add get groceries")."""
TDT_WORDS_AFTER = 0.2
"""...and its words are those whose middle is at least this long after the
cut: before that is the end of the wake word ("now we", "Abu"), the cut
landing a little early -- but later, real first words go too, worse than a
stray word before them. On the office-multicap corpus (the four VPEs' AGC
channel, five sessions, 3,664 commands), against the head on the command
alone: the command words' error rate 19.4% -> 16.7%, the first word right
70.8% -> 76.2% (no speaker worse), commands word for word 32.7% -> 36.6%,
and 30 ms rather than 174 ms (one encoder run). 0.28 s takes out more of the
wake word (41.3% word for word) but loses first words (72.9%)."""

SENTENCE_LEAD_IN = 0.3
"""The call is heard as one of the home's sentences (``settings.grammar``)
from this long before the cut, which is free: the end of the wake word, the
cut landing a little early. On the office-multicap corpus's mike-merged, as
good as the wake phrase in the graph and the whole call (86.0% against 85.9%
of the commands right at 10 nats), which a custom wake word wouldn't have, and
faster; the whole call without the wake phrase was 81.4%."""

SENTENCE_AGREEMENT = 0.5
"""...and taken only when its letters are at least this alike to what the
CTC head heard on its own (difflib's ratio). Far-off talk leaves the model
unsure of every frame, so a short sentence fits it cheaply: in conversation
played into the office-multicap room, 43 of 400 false wakes ran a command
as a sentence -- "like" heard as "more like this" over a transcript of
nothing at all -- and 9 with this. On the corpus's commands it cost 0.2
points (86.2% -> 86.0% right, 4.7% -> 4.6% the wrong thing done)."""

# Naming the closest command by meaning: only when it is this similar, and
# this much closer than the runner-up (another command or an everyday
# request). Hints only, so looser than adapt-ctc's acceptance margin.
HINT_MIN_SIMILARITY = 0.6
HINT_MARGIN = 0.02
HINT_COVERAGE = 0.5
"""A command is named from keyword evidence only when what it listens for
(keywords, names, numbers) covers at least this share of the words that are
not fillers: "set" alone does not make "set a timer" a temperature."""


@dataclass
class Satellite:
    """Who asked, as Home Assistant sees it."""

    key: str
    name: str
    area_id: str | None = None
    area_name: str | None = None
    floor_id: str | None = None
    floor_name: str | None = None
    device_id: str | None = None
    """Its device in Home Assistant (the Voice Incubator integration's)."""
    timer_list: str | None = None
    """Its voice_timer_list entity."""
    media_player: str | None = None
    """Its Music Assistant player (its Sendspin client's)."""
    voice: VoiceChoice = field(default_factory=VoiceChoice)
    """Home Assistant STT and TTS entities, when not the built-in models."""
    device_media_player: str | None = None
    """Its own media player in Home Assistant (ESPHome's), where TTS speaks."""
    skills: frozenset[str] | None = None
    """The skills that answer here (voice_incubator/skills/); None: every one."""
    settings: dict[str, Any] = field(default_factory=dict)
    """Its area's settings over the whole home's (voice_incubator/areas.py)."""
    night: bool = False
    """Its area's night mode is on (voice_incubator/night.py)."""
    beeps: bool = True
    """It plays the acknowledgement sound for a command that beeps. Home
    Assistant's satellites (voice_incubator/wyoming_server.py) can't be told to:
    there, such a command says its brief response."""

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.key,
            "name": self.name,
            "area_id": self.area_id,
            "area_name": self.area_name,
            "floor_id": self.floor_id,
            "floor_name": self.floor_name,
            "device_id": self.device_id,
            "timer_list": self.timer_list,
            "media_player": self.media_player,
            "device_media_player": self.device_media_player,
            "skills": sorted(self.skills) if self.skills is not None else None,
            "night": self.night,
        }

    def available(self, command: Command) -> Behavior | None:
        """What a command does here; None where it is not heard: another
        area's own, or the whole home's turned off in this area."""
        if command.area is not None:
            here = command.area == self.area_id
        else:
            here = self.area_id is None or self.area_id not in command.off
        return command.behavior if here else None

    def picked(self, command: Command) -> bool:
        """It is this satellite's area's own."""
        return command.area is not None and command.area == self.area_id

    def first(self, commands: list[Command]) -> list[Command]:
        """The ones available here, the area's own first."""
        return sorted((c for c in commands if self.available(c) is not None), key=lambda c: not self.picked(c))

    def why_unavailable(self, command: Command) -> str:
        return "another area's own" if command.area is not None else "turned off in this area"

    def not_here(self) -> str:
        """What it says of a command it heard that isn't heard here: "That
        command isn't available in the Kitchen."."""
        say = languages.speech()
        return say.not_available(say.place([("area", self.area_name)]) if self.area_name else "")


@dataclass
class Decision:
    """What a command's audio comes to."""

    recognition: dict[str, Any]
    """adapt-ctc's report of the recognition."""

    command: Command | None = None
    behavior: Behavior | None = None

    intent: IntentResult | None = None
    slots: dict[str, Any] = field(default_factory=dict)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    """Matches passed over: not available on this satellite."""

    hint: dict[str, Any] | None = None
    refused: dict[str, Any] | None = None
    """Understood, but not possible as said ("the fan in the office" when
    the office has no fan): the hint, with nothing else tried."""
    message: str | None = None
    """What to say when nothing will run."""

    text_from_tdt: dict[str, str] = field(default_factory=dict)
    """Free-text slots re-read from the TDT transcript (or the STT entity's):
    slot -> as the CTC head heard it."""

    text_source: str = "TDT"
    """What wrote the transcript: the TDT head, or an STT entity."""

    matched_by: str = "ctc"
    """What found the command: the CTC matcher on the audio (``ctc``), the
    same matcher on the TDT or STT transcript (``transcript``), hassil on a
    transcript (``trigger``: Home Assistant's trigger with free text), the
    fallback (``meaning``, ``gliner``), or nothing: Never mind instead
    (``never_mind``, ``instead`` says why)."""
    transcript_pass: dict[str, Any] | None = None
    """Nothing matched in the audio, or what did was doubtful: what the
    keyword matcher made of the transcript."""
    doubt: dict[str, Any] | None = None
    """A match heard again with the transcript (``why``): it cost as much as
    a word it left unexplained (``cost``) -- and then the transcript
    ``agrees``, another command ``replaced`` it, one was ``refused`` as said,
    or none was found (``unconfirmed``) -- or it named nothing where a
    version of it takes a name (``unnamed``), and then the transcript
    ``named`` one, or it was ``kept``. With the command, its cost and the
    transcript."""
    fallback: dict[str, Any] | None = None
    """What the fallback tried, when nothing matched (voice_incubator/fallback.py)."""

    doubted: "Decision | None" = None
    """A doubtful match its transcript didn't confirm (``doubt``), kept for
    what may still confirm it: the list it acts on (server.VoiceIncubatorApp._listed)."""

    instead: dict[str, Any] | None = None
    """Never mind ran instead of saying it wasn't understood (``why``): it
    was the ``closest`` command, or ``nothing_heard`` -- not a word in either
    transcript. With the ``hint`` it would have been."""

    sentence: dict[str, Any] | None = None
    """The home's sentence the audio was heard as (``settings.grammar``):
    its ``text``, its ``cost`` against what the model heard, and whether it
    was ``taken`` -- matched as typed -- or the passes after it decided
    (too costly, or nothing that runs here)."""

    answer: dict[str, Any] | None = None
    """No command: a question, answered by a skill (voice_incubator/skills/)."""

    asked: Any = None
    """What the conversation agent made of it (agent.Reply), once it was
    asked and has answered: whether it asked something back."""

    outcome: str | None = None
    """How the command it ran turned out (actions.Outcome.name), once it ran."""
    choices: list[dict[str, Any]] | None = None
    """Which of these (timers, alarms, reminders) it meant, asked when it
    ran (actions.Outcome.choices): answered on the reopened microphone."""

    then: "Decision | None" = None
    """The second of two commands said together (voice_incubator/compound.py),
    run after this one."""
    compound: dict[str, Any] | None = None
    """How the sentence was split in two (``joiner``, each clause's
    ``texts``, which ``borrowed`` words, the ``first`` clause's own
    recognition), or that it couldn't be (``split``: false; ``half``: the
    command that would have done half of it)."""

    timings: dict[str, float] = field(default_factory=dict)

    @property
    def transcript(self) -> str:
        return self.recognition.get("tdt_transcript") or self.recognition.get("transcript") or ""

    def to_json(self) -> dict[str, Any]:
        return {
            "command": self.command.id if self.command else None,
            # A sentence trigger's automation, by name: its id is no name.
            **({"title": self.command.title} if self.command and self.command.behavior.trigger and self.command.title else {}),
            "description": self.command.description if self.command else None,
            "does": self.behavior.does if self.behavior else None,
            "slots": self.slots,
            "skipped": self.skipped,
            "matched_by": self.matched_by if self.command else None,
            "fallback": self.fallback,
            **({"sentence": self.sentence} if self.sentence is not None else {}),
            **({"instead": self.instead} if self.instead is not None else {}),
            "transcript_pass": self.transcript_pass,
            "doubt": self.doubt,
            "answer": self.answer,
            **({"compound": self.compound} if self.compound is not None else {}),
            **({"then": self.then.to_json()} if self.then is not None else {}),
            "hint": self.hint,
            "message": self.message,
            "text_from_tdt": self.text_from_tdt,
            "text_source": self.text_source,
            "recognition": self.recognition,
            "timings": {k: round(v, 4) for k, v in self.timings.items()},
        }


@dataclass
class Split:
    """Where the command starts in a call, in seconds from its first sample."""

    offset: float
    """Where decoding started: half a second before Kaldi's wake word."""

    cut: float
    kaldi_split: float
    phrase: str | None = None
    wake_start: float | None = None
    wake_end: float | None = None
    cost: float | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "offset": round(self.offset, 3),
            "cut": round(self.cut, 3),
            "kaldi_split": round(self.kaldi_split, 3),
            "phrase": self.phrase,
            "wake_start": None if self.wake_start is None else round(self.wake_start, 3),
            "wake_end": None if self.wake_end is None else round(self.wake_end, 3),
            "cost": None if self.cost is None else round(self.cost, 3),
        }


class CallTdt:
    """The TDT head on a call's command (``command``: the audio from the
    cut) as it is heard best: decoded from ``TDT_LEAD_IN`` before the cut,
    the encoder having heard ``call`` (the call from where the CTC evidence
    was decoded) -- with ``shared``, the head's encoder run for the CTC
    evidence, so one run for both -- its words those from
    ``TDT_WORDS_AFTER`` past the cut, timed from the cut as the command's CTC
    evidence is. Other audio (a clause of two commands said together) is
    the head's own."""

    source = "TDT"

    def __init__(self, head: Any, call: np.ndarray, cut: float, command: np.ndarray, shared: bool = True) -> None:
        self.head = head
        self.call = call
        self.cut = cut
        """Seconds into ``call``."""
        self.command = command
        self.shared = shared
        self._heard: tuple[str, list[tuple[str, float, float]]] | None = None

    def transcribe(self, audio: np.ndarray) -> str:
        return self.transcribe_words(audio)[0]

    def transcribe_words(self, audio: np.ndarray) -> tuple[str, list[tuple[str, float, float]]]:
        if audio is not self.command:
            return self.head.transcribe_words(audio)
        if self._heard is None:
            start = max(0.0, self.cut - TDT_LEAD_IN)
            if self.shared:
                _text, words = self.head.transcribe_words(self.call, start=start)
            else:  # a head of its own: encoded again, from there
                _text, words = self.head.transcribe_words(self.call[int(start * SAMPLE_RATE) :])
                words = [(w, s + start, e + start) for w, s, e in words]
            after = self.cut + TDT_WORDS_AFTER
            kept = [(w, s - self.cut, e - self.cut) for w, s, e in words if (s + min(e, s + WORD_SECONDS)) / 2 >= after]
            self._heard = (" ".join(w for w, _, _ in kept), kept)
        return self._heard


class CachedEmbedder:
    """Embeds documents (examples, descriptions, everyday requests) once.
    Rebuilding after an edit then costs only the new sentences; with
    ``path``, kept there for the next start too, as long as the model is the
    same (91 s for 25 commands on an HA Green, every start, without it)."""

    def __init__(self, embedder, path: Path | None = None) -> None:
        self._embedder = embedder
        self._cache: dict[str, np.ndarray] = {}
        self._used: set[str] = set()
        self._added = False
        self._path = Path(path) if path is not None else None
        self._model = _model_of(embedder)
        if self._path is not None and self._path.is_file():
            self._load(self._path)

    def embed(self, texts: list[str], document: bool = False) -> np.ndarray:
        if not document:
            return self._embedder.embed(texts, document)
        missing = [t for t in dict.fromkeys(texts) if t not in self._cache]
        if missing:
            for text, vector in zip(missing, self._embedder.embed(missing, True)):
                self._cache[text] = vector
            self._added = True
        self._used.update(texts)
        if not texts:
            return np.zeros((0, 768), dtype=np.float32)
        return np.stack([self._cache[t] for t in texts])

    def _load(self, path: Path) -> None:
        try:
            with np.load(path, allow_pickle=False) as saved:
                if str(saved["model"]) != self._model:
                    _LOGGER.info("%s is another model's; embedding anew", path.name)
                    return
                for text, vector in zip(saved["texts"].tolist(), saved["vectors"]):
                    self._cache[str(text)] = vector
        except (OSError, KeyError, ValueError) as err:
            _LOGGER.warning("Could not read %s (%s); embedding anew", path, err)
            return
        _LOGGER.info("%d embedded sentences from %s", len(self._cache), path.name)

    def save(self) -> None:
        """Keep what the builds since starting used, if anything changed:
        sentences no command says any more are left out."""
        if self._path is None or not self._used or (not self._added and self._used >= set(self._cache)):
            return
        texts = [t for t in self._cache if t in self._used]
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp = self._path.with_name(f".{self._path.name}.tmp")
        with open(temp, "wb") as out:
            np.savez(out, model=np.array(self._model), texts=np.array(texts), vectors=np.stack([self._cache[t] for t in texts]))
        temp.replace(self._path)
        self._cache = {t: self._cache[t] for t in texts}
        self._added = False


def _model_of(embedder) -> str:
    """What a saved embedding was made with: the model's files and sizes,
    and how documents are prompted."""
    from adapt_ctc.embedding import DOCUMENT_PROMPT  # pylint: disable=import-outside-toplevel

    model_dir = Path(getattr(embedder, "model_dir", "") or ".")
    files = sorted((p.name, p.stat().st_size) for p in model_dir.rglob("*.onnx*")) if model_dir.is_dir() else []
    return f"{model_dir.name} {files} {DOCUMENT_PROMPT!r}"


class Engine:
    def __init__(
        self,
        model_dir: Path,
        tdt_dir: Path | None = None,
        embedding_dir: Path | None = None,
        threads: int = 2,
        embedding_cache: Path | None = None,
    ) -> None:
        self.threads = threads
        shared = self._shared(model_dir, tdt_dir)
        self.model = shared or CtcModel(model_dir, num_threads=threads)
        self.transcriber = shared or self._load_tdt(tdt_dir)
        self.tdt_dir = Path(tdt_dir) if self.transcriber is not None and tdt_dir is not None else None
        self.embedder = None
        if embedding_dir is not None and (Path(embedding_dir) / "onnx/model_q4.onnx").is_file():
            try:
                from adapt_ctc.embedding import Embedder  # pylint: disable=import-outside-toplevel

                self.embedder = CachedEmbedder(Embedder(embedding_dir, threads), embedding_cache)
            except ImportError:
                _LOGGER.warning("tokenizers is not installed; no hints by meaning")
        elif embedding_dir is not None:
            _LOGGER.info("No embedding model at %s; hints come from keywords only", embedding_dir)
        self._placers: dict[tuple[str, ...], WakeVerifier] = {}
        self.placer = self.placer_for(WAKE_PHRASES)
        self.fallback: Any = None
        """When nothing matched: meaning, then GLiNER (voice_incubator/fallback.py)."""
        self.skill_settings: dict[str, Any] | None = None
        """Each skill's settings (skills.configure); None: as built in."""
        self.dynamic: dict[str, list[str]] = {name: [] for name in DYNAMIC_LISTS}
        """Values of the lists the app keeps current: running timers' names."""
        self.entity_area: Any = None
        """Where a device is -- its Home Assistant area, or None -- to pick out
        the device of a name in the room said with it (``fit_devices``)."""

        self.compiled: Compiled | None = None
        self.recognizer: Recognizer | None = None
        self.unspeakable: list[str] = []
        self.build_seconds = 0.0
        self._lock = threading.Lock()

        self.kaldi: kaldi_mode.KaldiMode | None = None
        """Kaldi mode's graphs and decoder (voice_incubator/kaldi_mode.py), when
        the app has the model and tools for it."""
        self.recognizer_choice = "parakeet"
        """What hears commands: ``parakeet``, or ``kaldi`` (settings.json
        ``recognizer``)."""
        self.pronunciations: dict[str, list[str]] = {}
        """The person's own pronunciations, for Kaldi (pronunciations.json)."""
        self.kaldi_sentences: kaldi_mode.Sentences | None = None
        """What Kaldi was last built to hear, and which commands it doesn't."""
        self.on_built: Any = None
        """Called (from the building thread) once the commands are built: what
        builds Kaldi's graphs for them, after -- Parakeet's recognizer is
        ready at once, Kaldi's when they are."""
        self.kaldi_wake: tuple[list[tuple[str, ...]], dict[str, list[str]]] = ([], {})
        """The wake phrases in use, as words, and the wake word verifier's
        pronunciations of them: what Kaldi hears a call start with."""

    def _shared(self, model_dir: Path, tdt_dir: Path | None):
        """The CTC model and the transducer as one, with one encoder, when the
        transducer has the CTC model's head beside it (adapt_ctc.hybrid):
        Parakeet's encoder loaded once rather than twice (720 -> 486 MB)."""
        from adapt_ctc.hybrid import HybridModel, has_shared_model  # pylint: disable=import-outside-toplevel

        if not has_shared_model(tdt_dir, model_dir):
            return None
        assert tdt_dir is not None
        _LOGGER.info("CTC and TDT share one encoder: %s", tdt_dir)
        return HybridModel(tdt_dir, self.threads)

    def _load_tdt(self, tdt_dir: Path | None):
        from adapt_ctc.tdt import has_model  # pylint: disable=import-outside-toplevel

        if tdt_dir is None:
            return None
        if not has_model(Path(tdt_dir)):
            _LOGGER.warning("No TDT model at %s; examples are matched in the CTC evidence only", tdt_dir)
            return None
        try:
            from adapt_ctc.tdt import TdtTranscriber  # pylint: disable=import-outside-toplevel

            transcriber = TdtTranscriber(tdt_dir, self.threads)
        except ImportError:
            _LOGGER.warning("sherpa-onnx is not installed; no TDT transcripts")
            return None
        _LOGGER.info("TDT model: %s", tdt_dir)
        return transcriber

    def use_tdt(self, tdt_dir: Path | None) -> bool:
        """Transcribe with another TDT model from now on (the English one or
        the multilingual one). Slow: call from a thread. False, and the old
        one kept, when it cannot be loaded."""
        shared = getattr(self.model, "transcribe", None) is not None and tdt_dir is not None
        if shared and Path(tdt_dir).resolve() == self.model.model_dir:
            transcriber = self.model  # back to the one sharing its encoder
        else:
            transcriber = self._load_tdt(tdt_dir)
        if transcriber is None and tdt_dir is not None:
            return False
        with self._lock:
            self.transcriber = transcriber
            self.tdt_dir = Path(tdt_dir) if tdt_dir is not None else None
            compiled = self.compiled
        if compiled is not None:
            self.build(compiled)  # the recognizer holds the transcriber
        return True

    # ------------------------------------------------------------- building

    def build(self, compiled: Compiled) -> None:
        """Make the store's commands the ones recognized. Slow-ish (graph,
        examples, embeddings): call from a thread. On error the previous
        commands stay."""
        started = time.perf_counter()
        recognizer = None
        config = copy.deepcopy(compiled.config or {**base_config(), "intents": {}})
        self._fill_dynamic(config)
        if config["intents"]:
            recognizer = Recognizer(
                parse_config(config),
                self.model,
                transcriber=self.transcriber,
                embedder=self.embedder,
                meaning_texts=triggers.meaning_texts(compiled.triggers or {}, compiled.commands, config["intents"]),
            )
        with self._lock:
            self.compiled = compiled
            self.recognizer = recognizer
            self.unspeakable = list(recognizer.unspeakable) if recognizer else []
        if self.on_built is not None:
            self.on_built()  # Kaldi's graphs, built after (seconds on a Pi): not here
        if isinstance(self.embedder, CachedEmbedder):
            try:
                self.embedder.save()
            except OSError as err:
                _LOGGER.warning("Could not keep the embeddings: %s", err)
        self.build_seconds = time.perf_counter() - started
        _LOGGER.info(
            "Recognizing %d command(s); built in %.0f ms%s",
            len(compiled.commands),
            self.build_seconds * 1000,
            f"; cannot spell {self.unspeakable}" if self.unspeakable else "",
        )

    @property
    def kaldi_on(self) -> bool:
        """Whether Kaldi hears commands now: chosen, and its decoder runs."""
        return self.recognizer_choice == "kaldi" and self.kaldi is not None and self.kaldi.ready

    def build_kaldi(self) -> None:
        """Kaldi mode's graphs for the commands recognized now (slow: from a
        thread). A failure is kept in ``kaldi.error``; Parakeet hears
        meanwhile."""
        if self.kaldi is None:
            return
        with self._lock:
            recognizer, compiled = self.recognizer, self.compiled
        if recognizer is None:
            return
        language = recognizer.config.language
        try:
            if language.split("-")[0].split("_")[0] not in kaldi_mode.LANGUAGES:
                raise RuntimeError(f"Kaldi has no model for {language}")
            heard = kaldi_mode.sentences(
                recognizer.config, lambda intent, text: self._free_text(recognizer, intent, text),
                compiled.commands.values() if compiled is not None else (),
            )
            self._misread(recognizer, heard)
            reading = languages.current().reading
            answers = sorted({
                *reading.ANSWER_ENDS, *reading.YES_SAID, *reading.NO_SAID,
                # Which of several timers, alarms or reminders (follow_up.pick).
                *kaldi_mode.answer_sentences(getattr(reading, "WHICH_SAID", ()), heard),
            })
            phrases, lexicon = self.kaldi_wake
            self.kaldi.build(heard, language, self.pronunciations, answers, phrases, lexicon)
            self.kaldi_sentences = heard
        except Exception as err:  # noqa: BLE001 - Parakeet hears instead
            _LOGGER.exception("Kaldi mode can't be built")
            self.kaldi.error = str(err) or type(err).__name__

    def _misread(self, recognizer: Recognizer, heard: kaldi_mode.Sentences) -> None:
        """Each command's sentence templates that the matcher doesn't read
        as the command -- one sentence each stands for, the slots' first
        phrases -- into ``heard.misread``: Kaldi would hear them, and
        something else would run."""
        for command_id, templates in heard.templates.items():
            for template, words in templates:
                text = " ".join(words)
                spellings = spoken_words(text, recognizer.config.language)
                log_probs, _ = self.model.vocab.text_log_probs(spellings[0] if spellings else [])
                best = recognizer.recognize_log_probs(log_probs, self.model.frame_seconds).best
                read = command_of(best.intent) if best is not None else None
                if read != command_id:
                    heard.misread.setdefault(command_id, []).append({"template": template, "sentence": text, "as": read})

    def _free_text(self, recognizer: Recognizer, intent: str, text: str) -> str | None:
        """The free text the matcher reads in ``text``, one of ``intent``'s
        example sentences."""
        spellings = spoken_words(text, recognizer.config.language)
        log_probs, _ = self.model.vocab.text_log_probs(spellings[0] if spellings else [])
        recognition = recognizer.recognize_log_probs(log_probs, self.model.frame_seconds, examples=False)
        wildcard = recognizer.config.intents[intent].wildcard
        for result in recognition.matches:
            if result.intent == intent and wildcard is not None:
                value = result.slots.get(wildcard.slot)
                return str(value) if value else None
        return None

    def kaldi_stream(self, graphs: tuple[str, ...]) -> kaldi_mode.Stream | None:
        """Kaldi's decode of a call (kaldi_mode.CALL), an answer (ANSWER) or
        Home Assistant's audio (COMMAND) as it is said, when Kaldi hears
        commands: for ``recognize_*``'s ``kaldi_stream``, fed the same audio
        from its start."""
        if not self.kaldi_on:
            return None
        assert self.kaldi is not None
        try:
            return self.kaldi.stream(graphs)
        except RuntimeError:
            return None

    def _kaldi_decode(
        self, audio: np.ndarray, graphs: tuple[str, ...], stream: kaldi_mode.Stream | None = None,
    ) -> kaldi_mode.Heard | None:
        """Kaldi's decode of ``audio`` against ``graphs`` (kaldi_mode.CALL,
        COMMAND, ANSWER): the stream's, when it was fed all of it (and only
        it: someone else's speech cuts a call short), else all of it now;
        None when its decoder failed (being replaced, or gone): Parakeet
        hears that call."""
        assert self.kaldi is not None
        if stream is not None and stream.samples == len(audio):
            try:
                return stream.finish()
            except RuntimeError as err:
                _LOGGER.warning("Kaldi's stream failed, so the call is decoded whole: %s", err)
        elif stream is not None:
            stream.cancel()
        try:
            return self.kaldi.decode(audio, graphs)
        except RuntimeError as err:
            _LOGGER.warning("Kaldi couldn't hear it, so Parakeet does: %s", err)
            return None

    def _kaldi_heard(
        self, heard: kaldi_mode.Heard, satellite: Satellite, graph: str = "home", after: float = 0.0,
    ) -> Decision:
        """What Kaldi heard (``graph``'s sentence: kaldi_mode.TAGS), as a
        decision: its sentence matched as typed, when it was one of the
        home's; else nothing -- Never mind when not a word was said (the
        background heard none ``after`` the wake word)."""
        answer = graph == "answer"
        text, taken = heard.text(graph), heard.taken(graph)
        decision = self.recognize_text(text if taken else "", satellite)
        if taken and decision.command is not None:
            decision.matched_by = "kaldi"
        decision.text_source = "kaldi"
        decision.recognition["transcript"] = text if taken else ""
        decision.recognition["tdt_transcript"] = text if taken else ""
        decision.sentence = heard.to_json(graph)
        if not taken and not answer and not heard.background_after(after, graph):
            with self._lock:
                compiled = self.compiled
            if compiled is not None:
                self._never_mind(decision, compiled, satellite, {"why": "nothing_heard"})
        decision.timings = {"kaldi": heard.seconds, **decision.timings}
        return decision

    def _fill_dynamic(self, config: dict[str, Any]) -> None:
        """Put the app's own lists in, or take out the intents (combinations)
        that hear an empty one."""
        lists = config.setdefault("lists", {})
        empty = set()
        for name in DYNAMIC_LISTS:
            values = {}
            for value in self.dynamic.get(name, []):
                try:
                    _phrase(value, "en", name)
                except ConfigError:
                    continue  # nothing speakable in it
                values[value] = [value]
            if values:
                lists[name] = values
            else:
                lists.pop(name, None)
                empty.add(name)
        if not empty:
            return
        for intent_name, intent in list(config["intents"].items()):
            slots = intent.get("lists") or {}
            for spec in list(slots.values()):
                listed = spec if isinstance(spec, str) else (spec or {}).get("list")
                if listed in empty:
                    del config["intents"][intent_name]
                    break

    # ---------------------------------------------------------- recognizing

    def placer_for(self, phrases: tuple[str, ...]) -> WakeVerifier:
        """Finds one wake word's phrases in the CTC evidence. Placement only,
        so no confusables: the wake word was already decided on."""
        placer = self._placers.get(phrases)
        if placer is None:
            placer = WakeVerifier(WakeConfig(phrases, scoring="best"), self.model.vocab, "en")
            self._placers[phrases] = placer
        return placer

    def place_command(
        self,
        audio: np.ndarray,
        wake_start: float | None,
        kaldi_split: float,
        phrases: tuple[str, ...] | None = None,
    ) -> tuple[np.ndarray, float, Split]:
        """Log-probabilities of the command, their frame length, and where it
        starts: after the wake phrase (``phrases``, default "okay nabu") as the
        CTC evidence places it."""
        log_probs, frame_seconds, split, cut = self._place(audio, wake_start, kaldi_split, phrases)
        return log_probs[cut:], frame_seconds, split

    def _place(
        self, audio: np.ndarray, wake_start: float | None, kaldi_split: float, phrases: tuple[str, ...] | None,
    ) -> tuple[np.ndarray, float, Split, int]:
        """``place_command``'s, but the log-probabilities from the call's
        offset (``split.offset``), the wake word in them, and the frame the
        command starts at."""
        anchor = wake_start if wake_start is not None else max(0.0, kaldi_split - 1.0)
        offset = max(0.0, anchor - LEAD)
        log_probs, frame_seconds = self.model.log_probs(audio[int(offset * SAMPLE_RATE) :])
        bound = int((kaldi_split - offset + SPLIT_BOUND) / frame_seconds)
        placer = self.placer_for(tuple(phrases)) if phrases else self.placer
        wake = placer.verify_log_probs(log_probs, frame_seconds, max_end=bound)
        split = Split(offset, kaldi_split, kaldi_split)
        if wake.phrase is not None:
            end = wake.end
            split.phrase = wake.phrase
            split.wake_start = offset + wake.start * frame_seconds
            split.wake_end = offset + (wake.end + 1) * frame_seconds
            split.cost = wake.cost
            if wake.phrase in PARTIAL_WAKE_PHRASES:
                # Only the start of the wake word's last word: what follows it
                # may be the rest ("-boo"), which Kaldi's end takes in.
                end = max(end, int((kaldi_split - offset) / frame_seconds) - 1)
        else:
            end = int((kaldi_split - offset) / frame_seconds) - 1
        split.cut = offset + (end + 1) * frame_seconds
        return log_probs, frame_seconds, split, end + 1

    def recognize_call(
        self,
        audio: np.ndarray,
        wake_start: float | None,
        kaldi_split: float,
        satellite: Satellite,
        transcriber: Any = None,
        wake_phrases: tuple[str, ...] | None = None,
        kaldi_stream: kaldi_mode.Stream | None = None,
    ) -> tuple[Decision, Split]:
        """A whole call, from the start of its pre-roll, after Kaldi accepted
        the wake word. ``transcriber`` stands in for the TDT head (an STT
        entity, see voice_incubator/voices.py). ``kaldi_stream``: Kaldi's decode
        of the call as it was said (``kaldi_stream``)."""
        started = time.perf_counter()
        if self.kaldi_on and (heard := self._kaldi_decode(audio, kaldi_mode.CALL, kaldi_stream)) is not None:
            # The whole call, pre-roll and all: a wake phrase, then a sentence.
            return self._kaldi_heard(heard, satellite, "call", kaldi_split), Split(0.0, kaldi_split, kaldi_split)
        if kaldi_stream is not None:
            kaldi_stream.cancel()
        evidence, frame_seconds, split, cut = self._place(audio, wake_start, kaldi_split, wake_phrases)
        log_probs = evidence[cut:]
        placed = time.perf_counter() - started
        lead = min(cut, round(SENTENCE_LEAD_IN / frame_seconds))
        heard, sentence = self.hear_sentence(evidence[cut - lead :], lead, log_probs, satellite)
        if heard is not None:
            heard.timings = {"acoustic+placement": placed, **heard.timings}
            return heard, split
        command_audio = audio[int(split.cut * SAMPLE_RATE) :]
        if transcriber is None and self.transcriber is not None:
            # The TDT head from before the cut, on the CTC evidence's encoder run.
            transcriber = CallTdt(
                self.transcriber, audio[int(split.offset * SAMPLE_RATE) :], split.cut - split.offset, command_audio,
                shared=self.transcriber is self.model,
            )
        decision = self.decide(log_probs, frame_seconds, command_audio, satellite, transcriber)
        decision.sentence = sentence
        decision.timings = {"acoustic+placement": placed, **sentence_time(sentence), **decision.timings}
        return decision, split

    def recognize_command(
        self, audio: np.ndarray, satellite: Satellite, transcriber: Any = None,
        evidence: tuple[np.ndarray, float] | None = None, sentences: bool = True,
        kaldi_stream: kaldi_mode.Stream | None = None,
    ) -> Decision:
        """Audio that is only the command (typed and synthesized, uploaded, or
        Home Assistant's). ``evidence``: the CTC head's on it (``model.log_probs``),
        when it was had already. ``sentences``: heard as one of the home's
        sentences first (``hear_sentence``)."""
        started = time.perf_counter()
        if self.kaldi_on and sentences and (heard := self._kaldi_decode(audio, kaldi_mode.COMMAND, kaldi_stream)) is not None:
            return self._kaldi_heard(heard, satellite)
        if kaldi_stream is not None:
            kaldi_stream.cancel()
        log_probs, frame_seconds = evidence if evidence is not None else self.model.log_probs(audio)
        acoustic = time.perf_counter() - started
        heard, sentence = self.hear_sentence(log_probs, 0, log_probs, satellite) if sentences else (None, None)
        if heard is not None:
            heard.timings = {"acoustic": acoustic, **heard.timings}
            return heard
        decision = self.decide(log_probs, frame_seconds, audio, satellite, transcriber)
        decision.sentence = sentence
        decision.timings = {"acoustic": acoustic, **sentence_time(sentence), **decision.timings}
        return decision

    def hear_sentence(
        self, evidence: np.ndarray, free: int, command: np.ndarray, satellite: Satellite,
    ) -> tuple[Decision | None, dict[str, Any] | None]:
        """The audio as one of the home's sentences (``settings.grammar``),
        matched as typed when it costs no more than ``grammar_max_cost``: the
        decision, when it is a command or one refused as said -- else None,
        for the passes after it. And what it was heard as, for the record.
        ``evidence``: the CTC head's on the audio, the first ``free`` frames
        the end of the wake word; ``command``: on the command alone."""
        with self._lock:
            recognizer = self.recognizer
        if recognizer is None or recognizer.grammar is None:
            return None, None
        started = time.perf_counter()
        heard, cost = recognizer.hear_sentence(evidence, free)
        if heard is None or cost is None:
            return None, {"text": None, "taken": False, "seconds": time.perf_counter() - started}
        sentence = {"text": heard.text, "cost": round(cost, 2), "taken": False}
        if cost > recognizer.config.settings.grammar_max_cost:
            return None, {**sentence, "seconds": time.perf_counter() - started}
        greedy = self._greedy(command)
        sentence["agrees"] = round(agreement(heard.text, greedy), 2)
        if sentence["agrees"] < SENTENCE_AGREEMENT:
            return None, {**sentence, "seconds": time.perf_counter() - started}
        searched = time.perf_counter() - started
        decision = self.recognize_text(heard.text, satellite)
        if decision.command is None and decision.refused is None:
            return None, {**sentence, "seconds": time.perf_counter() - started}
        sentence["taken"] = True
        sentence["timed"] = self._timed(decision, heard, free)
        decision.sentence = sentence
        decision.text_source = "sentence"
        if decision.command is not None:
            decision.matched_by = "sentence"
        # What the CTC head heard on its own, beside the sentence it was heard as.
        decision.recognition["transcript"] = greedy
        decision.timings = {"sentence": searched, **decision.timings}
        return decision, sentence

    def recognize_answer(
        self, audio: np.ndarray, satellite: Satellite, transcriber: Any = None,
        kaldi_stream: kaldi_mode.Stream | None = None,
    ) -> Decision:
        """An answer to what the app asked (voice_incubator/follow_up.py): as a
        command, and always with its words -- the transcriber's, which
        whoever asked reads -- even when the command needed none. Not as one
        of the home's sentences: an answer is seldom one. With Kaldi, the
        home's sentences and the language's ways of answering."""
        if self.kaldi_on and (heard := self._kaldi_decode(audio, kaldi_mode.ANSWER, kaldi_stream)) is not None:
            return self._kaldi_heard(heard, satellite, "answer")
        if kaldi_stream is not None:
            kaldi_stream.cancel()
        decision = self.recognize_command(audio, satellite, transcriber, sentences=False)
        reader = transcriber or self.transcriber
        if decision.recognition.get("tdt_transcript") is None and reader is not None and len(audio):
            started = time.perf_counter()
            decision.recognition["tdt_transcript"] = reader.transcribe(audio)
            decision.timings["answer_words"] = time.perf_counter() - started
        return decision

    def recognize_text(self, text: str, satellite: Satellite, clause: bool = False) -> Decision:
        """Typed text, as if it had been said and heard word for word: the
        CTC matcher reads evidence made from its spoken words (no audio, no
        speech recognition), and the text itself is the transcript.
        ``clause``: as ``decide``'s."""
        started = time.perf_counter()
        with self._lock:
            recognizer = self.recognizer
        language = recognizer.config.language if recognizer is not None else languages.current().code
        spellings = spoken_words(text, language)
        log_probs, unspellable = self.model.vocab.text_log_probs(spellings[0] if spellings else [])
        built = time.perf_counter() - started
        decision = self.decide(
            log_probs, self.model.frame_seconds, np.zeros(0, dtype=np.float32), satellite, TypedText(text), clause
        )
        if decision.recognition.get("tdt_transcript") is None:
            decision.recognition["tdt_transcript"] = text  # known, needed or not
        if unspellable:
            decision.recognition["unspellable"] = unspellable
        decision.timings = {"evidence": built, **decision.timings}
        return decision

    def decide(
        self,
        log_probs: np.ndarray,
        frame_seconds: float,
        command_audio: np.ndarray,
        satellite: Satellite,
        transcriber: Any = None,
        clause: bool = False,
    ) -> Decision:
        """What the command comes to. The CTC matcher always runs; text --
        examples not found in the CTC evidence, free text, hints -- comes from
        ``transcriber`` when given, else the TDT head, and only when needed.
        ``clause``: one of two commands said together (voice_incubator/compound.py):
        a command or nothing -- no skill, fallback or hint."""
        transcriber = transcriber or self.transcriber
        with self._lock:
            compiled, recognizer = self.compiled, self.recognizer
        if recognizer is None or compiled is None:
            decision = Decision(recognition={"transcript": self._greedy(log_probs)})
            decision.message = languages.speech().NO_COMMANDS
            decision.hint = {"source": None, "text": decision.message}
            return decision

        typed = getattr(transcriber, "source", "TDT") == "typed"
        # Typed text is clean: an example counts only typed whole, not a short
        # one with words around it ("grab bananas *next* time you shop").
        recognition = recognizer.recognize_log_probs(log_probs, frame_seconds, examples=not typed)
        if typed:
            recognizer.example_first(recognition, transcriber.transcribe(command_audio))
        recognition = recognizer.match_examples(recognition, command_audio, transcriber)
        decision = Decision(recognition={}, text_source=getattr(transcriber, "source", "TDT"))

        # Home Assistant's triggers with free text, before anything else, as
        # it has them: on the CTC head's transcript (or what was typed).
        if compiled.heard_triggers is not None:
            said = transcriber.transcribe(command_audio) if typed else recognition.transcript
            if self._said_trigger(decision, [said or ""], compiled, satellite) and not typed and transcriber is not None:
                self._spell_trigger(decision, recognition, compiled, command_audio, transcriber)
        if decision.command is None:
            self._take(decision, recognition, compiled, satellite, command_audio, frame_seconds, transcriber)
        if decision.command is not None and not typed and transcriber is not None and decision.intent is not None:
            if _doubt(decision.intent) >= recognizer.config.settings.unexplained_important_cost:
                self._hear_again(decision, recognition, compiled, recognizer, satellite, command_audio, transcriber, "cost")
            elif decision.intent.unexplained and self._may_name(decision, compiled, satellite):
                self._hear_again(decision, recognition, compiled, recognizer, satellite, command_audio, transcriber, "unnamed")

        if decision.command is None and decision.refused is None and decision.doubt is None:
            if transcriber is not None and recognition.tdt_transcript is None:
                started = time.perf_counter()
                recognition.tdt_transcript = transcriber.transcribe(command_audio)
                _took(recognition.timings, _timing(transcriber), started)
            # The keywords and names, on the transcript: a strong speech model
            # may have heard plainly what the CTC evidence only half did.
            if recognition.tdt_transcript and getattr(transcriber, "source", "TDT") != "typed":
                self._match_transcript(decision, recognition.tdt_transcript, compiled, recognizer, satellite)
            if decision.command is None and decision.refused is None and compiled.heard_triggers is not None and not typed:
                self._said_trigger(decision, [recognition.tdt_transcript or ""], compiled, satellite)
        # Two commands said together: each on its own, or neither -- never
        # half of what was asked (voice_incubator/compound.py).
        words = [w.text for w in recognition.spotting.words]
        joined = compound.leaves_joined(decision, words) and self._joined_said(recognition, command_audio, transcriber, typed)
        if not joined and decision.intent is not None and decision.intent.unexplained and recognition.tdt_transcript:
            # The CTC head heard the joining word as another ("... light *in*
            # set its brightness"), which the transcript, made already, has.
            joined = compound.has_joiner((recognition.tdt_transcript or "").split())
        if decision.refused is None and joined:
            if clause or not self._compound(decision, recognition, log_probs, frame_seconds, command_audio, satellite, transcriber):
                if decision.command is not None:
                    decision.compound = {"half": decision.command.id}
                decision.command, decision.behavior, decision.intent, decision.slots = None, None, None, {}
                decision.compound = {**(decision.compound or {}), "split": False}
        if clause:
            decision.recognition = to_json(recognition)
            decision.timings = dict(recognition.timings)
            return decision
        if decision.command is None and decision.refused is None:
            # Each transcript, TDT's first: one may hear "fifty degrees F in
            # Celsius" where the other heard "FN Celsius".
            transcripts = dict.fromkeys(t for t in (recognition.tdt_transcript, recognition.transcript) if t)
            exact = next(filter(None, (skills.answer(t, satellite.skills, self.skill_settings) for t in transcripts)), None)
            if exact is None and not typed:
                # The wake word's tail heard as a word or two before the
                # question: "group what's twelve times eight" for "... Naboo,
                # what's twelve times eight". The question without them, if it
                # is one whole.
                exact = next(filter(None, (
                    skills.answer(t, satellite.skills, self.skill_settings) for t in without_lead(transcripts)
                )), None)
            if exact is not None:
                # Spelling, dates, units, arithmetic: exact, and instant.
                decision.answer = exact.to_json()
            elif self.fallback is not None and compiled.commands and decision.compound is None:
                # (Not two commands: meaning and the model would pick one.)
                self._fall_back(decision, recognition, compiled, satellite)
        if decision.refused is not None:
            decision.hint = decision.refused
            decision.message = decision.refused["text"]
        elif decision.command is None and decision.answer is None:
            decision.hint = self._hint(recognition, compiled, satellite, decision.skipped)
            picked = (decision.fallback or {}).get("llm") or {}
            say = languages.speech()
            if picked.get("command") in compiled.commands:
                # GLiNER knew what was meant but not all of it, or it isn't heard here.
                command = compiled.commands[picked["command"]]
                text = satellite.not_here() if satellite.available(command) is None else say.hint(command.description, say.PARTLY)
                decision.hint = {"source": "gliner", "command": command.id, "text": text}
            half = (decision.compound or {}).get("half")
            if half in compiled.commands:
                # Two commands said together, not both understood: neither ran.
                decision.hint = {"source": "compound", "command": half, "text": say.hint(compiled.commands[half].description, say.PARTLY)}
            if not compiled.commands:
                # Only the app's own commands are listened for.
                decision.hint = {"source": None, "text": say.NO_COMMANDS}
            decision.message = decision.hint["text"]
            hinted = compiled.commands.get(str(decision.hint.get("command") or ""))
            if hinted is not None and never_mind(hinted):
                # "It looks like you're trying to never mind": then never mind.
                self._never_mind(decision, compiled, satellite, {"why": "closest", "hint": decision.hint})
            elif not typed and not any(_has_words(t) for t in (recognition.transcript, recognition.tdt_transcript)):
                # Not a word heard after the wake word: woken by mistake, most
                # likely -- never a command said to it, in the corpus's 900.
                self._never_mind(decision, compiled, satellite, {"why": "nothing_heard", "hint": decision.hint})

        decision.recognition = to_json(recognition)
        decision.timings = dict(recognition.timings)
        # Who wrote the transcript, once it was asked for: the TDT head stands
        # in for an STT entity that failed or heard nothing.
        decision.text_source = getattr(transcriber, "source", decision.text_source)
        return decision

    @staticmethod
    def _timed(decision: Decision, heard: Any, lead: int) -> bool:
        """The decision's names, numbers and keywords -- from the sentence as
        typed -- put where its words were said: in the command's frames (the
        sentence's less ``lead``), as the CTC matcher's own are, so the
        transcript can write them over the words heard there
        (voice_incubator/corrected.py). False when a word can't be found."""
        intent = decision.intent
        if intent is None:
            return False
        words = [w.text for w in heard.said]
        frames = heard.said_frames
        at, timed = 0, {}
        for use in sorted(intent.spans, key=lambda u: u.hit.start):
            phrase = use.meaning.phrase.split()
            start = next((i for i in range(at, len(words) - len(phrase) + 1) if words[i : i + len(phrase)] == phrase), None)
            if start is None:
                return False
            first, last = frames[start][0] - lead, frames[start + len(phrase) - 1][1] - lead
            timed[id(use)] = replace(use, hit=replace(use.hit, start=max(0, first), end=max(0, last)))
            at = start + len(phrase)
        intent.spans = [timed[id(u)] for u in intent.spans]
        seconds = decision.recognition.get("frame_seconds")
        for match in decision.recognition.get("matches") or []:
            if match.get("intent") == intent.intent and seconds and len(match.get("spans") or []) == len(intent.spans):
                for shown, use in zip(match["spans"], intent.spans):
                    shown["start"], shown["end"] = round(use.hit.start * seconds, 3), round((use.hit.end + 1) * seconds, 3)
        return True

    @staticmethod
    def _joined_said(recognition: Recognition, command_audio: np.ndarray, transcriber: Any, typed: bool) -> bool:
        """Whether the transcript has a joining word too: the CTC head's
        "and" may be a word it misheard ("set *and* light to green", said
        "set bed light")."""
        if typed or transcriber is None:
            return True
        if recognition.tdt_transcript is None:
            started = time.perf_counter()
            recognition.tdt_transcript = transcriber.transcribe(command_audio)
            _took(recognition.timings, _timing(transcriber), started)
        joining = compound.joining_words()
        return any(w.lower().strip(".,!?") in joining for w in (recognition.tdt_transcript or "").split())

    def _compound(
        self,
        decision: Decision,
        recognition: Recognition,
        log_probs: np.ndarray,
        frame_seconds: float,
        command_audio: np.ndarray,
        satellite: Satellite,
        transcriber: Any,
    ) -> bool:
        """Two commands said together (voice_incubator/compound.py), at the first
        joining word where both sides are commands: each heard on its own
        stretch of the audio (typed, its own words), one that is none
        borrowing words from the other. Else the transcript's words, as
        typed: the CTC head may have missed the joining word, or misheard a
        clause ("turn on the home assistant *like*") the transcript has
        right. Into ``decision``: the first, with the second as its
        ``then``. Whether they were."""
        typed = getattr(transcriber, "source", "TDT") == "typed"
        # A clause is its own stretch of audio: the TDT head transcribes it,
        # where an STT entity hears the whole call.
        heard_by = transcriber if typed or getattr(transcriber, "source", "TDT") == "TDT" else self.transcriber
        spotted = recognition.spotting.words
        words = [w.text for w in spotted]
        started = time.perf_counter()
        try:
            for start, end in compound.splits(words):
                texts = [" ".join(words[:start]), " ".join(words[end:])]
                if typed:
                    clauses = [self.recognize_text(text, satellite, clause=True) for text in texts]
                else:
                    cut, resume = spotted[start].start, spotted[end - 1].end + 1
                    samples = int(round(frame_seconds * SAMPLE_RATE))
                    audio = [command_audio[: cut * samples], command_audio[resume * samples :]]
                    if min(len(a) for a in audio) < SAMPLE_RATE // 10:
                        continue  # no audio to hear one in (evidence made from text)
                    clauses = [
                        self.decide(log_probs[:cut], frame_seconds, audio[0], satellite, heard_by, clause=True),
                        self.decide(log_probs[resume:], frame_seconds, audio[1], satellite, heard_by, clause=True),
                    ]
                if self._both(decision, clauses, texts, " ".join(words[start:end]), satellite):
                    return True
            if typed or not recognition.tdt_transcript:
                return False
            heard = recognition.tdt_transcript.split()
            for start, end in compound.splits(heard):
                texts = [" ".join(heard[:start]), " ".join(heard[end:])]
                clauses = [self.recognize_text(text, satellite, clause=True) for text in texts]
                if self._both(decision, clauses, texts, " ".join(heard[start:end]), satellite):
                    assert decision.compound is not None
                    decision.compound["from"] = "transcript"
                    return True
            return False
        finally:
            recognition.timings["compound"] = time.perf_counter() - started

    def _both(self, decision: Decision, clauses: list[Decision], texts: list[str], joiner: str, satellite: Satellite) -> bool:
        """Two clauses as two commands into ``decision`` (``_compound``);
        whether they are. A first that is none borrows the second's last
        words; a second that names nothing -- or is none -- is about what
        the first named, when it can be (its pronoun that name, else the name
        said after it: "... and set its brightness to 10", "... kitchen
        lights and set the brightness to 50"); else, none, it borrows the
        first's first words ("... and the TV")."""
        with self._lock:
            compiled = self.compiled
        assert compiled is not None
        said = [(c.transcript or t).split() for c, t in zip(clauses, texts)]

        def clean(text: str) -> Decision | None:
            found = self.recognize_text(text, satellite, clause=True)
            ok = found.command is not None and found.intent is not None and not found.intent.unexplained
            return found if ok else None

        borrowed = None
        if clauses[0].command is None:
            borrowed = "first"
            clauses[0] = next(filter(None, map(clean, compound.borrowings(said[0], said[1], "first"))), clauses[0])
        first = clauses[0]
        if first.command is not None and _named(first) and (clauses[1].command is None or not _named(clauses[1])):
            names, devices = compiled.data.get("names") or {}, compiled.devices
            text = " ".join(said[1])
            readings = [
                context.substitute(text, compound.device_names(first.slots.get("entity") or [], names, devices)),
                f"{text} {compound.target(first.slots, names, devices)}",
            ]
            for reading in filter(None, readings):
                again = clean(reading)
                if again is not None and all(again.slots.get(s) == first.slots.get(s) for s in NAME_SLOTS if first.slots.get(s)):
                    clauses[1] = again
                    break
        if clauses[1].command is None:
            borrowed = "second"
            clauses[1] = next(filter(None, map(clean, compound.borrowings(said[0], said[1], "second"))), clauses[1])
        second = clauses[1]
        if first.command is None or second.command is None:
            return False
        decision.command, decision.behavior, decision.intent = first.command, first.behavior, first.intent
        decision.slots, decision.matched_by, decision.text_from_tdt = first.slots, first.matched_by, first.text_from_tdt
        decision.then = second
        decision.compound = {
            "joiner": joiner, "texts": [first.transcript, second.transcript], "borrowed": borrowed, "first": first.recognition,
        }
        return True

    def _fall_back(self, decision: Decision, recognition: Recognition, compiled: Compiled, satellite: Satellite) -> None:
        """Nothing matched: the closest command by meaning, then GLiNER."""
        with self._lock:
            recognizer = self.recognizer
        texts = [t for t in (recognition.tdt_transcript, recognition.transcript) if t]

        def same(intent: str) -> str:
            # A command's versions, said alike, are no rivals to each other.
            command = compiled.commands.get(command_of(intent))
            return label_of(command) if command is not None else intent

        def closest() -> dict[str, Any] | None:
            if recognizer is None or self.embedder is None:
                return None
            return recognizer.closest_by_meaning(texts, group=same)

        def ranked() -> list[tuple[str, float]]:
            if recognizer is None or self.embedder is None:
                return []
            return recognizer.rank_by_meaning(texts)

        started = time.perf_counter()
        found = self.fallback.decide(texts[0] if texts else "", closest, compiled, satellite, ranked)
        recognition.timings["fallback"] = time.perf_counter() - started
        decision.fallback = found.trace or None
        if found.command is None:
            return
        behavior = satellite.available(found.command)
        if behavior is None:
            return
        decision.command = found.command
        decision.behavior = behavior
        decision.slots = {**decision.behavior.slots, **found.slots}
        if found.command.behavior.trigger:
            # What Home Assistant hears: a sentence of the trigger's own, or
            # one of its templates said with what the model heard.
            step = (decision.fallback or {}).get("meaning" if found.by == "meaning" else "llm")
            sentence = triggers.sentence_for(
                found.command, (compiled.triggers or {}).get(found.command.id), decision.slots,
                ((decision.fallback or {}).get("meaning") or {}).get("closest_to"),
            )
            if sentence is None:
                if isinstance(step, dict):
                    step["problems"] = ["Home Assistant wouldn't hear it in any of its sentences said with that"]
                decision.command, decision.behavior, decision.slots = None, None, {}
                return
            decision.slots[triggers.SENTENCE] = sentence
        decision.matched_by = found.by or "gliner"
        refused = self.fit_devices(decision, compiled, satellite)
        if refused is not None:
            decision.command, decision.behavior, decision.refused = None, None, refused

    @staticmethod
    def _never_mind(decision: Decision, compiled: Compiled, satellite: Satellite, why: dict[str, Any]) -> bool:
        """The home's Never mind instead of what wasn't understood
        (commands.never_mind), when it has one heard here; ``why`` goes in
        ``decision.instead``."""
        for command in compiled.commands.values():
            behavior = satellite.available(command) if never_mind(command) and not command.disabled else None
            if behavior is not None:
                decision.command, decision.behavior, decision.slots = command, behavior, dict(behavior.slots)
                decision.matched_by, decision.instead = "never_mind", why
                decision.hint = decision.message = None
                return True
        return False

    @staticmethod
    def _said_trigger(decision: Decision, texts: list[str], compiled: Compiled, satellite: Satellite) -> bool:
        """The first of Home Assistant's triggers with free text that one of
        ``texts`` is, as it matches them (triggers.match), heard here: its
        command, and the slots it took with the ``sentence``."""
        for text in texts:
            for command_id, slots in triggers.match(compiled.heard_triggers, text):
                command = compiled.commands.get(command_id)
                if command is None or command.disabled:
                    continue
                behavior = satellite.available(command)
                if behavior is None:
                    decision.skipped.append({"command": command.id, "why": satellite.why_unavailable(command)})
                    continue
                decision.command, decision.behavior = command, behavior
                trigger = (compiled.triggers or {}).get(command_id)
                decision.slots = {**slots, triggers.SENTENCE: triggers.said_with(trigger, text, slots)}
                decision.matched_by = "trigger"
                return True
        return False

    @staticmethod
    def _spell_trigger(
        decision: Decision, recognition: Recognition, compiled: Compiled, command_audio: np.ndarray, transcriber: Any
    ) -> None:
        """A trigger found on the CTC head's transcript is sent as the TDT
        head heard it, when that is the same trigger: it spells free text
        far better."""
        if recognition.tdt_transcript is None:
            started = time.perf_counter()
            recognition.tdt_transcript = transcriber.transcribe(command_audio)
            _took(recognition.timings, _timing(transcriber), started)
        text = recognition.tdt_transcript or ""
        assert decision.command is not None
        for command_id, slots in triggers.match(compiled.heard_triggers, text):
            if command_id == decision.command.id:
                trigger = (compiled.triggers or {}).get(command_id)
                decision.slots = {**slots, triggers.SENTENCE: triggers.said_with(trigger, text, slots)}
                return

    def _take(
        self,
        decision: Decision,
        recognition: Recognition,
        compiled: Compiled,
        satellite: Satellite,
        command_audio: np.ndarray,
        frame_seconds: float,
        transcriber: Any,
    ) -> None:
        """The first of ``recognition``'s matches this satellite may run:
        its command, behavior and slots into ``decision`` (or a refusal).
        Of matches that cost the same, the area's own goes first.

        A device name not heard as said (``fit_devices``) gives way to a
        match as good as it: in "turn off utility room light", with only the
        Exercise Room's light called "Light", "light" is the lights' keyword,
        not that name. The refusal is said when no match as good runs."""

        def mine_first(result) -> tuple[float, bool]:
            command = compiled.commands.get(command_of(result.intent))
            return (result.cost, not (command is not None and satellite.picked(command)))

        recognizer = self.recognizer
        margin = recognizer.config.settings.ambiguity_margin if recognizer is not None else 0.0
        refusal: tuple[IntentResult, dict[str, Any], dict[str, Any]] | None = None
        for result in sorted(recognition.matches, key=mine_first):
            if refusal is not None and result.cost - refusal[0].cost >= margin:
                break
            command = compiled.commands.get(command_of(result.intent))
            if command is None:
                continue
            behavior = satellite.available(command)
            if result.example and not satellite.picked(command):
                # A copy of it says the same sentence: one picked for this
                # satellite, or one it may run where it may not run this one.
                # A trigger's goes first wherever it is heard.
                twins = [compiled.commands.get(command_of(t)) for t in compiled.twins.get(result.intent, ())]
                twin = next(iter(satellite.first([t for t in twins if t is not None and says(t, result.example)])), None)
                if twin is not None and (behavior is None or (satellite.picked(twin) and not command.behavior.trigger)):
                    why = f"its copy {twin.id}, this area's own," if satellite.picked(twin) else f"its copy {twin.id}, which runs here,"
                    result.notes.append(f"{command.id}: {why} says the same sentence")
                    command, behavior = twin, satellite.available(twin)
            if behavior is None:
                decision.skipped.append({"command": command.id, "why": satellite.why_unavailable(command)})
                continue
            decision.command = command
            decision.behavior = behavior
            decision.intent = result
            heard = match_slots(command, result.slots)
            # Fixed slots are the command's target; a name that was heard
            # is more specific.
            decision.slots = {**decision.behavior.slots, **heard}
            if command.behavior.trigger:
                # What Home Assistant hears: the sentence, as the trigger has
                # it -- a value list's value in place of what was said.
                sentence, values = triggers.heard_as((compiled.triggers or {}).get(command.id), result.example or "")
                decision.slots.update(values)
                decision.slots[triggers.SENTENCE] = sentence or recognition.tdt_transcript or recognition.transcript or ""
            refused = self.fit_devices(decision, compiled, satellite)
            if refused is not None:
                refusal = refusal or (result, decision.slots, refused)
                decision.command, decision.behavior, decision.intent, decision.slots = None, None, None, {}
                continue
            if refusal is not None:
                result.notes.append(f"{refusal[0].intent}, as good, was refused: {refusal[2]['text']}")
            heard_intent = command.intents.get(result.intent) or command.intent
            if heard_intent.get("wildcard"):
                self._reread_text(
                    decision, recognition, command_audio, frame_seconds,
                    heard_intent["wildcard"], transcriber,
                )
            return
        if refusal is not None:
            decision.intent, decision.slots, decision.refused = refusal

    def _hear_again(
        self,
        decision: Decision,
        recognition: Recognition,
        compiled: Compiled,
        recognizer: Recognizer,
        satellite: Satellite,
        command_audio: np.ndarray,
        transcriber: Any,
        why: str,
    ) -> None:
        """A doubtful match (``why``: ``cost``) runs only when the transcript
        agrees (the same command); one the transcript finds instead runs in
        its place, and none means nothing runs. In sessions, every wrong
        command run on keywords cost this much -- "pause the chicken timer"
        as resuming it, "cancel the timer" as the time -- and the
        transcripts said so. A match that named nothing (``unnamed``) gives
        way only to a version of it the transcript names a device, area or
        floor for. The transcript's match counts only when it is sure: as
        doubtful there, it says nothing."""
        if recognition.tdt_transcript is None:
            started = time.perf_counter()
            recognition.tdt_transcript = transcriber.transcribe(command_audio)
            _took(recognition.timings, _timing(transcriber), started)
        text = recognition.tdt_transcript or ""
        assert decision.command is not None and decision.intent is not None
        doubt: dict[str, Any] = {"command": decision.command.id, "cost": round(float(decision.intent.cost), 2), "transcript": text}
        again = Decision(recognition={}, text_source=decision.text_source)
        if text.strip():
            self._match_transcript(again, text, compiled, recognizer, satellite)
        decision.transcript_pass = again.transcript_pass
        decision.doubt = {"why": why, **doubt}
        doubt = decision.doubt
        if again.command is not None and again.intent is not None and _doubt(again.intent) >= recognizer.config.settings.unexplained_important_cost:
            # The transcript's match is as unsure: it says nothing ("what
            # day is Christmas" is the date's "what day", "is Christmas" left
            # over, in the transcript too -- and the dates skill's).
            doubt["transcript_cost"] = round(float(again.intent.cost), 2)
            again = Decision(recognition={})
        if why == "unnamed":
            versions = {c.id for c in kin(compiled, decision.command)}
            if again.command is not None and again.command.id in versions and _named(again):
                doubt["outcome"] = "named"
                decision.command, decision.behavior, decision.intent = again.command, again.behavior, again.intent
                decision.slots, decision.text_from_tdt = again.slots, again.text_from_tdt
                decision.matched_by = "transcript"
            else:
                doubt["outcome"] = "kept"
            return
        if again.command is not None and again.command.id == decision.command.id:
            doubt["outcome"] = "agrees"
        elif again.command is not None or again.refused is not None:
            doubt["outcome"] = "replaced" if again.command is not None else "refused"
            decision.command, decision.behavior, decision.intent = again.command, again.behavior, again.intent
            decision.slots, decision.refused, decision.text_from_tdt = again.slots, again.refused, again.text_from_tdt
            decision.matched_by = "transcript"
        else:
            doubt["outcome"] = "unconfirmed"
            decision.doubted = Decision(
                recognition={}, command=decision.command, behavior=decision.behavior, intent=decision.intent,
                slots=dict(decision.slots), text_from_tdt=dict(decision.text_from_tdt),
            )
            decision.command, decision.behavior, decision.intent, decision.slots = None, None, None, {}

    @staticmethod
    def _may_name(decision: Decision, compiled: Compiled, satellite: Satellite) -> bool:
        """Whether a match named nothing where a version of it, here, takes
        a device, area or floor by name."""
        assert decision.command is not None
        if _named(decision):
            return False
        return any(
            c.id != decision.command.id and c.intent.get("names") and satellite.available(c) is not None
            for c in kin(compiled, decision.command)
        )

    def _match_transcript(
        self, decision: Decision, transcript: str, compiled: Compiled, recognizer: Recognizer, satellite: Satellite
    ) -> None:
        """Nothing matched in the audio: the same matcher on the TDT or STT
        transcript, as typed text is matched ("Unlock front door." from Home
        Assistant Cloud, where the CTC head heard "front door" too poorly)."""
        started = time.perf_counter()
        spellings = spoken_words(transcript, recognizer.config.language)
        log_probs, unspellable = self.model.vocab.text_log_probs(spellings[0] if spellings else [])
        # Keywords and names only: the transcript was looked up among the
        # example sentences whole already, with the audio -- and on clean
        # text, a short example with words around it ("grab bananas *next*
        # time you shop") is no example said.
        heard = recognizer.recognize_log_probs(log_probs, self.model.frame_seconds, examples=False)
        before = (decision.command, decision.refused)
        self._take(decision, heard, compiled, satellite, np.zeros(0, dtype=np.float32), self.model.frame_seconds, TypedText(transcript))
        found = decision.command is not None and before[0] is None
        if found:
            decision.matched_by = "transcript"
        decision.transcript_pass = {
            "text": transcript,
            "command": decision.command.id if found else None,
            "matches": [{"intent": m.intent, "cost": round(m.cost, 2), "slots": m.slots} for m in heard.matches[:3]],
            "unspellable": unspellable,
            "seconds": round(time.perf_counter() - started, 4),
        }

    def fit_devices(self, decision: Decision, compiled: Compiled, satellite: Satellite) -> dict[str, Any] | None:
        """The devices a command names, by the names heard and where each is
        heard (the Names page): a name of the satellite's area's devices
        there, else a whole house name; with an area said, a name of that
        area's devices (heard so from elsewhere: ``with_area``), else a whole
        house name of one there. A name not heard from here, or one more than
        one device has there, runs nothing: the hint says why. The command's
        own target (a device it names itself) is as it is."""
        slots = decision.slots
        entities = [str(e) for e in slots.get("entity") or []]
        if not entities or decision.command is None:
            return None
        if decision.behavior is not None and [str(e) for e in decision.behavior.slots.get("entity") or []] == entities:
            return None
        devices = compiled.devices
        code = languages.current().code
        area_of = self.entity_area or (lambda _entity_id: None)

        def keys(name: str) -> set[str]:
            return {" ".join(units) for units in name_units(name, code)} | {phrase_key(name)}

        here_keys = {e: set().union(*(keys(n) for n in d.here)) for e, d in devices.items() if d.here}
        house_keys = {e: set().union(*(keys(n) for n in d.house)) for e, d in devices.items() if d.house}
        said_areas = [str(a) for a in slots.get("area") or []]
        own = satellite.area_id
        say = languages.speech()

        def room(area_id: str) -> str:
            area_item = ((compiled.data.get("names") or {}).get("areas") or {}).get(area_id) or {}
            return area_item if isinstance(area_item, str) else area_item.get("name") or area_id

        def refused(why: str) -> dict[str, Any]:
            return {"source": "place", "command": decision.command.id, "text": say.hint(decision.command.description, why)}

        def written(phrase: str) -> str:
            """The name heard as the Names page writes it."""
            return next((n for d in devices.values() for n in d.phrases if phrase in keys(n)), phrase)

        def in_area(phrase: str, area: str | None) -> list[str]:
            return [e for e, k in here_keys.items() if area and phrase in k and area_of(e) == area]

        found: list[str] = []
        for phrase, entity_id in self._names_heard(decision, entities, devices, keys):
            if phrase is None or entity_id not in devices:
                found.append(entity_id)  # named by what the Names page doesn't name, or without its words
                continue
            house = [e for e, k in house_keys.items() if phrase in k]
            label = written(phrase)
            if said_areas:
                hits: list[str] = []
                for area in said_areas:
                    there = [e for e in in_area(phrase, area) if area == own or devices[e].with_area]
                    there = there or [e for e in house if area_of(e) == area]
                    if len(there) > 1:
                        return refused(say.several(label))
                    hits += there
                if not hits:
                    heard_there = [e for area in said_areas for e in in_area(phrase, area)]
                    anywhere = [e for e in devices if phrase in here_keys.get(e, ()) or phrase in house_keys.get(e, ())]
                    if heard_there:  # a name heard only there
                        return refused(say.not_here(label, [room(said_areas[0])]))
                    if all(area_of(e) is None for e in anywhere):
                        return refused(say.no_room(label))
                    return refused(say.not_in(label, room(said_areas[0])))
            else:
                hits = in_area(phrase, own) or house
                if len(hits) > 1:
                    return refused(say.several(label))
                if not hits:
                    rooms = list(dict.fromkeys(room(area_of(e)) for e in here_keys if phrase in here_keys[e] and area_of(e)))
                    return refused(say.not_here(label, rooms))
            found += hits
        slots["entity"] = list(dict.fromkeys(found))
        return None

    @staticmethod
    def _names_heard(
        decision: Decision, entities: list[str], devices: dict[str, Device], keys: Any
    ) -> list[tuple[str | None, str]]:
        """Each device name heard, as ``(phrase, entity id)``: from the
        matcher's spans, else (the fallback's) the longest of the device's
        names in the transcript -- None when it isn't there."""
        result = decision.intent
        if result is not None:
            spans = [(u.meaning.phrase, u.meaning.name_id) for u in result.spans if u.role == "name" and u.meaning.name_type == "entity"]
            if spans:
                return [(phrase, str(entity_id)) for phrase, entity_id in spans]
        recognition = decision.recognition or {}
        text = recognition.get("tdt_transcript") or recognition.get("transcript") or ""
        words = re.findall(r"[^\W_]+", str(text).lower())
        said = f" {' '.join(words)} "
        out: list[tuple[str | None, str]] = []
        for entity_id in entities:
            device = devices.get(entity_id)
            heard = sorted(
                (k for n in (device.phrases if device is not None else ()) for k in keys(n) if f" {k} " in said),
                key=len, reverse=True,
            )
            out.append((heard[0] if heard else None, entity_id))
        return out

    def _greedy(self, log_probs: np.ndarray) -> str:
        ids, previous = [], None
        blank = self.model.vocab.blank_id
        for token in log_probs.argmax(axis=-1):
            token = int(token)
            if token != previous and token != blank:
                ids.append(token)
            previous = token
        return self.model.vocab.ids_to_text(ids)

    def _reread_text(
        self,
        decision: Decision,
        recognition: Recognition,
        command_audio: np.ndarray,
        frame_seconds: float,
        wildcard: dict[str, Any],
        transcriber: Any,
    ) -> None:
        """Replace free text as the CTC head spelled it with the transcript's
        words over the same stretch of audio -- the TDT head's, timed -- or,
        from a transcriber without word times (an STT entity), the words its
        keywords bound in the transcript."""
        slot = wildcard["slot"]
        if transcriber is None or decision.intent is None:
            return
        uses = [use for use in decision.intent.spans if use.role == "wildcard"]
        if not uses:
            return
        started = time.perf_counter()
        transcript, words = transcriber.transcribe_words(command_audio)
        _took(recognition.timings, _timing(transcriber), started)
        recognition.tdt_transcript = transcript
        if words is None:
            text = free_text(transcript, wildcard, str(decision.slots.get(slot) or "")) or ""
            # Text to the end stops before the place it was told apart from:
            # a room, a floor, a device ("on the Mark 2").
            places = [use.meaning.phrase for use in decision.intent.spans if use.role == "name"]
            config = self.recognizer.config if self.recognizer is not None else None
            filler = list(config.filler) if config is not None else []
            text = without_place(text, places, filler, config.language if config is not None else None)
            text = " ".join(trimmed(text.split(), self._edge_words(decision.intent.intent, wildcard))).strip()
        else:
            use = uses[0]
            start = use.hit.start * frame_seconds - frame_seconds
            end = (use.hit.end + 1) * frame_seconds + frame_seconds
            # A word is the text's when its middle is: a transducer may give
            # a word's start a frame early ("um halb sieben"), or a keyword's
            # end a frame late -- and at the text's edge, a keyword, filler or
            # what says a place follows is no text, as the CTC matcher reads
            # it ("pizza for", "jazz in").
            edges = self._edge_words(decision.intent.intent, wildcard)
            inside = trimmed(within(words, start, end), edges)
            # A keyword it misspelled is the keyword's, not the text's: "and
            # pick up groceries" for "add pick up groceries", the transducer's
            # "and" a few frames late.
            inside = trimmed(misheard_off(inside, str(decision.slots.get(slot) or ""), wildcard), edges)
            text = " ".join(inside).strip()
        if text and slot in decision.slots and text != str(decision.slots[slot]):
            # The transcript's words, and its case ("Kind of Blue").
            if text.lower() != str(decision.slots[slot]).lower():
                decision.text_from_tdt[slot] = str(decision.slots[slot])
            decision.slots[slot] = text

    def _edge_words(self, intent: str, wildcard: dict[str, Any]) -> set[str]:
        """Words free text does not start or end with: its intent's keywords
        and the matcher's fillers and name cues, and the language's words
        before a place ("in", "the")."""
        words = {str(k).lower() for k in [*(wildcard.get("after") or []), *(wildcard.get("before") or [])]}
        config = self.recognizer.config if self.recognizer is not None else None
        if config is not None:
            spec = config.intents.get(intent)
            phrases = [p for group in (spec.require + spec.optional if spec else ()) for p in group]
            words |= {part for phrase in [*phrases, *config.filler, *config.name_cues] for part in phrase.split()}
        read = languages.reading()
        return words | set(read.PLACE_BEFORE) | set(read.ARTICLES)

    # --------------------------------------------------------------- hints

    def _hint(
        self,
        recognition: Recognition,
        compiled: Compiled,
        satellite: Satellite,
        skipped: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """What the command looks like it was trying to do, and why it did
        not happen."""
        commands = compiled.commands
        say = languages.speech()
        with self._lock:
            recognizer = self.recognizer
        if skipped:
            command = commands[skipped[0]["command"]]
            return {"source": "unavailable", "command": command.id, "text": satellite.not_here()}

        candidates = []
        for result in recognition.rejected:
            command = commands.get(command_of(result.intent))
            if command is None or not result.missing:
                continue
            groups = len(command_groups(command))
            matched_constraints = bool(result.spans)
            if not matched_constraints and result.heard_groups == 0:
                continue  # nothing of it was heard
            if not matched_constraints and groups and result.heard_groups < max(1, groups - 1):
                continue  # too little of it was heard
            coverage = _coverage(recognition, recognizer, result.intent) if recognizer else 1.0
            # Heard whole but for a room, floor or device it can't take: that
            # is why, however much of the sentence was its free text ("play
            # jazz in the office").
            only_place = matched_constraints and all(m.get("kind") == "place" for m in result.missing)
            if coverage < HINT_COVERAGE and not only_place:
                continue
            rank = (
                0 if matched_constraints else 1,
                result.cost if matched_constraints else -coverage,
                len(result.missing),
            )
            candidates.append((rank, command, result))
        if candidates:
            _, command, result = min(candidates, key=lambda item: item[0])
            if satellite.available(command) is None:
                text = satellite.not_here()
            else:
                why = say.missing(_which(result.missing[0], command), command.duration, command.number_slot)
                text = say.hint(command.description, why)
            return {"source": "keywords", "command": command.id, "missing": result.missing, "text": text}

        if recognizer_has_meaning(self):
            texts = [t for t in (recognition.tdt_transcript, recognition.transcript) if t]
            closest = recognizer.closest_by_meaning(texts) if recognizer else None
            if closest is not None:
                margin = closest["similarity"] - (closest["next_similarity"] or 0.0)
                hint = {"source": "meaning", **closest, "margin": round(margin, 4)}
                hint["similarity"] = round(closest["similarity"], 4)
                if closest["next_similarity"] is not None:
                    hint["next_similarity"] = round(closest["next_similarity"], 4)
                command = commands.get(command_of(closest["intent"]))
                if (
                    command is not None
                    and closest["similarity"] >= HINT_MIN_SIMILARITY
                    and margin >= HINT_MARGIN
                ):
                    hint["command"] = command.id
                    here = satellite.available(command) is not None
                    hint["text"] = say.hint(command.description, say.PARTLY) if here else satellite.not_here()
                    return hint
                hint["text"] = say.SORRY
                return hint
        return {"source": None, "text": say.SORRY}


def _coverage(recognition: Recognition, recognizer: Recognizer, intent_name: str) -> float:
    """Share of the transcript's words (fillers aside) that something this
    intent listens for was heard on."""
    intent = recognizer.config.intents.get(intent_name)
    spotting = recognition.spotting
    if intent is None:
        return 0.0
    keywords = {
        part
        for group in intent.require + intent.optional
        for phrase in group
        for part in phrase.split(" ... ")
    }

    def relevant(meaning) -> bool:
        if meaning.kind == "keyword":
            return meaning.phrase in keywords
        if meaning.kind == "number":
            return intent.number is not None
        if meaning.kind == "name":
            return intent.name_count(meaning.name_type) > 0
        return False

    hits = [h for h in spotting.hits if h.heard and any(relevant(m) for m in h.meanings)]
    filler = {f for f in recognizer.config.filler if " " not in f}
    words = [
        w
        for w in spotting.words
        if w.frames and w.text.lower().strip(".,?!;:'\"") not in filler
    ]
    if not words:
        return 0.0
    covered = sum(
        1
        for w in words
        if 2 * sum(any(h.start <= f <= h.end for h in hits) for f in w.frames) >= len(w.frames)
    )
    return covered / len(words)


def recognizer_has_meaning(engine: Engine) -> bool:
    return engine.embedder is not None and engine.recognizer is not None


def command_groups(command: Command) -> list:
    return list(command.intent.get("require") or [])


def within(words: list[tuple[str, float, float]], start: float, end: float) -> list[str]:
    """The timed words whose middle is between ``start`` and ``end``
    (seconds), a word taken at most ``WORD_SECONDS`` long: the last word's
    end may run on through the silence after it ("et demie." to the end of
    the audio)."""
    return [_strip(w) for w, s, e in words if start <= (s + min(e, s + WORD_SECONDS)) / 2 <= end]


def misheard_off(words: list[str], heard: str, wildcard: dict[str, Any]) -> list[str]:
    """Timed free text without a misspelling of the keyword it follows (the
    wildcard's ``after``) or comes before (``before``) at that end -- "and"
    for "add", one letter off and the same first letter -- unless the CTC
    head's own spelling of the text (``heard``), which heard the keyword
    there, has such a word at that end too; never none of them."""
    theirs = heard.lower().split()

    def misheard(word: str, keywords: Any, edge: str | None) -> bool:
        word = word.lower()
        keys = {str(k).lower() for k in keywords or ()}
        return word not in keys and any(_one_off(word, k) for k in keys) and not (edge and (edge == word or _one_off(word, edge)))

    start, end = 0, len(words)
    while end - start > 1 and misheard(words[start], [str(k).split()[-1] for k in wildcard.get("after") or []], theirs[0] if theirs else None):
        start += 1
    while end - start > 1 and misheard(words[end - 1], [str(k).split()[0] for k in wildcard.get("before") or []], theirs[-1] if theirs else None):
        end -= 1
    return words[start:end]


def _one_off(a: str, b: str) -> bool:
    """One letter added, dropped or changed, the first letter the same."""
    if not a or not b or a == b or a[0] != b[0] or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    short, long = sorted((a, b), key=len)
    return any(long[:i] + long[i + 1:] == short for i in range(len(long)))


def trimmed(words: list[str], edges: set[str]) -> list[str]:
    """Free text's words without ``edges`` (lowercase) at either end; never
    none of them."""
    start, end = 0, len(words)
    while end - start > 1 and words[start].lower() in edges:
        start += 1
    while end - start > 1 and words[end - 1].lower() in edges:
        end -= 1
    return words[start:end]


def _strip(word: str) -> str:
    return re.sub(r"^[^\w']+|[^\w']+$", "", word)


def _norm(value: str) -> str:
    return re.sub(r"[\W_]+", "", value.lower())


def model_note(fallback: dict[str, Any] | None) -> str | None:
    """What the fallback's model step (GLiNER) came to when it ran no
    command, in a line (None when it was not asked)."""
    step = (fallback or {}).get("llm") or {}
    meaning = (fallback or {}).get("meaning") or {}
    if not step and meaning.get("problems"):
        # Meaning picked it; what was said in the sentence did not fit.
        return f"meaning picked {meaning['command']}, not run: {'; '.join(meaning['problems'])}"
    if not step:
        return None
    by = step.get("by", "GLiNER")
    if step.get("skipped"):
        return f"{by} skipped: {step['skipped']}"
    refused = step.get("refused")
    if refused:
        return f"{by} picked {refused['command']}, not run: {refused['why']}"
    if step.get("problems"):
        picked = f"{by} picked {step['command']}" if step.get("command") else f"{by}'s pick"
        return f"{picked}, not run: {'; '.join(step['problems'])}"
    surer = f"; {', '.join(step['surer'])} likelier, not chosen for it" if step.get("surer") else ""
    return f"{by}: {step.get('said') or 'not a command'}{surer}"


def _doubt(result: IntentResult) -> float:
    """What a match costs but its free text, which costs a little a word only
    so that a name is worth taking over it: the words it left unexplained, and
    how poorly its own were heard. Eight words of a reminder are as much as an
    unexplained word, and say nothing about whether it was heard right."""
    return result.cost - sum(use.hit.penalty for use in result.spans if use.role == "wildcard")


def _named(decision: Decision) -> bool:
    """Whether a decision heard a device, area or floor named (not one its
    command implies)."""
    fixed = decision.behavior.slots if decision.behavior is not None else {}
    return any(decision.slots.get(slot) and decision.slots.get(slot) != fixed.get(slot) for slot in NAME_SLOTS)


SKILL_LEAD_WORDS = 2
"""The most words before a question that a skill's answer may leave out
(``without_lead``): the end of the wake word heard after the cut ("group"
for "...boo", "the gp"), or an "um"."""


def without_lead(transcripts: Iterable[str], most: int = SKILL_LEAD_WORDS) -> Iterator[str]:
    """Each transcript without its first word, then its first two (up to
    ``most``), while two words or more are left."""
    texts = list(transcripts)
    for drop in range(1, most + 1):
        for text in texts:
            words = text.split()
            if len(words) >= drop + 2:
                yield " ".join(words[drop:])


def _has_words(text: str | None) -> bool:
    """Whether a transcript has a word in it, not only punctuation."""
    return bool(re.search(r"[^\W\d_]", text or ""))


def agreement(a: str, b: str) -> float:
    """How alike two transcripts' letters are, 0 to 1 (difflib's ratio):
    spaces, case and punctuation aside."""
    letters = [re.sub(r"[\W\d_]", "", text.lower()) for text in (a, b)]
    return difflib.SequenceMatcher(None, *letters).ratio() if all(letters) else 0.0


def sentence_time(sentence: dict[str, Any] | None) -> dict[str, float]:
    """The time the home's sentence took, for a decision's timings."""
    return {"sentence": sentence.pop("seconds")} if sentence and "seconds" in sentence else {}


def _took(timings: dict[str, float], key: str, started: float) -> None:
    """Time since ``started``, added to what ``key`` took before: a
    transcriber asked again (it answers from what it heard the first time)
    adds next to nothing, rather than leaving its first time out."""
    timings[key] = timings.get(key, 0.0) + time.perf_counter() - started


def _timing(transcriber: Any) -> str:
    source = getattr(transcriber, "source", "TDT")
    return "tdt" if source == "TDT" else "typed" if source == "typed" else "stt"


def without_place(text: str, places: list[str], filler: list[str] | None = None, language: str | None = None) -> str:
    """Free text without a place it ends on, that was heard as one -- a room,
    a floor, a device -- compared as spoken: "jazz in the kitchen" -> "jazz",
    "jazz upstairs please" -> "jazz", "jazz on the Mark 2" -> "jazz" (the
    name "Mark two"), "джаз на кухне" -> "джаз" (the Кухня, in the case it
    was said: the language's endings). Or one it starts with, said with what
    comes before a place: "in the kitchen dinner is ready" -> "dinner is
    ready", as the matcher reads it ("announce in the kitchen ..."). What may
    come before it is the language's reading (PLACE_BEFORE, ARTICLES)."""
    read = languages.reading()
    language = language or languages.current().code
    articles = set(read.ARTICLES)
    words = text.split()
    fill = {f.lower() for f in filler or []}
    end = len(words)
    while end > 0 and _strip(words[end - 1]).lower() in fill:
        end -= 1
    # Each place's words, each as any of its forms in a sentence, the first
    # also with an article joined ("l'entrée").
    shapes = []
    for key in {sentence_key(phrase, language) for phrase in places} - {""}:
        shape = [alternated(word_forms(word, endings(language)), alternations(language)) for word in key.split()]
        shape[0] = tuple(form.replace("'", "") for form in elided(shape[0], elisions(language)))  # as keys are
        shapes.append([set(forms) for forms in shape])

    def said(text: str) -> bool:
        heard = sentence_key(text, language).split()
        return any(len(heard) == len(shape) and all(w in forms for w, forms in zip(heard, shape)) for shape in shapes)

    # A head that is the place, "in the" before it; some text stays.
    if words and _strip(words[0]).lower() in read.PLACE_BEFORE:
        skip = 2 if len(words) > 1 and _strip(words[1]).lower() in articles else 1
        for stop in range(len(words) - 1, skip, -1):
            if said(" ".join(words[skip:stop])) and not all(_strip(w).lower() in fill | articles for w in words[stop:end]):
                words = words[stop:]
                end = len(words)
                while end > 0 and _strip(words[end - 1]).lower() in fill:
                    end -= 1
                break
    # The longest tail that is the place, with "in the" before it; some text stays.
    for start in range(1, end):
        tail = [_strip(w).lower() for w in words[start:end]]
        skip = 1 if tail[0] in read.PLACE_BEFORE else 0
        if skip < len(tail) and tail[skip] in articles:
            skip += 1
        if skip < len(tail) and said(" ".join(words[start + skip : end])):
            kept = words[:start]
            if all(_strip(w).lower() in fill | articles for w in kept):
                break  # nothing but "the" before it: the place is the text
            return " ".join(kept).strip()
    return " ".join(words)


def free_text(transcript: str, wildcard: dict[str, Any], heard: str = "") -> str | None:
    """A wildcard's text in a transcript without word times, found as the
    recognizer found it: after one of its ``after`` keywords (to the end, or
    up to a ``before`` keyword), or before one of its ``before`` keywords; as
    many words as the CTC head heard there when only one edge bounds it.

    A keyword can come more than once ("how much *time* is left on the pizza
    *timer*"), so every place it could be is read, and the one closest to what
    the CTC head heard (``heard``) wins."""
    words = [_strip(w) for w in transcript.split()]
    words = [w for w in words if w]
    lower = [w.lower() for w in words]

    def find(phrases: list[str]) -> list[tuple[int, int]]:
        found = []
        for phrase in phrases:
            parts = [p for p in str(phrase).lower().split() if p != "..."]
            for i in range(len(lower) - len(parts) + 1):
                if parts and lower[i : i + len(parts)] == parts:
                    found.append((i, i + len(parts)))
        return sorted(found)

    count = max(1, len(heard.split())) if heard else int(wildcard.get("max_words") or 3)
    count = min(count, int(wildcard.get("max_words") or count))
    afters = find(wildcard.get("after") or [])
    befores = find(wildcard.get("before") or [])
    bounded, to_end = bool(wildcard.get("bounded")), bool(wildcard.get("to_end"))
    candidates: list[list[str]] = []
    for _, start in afters:
        if to_end:
            candidates.append(words[start:])
            continue
        ends = [b for b, _ in befores if b > start]
        if ends:
            candidates.append(words[start : ends[0]])
        if not bounded:
            # Unbounded, a later keyword need not end it ("a timer named
            # lasagna for the time being").
            candidates.append(words[start : start + count])
    if not bounded and not to_end:
        candidates += [words[max(0, end - count) : end] for end, _ in befores]
    candidates = [c for c in candidates if c]
    if not candidates:
        return None
    if heard:
        target = _norm(heard)
        best = max(
            candidates,
            key=lambda c: (difflib.SequenceMatcher(None, _norm(" ".join(c)), target).ratio(), -abs(len(c) - count)),
        )
    else:
        best = candidates[0]
    return " ".join(best).strip() or None


def _which(missing: dict[str, Any], command: Command) -> dict[str, Any]:
    """A device name missing, as what the command names: a scene or a script
    when those are all it listens for."""
    domains = list(((command.intent.get("names") or {}).get("entity") or {}).get("domains") or [])
    if missing.get("kind") == "name" and missing.get("type") == "entity" and domains in (["scene"], ["script"]):
        return {**missing, "type": domains[0]}
    return missing
