"""Who is speaking: speaker embeddings, household profiles, enrollment.

ReDimNet-b2 (raw 16 kHz waveform in, 192-dim embedding out, mel frontend inside
the graph) run directly on onnxruntime. Ported from whosaidit, where it was
benchmarked on the office-multicap VPE recordings: ~1% EER for four household
speakers on the AGC channel and ~0.5% on AEC, ~98% of household commands accepted
at a threshold that lets 0.5% of an un-enrolled speaker in the same room through.

Audio is cut into 4 s windows at a 2 s hop, each embedded and L2-normalised; a
profile is the mean of its windows' unit vectors, so new speech folds in as an
exact weighted average. A typical voice command is one window.

Profiles live in one JSON file shared by every satellite: in benchmarking,
enrolling on one VPE and identifying on another cost almost nothing.

    python -m voice_call.speaker list
    python -m voice_call.speaker enroll Mike call-aec-1791*.wav
    python -m voice_call.speaker color Mike auto
    python -m voice_call.speaker rename Mike Michael
    python -m voice_call.speaker forget Mike
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

_LOGGER = logging.getLogger(__name__)

SAMPLE_RATE = 16000
WINDOW_SECONDS, HOP_SECONDS, MIN_SPEECH_SECONDS = 4.0, 2.0, 0.6
MODEL_NAME = "redimnet-b2"

# Colors for the LED ring, assigned to new speakers in order. Saturated hues read
# best on WS2812s. Red is left out (it means error/mute on the VPE) and so is the
# ring's default cyan, which marks "nobody recognized".
PALETTE: list[tuple[str, tuple[int, int, int]]] = [
    ("orange", (255, 96, 0)),
    ("green", (0, 255, 32)),
    ("violet", (128, 0, 255)),
    ("yellow", (255, 200, 0)),
    ("magenta", (255, 0, 160)),
    ("blue", (0, 40, 255)),
    ("lime", (140, 255, 0)),
    ("pink", (255, 80, 120)),
    ("teal", (0, 255, 160)),
    ("white", (255, 255, 255)),
]

# "learn a new voice for Mike". Trained into the recognizer alongside Home
# Assistant's wildcard sentence triggers, but handled here, never sent to HA.
ENROLL_SENTENCES = ["learn [a] [new] voice for {speaker}"]
_ENROLL_TEXT = re.compile(r"^\s*learn\s+(?:a\s+)?(?:new\s+)?voice\s+for\s+(.+?)\s*$", re.I)


def enroll_name(text: str | None) -> str | None:
    """The speaker name if ``text`` is the enroll command, else None."""
    match = _ENROLL_TEXT.match(text or "")
    if match is None:
        return None
    name = re.sub(r"[^\w' -]+", "", match.group(1)).strip()
    return " ".join(word.capitalize() for word in name.split()) or None


def _unit(v: np.ndarray) -> np.ndarray:
    return v / (np.linalg.norm(v, axis=-1, keepdims=True) + 1e-12)


def pcm_to_float(pcm: bytes) -> np.ndarray:
    return np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768


def trim_silence(audio: np.ndarray) -> np.ndarray:
    """Drop leading/trailing non-speech using a relative energy floor."""
    if len(audio) < 800:
        return audio
    n = (len(audio) - 400) // 160 + 1
    frames = np.lib.stride_tricks.as_strided(
        audio, (n, 400), (audio.strides[0] * 160, audio.strides[0])
    )
    energy = 20 * np.log10(np.sqrt((frames**2).mean(1)) + 1e-10)
    keep = energy > max(np.percentile(energy, 95) - 25, np.percentile(energy, 10) + 6)
    idx = np.where(keep)[0]
    return audio[idx[0] * 160 : idx[-1] * 160 + 400] if len(idx) > 1 else audio


@dataclass
class Utterance:
    """One utterance's embedding: per-window unit vectors and their mean."""

    windows: np.ndarray  # [n, dim]
    speech_s: float

    @property
    def vector(self) -> np.ndarray:
        return _unit(self.windows.mean(0))


@dataclass
class Identification:
    utterance: Utterance | None
    name: str | None = None
    """Accepted speaker, or None for unknown / too little speech."""
    best: str | None = None
    """Best-matching profile whether or not it was accepted."""
    score: float = 0.0
    margin: float = 0.0
    scores: dict[str, float] = field(default_factory=dict)


