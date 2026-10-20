"""Names as other languages say them: with an ending ("des Wohnzimmers", "den
kleinen Ventilator")."""

import pytest

from adapt_ctc.config import ConfigError, parse_config
from adapt_ctc.graph import Meaning, PhraseGraph
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
