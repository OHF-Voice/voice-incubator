"""One encoder for Parakeet's CTC and TDT heads (adapt_ctc/hybrid.py): the CTC
model's log-probabilities from the transducer's encoder, transcripts decoded
here, and the engine loading the encoder once."""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest

from adapt_ctc.acoustic import CtcModel
from adapt_ctc.hybrid import CTC_HEAD, HybridModel, has_shared_model, make_head

ROOT = Path(__file__).resolve().parent.parent
CTC = ROOT / "models/speech-to-phrase/stt_en_parakeet_tdt_ctc_110m-int8"
TDT = ROOT / "models/parakeet-tdt-int8"
CALL = ROOT / "calls/call-15788.wav"

pytestmark = pytest.mark.skipif(
    not (CTC / "model.onnx").exists() or not (TDT / "encoder.onnx").exists() or not CALL.exists(),
    reason="needs Parakeet's CTC and TDT models",
)


def transducer(directory: Path, tokens: str | None = None) -> Path:
    """The transducer's files, linked, without a CTC head yet."""
    directory.mkdir()
    for name in ("encoder.onnx", "decoder.onnx", "joiner.onnx"):
        (directory / name).symlink_to(TDT / name)
    (directory / "tokens.txt").write_text(tokens if tokens is not None else (TDT / "tokens.txt").read_text("utf-8"))
    return directory


def audio() -> np.ndarray:
    with wave.open(str(CALL)) as wav:
        return np.frombuffer(wav.readframes(wav.getnframes()), "<i2").astype(np.float32) / 32768


def test_one_encoder_for_both(tmp_path):
    tdt = transducer(tmp_path / "tdt")
    assert not has_shared_model(tdt)
    assert make_head(CTC, tdt) < 1e-3
    assert has_shared_model(tdt, CTC) and not has_shared_model(tdt, tmp_path / "another-ctc-model")

    model, x = HybridModel(tdt, num_threads=2), audio()
    expected, step = CtcModel(CTC, num_threads=2).log_probs(x)
    got, got_step = model.log_probs(x)
    assert got.shape == expected.shape and got_step == step == 0.08
    assert np.abs(got - expected).max() < 1e-3
    assert model.frame_seconds == 0.08 and model.vocab.blank_id == 1024

    text, words = model.transcribe_words(x)
    assert "turn on the lights" in text.lower(), text
    assert [w for w, _, _ in words][-1].lower().startswith("lights")
    assert all(start < end for _, start, end in words)
    assert [start for _, start, _ in words] == sorted(start for _, start, _ in words)


def test_a_transducer_of_another_model_is_refused(tmp_path):
    tokens = (TDT / "tokens.txt").read_text("utf-8").replace("▁the ", "▁teh ", 1)
    tdt = transducer(tmp_path / "tdt", tokens)
    with pytest.raises(ValueError, match="different tokens"):
        make_head(CTC, tdt)
    assert not (tdt / CTC_HEAD).exists()


def test_the_engine_loads_the_encoder_once(tmp_path):
    from voice_labs.engine import Engine  # pylint: disable=import-outside-toplevel

    tdt = transducer(tmp_path / "tdt")
    make_head(CTC, tdt)
    engine = Engine(CTC, tdt, threads=2)
    assert isinstance(engine.model, HybridModel) and engine.transcriber is engine.model
    # Another transducer (or none) for transcripts: the shared one stays the CTC model.
    assert engine.use_tdt(None) and engine.transcriber is None and isinstance(engine.model, HybridModel)
    assert engine.use_tdt(tdt) and engine.transcriber is engine.model
    # Without the head, two models as before.
    (tdt / CTC_HEAD).unlink()
    separate = Engine(CTC, tdt, threads=2)
    assert isinstance(separate.model, CtcModel) and separate.transcriber is not separate.model
