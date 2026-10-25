"""A hybrid TDT-CTC model with one encoder: the CTC head adapt-ctc matches on
and the TDT head that writes transcripts.

NeMo's hybrid models (Parakeet's ``parakeet-tdt_ctc-110m``, the FastConformer
``*_hybrid_large_pc``) are exported twice: the CTC branch alone (``model.onnx``)
and the transducer alone (sherpa-onnx's ``encoder``/``decoder``/``joiner``).
Each holds the same encoder, loaded twice: 486 MB at its peak with the CTC model,
720 MB with the transducer as well. The CTC branch adds to that encoder only a
1x1 convolution to the vocabulary and a log-softmax; with it taken out of the
CTC model (``ctc_head.npz``, beside the transducer), the transducer's encoder
gives the CTC model's log-probabilities to the last digit, and one encoder is
loaded.

The transcript is then decoded here, greedily, as sherpa-onnx decodes: for a
TDT model (Parakeet), at each frame the joiner's likeliest token and duration;
a token other than blank feeds the decoder; the frame moves on by the duration
(by one after a blank of none), at most MAX_TOKENS_PER_FRAME tokens to a
frame. For a plain RNN-T (the FastConformer hybrids: no durations), tokens
until a blank, then the next frame. Its
features are adapt-ctc's own (sherpa-onnx's differ a little): of 200 prompts
from the office-multicap corpus, 91 came out word for word as sherpa-onnx's,
and the word error rate was the same (32.9% against 32.5%).

    python -m adapt_ctc.hybrid CTC_DIR TDT_DIR    # writes TDT_DIR/ctc_head.npz
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import unicodedata
from pathlib import Path

import numpy as np

from .acoustic import SAMPLE_RATE, MelFeaturizer, Vocabulary
from .runtime import session_options
from .tdt import has_model, model_file

CTC_HEAD = "ctc_head.npz"
MAX_TOKENS_PER_FRAME = 5
"""As sherpa-onnx's greedy search: then the frame moves on regardless."""


def has_shared_model(tdt_dir: Path | None, ctc_dir: Path | None = None) -> bool:
    """Whether ``tdt_dir`` holds a transducer with a CTC head beside it --
    taken from ``ctc_dir``'s model, when that is given."""
    if tdt_dir is None or not has_model(Path(tdt_dir)) or not (Path(tdt_dir) / CTC_HEAD).is_file():
        return False
    if ctc_dir is None:
        return True
    with np.load(Path(tdt_dir) / CTC_HEAD) as head:
        return str(head["ctc_model"]) == Path(ctc_dir).resolve().name


