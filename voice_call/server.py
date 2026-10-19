"""Connects to ESPHome voice_call devices and runs one call at a time on each."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import re
import socket
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from adapt_ctc.config import ConfigError

from . import actions, skills, timers
from .agent import KEEP_SECONDS, Agent
from .calls import CallLog, CallRecord
from .commands import VERBOSITY
from .discover import Device, Discovery
from .endpoint import Endpointer
from .fallback import (
    DEFAULT_FALLBACK_MODEL,
    GLINER_MODELS,
    GLINER_THRESHOLD,
    TOOL_MODELS,
    Fallback,
    GlinerPicker,
    ToolModel,
    chosen,
    llama_available,
    needs_model,
)
from .engine import Decision, Engine, Satellite, llm_note
from .hass import HassError, HomeAssistant
from .integration import Integration
from .music import Music
from .voices import TypedText, Voices, choose
from .mixednet import WakeModel
from .protocol import (
    BYTE_RATE,
    BYTES_PER_SAMPLE,
    HEADER,
    MAX_PAYLOAD,
    SAMPLE_RATE,
    Message,
    encode,
    encode_json,
    ms_to_bytes,
)
from .speaker import Identification, SpeakerId
from .store import Compiled, Store
from .tts import Speech
from .wake import VerifyResult, WakeVerifier
from .wake import ConfigError as WakeConfigError
from .wake_ratio import DEFAULT_THRESHOLD as DEFAULT_RATIO, WakeRatio
from .wake_words import DEFAULT_MODEL_SLOTS, RATIO_THRESHOLD, RESERVED_SLOTS, SPECIAL, STOP, VAD, WakeWords

_LOGGER = logging.getLogger(__name__)

# A connection that survives this long is treated as healthy, which resets the
# reconnect backoff.
STABLE_CONNECTION_SECONDS = 30.0

# How often a hand edit of commands.yaml is looked for.
STORE_POLL_SECONDS = 2.0

# Running timers' names are recognized from a list rebuilt when they change;
# a burst of changes makes one rebuild.
TIMER_LIST_DEBOUNCE = 0.3

# The microWakeWord model the satellite listens with while a timer rings.
STOP_MODEL = STOP

# Outside a call, how far audio may run ahead of the device playing it: enough
# to ride out network jitter, little enough that a call cutting in is heard at
# once.
PLAY_LEAD = 0.3

# Home Assistant TTS on the satellite: how long until its media player starts
# the announcement (synthesis, then fetching the URL), and plays it out.
ANNOUNCE_START_TIMEOUT = 10.0
ANNOUNCE_TIMEOUT = 120.0

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _rgb(color: tuple[int, int, int] | None) -> dict[str, int]:
    """Event fields for an LED color; empty for none, so the device keeps its own."""
    return dict(zip("rgb", color)) if color else {}


def _device_id(hello: dict) -> str:
    """A filesystem-safe identity for the device that sent this HELLO.

    ESPHome names are only unique if the YAML sets name_add_mac_suffix, so the
    tail of the MAC is appended to keep identically-named devices apart.
    """
    name = _UNSAFE.sub("-", str(hello.get("name") or "device")).strip("-")
    mac = _UNSAFE.sub("", str(hello.get("mac") or ""))
    return f"{name}-{mac[-6:]}" if mac else name


def _satellite_key(hello: dict, fallback: str) -> str:
    """What scopes and settings are keyed on: the MAC, which survives renames
    and reflashing; else whatever discovery keyed the session on."""
    mac = re.sub(r"[^0-9a-fA-F]", "", str(hello.get("mac") or "")).lower()
    return mac or fallback


def _stt_json(transcriber) -> dict | None:
    return transcriber.to_json() if hasattr(transcriber, "to_json") else None


def _pcm_to_float(pcm: bytes) -> np.ndarray:
    return np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0


# Parakeet's TDT head, which writes example sentences, free text and hints:
# the English one beside the CTC model (parakeet-tdt_ctc-110m, int8 preferred)
# or NVIDIA's multilingual parakeet-tdt-0.6b-v3 (25 European languages; it
# finds the language itself), as sherpa-onnx releases it.
TDT_MODELS = {
    "en": ("English", "parakeet-tdt_ctc-110m, 110M parameters"),
    "multilingual": ("Multilingual (25 European languages)", "parakeet-tdt-0.6b-v3, 600M parameters"),
}
DEFAULT_TDT = "en"
DEFAULT_TOP_K = 8
"""How many commands the fallback's model chooses among. GLiNER: the closest
this many in meaning among those that hear something, chosen or not (it
needs others to tell the chosen ones from). An LLM: with more chosen than
this, the closest this many of them; with no more, all of them, read once (a
reused prefix)."""


def tdt_models(root: Path) -> dict[str, Path]:
    from adapt_ctc.tdt import has_model  # pylint: disable=import-outside-toplevel

    english = root / "models/parakeet-tdt-int8"
    if not has_model(english):
        english = root / "models/parakeet-tdt"
    return {"en": english, "multilingual": root / "models/parakeet-tdt-v3-int8"}


# Shipped lower than the models' own manifests, per wake word (see
# wake_words.VERIFIED_CUTOFF, and the "Two stages" and "Wake words" sections of
# the README for the measurements behind them).
DEFAULT_WAKE_CUTOFF = 0.2

# How much audio from after the detection to add when the wake word ran into
# the end of the pre-roll, and how close to that end counts as running into it.
DEFAULT_WAKE_TAIL = 0.3
TAIL_EDGE_SECONDS = 0.03


@dataclass
class Options:
    host: str | None = None
    """Pin to one device. None serves every satellite mDNS finds."""
    port: int = 6055
    hass_url: str = "http://localhost:8123"
    hass_token: str = ""
    hass_websocket_url: str | None = None
    """None derives it from ``hass_url``; the Supervisor's proxy differs."""
    data_dir: Path | None = None
    """Commands, satellites, settings and saved calls. Default: var/."""
    http_host: str = "127.0.0.1"
    http_port: int = 8099
    http_allow: tuple[str, ...] = ()
    """Client addresses the web UI answers; empty for any. As an app, only
    the Supervisor's ingress proxy."""
    language: str = "en"
    silence_seconds: float = 1.0
    max_command_seconds: float = 15.0
    drain_timeout: float = 30.0
    save_dir: Path | None = None
    """Also write each call's audio here, per device (the bench layout)."""
    tdt: bool = True
    """Load Parakeet's TDT head: example sentences the CTC search missed,
    free text that spells properly, and what was said when nothing matched."""
    embedding_dir: Path | None = None
    """EmbeddingGemma 2 (onnx-community/embeddinggemma-2-ONNX), to name the
    closest command by meaning when nothing matched. Never accepts one."""
    threads: int = 2
    reconnect_seconds: float = 5.0
    max_reconnect_seconds: float = 60.0
    """Ceiling for the backoff after short-lived connections."""
    wake_models: tuple[str, ...] = ("okay_nabu", "vad")
    """The wake words of a satellite that has none picked, when none are picked
    for every satellite either, by id (``vad`` here is ignored: the VAD gate is
    always pushed when it is there). Nothing is compiled into the firmware: the
    models are pushed when the device connects."""
    wake_words_dir: Path | None = None
    """Custom wake words: microWakeWord manifests and models (default:
    wake_words/ beside models/)."""
    wake_tail: float = DEFAULT_WAKE_TAIL
    """Seconds of audio from after the detection to add before verifying again,
    when the first attempt put the end of the wake phrase at the end of the
    pre-roll -- which means the word had not finished inside it.

    Only the calls that need it pay for it. A fixed tail helps the speaker
    whose wake word is being truncated and costs the ones whose is not, because
    for them it only adds command audio the verifier has to explain: measured
    across four speakers, always-on was +7.7 points for one and -1.4 and -2.8
    for two others, where retrying only on a pinned split was +3.8, +0.7 and
    nobody worse. Zero disables the retry."""
    wake_ratio: float | None = None
    """Per-frame threshold for the second opinion on a rejected wake word
    (one set on the Wake words page wins).
    Negative disables it."""
    wake_probability_cutoff: float | None = None
    """Overrides the first-stage cutoff of every verified wake word (each has
    its own: okay_nabu's is DEFAULT_WAKE_CUTOFF), never the VAD gate's or a
    custom word's, which run at their manifests'.

    The manifests ship 0.85, which assumes a voice at the device. Across a room
    that costs most of the recall, and the detections it refuses are ones the
    verifier would have accepted: at 0.20 a child's commands reaching
    recognition go from 27.6% to 42.8% while verification holds at 90-93%.
    What it buys the other way is nothing measurable -- on 1.5 h per channel of
    speech and music, the extra first-stage firings were all refused by the
    verifier."""
    speaker_id: bool = True
    """Identify the speaker of each command and send it in the processing event."""
    speaker_profiles: Path | None = None
    """Default: speakers.json in the data directory."""
    speaker_threshold: float = 0.45
    speaker_channel: str = "aec"
    """Which capture to identify from. AEC benchmarked at half the EER of AGC,
    provided the firmware streams it with gain (see voice-call-vpe.yaml)."""
    speaker_learn: bool = True
    timer_ring_seconds: float = 300.0
    """How long a finished timer rings if nobody stops it."""
    downloads: bool = False
    """Download the LLMs from Hugging Face when they are turned on and not
    there (the command line and the app turn it on; tests leave it off)."""


@dataclass
class Call:
    """Audio and state for one wake-word-to-hangup session."""

    preroll_bytes: int
    wake_word: str
    started: float = field(default_factory=time.monotonic)
    audio: bytearray = field(default_factory=bytearray)
    """The AGC capture. The command is split out of this one."""
    aec: bytearray = field(default_factory=bytearray)
    """The same call tapped earlier in the XMOS pipeline: recorded, and what the
    speaker is identified from."""
    aec_preroll_bytes: int = 0
    """Where the detection falls in ``aec``; its pre-roll can differ from AGC's."""
    command: bytearray = field(default_factory=bytearray)
    verified: bool = False
    finished: bool = False
    endpointer: Endpointer | None = None
    split_bytes: int = 0
    interrupted_ring: bool = False
    """The call stopped a ringing timer: if it says nothing else, it was that."""
    wake_start: float | None = None
    """Seconds from the start of the call to the start of the wake word, as
    Kaldi placed it."""
    record: CallRecord | None = None
    live_bytes: int = 0
    """How much of the AGC capture someone listening live has been given (or
    skipped: the pre-roll, which they heard as it happened)."""


@dataclass
class WakeVerdict:
    accepted: bool
    result: Any
    offset: int = 0
    """Bytes into the call where the verified window started (the retry)."""
    window: bytes = b""
    ratio: float | None = None
    overturned: bool = False
    retried: bool = False


