"""Dutch: NVIDIA's FastConformer hybrid (stt_nl_fastconformer_hybrid_large_pc)
for matching, its transducer for writing, Piper's Ronnie, and every skill
(skills/). No GLiNER (it doesn't understand Dutch), and no second opinion on
the wake word (not calibrated for this model).

Names are heard with Dutch endings ("de grote lamp", "de lampen": adapt-ctc's
``name_endings``, from the language) and a room joined to what is in it
("de keukenlamp", "het woonkamerlicht": ``compounds``)."""

from pathlib import Path

from .. import Pack

PACK = Pack(
    code="nl",
    name="Dutch",
    ctc="speech-to-phrase/stt_nl_fastconformer_hybrid_large_pc",
    transducer="stt_nl_fastconformer_hybrid_large_pc-transducer",
    transducer_about="stt_nl_fastconformer_hybrid_large_pc, 115M parameters",
    piper="nl_NL-ronnie-medium.onnx",
    adapt={
        "language": "nl",
        "filler": [
            "de", "het", "een", "alsjeblieft", "alstublieft", "even", "eens", "mijn", "m'n", "in", "op", "van",
            "kun je", "kunt u", "wil je", "wilt u", "zou je", "graag",
        ],
        "name_cues": ["de", "het", "mijn", "genaamd"],
        # Keywords that may end a compound after a room or floor: "de
        # keukenlamp", "het woonkamerlicht", "de slaapkamerverlichting" -- and
        # a separable verb's particle the model writes onto the place before
        # it ("op de begane gronduit"). Only those a home's commands have
        # count (commands.adapt_config).
        "compounds": [
            "licht", "lichten", "lamp", "lampen", "verlichting", "verwarming", "temperatuur", "muziek", "speaker",
            "luidspreker", "gordijnen", "rolluiken", "raam", "aan", "uit", "dicht", "open",
        ],
    },
    skills=("spelling", "dates", "clock", "units", "arithmetic"),
    gliner=False,
    directory=Path(__file__).parent,
)
