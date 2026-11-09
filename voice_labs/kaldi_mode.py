"""Kaldi mode: commands heard by Kaldi instead of Parakeet (settings.json
``recognizer``: ``kaldi``), as Speech-to-Phrase heard them -- with the wake
verifier's own model (en_US-rhasspy, Speech-to-Phrase's English one).

**What it hears** is a fixed grammar (``sentences``): every example sentence
of the home's commands, word for word -- the app's own commands' and Home
Assistant's sentence triggers', as compiled (store.compile_store) -- with one
of the language's extras before or after it ("um", "please"), and noise
(``<unk>``) around it. Nothing else: a command with no example sentence
isn't heard, nor are its keywords. Free text is heard only from a value
list: a shared one named as the slot (``item``), whose every phrase then
takes the place of each example's free text -- what the matcher reads in it
("add <milk> to the shopping list"). ``Sentences.notes`` says which commands
aren't heard, or not all of them, and why: the Commands page shows it. A
sentence trigger with free text (``add {item} to my list``) is heard the same
way, once a list is named as its slot (triggers.py).

**Whether anything was said** is the background's: the same audio decoded as
any of the model's frequent words (its 10,000), in any order -- a call's
after the same wake phrase, so that only the command is compared. A sentence
that costs more than ``max_cost`` a frame more than the background
(``Heard.margin``) was not one of the home's: refused.

**The graphs** (``KaldiMode.build``): each word's pronunciations -- the
person's own (pronunciations.json, the Pronunciations page), else the
model's dictionary, else phonetisaurus's guess with the model's G2P model,
kept in ``guesses.json`` -- through Kaldi's prepare_lang and mkgraph, as the
wake verifier builds its graphs (wyoming_wake_kaldi.train). A word none of
them can say leaves out the sentences it is in. The background's graph is
the same for every home: built once, and kept. One resident decoder
(wyoming_wake_kaldi's verify_decoderd) holds the graphs (``TAGS``) -- the
home's sentences (Wyoming's audio, which has no wake word); the same with
the answers to a question (the language's yes, no and ways of ending it);
the same after one of the wake phrases in use, said as the wake word
verifier says them, for a call decoded whole, pre-roll and all; the
background -- and decodes all of them in one acoustic pass.

Kaldi mode still loads Parakeet: the matcher reads the sentence heard as
typed text with its vocabulary (Engine.recognize_text).
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
import tempfile
import threading
import time
import wave
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from adapt_ctc.config import Config
from adapt_ctc.text import spoken_words
from wyoming_wake_kaldi.kaldi import FRAME_SHIFT, SpeechTools
from wyoming_wake_kaldi.lexicon import ModelLexicon
from wyoming_wake_kaldi.model import PreparedModel, prepare_model

_LOGGER = logging.getLogger(__name__)

MODEL_ID = "en_US-rhasspy"
"""The model: the wake verifier's (models/wake-kaldi-models)."""

LANGUAGES = ("en",)
"""What it has a model for."""

UNK, SIL = "<unk>", "<sil>"

EXTRA = "__"
"""Before an extra's word in the grammar (its twin in the lexicon): left out
of what was said. Kaldi aligns words only to words written out."""

WAKE = "__wake_"
"""Before a wake phrase's word in the grammar of a call: its twin, said as
the wake word verifier says it."""

SAID_AFTER = 0.15
"""How far past Kaldi's split a background word must run to be said after
the wake word, not its tail."""

UNK_COST = 4.0
"""Noise before or after the sentence, each stretch of it."""

EXTRA_COST = 3.0
"""One of the language's extras before or after the sentence ("um",
"please")."""

BACKGROUND_COST = 6.0
"""Each word of the background: dear enough that silence isn't words."""

MAX_COST = 0.0
"""How much more than the background a frame of the sentence may cost
(``settings.json`` ``kaldi_max_cost``). On the office-multicap corpus's
calls the wake chain accepted (scripts/kaldi_mode.py --wake), the corpus's
own sentences the examples, a call decoded whole after "okay nabu": at 0,
86.0% of 1,230 commands right, 4.6% wrong, 86.9% of 221 sentences that are
none refused, and 5 of 400 calls of talk overheard ran a command; at -0.02,
82.3%, 3.9%, 90.5% and 0; at 0.02, 88.5%, 5.8%, 79.2% and 29."""

