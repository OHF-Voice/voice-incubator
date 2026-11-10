"""German: NVIDIA's FastConformer hybrid (stt_de_fastconformer_hybrid_large_pc)
for matching, its transducer for writing, Piper's Thorsten, and every skill
(skills/). No GLiNER (it doesn't understand German), and no second opinion
on the wake word (not calibrated for this model).

Names are heard with German's endings ("des Wohnzimmers", "den kleinen
Ventilator": adapt-ctc's ``name_endings``, from the language) and a room
joined to what is in it ("Küchenlicht": ``compounds``)."""

from pathlib import Path

from .. import Pack

PACK = Pack(
    code="de",
    name="German",
    ctc="speech-to-phrase/stt_de_fastconformer_hybrid_large_pc",
    transducer="stt_de_fastconformer_hybrid_large_pc-transducer",
    transducer_about="stt_de_fastconformer_hybrid_large_pc, 115M parameters",
    piper="de_DE-thorsten-medium.onnx",
    adapt={
        "language": "de",
        "filler": [
            "der", "die", "das", "den", "dem", "des", "bitte", "mal", "doch", "im", "in", "am", "beim",
            "vom", "zum", "zur", "ins", "mir", "mein", "meine", "meinen", "eine", "einen", "es",
            "kannst du", "könntest du", "würdest du",
        ],
        "name_cues": ["der", "die", "das", "den", "dem", "des", "mein", "meine", "meinen", "meinem", "namens"],
        # Keywords that may end a compound after a room or floor: "das
        # Küchenlicht", "die Wohnzimmerlampe", "die Schlafzimmerrollläden".
        # Only those a home's commands have count (commands.adapt_config).
        "compounds": [
            "licht", "lichter", "lampe", "lampen", "beleuchtung", "rollladen", "rollläden", "rollo", "rollos",
            "jalousie", "jalousien", "markise", "vorhang", "vorhänge", "fenster", "heizung", "temperatur",
            "musik", "lautsprecher",
        ],
    },
    skills=("spelling", "dates", "clock", "units", "arithmetic"),
    gliner=False,
    directory=Path(__file__).parent,
)