class HybridModel:
    """CtcModel and TdtTranscriber in one: ``vocab``, ``frame_seconds`` and
    ``log_probs`` for matching, ``transcribe`` and ``transcribe_words`` for
    transcripts, one encoder run for both on the same audio."""

    def __init__(self, tdt_dir: Path, num_threads: int = 4) -> None:
        import onnxruntime as ort  # pylint: disable=import-outside-toplevel

        tdt_dir = Path(tdt_dir)
        self.model_dir = tdt_dir.resolve()
        options = session_options(num_threads)

        def session(part: str):
            return ort.InferenceSession(str(model_file(tdt_dir, part)), sess_options=options,
                                        providers=["CPUExecutionProvider"])

        self._encoder, self._decoder, self._joiner = session("encoder"), session("decoder"), session("joiner")
        self._decoder_inputs = [i.name for i in self._decoder.get_inputs()]
        self._hidden = int(self._decoder.get_inputs()[2].shape[-1])
        with np.load(tdt_dir / CTC_HEAD) as head:
            self._weight = np.ascontiguousarray(head["weight"], dtype=np.float32)
            self._bias = np.ascontiguousarray(head["bias"], dtype=np.float32)
        self.vocab = Vocabulary.from_tokens_txt(tdt_dir / "tokens.txt")
        self._pieces = dict(self.vocab.id2piece)
        self._blank = self._weight.shape[0] - 1
        self._durations = int(self._joiner.get_outputs()[0].shape[-1]) - (self._blank + 1)
        """TDT's duration classes (0, 1, 2, ... frames); none for a plain RNN-T."""
        self._featurizer = MelFeaturizer()
        self._frame_seconds: float | None = None
        self._last: tuple[bytes, np.ndarray, float] | None = None
        self.max_tokens_per_frame = MAX_TOKENS_PER_FRAME

    # ------------------------------------------------------------ encoder

    def _encode(self, audio: np.ndarray) -> tuple[np.ndarray, float]:
        """The encoder's output ``[channels, T]`` and seconds per frame; the
        last audio's is kept, so its log-probabilities and its transcript cost
        one encoder run."""
        audio = np.ascontiguousarray(audio, dtype=np.float32)
        key = hashlib.blake2b(audio.tobytes(), digest_size=16).digest()
        if self._last is not None and self._last[0] == key:
            return self._last[1], self._last[2]
        feats = self._featurizer(audio)
        encoded = self._encoder.run(
            None, {"audio_signal": feats[np.newaxis], "length": np.array([feats.shape[1]], dtype=np.int64)}
        )[0][0]
        hop = self._featurizer.hop_length / self._featurizer.sample_rate
        seconds = hop * max(1, round(feats.shape[1] / max(1, encoded.shape[1])))
        self._last = (key, encoded, seconds)
        return encoded, seconds

    # ---------------------------------------------------------------- CTC

    @property
    def frame_seconds(self) -> float:
        """Seconds per output frame (from a second of silence, once)."""
        if self._frame_seconds is None:
            self._frame_seconds = self.log_probs(np.zeros(SAMPLE_RATE, dtype=np.float32))[1]
        return self._frame_seconds

    def log_probs(self, audio: np.ndarray) -> tuple[np.ndarray, float]:
        """``[T, V]`` natural-log probabilities and the seconds per output frame."""
        encoded, seconds = self._encode(audio)
        logits = (self._weight @ encoded + self._bias[:, np.newaxis]).T
        top = logits.max(axis=1, keepdims=True)
        log_probs = logits - top - np.log(np.exp(logits - top).sum(axis=1, keepdims=True))
        return np.ascontiguousarray(log_probs, dtype=np.float32), seconds

    # ---------------------------------------------------------------- TDT

    def _decode(self, token: int, state: tuple[np.ndarray, np.ndarray]):
        names = self._decoder_inputs
        out = self._decoder.run(None, {
            names[0]: np.array([[token]], dtype=np.int32), names[1]: np.array([1], dtype=np.int32),
            names[2]: state[0], names[3]: state[1],
        })
        return out[0], (out[2], out[3])

    def transcribe(self, audio: np.ndarray) -> str:
        return self.transcribe_words(audio)[0]

    def transcribe_words(self, audio: np.ndarray) -> tuple[str, list[tuple[str, float, float]]]:
        """The transcript, and its words with start and end times in seconds,
        as TdtTranscriber gives them."""
        encoded, seconds = self._encode(audio)
        zeros = np.zeros((1, 1, self._hidden), dtype=np.float32)
        decoded, state = self._decode(self._blank, (zeros, zeros.copy()))
        tokens: list[tuple[int, float, float]] = []
        frame, this_frame = 0, 0
        while frame < encoded.shape[1]:
            logits = self._joiner.run(
                None, {"encoder_outputs": encoded[np.newaxis, :, frame : frame + 1], "decoder_outputs": decoded}
            )[0].reshape(-1)
            token = int(np.argmax(logits[: self._blank + 1]))
            if self._durations:
                skip = int(np.argmax(logits[self._blank + 1 :]))
            else:  # RNN-T: the same frame again after a token, the next after a blank
                skip = 0 if token != self._blank else 1
            if token != self._blank:
                # A token's duration: TDT's; a frame where RNN-T has none (as TdtTranscriber takes it).
                tokens.append((token, frame * seconds, (skip if self._durations else 1) * seconds))
                decoded, state = self._decode(token, state)
                this_frame += 1
            if skip > 0:
                this_frame = 0
            if this_frame >= self.max_tokens_per_frame or (token == self._blank and skip == 0):
                this_frame, skip = 0, 1
            frame += skip
        words: list[list] = []
        for token, start, duration in tokens:
            piece = self._pieces.get(token, "")
            if piece.startswith("▁") or not words:
                words.append([piece.lstrip("▁"), start, start + duration])
            else:
                words[-1][0] += piece
                words[-1][2] = start + duration
        # Punctuation that starts a word ("▁," in some vocabularies) follows the
        # word before it in the text, as sherpa-onnx writes it.
        text = "".join(
            piece[1:] if piece.startswith("▁") and _punctuation(piece[1:]) else piece
            for piece in (self._pieces.get(t, "") for t, _, _ in tokens)
        ).replace("▁", " ").strip()
        return text, [(w, s, e) for w, s, e in words if w]


