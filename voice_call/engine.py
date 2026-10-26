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
   transcript against the examples, word for word.
3. **A doubtful match is heard again.** One that costs as much as a word it
   left unexplained ("we resume the chicken pder" as resuming the music,
   "chicken" left over) runs only when the TDT or STT transcript agrees: the
   same matcher on the transcript finds the same command. A command it finds
   instead runs in its place; finding none, nothing runs.
4. **Where it is available.** Matches are taken best first, skipping commands
   that are not on this satellite (they are on some others). Of matches as
   good as each other, and of example sentences a copy also has, one on some
   satellites, this one among them, goes first.
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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from adapt_ctc.acoustic import CtcModel
from adapt_ctc.config import WakeConfig, parse_config
from adapt_ctc.matcher import IntentResult, Recognition, Recognizer
from adapt_ctc.morphology import alternated, alternations, elided, elisions, endings, word_forms
from adapt_ctc.report import to_json
from adapt_ctc.text import sentence_key, spoken_words
from adapt_ctc.wake import LEAD, WakeVerifier

from adapt_ctc.config import ConfigError, _phrase

from . import languages, skills
from .commands import DYNAMIC_LISTS, Behavior, Command, base_config, command_of, match_slots
from .fallback import label_of
from .store import Compiled
from .voices import TypedText, VoiceChoice

_LOGGER = logging.getLogger(__name__)

SAMPLE_RATE = 16000

# How Parakeet writes "okay nabu": it has no word "Nabu". Placement only, so no
# confusables: Kaldi has already decided it was the wake word.
WAKE_PHRASES = ("okay nabu", "okay naboo", "ok nabu", "ok naboo", "okay now")
SPLIT_BOUND = 0.3
"""Seconds past Kaldi's wake word end that the wake phrase may still end."""

