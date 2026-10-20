"""Names as other languages say them: with an ending ("des Wohnzimmers", "den
kleinen Ventilator"), and joined to what follows ("Küchenlicht")."""

import pytest

from adapt_ctc.acoustic import SPM_SPACE, Vocabulary
from adapt_ctc.config import ConfigError, parse_config
from adapt_ctc.graph import Meaning, PhraseGraph
from adapt_ctc.matcher import Recognizer
from adapt_ctc.morphology import endings, word_forms
from adapt_ctc.spotter import spot

from .conftest import utterance
from .test_matcher import CONFIG, recognize


def test_word_forms():
    german = endings("de")
    assert endings("de-CH") == endings("de_DE") == german and endings("en") == ()
    assert word_forms("kitchen", ()) == ("kitchen",)
    forms = word_forms("kleiner", german)
    # As written first; an ending added, or in place of its own.
    assert forms[0] == "kleiner" and {"kleinen", "kleine", "kleines", "kleinem", "kleiners"} <= set(forms)
    assert {"küchen", "küch"} <= set(word_forms("küche", german))  # Küchenlicht; Schul-
    # Too short to lose an ending: "Bad" keeps its "d".
    assert "bades" in word_forms("bad", german) and not any(len(f) < 3 for f in word_forms("bad", german))


def test_one_lattice_spells_every_form(vocab):
    graph = PhraseGraph(vocab)
    graph.add([word_forms("kitchen", ("s",))], Meaning("name", "kitchen", "area", "kitchen"), flexible=True)
    compiled = graph.compile()
    assert len(compiled.terminals) == 1
    for script, pieces in ((["kitchen"], 1), (["kitchen", "+s"], 2)):
        hits = spot(compiled, vocab, utterance(vocab, script), 0.08, 2.5, 2.0).hits
        # The longer form where both fit: it explains the "s" too.
        assert [(h.phrase, h.tokens, round(h.penalty, 3)) for h in hits] == [("kitchen", pieces, 0.0)]


def test_a_name_heard_with_an_ending():
    script = ["turn", "on", "the", "kitchen", "+s", "lights"]
    plain = recognize(script)
    with_endings = recognize(script, {"name_endings": ["s"]})
    assert with_endings.best.intent == "TurnOnArea" and with_endings.best.slots == {"area": ["kitchen"]}
    # The "s" is the name's: nothing left unexplained, where without endings
    # it is a stray piece.
    assert with_endings.best.cost == 0.0 and plain.best.cost > with_endings.best.cost


def test_name_endings_in_the_config():
    data = {**CONFIG, "language": "de"}
    assert parse_config(data).name_endings == endings("de")
    assert parse_config({**data, "name_endings": ["S", "es"]}).name_endings == ("s", "es")
    assert parse_config({**data, "name_endings": []}).name_endings == ()
    assert parse_config(CONFIG).name_endings == ()
    with pytest.raises(ConfigError, match="name_endings"):
        parse_config({**data, "name_endings": ["-s"]})


# A compound's tail is written by the model as part of the word before it:
# "kitchenlights" is "▁kitchen" then "lights", no word marker.
COMPOUND_VOCAB = [
    "<unk>", *(SPM_SPACE + w for w in "turn on off the kitchen den upstairs lights".split()), "lights", "s", "<blk>"
]
COMPOUND_CONFIG = {
    "names": {"areas": {"kitchen": "Kitchen", "den": "Den"}, "floors": {"upstairs": "Upstairs"}},
    "intents": {
        "LightsOn": {"require": [["turn on"], ["lights"]], "names": {"area": {"count": 1, "optional": True}}},
        "LightsOff": {"require": [["turn off"], ["lights"]], "names": {"floor": {"count": 1, "optional": True}}},
    },
}


