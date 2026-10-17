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
3. **Where it is available.** Matches are taken best first, skipping commands
   that are not available on this satellite (not in its command groups).
4. **Free text** is re-read from the TDT transcript over the same stretch of
   audio, which spells far better than the CTC head ("lasagna", not "Lasaga").
5. **When nothing matched**, the closest command is named: from the keyword
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
from adapt_ctc.report import to_json
from adapt_ctc.text import sentence_key, spoken_words
from adapt_ctc.wake import LEAD, WakeVerifier

from adapt_ctc.config import ConfigError, _phrase

from . import skills
from .commands import BASE_CONFIG, DYNAMIC_LISTS, Behavior, Command, command_of, match_slots
from .store import Compiled
from .voices import TypedText, VoiceChoice

_LOGGER = logging.getLogger(__name__)

SAMPLE_RATE = 16000

# How Parakeet writes "okay nabu": it has no word "Nabu". Placement only, so no
# confusables: Kaldi has already decided it was the wake word.
WAKE_PHRASES = ("okay nabu", "okay naboo", "ok nabu", "ok naboo", "okay now")
SPLIT_BOUND = 0.3
"""Seconds past Kaldi's wake word end that the wake phrase may still end."""

# Naming the closest command by meaning: only when it is this similar, and
# this much closer than the runner-up (another command or an everyday
# request). Hints only, so looser than adapt-ctc's acceptance margin.
HINT_MIN_SIMILARITY = 0.6
HINT_MARGIN = 0.02
HINT_COVERAGE = 0.5
"""A command is named from keyword evidence only when what it listens for
(keywords, names, numbers) covers at least this share of the words that are
not fillers: "set" alone does not make "set a timer" a temperature."""

NAME_WORDS = {"entity": "which device", "area": "which room", "floor": "which floor"}
NO_COMMANDS = "No voice commands are set up yet. Add some in the Voice Call app."

