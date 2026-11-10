"""Second-stage wake word verification via wyoming-wake-kaldi.

The on-device detector has already fired; this re-decodes the pre-roll window
against explicit wake/background/prefix/confusable grammars. It answers both
"was that really the wake word" and "where does the command start".

Every wake word in ``wake.yaml`` can be verified, but each costs resident
decoder processes, so a word's graphs and decoders are started the first time
a satellite listens for it (``ensure``), not all at startup. A wake word that
is not in ``wake.yaml`` -- a custom one -- has no verifier: ``verify`` returns
None and the detection stands.

What the Wake words page changes (``wake_words.json`` in the data directory)
goes over ``wake.yaml``: a wake word's confusables, pronunciations and
thresholds, and custom wake words given confusables, which are then verified
like the built-in ones (``build``). A change rebuilds the graphs and restarts
the decoders of the wake words it touches (``apply``); the others run on.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import tempfile
import wave
from pathlib import Path
from typing import Any

import yaml
from wyoming_wake_kaldi.config import ConfigError, ServiceConfig, load_config, tokenize_phrase
from wyoming_wake_kaldi.lexicon import ModelLexicon
from wyoming_wake_kaldi.service import WakeService
from wyoming_wake_kaldi.model import prepare_model
from wyoming_wake_kaldi.pool import maybe_start_decoder
from wyoming_wake_kaldi.train import train_wake_word
from wyoming_wake_kaldi.verify import Verifier, VerifyResult

from .protocol import BYTES_PER_SAMPLE, SAMPLE_RATE

_LOGGER = logging.getLogger(__name__)

DEFAULT_WAKE_WORD = "okay_nabu"

THRESHOLDS = ("theta_prefix", "theta_last", "theta_competitor")


class WakeVerifier:
    def __init__(
        self, config_path: Path, wake_word: str = DEFAULT_WAKE_WORD, train_dir: Path | None = None
    ) -> None:
        self.config_path = Path(config_path)
        self.wake_word = wake_word
        """Verified when ``verify`` is not told which word fired."""
        self.train_dir = Path(train_dir) if train_dir is not None else None
        """Where graphs are built (default: ``wake.yaml``'s ``train_dir``)."""
        self.shipped = yaml.safe_load(self.config_path.read_text("utf-8"))
        """``wake.yaml`` as shipped, before anything changed on the page."""
        self.config = self.build({}, {})
        self._service: WakeService | None = None
        self._lock = asyncio.Lock()

    # -------------------------------------------------------------- config

    @property
    def model_id(self) -> str:
        """The Kaldi model custom wake words are verified with."""
        return next(iter(self.shipped["models"]))

    def shipped_entry(self, wake_word: str) -> dict[str, Any] | None:
        """A built-in wake word's verifier entry, as shipped."""
        entry = (self.shipped.get("wake_words") or {}).get(wake_word)
        return copy.deepcopy(entry) if entry is not None else None

    def default_thresholds(self, wake_word: str | None = None) -> dict[str, float]:
        """The thresholds a wake word has before the page changes them."""
        defaults = load_config_defaults(self.shipped)
        entry = self.shipped_entry(wake_word) if wake_word else None
        defaults.update(((entry or {}).get("thresholds")) or {})
        return {k: float(v) for k, v in defaults.items()}

    def raw(self, tuning: dict[str, dict[str, Any]], custom: dict[str, str]) -> dict[str, Any]:
        """``wake.yaml`` with the page's changes, paths absolute.

        ``tuning``: per wake word, what the page changed (``wake_words.json``).
        ``custom``: custom wake words, by id, with their phrase ("hey tater"):
        those with confusables get a verifier entry of their own.
        """
        base = self.config_path.parent
        raw = copy.deepcopy(self.shipped)
        for key in ("tools_dir", "train_dir"):
            raw[key] = str(_absolute(raw.get(key, key), base))
        if self.train_dir is not None:
            raw["train_dir"] = str(self.train_dir)
        for model in raw.get("models", {}).values():
            if model and model.get("directory"):
                model["directory"] = str(_absolute(model["directory"], base))
        words = raw.setdefault("wake_words", {})
        for word_id, phrase in custom.items():
            if word_id in words or not (tuning.get(word_id) or {}).get("confusables"):
                continue
            # Its id is what the device reports; its name could clash with a
            # built-in one's ("Hey Jarvis" by someone else).
            words[word_id] = {"model": self.model_id, "names": [word_id], "phrases": [phrase]}
        for word_id, entry in words.items():
            changes = tuning.get(word_id) or {}
            if "confusables" in changes:
                entry["confusables"] = list(changes["confusables"])
            if changes.get("pronunciations"):
                entry["pronunciations"] = {**(entry.get("pronunciations") or {}), **changes["pronunciations"]}
            if changes.get("thresholds"):
                entry["thresholds"] = {**(entry.get("thresholds") or {}), **changes["thresholds"]}
            if changes and entry.get("pronunciations"):
                # Only for the words still said: "nabil", once its confusable goes.
                said = {w for phrase in [*entry.get("phrases", []), *entry.get("confusables", [])] for w, _ in tokenize_phrase(str(phrase))}
                entry["pronunciations"] = {w: p for w, p in entry["pronunciations"].items() if str(w).lower() in said}
        return raw

    def build(self, tuning: dict[str, dict[str, Any]], custom: dict[str, str]) -> ServiceConfig:
        """The verifier's config with the page's changes. Raises ConfigError
        naming what cannot be verified (a word with no pronunciation)."""
        raw = self.raw(tuning, custom)
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", encoding="utf-8") as temp:
            yaml.safe_dump(raw, temp, allow_unicode=True, sort_keys=False)
            temp.flush()
            return load_config(temp.name)

    def lexicon(self) -> ModelLexicon:
        """The model's dictionary and phones (close it after)."""
        return ModelLexicon(Path(self.raw({}, {})["models"][self.model_id]["directory"]))

    def unknown_words(self, phrases: list[str], pronunciations: dict[str, Any], lexicon: ModelLexicon) -> list[str]:
        """Words of ``phrases`` neither in the dictionary nor pronounced."""
        unknown: list[str] = []
        for phrase in phrases:
            for word, inline in tokenize_phrase(phrase):
                if inline is None and word not in pronunciations and not lexicon.pronunciations(word):
                    unknown.append(word)
        return list(dict.fromkeys(unknown))

    def knows(self, wake_word: str) -> bool:
        """Whether this wake word has a verifier entry."""
        return self.config.resolve(wake_word) is not None

    @property
    def started(self) -> list[str]:
        return sorted(self._service.verifiers) if self._service else []

    async def start(self, wake_words: tuple[str, ...] | None = None) -> None:
        """Check the tools and models, and start ``wake_words`` (default: the
        default wake word)."""
        daemon = self.config_path.parent / "models/wake-kaldi-decoderd/verify_decoderd"
        service = WakeService(self.config, daemon if daemon.exists() else None)
        service.tools.check()
        for model_id, model_config in self.config.models.items():
            service.models[model_id] = prepare_model(model_id, model_config.directory, self.config.train_dir)
        self._service = service
        if not self.knows(self.wake_word):
            raise RuntimeError(f"Wake word {self.wake_word!r} not in {self.config_path}")
        await self.ensure(wake_words if wake_words is not None else (self.wake_word,))

    async def ensure(self, wake_words) -> None:
        """Build the graphs and start the decoders of any of these not running."""
        assert self._service is not None
        service = self._service
        async with self._lock:
            for name in wake_words:
                config = self.config.resolve(name)
                if config is None or config.id in service.verifiers:
                    continue
                service.verifiers[config.id] = await self._verifier(config)
                _LOGGER.info("Wake verifier ready (%s)", config.id)

    async def _verifier(self, config) -> Verifier:
        assert self._service is not None
        service = self._service
        model = service.models[config.model]
        graph_dir = await train_wake_word(config, model, self.config.train_dir, service.tools)
        pool = None
        if self.config.resident_decoders:
            pool = await maybe_start_decoder(
                service.daemon_bin, model, graph_dir, service.tools, self.config.decoder
            )
            if pool is not None:
                service._decoders.append(pool)  # pylint: disable=protected-access
        return Verifier(config, model, graph_dir, service.tools, pool, self.config.decoder)

    async def apply(self, config: ServiceConfig) -> list[str]:
        """Run with ``config`` (from ``build``) from now on. A running wake
        word whose entry changed is rebuilt -- its graphs, its decoders --
        while the old one answers; one that is gone is stopped. Returns the
        wake words rebuilt."""
        rebuilt = []
        async with self._lock:
            old, self.config = self.config, config
            service = self._service
            if service is None:
                return rebuilt
            service.config = config
            for word_id, verifier in list(service.verifiers.items()):
                entry = config.wake_words.get(word_id)
                if entry == old.wake_words.get(word_id):
                    continue
                if entry is not None:
                    service.verifiers[word_id] = await self._verifier(entry)
                    rebuilt.append(word_id)
                    _LOGGER.info("Wake verifier rebuilt (%s)", word_id)
                else:
                    del service.verifiers[word_id]
                    _LOGGER.info("Wake verifier stopped (%s)", word_id)
                if verifier.pool is not None:
                    if verifier.pool in service._decoders:  # pylint: disable=protected-access
                        service._decoders.remove(verifier.pool)  # pylint: disable=protected-access
                    await verifier.pool.stop()
        return rebuilt

    async def stop(self) -> None:
        if self._service is not None:
            await self._service.stop()
            self._service = None

    async def verify(self, pcm: bytes, wake_word: str | None = None) -> VerifyResult | None:
        """Verify a window of 16 kHz mono PCM ending at the detection. None:
        this wake word has no verifier."""
        assert self._service is not None
        config = self.config.resolve(wake_word or self.wake_word)
        if config is None:
            return None
        if config.id not in self._service.verifiers:
            await self.ensure([config.id])
        verifier = self._service.verifiers[config.id]

        # The decoder reads a file; keep it out of the project tree.
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as temp:
            with wave.open(temp.name, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(BYTES_PER_SAMPLE)
                wav.setframerate(SAMPLE_RATE)
                wav.writeframes(pcm)
            return await verifier.verify(Path(temp.name))


def load_config_defaults(raw: dict[str, Any]) -> dict[str, float]:
    """The thresholds every wake word has unless its entry says otherwise."""
    from wyoming_wake_kaldi.config import Thresholds  # pylint: disable=import-outside-toplevel

    try:
        defaults = Thresholds.from_dict(raw.get("defaults") or {}, Thresholds())
    except ConfigError:
        defaults = Thresholds()
    return {name: float(getattr(defaults, name)) for name in THRESHOLDS}


def _absolute(value: Any, base: Path) -> Path:
    path = Path(str(value)).expanduser()
    return path if path.is_absolute() else (base / path).resolve()