LEAD = 0.3
"""Seconds before Kaldi's split that a call was decoded from, before calls
were decoded whole after their wake phrase: scripts/kaldi_mode.py's
``split``, to compare."""

SEARCH = ("--beam=13.0", "--max-active=7000", "--lattice-beam=2.0")

BUILD_VERSION = 5
"""Bumped when the graphs change for the same sentences."""

EXTRAS = {
    "en": (("um", "uh", "so", "hey", "okay"), ("please", "thanks", "thank you", "for me")),
}
"""Said around a command that no example says, by language: (before, after)."""

TAGS = ("home", "answer", "call", "background", "call_background")
"""The decoder's graphs: the home's sentences, those with the answers to a
question, those after a wake phrase (a call, decoded whole), the
background, and the background after a wake phrase (a call's)."""

_START_TIMEOUT = 120.0
_DECODE_TIMEOUT = 30.0


# ------------------------------------------------------------- sentences


@dataclass
class Sentences:
    """What Kaldi hears: each intent's sentences, as words."""

    by_intent: dict[str, list[tuple[str, ...]]] = field(default_factory=dict)
    notes: dict[str, dict[str, Any]] = field(default_factory=dict)
    """Intents not heard (``why``: ``examples``, none) or heard with only the
    free text their examples say (``list``: the value list it needs)."""

    @property
    def all(self) -> list[tuple[str, ...]]:
        return list(dict.fromkeys(s for sentences in self.by_intent.values() for s in sentences))


def words_of(text: str, language: str) -> list[tuple[str, ...]]:
    """``text`` as spoken words (adapt_ctc.text): its first spelling only --
    the others are the CTC matcher's ("dont" for "don't"), not words the
    dictionary has."""
    return [tuple(w) for w in spoken_words(text, language)[:1] if w]


def sentences(config: Config, free_text: Callable[[str, str], str | None]) -> Sentences:
    """Every intent's example sentences, as words; its free text, when it
    needs one, each phrase of the value list named as its slot.
    ``free_text(intent, sentence)``: the free text the matcher reads in an
    example of the intent."""
    language = config.language
    out = Sentences()
    for intent in config.intents.values():
        if not intent.examples:
            if intent.keywords or intent.wildcard is not None:
                out.notes[intent.name] = {"why": "examples"}
            continue
        said = [s for example in intent.examples for s in words_of(example, language)]
        wildcard = intent.wildcard
        if wildcard is not None and wildcard.required:
            values = config.lists.get(wildcard.slot)
            if not values:
                out.notes[intent.name] = {"why": "list", "list": wildcard.slot}
            else:
                phrases = [w for _, texts in values for text in texts for w in words_of(text, language)]
                said = [s for example in intent.examples for s in _with_values(example, intent.name, phrases, language, free_text)]
        out.by_intent[intent.name] = list(dict.fromkeys(said))
    return out


def _with_values(
    example: str, intent: str, phrases: list[tuple[str, ...]], language: str,
    free_text: Callable[[str, str], str | None],
) -> list[tuple[str, ...]]:
    """``example`` with each of ``phrases`` in place of its free text; as
    written when the matcher reads none in it."""
    written = words_of(example, language)
    text = free_text(intent, example)
    found = words_of(text, language)[0] if text else ()
    out: list[tuple[str, ...]] = []
    for words in written:
        at = _find(words, found) if found else -1
        if at < 0:
            out.append(words)
            continue
        out.extend(words[:at] + phrase + words[at + len(found):] for phrase in phrases)
    return out


def _find(words: tuple[str, ...], part: tuple[str, ...]) -> int:
    for at in range(len(words) - len(part) + 1):
        if words[at: at + len(part)] == part:
            return at
    return -1


# ------------------------------------------------------- pronunciations


@dataclass
class Word:
    """A word of the grammar, as the Pronunciations page shows it."""

    word: str
    pronunciations: list[str]
    source: str
    """``yours``, ``dictionary``, ``guessed``, or ``none``."""


