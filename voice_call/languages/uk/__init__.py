"""Ukrainian: NVIDIA's FastConformer hybrid (stt_ua_fastconformer_hybrid_large_pc,
named "ua") for matching, its transducer for writing, Piper's Tetiana -- a
"high" voice, as the medium one reads only lowercase letters and drops
digits -- and every skill (skills/). No GLiNER (it doesn't understand
Ukrainian), and no second opinion on the wake word (not calibrated for this
model).

Names are heard in their cases ("у вітальні" for the Вітальня, "на першому
поверсі" for the Перший поверх: adapt-ctc's ``name_endings`` and
alternations, from the language). Ukrainian has no articles: the filler is
prepositions and politeness. The model writes the apostrophe as "'"
("п'ять"), as the library does."""

from pathlib import Path

from .. import Pack

PACK = Pack(
    code="uk",
    name="Ukrainian",
    ctc="speech-to-phrase/stt_ua_fastconformer_hybrid_large_pc",
    transducer="stt_ua_fastconformer_hybrid_large_pc-transducer",
    transducer_about="stt_ua_fastconformer_hybrid_large_pc, 115M parameters",
    piper="uk_UA-tetiana-high.onnx",
    adapt={
        "language": "uk",
        "filler": [
            "в", "у", "на", "до", "з", "із", "зі", "мій", "моя", "моє", "мою", "мої", "моїй", "моєму", "мені",
            "будь ласка", "можеш", "можете", "можна", "будь-ласка", "будь ласочка", "прошу", "а", "ну", "і",
        ],
        "name_cues": ["мій", "моя", "моє", "мою", "моїй", "моєму", "назвою", "назва"],
        # "Ввімкни" (on) can score closer to "вимкни" (off) than to itself on
        # this model: off counts only when it clearly beats on. Said, "вимкни"
        # beats it by 20 nats and more; misheard "ввімкни" by at most 10.
        "contrasts": [{
            "phrases": ["вимкни", "вимкніть", "вимкне", "виключи"],
            "against": ["увімкни", "ввімкни", "вімкни", "увімкніть", "ввімкніть", "увімкне", "ввімкне", "включи"],
            "margin": 12,
        }],
    },
    skills=("spelling", "dates", "clock", "units", "arithmetic"),
    gliner=False,
    directory=Path(__file__).parent,
)
