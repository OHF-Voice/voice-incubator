"""English: Parakeet (parakeet-tdt_ctc-110m) for matching and writing, Piper's
Jenny, every skill, and GLiNER."""

from pathlib import Path

from .. import Pack

PACK = Pack(
    code="en",
    name="English",
    ctc="speech-to-phrase/stt_en_parakeet_tdt_ctc_110m",
    transducer="parakeet-tdt",
    transducer_about="parakeet-tdt_ctc-110m, 110M parameters",
    piper="en_GB-jenny_dioco-medium.onnx",
    adapt={
        "language": "en",
        "filler": ["the", "a", "an", "please", "my", "can you", "could you", "would you", "it", "of"],
        "name_cues": ["the", "my", "named", "called"],
    },
    skills=("spelling", "dates", "clock", "units", "arithmetic"),
    gliner=True,
    wake_ratio=True,
    # LibriTTS-R: 2,306 speakers, for the positives of a wake word trained here.
    wake_voice="en_US-libritts_r-medium.onnx",
    directory=Path(__file__).parent,
)