@dataclass
class Session:
    """One connected satellite.

    Everything here used to live on the app. Splitting it out is what lets one
    set of models serve several devices: the models are the expensive part, and
    a second satellite now costs a socket and a buffer rather than another
    450 MB of ONNX.
    """

    key: str
    host: str
    port: int
    writer: asyncio.StreamWriter | None = None
    call: Call | None = None
    drained: asyncio.Event = field(default_factory=asyncio.Event)
    device_id: str = "unknown-device"
    satellite: str = ""
    """The satellite's key (its MAC) once it has said HELLO."""
    hello: dict = field(default_factory=dict)
    connected_at: float | None = None
    warned_quiet_aec: bool = False
    ringing: asyncio.Task | None = None
    """A finished timer ringing on this satellite."""
    ringing_timers: list[dict] = field(default_factory=list)
    ring_stopped_by: str = ""
    """What cut the ring off, when it was cut off: "stop" (said), "button"."""
    timer_progress: dict | None = None
    """The last timer_progress event sent (what its LED ring shows)."""
    timer_progress_at: float = 0.0
    announcing: asyncio.Event = field(default_factory=asyncio.Event)
    announced: asyncio.Event = field(default_factory=asyncio.Event)
    wake_words: list[str] = field(default_factory=list)
    """The wake words last pushed to it, by id."""
    wake_cutoffs: dict[str, float] = field(default_factory=dict)
    """The cutoffs they were pushed at."""
    listeners: set[asyncio.Queue] = field(default_factory=set)
    """Someone listening live (the Test page): each gets the microphone as it
    comes, between calls (the device streams it while monitored) and in them."""
    live_call: bool = False
    """Listeners were told a call started, and not yet that it ended."""

    def __str__(self) -> str:
        return self.device_id


