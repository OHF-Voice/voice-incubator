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
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import numpy as np

from adapt_ctc.config import ConfigError
from adapt_ctc.runtime import release_memory

from . import actions, alarms, languages, people, skills, timers, tones
from .duplex import Duplex
from .skills import skill_response
from .agent import KEEP_SECONDS, Agent
from .calls import CallLog, CallRecord
from .commands import VERBOSITY
from .discover import Device, Discovery
from .endpoint import VADS, WAKE_SECONDS, EndOfSpeech, Endpointer, micro_available, vad_gain, wake_speech
from .endpoint import parse as parse_end_of_speech
from .fallback import (
    DEFAULT_FALLBACK_MODEL,
    GLINER_MODELS,
    GLINER_THRESHOLD,
    Fallback,
    GlinerPicker,
    chosen,
    needs_model,
)
from .engine import Decision, Engine, Satellite, model_note
from .hass import HassError, HomeAssistant
from .integration import Integration
from .music import Music
from .voices import BUILTIN, HassTranscriber, TypedText, Voices, choose
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
from .enrollment import MIN_SECONDS as ENROLL_MIN_SECONDS
from .enrollment import Enrollment
from .speaker import PALETTE, Identification, SpeakerId
from .store import Compiled, Store
from .tts import Speech, sentences_of
from .wake import VerifyResult, WakeVerifier
from .wake import ConfigError as WakeConfigError
from .wake_ratio import DEFAULT_THRESHOLD as DEFAULT_RATIO, WakeRatio
from .wake_recordings import TAKE_SCORE, WakeRecording, WakeTakes
from .wake_recordings import summary as wake_summary
from .wake_training import WakeTraining, phrase_of, word_id
from .wake_words import DEFAULT_MODEL_SLOTS, RATIO_THRESHOLD, RESERVED_SLOTS, SPECIAL, STOP, WakeWords

_LOGGER = logging.getLogger(__name__)

# A connection that survives this long is treated as healthy, which resets the
# reconnect backoff.
STABLE_CONNECTION_SECONDS = 30.0

# Running timers' names are recognized from a list rebuilt when they change;
# a burst of changes makes one rebuild.
TIMER_LIST_DEBOUNCE = 0.3

# The microWakeWord model the satellite listens with while a timer rings.
STOP_MODEL = STOP

# Outside a call, how far audio may run ahead of the device playing it: enough
# to ride out network jitter, little enough that a call cutting in is heard at
# once.
PLAY_LEAD = 0.3

# The thinking sound (settings.json ``thinking_sound``, on unless false): played
# when no reply has begun this long after the command ended (``thinking_delay``,
# within THINKING_DELAYS), and none for a quicker one. It runs only a little
# ahead of the device, so that once the reply is ready it stops short, faded
# out.
THINKING_DELAY = 1.0
THINKING_DELAYS = (0.0, 10.0)
THINKING_LEAD = 0.2
THINKING_FADE = 0.03

# Home Assistant TTS on the satellite: how long until its media player starts
# the announcement (synthesis, then fetching the URL), and plays it out.
ANNOUNCE_START_TIMEOUT = 10.0
ANNOUNCE_TIMEOUT = 120.0

# An announcement waits this long for a satellite busy with a call, a ringing
# timer or the intercom, then is dropped there.
ANNOUNCE_WAIT = 60.0

# The intercom: how long the call that asked for it may take to end; how much
# of one satellite's microphone may wait to reach the other (beyond it, the
# audio is dropped, not queued: what is said must arrive now or not at all);
# and how long it lasts, unless a button ends it first (a microphone left open
# in an empty room).
INTERCOM_START_WAIT = 30.0
INTERCOM_BACKLOG = 64 * 1024
INTERCOM_LIMIT = 30 * 60.0

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


SPEAKER_END_MARGIN = 0.3
"""What is recognized runs this far past the speaker's last word, when someone
else's speech ended the command (Silero's segments end close on the speech)."""


def _seconds_bytes(seconds: float) -> int:
    """Seconds as a whole number of samples' bytes (negative before)."""
    return int(round(seconds * BYTE_RATE / BYTES_PER_SAMPLE)) * BYTES_PER_SAMPLE


def _seconds(value: float | None) -> float | None:
    return None if value is None else round(value, 3)


def _cancel_stt(call: Call | None) -> None:
    """A call ended before its command did: nothing for its STT stream."""
    if call is not None and call.stt is not None and not call.stt.closed:
        call.stt.cancel()


def _stt_json(transcriber) -> dict | None:
    return transcriber.to_json() if hasattr(transcriber, "to_json") else None


def _pcm_to_float(pcm: bytes) -> np.ndarray:
    return np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0


AEC_LEAD_SAMPLES = 905
"""How far the AEC capture runs ahead of the AGC one: the pipeline between
the XMOS's two taps (README "Two captures per call", 56.5 ms)."""
STT_PEAK = 10 ** (-3 / 20)
STT_MAX_GAIN = 10 ** (30 / 20)


NULL_CALL_SPEECH = 0.1
NULL_AEC_SPEECH = 0.25
"""Silero's share of the wake word heard as speech, under which the call
capture has lost the talker (learn-tv's null on them) and from which the AEC
capture still has them: ordinary calls hear 0.3-0.8 in both."""


def _aec_shift(call: Call) -> int:
    """Bytes to add to a position in the AGC capture for the same moment in
    the AEC one: each pre-roll ends at the detection, less the AEC tap's lead."""
    return (call.aec_preroll_bytes or call.preroll_bytes) - call.preroll_bytes - call.aec_lead * 2


def _aec_lined_up(call: Call, start: int, end: int) -> bytes:
    """The AEC capture from ``start`` to ``end`` of the AGC capture's timeline
    (silence where it has nothing)."""
    lo, hi = (start + _aec_shift(call)) & ~1, (end + _aec_shift(call)) & ~1
    out = bytearray(max(0, hi - lo))
    have_lo, have_hi = max(lo, 0), min(hi, len(call.aec) & ~1)
    if have_hi > have_lo:
        out[have_lo - lo : have_hi - lo] = call.aec[have_lo:have_hi]
    return bytes(out)


def _heard(call: Call, start: int, end: int) -> bytes:
    """The call from ``start`` to ``end`` as heard: the call capture, or in
    the null the AEC one lined up with it."""
    return _aec_lined_up(call, start, end) if call.in_null else bytes(call.audio[start:end])


def _aec_since(call: Call) -> bytes:
    """In the null: what has come of the AEC capture since ``endpoint_pos``
    (on the AGC capture's timeline), up to what the AGC capture has."""
    end = min(len(call.audio), len(call.aec) - _aec_shift(call)) & ~1
    if end <= call.endpoint_pos:
        return b""
    pcm = _aec_lined_up(call, call.endpoint_pos, end)
    call.endpoint_pos = end
    return pcm


