"""Italian: NVIDIA's FastConformer hybrid (stt_it_fastconformer_hybrid_large_pc)
for matching, its transducer for writing, Piper's Serena, and every skill
(skills/). No GLiNER (it doesn't understand Italian), and no second opinion
on the wake word (not calibrated for this model).

A name may be heard with its article or preposition joined ("nell'ingresso",
"dell'ufficio": adapt-ctc's elision, from the language), and with Italian's
plural and agreement ("le lampade", "la lampada piccola": ``name_endings``)."""

from pathlib import Path

from .. import Pack

PACK = Pack(
    code="it",
    name="Italian",
    ctc="speech-to-phrase/stt_it_fastconformer_hybrid_large_pc",
    transducer="stt_it_fastconformer_hybrid_large_pc-transducer",
    transducer_about="stt_it_fastconformer_hybrid_large_pc, 115M parameters",
    piper="it_IT-serena-medium.onnx",
    adapt={
        "language": "it",
        "filler": [
            "il", "lo", "la", "l'", "i", "gli", "le", "un", "uno", "una", "un'", "di", "del", "della", "dello", "dell'",
            "in", "nel", "nella", "nello", "nell'", "a", "al", "alla", "allo", "all'", "mi", "mio", "mia", "miei", "mie",
            "per favore", "per piacere", "puoi", "potresti", "vorrei",
        ],
        "name_cues": ["il", "lo", "la", "i", "gli", "le", "mio", "mia", "chiamato", "chiamata"],
    },
    skills=("spelling", "dates", "clock", "units", "arithmetic"),
    gliner=False,
    directory=Path(__file__).parent,
)