class VoiceCallApp:
    def __init__(self, options: Options) -> None:
        self.options = options
        root = Path(__file__).resolve().parent.parent
        self.root = root
        self.store = Store(options.data_dir or root / "var")
        self.hass = HomeAssistant(
            options.hass_url, options.hass_token, options.hass_websocket_url
        )
        self.wake = WakeVerifier(root / "wake.yaml", train_dir=self.store.data_dir / "wake-train")
        self.wake_words = WakeWords(
            root / "models/wake",
            options.wake_words_dir or root / "wake_words",
            self.wake.knows,
            options.wake_probability_cutoff,
            tuning=lambda: self.store.wake_tuning,
        )
        self.wake_error: str | None = None
        """Why the Wake words page's changes could not be used at start."""
        self._wake_lock = asyncio.Lock()
        """One wake word change at a time: each may rebuild a verifier."""
        # The fp32 encoder is 862 MB resident, which is most of a 2 GB Pi.
        # Prefer the int8 build when it has been produced.
        ctc_dir = root / "models/speech-to-phrase/stt_en_parakeet_tdt_ctc_110m-int8"
        if not (ctc_dir / "model.onnx").exists():
            ctc_dir = root / "models/speech-to-phrase/stt_en_parakeet_tdt_ctc_110m"
        self._ctc_dir = ctc_dir
        self._tdt_dirs = tdt_models(root)
        self.tdt_status: str | None = None
        """What became of the last change of TDT model, for the UI."""
        self._llm_status: str | None = None
        """Getting the LLM, when it is not the fallback's to say."""
        self.engine: Engine | None = None
        # A second opinion on the two Kaldi tests that are about "is this the
        # wake word or just speech". Its `confusable` test is left alone.
        self.wake_ratios: dict[str, WakeRatio] = {}
        """The second opinion, per verified wake word (built at start)."""
        self.speech = Speech(
            root / "models/piper/en_GB-jenny_dioco-medium.onnx",
            root / "sounds/acknowledge.mp3",
            root / "sounds/timer_finished.flac",
        )
        speaker_model = root / "models/speaker/redimnet_b2_vox2.onnx"
        self.speakers: SpeakerId | None = None
        if options.speaker_id and speaker_model.exists():
            self.speakers = SpeakerId(
                speaker_model,
                options.speaker_profiles or self.store.data_dir / "speakers.json",
                channel=options.speaker_channel,
                threshold=options.speaker_threshold,
                learn=options.speaker_learn,
            )
        elif options.speaker_id:
            _LOGGER.warning("Speaker model %s not found; speaker ID disabled", speaker_model)
        self.calls = CallLog(self.store.calls_dir, self.store.settings)
        self.integration = Integration(self.hass, self._timer_finished, self._timers_changed)
        self.music = Music(self.hass)
        self.agent = Agent(self.hass)
        """What nothing here understood goes to, when one is chosen."""
        self.voices = Voices(self.hass)
        self.hass.on_catalog_change(self.voices.forget)
        self._timer_rebuild: asyncio.Task | None = None
        self.store_error: str | None = None
        self._tasks: set[asyncio.Task] = set()
        self._sessions: dict[str, asyncio.Task] = {}
        self.sessions: dict[str, Session] = {}
        """Connected satellites, by satellite key."""
        self._discovery: Discovery | None = None
        self._web = None
        self._store_watcher: asyncio.Task | None = None
        # Recognition, Piper and the wake verifier's retry share the CPU; two
        # satellites waking at once is rare, so one lock over them is enough.
        self._model_lock = asyncio.Lock()
        self._build_lock = asyncio.Lock()
        self._fallback_lock = asyncio.Lock()
        """One fallback change at a time: a second waits, and reuses the LLM."""

    # ------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        self.store.load_extras()
        self.calls.settings = self.store.settings
        for key, entry in self.store.satellites.items():
            self.integration.want(key, entry.get("name") or key)
        await self.hass.start()

        try:
            self.wake.config = await asyncio.to_thread(self._verifier_config)
        except WakeConfigError as err:
            self.wake_error = f"What the Wake words page changed can't be verified, so the built-in settings are used: {err}"
            _LOGGER.error("%s", self.wake_error)
        self.wake_words.forget()
        await self.wake.start(tuple(w for w in self._wake_words_in_use() if self.wake.knows(w)))
        if self.wake_words.model(STOP) is None:
            _LOGGER.warning("No %s model: a ringing timer can only be stopped by a call", STOP)
        self.engine = await asyncio.to_thread(
            Engine,
            self._ctc_dir,
            self.tdt_dir(),
            self.options.embedding_dir,
            self.options.threads,
        )
        self.engine.entity_area = self._entity_area
        await self.reload_commands()
        self._spawn(self.configure_fallback())
        self.configure_agent()
        await self._ensure_ratios()
        await self.speech.start()
        if self.speakers is not None:
            await self.speakers.start()

        self._store_watcher = asyncio.ensure_future(self._watch_store())
        from .web import start_web  # pylint: disable=import-outside-toplevel

        self._web = await start_web(self)

    async def stop(self) -> None:
        for task in (self._store_watcher,):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        if self._web is not None:
            await self._web.cleanup()
        for task in list(self._sessions.values()):
            task.cancel()
        self._sessions.clear()
        if self._discovery is not None:
            await self._discovery.stop()
            self._discovery = None
        await self.wake.stop()
        await self.hass.stop()

    # ------------------------------------------------------------- commands

    async def reload_commands(self) -> Compiled | None:
        """Load commands.yaml and recognize its commands. A file that does not
        parse keeps the previous commands (and says why in the UI)."""
        try:
            compiled = self.store.load()
        except (ConfigError, ValueError, OSError) as err:
            self.store_error = str(err)
            _LOGGER.error("commands.yaml: %s; keeping the previous commands", err)
            return None
        await self.apply(compiled)
        return compiled

    async def apply(self, compiled: Compiled) -> None:
        """Recognize these commands from now on. Raises ConfigError when
        adapt-ctc cannot build them."""
        assert self.engine is not None
        async with self._build_lock:
            await asyncio.to_thread(self.engine.build, compiled)
        self.store_error = None
        fallback = self.engine.fallback
        if fallback is not None:
            if fallback.llm is None and self._llm_wanted():
                self._spawn(self.configure_fallback())  # the first command chosen for it
            else:
                fallback.prepare(self.engine)  # new commands, new tools

    # ------------------------------------------------------------ fallback

    def fallback_settings(self) -> dict[str, Any]:
        """``settings.yaml``: when nothing matched, run the closest command by
        meaning (``fallback_meaning``), and let a model (``fallback_llm``)
        pick one and fill in what it needs: GLiNER (``fallback_model``, the
        default) among the closest ``fallback_top_k`` commands that need
        something said, sure to at least ``fallback_threshold``, or an LLM.
        Both on unless turned off; each only for the commands chosen for it
        (``fallback: [meaning, llm]``)."""
        settings = self.store.settings
        model = str(settings.get("fallback_model") or DEFAULT_FALLBACK_MODEL)
        threshold = settings.get("fallback_threshold")
        return {
            "meaning": bool(settings.get("fallback_meaning", True)),
            "llm": bool(settings.get("fallback_llm", True)),
            "model": model if model in TOOL_MODELS or model in GLINER_MODELS else DEFAULT_FALLBACK_MODEL,
            "top_k": int(settings.get("fallback_top_k") or DEFAULT_TOP_K),
            "threshold": float(threshold) if threshold is not None else GLINER_THRESHOLD,
        }

    def _llm_wanted(self) -> bool:
        """The model step is turned on, and some command is chosen for it -- or
        chosen for meaning and hears something, which the model hears."""
        compiled = self.engine.compiled if self.engine is not None else None
        return (
            self.fallback_settings()["llm"]
            and compiled is not None
            and bool(chosen(compiled, "llm") or needs_model(compiled))
        )

    def fallback_status(self) -> dict[str, Any]:
        fallback = self.engine.fallback if self.engine is not None else None
        compiled = self.engine.compiled if self.engine is not None else None
        settings = self.fallback_settings()
        llm_commands = chosen(compiled, "llm") if compiled is not None else []
        if not settings["llm"]:
            status = "off"
        elif not llm_commands and not (compiled is not None and needs_model(compiled)):
            status = "no commands chosen for it"
        else:
            status = self._llm_status or (fallback.status if fallback is not None else "off")
        models = {
            m: {"name": i.name, "mb": i.mb, "licence": i.licence, "kind": "gliner",
                "installed": GlinerPicker.find_model(m, self.root) is not None}
            for m, i in GLINER_MODELS.items()
        }
        models.update(
            {m: {"name": i.name, "mb": i.mb, "licence": i.licence, "kind": "llm", "installed": llama_available()}
             for m, i in TOOL_MODELS.items()}
        )
        return {
            **settings,
            "models": models,
            "meaning_available": bool(self.engine and self.engine.embedder),
            "meaning_commands": len(chosen(compiled, "meaning")) if compiled is not None else 0,
            "llm_commands": len(llm_commands),
            "llm_installed": llama_available(),
            "llm_status": status,
        }

    async def configure_fallback(self) -> None:
        """Apply the fallback settings. GLiNER is loaded from models/ in the
        background; an LLM is downloaded the first time it is needed --
        turned on, with a command chosen for it -- then loaded."""
        if self.engine is None:
            return
        async with self._fallback_lock:
            await self._configure_fallback()

    async def _configure_fallback(self) -> None:
        assert self.engine is not None
        wanted = self.fallback_settings()
        current = self.engine.fallback
        llm = current.llm if current is not None else None
        self._llm_status = None
        if not self._llm_wanted() or (llm is not None and llm.model != wanted["model"]):
            llm = None
        if llm is None and self._llm_wanted():
            llm = await self._fallback_model(wanted["model"])
        if isinstance(llm, GlinerPicker):
            llm.threshold = wanted["threshold"]
        fallback = Fallback(wanted["meaning"], llm, wanted["top_k"])
        if llm is None:
            # Why it won't run, for a command chosen for it.
            fallback.by = "GLiNER" if wanted["model"] in GLINER_MODELS else "LLM"
            fallback.status = self._llm_status or ("turned off on the Settings page" if not wanted["llm"] else "off")
        self.engine.fallback = fallback
        fallback.prepare(self.engine)

    async def _fallback_model(self, model: str) -> ToolModel | GlinerPicker | None:
        """The model step's model, not loaded yet: GLiNER from models/, or an
        LLM from the Hugging Face cache or downloaded into the data
        directory. None (and why, in ``_llm_status``) when there is none."""
        if model in GLINER_MODELS:
            path = GlinerPicker.find_model(model, self.root)
            if path is None:
                directory = GLINER_MODELS[model].directory
                self._llm_status = f"not found: models/{directory} (scripts/export_gliner.py)"
                return None
            return GlinerPicker(path, n_threads=self.options.threads, model=model)
        info = TOOL_MODELS[model]
        if not llama_available():
            self._llm_status = "llama.cpp is not installed"
            return None
        self._llm_status = f"downloading {info.name} ({info.mb} MB)"
        path = await asyncio.to_thread(
            ToolModel.find_model, model, self.options.downloads, self.store.data_dir / "models"
        )
        if path is None:
            self._llm_status = f"could not download {info.name}"
            return None
        self._llm_status = None
        return ToolModel(path, n_threads=self.options.threads, model=model)

    # ------------------------------------------------- devices, responses, agent

    def _entity_area(self, entity_id: str) -> str | None:
        """A device's area in Home Assistant (its own, else its device's)."""
        catalog = self.hass.catalog
        entity = catalog.entities.get(entity_id) if catalog is not None else None
        return entity.area_id if entity is not None else None

    def beep_elsewhere(self) -> str:
        """``settings.yaml`` ``beep_elsewhere``: what a command that beeps
        says when it acted out of the satellite's area (actions.BEEP_ELSEWHERE
        unless set)."""
        value = self.store.settings.get("beep_elsewhere")
        return value if value in VERBOSITY[1:] else actions.BEEP_ELSEWHERE

    def agent_settings(self) -> dict[str, Any]:
        """``settings.yaml``: the Home Assistant conversation agent that gets
        what nothing here understood (``agent``; none unless chosen), and how
        long a satellite's conversation with it is kept (``agent_keep_minutes``,
        five as on the Voice PE)."""
        settings = self.store.settings
        try:
            minutes = float(settings.get("agent_keep_minutes", KEEP_SECONDS / 60))
        except (TypeError, ValueError):
            minutes = KEEP_SECONDS / 60
        return {"agent": settings.get("agent") or None, "keep_minutes": minutes}

    def configure_agent(self) -> None:
        wanted = self.agent_settings()
        self.agent.configure(wanted["agent"], wanted["keep_minutes"] * 60)
        if self.engine is not None:
            # The live skill ("can't look that up") waits for the agent.
            self.engine.agent_answers = wanted["agent"] is not None

    def agent_status(self) -> dict[str, Any]:
        return {**self.agent_settings(), "conversations": self.agent.status()}

    async def _watch_store(self) -> None:
        while True:
            await asyncio.sleep(STORE_POLL_SECONDS)
            if self.store.changed_on_disk():
                _LOGGER.info("commands.yaml changed on disk; reloading")
                await self.reload_commands()

    # ----------------------------------------------------------- satellites

    def satellite(self, key: str) -> Satellite:
        """A satellite as commands see it. Its area is its Home Assistant
        device's when the Voice Call integration knows it, else the app's own.
        Its music player is the one picked for it, else its Sendspin client's
        in Music Assistant (found by MAC by the integration, else guessed)."""
        stored = self.store.satellite(key)
        session = self.sessions.get(key)
        name = stored.get("name") or (session.hello.get("name") if session else None) or key
        link = self.integration.links.get(key)
        node = (session.hello.get("name") if session else None) or stored.get("node")
        media_player = (
            stored.get("media_player")
            or (link.media_player if link is not None else None)
            or self.hass.catalog.music_player_for(key, node)
        )
        extra = {
            "media_player": media_player,
            "voice": choose(self.store.settings, stored),
            "device_media_player": self.hass.catalog.device_media_player(key) if key else None,
            "command_groups": self.satellite_command_groups(key),
            "skills": self.satellite_skills(key),
        }
        if link is not None and link.device_id:
            area = self.hass.catalog.device_areas.get(link.device_id)
            info = self.hass.catalog.area_info(area)
            return Satellite(key, name, **info, device_id=link.device_id, timer_list=link.timer_list, **extra)
        return Satellite(key, name, **self.hass.catalog.area_info(stored.get("area")), **extra)

    # ----------------------------------------------------------- TDT model

    def tdt_dir(self, choice: str | None = None) -> Path | None:
        """The TDT model's directory: the one picked (settings.yaml
        ``tdt_model``), else English. None with ``--no-tdt``."""
        if not self.options.tdt:
            return None
        choice = choice or str(self.store.settings.get("tdt_model") or DEFAULT_TDT)
        return self._tdt_dirs.get(choice) or self._tdt_dirs.get(DEFAULT_TDT)

    def tdt_models(self) -> list[dict[str, Any]]:
        from adapt_ctc.tdt import has_model  # pylint: disable=import-outside-toplevel

        current = self.engine.tdt_dir if self.engine is not None else None
        out = []
        for model_id, (name, detail) in TDT_MODELS.items():
            path = self._tdt_dirs.get(model_id)
            out.append(
                {
                    "id": model_id,
                    "name": name,
                    "detail": detail,
                    "available": bool(path and has_model(path)),
                    "loaded": bool(path and current and Path(current).resolve() == path.resolve()),
                }
            )
        return out

    async def change_tdt(self) -> None:
        """Load the picked TDT model in place of the current one. Recognition
        waits for it; the previous model stays if it cannot be loaded."""
        wanted = self.tdt_dir()
        if self.engine is None or wanted is None or self.engine.tdt_dir == wanted:
            return
        self.tdt_status = f"loading {wanted.name}"
        async with self._model_lock:
            loaded = await asyncio.to_thread(self.engine.use_tdt, wanted)
        self.tdt_status = None if loaded else f"could not load {wanted.name}; kept {self.engine.tdt_dir}"

    def satellite_command_groups(self, key: str | None) -> frozenset[str] | None:
        """The command groups a satellite is limited to: its own, else every
        satellite's; None (every command) without any. Groups that no longer
        exist are left out."""
        compiled = self.engine.compiled if self.engine is not None else None
        known = compiled.groups if compiled is not None else {}
        for source in (self.store.satellite(key or "").get("command_groups"), self.store.settings.get("command_groups")):
            picked = frozenset(str(g) for g in source or () if str(g) in known)
            if picked:
                return picked
        return None

    def satellite_skills(self, key: str | None) -> frozenset[str]:
        """The skills that answer on a satellite: each as its own setting says,
        else the home's, else on (voice_call/skills/)."""
        own = self.store.satellite(key).get("skills") if key else None
        return skills.enabled(self.store.settings.get("skills"), own)

    def _remember_satellite(self, session: Session) -> None:
        entry = self.store.satellites.setdefault(session.satellite, {})
        changed = False
        if not entry.get("name"):
            entry["name"] = str(session.hello.get("name") or session.device_id)
            changed = True
        if entry.get("device_id") != session.device_id:
            entry["device_id"] = session.device_id
            changed = True
        node = session.hello.get("name")
        if node and entry.get("node") != node:
            entry["node"] = str(node)  # its ESPHome name, for finding its music player
            changed = True
        if changed:
            self.store.save_satellites()
        self.integration.want(session.satellite, entry["name"])

    # ----------------------------------------------------------- wake words

    def custom_wake_phrases(self) -> dict[str, str]:
        """Custom wake words by id, with what the verifier would accept."""
        return {w.id: w.phrases[0] for w in self.wake_words.scan().values() if w.custom and w.phrases}

    def _verifier_config(self, tuning: dict[str, dict[str, Any]] | None = None):
        """The verifier's config with what the Wake words page changed.
        Raises the verifier's ConfigError."""
        return self.wake.build(self.store.wake_tuning if tuning is None else tuning, self.custom_wake_phrases())

    def default_ratio(self, word_id: str) -> float:
        """The second opinion's threshold unless the page sets one."""
        if self.options.wake_ratio is not None and self.options.wake_ratio >= 0:
            return self.options.wake_ratio
        return RATIO_THRESHOLD.get(word_id, DEFAULT_RATIO)

    def ratio_threshold(self, word_id: str) -> float:
        tuned = (self.store.wake_tuning.get(word_id) or {}).get("second_opinion")
        return float(tuned) if tuned is not None else self.default_ratio(word_id)

    async def _ensure_ratios(self) -> None:
        """The second opinion for each verified wake word, at its threshold
        (none with ``--wake-ratio`` below 0)."""
        if self.engine is None or (self.options.wake_ratio is not None and self.options.wake_ratio < 0):
            return
        words = self.wake_words.scan()
        for word_id in [w for w in self.wake_ratios if w not in words or not words[w].verified]:
            del self.wake_ratios[word_id]
        for word in words.values():
            if not word.verified or not word.phrases:
                continue
            ratio = self.wake_ratios.get(word.id)
            if ratio is not None and ratio.wake_word == word.phrases[0]:
                ratio.threshold = self.ratio_threshold(word.id)
                continue
            ratio = WakeRatio(word.phrases[0], threshold=self.ratio_threshold(word.id))
            try:
                # Shares the recognizer's loaded net.
                await asyncio.to_thread(ratio.train, self.engine.model)
            except Exception:  # noqa: BLE001 - a missing second opinion is not fatal
                _LOGGER.exception("Wake word second opinion unavailable for %s", word.id)
                continue
            self.wake_ratios[word.id] = ratio

    async def tune_wake_word(self, word_id: str, entry: dict[str, Any] | None) -> list[str]:
        """Keep what the Wake words page set for ``word_id`` (None: nothing,
        its defaults) and run with it: its verifier rebuilt if that changed,
        its second opinion's threshold, and satellites given its cutoff.
        Returns the verifiers rebuilt. Raises ConfigError, changing nothing,
        when what it says can't be verified."""
        async with self._wake_lock:
            tuning = {k: v for k, v in self.store.wake_tuning.items() if k != word_id}
            if entry:
                tuning[word_id] = entry
            try:
                config = await asyncio.to_thread(self._verifier_config, tuning)
            except WakeConfigError as err:
                raise ConfigError(str(err)) from err
            self.store.wake_tuning = tuning
            self.store.save_wake_tuning()
            self.wake_error = None
            rebuilt = await self.wake.apply(config)
            self.wake_words.forget()
            await self._ensure_ratios()
            # A custom wake word verified now, where it is listened for.
            if word_id in self._wake_words_in_use() and self.wake.knows(word_id):
                await self.wake.ensure([word_id])
            self.wake_words_changed()
            return rebuilt

    # ---------------------------------------------------------- wake models

    @property
    def default_wake_words(self) -> tuple[str, ...]:
        """The app's own default, when nothing is picked anywhere."""
        return tuple(w for w in self.options.wake_models if w not in SPECIAL)

    def satellite_wake_words(self, key: str) -> list[str]:
        """What this satellite listens for: its own pick, else every
        satellite's, else the app's default."""
        return self.wake_words.chosen(
            self.store.settings, self.store.satellite(key), self.default_wake_words
        )

    def _wake_words_in_use(self) -> list[str]:
        keys = dict.fromkeys([*self.store.satellites, *self.sessions])
        used = [w for key in keys for w in self.satellite_wake_words(key)]
        used += self.wake_words.chosen(self.store.settings, {}, self.default_wake_words)
        return list(dict.fromkeys(used))

    def _wake_models_for(self, session: Session) -> list[WakeModel]:
        """The VAD gate and the satellite's wake words, as many as fit beside
        "stop" in the device's model slots.

        The device has no TFLite interpreter and no flatbuffer parser, so each
        graph is walked here and sent as a flat op table plus weights.
        """
        slots = int(session.hello.get("model_slots") or DEFAULT_MODEL_SLOTS)
        vad = self.wake_words.model(VAD)
        room = max(1, slots - RESERVED_SLOTS)
        wanted = self.satellite_wake_words(session.satellite)
        models = [m for m in (self.wake_words.model(w) for w in wanted) if m is not None]
        if len(models) > room:
            _LOGGER.warning(
                "[%s] Only %d wake word(s) fit on this device; not listening for %s",
                session,
                room,
                ", ".join(m.model_id for m in models[room:]),
            )
            models = models[:room]
        if not models:
            _LOGGER.warning("[%s] No wake word model loaded; the device cannot wake", session)
        return ([vad] if vad is not None else []) + models

    async def _push_wake_models(self, session: Session) -> None:
        """Send the satellite's models. The device keeps none across a reboot,
        and may still hold another set from before a reconnect, so they are
        cleared first -- which stops its microphone, so never during a call."""
        while session.call is not None and session.writer is not None:
            await asyncio.sleep(0.5)
        if session.writer is None:
            return
        models = self._wake_models_for(session)
        session.wake_words = [m.model_id for m in models if not m.is_vad]
        session.wake_cutoffs = {m.model_id: m.probability_cutoff for m in models if not m.is_vad}
        for model in models:
            _LOGGER.info(
                "[%s] Wake model %r: %d bytes, cutoff %.2f, window %d%s",
                session,
                model.model_id,
                len(model.blob),
                model.probability_cutoff,
                model.sliding_window_size,
                " (VAD gate)" if model.is_vad else "",
            )
        verified = [w for w in session.wake_words if self.wake.knows(w)]
        if verified:
            # Its graphs and decoders, before the first call needs them.
            self._spawn(self.wake.ensure(verified))
        await self._send(session, Message.MODEL_CLEAR)
        await self._push_models(session, models)

    def wake_words_changed(self, key: str | None = None) -> None:
        """A satellite's pick (or every satellite's), or a cutoff, changed:
        push again where it changes what the device listens for. A ringing
        satellite gets them when the ring ends."""
        for session in list(self.sessions.values()):
            if key is not None and session.satellite != key:
                continue
            if session.writer is None or (session.ringing and not session.ringing.done()):
                continue
            wanted = [(m.model_id, m.probability_cutoff) for m in self._wake_models_for(session) if not m.is_vad]
            if wanted != [(w, session.wake_cutoffs.get(w)) for w in session.wake_words]:
                self._spawn(self._push_wake_models(session))

    async def _push_models(self, session: Session, models: list[WakeModel], first_slot: int = 0) -> None:
        for slot, model in enumerate(models, start=first_slot):
            await self._send_json(session, Message.MODEL_START, model.header(slot))
            for offset in range(0, len(model.blob), MAX_PAYLOAD):
                await self._send(
                    session,
                    Message.MODEL_DATA,
                    model.blob[offset : offset + MAX_PAYLOAD],
                )
            await self._send_json(session, Message.MODEL_END, {"id": model.model_id})

    def _spawn(self, coro) -> None:
        """Run beside the read loop, which must stay free to deliver frames."""
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ------------------------------------------------------------ discovery

    async def run_forever(self) -> None:
        """Serve every satellite on the network, as they come and go."""
        if self.options.host:
            # Explicit host pins the app to one device: useful across subnets,
            # and for testing one satellite while others are on the network.
            await self._run_session(
                Session(key="manual", host=self.options.host, port=self.options.port)
            )
            return

        self._discovery = Discovery()
        await self._discovery.start()
        try:
            while True:
                change, device = await self._discovery.events.get()
                if change == "added":
                    self._start_session(device)
                else:
                    self._stop_session(device)
        finally:
            await self._discovery.stop()
            for task in list(self._sessions.values()):
                task.cancel()

    def _start_session(self, device: Device) -> None:
        existing = self._sessions.pop(device.key, None)
        if existing is not None:
            existing.cancel()
        _LOGGER.info("Satellite appeared: %s", device)
        session = Session(key=device.key, host=device.host, port=device.port)
        task = asyncio.ensure_future(self._run_session(session))
        self._sessions[device.key] = task
        task.add_done_callback(
            lambda t, key=device.key: self._sessions.pop(key, None)
            if self._sessions.get(key) is t
            else None
        )

    def _stop_session(self, device: Device) -> None:
        task = self._sessions.pop(device.key, None)
        if task is not None:
            _LOGGER.info("Satellite went away: %s", device)
            task.cancel()

    async def _run_session(self, session: Session) -> None:
        backoff = self.options.reconnect_seconds
        try:
            while True:
                try:
                    _LOGGER.info("Connecting to %s:%d", session.host, session.port)
                    reader, writer = await asyncio.open_connection(
                        session.host, session.port
                    )
                except OSError as err:
                    _LOGGER.warning("Connect failed (%s); retrying in %.0fs", err, backoff)
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, self.options.max_reconnect_seconds)
                    continue

                self._enable_keepalive(writer)
                session.writer = writer
                started = time.monotonic()
                try:
                    await self._read_loop(session, reader)
                except (asyncio.IncompleteReadError, ConnectionError, OSError) as err:
                    # OSError covers the keepalive firing on a rebooted device,
                    # which surfaces as ETIMEDOUT rather than a ConnectionError.
                    _LOGGER.warning("[%s] Connection lost: %s", session, err)
                finally:
                    session.call = None
                    session.writer = None
                    session.connected_at = None
                    # Whoever listened hears the end: the device stopped streaming.
                    for queue in session.listeners:
                        while not queue.empty():
                            queue.get_nowait()
                        queue.put_nowait(None)
                    session.listeners.clear()
                    writer.close()
                    with contextlib.suppress(Exception):
                        await writer.wait_closed()

                # A connection that barely lived means something is wrong that
                # retrying fast will not fix -- most often a second app holding the
                # device. Backing off turns a busy loop into a slow one, and the
                # growing delay in the log is the clue that it is not the network.
                if time.monotonic() - started >= STABLE_CONNECTION_SECONDS:
                    backoff = self.options.reconnect_seconds
                else:
                    _LOGGER.warning(
                        "[%s] Connection lasted under %.0fs; retrying in %.0fs. If "
                        "this repeats, check whether another app is connected to "
                        "the device -- it accepts one client at a time.",
                        session,
                        STABLE_CONNECTION_SECONDS,
                        backoff,
                    )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self.options.max_reconnect_seconds)
        finally:
            if session.satellite and self.sessions.get(session.satellite) is session:
                del self.sessions[session.satellite]

    @staticmethod
    def _enable_keepalive(writer: asyncio.StreamWriter) -> None:
        """Notice a device that rebooted under an idle connection.

        Nothing crosses the socket between calls, so a device that reboots
        leaves the app holding a half-open connection it will never see fail --
        and the device, having lost its models to the reboot, stays deaf until
        the app reconnects and pushes them again.
        """
        sock = writer.get_extra_info("socket")
        if sock is None:
            return
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 10)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 5)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
        except OSError as err:
            _LOGGER.warning("Could not enable TCP keepalive: %s", err)

    async def _read_loop(
        self, session: Session, reader: asyncio.StreamReader
    ) -> None:
        while True:
            header = await reader.readexactly(HEADER.size)
            msg_type, length = HEADER.unpack(header)
            payload = await reader.readexactly(length) if length else b""
            await self._handle(session, msg_type, payload)

    async def _send(
        self, session: Session, msg_type: Message, payload: bytes = b""
    ) -> None:
        if session.writer is None:
            return
        session.writer.write(encode(msg_type, payload))
        # Propagates the device's TCP backpressure back to whatever produced
        # this audio, so playback is never queued faster than it drains.
        await session.writer.drain()

    async def _send_json(self, session: Session, msg_type: Message, obj: dict) -> None:
        if session.writer is None:
            return
        session.writer.write(encode_json(msg_type, obj))
        await session.writer.drain()

    async def _event(self, session: Session, event_type: str, **data) -> None:
        await self._send_json(
            session, Message.EVENT, {"type": event_type, "data": data}
        )

    async def _handle(self, session: Session, msg_type: int, payload: bytes) -> None:
        if msg_type == Message.AUDIO_IN:
            if session.listeners:
                self._live_call_audio(session, payload)
            await self._on_audio(session, payload)
            return

        if msg_type == Message.AUDIO_MONITOR:
            self._live(session, payload)
            return

        if msg_type == Message.WAKE_REPORT:
            if session.listeners:
                self._live(session, self._wake_report(json.loads(payload) if payload else {}))
            return

        if msg_type == Message.AUDIO_IN_AEC:
            if session.call is not None:
                session.call.aec += payload
            return

        info = json.loads(payload) if payload else {}

        if msg_type == Message.HELLO:
            session.device_id = _device_id(info)
            session.hello = info
            session.satellite = _satellite_key(info, session.key)
            session.connected_at = time.time()
            self.sessions[session.satellite] = session
            self._remember_satellite(session)
            _LOGGER.info("[%s] Device: %s", session, info)
            self._spawn(self._push_wake_models(session))
            self._spawn(self._send_timer_progress(session, force=True))
        elif msg_type == Message.MODEL_RESULT:
            if info.get("ok"):
                _LOGGER.info(
                    "[%s] Device loaded model %r (%s resident)",
                    session,
                    info.get("id"),
                    info.get("models"),
                )
            else:
                _LOGGER.error(
                    "[%s] Device rejected model %r: %s",
                    session,
                    info.get("id"),
                    info.get("error"),
                )
        elif msg_type == Message.CALL_START:
            self._on_call_start(session, info)
            session.live_call = True
            self._live(session, {"type": "call", "active": True, "wake_word": session.call.wake_word if session.call else None})
        elif msg_type == Message.CALL_END:
            self._live_call_ended(session)
            _LOGGER.info("[%s] Device ended call: %s", session, info.get("reason"))
            call = session.call
            if call is not None and call.record is not None and call.record.finished is None:
                self.calls.finish(call.record, "ended by device", str(info.get("reason") or ""))
            session.call = None
        elif msg_type == Message.PLAYBACK_DRAINED:
            session.drained.set()
        elif msg_type == Message.DEVICE_EVENT:
            kind = info.get("type")
            _LOGGER.debug("[%s] Device event: %s", session, kind)
            if kind == "announcing":
                session.announcing.set()
            elif kind == "announced":
                session.announced.set()
            elif kind == "stop_timer" and session.ringing is not None and not session.ringing.done():
                # The center button.
                _LOGGER.info("[%s] Timer stopped by the button", session)
                session.ring_stopped_by = "button"
                session.ringing.cancel()
        elif msg_type == Message.STATUS:
            _LOGGER.debug("[%s] status: %s", session, info)
        else:
            _LOGGER.warning("Unhandled message 0x%02X: %s", msg_type, info)

    # ------------------------------------------------------------ listening

    LIVE_QUEUE = 200
    """Chunks a slow listener may fall behind by before chunks are dropped."""

    def _live(self, session: Session, item: bytes | dict) -> None:
        """Audio (or an event: a call starting, ending) for whoever listens."""
        for queue in list(session.listeners):
            try:
                queue.put_nowait(item)
            except asyncio.QueueFull:
                pass  # a listener behind by seconds loses audio, not the app

    def _wake_report(self, report: dict) -> dict:
        """The device's report, for the page: names, and probabilities 0-1."""
        models = []
        for model in report.get("models") or []:
            model_id = str(model.get("id") or "")
            word = self.wake_words.get(model_id)
            models.append({
                "id": model_id,
                "name": "Voice activity" if model.get("vad") else (word.name if word is not None else model_id),
                "vad": bool(model.get("vad")),
                "cutoff": round(int(model.get("cutoff") or 0) / 255, 3),
                "peak": round(int(model.get("peak") or 0) / 255, 3),
                "window": round(int(model.get("window") or 0) / 255, 3),
            })
        return {"type": "wake", "models": models, "detected": [str(d) for d in report.get("detected") or []]}

    def _live_call_ended(self, session: Session) -> None:
        """Hung up, or ended by the device: once."""
        if session.live_call:
            session.live_call = False
            self._live(session, {"type": "call", "active": False})

    def _live_call_audio(self, session: Session, chunk: bytes) -> None:
        """A call's audio for whoever listens, without its pre-roll, which
        they heard as it happened."""
        call = session.call
        if call is not None:
            seen = call.live_bytes
            call.live_bytes += len(chunk)
            chunk = chunk[max(0, min(len(chunk), call.preroll_bytes - seen)) :]
        if chunk:
            self._live(session, chunk)

    async def listen(self, key: str) -> asyncio.Queue:
        """Someone starts listening to a satellite live: its microphone from
        now on (bytes), and its calls (``{"type": "call", "active": ...}``);
        None when it disconnects. The device streams it between calls, and
        shows that it does."""
        session = self.sessions.get(key)
        if session is None or session.writer is None:
            raise ConnectionError("The satellite is not connected")
        if not session.hello.get("monitor"):
            raise ConfigError("This satellite's firmware can't stream outside calls: update it to listen live")
        queue: asyncio.Queue = asyncio.Queue(maxsize=self.LIVE_QUEUE)
        session.listeners.add(queue)
        if len(session.listeners) == 1:
            _LOGGER.info("[%s] Someone is listening live", session)
            await self._send_json(session, Message.MONITOR, {"active": True})
        return queue

    async def stop_listening(self, key: str, queue: asyncio.Queue) -> None:
        session = self.sessions.get(key)
        if session is None or queue not in session.listeners:
            return
        session.listeners.discard(queue)
        if not session.listeners:
            _LOGGER.info("[%s] Nobody is listening live any more", session)
            with contextlib.suppress(ConnectionError, OSError):
                await self._send_json(session, Message.MONITOR, {"active": False})

    # ----------------------------------------------------------------- calls

    def _on_call_start(self, session: Session, info: dict) -> None:
        heard = str(info.get("wake_word", ""))
        session.call = Call(
            preroll_bytes=int(info.get("preroll_bytes", 0)),
            # The id it was pushed with; older firmware or models report a name.
            wake_word=self.wake_words.resolve(heard) or heard,
            aec_preroll_bytes=int(info.get("aec_preroll_bytes", 0)),
        )
        session.drained.clear()
        if session.ringing is not None and not session.ringing.done():
            # The device already cut the alarm off when the call started.
            session.ringing.cancel()
            session.call.interrupted_ring = True
            if session.call.wake_word.strip().lower() == STOP_MODEL:
                record = self.calls.begin("call", self.satellite(session.satellite).as_dict())
                record.stage("timer_stopped", by="stop", timers=[t.get("name") for t in session.ringing_timers])
                self.calls.finish(record, "timer stopped", "stopped the timer")
                session.call.finished = True
                session.call.verified = True
                self._spawn(self._hang_up(session, "timer_stopped"))
                return
        record = self.calls.begin("call", self.satellite(session.satellite).as_dict())
        session.call.record = record
        record.marks["detection"] = session.call.preroll_bytes / BYTE_RATE
        word = self.wake_words.get(session.call.wake_word)
        # What the wake word came in with, 0-1 (the device's are 0-255).
        detection = {
            key: round(float(info[key]) / 255, 3)
            for key in ("probability", "max_probability", "cutoff")
            if isinstance(info.get(key), (int, float))
        }
        record.stage(
            "call_start",
            wake_word=session.call.wake_word,
            name=word.name if word is not None else None,
            **detection,
            preroll=round(session.call.preroll_bytes / BYTE_RATE, 3),
            aec_preroll=round(session.call.aec_preroll_bytes / BYTE_RATE, 3),
        )
        _LOGGER.info(
            "[%s] Call started: wake_word=%r%s pre-roll=%dms%s",
            session,
            session.call.wake_word,
            f" probability={detection['probability']:.2f}" if "probability" in detection else "",
            session.call.preroll_bytes * 1000 // BYTE_RATE,
            (
                f" aec pre-roll={int(info['aec_preroll_bytes']) * 1000 // BYTE_RATE}ms"
                if info.get("aec")
                else ""
            ),
        )
        self._spawn(self._watchdog(session, session.call))

    async def _watchdog(self, session: Session, call: Call) -> None:
        """End the call even if the VAD never fires or audio stops arriving."""
        await asyncio.sleep(self.options.max_command_seconds)
        if session.call is call and not call.finished:
            _LOGGER.warning(
                "[%s] Command exceeded %.1fs; ending",
                session,
                self.options.max_command_seconds,
            )
            call.finished = True
            if call.record is not None:
                self.calls.finish(call.record, "timeout", "", self._call_audio(call))
            self._spawn(self._hang_up(session, "timeout"))

    async def _on_audio(self, session: Session, chunk: bytes) -> None:
        call = session.call
        if call is None or call.finished:
            return

        call.audio += chunk

        # The pre-roll is the verification window: it ends at the detection, so
        # it holds the wake word and whatever preceded it.
        if not call.verified:
            if len(call.audio) < call.preroll_bytes:
                return
            call.verified = True
            self._spawn(self._verify(session, call))
            return

        if call.endpointer is None:
            return  # verification still running; audio keeps accumulating

        start = max(call.split_bytes, len(call.audio) - len(chunk))
        new_audio = bytes(call.audio[start:])
        if not new_audio:
            return
        call.command += new_audio
        if call.endpointer.process(new_audio) and not call.finished:
            call.finished = True
            self._spawn(self._respond(session, call))

    def _ran_into_edge(self, result: Any, call: Call) -> bool:
        """Did the wake phrase end at the end of the pre-roll?

        The pre-roll ends *at* the detection, so a detector that fires before
        the speaker has finished leaves the last phoneme outside the window.
        The aligner then has nowhere to put the end of the word but the edge,
        and the thresholds are applied to a truncated phrase.
        """
        if self.options.wake_tail <= 0 or result.split is None:
            return False
        return result.split >= (call.preroll_bytes / BYTE_RATE) - TAIL_EDGE_SECONDS

    async def _audio_through(self, call: Call, want: int, timeout: float) -> bool:
        """Wait for the call to have ``want`` bytes. False if it never does."""
        deadline = time.monotonic() + timeout
        while len(call.audio) < want and not call.finished:
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(0.02)
        return len(call.audio) >= want

    async def _verify_at(
        self, label: str, call: Call, offset: int
    ) -> tuple[Any, bytes]:
        """Verify the pre-roll-sized window that starts ``offset`` bytes in."""
        window = bytes(call.audio[offset : offset + call.preroll_bytes])
        result = await self.wake.verify(window, call.wake_word or None)
        assert result is not None
        _LOGGER.info(
            "[%s] Wake verification%s: accepted=%s rejected_by=%s split=%s "
            "decode=%.0fms",
            label,
            f" (+{offset * 1000 // BYTE_RATE}ms)" if offset else "",
            result.accepted,
            result.rejected_by,
            result.split,
            result.decode_ms,
        )
        if call.record is not None:
            call.record.stage(
                "wake_kaldi", offset=round(offset / BYTE_RATE, 3), **result.to_dict()
            )
        return result, window

    async def _verify_wake(self, label: str, call: Call) -> WakeVerdict:
        """Kaldi on the pre-roll, retried with a tail when the wake word ran
        off its end, and the second opinion on a `last`/`prefix` rejection.
        A wake word with no verifier (a custom one) stands as detected."""
        if call.wake_word and not self.wake.knows(call.wake_word):
            window = bytes(call.audio[: call.preroll_bytes])
            if call.record is not None:
                call.record.stage("wake_unverified", wake_word=call.wake_word)
            return WakeVerdict(True, VerifyResult(call.wake_word, accepted=True), 0, window)
        offset = 0
        result, window = await self._verify_at(label, call, offset)
        verdict = WakeVerdict(result.accepted, result, 0, window)

        if not result.accepted and self._ran_into_edge(result, call):
            tail = ms_to_bytes(self.options.wake_tail * 1000)
            # The retry needs audio the device has not sent yet: the call opens
            # at the detection, so this is a real wait rather than a slice.
            if await self._audio_through(
                call, call.preroll_bytes + tail, self.options.wake_tail + 0.5
            ):
                verdict.retried = True
                retry, retry_window = await self._verify_at(label, call, tail)
                if retry.accepted or retry.split is not None:
                    verdict = WakeVerdict(retry.accepted, retry, tail, retry_window, retried=True)

        if not verdict.accepted:
            # The thresholds Kaldi applies to the phrase and to its last word
            # were calibrated on adult speech, and they refuse a lot of real
            # wake words from other voices. Ask the recognizer, which is
            # already loaded, whether the window is the wake word at all --
            # but never about `confusable`, which is the one decision it gets
            # wrong far more often than Kaldi does.
            ratio = self.wake_ratios.get(verdict.result.wake_word_id)
            overturned, score = (
                ratio.would_accept(verdict.result.rejected_by, verdict.window)
                if ratio is not None
                else (False, None)
            )
            verdict.ratio = score
            verdict.overturned = overturned
            verdict.accepted = overturned
            if call.record is not None:
                call.record.stage(
                    "wake_ratio",
                    rejected_by=verdict.result.rejected_by,
                    score=None if score is None else round(float(score), 4),
                    overturned=overturned,
                )
        return verdict

    async def _verify(self, session: Session, call: Call) -> None:
        verdict = await self._verify_wake(str(session), call)
        result, offset = verdict.result, verdict.offset

        if not verdict.accepted:
            return await self._wake_rejected(session, call, verdict)
        if verdict.overturned:
            _LOGGER.info(
                "[%s] Wake %s rejection overturned on the ratio (%.2f)",
                session,
                result.rejected_by,
                verdict.ratio if verdict.ratio is not None else float("nan"),
            )
            await self._event(
                session, "wake_overturned", reason=result.rejected_by or "unknown"
            )

        self._place_split(call, verdict)
        call.endpointer = Endpointer(silence_seconds=self.options.silence_seconds)
        call.command += bytes(call.audio[call.split_bytes :])
        await self._event(session, "listening", stage="listening")
        if call.endpointer.process(bytes(call.command)) and not call.finished:
            call.finished = True
            self._spawn(self._respond(session, call))

    @staticmethod
    def _place_split(call: Call, verdict: WakeVerdict) -> None:
        # Everything after the wake phrase is command audio, including what
        # arrived while the decode was running. The split is measured from the
        # start of the window that was decoded, which the retry moves. This cut
        # is for endpointing; recognition finds the wake phrase again.
        result, offset = verdict.result, verdict.offset
        call.split_bytes = (
            offset + ms_to_bytes(result.split * 1000)
            if result.split
            else call.preroll_bytes
        )
        if result.wake_start is not None:
            call.wake_start = offset / BYTE_RATE + result.wake_start
        if call.record is not None:
            marks = call.record.marks
            marks["kaldi_split"] = call.split_bytes / BYTE_RATE
            if call.wake_start is not None:
                marks["kaldi_wake_start"] = call.wake_start

    async def _wake_rejected(self, session: Session, call: Call, verdict: WakeVerdict) -> None:
        """Hang up on a wake word neither stage would accept.

        Keep the audio: a rejected wake word is otherwise invisible, and the
        rejections are what need examining when the rate looks wrong.
        """
        result = verdict.result
        if result.split:
            call.command += bytes(call.audio[ms_to_bytes(result.split * 1000) :])
        self._save(session, call, prefix="rejected-")
        if verdict.ratio is not None:
            _LOGGER.info(
                "[%s] Ratio agreed with the %s rejection (%.2f)",
                session,
                result.rejected_by,
                verdict.ratio,
            )
        await self._event(
            session, "wake_rejected", reason=result.rejected_by or "unknown"
        )
        call.finished = True
        if call.record is not None:
            self.calls.finish(
                call.record,
                "wake rejected",
                f"wake word rejected ({result.rejected_by or 'unknown'})",
                self._call_audio(call),
            )
        await self._hang_up(session, "wake_rejected")

    @staticmethod
    def _call_audio(call: Call) -> dict[str, bytes]:
        return {"call": bytes(call.audio), "call-aec": bytes(call.aec), "command": bytes(call.command)}

    async def _respond(self, session: Session, call: Call) -> None:
        """Recognize the command and do it. Whatever goes wrong, the call is
        answered and hung up rather than left open on the satellite."""
        try:
            await self._respond_unguarded(session, call)
        except Exception as err:  # noqa: BLE001 - the satellite must hear back
            _LOGGER.exception("[%s] Call failed", session)
            if call.record is not None and call.record.finished is None:
                call.record.stage("error", error=repr(err))
                self.calls.finish(call.record, "error", repr(err), self._call_audio(call))
            with contextlib.suppress(Exception):
                await self._say(session, "Sorry, something went wrong.")
            with contextlib.suppress(Exception):
                await self._hang_up(session, "error")

    async def _respond_unguarded(self, session: Session, call: Call) -> None:
        self._save(session, call)
        record = call.record
        _LOGGER.info(
            "[%s] Command audio: %.2fs", session, len(call.command) / BYTE_RATE
        )
        if record is not None:
            record.stage("endpoint", command_seconds=round(len(call.command) / BYTE_RATE, 3))
            record.marks["end"] = len(call.audio) / BYTE_RATE

        # Speaker ID runs beside recognition -- its own lock and threads -- and
        # sends the processing event the moment it knows, so the ring takes the
        # speaker's color while the command is still being recognized.
        if self.speakers is not None:
            speaker_task = asyncio.ensure_future(self._identify(session, call))
        else:
            speaker_task = None
            await self._event(session, "processing", speaker="unknown")

        satellite = self.satellite(session.satellite)
        transcriber = await self._transcriber(satellite)
        async with self._model_lock:
            assert self.engine is not None
            decision, split = await asyncio.to_thread(
                self.engine.recognize_call,
                _pcm_to_float(bytes(call.audio)),
                call.wake_start,
                call.split_bytes / BYTE_RATE,
                satellite,
                transcriber,
                self.wake_words.phrases(call.wake_word),
            )
        if record is not None:
            record.stage("split", **split.to_json())
            record.marks["cut"] = split.cut
            if split.wake_end is not None:
                record.marks["wake_start"] = split.wake_start
                record.marks["wake_end"] = split.wake_end
            record.stage("recognition", **decision.to_json(), stt=_stt_json(transcriber))
        ident = await speaker_task if speaker_task is not None else None

        self._after_identify(session, ident)
        speaker = _speaker_json(ident)
        dry_run = bool(record is not None and record.debug_session and record.dry_run)

        if call.interrupted_ring and (
            decision.command is None
            or (decision.behavior is not None and decision.behavior.does == "nothing")
            or timers.stops_ringing(decision.behavior.timer if decision.behavior else None, decision.slots)
        ):
            # "Okay Nabu, stop" (or "never mind", or anything) said over the
            # alarm stopped it.
            if record is not None:
                record.stage("timer_stopped", by="call", heard=decision.transcript)
                self.calls.finish(record, "timer stopped", "stopped the timer", self._call_audio(call))
            await self._hang_up(session, "timer_stopped")
            return
        speech, outcome, summary = await self._carry_out(decision, satellite, speaker, dry_run, record)
        answered = outcome == "answered"
        if answered:
            _LOGGER.info("[%s] Answered %r", session, decision.transcript)
            await self._event(session, "recognized", text=decision.transcript, command="question")
        elif decision.command is None:
            _LOGGER.info("[%s] Not recognized; heard %r", session, decision.transcript)
            await self._event(session, "not_recognized", heard=decision.transcript)
        else:
            await self._event(session, "recognized", text=decision.transcript, command=decision.command.id)
        if speech is None:
            # Silent: not even the acknowledgement sound ("never mind").
            if record is not None:
                record.stage("speech", text="", silent=True)
        else:
            await self._event(session, "responding", text=speech)
            await self._say(session, speech, record)
        if record is not None:
            self.calls.finish(record, outcome, summary, self._call_audio(call))
        await self._hang_up(session, "done" if decision.command or answered else "not_recognized")

    async def _carry_out(
        self,
        decision: Decision,
        satellite: Satellite,
        speaker: dict[str, Any],
        dry_run: bool,
        record: CallRecord | None,
    ) -> tuple[str | None, str, str]:
        """Run (or show) the decided command. Returns what to say (None: a
        silent command, not even the acknowledgement sound), the outcome, and
        a one-line summary."""
        if (
            decision.command is None
            and decision.answer is None
            and decision.refused is None
            and self.agent.agent_id
            and decision.transcript.strip()
        ):
            said = await self._ask_agent(decision, satellite, dry_run, record)
            if said:
                return said, "answered", f"{decision.transcript!r}: {self.agent.agent_id}'s answer"
            # "Can't look that up", which waited for the agent.
            late = skills.answer(decision.transcript, satellite.skills, last=True)
            if late is not None:
                decision.answer = late.to_json()
        if decision.command is None and decision.answer is not None and decision.answer.get("text"):
            answer = decision.answer
            return answer["text"], "answered", f"{decision.transcript!r}: {answer['kind']} answer"
        if decision.command is None:
            message = decision.message or actions.SORRY
            compiled = self.engine.compiled if self.engine is not None else None
            if compiled is not None and compiled.unknown and decision.refused is None:
                # Nothing understood it: the Unknown command says what to say.
                values = actions.unknown_variables(decision, satellite, speaker, compiled.commands, self.hass.catalog)
                said, error = await actions.say_unknown(self.hass, compiled.unknown, values)
                if record is not None:
                    record.stage("unknown", said=said, error=error, variables=values)
                if said:
                    message = said
            tried = llm_note(decision.fallback)
            summary = f"{decision.transcript!r}: {message}" + (f" ({tried})" if tried else "")
            return message, "not recognized", summary
        _LOGGER.info(
            "Matched %s (%s) slots=%s%s",
            decision.command.id,
            decision.matched_by,
            decision.slots,
            " [dry run]" if dry_run else "",
        )
        outcome = await actions.run(
            self.hass, decision, satellite, speaker, dry_run, self.integration, self.music, self.beep_elsewhere()
        )
        if record is not None:
            record.stage("action", **outcome.to_json())
        state = "error" if outcome.error else ("dry run" if dry_run else "done")
        return None if outcome.silent else outcome.speech, state, f"{decision.command.id}: {decision.transcript!r}"

    async def _ask_agent(
        self, decision: Decision, satellite: Satellite, dry_run: bool, record: CallRecord | None
    ) -> str | None:
        """What the conversation agent answers, in the satellite's
        conversation with it; None when it understood nothing, could not be
        asked, or (a dry run) was not: it could act in the home."""
        if dry_run:
            if record is not None:
                record.stage("agent", agent=self.agent.agent_id, asked=False, text=decision.transcript)
            return None
        reply = await self.agent.ask(satellite.key, decision.transcript, satellite.device_id)
        if record is not None:
            record.stage("agent", asked=True, asked_text=decision.transcript, **reply.to_json())
        _LOGGER.info(
            "Agent %s on %r: %r (%s%s, %.2fs)", reply.agent, decision.transcript, reply.text,
            reply.response_type or reply.error, " in a kept conversation" if reply.continued else "", reply.seconds,
        )
        return reply.text if reply.understood and reply.text else None

    async def _say(
        self, session: Session, text: str, record: CallRecord | None = None, paced: bool = False
    ) -> None:
        satellite = self.satellite(session.satellite)
        voice = satellite.voice
        if text.strip() and voice.tts:
            target = voice.speak_on or satellite.device_media_player
            if target is None:
                _LOGGER.warning("[%s] No media player of its own in Home Assistant; speaking with Piper", session)
            else:
                try:
                    await self._speak(session, satellite, target, text, record)
                    return
                except (HassError, ConnectionError, OSError, TimeoutError) as err:
                    _LOGGER.warning("[%s] %s could not speak (%s); speaking with Piper", session, voice.tts, err)
                    if record is not None:
                        record.stage("speech_failed", tts=voice.tts, on=target, error=str(err))
        # Piper, or -- with nothing to say -- the acknowledgement sound, which
        # stays on the satellite even when its responses are spoken elsewhere.
        async with self._model_lock:
            audio = await self.speech.say(text)
        if record is not None:
            record.stage("speech", text=text, seconds=round(len(audio) / BYTE_RATE, 3))
        await self._play(session, audio, paced)

    async def _speak(
        self, session: Session, satellite: Satellite, target: str, text: str, record: CallRecord | None
    ) -> None:
        """Speak with Home Assistant's TTS entity on ``target``. On the
        satellite's own media player, wait until it has said it: the device
        tells when the announcement starts and ends."""
        voice = satellite.voice
        assert voice.tts is not None
        data: dict = {"media_player_entity_id": target, "message": text, "cache": True}
        language = await self.voices.tts_language(voice.tts, voice.tts_language)
        if language:
            data["language"] = language
        if voice.tts_voice:
            data["options"] = {"voice": voice.tts_voice}
        own = target == satellite.device_media_player
        session.announcing.clear()
        session.announced.clear()
        started = time.perf_counter()
        await self.hass.call_service("tts", "speak", data, {"entity_id": voice.tts})
        heard: dict = {"started": None, "ended": None}
        if own:
            try:
                await asyncio.wait_for(session.announcing.wait(), ANNOUNCE_START_TIMEOUT)
                heard["started"] = round(time.perf_counter() - started, 3)
                await asyncio.wait_for(session.announced.wait(), ANNOUNCE_TIMEOUT)
                heard["ended"] = round(time.perf_counter() - started, 3)
            except TimeoutError:
                _LOGGER.warning("[%s] %s: the announcement did not %s in time", session, target,
                                "start" if heard["started"] is None else "end")
        if record is not None:
            record.stage("speech", text=text, tts=voice.tts, on=target, voice=voice.tts_voice, **heard)

    # ------------------------------------------------- try, replay (web UI)

    async def try_text(
        self, text: str, satellite_key: str | None, run_actions: bool = False, speak: bool = False
    ) -> CallRecord:
        """``text`` as a command said on this satellite: as if heard word for
        word (no speech recognition), or (``speak``) said by Piper and
        recognized -- the whole pipeline after the wake word."""
        if satellite_key:
            satellite = self.satellite(satellite_key)
        else:
            satellite = Satellite("", "web", skills=self.satellite_skills(None))
        record = self.calls.begin("try", satellite.as_dict())
        record.dry_run = not run_actions
        record.stage("typed", text=text, spoken=speak)
        assert self.engine is not None
        pcm = b""
        if speak:
            transcriber = await self._transcriber(satellite)
            async with self._model_lock:
                pcm = await self.speech.say(text) if text.strip() else b""
                decision = await asyncio.to_thread(
                    self.engine.recognize_command, _pcm_to_float(pcm), satellite, transcriber
                )
        else:
            transcriber = TypedText(text)
            async with self._model_lock:
                decision = await asyncio.to_thread(self.engine.recognize_text, text, satellite)
        record.stage("recognition", **decision.to_json(), stt=_stt_json(transcriber))
        speech, outcome, summary = await self._carry_out(
            decision, satellite, {"name": None, "score": None}, not run_actions, record
        )
        record.stage("speech", text=speech or "", **({"silent": True} if speech is None else {}))
        self.calls.finish(record, outcome, summary, {"command": pcm} if pcm else None)
        return record

    async def replay(self, call_id: str, run_actions: bool = False) -> CallRecord:
        """Run a saved call through today's commands, from its pre-roll."""
        old = self.calls.get(call_id)
        path = self.calls.audio_path(call_id, "call")
        if old is None or path is None:
            raise FileNotFoundError(f"No saved audio for call {call_id}")
        with wave.open(str(path), "rb") as wav:
            pcm = wav.readframes(wav.getnframes())
        preroll = next(
            (s.get("preroll") for s in old.stages if s.get("stage") == "call_start"), 1.5
        )
        satellite = self.satellite(str(old.satellite.get("id") or ""))
        record = self.calls.begin("replay", satellite.as_dict())
        record.dry_run = not run_actions
        record.stage("replay_of", call=call_id)
        heard = next(
            (s.get("wake_word") for s in old.stages if s.get("stage") == "call_start"), ""
        )
        wake_word = self.wake_words.resolve(str(heard or "")) or self.wake.wake_word
        call = Call(preroll_bytes=ms_to_bytes(preroll * 1000), wake_word=wake_word, record=record)
        call.audio = bytearray(pcm)
        record.marks["detection"] = preroll
        verdict = await self._verify_wake(f"replay {call_id}", call)
        if not verdict.accepted:
            self.calls.finish(record, "wake rejected", "wake word rejected", {"call": pcm})
            return record
        self._place_split(call, verdict)
        transcriber = await self._transcriber(satellite)
        async with self._model_lock:
            assert self.engine is not None
            decision, split = await asyncio.to_thread(
                self.engine.recognize_call,
                _pcm_to_float(pcm),
                call.wake_start,
                call.split_bytes / BYTE_RATE,
                satellite,
                transcriber,
                self.wake_words.phrases(call.wake_word),
            )
        record.stage("split", **split.to_json())
        record.marks["cut"] = split.cut
        if split.wake_end is not None:
            record.marks["wake_start"] = split.wake_start
            record.marks["wake_end"] = split.wake_end
        record.stage("recognition", **decision.to_json(), stt=_stt_json(transcriber))
        speaker = next(
            (
                {k: s.get(k) for k in ("name", "score")}
                for s in old.stages
                if s.get("stage") == "speaker"
            ),
            {"name": None, "score": None},
        )
        speech, outcome, summary = await self._carry_out(
            decision, satellite, speaker, not run_actions, record
        )
        record.stage("speech", text=speech or "", **({"silent": True} if speech is None else {}))
        self.calls.finish(record, outcome, summary, {"call": pcm})
        return record

    async def add_voice(self, call_id: str, name: str) -> dict[str, Any]:
        """A kept call's voice, into a speaker's profile (``name``: one that
        exists, or a new one): the wake word and the command from the
        capture speakers are identified on, as a call is."""
        if self.speakers is None:
            raise ConfigError("Speaker identification is turned off")
        speaker = _speaker_name(name)
        if speaker is None:
            raise ConfigError("Give the speaker a name")
        record = self.calls.get(call_id)
        if record is None:
            raise FileNotFoundError(f"No session {call_id}")
        aec = self.speakers.channel == "aec" and self.calls.audio_path(call_id, "call-aec") is not None
        path = self.calls.audio_path(call_id, "call-aec" if aec else "call")
        if path is None:
            raise FileNotFoundError("No audio was kept for this session")
        with wave.open(str(path), "rb") as wav:
            pcm = wav.readframes(wav.getnframes())
        # From 1.2 s before the end of the wake word, as _speaker_audio.
        split = record.marks.get("kaldi_split", record.marks.get("detection", 0.0))
        if aec:
            start = next((s for s in record.stages if s.get("stage") == "call_start"), {})
            preroll = float(start.get("preroll") or 0.0)
            split += float(start.get("aec_preroll") or preroll) - preroll
        utterance = await self.speakers.embed(pcm[ms_to_bytes(max(0.0, split - 1.2) * 1000) :])
        if utterance is None:
            raise ConfigError("Too little speech in this session to learn a voice from")
        speaker, profile = self.speakers.add(speaker, utterance)
        return {"name": speaker, "speech_s": round(utterance.speech_s, 2), **_profile_json(speaker, profile)}

    def speaker_list(self) -> list[dict[str, Any]]:
        if self.speakers is None:
            return []
        return [_profile_json(name, p) for name, p in sorted(self.speakers.profiles.speakers.items())]

    # ---------------------------------------------------------------- timers

    def _timers_changed(self) -> None:
        """Running timers changed somewhere: show them on the satellites' rings,
        and recognize their names from now on."""
        for session in list(self.sessions.values()):
            self._spawn(self._send_timer_progress(session))
        assert self.engine is not None
        names = self.integration.running_names()
        if names == self.engine.dynamic.get("running_timers"):
            return
        self.engine.dynamic["running_timers"] = names
        if self._timer_rebuild is None or self._timer_rebuild.done():
            self._timer_rebuild = asyncio.ensure_future(self._rebuild_for_timers())

    async def _rebuild_for_timers(self) -> None:
        await asyncio.sleep(TIMER_LIST_DEBOUNCE)
        if self.engine is not None and self.engine.compiled is not None:
            try:
                await self.apply(self.engine.compiled)
            except (ConfigError, ValueError) as err:
                _LOGGER.error("Could not rebuild with the running timers: %s", err)

    async def _send_timer_progress(self, session: Session, force: bool = False) -> None:
        """The timer that finishes soonest, for the satellite's LED ring; only
        when that changed (the device counts down by itself)."""
        if session.writer is None or not session.satellite:
            return
        shown = timers.progress(self.integration.running(session.satellite))
        last = session.timer_progress
        if last is not None and not force:
            # The device counts what it was sent down: compare where that is now.
            counted = float(last.get("remaining") or 0) - (time.monotonic() - session.timer_progress_at)
            if (
                last.get("shown") == shown.get("shown")
                and last.get("total") == shown.get("total")
                and abs(max(counted, 0.0) - float(shown.get("remaining") or 0)) < 1.0
            ):
                return
        session.timer_progress = dict(shown)
        session.timer_progress_at = time.monotonic()
        await self._event(session, "timer_progress", **shown)

    def _timer_finished(self, key: str, timer: dict) -> None:
        """Ring the satellite the timer was set on."""
        session = self.sessions.get(key)
        if session is None or session.writer is None:
            _LOGGER.warning("Timer %r finished on %s, which is not connected", timer.get("name"), key)
            return
        session.ringing_timers.append(timer)
        if session.ringing is None or session.ringing.done():
            session.ringing = asyncio.ensure_future(self._ring(session))

    async def _ring(self, session: Session) -> None:
        """Say which timer finished, then sound the alarm until someone says
        "stop" (or wakes the satellite), or it times out."""
        record = self.calls.begin("timer", self.satellite(session.satellite).as_dict())
        deadline = time.monotonic() + self.options.timer_ring_seconds
        stopped_by = "timeout"
        session.ring_stopped_by = ""
        try:
            # Not over someone's command.
            while session.call is not None and time.monotonic() < deadline:
                await asyncio.sleep(0.2)
            stop = self.wake_words.model(STOP)
            if stop is not None:
                await self._push_models(session, [stop], first_slot=len(session.wake_words) + 1)
            names = [t.get("name") for t in session.ringing_timers]
            record.stage("timer_ringing", timers=session.ringing_timers)
            await self._event(session, "timer_ringing", timers=len(names), name=names[-1] or "")
            described = [timers._describe(t, capital=True) for t in session.ringing_timers]  # pylint: disable=protected-access
            await self._say(session, " ".join(f"{d} is done." for d in described), paced=True)
            while time.monotonic() < deadline and session.writer is not None:
                await self._play(session, self.speech.alarm, paced=True)
        except asyncio.CancelledError:
            stopped_by = session.ring_stopped_by or "stop"
            raise
        finally:
            record.stage("timer_stopped", by=stopped_by)
            self.calls.finish(record, "timer rang out" if stopped_by == "timeout" else "timer stopped", ", ".join(
                str(t.get("name") or timers._spoken_duration(float(t.get("created_duration") or 0))) for t in session.ringing_timers  # pylint: disable=protected-access
            ))
            session.ringing_timers = []
            self._spawn(self._after_ring(session))

    async def _after_ring(self, session: Session) -> None:
        """Back to the wake word alone."""
        if session.writer is None:
            return
        await self._event(session, "timer_stopped")
        if self.wake_words.model(STOP) is not None:
            await self._push_wake_models(session)

    # ------------------------------------------------------------ speaker ID

    def _speaker_audio(self, session: Session, call: Call) -> bytes:
        """The wake word and the command, from the configured capture.

        Starts 1.2 s before the wake word ends: "okay nabu" plus the command is
        what was benchmarked. Not the whole pre-roll -- whatever was said before
        the wake word (a TV, someone else) would be scored too.
        """
        lead = ms_to_bytes(1200)
        assert self.speakers is not None
        if self.speakers.channel == "aec" and call.aec:
            # The two captures carry their own pre-rolls; line them up on the
            # detection, which ends both.
            split = call.split_bytes - call.preroll_bytes + (
                call.aec_preroll_bytes or call.preroll_bytes
            )
            return bytes(call.aec[max(0, split - lead) :])
        if self.speakers.channel == "aec":
            _LOGGER.debug("[%s] No AEC capture; identifying from AGC", session)
        return bytes(call.audio[max(0, call.split_bytes - lead) :])

    async def _identify(self, session: Session, call: Call) -> Identification | None:
        assert self.speakers is not None
        pcm = self._speaker_audio(session, call)
        try:
            ident = await self.speakers.identify(pcm)
        except Exception:  # never let speaker ID take the call down with it
            _LOGGER.exception("[%s] Speaker identification failed", session)
            ident = None

        if (
            ident is not None
            and ident.utterance is not None
            and self.speakers.channel == "aec"
            and call.aec
            and not session.warned_quiet_aec
        ):
            peak = int(np.abs(np.frombuffer(pcm, dtype="<i2").astype(np.int32)).max()) if pcm else 0
            if peak < 200:  # about -44 dBFS: unity gain on the AEC tap
                session.warned_quiet_aec = True
                _LOGGER.warning(
                    "[%s] The AEC capture peaks at %d/32768 -- too quiet to identify "
                    "speakers reliably. Give aec_microphone a gain_factor (16 in "
                    "voice-call-vpe.yaml) or run with --speaker-channel agc.",
                    session,
                    peak,
                )

        fields: dict[str, Any] = {"speaker": "unknown"}
        if ident is not None and ident.name is not None:
            fields = {"speaker": ident.name, "score": round(ident.score, 3)}
            fields.update(_rgb(self.speakers.profiles.color(ident.name)))
        if ident is None or ident.utterance is None:
            _LOGGER.info("[%s] Speaker: too little speech to identify", session)
        else:
            _LOGGER.info(
                "[%s] Speaker: %s (best %s %.3f, margin %.3f, %.1fs) %s",
                session,
                ident.name or "unknown",
                ident.best,
                ident.score,
                ident.margin,
                ident.utterance.speech_s,
                ident.scores,
            )
        if call.record is not None:
            call.record.stage(
                "speaker",
                name=ident.name if ident else None,
                score=round(float(ident.score), 4) if ident and ident.utterance else None,
                best=ident.best if ident else None,
                # No margin with one speaker enrolled: nobody is second.
                margin=round(float(ident.margin), 4) if ident and ident.utterance and math.isfinite(ident.margin) else None,
            )
        await self._event(session, "processing", **fields)
        return ident

    def _after_identify(self, session: Session, ident: Identification | None) -> None:
        """Learn from a confident call: its voice into the speaker's profile."""
        if self.speakers is None or ident is None:
            return
        if self.speakers.maybe_learn(ident):
            _LOGGER.info("[%s] Learned this call into %s's profile", session, ident.name)

    # ---------------------------------------------------------------- output

    async def _play(self, session: Session, pcm: bytes, paced: bool = False) -> None:
        """Stream audio and wait until the device has played it out.

        ``paced``: stay at most PLAY_LEAD ahead of the device's clock, for
        audio outside a call (a ringing timer). The device's buffer only
        pushes back once full, and its TCP window holds seconds more; a call
        that starts meanwhile (the "stop" word) would otherwise only hear
        the app's hang-up after every queued second has played."""
        # The previous playback's "drained" must not answer for this one.
        session.drained.clear()
        loop = asyncio.get_running_loop()
        started = loop.time()
        sent = 0
        for offset in range(0, len(pcm), MAX_PAYLOAD):
            chunk = pcm[offset : offset + MAX_PAYLOAD]
            if paced:
                ahead = sent / BYTE_RATE - (loop.time() - started)
                if ahead > PLAY_LEAD:
                    await asyncio.sleep(ahead - PLAY_LEAD)
            await self._send(session, Message.AUDIO_OUT, chunk)
            sent += len(chunk)
        await self._send(session, Message.AUDIO_OUT_END)
        try:
            await asyncio.wait_for(
                session.drained.wait(), timeout=self.options.drain_timeout
            )
        except TimeoutError:
            _LOGGER.warning("[%s] Timed out waiting for playback to drain", session)

    async def _transcriber(self, satellite: Satellite):
        """The satellite's STT entity for this command, or None: the TDT head."""
        if satellite.voice.stt is None or self.engine is None:
            return None
        return await self.voices.transcriber(
            satellite.voice.stt, self.engine.transcriber, satellite.voice.stt_language
        )

    async def _hang_up(self, session: Session, reason: str) -> None:
        _LOGGER.info("[%s] Hanging up: %s", session, reason)
        await self._send_json(session, Message.HANGUP, {"reason": reason})
        session.call = None
        self._live_call_ended(session)

    def _save(self, session: Session, call: Call, prefix: str = "") -> None:
        if self.options.save_dir is None:
            return
        # One directory per device. Several VPEs flashed from the same YAML all
        # report the same name, so the MAC suffix is what actually separates
        # them, and timestamps alone would interleave their calls.
        out_dir = self.options.save_dir / session.device_id
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = int(call.started)
        for name, pcm in (
            ("call", call.audio),
            ("call-aec", call.aec),
            ("command", call.command),
        ):
            if not pcm:
                continue
            path = out_dir / f"{prefix}{name}-{stamp}.wav"
            with wave.open(str(path), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(BYTES_PER_SAMPLE)
                wav.setframerate(SAMPLE_RATE)
                wav.writeframes(pcm)
        _LOGGER.info(
            "Saved %s/{call,call-aec,command}-%d.wav", out_dir, stamp
        )


def _profile_json(name: str, profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "color": profile.get("color"),
        "seconds": round(float(profile.get("speech_s") or 0.0), 1),
        "windows": profile.get("n_windows"),
        "learned": profile.get("learned", 0),
    }


def _speaker_name(text: Any) -> str | None:
    name = re.sub(r"[^\w' -]+", "", str(text or "")).strip()
    return " ".join(word.capitalize() for word in name.split()) or None


def _speaker_json(ident: Identification | None) -> dict[str, Any]:
    if ident is None or ident.name is None:
        return {"name": None, "score": None}
    return {"name": ident.name, "score": round(float(ident.score), 4)}