class Pronouncer:
    """Each word's pronunciations: the person's own, the model's
    dictionary, phonetisaurus's guess (kept in ``guesses_path``)."""

    def __init__(self, model_dir: Path, tools: SpeechTools, guesses_path: Path) -> None:
        self.model_dir = Path(model_dir)
        self.tools = tools
        self.guesses_path = Path(guesses_path)
        self.g2p = self.model_dir / "g2p.fst"
        self.phonetisaurus = tools.tools_dir / "phonetisaurus"
        self._guesses: dict[str, list[str]] | None = None

    @property
    def guessing(self) -> bool:
        """Whether phonetisaurus and its model are here."""
        return self.g2p.is_file() and self.phonetisaurus.is_file()

    def lexicon(self) -> ModelLexicon:
        return ModelLexicon(self.model_dir)

    def guesses(self) -> dict[str, list[str]]:
        if self._guesses is None:
            try:
                self._guesses = json.loads(self.guesses_path.read_text("utf-8"))
            except (OSError, ValueError):
                self._guesses = {}
        return self._guesses

    def dictionary(self, words: Iterable[str]) -> tuple[dict[str, list[str]], set[str]]:
        """The model's dictionary's pronunciations of ``words`` (those it can
        say), and its phones. In a few queries: the dictionary has no index,
        and one query a word took 4 ms."""
        words = list(dict.fromkeys(words))
        lexicon = self.lexicon()
        try:
            phones = set(lexicon.phones)
            found: dict[str, list[str]] = {}
            for at in range(0, len(words), 500):
                chunk = words[at: at + 500]
                rows = lexicon._conn.execute(  # pylint: disable=protected-access
                    f"SELECT word, phonemes FROM word_phonemes WHERE word IN ({','.join('?' * len(chunk))}) ORDER BY pron_order",
                    chunk,
                )
                for word, pron in rows:
                    if _speakable(pron, phones):
                        found.setdefault(word, []).append(pron)
        finally:
            lexicon.close()
        return found, phones

    def words(self, words: Iterable[str], own: dict[str, list[str]]) -> dict[str, Word]:
        """Each word's pronunciations and where they came from; guessed
        first where needed (slow the first time: phonetisaurus)."""
        words = list(dict.fromkeys(words))
        dictionary, phones = self.dictionary(w for w in words if not own.get(w))
        out: dict[str, Word] = {}
        unknown = []
        for word in words:
            if own.get(word):
                out[word] = Word(word, list(own[word]), "yours")
            elif dictionary.get(word):
                out[word] = Word(word, dictionary[word], "dictionary")
            else:
                unknown.append(word)
        guesses = self._guess(unknown, phones)
        for word in unknown:
            prons = guesses.get(word) or []
            out[word] = Word(word, prons, "guessed" if prons else "none")
        return {w: out[w] for w in words}

    def _guess(self, words: list[str], phones: set[str]) -> dict[str, list[str]]:
        guesses = self.guesses()
        missing = [w for w in words if w not in guesses]
        if missing and self.guessing:
            started = time.perf_counter()
            with tempfile.NamedTemporaryFile("w", suffix=".txt", encoding="utf-8") as listed:
                listed.write("\n".join(missing) + "\n")
                listed.flush()
                try:
                    output = subprocess.run(
                        [str(self.phonetisaurus), f"--model={self.g2p}", f"--wordlist={listed.name}"],
                        capture_output=True, text=True, env=self.tools.extended_env, timeout=120, check=True,
                    ).stdout
                except (OSError, subprocess.SubprocessError) as err:
                    _LOGGER.warning("phonetisaurus failed: %s", err)
                    output = ""
            for line in output.splitlines():
                parts = line.split("\t")
                if len(parts) == 3 and _speakable(parts[2], phones):
                    guesses.setdefault(parts[0], []).append(parts[2].strip())
            for word in missing:
                guesses.setdefault(word, [])
            try:
                self.guesses_path.parent.mkdir(parents=True, exist_ok=True)
                self.guesses_path.write_text(json.dumps(guesses, ensure_ascii=False, indent=1), "utf-8")
            except OSError as err:
                _LOGGER.warning("Could not keep the guessed pronunciations: %s", err)
            _LOGGER.info("Guessed %d pronunciation(s) in %.1f s", len(missing), time.perf_counter() - started)
        return {w: guesses.get(w) or [] for w in words}


