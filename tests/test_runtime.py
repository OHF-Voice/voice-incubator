"""onnxruntime sessions that give memory back (adapt_ctc/runtime.py): no CPU
memory arena in any of the app's models, and the heap trimmed after."""

from __future__ import annotations

from pathlib import Path

import pytest

from adapt_ctc.runtime import release_memory, session_options

ROOT = Path(__file__).resolve().parent.parent
TDT = ROOT / "models/parakeet-tdt-int8"
VOICE = ROOT / "models/piper/en_US-ellie-medium.onnx"
SPEAKER = ROOT / "models/speaker/redimnet_b2_vox2.onnx"


def test_session_options():
    options = session_options(3)
    assert options.intra_op_num_threads == 3
    assert options.inter_op_num_threads == 1
    assert options.enable_cpu_mem_arena is False
    release_memory()


@pytest.mark.skipif(not (TDT / "ctc_head.npz").exists(), reason="needs Parakeet's transducer and CTC head")
def test_parakeet_keeps_no_arena():
    from adapt_ctc.hybrid import HybridModel

    model = HybridModel(TDT, 1)
    for session in (model._encoder, model._decoder, model._joiner):
        assert session.get_session_options().enable_cpu_mem_arena is False


@pytest.mark.skipif(not VOICE.exists(), reason="needs the English Piper voice")
def test_piper_keeps_no_arena():
    from voice_call.tts import load_voice

    voice = load_voice(VOICE)
    assert voice.session.get_session_options().enable_cpu_mem_arena is False
    audio = b"".join(chunk.audio_int16_bytes for chunk in voice.synthesize("The lights are on."))
    assert len(audio) > voice.config.sample_rate  # over half a second of speech


@pytest.mark.skipif(not SPEAKER.exists(), reason="needs the speaker ID model")
def test_speaker_id_keeps_no_arena():
    from voice_call.speaker import Embedder

    embedder = Embedder(SPEAKER, 1)
    assert embedder._session.get_session_options().enable_cpu_mem_arena is False
