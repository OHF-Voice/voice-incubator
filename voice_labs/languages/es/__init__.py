"""Spanish: NVIDIA's FastConformer hybrid (stt_es_fastconformer_hybrid_large_pc)
for matching, its transducer for writing, Piper's Davefx, and every skill
(skills/). No GLiNER (it doesn't understand Spanish), and no second opinion
on the wake word (not calibrated for this model).

Names are heard with Spanish plurals ("las lámparas", "los ventiladores":
adapt-ctc's ``name_endings``, from the language). A place comes with its
preposition and article ("en la cocina", "la luz del salón"), which are
filler; Spanish joins no room to what is in it, so there are no
``compounds``."""

from pathlib import Path

from .. import Pack

PACK = Pack(
    code="es",
    name="Spanish",
    ctc="speech-to-phrase/stt_es_fastconformer_hybrid_large_pc",
    transducer="stt_es_fastconformer_hybrid_large_pc-transducer",
    transducer_about="stt_es_fastconformer_hybrid_large_pc, 115M parameters",
    piper="es_ES-davefx-medium.onnx",
    adapt={
        "language": "es",
        "filler": [
            "el", "la", "los", "las", "un", "una", "unos", "unas", "en", "de", "del", "a", "al", "mi", "mis", "me",
            "por favor", "puedes", "podrías", "quiero que",
        ],
        "name_cues": ["el", "la", "los", "las", "del", "al", "mi", "mis", "llamado", "llamada"],
    },
    skills=("spelling", "dates", "clock", "units", "arithmetic"),
    gliner=False,
    directory=Path(__file__).parent,
)