def _speakable(pron: str, phones: set[str]) -> bool:
    said = pron.split()
    return bool(said) and all(p in phones for p in said)


def validate(pron: str, phones: set[str]) -> str:
    """A pronunciation as the page sends it, checked: its phones, spaced.
    Raises ValueError naming the phones the model doesn't have."""
    said = pron.split()
    if not said:
        raise ValueError("an empty pronunciation")
    unknown = [p for p in said if p not in phones]
    if unknown:
        raise ValueError(f"not the model's phones: {' '.join(unknown)}")
    return " ".join(said)


# ------------------------------------------------------------- the graphs


@dataclass
class Built:
    """One build of the graphs."""

    directory: Path
    signature: str
    sentences: int
    words: dict[str, Word]
    unspeakable: list[str]
    """Words none of the sources can say: their sentences are left out."""
    seconds: float


def _grammar(
    sentences: list[tuple[str, ...]], extras: tuple[list[tuple[str, ...]], list[tuple[str, ...]]],
    wake: list[tuple[str, ...]] | None = None,
) -> list[str]:
    """``sentences`` as an OpenFST text grammar: noise, one of the ``wake``
    phrases when given, an extra, a sentence (their shared-prefix tree), an
    extra, noise. Epsilons are ``#0`` in and nothing out, as Kaldi's
    grammars have them. An extra is its word's twin (``EXTRA``), and a wake
    phrase's word its own (``WAKE``): heard, and left out of what was said,
    as noise is."""
    lines: list[str] = []
    states = 6

    def arc(src: int, dst: int, word: str | None = None, cost: float = 0.0, prefix: str = "") -> None:
        label = "#0 <eps>" if word is None else f"{prefix}{word} {prefix}{word}"
        lines.append(f"{src} {dst} {label} {cost}")

    def tree(src: int, dst: int, phrases: Iterable[tuple[str, ...]], cost: float = 0.0, prefix: str = "") -> None:
        nonlocal states
        children: dict[tuple[int, str], int] = {}
        ends: set[int] = set()
        for words in phrases:
            at = src
            for word in words:
                key = (at, word)
                if key not in children:
                    children[key] = states
                    arc(at, states, word, cost if at == src else 0.0, prefix)
                    states += 1
                at = children[key]
            ends.add(at)
        for end in sorted(ends):
            arc(end, dst)

    # 0 noise (then the wake word), 1 an extra, 2 the sentence; 3 its end, 4
    # an extra, 5 noise.
    arc(0, 0, UNK, UNK_COST)
    if wake:
        tree(0, 1, wake, prefix=WAKE)
    else:
        arc(0, 1)
    arc(1, 2)
    tree(1, 2, extras[0], EXTRA_COST, prefix=EXTRA)
    tree(2, 3, sentences)
    arc(3, 4)
    tree(3, 4, extras[1], EXTRA_COST, prefix=EXTRA)
    arc(4, 5)
    arc(5, 5, UNK, UNK_COST)
    lines.append("5")
    return lines


