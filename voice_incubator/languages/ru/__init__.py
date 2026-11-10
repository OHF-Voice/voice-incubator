"""Russian: NVIDIA's FastConformer hybrid (stt_ru_fastconformer_hybrid_large_pc)
for matching, its transducer for writing, Piper's Denis, and every skill
(skills/). No GLiNER (it doesn't understand Russian), and no second opinion
on the wake word (not calibrated for this model).

Names are heard in their cases ("на кухне" for the Кухня, "в гостиной",
"на первом этаже", "лампу": adapt-ctc's ``name_endings``, from the
language). A place comes with its preposition ("в", "на"), which is filler,
as is "-ка" ("включи-ка свет"); Russian has no articles. The model writes
"ё" as "е", and so do the keywords."""

from pathlib import Path

from .. import Pack

PACK = Pack(
    code="ru",
    name="Russian",
    ctc="speech-to-phrase/stt_ru_fastconformer_hybrid_large_pc",
    transducer="stt_ru_fastconformer_hybrid_large_pc-transducer",
    transducer_about="stt_ru_fastconformer_hybrid_large_pc, 115M parameters",
    piper="ru_RU-denis-medium.onnx",
    adapt={
        "language": "ru",
        "filler": [
            "в", "во", "на", "мне", "нам", "мой", "моя", "мое", "мои", "мою", "моей", "моем", "моего", "моих",
            "пожалуйста", "можешь", "можете", "не мог бы", "не могли бы", "не могла бы", "давай", "ну", "ка",
        ],
        "name_cues": ["мой", "моя", "мое", "мои", "мою", "моей", "моем", "моего", "названием"],
    },
    skills=("spelling", "dates", "clock", "units", "arithmetic"),
    gliner=False,
    directory=Path(__file__).parent,
)