def stt_audio_for(
    agc: bytes,
    aec: bytes,
    start: int,
    preroll: int,
    aec_preroll: int,
    capture: str = "agc",
    boost: bool = False,
    lead: int = AEC_LEAD_SAMPLES,
) -> Any:
    """What an STT entity hears of a call: from ``start`` (samples into the
    AGC capture: Kaldi's split, where it ended the wake phrase) to the end --
    not the engine's cut, which keeps the end of "Nabu" more often (README
    "Always run") -- of the AGC capture, or (``capture`` "aec") the AEC one.
    The two are lined up on the detection, which ends both pre-rolls (each
    its own length), less the 56.5 ms the AEC tap runs ahead of AGC's: AGC
    is the later stage in the XMOS pipeline (``lead``, in samples: what the
    satellite says, for captures that aren't those two taps). As captured, or with ``boost``
    raised to a -3 dBFS peak, by at most 30 dB (the tap is recorded ~25 dB
    below AGC). Without an AEC capture, the AGC one."""

    def pick(command: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
        heard = _pcm_to_float(agc[start * 2 :])
        if not len(heard):
            return command, {"channel": "agc", "why": "nothing after Kaldi's split"}
        info: dict[str, Any] = {"channel": "agc", "from": round(start / SAMPLE_RATE, 3)}
        if capture == "agc":
            return heard, info
        if not aec:
            return heard, info | {"why": "no AEC capture"}
        at = max(0, start - preroll + aec_preroll - lead)
        out = _pcm_to_float(aec[at * 2 : (at + len(heard)) * 2])
        if len(out) < len(heard) // 2:
            return heard, info | {"why": "the AEC capture is short"}
        info["channel"] = "aec"
        if not boost:
            return out, info
        peak = float(np.abs(out).max()) if len(out) else 0.0
        gain = min(STT_MAX_GAIN, STT_PEAK / peak) if peak > 0 else 1.0
        return out * gain, info | {"gain_db": round(20 * math.log10(gain), 1)}

    return pick


# The transducer that writes example sentences, free text and hints: the
# language's own (its pack's: for English, the TDT head beside the CTC model,
# parakeet-tdt_ctc-110m, int8 preferred), or NVIDIA's multilingual
# parakeet-tdt-0.6b-v3 (25 European languages; it finds the language itself),
# as sherpa-onnx releases it. ``en`` in settings.json, from before languages
# had packs, is the language's own.
TDT_MODELS = ("own", "multilingual")
DEFAULT_TDT = "own"
TDT_ALIASES = {"en": "own"}
DEFAULT_TOP_K = 8
"""How many commands GLiNER chooses among: the closest this many in meaning,
chosen or not (it needs others to tell the chosen ones from)."""


def tdt_models(root: Path) -> dict[str, Path]:
    """Each transducer's directory: the language's own, and the multilingual."""
    own = languages.current().transducer_dir(root)
    return {**({"own": own} if own is not None else {}), "multilingual": root / "models/parakeet-tdt-v3-int8"}


def tdt_choice(value: Any) -> str:
    """A ``tdt_model`` setting as one of TDT_MODELS (``en`` is ``own``)."""
    value = TDT_ALIASES.get(str(value or DEFAULT_TDT), str(value or DEFAULT_TDT))
    return value if value in TDT_MODELS else DEFAULT_TDT


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
    wake_models: tuple[str, ...] = ("okay_nabu",)
    """The wake words of a satellite that has none picked, when none are picked
    for every satellite either, by id. Nothing is compiled into the firmware:
    the models are pushed when the device connects."""
    wake_words_dir: Path | None = None
    """Custom wake words: microWakeWord manifests and models (default:
    wake_words/ beside models/)."""
    wake_training_dirs: tuple[Path, ...] = ()
    """Where the wake word training kit may be (scripts/make_wake_training_kit.py),
    before models/wake-training."""
    wake_trainer_python: str | None = None
    """A Python with TensorFlow and microWakeWord, to train wake words with
    (default: the app's own)."""
    micro_wake_word: Path | None = None
    """microWakeWord's source, when the trainer's Python doesn't have it installed."""
    wake_training_clips: int | None = None
    """Piper clips of a wake word being trained (default POSITIVE_CLIPS): fewer
    to try the training quickly."""
    wake_training_steps: int | None = None
    """Its training steps (default 10,000), down the same ladder."""
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
    its own: okay_nabu's is DEFAULT_WAKE_CUTOFF), never a custom word's, which
    runs at its manifest's.

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


class Thinking:
    """The thinking sound while a call's reply is being made: from
    THINKING_DELAY after the command ended, unless the reply is on its way by
    then. It is left open, so a reply played on the device follows it in the
    same stream; ``stop(close=True)`` ends the stream (and waits until it has
    played out) for a reply spoken some other way, or none."""

    def __init__(self, app: VoiceCallApp, session: Session, sound: bytes, delay: float = THINKING_DELAY) -> None:
        self.sent = 0
        """Bytes of it the device was sent."""
        self._app, self._session, self._sound = app, session, sound
        self._stop = asyncio.Event()
        self._task = asyncio.ensure_future(self._run(delay))

    async def _run(self, delay: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), delay)
            return  # the reply came first
        except TimeoutError:
            pass
        session, sound = self._session, self._sound
        session.drained.clear()  # an earlier "drained" must not answer for this
        loop = asyncio.get_running_loop()
        started = loop.time()
        chunk = MAX_PAYLOAD // 2
        while self.sent < len(sound):
            ahead = self.sent / BYTE_RATE - (loop.time() - started)
            if ahead > THINKING_LEAD:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), ahead - THINKING_LEAD)
            if self._stop.is_set():
                # Faded out from where it got to, rather than cut.
                tail = np.frombuffer(sound[self.sent : self.sent + int(THINKING_FADE * BYTE_RATE) // 2 * 2], "<i2")
                faded = (tail * np.linspace(1.0, 0.0, len(tail))).astype("<i2").tobytes()
                if faded:
                    await self._app._send(session, Message.AUDIO_OUT, faded)  # pylint: disable=protected-access
                    self.sent += len(faded)
                return
            await self._app._send(session, Message.AUDIO_OUT, sound[self.sent : self.sent + chunk])  # pylint: disable=protected-access
            self.sent += len(sound[self.sent : self.sent + chunk])

    async def stop(self, close: bool) -> None:
        """No more of it. ``close``: and end its stream, once played out;
        else the caller's playback goes on in the stream, and ends it."""
        self._stop.set()
        with contextlib.suppress(Exception):
            await self._task
        sent, self.sent = self.sent, 0
        if close and sent:
            await self._app._send(self._session, Message.AUDIO_OUT_END)  # pylint: disable=protected-access
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._session.drained.wait(), self._app.options.drain_timeout)


@dataclass
class Call:
    """Audio and state for one wake-word-to-hangup session."""

    preroll_bytes: int
    wake_word: str
    thinking: Thinking | None = None
    """The thinking sound, from when the command ended until the reply."""
    responding: asyncio.Task | None = None
    """Recognizing and answering it, from when the command ended: cancelled
    when the device ends the call (its button)."""
    started: float = field(default_factory=time.monotonic)
    audio: bytearray = field(default_factory=bytearray)
    """The AGC capture. The command is split out of this one."""
    aec: bytearray = field(default_factory=bytearray)
    """The same call tapped earlier in the XMOS pipeline: recorded, and what the
    speaker is identified from."""
    aec_preroll_bytes: int = 0
    """Where the detection falls in ``aec``; its pre-roll can differ from AGC's."""
    aec_lead: int = AEC_LEAD_SAMPLES
    """How far ``aec`` runs ahead of ``audio``, in samples: the XMOS's AEC tap
    leads its AGC tap, unless the satellite says otherwise (HELLO
    ``aec_lead_samples``; learn-tv's two captures are delayed alike)."""
    command: bytearray = field(default_factory=bytearray)
    last_audio: float | None = None
    """When the AGC capture last arrived (monotonic)."""
    verified: bool = False
    finished: bool = False
    endpointer: Endpointer | None = None
    split_bytes: int = 0
    in_null: bool = False
    """The talker stood in learn-tv's null: the call capture lost their voice
    and the AEC one is heard instead (endpointing, recognition, STT)."""
    endpoint_pos: int = 0
    """In the null: how far into ``aec`` the command has been taken."""
    interrupted_ring: bool = False
    """The call stopped a ringing timer: if it says nothing else, it was that."""
    wake_start: float | None = None
    """Seconds from the start of the call to the start of the wake word, as
    Kaldi placed it."""
    record: CallRecord | None = None
    live_bytes: int = 0
    """How much of the AGC capture someone listening live has been given (or
    skipped: the pre-roll, which they heard as it happened)."""
    stt: Any = None
    """The STT entity hearing the command as it is said (voices.StreamingTranscriber),
    when it always runs."""
    stt_gain: float = 1.0
    """What the AEC capture is boosted by as it is streamed."""
    speaker_ref: Any = None
    """The identified speaker's profile, once the command's first segment is in
    (what later segments are checked against); False when nobody was."""
    speaker_watch: dict[str, Any] | None = None
    """Who that was, and the score."""
    speaker_checking: bool = False
    """A check of a segment is running."""
    speaker_end: float | None = None
    """Where the speaker's last word ended, in seconds from the split, when
    someone else's speech ended the command: what is recognized stops there."""


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
    """Someone listening live (a satellite's page): each gets the microphone as it
    comes, between calls (the device streams it while monitored) and in them."""
    live_call: bool = False
    """Listeners were told a call started, and not yet that it ended."""
    monitoring: bool = False
    """What the device was last told (MONITOR): someone listens live, or a
    speaker is enrolled from it."""
    monitoring_intercom: bool = False
    """And whether it was told that is the intercom."""
    intercom: "Intercom | None" = None
    """Joined to another satellite: each one's microphone on the other's speaker."""
    announcements: asyncio.Lock = field(default_factory=asyncio.Lock)
    """One announcement at a time."""
    announcing_now: asyncio.Task | None = None
    """The announcement being said: a call starting cuts it off."""

    def __str__(self) -> str:
        return self.device_id


@dataclass
class Intercom:
    """Two satellites joined, each one's microphone on the other's speaker,
    until the button on either is pressed."""

    a: str
    b: str
    """Who asked, and who was connected to (satellite keys)."""
    started: float = field(default_factory=time.monotonic)
    record: CallRecord | None = None
    limit: asyncio.Task | None = None
    ended: bool = False
    duplex: Duplex = field(default_factory=Duplex)
    """What each sends the other: no squeal (voice_call/duplex.py)."""

    def peer(self, key: str) -> str:
        return self.b if key == self.a else self.a


class VoiceCallApp:
    def __init__(self, options: Options) -> None:
        self.options = options
        self.language = languages.use(options.language)
        """What it hears and says (voice_call/languages): one per app."""
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
        piper_dir = root / "models/piper"
        self.wake_training = WakeTraining(
            self.store.data_dir / "wake_training",
            self.wake_words.custom_dir,
            [*options.wake_training_dirs, root / "models/wake-training"],
            piper_dir / self.language.wake_voice if self.language.wake_voice else None,
            self.language.code,
            trainer_python=options.wake_trainer_python,
            micro_wake_word=options.micro_wake_word,
            check_model=self._trained_model_error,
            on_installed=self._wake_word_trained,
            exists=lambda word: self.wake_words.get(word) is not None or word in SPECIAL,
            retrainable=self.trained_here,
            builtin=lambda word: (found := self.wake_words.get(word)) is not None and not found.custom,
        )
        """Training a new wake word from a phrase (the Wake words page)."""
        if options.wake_training_clips:
            self.wake_training.positive_clips = options.wake_training_clips
        if options.wake_training_steps:
            rungs = len(self.wake_training.training_steps)
            self.wake_training.training_steps = [max(1, options.wake_training_steps // rungs)] * rungs
        self._wake_lock = asyncio.Lock()
        """One wake word change at a time: each may rebuild a verifier."""
        # The fp32 encoder is 862 MB resident, which is most of a 2 GB Pi.
        # Prefer the int8 build when it has been produced.
        self._ctc_dir = self.language.ctc_dir(root)
        self._tdt_dirs = tdt_models(root)
        self.tdt_status: str | None = None
        """What became of the last change of TDT model, for the UI."""
        self._model_status: str | None = None
        """Why the model step has no model, when it is not the fallback's to say."""
        self.engine: Engine | None = None
        # A second opinion on the two Kaldi tests that are about "is this the
        # wake word or just speech". Its `confusable` test is left alone.
        self.wake_ratios: dict[str, WakeRatio] = {}
        """The second opinion, per verified wake word (built at start)."""
        self.speech = Speech(
            root / "models/piper" / self.language.piper,
            root / "sounds/acknowledge.mp3",
            root / "sounds/timer_finished.flac",
            root / "sounds/thinking.flac",
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
        self.alarms = (
            alarms.AlarmCalendar(self.hass, self._calendar_due) if languages.current().calendar is not None else None
        )
        """Alarms and reminders on a Local Calendar, in a language that reads them."""
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
        self.enrollment: Enrollment | None = None
        """A speaker being enrolled from a satellite (the Speakers page)."""
        self.wake_recording: WakeRecording | None = None
        """A speaker saying a wake phrase at a satellite (the Speakers page)."""
        self.wake_takes = (
            WakeTakes(self.speakers.profiles.path.parent / "wake_recordings", self.speakers.profiles.speakers)
            if self.speakers is not None else None
        )
        """Their takes, kept with their voices."""
        """Connected satellites, by satellite key."""
        self._discovery: Discovery | None = None
        self._web = None
        # Recognition, Piper and the wake verifier's retry share the CPU; two
        # satellites waking at once is rare, so one lock over them is enough.
        self._model_lock = asyncio.Lock()
        self._build_lock = asyncio.Lock()
        self._fallback_lock = asyncio.Lock()
        """One fallback change at a time: a second waits, and reuses the model."""

    # ------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        missing = self.language.missing(self.root)
        if missing:
            message = f"{self.language.name} needs its models, which aren't here: {', '.join(missing)}"
            _LOGGER.error("%s", message)
            raise SystemExit(message)
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
            self.store.data_dir / "embeddings.npz",  # kept for the next start
        )
        self.engine.entity_area = self._entity_area
        self.configure_skills()
        await self.reload_commands()
        self._spawn(self.configure_fallback())
        self.configure_agent()
        await self._ensure_ratios()
        await self.speech.start()
        if self.speakers is not None:
            await self.speakers.start()
        release_memory()  # what loading the models needed only meanwhile

        from .web import start_web  # pylint: disable=import-outside-toplevel

        self._web = await start_web(self)

    async def stop(self) -> None:
        await self.wake_training.stop()
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
        """Load the commands and recognize them. Commands that do not
        compile keep the previous ones (and say why in the UI)."""
        try:
            compiled = self.store.load()
        except (ConfigError, ValueError, OSError) as err:
            self.store_error = str(err)
            _LOGGER.error("%s: %s; keeping the previous commands", self.store.commands_path.name, err)
            return None
        await self.apply(compiled)
        return compiled

    async def apply(self, compiled: Compiled) -> None:
        """Recognize these commands from now on. Raises ConfigError when
        adapt-ctc cannot build them."""
        assert self.engine is not None
        async with self._build_lock:
            await asyncio.to_thread(self.engine.build, compiled)
            release_memory()
        self.store_error = None
        fallback = self.engine.fallback
        if fallback is not None:
            if fallback.model is None and self._model_wanted():
                self._spawn(self.configure_fallback())  # the first command chosen for it
            else:
                fallback.prepare(self.engine)  # new commands, new arguments

    # ------------------------------------------------------------ fallback

    def fallback_settings(self) -> dict[str, Any]:
        """``settings.json``: when nothing matched, run the closest command by
        meaning (``fallback_meaning``), and let GLiNER (``fallback_llm``;
        ``fallback_model``) pick one among the closest ``fallback_top_k`` and
        hear what it needs, sure to at least ``fallback_threshold``. Both on
        unless turned off; each only for the commands chosen for it
        (``fallback: [meaning, llm]``)."""
        settings = self.store.settings
        model = str(settings.get("fallback_model") or DEFAULT_FALLBACK_MODEL)
        threshold = settings.get("fallback_threshold")
        return {
            "meaning": bool(settings.get("fallback_meaning", True)),
            "llm": bool(settings.get("fallback_llm", True)),
            "model": model if model in GLINER_MODELS else DEFAULT_FALLBACK_MODEL,
            "top_k": int(settings.get("fallback_top_k") or DEFAULT_TOP_K),
            "threshold": float(threshold) if threshold is not None else GLINER_THRESHOLD,
        }

    def _model_wanted(self) -> bool:
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
            status = self._model_status or (fallback.status if fallback is not None else "off")
        models = {
            m: {"name": i.name, "mb": i.mb, "licence": i.licence,
                "installed": GlinerPicker.find_model(m, self.root) is not None}
            for m, i in GLINER_MODELS.items()
        }
        return {
            **settings,
            "models": models,
            "meaning_available": bool(self.engine and self.engine.embedder),
            "meaning_commands": len(chosen(compiled, "meaning")) if compiled is not None else 0,
            "llm_commands": len(llm_commands),
            "llm_status": status,
        }

    async def configure_fallback(self) -> None:
        """Apply the fallback settings: GLiNER is loaded from models/ in the
        background, once some command is chosen for it."""
        if self.engine is None:
            return
        async with self._fallback_lock:
            await self._configure_fallback()
            release_memory()

    async def _configure_fallback(self) -> None:
        assert self.engine is not None
        wanted = self.fallback_settings()
        current = self.engine.fallback
        model = current.model if current is not None else None
        self._model_status = None
        if not self._model_wanted() or (model is not None and model.model != wanted["model"]):
            model = None
        if model is None and self._model_wanted():
            model = self._fallback_model(wanted["model"])
        if model is not None:
            model.threshold = wanted["threshold"]
        fallback = Fallback(wanted["meaning"], model, wanted["top_k"])
        if model is None:
            # Why it won't run, for a command chosen for it.
            fallback.status = self._model_status or ("turned off on the Settings page" if not wanted["llm"] else "off")
        self.engine.fallback = fallback
        fallback.prepare(self.engine)

    def _fallback_model(self, model: str) -> GlinerPicker | None:
        """GLiNER from models/, not loaded yet; None (and why, in
        ``_model_status``) when it isn't there, or doesn't know the language."""
        if not self.language.gliner:
            self._model_status = f"GLiNER doesn't understand {self.language.name}"
            return None
        path = GlinerPicker.find_model(model, self.root)
        if path is None:
            self._model_status = f"not found: models/{GLINER_MODELS[model].directory} (scripts/export_gliner.py)"
            return None
        return GlinerPicker(path, n_threads=self.options.threads, model=model)

    # ------------------------------------------------- devices, responses, agent

    def _entity_area(self, entity_id: str) -> str | None:
        """A device's area in Home Assistant (its own, else its device's)."""
        catalog = self.hass.catalog
        entity = catalog.entities.get(entity_id) if catalog is not None else None
        return entity.area_id if entity is not None else None

    def end_of_speech_base(self) -> EndOfSpeech:
        """The defaults under the Settings page's: the options
        ``silence_seconds`` (the app's option, ``--silence``) and
        ``max_command_seconds``."""
        return EndOfSpeech(silence_seconds=self.options.silence_seconds, max_seconds=self.options.max_command_seconds)

    def end_of_speech(self) -> EndOfSpeech:
        """``settings.json`` ``end_of_speech`` (the Settings page) over
        ``end_of_speech_base``."""
        base = self.end_of_speech_base()
        try:
            return parse_end_of_speech(self.store.settings.get("end_of_speech"), base)
        except ValueError as err:
            _LOGGER.warning("Ignoring end_of_speech in settings.json: %s", err)
            return base

    def end_of_speech_status(self) -> dict[str, Any]:
        """For the Settings page: what is used, what is set there, and the
        defaults under it."""
        return {
            "used": self.end_of_speech().to_json(),
            "set": dict(self.store.settings.get("end_of_speech") or {}),
            "defaults": self.end_of_speech_base().to_json(),
            "vads": {key: {"name": name, "installed": key == "silero" or micro_available()} for key, name in VADS.items()},
            # For "End when someone else talks": the voices to tell them apart by
            # (None: speaker identification is off).
            "voices": len(self.speakers.profiles.speakers) if self.speakers is not None else None,
        }

    def beep_elsewhere(self) -> str:
        """``settings.json`` ``beep_elsewhere``: what a command that beeps
        says when it acted out of the satellite's area (actions.BEEP_ELSEWHERE
        unless set)."""
        value = self.store.settings.get("beep_elsewhere")
        return value if value in VERBOSITY[1:] else actions.BEEP_ELSEWHERE

    def agent_settings(self) -> dict[str, Any]:
        """``settings.json``: the Home Assistant conversation agent that gets
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

    def agent_status(self) -> dict[str, Any]:
        return {**self.agent_settings(), "conversations": self.agent.status()}

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
            "skills": self.satellite_skills(key),
        }
        if link is not None and link.device_id:
            area = self.hass.catalog.device_areas.get(link.device_id)
            info = self.hass.catalog.area_info(area)
            return Satellite(key, name, **info, device_id=link.device_id, timer_list=link.timer_list, **extra)
        return Satellite(key, name, **self.hass.catalog.area_info(stored.get("area")), **extra)

    # ----------------------------------------------------------- TDT model

    def tdt_dir(self, choice: str | None = None) -> Path | None:
        """The transducer's directory: the one picked (settings.json
        ``tdt_model``), else the language's own. None with ``--no-tdt``."""
        if not self.options.tdt:
            return None
        choice = tdt_choice(choice or self.store.settings.get("tdt_model"))
        return self._tdt_dirs.get(choice) or self._tdt_dirs.get(DEFAULT_TDT)

    def tdt_choice(self) -> str:
        """The transducer picked: one of TDT_MODELS."""
        return tdt_choice(self.store.settings.get("tdt_model"))

    def tdt_models(self) -> list[dict[str, Any]]:
        from adapt_ctc.tdt import has_model  # pylint: disable=import-outside-toplevel

        current = self.engine.tdt_dir if self.engine is not None else None
        about = {
            "own": (self.language.name, self.language.transducer_about),
            "multilingual": ("Multilingual (25 European languages)", "parakeet-tdt-0.6b-v3, 600M parameters"),
        }
        out = []
        for model_id in TDT_MODELS:
            name, detail = about[model_id]
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

    def configure_skills(self) -> None:
        """The skills' settings, as their pages set them
        (``settings.json`` ``skill_settings``)."""
        if self.engine is not None:
            self.engine.skill_settings = skills.configure(self.store.settings.get("skill_settings"))

    # --------------------------------------------------------------- people

    def voice_names(self) -> list[str]:
        """Everyone with an enrolled voice."""
        return list(self.speakers.profiles.speakers) if self.speakers is not None else []

    def people_names(self) -> list[str]:
        """Everyone: with things of their own, a voice, or both."""
        return people.names(self.store.people, self.voice_names())

    def wake_owner(self, word_id: str | None) -> str | None:
        """Whose wake word it is (their page), if anyone's."""
        return people.owner(self.store.people, word_id)

    def identifies(self) -> bool:
        """Whether voices are identified: someone has one enrolled."""
        return self.speakers is not None and bool(self.voice_names())

    def speaking(self, ident: Identification | None = None, wake_word: str | None = None) -> people.Speaker:
        """Who is speaking (people.speaking): their voice, else whose wake
        word it was, else the only person -- unless a voice was heard and
        was nobody's."""
        named = ident is not None and ident.name is not None
        return people.speaking(
            self.store.people, self.voice_names(),
            ident.name if named else None, round(float(ident.score), 4) if named else None,
            self.wake_owner(wake_word),
            refused=ident is not None and ident.utterance is not None and not named,
        )

    def voice_for(self, satellite: Satellite, who: people.Speaker) -> Satellite:
        """The satellite as replies to them are said: in their voice, when
        they have one of their own."""
        if not who.own.get("tts"):
            return satellite
        tts = None if who.own["tts"] == BUILTIN else who.own["tts"]
        voice = replace(
            satellite.voice,
            tts=tts,
            tts_language=who.own.get("tts_language") if tts else None,
            tts_voice=who.own.get("tts_voice") if tts else None,
            speak_on=satellite.voice.speak_on if tts else None,
        )
        return replace(satellite, voice=voice)

    def put_person(self, name: str, own: dict[str, Any], new_name: str | None = None) -> str:
        """``name``'s things (all of them, as the People page has them), and
        maybe a new name. A new person when nobody has the name."""
        key = people.find(self.store.people, name) or people.find(dict.fromkeys(self.voice_names()), name)
        if key is None:
            key = people.person_name(name)
            if key is None:
                raise ConfigError("Give the person a name")
        try:
            things = people.clean(own)
        except ValueError as err:
            raise ConfigError(str(err)) from err
        self.store.people.pop(people.find(self.store.people, key) or key, None)
        self.store.people[key] = things
        self.store.save_people()
        if new_name is not None and new_name != key:
            renamed = people.person_name(new_name)
            if renamed is None:
                raise ConfigError("Give the person a name")
            if people.find(self.store.people, renamed) not in (None, key):
                raise ConfigError(f"There is already someone called {renamed}")
            if self.speakers is not None and self.speakers.profiles.find(key) is not None:
                self.update_speaker(key, new_name=renamed)
            else:
                self._renamed(key, renamed)
            key = renamed
        return key

    def delete_person(self, name: str) -> None:
        """Forget someone: their things (their wake words among them) and
        their voice."""
        found = people.find(self.store.people, name)
        voiced = self.speakers is not None and self.speakers.profiles.find(name) is not None
        if found is None and not voiced:
            raise FileNotFoundError(f"Nobody called {name}")
        if voiced:
            self.delete_speaker(name)
        if found is not None:
            del self.store.people[found]
            self.store.save_people()

    def _renamed(self, old: str, new: str) -> None:
        """Someone's things follow them to a new name."""
        found = people.find(self.store.people, old)
        if found is not None:
            self.store.people[new] = self.store.people.pop(found)
            self.store.save_people()

    def satellite_skills(self, key: str | None) -> frozenset[str]:
        """The skills that answer on a satellite (voice_call/skills/). No
        satellite: those on every one."""
        return skills.enabled(self.store.settings.get("skills"), key)

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
        return RATIO_THRESHOLD.get(word_id) or RATIO_THRESHOLD.get(self.wake_words.trained_from(word_id) or "", DEFAULT_RATIO)

    def ratio_threshold(self, word_id: str) -> float:
        tuned = (self.store.wake_tuning.get(word_id) or {}).get("second_opinion")
        return float(tuned) if tuned is not None else self.default_ratio(word_id)

    async def _ensure_ratios(self) -> None:
        """The second opinion for each verified wake word, at its threshold
        (none with ``--wake-ratio`` below 0, or for a language it is not
        calibrated for)."""
        if self.engine is None or (self.options.wake_ratio is not None and self.options.wake_ratio < 0):
            return
        if not self.language.wake_ratio:
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
        """The satellite's wake words, as many as fit beside "stop" in the
        device's model slots. No VAD gate: microWakeWord's VAD dropped ~2% of
        wake words in the TV study and stopped no false accepts (README "No VAD
        gate"), and the host verifies every wake word anyway.

        The device has no TFLite interpreter and no flatbuffer parser, so each
        graph is walked here and sent as a flat op table plus weights.
        """
        slots = int(session.hello.get("model_slots") or DEFAULT_MODEL_SLOTS)
        room = max(1, slots - RESERVED_SLOTS)
        wanted = self.satellite_wake_words(session.satellite)
        models = [m for m in (self.wake_words.model(w) for w in wanted) if m is not None]
        if not session.hello.get("max_pool"):
            # A word trained here pools its last layer, which older firmware's
            # executor refuses.
            for model in [m for m in models if m.max_pool]:
                _LOGGER.warning("[%s] %s needs newer firmware (max pooling): update it", session, model.model_id)
            models = [m for m in models if not m.max_pool]
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
        return models

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
                "[%s] Wake model %r: %d bytes, cutoff %.2f, window %d",
                session,
                model.model_id,
                len(model.blob),
                model.probability_cutoff,
                model.sliding_window_size,
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

    def _spawn(self, coro) -> asyncio.Task:
        """Run beside the read loop, which must stay free to deliver frames."""
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

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
                    _cancel_stt(session.call)
                    session.call = None
                    session.writer = None
                    session.connected_at = None
                    # Whoever listened hears the end: the device stopped streaming.
                    for queue in session.listeners:
                        while not queue.empty():
                            queue.get_nowait()
                        queue.put_nowait(None)
                    session.listeners.clear()
                    session.monitoring = session.monitoring_intercom = False
                    if session.intercom is not None:
                        self._spawn(self.end_intercom(session.intercom, f"{self.satellite(session.satellite).name} disconnecting"))
                    enrollment = self.enrollment
                    if enrollment is not None and enrollment.satellite == session.satellite and enrollment.listening:
                        enrollment.state, enrollment.error = "failed", "The satellite disconnected"
                    recording = self.wake_recording
                    if recording is not None and recording.satellite == session.satellite and recording.listening:
                        recording.state, recording.error = "failed", "The satellite disconnected"
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
            if session.intercom is not None:
                self._to_peer(session, payload)
            return

        if msg_type == Message.AUDIO_MONITOR_AEC:
            enrollment = self.enrollment
            if enrollment is not None and enrollment.satellite == session.satellite:
                was = enrollment.listening
                enrollment.add(payload)
                if was and not enrollment.listening:
                    await self._set_monitor(session)  # collected all it takes
            recording = self.wake_recording
            if recording is not None and recording.satellite == session.satellite:
                was = recording.listening
                for audio, start, end in recording.add(payload):
                    self._spawn(self._judge_take(recording, audio, start, end))
                if was and not recording.listening:
                    await self._set_monitor(session)  # long enough
            return

        if msg_type == Message.WAKE_REPORT:
            recording = self.wake_recording
            if session.listeners or (recording is not None and recording.satellite == session.satellite):
                report = json.loads(payload) if payload else {}
                if recording is not None and recording.satellite == session.satellite:
                    recording.report(report)
                if session.listeners:
                    self._live(session, self._wake_report(report))
            return

        if msg_type == Message.AUDIO_IN_AEC:
            if session.call is not None:
                session.call.aec += payload
                self._feed_stt(session.call)
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
            reason = str(info.get("reason") or "")
            _LOGGER.info("[%s] Device ended call: %s", session, reason)
            call = session.call
            if call is not None and call.record is not None and call.record.finished is None:
                cancelled = reason == "cancelled"
                self.calls.finish(call.record, "cancelled" if cancelled else "ended by device",
                                  "cancelled on the satellite" if cancelled else reason)
            _cancel_stt(call)
            session.call = None
            if call is not None and call.responding is not None and not call.responding.done():
                # Its button: no more of the reply, nor of what led to it.
                call.responding.cancel()
            if reason == "cancelled":
                # The device drops what was already sent until this.
                await self._send(session, Message.AUDIO_OUT_END)
        elif msg_type == Message.PLAYBACK_DRAINED:
            session.drained.set()
        elif msg_type == Message.DEVICE_EVENT:
            kind = info.get("type")
            _LOGGER.debug("[%s] Device event: %s", session, kind)
            if kind == "announcing":
                session.announcing.set()
            elif kind == "announced":
                session.announced.set()
            elif kind == "intercom_end" and session.intercom is not None:
                # The button, on either.
                self._spawn(self.end_intercom(session.intercom, f"the button on {self.satellite(session.satellite).name}"))
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
        await self._set_monitor(session)
        return queue

    async def stop_listening(self, key: str, queue: asyncio.Queue) -> None:
        session = self.sessions.get(key)
        if session is None or queue not in session.listeners:
            return
        session.listeners.discard(queue)
        if not session.listeners:
            _LOGGER.info("[%s] Nobody is listening live any more", session)
        with contextlib.suppress(ConnectionError, OSError):
            await self._set_monitor(session)

    async def _set_monitor(self, session: Session) -> None:
        """Have the device stream outside calls while someone listens live, a
        speaker is enrolled from it or records a wake word there, and not
        otherwise."""
        intercom = session.intercom is not None
        wanted = intercom or bool(session.listeners) or any(
            s is not None and s.satellite == session.satellite and s.listening
            for s in (self.enrollment, self.wake_recording)
        )
        if (wanted, intercom) != (session.monitoring, session.monitoring_intercom) and session.writer is not None:
            session.monitoring, session.monitoring_intercom = wanted, intercom and wanted
            await self._send_json(session, Message.MONITOR, {"active": wanted, "intercom": intercom and wanted})

    # ------------------------------------------------------------ speakers

    def enrollment_status(self) -> dict[str, Any] | None:
        return self.enrollment.to_json() if self.enrollment is not None else None

    async def start_enrollment(self, key: str, name: str) -> dict[str, Any]:
        """Collect a speaker's voice from a satellite while they read aloud."""
        if self.speakers is None:
            raise ConfigError("Speaker identification is turned off")
        speaker = people.person_name(name)
        if speaker is None:
            raise ConfigError("Give the speaker a name")
        session = self.sessions.get(key)
        if session is None or session.writer is None:
            raise ConfigError("The satellite is not connected")
        if not session.hello.get("monitor_aec"):
            raise ConfigError("This satellite's firmware can't stream for enrollment: update it")
        self._not_listening()
        self.enrollment = Enrollment(key, self.satellite(key).name, self.speakers.profiles.find(speaker) or speaker)
        _LOGGER.info("[%s] Enrolling %s", session, self.enrollment.name)
        await self._set_monitor(session)
        return self.enrollment.to_json()

    async def finish_enrollment(self, save: bool) -> dict[str, Any] | None:
        """Stop listening; with ``save``, make or extend the profile."""
        enrollment = self.enrollment
        if enrollment is None:
            return None
        if enrollment.state in ("done", "failed", "cancelled"):
            if not save:
                self.enrollment = None
            return enrollment.to_json()
        enrollment.state = "saving" if save else "cancelled"
        session = self.sessions.get(enrollment.satellite)
        if session is not None:
            with contextlib.suppress(ConnectionError, OSError):
                await self._set_monitor(session)
        if not save:
            self.enrollment = None
            return enrollment.to_json()
        assert self.speakers is not None
        try:
            if enrollment.speech_s < ENROLL_MIN_SECONDS:
                raise ConfigError(f"Only {enrollment.speech_s:.0f} s of speech: read for longer")
            utterance = await self.speakers.embed(bytes(enrollment.audio))
            if utterance is None:
                raise ConfigError("Too little speech to learn a voice from")
            name, profile = self.speakers.add(enrollment.name, utterance)
            profile.setdefault("enrolled", []).append({
                "satellite": enrollment.satellite_name,
                "speech_s": round(enrollment.speech_s, 1),
                "at": time.time(),
            })
            self.speakers.profiles.save()
            enrollment.name, enrollment.profile, enrollment.state = name, _profile_json(name, profile), "done"
        except ConfigError as err:
            enrollment.state, enrollment.error = "failed", str(err)
        release_memory()
        return enrollment.to_json()

    def update_speaker(self, name: str, new_name: str | None = None, color: str | None = None) -> dict[str, Any]:
        """Rename a speaker, or give them another ring color (a palette name)."""
        if self.speakers is None:
            raise ConfigError("Speaker identification is turned off")
        profiles = self.speakers.profiles
        key = profiles.find(name)
        if key is None:
            raise FileNotFoundError(f"No speaker {name}")
        if new_name is not None:
            renamed = people.person_name(new_name)
            if renamed is None:
                raise ConfigError("Give the speaker a name")
            try:
                key = profiles.rename(key, renamed)
            except ValueError as err:
                raise ConfigError(str(err)) from err
            self._renamed(name, key)
        if color is not None:
            rgb = dict(PALETTE).get(color)
            if rgb is None:
                raise ConfigError(f"No color {color}: one of {', '.join(n for n, _ in PALETTE)}")
            profiles.recolor(key, rgb)
        profiles.save()
        return _profile_json(key, profiles.speakers[key])

    def delete_speaker(self, name: str) -> None:
        if self.speakers is None:
            raise ConfigError("Speaker identification is turned off")
        key = self.speakers.profiles.find(name)
        if key is None:
            raise FileNotFoundError(f"No speaker {name}")
        if self.wake_takes is not None:
            self.wake_takes.forget(self.speakers.profiles.speakers[key])
        self.speakers.profiles.remove(key)
        self.speakers.profiles.save()

    def _not_listening(self) -> None:
        """One satellite listens for the Speakers page at a time."""
        if self.enrollment is not None and self.enrollment.listening:
            raise ConfigError(f"Already enrolling {self.enrollment.name} on {self.enrollment.satellite_name}")
        recording = self.wake_recording
        if recording is not None and recording.listening:
            raise ConfigError(f"Already recording {recording.speaker} on {recording.satellite_name}")

    # ------------------------------------------------------ wake recordings

    def wake_recording_status(self) -> dict[str, Any] | None:
        return self.wake_recording.to_json() if self.wake_recording is not None else None

    async def start_wake_recording(self, key: str, name: str, phrase: Any) -> dict[str, Any]:
        """Record a speaker saying a wake phrase at a satellite, again and again."""
        if self.speakers is None or self.wake_takes is None:
            raise ConfigError("Speaker identification is turned off")
        speaker = self.speakers.profiles.find(str(name or ""))
        if speaker is None:
            raise ConfigError("Enroll their voice first")
        said = phrase_of(phrase)
        word = word_id(said)
        if len(word) < 3:
            raise ConfigError("Type the wake phrase")
        session = self.sessions.get(key)
        if session is None or session.writer is None:
            raise ConfigError("The satellite is not connected")
        if not session.hello.get("monitor_aec"):
            raise ConfigError("This satellite's firmware can't stream for recording: update it")
        self._not_listening()
        # The satellite's own model of the word, if it runs one: did it hear each take?
        model = next((
            w for w in session.wake_words
            if w == word or (self.wake_words.get(w) is not None and word_id(self.wake_words.get(w).name) == word)
        ), None)
        self.wake_recording = WakeRecording(key, self.satellite(key).name, speaker, said, word, model=model)
        _LOGGER.info("[%s] Recording %s saying %r", session, speaker, said)
        await self._set_monitor(session)
        return self.wake_recording.to_json()

    async def stop_wake_recording(self) -> dict[str, Any] | None:
        recording = self.wake_recording
        if recording is None:
            return None
        if recording.state == "listening":
            recording.state = "done"
        session = self.sessions.get(recording.satellite)
        if session is not None:
            with contextlib.suppress(ConnectionError, OSError):
                await self._set_monitor(session)
        return recording.to_json()

    async def _judge_take(self, recording: WakeRecording, audio: bytes, start: float, end: float) -> None:
        """Keep an utterance when it sounds like the speaker, and like no one
        else more."""
        assert self.speakers is not None and self.wake_takes is not None
        try:
            names, voices = self.speakers.profiles.matrix()
            vector = await self.speakers.embed_window(audio)
            scores = dict(zip(names, (voices @ vector).tolist())) if voices is not None else {}
            score = scores.get(recording.speaker)
            closest = max(scores, key=scores.get, default=None)
            if score is None or score < TAKE_SCORE:
                recording.taken(False, start, end, why=f"Didn't sound like {recording.speaker}", score=score)
                return
            if closest != recording.speaker:
                recording.taken(False, start, end, why=f"Sounded more like {closest}", score=score)
                return
            if self.speakers.profiles.find(recording.speaker) != recording.speaker:
                recording.taken(False, start, end, why=f"{recording.speaker} is gone")
                return
            take = self.wake_takes.add(
                recording.speaker, recording.word, recording.phrase, audio,
                satellite=recording.satellite_name, score=None if score is None else round(score, 3),
                heard=recording.heard(start, end),
            )
            self.speakers.profiles.save()
            recording.taken(True, start, end, score=score, take=take)
        except Exception as err:  # noqa: BLE001 - shown with the take
            _LOGGER.exception("Judging a take of %s failed", recording.speaker)
            recording.taken(False, start, end, why=str(err))
        finally:
            recording.checking -= 1

    def delete_wake_takes(self, name: str, word: str, take_id: str | None = None) -> int:
        if self.speakers is None or self.wake_takes is None:
            raise ConfigError("Speaker identification is turned off")
        key = self.speakers.profiles.find(name)
        if key is None:
            raise FileNotFoundError(f"No speaker {name}")
        gone = self.wake_takes.remove(key, word, take_id)
        if not gone:
            raise FileNotFoundError("No such recording")
        self.speakers.profiles.save()
        return gone

    def wake_take_path(self, name: str, word: str, take_id: str) -> Path | None:
        if self.speakers is None or self.wake_takes is None:
            return None
        key = self.speakers.profiles.find(name)
        return self.wake_takes.find(key, word, take_id) if key is not None else None

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
        if session.announcing_now is not None and not session.announcing_now.done():
            session.announcing_now.cancel()  # someone wants it: the announcement waits no more
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
        lead = session.hello.get("aec_lead_samples")
        if isinstance(lead, int) and not isinstance(lead, bool) and lead >= 0:
            session.call.aec_lead = lead
        # With learn-tv: which detector heard it -- the null's, or mic 0 alone,
        # in which case the satellite sent mic 0 as the call (swapped).
        heard_by = {key: info[key] for key in ("detector", "swapped") if key in info}
        record.stage(
            "call_start",
            wake_word=session.call.wake_word,
            name=word.name if word is not None else None,
            **detection,
            **heard_by,
            preroll=round(session.call.preroll_bytes / BYTE_RATE, 3),
            aec_preroll=round(session.call.aec_preroll_bytes / BYTE_RATE, 3),
            aec_lead=session.call.aec_lead,
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
        longest = self.end_of_speech().max_seconds
        await asyncio.sleep(longest)
        if session.call is call and not call.finished:
            why, details = self._timed_out(call)
            _LOGGER.warning("[%s] Command exceeded %.1fs (%s); ending", session, longest, why)
            call.finished = True
            self._give_up(session, call, why, longest, details)

    def _give_up(self, session: Session, call: Call, why: str, seconds: float, details: dict[str, Any]) -> None:
        """The call ends with nothing done: it ran out of time, or nobody spoke."""
        if call.record is not None:
            call.record.stage("timeout", why=why, seconds=seconds, **details)
            self.calls.finish(call.record, "timeout", why, self._call_audio(call))
        self._spawn(self._hang_up(session, "timeout"))

    def _command_ended(self, session: Session, call: Call) -> None:
        """The endpointer ended the command: answer it -- or, nothing said
        ``no_speech_seconds`` after the wake word, hang up."""
        call.finished = True
        if call.endpointer is not None and call.endpointer.no_speech:
            seconds = call.endpointer.settings.no_speech_seconds
            _LOGGER.info("[%s] No speech %.1fs after the wake word; ending", session, seconds)
            self._give_up(session, call, "no speech was heard", seconds, self._timed_out(call)[1])
            return
        call.responding = self._spawn(self._respond(session, call))

    @staticmethod
    def _timed_out(call: Call) -> tuple[str, dict[str, Any]]:
        """Why a call ran out of time: the audio stopped arriving, the wake
        word was still being checked, no speech was heard, or speech never
        ended in enough silence -- and what was heard."""
        quiet = time.monotonic() - call.last_audio if call.last_audio is not None else None
        details: dict[str, Any] = {
            "audio_seconds": round(len(call.audio) / BYTE_RATE, 2),
            "audio_stopped": None if quiet is None else round(quiet, 2),
        }
        ended = call.endpointer
        if ended is not None:
            details.update(
                vad=ended.vad,
                vad_gain=round(ended.gain, 2),
                command_seconds=round(len(call.command) / BYTE_RATE, 2),
                speech_started=ended.speech_started,
                speech_seconds=round(ended.speech_seconds, 2),
                silence_seconds=round(ended.silence_seconds, 2),
                settings=ended.settings.to_json(),
            )
        if quiet is None or quiet >= 1.0:
            return f"the audio stopped arriving {details['audio_seconds']:.1f} s in", details
        if ended is None:
            return "the wake word was still being checked", details
        if not ended.speech_started:
            return "no speech was heard", details
        return (
            f"{ended.speech_seconds:.1f} s of speech, never {ended.settings.silence_seconds:g} s of silence",
            details,
        )

    async def _on_audio(self, session: Session, chunk: bytes) -> None:
        call = session.call
        if call is None or call.finished:
            return
        call.last_audio = time.monotonic()

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

        if call.in_null:
            new_audio = _aec_since(call)
        else:
            start = max(call.split_bytes, len(call.audio) - len(chunk))
            new_audio = bytes(call.audio[start:])
        if not new_audio:
            return
        call.command += new_audio
        self._feed_stt(call)
        if call.endpointer.process(new_audio) and not call.finished:
            self._command_ended(session, call)
        else:
            self._check_speaker_due(session, call)

    def _check_speaker_due(self, session: Session, call: Call) -> None:
        """Check the command's last second against the speaker, when it is
        due, beside the audio."""
        if call.speaker_checking or call.speaker_ref is False or call.endpointer is None:
            return
        due = call.endpointer.due()
        if due is not None:
            call.speaker_checking = True
            self._spawn(self._check_speaker(session, call, *due))

    async def _check_speaker(self, session: Session, call: Call, start: float, end: float) -> None:
        """Is the command from ``start`` to ``end`` still the identified
        speaker? Identifies them first (the wake word and the command so far), once."""
        assert self.speakers is not None and call.endpointer is not None
        endpointer = call.endpointer
        try:
            if call.speaker_ref is None:
                ident = await self.speakers.identify(self._speaker_slice(call, -WAKE_SECONDS, end))
                call.speaker_watch = {"speaker": ident.name, "score": None if ident.score is None else round(ident.score, 3)}
                reference = self.speakers.reference(ident.name) if ident.name else None
                if reference is None:
                    call.speaker_ref = False
                    endpointer.watch(False)
                    _LOGGER.debug("[%s] Nobody identified: not watching for someone else", session)
                    return
                call.speaker_ref = reference
                _LOGGER.debug("[%s] Watching for someone other than %s", session, ident.name)
            vector = await self.speakers.embed_window(self._speaker_slice(call, start, end))
            score = float(vector @ call.speaker_ref)
            ended = endpointer.verdict(score, end)
            if endpointer.other_from is not None:
                call.speaker_end = endpointer.other_from
            if ended and not call.finished:
                call.finished = True
                _LOGGER.info(
                    "[%s] Someone other than %s talking: the command ended at their last word, %.2fs",
                    session, (call.speaker_watch or {}).get("speaker"), call.speaker_end or 0.0,
                )
                call.responding = self._spawn(self._respond(session, call))
        except Exception:  # never let the check take the call down with it
            _LOGGER.exception("[%s] Checking the speaker failed", session)
            call.speaker_ref = False
            endpointer.watch(False)
        finally:
            call.speaker_checking = False
        if not call.finished:
            self._check_speaker_due(session, call)

    def _speaker_slice(self, call: Call, start: float, end: float | None) -> bytes:
        """The capture speakers are identified on, from ``start`` to ``end``
        seconds after the split (negative: before it; None: to the end)."""
        assert self.speakers is not None
        if self.speakers.channel == "aec" and call.aec:
            # The two captures carry their own pre-rolls; line them up on the
            # detection, which ends both.
            audio = call.aec
            split = call.split_bytes - call.preroll_bytes + (call.aec_preroll_bytes or call.preroll_bytes)
        else:
            audio, split = call.audio, call.split_bytes
        a = max(0, split + _seconds_bytes(start))
        b = len(audio) if end is None else max(a, split + _seconds_bytes(end))
        return bytes(audio[a:b])

    def _speaker_stop(self, call: Call) -> float | None:
        """Where what is recognized stops, in seconds from the split: a little
        after the speaker's last word, when someone else's speech ended it."""
        return None if call.speaker_end is None else call.speaker_end + SPEAKER_END_MARGIN

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

    async def _check_null(self, session: Session, call: Call) -> None:
        """Whether the talker stood in learn-tv's null: the wake word, the
        pre-roll's last WAKE_SECONDS (it ends at the detection), is not speech
        in the call capture and is in the AEC one. Then the call is heard on
        the AEC capture throughout -- verified, ended and recognized."""
        if not call.aec_preroll_bytes:
            return
        end = call.preroll_bytes
        start = max(0, end - int(WAKE_SECONDS * BYTE_RATE))
        heard = await asyncio.to_thread(wake_speech, bytes(call.audio[start:end]))
        if heard >= NULL_CALL_SPEECH:
            return
        # The AEC capture's pre-roll can trail the call's by a moment.
        deadline = time.monotonic() + 0.5
        while len(call.aec) < end + _aec_shift(call) and time.monotonic() < deadline and not call.finished:
            await asyncio.sleep(0.02)
        aec_heard = await asyncio.to_thread(wake_speech, _aec_lined_up(call, start, end))
        call.in_null = aec_heard >= NULL_AEC_SPEECH
        if call.record is not None:
            call.record.stage("talker_in_null", call_speech=round(heard, 2), aec_speech=round(aec_heard, 2),
                              used="aec" if call.in_null else "call")
        if call.in_null:
            _LOGGER.info("[%s] The talker is in the null: heard on the AEC capture", session)

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
        window = _heard(call, offset, offset + call.preroll_bytes)
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
            window = _heard(call, 0, call.preroll_bytes)
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
            asked = time.perf_counter()
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
                    seconds=round(time.perf_counter() - asked, 3),
                )
        return verdict

    async def _verify(self, session: Session, call: Call) -> None:
        await self._check_null(session, call)
        verdict = await self._verify_wake(str(session), call)
        if call.in_null and not verdict.accepted:
            # Refused on the AEC capture: the call capture's verdict as well,
            # as before the null was noticed, so that nothing is refused that
            # was taken then. The call is heard on the AEC capture either way.
            call.in_null = False
            again = await self._verify_wake(str(session), call)
            call.in_null = True
            if again.accepted:
                verdict = again
        result = verdict.result

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
        wake = _heard(call, max(0, call.split_bytes - int(WAKE_SECONDS * BYTE_RATE)), call.split_bytes)
        await self._start_stt(session, call)
        call.endpointer = Endpointer(self.end_of_speech(), gain=vad_gain(wake), warm=wake)
        # Someone else talking ends the command, once the speaker is
        # identified: when that is on, with profiles to identify them by.
        call.endpointer.watch(call.endpointer.settings.end_on_other_voice and self.identifies())
        if call.in_null:
            call.endpoint_pos = call.split_bytes
            call.command += _aec_since(call)
        else:
            call.command += bytes(call.audio[call.split_bytes :])
        await self._event(session, "listening", stage="listening")
        if call.endpointer.process(bytes(call.command)) and not call.finished:
            self._command_ended(session, call)
        else:
            self._check_speaker_due(session, call)

    async def _start_stt(self, session: Session, call: Call) -> None:
        """When the satellite's STT entity always runs: the stream it hears
        the command on, from the split (Kaldi's: where endpointing starts) --
        the AGC capture, or the AEC one lined up with it (``stt_audio_for``),
        boosted by what brings the wake word in it to ``STT_PEAK``."""
        satellite = self.satellite(session.satellite)
        voice = satellite.voice
        if not voice.stt_always or voice.stt is None or self.engine is None:
            return
        start, gain = call.split_bytes, 1.0
        audio: dict[str, Any] = {"channel": "agc", "from": round(call.split_bytes / BYTE_RATE, 3)}
        if call.in_null:
            audio.update(channel="aec", why="the talker is in the null")
            start = max(0, start + _aec_shift(call))
        elif voice.stt_audio != "agc" and not call.aec:
            audio["why"] = "no AEC capture"
        elif voice.stt_audio != "agc":
            audio["channel"] = "aec"
            shift = (call.aec_preroll_bytes or call.preroll_bytes) - call.preroll_bytes - call.aec_lead * 2
            start = max(0, start + shift)
            if voice.stt_audio == "aec_boost":
                wake_start = (
                    ms_to_bytes(call.wake_start * 1000)
                    if call.wake_start is not None
                    else call.split_bytes - BYTE_RATE
                )
                wake = _pcm_to_float(bytes(call.aec[max(0, wake_start + shift) & ~1 : start & ~1]))
                peak = float(np.max(np.abs(wake))) if len(wake) else 0.0
                gain = min(STT_MAX_GAIN, STT_PEAK / peak) if peak > 0 else 1.0
                audio.update(gain_db=round(20 * math.log10(gain), 1), set_by="the wake word")
        stream = await self.voices.streaming_transcriber(voice.stt, self.engine.transcriber, voice.stt_language, audio)
        if stream is None:
            return
        if call.finished or session.call is not call:
            stream.cancel()
            return
        stream.position = start & ~1
        call.stt, call.stt_gain = stream, gain
        self._feed_stt(call)

    @staticmethod
    def _feed_stt(call: Call) -> None:
        """What has come of the command since, to the STT entity hearing it."""
        stream = call.stt
        if stream is None or stream.closed or stream.position is None:
            return
        if stream.audio["channel"] == "aec":
            # Up to the end of the AGC capture, lined up.
            shift = (call.aec_preroll_bytes or call.preroll_bytes) - call.preroll_bytes - call.aec_lead * 2
            end = min(len(call.aec), len(call.audio) + shift) & ~1
            pcm = bytes(call.aec[stream.position : end])
            if call.stt_gain != 1.0 and pcm:
                scaled = np.clip(np.frombuffer(pcm, dtype="<i2").astype(np.float32) * call.stt_gain, -32768, 32767)
                pcm = scaled.astype("<i2").tobytes()
        else:
            end = len(call.audio) & ~1
            pcm = bytes(call.audio[stream.position : end])
        if end > stream.position:
            stream.feed(pcm)
            stream.position = end

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

    async def _keep_streamed(self, record: CallRecord, recognized: dict[str, Any], stream: Any) -> None:
        """The streamed transcript the engine didn't need, in the session's
        recognition step when the entity has answered."""
        await stream.result()
        recognized["stt"] = stream.to_json()
        self.calls.update(record)

    @staticmethod
    def _call_audio(call: Call) -> dict[str, bytes]:
        return {"call": bytes(call.audio), "call-aec": bytes(call.aec), "command": bytes(call.command)}

    async def _respond(self, session: Session, call: Call) -> None:
        """Recognize the command and do it. Whatever goes wrong, the call is
        answered and hung up rather than left open on the satellite. The
        thinking sound plays if the reply is slow to come."""
        if self.thinking_sound() and self.speech.thinking:
            call.thinking = Thinking(self, session, self.speech.thinking, self.thinking_delay())
        try:
            await self._respond_unguarded(session, call)
        except Exception as err:  # noqa: BLE001 - the satellite must hear back
            _LOGGER.exception("[%s] Call failed", session)
            if call.record is not None and call.record.finished is None:
                call.record.stage("error", error=repr(err))
                self.calls.finish(call.record, "error", repr(err), self._call_audio(call))
            with contextlib.suppress(Exception):
                await self._say(session, self.language.speech.WENT_WRONG)
            with contextlib.suppress(Exception):
                await self._hang_up(session, "error")
        finally:
            if call.thinking is not None:
                await call.thinking.stop(close=False)  # the call is over: nothing left to end
            release_memory()  # what recognizing and answering it used

    async def _respond_unguarded(self, session: Session, call: Call) -> None:
        self._save(session, call)
        record = call.record
        _LOGGER.info(
            "[%s] Command audio: %.2fs", session, len(call.command) / BYTE_RATE
        )
        if record is not None:
            ended = call.endpointer
            record.stage(
                "endpoint",
                command_seconds=round(len(call.command) / BYTE_RATE, 3),
                **(
                    {
                        "vad": ended.vad,
                        "vad_gain": round(ended.gain, 2),
                        "speech_seconds": round(ended.speech_seconds, 2),
                        "silence_seconds": round(ended.silence_seconds, 2),
                        "settings": ended.settings.to_json(),
                        # Who was watched for, and when someone else's speech ended it.
                        **({"others": {**(call.speaker_watch or {}), **(ended.cut or {})}}
                           if call.speaker_watch or ended.cut else {}),
                    }
                    if ended is not None
                    else {}
                ),
            )
            record.marks["end"] = len(call.audio) / BYTE_RATE

        # Speaker ID runs beside recognition -- its own lock and threads -- and
        # sends the processing event the moment it knows, so the ring takes the
        # speaker's color while the command is still being recognized. With
        # no voice enrolled, there is nothing to identify.
        if self.identifies():
            speaker_task = asyncio.ensure_future(self._identify(session, call))
        else:
            speaker_task = None
            who = self.speaking(None, call.wake_word)
            if who.name is not None and record is not None:
                record.stage("speaker", name=who.name, by=who.by)
            await self._event(session, "processing", **self._speaker_event(who))

        satellite = self.satellite(session.satellite)
        transcriber = call.stt
        if transcriber is not None:
            # It has heard the command as it was said: all of it, now.
            self._feed_stt(call)
            transcriber.finish()
        else:
            transcriber = await self._transcriber(satellite)
        agc, aec = bytes(call.audio), bytes(call.aec)
        if call.in_null:
            # Heard as the AEC capture, on the AGC capture's timeline.
            agc = _aec_lined_up(call, 0, len(call.audio))
        stop = self._speaker_stop(call)
        if stop is not None:
            # Someone else's speech ended it: recognized up to the speaker's last word.
            agc = agc[: call.split_bytes + _seconds_bytes(stop)]
            aec = aec[: call.split_bytes - call.preroll_bytes + (call.aec_preroll_bytes or call.preroll_bytes) + _seconds_bytes(stop)]
        if isinstance(transcriber, HassTranscriber):
            # A request (or, when a stream failed, the request after it):
            # from Kaldi's split, as a stream is.
            transcriber.audio_for = stt_audio_for(
                agc, aec, call.split_bytes // 2, call.preroll_bytes // 2,
                (call.aec_preroll_bytes or call.preroll_bytes) // 2,
                capture="agc" if satellite.voice.stt_audio == "agc" else "aec",
                boost=satellite.voice.stt_audio == "aec_boost",
                lead=call.aec_lead,
            )
        async with self._model_lock:
            assert self.engine is not None
            decision, split = await asyncio.to_thread(
                self.engine.recognize_call,
                _pcm_to_float(agc),
                call.wake_start,
                call.split_bytes / BYTE_RATE,
                satellite,
                transcriber,
                self.wake_words.phrases(call.wake_word),
            )
        if record is not None:
            record.stage("split", **split.to_json(), seconds=_seconds(decision.timings.get("acoustic+placement")))
            record.marks["cut"] = split.cut
            if split.wake_end is not None:
                record.marks["wake_start"] = split.wake_start
                record.marks["wake_end"] = split.wake_end
            recognized = record.stage("recognition", **decision.to_json(), stt=_stt_json(transcriber))
            if call.stt is not None and not call.stt.asked:
                # Not needed, and so not waited for: kept when it comes.
                self._spawn(self._keep_streamed(record, recognized, call.stt))
        ident = await speaker_task if speaker_task is not None else None

        self._after_identify(session, ident)
        who = self.speaking(ident, call.wake_word)
        speaker = who.as_dict()
        satellite = self.voice_for(satellite, who)
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
        speech, outcome, summary = await self._carry_out(
            decision, satellite, speaker, dry_run, record, self._wake_json(call.wake_word)
        )
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
        wake_word: dict[str, Any] | None = None,
    ) -> tuple[str | None, str, str]:
        """Run (or show) the decided command. Returns what to say (None: a
        silent command, not even the acknowledgement sound), the outcome, and
        a one-line summary. ``wake_word``: what started the call
        (``_wake_json``), as commands see it."""
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
        if decision.command is None and decision.answer is not None and decision.answer.get("text"):
            answer = decision.answer
            said = await self._skill_says(answer, satellite, speaker, record, wake_word)
            return said, "answered", f"{decision.transcript!r}: {answer.get('skill') or answer['kind']}'s answer"
        if decision.command is None:
            message = decision.message or self.language.speech.SORRY
            compiled = self.engine.compiled if self.engine is not None else None
            if compiled is not None and compiled.unknown and decision.refused is None:
                # Nothing understood it: the Unknown command says what to say.
                values = actions.unknown_variables(decision, satellite, speaker, compiled.commands, self.hass.catalog, wake_word)
                started = time.perf_counter()
                said, error = await actions.render_said(self.hass, compiled.unknown, values)
                if record is not None:
                    record.stage(
                        "unknown", said=said, error=error, seconds=_seconds(time.perf_counter() - started),
                        variables={k: v for k, v in values.items() if k not in actions.PRIVATE},
                    )
                if said:
                    message = said
            tried = model_note(decision.fallback)
            summary = f"{decision.transcript!r}: {message}" + (f" ({tried})" if tried else "")
            return message, "not recognized", summary
        _LOGGER.info(
            "Matched %s (%s) slots=%s%s",
            decision.command.id,
            decision.matched_by,
            decision.slots,
            " [dry run]" if dry_run else "",
        )
        started = time.perf_counter()
        outcome = await actions.run(
            self.hass, decision, satellite, speaker, dry_run, self.integration, self.music, self.beep_elsewhere(),
            calendar=self.alarms, wake_word=wake_word, people=self.store.people,
            home=self.store.settings.get("home"), satellites=self,
        )
        if record is not None:
            record.stage("action", **outcome.to_json(), seconds=_seconds(time.perf_counter() - started))
        state = "error" if outcome.error else ("dry run" if dry_run else "done")
        return None if outcome.silent else outcome.speech, state, f"{decision.command.id}: {decision.transcript!r}"

    async def _skill_says(
        self, answer: dict[str, Any], satellite: Satellite, speaker: dict[str, Any], record: CallRecord | None,
        wake_word: dict[str, Any] | None = None,
    ) -> str:
        """A skill's answer as its page says: at its level (specific unless
        set), or that level's template of yours, rendered by Home Assistant
        -- else, when it can't be, the built-in sentence."""
        config = skill_response(self.store.settings, str(answer.get("skill") or ""))
        level = config["verbosity"]
        built_in = (answer.get("levels") or {}).get(level) or answer["text"]
        template = config["response"].get(level)
        said, error, seconds = built_in, None, None
        if template:
            values = {
                "answer": answer.get("answer"),
                "question": answer.get("question"),
                "response": built_in,
                "satellite": satellite.as_dict(),
                "speaker": speaker or people.Speaker().as_dict(),
                "wake_word": wake_word or actions.NO_WAKE_WORD,
            }
            started = time.perf_counter()
            rendered, error = await actions.render_said(self.hass, template, values)
            seconds = _seconds(time.perf_counter() - started)
            said = rendered or built_in
        answer.update(text=said, level=level)
        if record is not None and (template or level != skills.DEFAULT_LEVEL):
            record.stage(
                "skill_response", skill=answer.get("skill"), level=level, template=template or None, said=said, error=error,
                seconds=seconds,
            )
        return said

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
        reply = await self.agent.ask(satellite.key, decision.transcript, satellite.device_id, self.language.code)
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
        pieces = sentences_of(text)
        text = " ".join(pieces)  # what is said: without markup
        if text and voice.tts:
            target = voice.speak_on or satellite.device_media_player
            if target is None:
                _LOGGER.warning("[%s] No media player of its own in Home Assistant; speaking with Piper", session)
            else:
                try:
                    await self._quiet(session, close=True)
                    await self._speak(session, satellite, target, text, record)
                    return
                except (HassError, ConnectionError, OSError, TimeoutError) as err:
                    _LOGGER.warning("[%s] %s could not speak (%s); speaking with Piper", session, voice.tts, err)
                    if record is not None:
                        record.stage("speech_failed", tts=voice.tts, on=target, error=str(err))
        # Piper, a sentence at a time, each played while the next is made; or
        # -- with nothing to say -- the acknowledgement sound, which stays on
        # the satellite even when its responses are spoken elsewhere.
        started = time.perf_counter()
        timing = {"seconds": 0, "synthesis": 0.0, "first": None}

        async def spoken():
            if not pieces:
                timing["first"], timing["seconds"] = 0.0, len(self.speech.acknowledge)
                await self._quiet(session, close=False)
                yield self.speech.acknowledge
                return
            for piece in pieces:
                begun = time.perf_counter()
                async with self._model_lock:
                    audio = await asyncio.to_thread(self.speech.synthesize, piece)
                timing["synthesis"] += time.perf_counter() - begun
                if timing["first"] is None:
                    timing["first"] = time.perf_counter() - started
                    await self._quiet(session, close=False)  # the reply follows the thinking sound
                timing["seconds"] += len(audio)
                yield audio

        try:
            await self._play_stream(session, spoken(), paced)
        finally:
            if record is not None:
                # How long it is, how long Piper took to say it, and how long
                # until it began.
                record.stage(
                    "speech", text=text, seconds=round(timing["seconds"] / BYTE_RATE, 3),
                    synthesis=_seconds(timing["synthesis"]),
                    first=_seconds(timing["first"]) if timing["first"] is not None else None,
                    pieces=len(pieces),
                )

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
        # Seconds after asking: Home Assistant took the request; the device
        # started saying it (Home Assistant made the audio, the device fetched
        # it), and ended.
        heard: dict = {"requested": _seconds(time.perf_counter() - started), "started": None, "ended": None}
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
        who = self.speaking()
        speech, outcome, summary = await self._carry_out(decision, self.voice_for(satellite, who), who.as_dict(), not run_actions, record)
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
        if isinstance(transcriber, HassTranscriber):
            aec = b""
            aec_path = self.calls.audio_path(call_id, "call-aec") if satellite.voice.stt_audio != "agc" else None
            if aec_path is not None:
                with wave.open(str(aec_path), "rb") as wav:
                    aec = wav.readframes(wav.getnframes())
            started = next((s for s in old.stages if s.get("stage") == "call_start"), {})
            aec_preroll = started.get("aec_preroll")
            transcriber.audio_for = stt_audio_for(
                pcm, aec, call.split_bytes // 2, ms_to_bytes(preroll * 1000) // 2,
                ms_to_bytes((aec_preroll or preroll) * 1000) // 2,
                capture="agc" if satellite.voice.stt_audio == "agc" else "aec",
                boost=satellite.voice.stt_audio == "aec_boost",
                lead=int(started.get("aec_lead", AEC_LEAD_SAMPLES)),
            )
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
        record.stage("split", **split.to_json(), seconds=_seconds(decision.timings.get("acoustic+placement")))
        record.marks["cut"] = split.cut
        if split.wake_end is not None:
            record.marks["wake_start"] = split.wake_start
            record.marks["wake_end"] = split.wake_end
        record.stage("recognition", **decision.to_json(), stt=_stt_json(transcriber))
        heard_by = next((s for s in old.stages if s.get("stage") == "speaker" and s.get("name")), None)
        who = self.speaking(None, call.wake_word)
        if heard_by is not None and not heard_by.get("by"):
            who = people.speaking(self.store.people, self.voice_names(), heard_by["name"], heard_by.get("score"))
        speech, outcome, summary = await self._carry_out(
            decision, self.voice_for(satellite, who), who.as_dict(), not run_actions, record, self._wake_json(call.wake_word)
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
        speaker = people.person_name(name)
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

    async def train_wake_word(self, phrase: Any, speakers: list[str] | None = None, name: Any = None):
        """Train ``phrase``'s wake word with the recordings of ``speakers``
        (None: everyone's), called ``name`` (None: the phrase)."""
        recordings = {}
        if self.wake_takes is not None:
            recordings = self.wake_takes.of(word_id(phrase_of(phrase)), speakers)
        return await self.wake_training.start(phrase, recordings, name)

    def trained_here(self, word: str) -> bool:
        found = self.wake_words.get(word)
        return found is not None and found.custom and (found.manifest.get("trained") or {}).get("by") == "voice_call"

    async def _wake_word_trained(self, word: str) -> None:
        """A model trained here is saved: a cutoff tuned for the one before it
        goes (its verifier stays), and satellites listening for it get it. One
        trained from a built-in word's phrase is verified as that word is, the
        first time: its confusables and pronunciations."""
        self.wake_words.forget()
        entry = dict(self.store.wake_tuning.get(word) or {})
        base = self.wake_words.trained_from(word)
        if base is not None and word not in self.store.wake_tuning:
            shipped = self.wake.shipped_entry(base) or {}
            tuned = self.store.wake_tuning.get(base) or {}
            await self.tune_wake_word(word, {
                "confusables": list(tuned.get("confusables", shipped.get("confusables") or [])),
                "pronunciations": {**(shipped.get("pronunciations") or {}), **(tuned.get("pronunciations") or {})},
            })
        elif entry.pop("cutoff", None) is not None:
            await self.tune_wake_word(word, entry or None)
        for session in list(self.sessions.values()):
            if session.writer is not None and word in session.wake_words:
                self._spawn(self._push_wake_models(session))

    def _trained_model_error(self, word: str) -> str | None:
        """Why a wake word just trained can't run on the satellites, if so."""
        self.wake_words.forget()
        if self.wake_words.model(word) is not None:
            return None
        return self.wake_words.errors.get(word) or "it wasn't found after saving"

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

    def _calendar_due(self, event: alarms.Event) -> None:
        """An alarm or a reminder on the calendar starts: ring the satellite
        that set it -- every one, for a reminder added in Home Assistant."""
        say = alarms.words()
        if say is None:
            return
        alarm = event.kind == "alarm"
        entry = {
            "name": event.summary,
            "announce": say.alarm_due(event.summary, event.start) if alarm else say.reminder_due(event.summary),
            # A reminder is said once; an alarm rings until stopped.
            "once": not alarm,
        }
        keys = [event.satellite] if event.satellite else [k for k, s in self.sessions.items() if s.writer is not None]
        for key in keys:
            self._timer_finished(key, dict(entry))

    async def _ring(self, session: Session) -> None:
        """Say which timer finished, then sound the alarm until someone says
        "stop" (or wakes the satellite), or it times out."""
        if session.intercom is not None:
            await self.end_intercom(session.intercom, "a timer")
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
            ringing = list(session.ringing_timers)
            names = [t.get("name") for t in ringing]
            record.stage("timer_ringing", timers=ringing)
            await self._event(session, "timer_ringing", timers=len(names), name=names[-1] or "")
            # Timers by their names; the calendar's alarms and reminders by their own sentences.
            plain = [t for t in ringing if not t.get("announce")]
            said = [self.language.speech.timers_ringing(plain)] if plain else []
            said += [t["announce"] for t in ringing if t.get("announce")]
            if all(t.get("once") for t in ringing):
                await self._play(session, self.speech.alarm, paced=True)
            await self._say(session, " ".join(said), paced=True)
            while (
                time.monotonic() < deadline
                and session.writer is not None
                and not all(t.get("once") for t in session.ringing_timers)
            ):
                await self._play(session, self.speech.alarm, paced=True)
            if all(t.get("once") for t in session.ringing_timers):
                stopped_by = "said"
        except asyncio.CancelledError:
            stopped_by = session.ring_stopped_by or "stop"
            raise
        finally:
            record.stage("timer_stopped", by=stopped_by)
            ending = {"timeout": "timer rang out", "said": "reminder said"}.get(stopped_by, "timer stopped")
            self.calls.finish(record, ending, ", ".join(
                str(t.get("name") or self.language.speech.duration(float(t.get("created_duration") or 0))) for t in session.ringing_timers
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

    # ------------------------------------------------------ other satellites
    # What commands announce on and connect the intercom to (actions.run's
    # ``satellites``).

    def others(self, source: str, areas: list[str] | None) -> list[dict[str, Any]]:
        """The connected satellites but ``source``, in ``areas`` (None: every
        one), by name: ``key``, ``name``, ``area_id``."""
        out = []
        for key, session in list(self.sessions.items()):
            if key == source or session.writer is None:
                continue
            satellite = self.satellite(key)
            if areas is None or satellite.area_id in areas:
                out.append({"key": key, "name": satellite.name, "area_id": satellite.area_id})
        return sorted(out, key=lambda s: str(s["name"]).lower())

    def announce(self, keys: list[str], text: str, source: str) -> None:
        """Say ``text`` on these satellites, each once it is free."""
        for key in keys:
            session = self.sessions.get(key)
            if session is not None:
                self._spawn(self._announce(session, text, source))

    def _busy(self, session: Session) -> bool:
        """In a call, ringing a timer or joined by the intercom."""
        return (
            session.call is not None
            or (session.ringing is not None and not session.ringing.done())
            or session.intercom is not None
        )

    async def _announce(self, session: Session, text: str, source: str) -> None:
        """The chime and the announcement on one satellite, after a call, a
        ringing timer or the intercom there -- or not at all, after
        ANNOUNCE_WAIT. A call starting meanwhile cuts it off."""
        record = self.calls.begin("announcement", self.satellite(session.satellite).as_dict())
        record.stage("announcement", text=text, source=self.satellite(source).name if source else None)
        async with session.announcements:
            deadline = time.monotonic() + ANNOUNCE_WAIT
            while self._busy(session) and session.writer is not None and time.monotonic() < deadline:
                await asyncio.sleep(0.2)
            if session.writer is None or self._busy(session):
                self.calls.finish(record, "not announced", "disconnected" if session.writer is None else "still busy")
                return
            session.announcing_now = asyncio.current_task()
            ending = "announced"
            try:
                await self._event(session, "announcement", active=True)
                await self._play(session, tones.CHIME, paced=True)
                await self._say(session, text, record, paced=True)
            except asyncio.CancelledError:
                ending = "cut off by a call"
                raise
            finally:
                session.announcing_now = None
                with contextlib.suppress(ConnectionError, OSError):
                    await self._event(session, "announcement", active=False)
                self.calls.finish(record, ending, text)

    def intercom_problem(self, source: str, target: str) -> str | None:
        """Why these two can't be joined: ``needs_firmware``, or ``busy`` (a
        call, a ringing timer, the intercom, or someone enrolling or recording
        there); None when they can."""
        a, b = self.sessions.get(source), self.sessions.get(target)
        if a is None or b is None or a.writer is None or b.writer is None:
            return "busy"
        if not (a.hello.get("intercom") and b.hello.get("intercom")):
            return "needs_firmware"
        recording = [s.satellite for s in (self.enrollment, self.wake_recording) if s is not None and s.listening]
        if a.intercom is not None or (b.call is not None or b.intercom is not None) or self._busy(b) or {source, target} & set(recording):
            return "busy"
        return None

    def intercom(self, source: str, target: str) -> None:
        """Join them once the call that asked has ended."""
        self._spawn(self._intercom_after_call(source, target))

    async def _intercom_after_call(self, source: str, target: str) -> None:
        session = self.sessions.get(source)
        deadline = time.monotonic() + INTERCOM_START_WAIT
        while session is not None and session.call is not None and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        await self._start_intercom(source, target)

    async def _start_intercom(self, source: str, target: str) -> None:
        """The connect tone on both, then each one's microphone on the other's
        speaker; the devices stop answering wake words and show it."""
        problem = self.intercom_problem(source, target)
        if problem is not None:
            _LOGGER.warning("Intercom %s -> %s not connected: %s", source, target, problem)
            return
        a, b = self.sessions[source], self.sessions[target]
        intercom = Intercom(source, target)
        intercom.record = self.calls.begin("intercom", self.satellite(source).as_dict())
        intercom.record.stage("intercom", to=self.satellite(target).as_dict())
        a.intercom = b.intercom = intercom
        _LOGGER.info("[%s] Intercom with %s", a, b)
        try:
            await asyncio.gather(self._play(a, tones.CONNECTED), self._play(b, tones.CONNECTED))
            for session in (a, b):
                await self._set_monitor(session)
        except (ConnectionError, OSError) as err:
            await self.end_intercom(intercom, f"an error ({err})")
            return
        intercom.limit = self._spawn(self._intercom_limit(intercom))

    async def _intercom_limit(self, intercom: Intercom) -> None:
        await asyncio.sleep(INTERCOM_LIMIT)
        await self.end_intercom(intercom, "its time limit")

    def _to_peer(self, session: Session, chunk: bytes) -> None:
        """One satellite's microphone, now, on the other's speaker -- the
        voice's band, and turned down while the other speaks (Duplex) --
        dropped rather than queued when the other can't keep up."""
        intercom = session.intercom
        peer = self.sessions.get(intercom.peer(session.satellite)) if intercom is not None else None
        if peer is None or peer.writer is None or peer.intercom is not intercom:
            return
        chunk = intercom.duplex.relay(session.satellite, peer.satellite, chunk)
        if peer.writer.transport.get_write_buffer_size() < INTERCOM_BACKLOG:
            peer.writer.write(encode(Message.AUDIO_OUT, chunk))

    async def end_intercom(self, intercom: Intercom, by: str) -> None:
        """Both satellites back to themselves: no more streaming, the end tone,
        wake words again."""
        if intercom.ended:
            return
        intercom.ended = True
        if intercom.limit is not None and intercom.limit is not asyncio.current_task():
            intercom.limit.cancel()
        _LOGGER.info("Intercom %s <-> %s ended by %s", intercom.a, intercom.b, by)
        ended = []
        for key in (intercom.a, intercom.b):
            session = self.sessions.get(key)
            if session is None or session.intercom is not intercom:
                continue
            session.intercom = None
            with contextlib.suppress(ConnectionError, OSError):
                await self._set_monitor(session)
                # What was on its way from the other is not played; the device
                # starts afresh (a cancelled stream is dropped until its end).
                await self._send(session, Message.AUDIO_OUT_END)
                ended.append(session)
        for session in ended:
            self._spawn(self._play(session, tones.ENDED))
        if intercom.record is not None:
            intercom.record.stage("intercom_end", by=by, seconds=round(time.monotonic() - intercom.started, 1))
            self.calls.finish(intercom.record, "intercom ended", f"with {self.satellite(intercom.b).name}, ended by {by}")

    # ------------------------------------------------------------ speaker ID

    def _speaker_audio(self, session: Session, call: Call) -> bytes:
        """The wake word and the command, from the configured capture.

        Starts 1.2 s before the wake word ends: "okay nabu" plus the command is
        what was benchmarked. Not the whole pre-roll -- whatever was said before
        the wake word (a TV, someone else) would be scored too.
        """
        assert self.speakers is not None
        if self.speakers.channel == "aec" and not call.aec:
            _LOGGER.debug("[%s] No AEC capture; identifying from AGC", session)
        # Not past their last word when someone else's speech ended the command.
        return self._speaker_slice(call, -WAKE_SECONDS, self._speaker_stop(call))

    async def _identify(self, session: Session, call: Call) -> Identification | None:
        assert self.speakers is not None
        pcm = self._speaker_audio(session, call)
        started = time.perf_counter()
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

        who = self.speaking(ident, call.wake_word)
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
                name=who.name,
                **({"by": who.by} if who.by and who.by != "voice" else {}),
                score=round(float(ident.score), 4) if ident and ident.utterance else None,
                best=ident.best if ident else None,
                # No margin with one speaker enrolled: nobody is second.
                margin=round(float(ident.margin), 4) if ident and ident.utterance and math.isfinite(ident.margin) else None,
                seconds=round(time.perf_counter() - started, 3),
            )
        await self._event(session, "processing", **self._speaker_event(who))
        return ident

    def _speaker_event(self, who: people.Speaker) -> dict[str, Any]:
        """The processing event's speaker: their name (and score, by voice),
        and their ring color when they have a voice."""
        if who.name is None:
            return {"speaker": "unknown"}
        fields: dict[str, Any] = {"speaker": who.name}
        if who.score is not None:
            fields["score"] = round(who.score, 3)
        if self.speakers is not None:
            fields.update(_rgb(self.speakers.profiles.color(self.speakers.profiles.find(who.name))))
        return fields

    def _after_identify(self, session: Session, ident: Identification | None) -> None:
        """Learn from a confident call: its voice into the speaker's profile."""
        if self.speakers is None or ident is None:
            return
        if self.speakers.maybe_learn(ident):
            _LOGGER.info("[%s] Learned this call into %s's profile", session, ident.name)

    # ---------------------------------------------------------------- output

    async def _play(self, session: Session, pcm: bytes, paced: bool = False) -> None:
        """Stream audio and wait until the device has played it out
        (``_play_stream``)."""

        async def once():
            yield pcm

        await self._play_stream(session, once(), paced)

    async def _play_stream(self, session: Session, pieces, paced: bool = False) -> None:
        """Stream audio as its pieces come (an async iterable of PCM), one
        stream however many, and wait until the device has played it out.

        ``paced``: stay at most PLAY_LEAD ahead of the device's clock, for
        audio outside a call (a ringing timer). The device's buffer only
        pushes back once full, and its TCP window holds seconds more; a call
        that starts meanwhile (the "stop" word) would otherwise only hear
        the app's hang-up after every queued second has played."""
        # The previous playback's "drained" must not answer for this one.
        session.drained.clear()
        loop = asyncio.get_running_loop()
        started = None
        sent = 0
        async for pcm in pieces:
            if started is None:
                started = loop.time()
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

    async def _quiet(self, session: Session, close: bool) -> None:
        """Stop the thinking sound, if it is playing (``Thinking.stop``)."""
        call = session.call
        if call is not None and call.thinking is not None:
            await call.thinking.stop(close)

    def _wake_json(self, word_id: str | None) -> dict[str, Any]:
        """A call's wake word as commands see it (``wake_word``): its ``id``,
        ``name`` and ``phrase`` -- the phrase it was trained on, for "Okay
        Nabu (Mike)" "okay nabu"."""
        if not word_id:
            return dict(actions.NO_WAKE_WORD)
        word = self.wake_words.get(word_id)
        return {
            "id": word_id,
            "name": word.name if word is not None else None,
            "phrase": word.phrases[0] if word is not None and word.phrases else None,
        }

    def thinking_sound(self) -> bool:
        """settings.json ``thinking_sound``: play it while a reply is slow to
        come (unless false)."""
        return self.store.settings.get("thinking_sound") is not False

    def thinking_delay(self) -> float:
        """settings.json ``thinking_delay``: how long after the command ended
        a reply not yet begun has the thinking sound (default THINKING_DELAY)."""
        delay = self.store.settings.get("thinking_delay")
        return THINKING_DELAY if delay is None else min(max(float(delay), THINKING_DELAYS[0]), THINKING_DELAYS[1])

    async def _hang_up(self, session: Session, reason: str) -> None:
        await self._quiet(session, close=True)  # played out, not cut
        _LOGGER.info("[%s] Hanging up: %s", session, reason)
        await self._send_json(session, Message.HANGUP, {"reason": reason})
        _cancel_stt(session.call)
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
    color = tuple(profile.get("color") or ())
    return {
        "name": name,
        "color": profile.get("color"),
        "color_name": next((n for n, rgb in PALETTE if rgb == color), None),
        "seconds": round(float(profile.get("speech_s") or 0.0), 1),
        "windows": profile.get("n_windows"),
        "learned": profile.get("learned", 0),
        "learned_s": round(float(profile.get("learned_s") or 0.0), 1),
        "enrolled": profile.get("enrolled", []),
        "wake": wake_summary(profile),
    }


