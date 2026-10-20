"""The acoustic front end: audio in, per-frame CTC log-probabilities out.

Vendored from speech-to-phrase (Apache-2.0, The Home Assistant Authors) so this
package needs neither its native OpenFST extension nor librosa. The mel front
end reproduces NeMo's ``AudioToMelSpectrogramPreprocessor``, which is not part
of the exported ONNX graph.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

try:  # librosa's FFT backend; numpy.fft differs from it in the last bit.
    from scipy.fft import rfft as _rfft
except ImportError:  # pragma: no cover - exercised only without scipy
    _rfft = np.fft.rfft

SAMPLE_RATE = 16000
SPM_SPACE = "▁"
"""SentencePiece word-start marker."""

_BLANK_PIECES = ("<blk>", "<blank>", "<pad>")
_UNK_PIECES = ("<unk>", "<UNK>")
_PUNCTUATION = frozenset(".,?!;:")
"""Pieces the model emits for sentence punctuation, with or without the word
marker ("." in Parakeet's English, "▁." in NeMo's German). They are not
speech, so the recognizer treats them as equivalent to blank."""


def _is_punctuation(piece: str) -> bool:
    return piece.lstrip(SPM_SPACE) in _PUNCTUATION

# Typed text as CTC evidence (``Vocabulary.text_log_probs``): frames per
# piece, blank frames between words and before and after them, and the
# logit of every token but the one heard.
TYPED_PIECE_FRAMES = 2
TYPED_WORD_GAP = 2
TYPED_SILENCE = 4
TYPED_OTHER = -20.0

# Slaney's mel scale, as in librosa: linear below 1 kHz, logarithmic above.
_F_SP = 200.0 / 3.0
_MIN_LOG_HZ = 1000.0
_MIN_LOG_MEL = _MIN_LOG_HZ / _F_SP
_LOGSTEP = float(np.log(6.4) / 27.0)


def _hz_to_mel(freq: np.ndarray) -> np.ndarray:
    freq = np.asarray(freq, dtype=np.float64)
    return np.where(
        freq >= _MIN_LOG_HZ,
        _MIN_LOG_MEL + np.log(np.maximum(freq, _MIN_LOG_HZ) / _MIN_LOG_HZ) / _LOGSTEP,
        freq / _F_SP,
    )


def _mel_to_hz(mel: np.ndarray) -> np.ndarray:
    mel = np.asarray(mel, dtype=np.float64)
    return np.where(
        mel >= _MIN_LOG_MEL,
        _MIN_LOG_HZ * np.exp(_LOGSTEP * (mel - _MIN_LOG_MEL)),
        mel * _F_SP,
    )


def _mel_filterbank(sample_rate: int, n_fft: int, n_mels: int) -> np.ndarray:
    """``librosa.filters.mel(htk=False, norm="slaney")``, float32 like librosa."""
    fft_freqs = np.fft.rfftfreq(n_fft, d=1.0 / sample_rate)
    edges = _mel_to_hz(
        np.linspace(_hz_to_mel(0.0), _hz_to_mel(sample_rate / 2.0), n_mels + 2)
    )
    widths = np.diff(edges)
    ramps = np.subtract.outer(edges, fft_freqs)
    weights = np.zeros((n_mels, fft_freqs.size), dtype=np.float32)
    for i in range(n_mels):
        rising = -ramps[i] / widths[i]
        falling = ramps[i + 2] / widths[i + 1]
        weights[i] = np.maximum(0.0, np.minimum(rising, falling))
    weights *= (2.0 / (edges[2 : n_mels + 2] - edges[:n_mels]))[:, np.newaxis]
    return weights


@dataclass
class MelFeaturizer:
    """NeMo's mel preprocessor: 80 bins, 25 ms window, 10 ms hop."""

    sample_rate: int = SAMPLE_RATE
    n_mels: int = 80
    n_fft: int = 512
    win_length: int = 400
    hop_length: int = 160
    preemph: float = 0.97
    log_zero_guard: float = 2.0**-24
    norm_eps: float = 1e-5
    _mel_fb: np.ndarray = field(init=False, repr=False)
    _window: np.ndarray = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._mel_fb = _mel_filterbank(self.sample_rate, self.n_fft, self.n_mels)
        window = np.hanning(self.win_length + 1)[:-1].astype(np.float32)
        left = (self.n_fft - self.win_length) // 2
        self._window = np.pad(window, (left, self.n_fft - self.win_length - left))

    def __call__(self, audio: np.ndarray) -> np.ndarray:
        """Log-mel features ``[n_mels, frames]``, normalized per feature."""
        x = np.asarray(audio, dtype=np.float32).reshape(-1)
        if x.size == 0:
            raise ValueError("Audio is empty")
        x = np.concatenate(([x[0]], x[1:] - self.preemph * x[:-1])).astype(np.float32)

        # Centred, reflect-padded STFT, as librosa.stft does it.
        pad = self.n_fft // 2
        padded = np.pad(x, (pad, pad), mode="reflect")
        frames = np.lib.stride_tricks.sliding_window_view(padded, self.n_fft)[
            :: self.hop_length
        ]
        power = np.abs(_rfft(frames * self._window, axis=-1).T) ** 2.0

        feats = np.log(self._mel_fb @ power + self.log_zero_guard)
        mean = feats.mean(axis=1, keepdims=True)
        std = feats.std(axis=1, ddof=1, keepdims=True)
        return ((feats - mean) / (std + self.norm_eps)).astype(np.float32)