def compound(script, **config):
    vocab = Vocabulary(dict(enumerate(COMPOUND_VOCAB)))
    recognizer = Recognizer(parse_config({**COMPOUND_CONFIG, **config}), vocab=vocab)
    return recognizer.recognize_log_probs(utterance(vocab, script), 0.08)


def test_a_compound_of_a_place_and_a_keyword():
    # "turn on the kitchenlights": the lights, in the kitchen.
    script = ["turn", "on", "the", "kitchen", "+lights"]
    assert not compound(script).matches  # "lights" not heard: it starts no word
    found = compound(script, compounds=["lights"]).best
    assert (found.intent, found.slots, found.cost) == ("LightsOn", {"area": ["kitchen"]}, 0.0)
    # A floor too; and with its ending ("kitchens" + lights).
    found = compound(["turn", "off", "the", "upstairs", "+lights"], compounds=["lights"]).best
    assert (found.intent, found.slots) == ("LightsOff", {"floor": ["upstairs"]})
    found = compound(["turn", "on", "the", "kitchen", "+s", "+lights"], compounds=["lights"], name_endings=["s"]).best
    assert found.slots == {"area": ["kitchen"]} and found.cost == 0.0
    # Said as a word of its own, it is the keyword as ever.
    found = compound(["turn", "on", "the", "kitchen", "lights"], compounds=["lights"]).best
    assert (found.intent, found.slots, found.cost) == ("LightsOn", {"area": ["kitchen"]}, 0.0)


def test_a_compound_needs_a_place_in_the_same_word():
    # After a word that is no place ("thelights"), or with another word
    # between it and the place ("kitchen thelights"), "lights" is no tail.
    assert not compound(["turn", "on", "the", "+lights"], compounds=["lights"]).matches
    assert not compound(["turn", "on", "kitchen", "the", "+lights"], compounds=["lights"]).matches


def test_compounds_in_the_config():
    with pytest.raises(ConfigError, match="no intent's keyword"):
        parse_config({**COMPOUND_CONFIG, "compounds": ["lamp"]})
    with pytest.raises(ConfigError, match="one word each"):
        parse_config({**COMPOUND_CONFIG, "compounds": ["the lights"]})
    assert parse_config({**COMPOUND_CONFIG, "compounds": ["Lights", "lights"]}).compounds == ("lights",)


# German durations: the units' own words, and numbers said only with one.
GERMAN_VOCAB = [
    "<unk>",
    *(SPM_SPACE + w for w in "timer für fünf zwei minuten minute stunde stunden sekunden eine einer eineinhalb halbe und".split()),
    "<blk>",
]
GERMAN_TIMER = {
    "language": "de",
    "intents": {
        "Timer": {
            "require": [["timer"], ["minuten", "minute", "sekunden", "sekunde", "stunden", "stunde"]],
            "optional": [["für"], ["und"]],
            "number": {"min": 0.5, "max": 100, "step": 0.5, "units": ["second", "minute", "hour"], "parts": 3},
        },
    },
}


@pytest.mark.parametrize(
    ("script", "slots"),
    [
        (["timer", "für", "fünf", "minuten"], {"minutes": 5}),
        (["timer", "für", "eine", "stunde"], {"hours": 1}),
        (["timer", "für", "eineinhalb", "stunden"], {"hours": 1.5}),
        (["timer", "für", "eine", "halbe", "stunde"], {"hours": 0.5}),
        (["timer", "für", "eine", "stunde", "und", "zwei", "minuten"], {"hours": 1, "minutes": 2}),
        (["timer", "für", "fünf", "sekunden"], {"seconds": 5}),
    ],
)
def test_german_durations(script, slots):
    vocab = Vocabulary(dict(enumerate(GERMAN_VOCAB)))
    recognizer = Recognizer(parse_config(GERMAN_TIMER), vocab=vocab)
    found = recognizer.recognize_log_probs(utterance(vocab, script), 0.08).best
    assert (found.intent, found.slots, found.unexplained) == ("Timer", slots, [])