class KaldiMode:
    """The graphs and the resident decoder (``build``, ``decode``)."""

    def __init__(self, root: Path, work_dir: Path) -> None:
        models = Path(root).absolute() / "models"
        self.model_dir = models / "wake-kaldi-models" / MODEL_ID
        self.tools = SpeechTools.from_dir(models / "wake-kaldi-tools")
        self.daemon = models / "wake-kaldi-decoderd" / "verify_decoderd"
        self.work_dir = Path(work_dir).absolute()  # Kaldi's scripts run from inside it
        self.pronouncer = Pronouncer(self.model_dir, self.tools, self.work_dir / "guesses.json")
        self.max_cost = MAX_COST
        self.built: Built | None = None
        self.error: str | None = None
        self._model: PreparedModel | None = None
        self._decoder: _Decoder | None = None
        self._build_lock = threading.Lock()

    @property
    def available(self) -> str | None:
        """None when it can run here; else what is missing."""
        if not (self.model_dir / "lexicon.db").is_file():
            return f"no Kaldi model ({self.model_dir})"
        if not (self.tools.kaldi_dir / "utils" / "mkgraph.sh").is_file():
            return f"no Kaldi tools ({self.tools.tools_dir})"
        if not self.daemon.is_file():
            return f"no Kaldi decoder ({self.daemon})"
        return None

    @property
    def ready(self) -> bool:
        return self._decoder is not None and self._decoder.running

    def model(self) -> PreparedModel:
        if self._model is None:
            self._model = prepare_model(MODEL_ID, self.model_dir, self.work_dir)
        return self._model

    def phones(self) -> set[str]:
        lexicon = self.pronouncer.lexicon()
        try:
            return set(lexicon.phones)
        finally:
            lexicon.close()

    # -------------------------------------------------------------- build

    def build(
        self, heard: Sentences, language: str, own: dict[str, list[str]], answers: Iterable[str] = (),
        wake: Iterable[tuple[str, ...]] = (), wake_lexicon: dict[str, Iterable[str]] | None = None,
    ) -> Built:
        """The graphs for ``heard`` (with ``answers``, the language's ways of
        answering a question; after one of the ``wake`` phrases, each said
        as ``wake_lexicon`` has it -- the wake word verifier's -- for a call),
        then a decoder holding them, in place of the one before. Slow (seconds): call from a thread. Raises RuntimeError
        when Kaldi can't build them; the old decoder stays."""
        missing = self.available
        if missing:
            raise RuntimeError(missing)
        with self._build_lock:
            started = time.perf_counter()
            before = [w for p in EXTRAS.get(language, ((), ()))[0] for w in words_of(p, language)]
            after = [w for p in EXTRAS.get(language, ((), ()))[1] for w in words_of(p, language)]
            said = heard.all
            answered = [w for text in answers for w in words_of(text, language)]
            vocabulary = sorted({w for s in [*said, *answered, *before, *after] for w in s})
            words = self.pronouncer.words(vocabulary, own)
            speakable = {w for w, entry in words.items() if entry.pronunciations}
            unspeakable = sorted(w for w in vocabulary if w not in speakable)

            def sayable(phrases: list[tuple[str, ...]]) -> list[tuple[str, ...]]:
                return [p for p in phrases if all(w in speakable for w in p)]

            keep, answer_keep, extras = sayable(said), sayable(answered), (sayable(before), sayable(after))
            wakes = self._wake_words(wake, wake_lexicon or {}, own)
            wake_keep = [p for p in dict.fromkeys(tuple(w.lower() for w in p) for p in wake) if all(f"{WAKE}{w}" in wakes for w in p)]
            signature = _signature({
                "sentences": keep, "answers": answer_keep, "extras": extras, "wake": wake_keep,
                "words": {w: words[w].pronunciations for w in sorted(speakable)},
                "wake_words": {w: e.pronunciations for w, e in sorted(wakes.items())}, "costs": [UNK_COST, EXTRA_COST],
            })
            background = self._background()
            call_background = self._background(wake_keep, wakes) if wake_keep else background
            if self.built is not None and self.built.signature == signature and self.ready:
                return self.built
            directory = self.work_dir / "graphs" / signature
            if not (directory / "done").is_file():
                shutil.rmtree(directory, ignore_errors=True)
                twins = {f"{EXTRA}{w}": Word(f"{EXTRA}{w}", words[w].pronunciations, words[w].source)
                         for phrase in [*extras[0], *extras[1]] for w in phrase}
                self._build_graphs(directory, {
                    "home": _grammar(keep, extras),
                    "answer": _grammar(list(dict.fromkeys([*keep, *answer_keep])), extras),
                    "call": _grammar(keep, extras, wake_keep or None),
                }, {**{w: words[w] for w in speakable}, **twins, **{w: wakes[w] for w in {f"{WAKE}{x}" for p in wake_keep for x in p}}})
                (directory / "done").write_text("", "utf-8")
            decoder = _Decoder(self.daemon, self.model(), {
                **{tag: directory / f"graph_{tag}" for tag in TAGS if "background" not in tag}, "background": background,
                "call_background": call_background,
            }, self.tools)
            decoder.start()
            old, self._decoder = self._decoder, decoder
            if old is not None:
                old.close()
            self.built = Built(directory, signature, len(keep), words, unspeakable, time.perf_counter() - started)
            self.error = None
            self._prune(directory)
            _LOGGER.info(
                "Kaldi hears %d sentence(s) of %d word(s)%s; built in %.1f s",
                len(keep), len(vocabulary), f"; can't say {unspeakable}" if unspeakable else "", self.built.seconds,
            )
            return self.built

    def _wake_words(
        self, wake: Iterable[tuple[str, ...]], lexicon: dict[str, Iterable[str]], own: dict[str, list[str]],
    ) -> dict[str, Word]:
        """The wake phrases' words, each its twin (``WAKE``) said as the wake
        word verifier says it -- else as any other word is."""
        wanted = {w.lower() for phrase in wake for w in phrase}
        given = {w.lower(): [str(p) for p in prons] for w, prons in lexicon.items()}
        others = self.pronouncer.words([w for w in wanted if not given.get(w)], own)
        out = {}
        for word in sorted(wanted):
            prons = given.get(word) or (others[word].pronunciations if word in others else [])
            if prons:
                out[f"{WAKE}{word}"] = Word(f"{WAKE}{word}", prons, "wake")
        return out

    def _background(self, wake: list[tuple[str, ...]] | None = None, wakes: dict[str, Word] | None = None) -> Path:
        """The background's graph: the model's frequent words, any of them in
        any order -- after noise and one of the ``wake`` phrases (``wakes``:
        their words' twins), when given, as a call's is. Built once for each
        (the plain one is the same for every home)."""
        files = [self.model_dir / "frequent_words.txt", self.model_dir / "lexicon.db", self.model_dir / "model/model/final.mdl"]
        stats = [(f.name, f.stat()) for f in files if f.exists()]
        twins = {w: e for w, e in (wakes or {}).items() if any(w == f"{WAKE}{x}" for p in wake or [] for x in p)}
        signature = _signature({"files": [[name, st.st_size, int(st.st_mtime)] for name, st in stats], "cost": BACKGROUND_COST,
                                **({"wake": wake, "wake_words": {w: e.pronunciations for w, e in sorted(twins.items())}} if wake else {})})
        directory = self.work_dir / "background" / signature
        if not (directory / "done").is_file():
            shutil.rmtree(directory, ignore_errors=True)
            started = time.perf_counter()
            dictionary, _ = self.pronouncer.dictionary(_frequent_words(self.model_dir))
            words = {w: Word(w, prons, "dictionary") for w, prons in dictionary.items()}
            loop = [f"1 1 {w} {w} {BACKGROUND_COST}" for w in sorted(words)] + ["1"]
            if wake:
                lines = _grammar([], ([], []), wake)
                # Noise, then a wake phrase into state 1: the words' loop.
                head = [line for line in lines if not line.startswith(("1 ", "2 ", "3 ", "4 ", "5 ")) and line != "5"]
            else:
                head = ["0 1 #0 <eps> 0.0"]
            self._build_graphs(directory, {"background": head + loop}, {**words, **twins})
            (directory / "done").write_text("", "utf-8")
            _LOGGER.info("Kaldi's background (%d words%s) built in %.1f s", len(words), ", after the wake word" if wake else "",
                         time.perf_counter() - started)
        return directory / "graph_background"

    def _build_graphs(self, directory: Path, grammars: dict[str, list[str]], words: dict[str, Word]) -> None:
        """A lexicon of ``words`` (those with pronunciations), and each grammar
        made a graph with it: ``graph_<tag>``."""
        tools, model = self.tools, self.model()
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "path.sh").write_text("", "utf-8")
        utils = directory / "utils"
        utils.unlink(missing_ok=True)
        utils.symlink_to(tools.egs_utils_dir, target_is_directory=True)
        dict_dir = directory / "data" / "local" / "dict"
        dict_dir.mkdir(parents=True, exist_ok=True)
        for phone_file in model.phones_dir.glob("*.txt"):
            shutil.copy(phone_file, dict_dir / phone_file.name)
        with open(dict_dir / "lexicon.txt", "w", encoding="utf-8") as lexicon:
            for word, entry in sorted(words.items()):
                for pron in dict.fromkeys(entry.pronunciations):
                    print(word, pron, file=lexicon)
            print(UNK, "SPN", file=lexicon)
            print(SIL, "SIL", file=lexicon)
        lang = directory / "data" / "lang"
        tools.run(
            "bash",
            [str(tools.egs_utils_dir / "prepare_lang.sh"), str(dict_dir), UNK, str(directory / "data/local/lang"), str(lang)],
            cwd=directory,
        )
        for tag, lines in grammars.items():
            lang_tag = directory / "data" / f"lang_{tag}"
            shutil.copytree(lang, lang_tag, symlinks=True)
            (lang_tag / "G.txt").write_text("\n".join(lines) + "\n", "utf-8")
            words_txt = lang_tag / "words.txt"
            tools.run_pipeline(
                ["fstcompile", f"--isymbols={words_txt}", f"--osymbols={words_txt}", str(lang_tag / "G.txt")],
                ["fstarcsort", "--sort_type=ilabel", "-", str(lang_tag / "G.fst")],
                cwd=directory,
            )
            tools.run(
                "bash",
                [str(tools.egs_utils_dir / "mkgraph.sh"), "--self-loop-scale", "1.0", str(lang_tag), str(model.am_dir),
                 str(directory / f"graph_{tag}")],
                cwd=directory,
            )

    def _prune(self, keep: Path) -> None:
        """Older builds' graphs, but the last few."""
        builds = sorted((p for p in (self.work_dir / "graphs").iterdir() if p.is_dir() and p != keep),
                        key=lambda p: p.stat().st_mtime)
        for old in builds[:-2]:
            shutil.rmtree(old, ignore_errors=True)

    # ------------------------------------------------------------- decode

    def decode(self, audio: np.ndarray) -> Heard:
        """``audio`` (16 kHz, float) against the graphs (TAGS). Raises
        RuntimeError when no decoder is running."""
        decoder = self._decoder
        if decoder is None or not decoder.running:
            raise RuntimeError(self.error or "Kaldi isn't ready")
        started = time.perf_counter()
        return Heard(decoder.decode(audio), time.perf_counter() - started, self.max_cost)

    def close(self) -> None:
        decoder, self._decoder = self._decoder, None
        if decoder is not None:
            decoder.close()

    def status(self) -> dict[str, Any]:
        built = self.built
        return {
            "available": self.available is None,
            "missing": self.available,
            "ready": self.ready,
            "error": self.error,
            "guessing": self.pronouncer.guessing,
            "sentences": built.sentences if built else None,
            "unspeakable": built.unspeakable if built else [],
            "guessed": sorted(w for w, e in built.words.items() if e.source == "guessed") if built else [],
            "seconds": round(built.seconds, 2) if built else None,
        }