class Embedder:
    def __init__(self, model_path: Path, threads: int = 2) -> None:
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        self._session = ort.InferenceSession(
            str(model_path), options, providers=["CPUExecutionProvider"]
        )
        self._input = self._session.get_inputs()[0].name

    def _one(self, audio: np.ndarray) -> np.ndarray:
        out = self._session.run(None, {self._input: audio[np.newaxis, np.newaxis, :]})
        return _unit(np.asarray(out[0], dtype=np.float64).ravel())

    def embed(self, audio: np.ndarray) -> Utterance | None:
        """None if there is too little speech to embed."""
        audio = trim_silence(audio)
        speech_s = len(audio) / SAMPLE_RATE
        if speech_s < MIN_SPEECH_SECONDS:
            return None
        win, hop = int(WINDOW_SECONDS * SAMPLE_RATE), int(HOP_SECONDS * SAMPLE_RATE)
        starts = [0] if len(audio) <= win else list(range(0, len(audio) - win + 1, hop))
        vecs = [self._one(audio[s : s + win]) for s in starts]
        if len(audio) > win and len(audio) - (starts[-1] + win) > MIN_SPEECH_SECONDS * SAMPLE_RATE:
            vecs.append(self._one(audio[-win:]))
        return Utterance(np.array(vecs), speech_s)


class Profiles:
    """speakers.json: one centroid per person, plus their LED color."""

    def __init__(self, path: Path, channel: str) -> None:
        self.path = path
        self.channel = channel
        self.speakers: dict[str, dict] = {}
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("model", MODEL_NAME) != MODEL_NAME:
                raise ValueError(f"{path} was built with {data.get('model')!r}, not {MODEL_NAME}")
            stored = data.get("channel", channel)
            if stored != channel and data.get("speakers"):
                # Profiles from one capture path score a different one poorly,
                # and the error looks like a bad threshold rather than this.
                _LOGGER.warning(
                    "%s was enrolled on the %s channel but identification uses %s; "
                    "scores will be lower until profiles are re-enrolled or have learned",
                    path, stored, channel,
                )
            self.speakers = data.get("speakers", {})

    def save(self) -> None:
        # write-then-rename: learning saves mid-call, and a crash must not leave a
        # truncated file
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(
            json.dumps({"model": MODEL_NAME, "channel": self.channel, "speakers": self.speakers}, indent=1),
            encoding="utf-8",
        )
        os.replace(tmp, self.path)

    def matrix(self) -> tuple[list[str], np.ndarray | None]:
        names = sorted(self.speakers)
        if not names:
            return names, None
        return names, _unit(np.array([self.speakers[n]["centroid"] for n in names]))

    def find(self, name: str) -> str | None:
        """Existing key for ``name``, case-insensitively."""
        return next((k for k in self.speakers if k.lower() == name.lower()), None)

    def color(self, name: str | None) -> tuple[int, int, int] | None:
        if name is None or name not in self.speakers:
            return None
        return tuple(self.speakers[name]["color"])

    def next_color(self) -> tuple[int, int, int]:
        used = {tuple(p["color"]) for p in self.speakers.values()}
        for _, rgb in PALETTE:
            if rgb not in used:
                return rgb
        return PALETTE[len(self.speakers) % len(PALETTE)][1]

    def set(self, name: str, windows: np.ndarray, speech_s: float, color=None) -> dict:
        """Create or replace a profile. A re-enrolled speaker keeps their color."""
        old = self.speakers.pop(self.find(name) or name, None)
        rgb = color or (old["color"] if old else None) or self.next_color()
        cen = windows.mean(0)
        u = _unit(cen)
        self.speakers[name] = {
            "centroid": cen.tolist(),
            "n_windows": len(windows),
            "speech_s": speech_s,
            "cohesion": float(np.mean(windows @ u)),
            "color": list(rgb),
            "created": time.time(),
            "learned": 0,
            "learned_s": 0.0,
        }
        return self.speakers[name]

    def learn(self, name: str, utt: Utterance, cap: int) -> None:
        """Fold an utterance in. The existing centroid counts as at most ``cap``
        windows, so the profile keeps following slow changes in voice and room."""
        p = self.speakers[name]
        n, k = p["n_windows"], len(utt.windows)
        w = min(n, cap)
        p["centroid"] = ((np.array(p["centroid"]) * w + utt.windows.sum(0)) / (w + k)).tolist()
        p["n_windows"] = n + k
        p["speech_s"] += utt.speech_s
        p["learned"] = p.get("learned", 0) + 1
        p["learned_s"] = p.get("learned_s", 0.0) + utt.speech_s


