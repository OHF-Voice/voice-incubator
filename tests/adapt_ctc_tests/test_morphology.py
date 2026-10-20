"""Names as other languages say them: with an ending ("des Wohnzimmers", "den
kleinen Ventilator"), and joined to what follows ("Küchenlicht")."""

import pytest

from adapt_ctc.acoustic import SPM_SPACE, Vocabulary
from adapt_ctc.config import ConfigError, parse_config
from adapt_ctc.graph import Meaning, PhraseGraph
from adapt_ctc.matcher import Recognizer
from adapt_ctc.morphology import alternated, alternations, elided, elisions, endings, word_forms
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


def test_a_word_begun_by_a_bare_word_marker():
    # German's vocabulary has no "▁ö": "Öffne" is "▁" "ö" "ff" "ne", typed too.
    vocab = Vocabulary(dict(enumerate(["<unk>", SPM_SPACE, "ö", "ff", "ne", SPM_SPACE + "die", "<blk>"])))
    piece = {p: i for i, p in vocab.id2piece.items()}
    assert vocab.word_pieces("öffne") == [piece[SPM_SPACE], piece["ö"], piece["ff"], piece["ne"]]
    assert vocab.word_pieces("die") == [piece[SPM_SPACE + "die"]] and vocab.word_pieces("x") is None


GERMAN_PLACES_VOCAB = [
    "<unk>", *(SPM_SPACE + w for w in "spiele jazz in im der küche wohnzimmer oben stelle heizung an".split()), "<blk>"
]


def german(config, script):
    vocab = Vocabulary(dict(enumerate(GERMAN_PLACES_VOCAB)))
    recognizer = Recognizer(parse_config({"language": "de", "filler": ["der", "im", "in"], **config}), vocab=vocab)
    return recognizer.recognize_log_probs(utterance(vocab, script), 0.08)


def test_free_text_ends_before_a_german_place():
    config = {
        "names": {"areas": {"kueche": "Küche", "wohnzimmer": "Wohnzimmer"}, "floors": {"oben": "Oben"}},
        "intents": {
            "Play": {
                "require": [["spiele"]],
                "wildcard": {"slot": "query", "after": ["spiele"], "to_end": True},
                "names": {"area": {"count": 1, "optional": True}, "floor": {"count": 1, "optional": True}},
            }
        },
    }
    for script, place in (
        (["spiele", "jazz", "in", "der", "küche"], {"area": ["kueche"]}),
        (["spiele", "jazz", "im", "wohnzimmer"], {"area": ["wohnzimmer"]}),
        (["spiele", "jazz", "oben"], {"floor": ["oben"]}),
    ):
        assert german(config, script).best.slots == {**place, "query": "jazz"}, script


def test_a_word_both_keyword_and_name_is_the_name():
    # "Stelle die Heizung an", a thermostat named Heizung: the device, though
    # "heizung" is the other's keyword and both explain every word.
    config = {
        "names": {"entities": {"climate.heizung": "Heizung"}},
        "intents": {
            "Here": {"require": [["an"]], "optional": [["stelle"], ["heizung"]]},
            "Device": {"require": [["an"]], "optional": [["stelle"]], "names": {"entity": {"count": 1}}},
        },
    }
    result = german(config, ["stelle", "heizung", "an"])
    assert [(m.intent, m.cost) for m in result.matches] == [("Device", 0.0), ("Here", 0.0)]


def test_a_french_name_with_its_article_joined():
    # "dans l'entrée": the model writes "l'entrée" as one word.
    assert elided(("entrée",), elisions("fr")) == ("entrée", "l'entrée", "d'entrée")
    assert elided(("cuisine",), elisions("fr")) == ("cuisine",) and elisions("en") == ()
    vocab = Vocabulary(dict(enumerate(["<unk>", SPM_SPACE + "allume", SPM_SPACE + "la", SPM_SPACE + "lumière",
                                       SPM_SPACE + "de", SPM_SPACE + "l", "'", "entrée", SPM_SPACE + "entrée", "<blk>"])))
    config = {
        "language": "fr",
        "names": {"areas": {"entree": "Entrée"}},
        "intents": {"On": {"require": [["allume"], ["lumière"]], "names": {"area": {"count": 1}}}},
    }
    recognizer = Recognizer(parse_config(config), vocab=vocab)
    found = recognizer.recognize_log_probs(utterance(vocab, ["allume", "la", "lumière", "de", "l", "+'", "+entrée"]), 0.08)
    assert found.best.slots == {"area": ["entree"]}


def test_italian_names_with_their_preposition_and_plural():
    assert {"nell'ingresso", "dell'ingresso"} <= set(elided(("ingresso",), elisions("it")))
    assert "lampade" in word_forms("lampada", endings("it"))


@pytest.mark.parametrize(
    ("language", "name", "said"),
    [
        ("pl", "kuchnia", "kuchni"),
        ("pl", "salon", "salonie"),
        ("pl", "łazienka", "łazience"),
        ("pl", "biuro", "biurze"),
        ("pl", "pokój", "pokoju"),
        ("pl", "gabinet", "gabinecie"),
        ("ru", "кухня", "кухне"),
        ("ru", "гостиная", "гостиной"),
        ("ru", "этаж", "этаже"),
        ("uk", "вітальня", "вітальні"),
        ("uk", "ванна", "ванній"),
        ("uk", "поверх", "поверсі"),
    ],
)
def test_a_slavic_name_in_its_cases(language, name, said):
    # "W kuchni", "в гостиной", "на першому поверсі": endings, and the
    # consonant before them changed ("łazience", "поверсі").
    assert said in alternated(word_forms(name, endings(language)), alternations(language))