class Vocabulary:
    """The acoustic model's SentencePiece pieces, indexed for lattice building.

    No SentencePiece model ships with the ONNX export, and none is needed: the
    recognizer never commits to one segmentation of a phrase. It accepts every
    sequence of pieces that spells it, so whichever one the model prefers to
    emit is the one that gets scored.
    """

    def __init__(self, id2piece: dict[int, str]) -> None:
        self.id2piece = dict(id2piece)
        blank = next((i for i, p in self.id2piece.items() if p in _BLANK_PIECES), None)
        self.blank_id = max(self.id2piece) + 1 if blank is None else blank
        self.size = max(max(self.id2piece), self.blank_id) + 1

        self.punctuation_ids = sorted(
            i for i, p in self.id2piece.items() if _is_punctuation(p)
        )
        self.space_id: int | None = None
        # (lowercase spelling, starts a word) -> token ids. Case is folded
        # because the model capitalizes freely: "▁T urn" at the start of a
        # command, "▁L iv ing ▁R oo m" for anything it takes for a name.
        self.pieces: dict[tuple[str, bool], list[int]] = {}
        self.characters: set[str] = set()
        for token_id, piece in self.id2piece.items():
            if (
                token_id == self.blank_id
                or piece in _UNK_PIECES
                or _is_punctuation(piece)
            ):
                continue
            starts_word = piece.startswith(SPM_SPACE)
            core = piece[1:] if starts_word else piece
            if not core:
                if starts_word:
                    self.space_id = token_id
                continue
            core = core.lower()
            self.pieces.setdefault((core, starts_word), []).append(token_id)
            self.characters.update(core)
        self.max_piece_length = max(len(core) for core, _ in self.pieces)

    @classmethod
    def from_tokens_txt(cls, path: Path) -> "Vocabulary":
        id2piece: dict[int, str] = {}
        with open(path, encoding="utf-8") as tokens_file:
            for line in tokens_file:
                piece, _, id_str = line.rstrip("\n").rpartition(" ")
                if piece:
                    id2piece[int(id_str)] = piece
        if not id2piece:
            raise ValueError(f"No tokens in {path}")
        return cls(id2piece)

    def starts_word(self, token_id: int) -> bool:
        return self.id2piece.get(token_id, "").startswith(SPM_SPACE)

    def is_punctuation(self, token_id: int) -> bool:
        return _is_punctuation(self.id2piece.get(token_id, ""))

    def ids_to_text(self, ids: list[int]) -> str:
        text = "".join(
            self.id2piece[i] for i in ids if i != self.blank_id and i in self.id2piece
        )
        return text.replace(SPM_SPACE, " ").strip()

    def word_pieces(self, word: str) -> list[int] | None:
        """``word`` (lowercase) as few of this vocabulary's pieces as spell
        it, the first one starting a word; None when no sequence does."""
        best: list[list[int] | None] = [None] * (len(word) + 1)
        best[0] = []
        for start in range(len(word)):
            if best[start] is None:
                continue
            for end in range(start + 1, min(len(word), start + self.max_piece_length) + 1):
                ids = self.pieces.get((word[start:end], start == 0))
                if ids is None:
                    continue
                candidate = best[start] + [ids[0]]
                if best[end] is None or len(candidate) < len(best[end]):
                    best[end] = candidate
        return best[len(word)]

    def text_log_probs(self, words: list[str]) -> tuple[np.ndarray, list[str]]:
        """CTC evidence for ``words`` (spoken, lowercase) as if each had been
        heard without a doubt: every piece certain for two frames, blanks
        between pieces, longer between words and around them. Also the
        words that no pieces spell (left out)."""
        frames: list[int] = [self.blank_id] * TYPED_SILENCE
        unspellable = []
        for word in words:
            pieces = self.word_pieces(word) or self.word_pieces(_plain_letters(word))
            if pieces is None:
                unspellable.append(word)
                continue
            for piece in pieces:
                frames += [piece] * TYPED_PIECE_FRAMES + [self.blank_id]
            frames += [self.blank_id] * (TYPED_WORD_GAP - 1)
        frames += [self.blank_id] * TYPED_SILENCE
        logits = np.full((len(frames), self.size), TYPED_OTHER, dtype=np.float32)
        logits[np.arange(len(frames)), frames] = 0.0
        log_probs = logits - np.log(np.exp(logits).sum(axis=1, keepdims=True))
        return np.ascontiguousarray(log_probs, dtype=np.float32), unspellable