def _signature(what: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps({"version": BUILD_VERSION, **what}, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]


def _is_word(word: str) -> bool:
    return not word.startswith(("<", "#", EXTRA))


def _frequent_words(model_dir: Path) -> list[str]:
    """The model's frequent words: the background's."""
    try:
        lines = (Path(model_dir) / "frequent_words.txt").read_text("utf-8").splitlines()
    except OSError:
        return []
    return [w.strip() for w in lines if w.strip() and w.strip().replace("'", "").isalpha() and w.strip().islower()]


@dataclass
class Heard:
    """What Kaldi made of some audio, against each of its graphs (TAGS)."""

    results: dict[str, tuple[float, int, list[tuple[str, float, float]]]]
    """Each graph's: its best path's cost, the frames, its words with their
    times (seconds)."""
    seconds: float
    max_cost: float = MAX_COST

    def words(self, graph: str = "home") -> list[str]:
        return [w for w, _, _ in self.results[graph][2] if _is_word(w)]

    def text(self, graph: str = "home") -> str:
        return " ".join(self.words(graph))

    def margin(self, graph: str = "home") -> float:
        """How much more than the background a frame of ``graph``'s sentence
        cost (a call's: the background after the same wake phrase)."""
        cost, frames, _ = self.results[graph]
        return (cost - self.results["call_background" if graph == "call" else "background"][0]) / max(1, frames)

    def taken(self, graph: str = "home") -> bool:
        return bool(self.text(graph)) and self.margin(graph) <= self.max_cost

    @property
    def background_words(self) -> list[str]:
        """What the background heard: whether anything was said."""
        return self.words("background")

    def background_after(self, seconds: float, graph: str = "home") -> list[str]:
        """What ``graph``'s background heard from ``seconds`` on: whether
        anything was said after the wake word, in a call decoded whole."""
        background = self.results["call_background" if graph == "call" else "background"][2]
        return [w for w, start, end in background if _is_word(w) and end > seconds + SAID_AFTER]

    def to_json(self, graph: str = "home") -> dict[str, Any]:
        """For the call's record (Decision.sentence)."""
        return {
            "recognizer": "kaldi",
            "graph": graph,
            "text": self.text(graph) or None,
            "cost": round(self.margin(graph), 3),
            "max_cost": self.max_cost,
            "taken": self.taken(graph),
            "background": " ".join(self.background_words),
            "seconds": round(self.seconds, 3),
        }


class _Decoder:
    """The resident decoder (verify_decoderd), its graphs loaded once: a
    wav file in, a line for each graph out."""

    def __init__(self, daemon: Path, model: PreparedModel, graphs: dict[str, Path], tools: SpeechTools) -> None:
        self.args = [
            str(daemon), f"--config={model.online_conf}", "--acoustic-scale=1.0", *SEARCH,
            "--frame-subsampling-factor=3", str(model.final_mdl),
        ]
        self.words: dict[str, dict[str, str]] = {}
        for tag in TAGS:
            graph = graphs[tag]
            self.args += [tag, str(graph / "HCLG.fst"), str(graph / "phones" / "word_boundary.int")]
            self.words[tag] = {
                number: word
                for word, number in (line.split() for line in (graph / "words.txt").read_text("utf-8").splitlines() if line.strip())
            }
        self.tools = tools
        self.proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._scratch = Path(tempfile.mkdtemp(prefix="voice-labs-kaldi-"))

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self) -> None:
        env = {**self.tools.extended_env, "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"}
        self.proc = subprocess.Popen(
            self.args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env,
        )
        ready = _read_line(self.proc, _START_TIMEOUT)
        if ready.strip() != "READY":
            self.close()
            raise RuntimeError("the Kaldi decoder didn't start")

    def decode(self, audio: np.ndarray) -> dict[str, tuple[float, int, list[tuple[str, float, float]]]]:
        with self._lock:
            if self.proc is None or self.proc.stdin is None:
                raise RuntimeError("the Kaldi decoder isn't running")
            path = self._scratch / "call.wav"
            with wave.open(str(path), "wb") as out:
                out.setnchannels(1)
                out.setsampwidth(2)
                out.setframerate(16000)
                out.writeframes((np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2").tobytes())
            self.proc.stdin.write(f"{path}\n")
            self.proc.stdin.flush()
            results = {}
            for _ in TAGS:
                parts = _read_line(self.proc, _DECODE_TIMEOUT).split()
                if not parts:
                    raise RuntimeError("the Kaldi decoder stopped")
                if parts[0] != "OK":
                    # Too short to decode ("no frames decoded"): nothing heard.
                    results[parts[1] if len(parts) > 1 else "?"] = (0.0, 0, [])
                    continue
                tag = parts[1]
                words = []
                for token in parts[4:]:
                    word_id, begin, end = token.split(":")
                    words.append((self.words[tag].get(word_id, word_id), int(begin) * FRAME_SHIFT, int(end) * FRAME_SHIFT))
                results[tag] = (float(parts[2]), int(parts[3]), words)
            for tag in TAGS:
                results.setdefault(tag, (0.0, 0, []))
            return results

    def close(self) -> None:
        proc, self.proc = self.proc, None
        if proc is not None and proc.poll() is None:
            try:
                assert proc.stdin is not None
                proc.stdin.close()
                proc.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired, AssertionError):
                proc.kill()
        shutil.rmtree(self._scratch, ignore_errors=True)


def _read_line(proc: subprocess.Popen, timeout: float) -> str:
    """A line of the decoder's output, or "" when it ends or takes longer
    than ``timeout``."""
    assert proc.stdout is not None
    box: list[str] = []
    reader = threading.Thread(target=lambda: box.append(proc.stdout.readline()), daemon=True)  # type: ignore[union-attr]
    reader.start()
    reader.join(timeout)
    if not box:
        proc.kill()
        return ""
    return box[0]
