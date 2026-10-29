"""French: NVIDIA's FastConformer hybrid (stt_fr_fastconformer_hybrid_large_pc)
for matching, its transducer for writing, Piper's Siwis, and every skill
(skills/). No GLiNER (it doesn't understand French), and no second opinion on
the wake word (not calibrated for this model).

A name may be heard with its article joined ("dans l'entrée": adapt-ctc's
elision, from the language) and in the plural ("les lampes")."""

from pathlib import Path

from .. import Pack

PACK = Pack(
    code="fr",
    name="French",
    ctc="speech-to-phrase/stt_fr_fastconformer_hybrid_large_pc",
    transducer="stt_fr_fastconformer_hybrid_large_pc-transducer",
    transducer_about="stt_fr_fastconformer_hybrid_large_pc, 115M parameters",
    piper="fr_FR-siwis-medium.onnx",
    adapt={
        "language": "fr",
        "filler": [
            "le", "la", "les", "l'", "un", "une", "des", "du", "de", "d'", "mon", "ma", "mes", "dans", "au", "à",
            "s'il te plaît", "s'il vous plaît", "peux tu", "pouvez vous", "est ce que", "tu peux", "vous pouvez",
        ],
        "name_cues": ["le", "la", "les", "mon", "ma", "mes", "nommé", "appelé"],
    },
    skills=("spelling", "dates", "clock", "units", "arithmetic"),
    gliner=False,
    directory=Path(__file__).parent,
)