@dataclass
class Enrollment:
    """Collecting one new speaker's voice from their next few ordinary commands."""

    name: str
    color: tuple[int, int, int]
    needed: int
    deadline: float
    utterances: list[Utterance] = field(default_factory=list)

    @property
    def expired(self) -> bool:
        return time.monotonic() > self.deadline

    def consistent(self, min_sim: float) -> list[Utterance]:
        """The largest group that sounds like one person: drop, one at a time, the
        utterance least similar to the rest until every remaining one is at least
        ``min_sim`` (cosine) from the others' mean. Guards against someone else
        speaking in between."""
        keep = list(self.utterances)
        while len(keep) > 1:
            V = np.array([u.vector for u in keep])
            sims = [float(V[i] @ _unit(np.delete(V, i, 0).mean(0))) for i in range(len(keep))]
            worst = int(np.argmin(sims))
            if sims[worst] >= min_sim:
                break
            keep.pop(worst)
        return keep


class SpeakerId:
    def __init__(
        self,
        model_path: Path,
        profiles_path: Path,
        *,
        channel: str = "aec",
        threshold: float = 0.45,
        learn: bool = True,
        learn_margin: float = 0.05,
        learn_min_speech: float = 2.0,
        learn_min_gap: float = 0.1,
        learn_cap: int = 100,
        enroll_commands: int = 5,
        enroll_timeout: float = 600.0,
        enroll_min_sim: float = 0.35,
        threads: int = 2,
    ) -> None:
        self.model_path = model_path
        self.profiles = Profiles(profiles_path, channel)
        self.channel = channel
        self.threshold = threshold
        self.learn_enabled = learn
        self.learn_margin = learn_margin
        self.learn_min_speech = learn_min_speech
        self.learn_min_gap = learn_min_gap
        self.learn_cap = learn_cap
        self.enroll_commands = enroll_commands
        self.enroll_timeout = enroll_timeout
        self.enroll_min_sim = enroll_min_sim
        self.threads = threads
        self._embedder: Embedder | None = None
        # One utterance at a time: onnxruntime sessions are thread-safe, but two
        # satellites embedding at once would just split the same cores.
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        self._embedder = await asyncio.to_thread(Embedder, self.model_path, self.threads)
        _LOGGER.info(
            "Speaker ID: %s on %s, threshold %.2f, %d profile(s): %s",
            MODEL_NAME, self.channel, self.threshold, len(self.profiles.speakers),
            ", ".join(sorted(self.profiles.speakers)) or "none yet",
        )

    async def identify(self, pcm: bytes) -> Identification:
        assert self._embedder is not None, "start() first"
        async with self._lock:
            utt = await asyncio.to_thread(self._embedder.embed, pcm_to_float(pcm))
        if utt is None:
            return Identification(None)
        names, P = self.profiles.matrix()
        if P is None:
            return Identification(utt)
        scores = P @ utt.vector
        order = np.argsort(-scores)
        best = names[order[0]]
        score = float(scores[order[0]])
        margin = float(score - scores[order[1]]) if len(order) > 1 else float("inf")
        return Identification(
            utt,
            name=best if score >= self.threshold else None,
            best=best,
            score=score,
            margin=margin,
            scores={n: round(float(s), 3) for n, s in zip(names, scores)},
        )

    def maybe_learn(self, ident: Identification) -> bool:
        """Fold a convincing identification into its profile. Only an utterance that
        clears every gate on its own is learned from, so one doubtful call cannot
        drag a profile toward someone else."""
        if not (self.learn_enabled and ident.name and ident.utterance):
            return False
        if (
            ident.score < self.threshold + self.learn_margin
            or ident.utterance.speech_s < self.learn_min_speech
            or ident.margin < self.learn_min_gap
        ):
            return False
        self.profiles.learn(ident.name, ident.utterance, self.learn_cap)
        self.profiles.save()
        return True

    def begin_enrollment(self, name: str) -> Enrollment:
        existing = self.profiles.find(name)
        color = self.profiles.color(existing) if existing else self.profiles.next_color()
        return Enrollment(
            name=existing or name,
            color=color,
            needed=self.enroll_commands,
            deadline=time.monotonic() + self.enroll_timeout,
        )

    def add_to_enrollment(self, enrollment: Enrollment, ident: Identification) -> dict | None:
        """Collect one utterance. Returns the new profile once enough consistent
        speech is in, else None."""
        if ident.utterance is None:
            return None
        # Someone already enrolled, recognized with confidence, is not the new
        # speaker -- unless this is a re-enrollment of that same person.
        if ident.name and ident.name != enrollment.name and ident.score >= self.threshold + 0.1:
            _LOGGER.info("Enrollment of %s: skipped an utterance from %s", enrollment.name, ident.name)
            return None
        enrollment.utterances.append(ident.utterance)
        keep = enrollment.consistent(self.enroll_min_sim)
        _LOGGER.info(
            "Enrollment of %s: %d/%d utterances (%d consistent)",
            enrollment.name, len(enrollment.utterances), enrollment.needed, len(keep),
        )
        if len(keep) < enrollment.needed:
            return None
        windows = np.concatenate([u.windows for u in keep])
        profile = self.profiles.set(
            enrollment.name, windows, sum(u.speech_s for u in keep), enrollment.color
        )
        self.profiles.save()
        _LOGGER.info(
            "Enrolled %s: %d windows, %.1fs, cohesion %.2f, color %s",
            enrollment.name, profile["n_windows"], profile["speech_s"], profile["cohesion"],
            profile["color"],
        )
        return profile