def _punctuation(text: str) -> bool:
    return bool(text) and all(unicodedata.category(ch).startswith("P") for ch in text)


def make_head(ctc_dir: Path, tdt_dir: Path) -> float:
    """``tdt_dir``/ctc_head.npz from the CTC model in ``ctc_dir``: its last
    convolution. Raises ValueError unless the transducer's encoder then gives
    the CTC model's log-probabilities; returns the largest difference."""
    import onnx  # pylint: disable=import-outside-toplevel
    from onnx import numpy_helper  # pylint: disable=import-outside-toplevel

    from .acoustic import CtcModel  # pylint: disable=import-outside-toplevel

    ctc_dir, tdt_dir = Path(ctc_dir), Path(tdt_dir)
    graph = onnx.load(str(sorted(ctc_dir.glob("*.onnx"))[0])).graph
    weights = {i.name: i for i in graph.initializer}
    convs = [n for n in graph.node if n.op_type == "Conv"]
    if not convs or graph.node[-1].op_type != "LogSoftmax":
        raise ValueError(f"{ctc_dir}: no 1x1 convolution and log-softmax at the end")
    conv = convs[-1]
    weight = numpy_helper.to_array(weights[conv.input[1]])[:, :, 0]
    bias = numpy_helper.to_array(weights[conv.input[2]])
    if (ctc_dir / "tokens.txt").read_text("utf-8") != (tdt_dir / "tokens.txt").read_text("utf-8"):
        raise ValueError("The CTC model and the transducer have different tokens")
    head = tdt_dir / CTC_HEAD
    np.savez(head, weight=weight, bias=bias, ctc_model=np.array(ctc_dir.resolve().name))
    try:
        rng = np.random.default_rng(0)
        audio = (rng.standard_normal(SAMPLE_RATE * 3) * 0.05).astype(np.float32)
        expected, _ = CtcModel(ctc_dir, num_threads=2).log_probs(audio)
        got, _ = HybridModel(tdt_dir, num_threads=2).log_probs(audio)
        largest = float(np.abs(expected - got).max()) if expected.shape == got.shape else float("inf")
        if largest > 1e-3:
            raise ValueError(f"The transducer's encoder is not the CTC model's (log-probabilities differ by {largest:g})")
    except Exception:
        head.unlink(missing_ok=True)
        raise
    return largest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m adapt_ctc.hybrid", description=__doc__.split("\n\n")[0])
    parser.add_argument("ctc_dir", type=Path, help="The CTC model (model.onnx, tokens.txt)")
    parser.add_argument("tdt_dir", type=Path, help="The transducer of the same model (encoder, decoder, joiner)")
    args = parser.parse_args(argv)
    try:
        largest = make_head(args.ctc_dir, args.tdt_dir)
    except ValueError as err:
        print(f"{parser.prog}: {err}", file=sys.stderr)
        return 1
    print(f"{args.tdt_dir / CTC_HEAD}: log-probabilities as the CTC model's (largest difference {largest:.2g})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