WORD_SECONDS = 0.5
"""How long a transducer's word is taken to be when it is placed in free
text: its end may run on through the silence after it."""

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
    """Its device in Home Assistant (the Voice Call integration's)."""
    timer_list: str | None = None
    """Its voice_timer_list entity."""
    media_player: str | None = None
    """Its Music Assistant player (its Sendspin client's)."""
    voice: VoiceChoice = field(default_factory=VoiceChoice)
    """Home Assistant STT and TTS entities, when not the built-in models."""
    device_media_player: str | None = None
    """Its own media player in Home Assistant (ESPHome's), where TTS speaks."""
    skills: frozenset[str] | None = None
    """The skills that answer here (voice_call/skills/); None: every one."""

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
            "skills": sorted(self.skills) if self.skills is not None else None,
        }

    def available(self, command: Command) -> Behavior | None:
        """What a command does here; None where it is not: it is on some
        other satellites."""
        here = command.satellites is None or self.key in command.satellites
        return command.behavior if here else None

    def picked(self, command: Command) -> bool:
        """It is on some satellites, this one among them."""
        return command.satellites is not None and self.key in command.satellites

    def first(self, commands: list[Command]) -> list[Command]:
        """The ones available here, those picked for it first."""
        return sorted((c for c in commands if self.available(c) is not None), key=lambda c: not self.picked(c))

    def why_unavailable(self, command: Command) -> str:
        return "not on this satellite"


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
    same matcher on the TDT or STT transcript (``transcript``), or the
    fallback (``meaning``, ``gliner``)."""
    transcript_pass: dict[str, Any] | None = None
    """Nothing matched in the audio, or what did was doubtful: what the
    keyword matcher made of the transcript."""
    doubt: dict[str, Any] | None = None
    """A match that cost as much as a word it left unexplained: the command,
    its cost, the transcript, and what came of hearing it again: it
    ``agrees``, another command ``replaced`` it, one was ``refused`` as said,
    or none was found (``unconfirmed``)."""
    fallback: dict[str, Any] | None = None
    """What the fallback tried, when nothing matched (voice_call/fallback.py)."""

    answer: dict[str, Any] | None = None
    """No command: a question, answered by a skill (voice_call/skills/)."""

    timings: dict[str, float] = field(default_factory=dict)

    @property
    def transcript(self) -> str:
        return self.recognition.get("tdt_transcript") or self.recognition.get("transcript") or ""

    def to_json(self) -> dict[str, Any]:
        return {
            "command": self.command.id if self.command else None,
            "description": self.command.description if self.command else None,
            "does": self.behavior.does if self.behavior else None,
            "slots": self.slots,
            "skipped": self.skipped,
            "matched_by": self.matched_by if self.command else None,
            "fallback": self.fallback,
            "transcript_pass": self.transcript_pass,
            "doubt": self.doubt,
            "answer": self.answer,
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
        """When nothing matched: meaning, then GLiNER (voice_call/fallback.py)."""
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
            )
        with self._lock:
            self.compiled = compiled
            self.recognizer = recognizer
            self.unspeakable = list(recognizer.unspeakable) if recognizer else []
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
        else:
            end = int((kaldi_split - offset) / frame_seconds) - 1
        split.cut = offset + (end + 1) * frame_seconds
        return log_probs[end + 1 :], frame_seconds, split

    def recognize_call(
        self,
        audio: np.ndarray,
        wake_start: float | None,
        kaldi_split: float,
        satellite: Satellite,
        transcriber: Any = None,
        wake_phrases: tuple[str, ...] | None = None,
    ) -> tuple[Decision, Split]:
        """A whole call, from the start of its pre-roll, after Kaldi accepted
        the wake word. ``transcriber`` stands in for the TDT head (an STT
        entity, see voice_call/voices.py)."""
        started = time.perf_counter()
        log_probs, frame_seconds, split = self.place_command(audio, wake_start, kaldi_split, wake_phrases)
        placed = time.perf_counter() - started
        command_audio = audio[int(split.cut * SAMPLE_RATE) :]
        decision = self.decide(log_probs, frame_seconds, command_audio, satellite, transcriber)
        decision.timings = {"acoustic+placement": placed, **decision.timings}
        return decision, split

    def recognize_command(self, audio: np.ndarray, satellite: Satellite, transcriber: Any = None) -> Decision:
        """Audio that is only the command (typed and synthesized, or uploaded)."""
        started = time.perf_counter()
        log_probs, frame_seconds = self.model.log_probs(audio)
        decision = self.decide(log_probs, frame_seconds, audio, satellite, transcriber)
        decision.timings = {"acoustic": time.perf_counter() - started, **decision.timings}
        return decision

    def recognize_text(self, text: str, satellite: Satellite) -> Decision:
        """Typed text, as if it had been said and heard word for word: the
        CTC matcher reads evidence made from its spoken words (no audio, no
        speech recognition), and the text itself is the transcript."""
        started = time.perf_counter()
        with self._lock:
            recognizer = self.recognizer
        language = recognizer.config.language if recognizer is not None else languages.current().code
        spellings = spoken_words(text, language)
        log_probs, unspellable = self.model.vocab.text_log_probs(spellings[0] if spellings else [])
        built = time.perf_counter() - started
        decision = self.decide(
            log_probs, self.model.frame_seconds, np.zeros(0, dtype=np.float32), satellite, TypedText(text)
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
    ) -> Decision:
        """What the command comes to. The CTC matcher always runs; text --
        examples not found in the CTC evidence, free text, hints -- comes from
        ``transcriber`` when given, else the TDT head, and only when needed."""
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

        self._take(decision, recognition, compiled, satellite, command_audio, frame_seconds, transcriber)
        if (
            decision.command is not None
            and not typed
            and transcriber is not None
            and decision.intent is not None
            and decision.intent.cost >= recognizer.config.settings.unexplained_important_cost
        ):
            self._hear_again(decision, recognition, compiled, recognizer, satellite, command_audio, transcriber)

        if decision.command is None and decision.refused is None and decision.doubt is None:
            if transcriber is not None and recognition.tdt_transcript is None:
                started = time.perf_counter()
                recognition.tdt_transcript = transcriber.transcribe(command_audio)
                _took(recognition.timings, _timing(transcriber), started)
            # The keywords and names, on the transcript: a strong speech model
            # may have heard plainly what the CTC evidence only half did.
            if recognition.tdt_transcript and getattr(transcriber, "source", "TDT") != "typed":
                self._match_transcript(decision, recognition.tdt_transcript, compiled, recognizer, satellite)
        if decision.command is None and decision.refused is None:
            # Each transcript, TDT's first: one may hear "fifty degrees F in
            # Celsius" where the other heard "FN Celsius".
            transcripts = dict.fromkeys(t for t in (recognition.tdt_transcript, recognition.transcript) if t)
            exact = next(filter(None, (skills.answer(t, satellite.skills, self.skill_settings) for t in transcripts)), None)
            if exact is not None:
                # Spelling, dates, units, arithmetic: exact, and instant.
                decision.answer = exact.to_json()
            elif self.fallback is not None and compiled.commands:
                self._fall_back(decision, recognition, compiled, satellite)
        if decision.refused is not None:
            decision.hint = decision.refused
            decision.message = decision.refused["text"]
        elif decision.command is None and decision.answer is None:
            decision.hint = self._hint(recognition, compiled, satellite, decision.skipped)
            picked = (decision.fallback or {}).get("llm") or {}
            say = languages.speech()
            if picked.get("command") in compiled.commands:
                # GLiNER knew what was meant but not all of it.
                command = compiled.commands[picked["command"]]
                why = say.NOT_HERE if satellite.available(command) is None else say.PARTLY
                decision.hint = {"source": "gliner", "command": command.id, "text": say.hint(command.description, why)}
            if not compiled.commands:
                # Only the app's own commands are listened for.
                decision.hint = {"source": None, "text": say.NO_COMMANDS}
            decision.message = decision.hint["text"]

        decision.recognition = to_json(recognition)
        decision.timings = dict(recognition.timings)
        # Who wrote the transcript, once it was asked for: the TDT head stands
        # in for an STT entity that failed or heard nothing.
        decision.text_source = getattr(transcriber, "source", decision.text_source)
        return decision

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
        decision.matched_by = found.by or "gliner"
        refused = self.fit_devices(decision, compiled, satellite)
        if refused is not None:
            decision.command, decision.behavior, decision.refused = None, None, refused

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
        Of matches that cost the same, one picked for this satellite goes
        first."""

        def mine_first(result) -> tuple[float, bool]:
            command = compiled.commands.get(command_of(result.intent))
            return (result.cost, not (command is not None and satellite.picked(command)))

        for result in sorted(recognition.matches, key=mine_first):
            command = compiled.commands.get(command_of(result.intent))
            if command is None:
                continue
            behavior = satellite.available(command)
            if result.example and not satellite.picked(command):
                # A copy of it says the same sentence: one picked for this
                # satellite, or one it may run where it may not run this one.
                twins = [compiled.commands.get(command_of(t)) for t in compiled.twins.get(result.intent, ())]
                twin = next(iter(satellite.first([t for t in twins if t is not None])), None)
                if twin is not None and (behavior is None or satellite.picked(twin)):
                    why = f"its copy {twin.id}, picked for this satellite," if satellite.picked(twin) else f"its copy {twin.id}, which runs here,"
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
            refused = self.fit_devices(decision, compiled, satellite)
            if refused is not None:
                decision.command, decision.behavior, decision.refused = None, None, refused
                return
            heard_intent = command.intents.get(result.intent) or command.intent
            if heard_intent.get("wildcard"):
                self._reread_text(
                    decision, recognition, command_audio, frame_seconds,
                    heard_intent["wildcard"], transcriber,
                )
            return

    def _hear_again(
        self,
        decision: Decision,
        recognition: Recognition,
        compiled: Compiled,
        recognizer: Recognizer,
        satellite: Satellite,
        command_audio: np.ndarray,
        transcriber: Any,
    ) -> None:
        """A doubtful match runs only when the transcript agrees (the same
        command); one the transcript finds instead runs in its place, and
        none means nothing runs. In sessions, every wrong command run on
        keywords cost this much -- "pause the chicken timer" as resuming it,
        "cancel the timer" as the time -- and the transcripts said so."""
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
        decision.doubt = doubt
        if again.command is not None and again.command.id == decision.command.id:
            doubt["outcome"] = "agrees"
        elif again.command is not None or again.refused is not None:
            doubt["outcome"] = "replaced" if again.command is not None else "refused"
            decision.command, decision.behavior, decision.intent = again.command, again.behavior, again.intent
            decision.slots, decision.refused, decision.text_from_tdt = again.slots, again.refused, again.text_from_tdt
            decision.matched_by = "transcript"
        else:
            doubt["outcome"] = "unconfirmed"
            decision.command, decision.behavior, decision.intent, decision.slots = None, None, None, {}

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
        """The devices a command names, by where they are. Every device with
        the name heard is a candidate (names may repeat: a Fan in the office
        and one in the bedroom). Said with a room, they are the ones there --
        none there, and nothing runs: the hint says why. Without one, a name
        shared by several is the one in the satellite's area, when one is."""
        slots = decision.slots
        entities = list(slots.get("entity") or [])
        if not entities or decision.command is None:
            return None
        names = (compiled.data.get("names") or {}).get("entities") or {}

        def said(entity_id: str) -> set[str]:
            item = names.get(entity_id) or {}
            item = {"name": item} if isinstance(item, str) else item
            return {" ".join(str(n).lower().split()) for n in [item.get("name") or entity_id, *(item.get("aliases") or [])]}

        candidates = list(dict.fromkeys(
            other for entity_id in entities for other in [entity_id, *names] if other == entity_id or said(other) & said(entity_id)
        ))
        where = {c: self.entity_area(c) if self.entity_area is not None else None for c in candidates}
        areas = list(slots.get("area") or [])
        if not areas:
            if len(candidates) > len(entities):
                here = [c for c in candidates if satellite.area_id and where[c] == satellite.area_id]
                if here:
                    slots["entity"] = here
            return None
        fit = [c for c in candidates if where[c] in areas]
        if fit:
            slots["entity"] = fit
            return None
        item = names.get(entities[0]) or {}
        device = item if isinstance(item, str) else item.get("name") or entities[0]
        area_item = ((compiled.data.get("names") or {}).get("areas") or {}).get(areas[0]) or {}
        room = area_item if isinstance(area_item, str) else area_item.get("name") or areas[0]
        say = languages.speech()
        why = say.no_room(device) if all(where[c] is None for c in candidates) else say.not_in(device, room)
        return {"source": "place", "command": decision.command.id, "text": say.hint(decision.command.description, why)}

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
            inside = within(words, start, end)
            text = " ".join(trimmed(inside, self._edge_words(decision.intent.intent, wildcard))).strip()
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
            return {
                "source": "unavailable",
                "command": command.id,
                "text": f"It looks like you're trying to {command.description}, "
                "but that isn't set up here.",
            }

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
                why = say.NOT_HERE
            else:
                why = say.missing(result.missing[0], command.duration, command.number_slot)
            return {
                "source": "keywords",
                "command": command.id,
                "missing": result.missing,
                "text": say.hint(command.description, why),
            }

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
                    why = say.PARTLY if satellite.available(command) is not None else say.NOT_HERE
                    hint["text"] = say.hint(command.description, why)
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
    return f"{by}: {step.get('said') or 'not a command'}"


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
    was said: the language's endings). What may come before it is the
    language's reading (PLACE_BEFORE, ARTICLES)."""
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
    return text


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