def _plain_letters(word: str) -> str:
    """"café" -> "cafe": the letters without their accents."""
    return "".join(ch for ch in unicodedata.normalize("NFKD", word) if not unicodedata.combining(ch))


class CtcModel:
    """A NeMo CTC model exported to ONNX (``model.onnx`` + ``tokens.txt``)."""

    def __init__(self, model_dir: Path, num_threads: int = 4) -> None:
        import onnxruntime as ort  # pylint: disable=import-outside-toplevel

        model_dir = Path(model_dir)
        self.model_dir = model_dir.resolve()
        onnx_files = sorted(model_dir.glob("*.onnx"))
        if not onnx_files:
            raise FileNotFoundError(f"No .onnx model in {model_dir}")
        self.vocab = Vocabulary.from_tokens_txt(model_dir / "tokens.txt")

        options = ort.SessionOptions()
        options.inter_op_num_threads = 1
        options.intra_op_num_threads = num_threads
        self._session = ort.InferenceSession(
            str(onnx_files[0]), sess_options=options, providers=["CPUExecutionProvider"]
        )
        inputs = self._session.get_inputs()
        self._signal_name = inputs[0].name
        self._length_name = inputs[1].name
        self._featurizer = MelFeaturizer()
        self._frame_seconds: float | None = None

    @property
    def frame_seconds(self) -> float:
        """Seconds per output frame (from a second of silence, once)."""
        if self._frame_seconds is None:
            self._frame_seconds = self.log_probs(np.zeros(SAMPLE_RATE, dtype=np.float32))[1]
        return self._frame_seconds

    def log_probs(self, audio: np.ndarray) -> tuple[np.ndarray, float]:
        """``[T, V]`` natural-log probabilities and the seconds per output frame."""
        feats = self._featurizer(audio)
        out = self._session.run(
            None,
            {
                self._signal_name: feats[np.newaxis],
                self._length_name: np.array([feats.shape[1]], dtype=np.int64),
            },
        )[0][0]
        log_probs = np.ascontiguousarray(out, dtype=np.float32)
        hop = self._featurizer.hop_length / self._featurizer.sample_rate
        subsampling = max(1, round(feats.shape[1] / log_probs.shape[0]))
        return log_probs, hop * subsampling