# ---------------------------------------------------------------- CLI


def _read_wav(path: Path) -> np.ndarray:
    import soundfile as sf
    import soxr

    audio, rate = sf.read(str(path), dtype="float32", always_2d=True)
    audio = audio.mean(axis=1)
    return soxr.resample(audio, rate, SAMPLE_RATE) if rate != SAMPLE_RATE else audio


def _rgb(text: str, profiles: Profiles) -> tuple[int, int, int]:
    if text == "auto":
        return profiles.next_color()
    named = dict(PALETTE)
    if text in named:
        return named[text]
    hexcode = text.lstrip("#")
    if re.fullmatch(r"[0-9a-fA-F]{6}", hexcode):
        return tuple(int(hexcode[i : i + 2], 16) for i in (0, 2, 4))
    raise SystemExit(f"color must be auto, #rrggbb or one of: {', '.join(named)}")


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(prog="python -m voice_call.speaker", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profiles", type=Path, default=root / "var/speakers.json")
    parser.add_argument("--model", type=Path, default=root / "models/speaker/redimnet_b2_vox2.onnx")
    parser.add_argument("--channel", default="aec", help="capture channel the profiles belong to")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    e = sub.add_parser("enroll", help="create or replace a profile from audio files")
    e.add_argument("name")
    e.add_argument("files", nargs="+", type=Path)
    e.add_argument("--color", help="auto, #rrggbb or a palette name (default: keep or auto)")
    f = sub.add_parser("forget")
    f.add_argument("name")
    r = sub.add_parser("rename")
    r.add_argument("old")
    r.add_argument("new")
    c = sub.add_parser("color")
    c.add_argument("name")
    c.add_argument("color")
    a = parser.parse_args()

    profiles = Profiles(a.profiles, a.channel)
    if a.cmd == "list":
        names = {rgb: n for n, rgb in PALETTE}
        print(f"{'speaker':<16}{'color':>16}{'windows':>9}{'speech':>9}{'learned':>9}{'cohesion':>10}")
        for n, p in sorted(profiles.speakers.items()):
            rgb = tuple(p["color"])
            print(f"{n:<16}{names.get(rgb, '#%02x%02x%02x' % rgb):>16}{p['n_windows']:>9}"
                  f"{p['speech_s']:>8.0f}s{p.get('learned', 0):>9}{p.get('cohesion', float('nan')):>10.2f}")
        print(f"\n{len(profiles.speakers)} profile(s), channel {profiles.channel}, {a.profiles}")
        return
    if a.cmd == "enroll":
        emb = Embedder(a.model)
        utts = []
        for path in a.files:
            u = emb.embed(_read_wav(path))
            if u is None:
                print(f"  skip {path.name}: too little speech")
            else:
                utts.append(u)
        if not utts:
            raise SystemExit("no usable audio")
        name = profiles.find(a.name) or a.name
        color = _rgb(a.color, profiles) if a.color else None
        p = profiles.set(name, np.concatenate([u.windows for u in utts]),
                         sum(u.speech_s for u in utts), color)
        print(f"{name}: {len(utts)} files, {p['n_windows']} windows, {p['speech_s']:.1f}s, "
              f"cohesion {p['cohesion']:.2f}, color {p['color']}")
        if p["cohesion"] < 0.55:
            print("  warning: low cohesion -- more than one voice, or very different recordings?")
    else:
        key = profiles.find(a.name if a.cmd != "rename" else a.old)
        if key is None:
            raise SystemExit(f"no profile named {a.name if a.cmd != 'rename' else a.old!r}")
        if a.cmd == "forget":
            del profiles.speakers[key]
            print(f"forgot {key}")
        elif a.cmd == "rename":
            profiles.speakers[a.new] = profiles.speakers.pop(key)
            print(f"{key} -> {a.new}")
        elif a.cmd == "color":
            profiles.speakers[key]["color"] = list(_rgb(a.color, profiles))
            print(f"{key}: color {profiles.speakers[key]['color']}")
    profiles.save()


if __name__ == "__main__":
    main()