# The app's own commands, recognized like the rest but handled here.
LEARN_VOICE = "voice_call_learn_voice"
INTERNAL_INTENTS: dict[str, dict[str, Any]] = {
    LEARN_VOICE: {
        "description": "learn a new voice",
        "require": [["learn"], ["voice"]],
        "optional": [["new"], ["for"]],
        "wildcard": {
            "slot": "speaker", "after": ["for"], "to_end": True, "required": True, "max_words": 3,
        },
    }
}


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
    command_groups: frozenset[str] | None = None
    """It runs only the commands in these command groups; None: every one."""
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
            "command_groups": sorted(self.command_groups) if self.command_groups is not None else None,
            "skills": sorted(self.skills) if self.skills is not None else None,
        }

    def available(self, command: Command) -> Behavior | None:
        """What a command does here; None where it is not available (not in
        this satellite's command groups)."""
        return command.behavior if command.allowed(self.command_groups) else None

    def why_unavailable(self, command: Command) -> str:
        return "not in this satellite's command groups"


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

    internal: str | None = None
    """One of the app's own commands (INTERNAL_INTENTS) matched."""

    text_from_tdt: dict[str, str] = field(default_factory=dict)
    """Free-text slots re-read from the TDT transcript (or the STT entity's):
    slot -> as the CTC head heard it."""

    text_source: str = "TDT"
    """What wrote the transcript: the TDT head, or an STT entity."""

    matched_by: str = "ctc"
    """What found the command: the CTC matcher on the audio (``ctc``), the
    same matcher on the TDT or STT transcript (``transcript``), or the
    fallback (``meaning``, ``gliner``, ``llm``)."""
    transcript_pass: dict[str, Any] | None = None
    """Nothing matched in the audio: what the keyword matcher made of the
    transcript."""
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
            "command": self.command.id if self.command else self.internal,
            "description": self.command.description if self.command else None,
            "does": self.behavior.does if self.behavior else None,
            "slots": self.slots,
            "skipped": self.skipped,
            "matched_by": self.matched_by if self.command else None,
            "fallback": self.fallback,
            "transcript_pass": self.transcript_pass,
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
    Rebuilding after an edit then costs only the new sentences."""

    def __init__(self, embedder) -> None:
        self._embedder = embedder
        self._cache: dict[str, np.ndarray] = {}

    def embed(self, texts: list[str], document: bool = False) -> np.ndarray:
        if not document:
            return self._embedder.embed(texts, document)
        missing = [t for t in dict.fromkeys(texts) if t not in self._cache]
        if missing:
            for text, vector in zip(missing, self._embedder.embed(missing, True)):
                self._cache[text] = vector
        if not texts:
            return np.zeros((0, 768), dtype=np.float32)
        return np.stack([self._cache[t] for t in texts])


class Engine:
    def __init__(
        self,
        model_dir: Path,
        tdt_dir: Path | None = None,
        embedding_dir: Path | None = None,
        threads: int = 2,
        internal: tuple[str, ...] = (),
    ) -> None:
        self.internal = {name: INTERNAL_INTENTS[name] for name in internal}
        self.model = CtcModel(model_dir, num_threads=threads)
        self.threads = threads
        self.transcriber = self._load_tdt(tdt_dir)
        self.tdt_dir = Path(tdt_dir) if self.transcriber is not None and tdt_dir is not None else None
        self.embedder = None
        if embedding_dir is not None and (Path(embedding_dir) / "onnx/model_q4.onnx").is_file():
            try:
                from adapt_ctc.embedding import Embedder  # pylint: disable=import-outside-toplevel

                self.embedder = CachedEmbedder(Embedder(embedding_dir, threads))
            except ImportError:
                _LOGGER.warning("tokenizers is not installed; no hints by meaning")
        elif embedding_dir is not None:
            _LOGGER.info("No embedding model at %s; hints come from keywords only", embedding_dir)
        self._placers: dict[tuple[str, ...], WakeVerifier] = {}
        self.placer = self.placer_for(WAKE_PHRASES)
        self.fallback: Any = None
        """When nothing matched: meaning, then an LLM (voice_call/fallback.py)."""
        self.agent_answers = False
        """A Home Assistant conversation agent gets what nothing here took
        (the server asks it, voice_call/agent.py): the live skill waits for it."""
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
        config = copy.deepcopy(compiled.config or {**BASE_CONFIG, "intents": {}})
        config["intents"].update(copy.deepcopy(self.internal))
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
        language = recognizer.config.language if recognizer is not None else BASE_CONFIG["language"]
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
            decision.message = NO_COMMANDS
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

        if decision.command is None and decision.internal is None and decision.refused is None:
            if transcriber is not None and recognition.tdt_transcript is None:
                started = time.perf_counter()
                recognition.tdt_transcript = transcriber.transcribe(command_audio)
                recognition.timings[_timing(transcriber)] = time.perf_counter() - started
            # The keywords and names, on the transcript: a strong speech model
            # may have heard plainly what the CTC evidence only half did.
            if recognition.tdt_transcript and getattr(transcriber, "source", "TDT") != "typed":
                self._match_transcript(decision, recognition.tdt_transcript, compiled, recognizer, satellite)
        if decision.command is None and decision.internal is None and decision.refused is None:
            text = recognition.tdt_transcript or recognition.transcript or ""
            # Each transcript, TDT's first: one may hear "fifty degrees F in
            # Celsius" where the other heard "FN Celsius".
            transcripts = dict.fromkeys(t for t in (recognition.tdt_transcript, recognition.transcript) if t)
            exact = next(filter(None, (skills.answer(t, satellite.skills) for t in transcripts)), None)
            if exact is not None:
                # Spelling, dates, units, arithmetic: exact, and instant.
                decision.answer = exact.to_json()
            elif self.fallback is not None and compiled.commands:
                self._fall_back(decision, recognition, compiled, satellite)
            if decision.command is None and decision.answer is None and decision.refused is None and not self.agent_answers:
                # Last, so the fallback could still take "do I need an
                # umbrella" -- and after the agent, when there is one.
                answer = skills.answer(text, satellite.skills, last=True)
                if answer is not None:
                    decision.answer = answer.to_json()
        if decision.refused is not None:
            decision.hint = decision.refused
            decision.message = decision.refused["text"]
        elif decision.command is None and decision.internal is None and decision.answer is None:
            decision.hint = self._hint(recognition, compiled, satellite, decision.skipped)
            llm = (decision.fallback or {}).get("llm") or {}
            if llm.get("command") in compiled.commands:
                # The LLM knew what was meant but not all of it.
                command = compiled.commands[llm["command"]]
                why = "but that isn't set up here" if satellite.available(command) is None else "but I didn't understand all of it"
                decision.hint = {"source": "llm", "command": command.id, "text": f"It looks like you're trying to {command.description}, {why}."}
            if not compiled.commands:
                # Only the app's own commands are listened for.
                decision.hint = {"source": None, "text": NO_COMMANDS}
            decision.message = decision.hint["text"]

        decision.recognition = to_json(recognition)
        decision.timings = dict(recognition.timings)
        return decision

    def _fall_back(self, decision: Decision, recognition: Recognition, compiled: Compiled, satellite: Satellite) -> None:
        """Nothing matched: the closest command by meaning, then the LLM."""
        with self._lock:
            recognizer = self.recognizer
        texts = [t for t in (recognition.tdt_transcript, recognition.transcript) if t]

        def closest() -> dict[str, Any] | None:
            if recognizer is None or self.embedder is None:
                return None
            return recognizer.closest_by_meaning(texts)

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
        decision.matched_by = found.by or "llm"
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
        its command, behavior and slots into ``decision`` (or an internal
        command, or a refusal)."""
        for result in recognition.matches:
            if result.intent in self.internal:
                decision.internal = result.intent
                decision.intent = result
                decision.slots = dict(result.slots)
                spec = self.internal[result.intent]
                if spec.get("wildcard"):
                    self._reread_text(
                        decision, recognition, command_audio, frame_seconds, spec["wildcard"], transcriber
                    )
                return
            command = compiled.commands.get(command_of(result.intent))
            if command is None:
                continue
            behavior = satellite.available(command)
            if behavior is None and result.example:
                # A copy of it in this satellite's groups says the same sentence.
                twin = next(
                    (
                        twin_command
                        for twin_command in (compiled.commands.get(command_of(t)) for t in compiled.twins.get(result.intent, ()))
                        if twin_command is not None and satellite.available(twin_command) is not None
                    ),
                    None,
                )
                if twin is not None:
                    result.notes.append(f"{command.id} is not in this satellite's command groups: its copy {twin.id} says the same sentence")
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
        before = (decision.command, decision.internal, decision.refused)
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
        if all(where[c] is None for c in candidates):
            why = f"but I don't know which room {device} is in"
        else:
            from .actions import _in_area  # pylint: disable=import-outside-toplevel

            why = f"but there's no {device} {_in_area(room)}"
        return {
            "source": "place",
            "command": decision.command.id,
            "text": f"It looks like you're trying to {decision.command.description}, {why}.",
        }

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
        recognition.timings[_timing(transcriber)] = time.perf_counter() - started
        recognition.tdt_transcript = transcript
        if words is None:
            text = free_text(transcript, wildcard, str(decision.slots.get(slot) or "")) or ""
            # Text to the end stops before the place it was told apart from:
            # a room, a floor, a device ("on the Mark 2").
            places = [use.meaning.phrase for use in decision.intent.spans if use.role == "name"]
            config = self.recognizer.config if self.recognizer is not None else None
            filler = list(config.filler) if config is not None else []
            text = without_place(text, places, filler, config.language if config is not None else "en")
        else:
            use = uses[0]
            start = use.hit.start * frame_seconds - frame_seconds
            end = (use.hit.end + 1) * frame_seconds + frame_seconds
            inside = [w for w, s, e in words if s >= start and (s + e) / 2 <= end]
            text = " ".join(_strip(w) for w in inside).strip()
        if text and slot in decision.slots and text != str(decision.slots[slot]):
            # The transcript's words, and its case ("Kind of Blue").
            if text.lower() != str(decision.slots[slot]).lower():
                decision.text_from_tdt[slot] = str(decision.slots[slot])
            decision.slots[slot] = text

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
            available = satellite.available(command) is not None
            why = (
                "but that isn't set up here"
                if not available
                else _missing_text(command, result.missing)
            )
            return {
                "source": "keywords",
                "command": command.id,
                "missing": result.missing,
                "text": f"It looks like you're trying to {command.description}, {why}.",
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
                    why = (
                        "but I didn't understand all of it"
                        if satellite.available(command) is not None
                        else "but that isn't set up here"
                    )
                    hint["text"] = f"It looks like you're trying to {command.description}, {why}."
                    return hint
                hint["text"] = "Sorry, I didn't understand that."
                return hint
        return {"source": None, "text": "Sorry, I didn't understand that."}


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


def _missing_text(command: Command, missing: list[dict[str, Any]]) -> str:
    first = missing[0]
    kind = first.get("kind")
    if kind == "require":
        if first.get("slot"):
            return f"but I didn't hear which {first['slot'].replace('_', ' ')}"
        return f"but I didn't hear \"{first['phrases'][0]}\""
    if kind == "name":
        return f"but I didn't hear {NAME_WORDS.get(first['type'], 'which one')}"
    if kind == "number":
        if first.get("heard"):
            return f"but {first['heard'][0]} is out of range"
        if command.duration:
            return "but I didn't hear how long"
        what = command.number_slot.replace("_", " ")
        return "but I didn't hear a number" if what == "number" else f"but I didn't hear the {what}"
    if kind == "text":
        return f"but I didn't hear the {first['slot'].replace('_', ' ')}"
    if kind == "unexplained":
        words = " ".join(first.get("words") or [])
        return f"but I didn't understand \"{words}\"" if words else "but I didn't understand all of it"
    if kind == "unknown_name":
        return f"but I don't know \"{' '.join(first.get('words') or [])}\""
    if kind == "place":
        # A room, floor or device it can't be pointed at: "play music in the office".
        what = {"floor": "floor", "entity": "device"}.get(str(first.get("type")), "room")
        return f"but I can't pick a {what} for it"
    return "but I wasn't sure"


def _strip(word: str) -> str:
    return re.sub(r"^[^\w']+|[^\w']+$", "", word)


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def llm_note(fallback: dict[str, Any] | None) -> str | None:
    """What the fallback's model step (GLiNER or an LLM) came to when it ran
    no command, in a line (None when it was not asked)."""
    llm = (fallback or {}).get("llm") or {}
    meaning = (fallback or {}).get("meaning") or {}
    if not llm and meaning.get("problems"):
        # Meaning picked it; what was said in the sentence did not fit.
        return f"meaning picked {meaning['command']}, not run: {'; '.join(meaning['problems'])}"
    if not llm:
        return None
    by = llm.get("by", "LLM")
    if llm.get("skipped"):
        return f"{by} skipped: {llm['skipped']}"
    refused = llm.get("refused")
    if refused and "similarity" in refused:
        return (
            f"{by} picked {refused['command']}, not close enough in meaning to run "
            f"({refused['similarity']:.2f}, needs {refused['floor']:.2f})"
        )
    if refused:
        return f"{by} picked {refused['command']}, not run: {refused['why']}"
    if llm.get("problems"):
        picked = f"{by} picked {llm['command']}" if llm.get("command") else f"{by}'s pick"
        return f"{picked}, not run: {'; '.join(llm['problems'])}"
    return f"{by}: {llm.get('said') or 'not a command'}"


def _timing(transcriber: Any) -> str:
    source = getattr(transcriber, "source", "TDT")
    return "tdt" if source == "TDT" else "typed" if source == "typed" else "stt"


def without_place(text: str, places: list[str], filler: list[str] | None = None, language: str = "en") -> str:
    """Free text without a place it ends on, that was heard as one -- a room,
    a floor, a device -- compared as spoken: "jazz in the kitchen" -> "jazz",
    "jazz upstairs please" -> "jazz", "jazz on the Mark 2" -> "jazz" (the
    name "Mark two")."""
    words = text.split()
    fill = {f.lower() for f in filler or []}
    end = len(words)
    while end > 0 and _strip(words[end - 1]).lower() in fill:
        end -= 1
    wanted = {sentence_key(phrase, language) for phrase in places} - {""}
    # The longest tail that is the place, with "in the" before it; some text stays.
    for start in range(1, end):
        tail = [_strip(w).lower() for w in words[start:end]]
        skip = 1 if tail[0] in ("in", "on", "at", "to") else 0
        if skip < len(tail) and tail[skip] == "the":
            skip += 1
        if skip < len(tail) and sentence_key(" ".join(words[start + skip : end]), language) in wanted:
            kept = words[:start]
            if all(_strip(w).lower() in fill | {"the"} for w in kept):
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
