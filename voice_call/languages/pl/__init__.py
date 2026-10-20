"""Polish: NVIDIA's FastConformer hybrid (stt_pl_fastconformer_hybrid_large_pc)
for matching, its transducer for writing, Piper's Gosia, and every skill
(skills/). No GLiNER (it doesn't understand Polish), and no second opinion
on the wake word (not calibrated for this model).

Polish has no articles, and declines names: a room is heard in the case its
preposition takes ("w kuchni" for the Kuchnia, "w łazience" for the
Łazienka, "na pierwszym piętrze" for the Pierwsze piętro), a device in the
accusative ("włącz lampę stojącą"): adapt-ctc's ``name_endings`` and
alternations, from the language. The prepositions are filler; Polish joins
no room to what is in it, so there are no ``compounds``."""

from pathlib import Path

from .. import Pack

PACK = Pack(
    code="pl",
    name="Polish",
    ctc="speech-to-phrase/stt_pl_fastconformer_hybrid_large_pc",
    transducer="stt_pl_fastconformer_hybrid_large_pc-transducer",
    transducer_about="stt_pl_fastconformer_hybrid_large_pc, 115M parameters",
    piper="pl_PL-gosia-medium.onnx",
    adapt={
        "language": "pl",
        "filler": [
            "w", "we", "na", "do", "z", "ze", "mój", "moja", "moje", "moją", "mojej", "moim", "mi", "proszę",
            "możesz", "czy możesz", "mógłbyś", "mogłabyś", "czy mógłbyś", "czy mogłabyś",
        ],
        "name_cues": ["mój", "moja", "moje", "moją", "mojej", "moim"],
    },
    skills=("spelling", "dates", "clock", "units", "arithmetic"),
    gliner=False,
    directory=Path(__file__).parent,
)
