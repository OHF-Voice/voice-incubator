"""voice_call's additions to adapt-ctc: value lists, free text with both
edges, examples first, descriptions and structured rejections."""

import copy

import pytest

from adapt_ctc.acoustic import SPM_SPACE, Vocabulary
from adapt_ctc.config import ConfigError, parse_config
from adapt_ctc.matcher import Recognizer

from .conftest import SUBWORDS, WORDS, utterance

EXTRA = """
add milk bag of rice shopping list remind me take out trash red crimson blue
color movie time
""".split()


def vocab() -> Vocabulary:
    pieces = ["<unk>"]
    pieces += [SPM_SPACE + w for w in dict.fromkeys(WORDS + EXTRA)]
    pieces += [SPM_SPACE + w for w in SUBWORDS if SPM_SPACE + w not in pieces]
    pieces += SUBWORDS + [SPM_SPACE, ".", "'"]
    pieces.append("<blk>")
    return Vocabulary(dict(enumerate(pieces)))


CONFIG = {
    "language": "en",
    "filler": ["the", "a", "please", "to"],
    "numbers": {"detect": {"min": 0, "max": 200}},
    "lists": {"colors": {"red": ["red", "crimson"], "blue": "blue"}},
    "intents": {
        "TurnOn": {
            "description": "turn on the lights",
            "require": [["turn on"]],
            "optional": [["light", "lights"]],
        },
        "SetColor": {
            "description": "change the color of the lights",
            "require": [["set"], ["light", "lights"]],
            "optional": [["color"]],
            "lists": {"color": "colors"},
        },
        "AddItem": {
            "description": "add something to the shopping list",
            "require": [["add"], ["shopping list", "list"]],
            "optional": [["to"]],
            "wildcard": {
                "slot": "item",
                "after": ["add"],
                "before": ["to"],
                "bounded": True,
                "required": True,
                "max_words": 5,
            },
        },
        "Remind": {
            "description": "set a reminder",
            "require": [["remind me"]],
            "optional": [["to"]],
            "wildcard": {"slot": "reminder", "after": ["to"], "to_end": True, "max_words": 8},
        },
        "Brightness": {
            "description": "set the brightness",
            "require": [["brightness"]],
            "optional": [["set"], ["percent"]],
            "number": {"min": 0, "max": 100},
        },
    },
}

# "to" is a filler above, so it can't be a keyword too; the free-text intents
# take it as a keyword instead.
CONFIG["filler"] = ["the", "a", "please"]


def recognize(script, config=CONFIG, **settings):
    data = copy.deepcopy(config)
    data.setdefault("settings", {}).update(settings)
    words = vocab()
    recognizer = Recognizer(parse_config(data), vocab=words)
    return recognizer.recognize_log_probs(utterance(words, script), 0.08)


def rejected(result, intent):
    return next(r for r in result.rejected if r.intent == intent)


@pytest.mark.parametrize(
    ("script", "color"),
    [
        (["set", "the", "lights", "to", "red"], "red"),
        (["set", "the", "light", "color", "to", "crimson"], "red"),
        (["set", "the", "lights", "blue"], "blue"),
    ],
)
def test_list_slot_reports_the_value_of_what_was_said(script, color):
    result = recognize(script)
    assert result.best.intent == "SetColor"
    assert result.best.slots == {"color": color}


def test_required_list_slot_is_missing():
    result = recognize(["set", "the", "lights"])
    assert result.best is None
    missing = rejected(result, "SetColor").missing
    assert missing == [{"kind": "require", "phrases": ["red", "crimson", "blue"], "slot": "color"}]
    assert rejected(result, "SetColor").heard_groups == 2
    assert rejected(result, "SetColor").reasons[0].startswith("required color [red | crimson | blue]")


def test_optional_list_slot():
    data = copy.deepcopy(CONFIG)
    data["intents"]["SetColor"]["lists"] = {"color": {"list": "colors", "optional": True}}
    result = recognize(["set", "the", "lights"], config=data)
    assert (result.best.intent, result.best.slots) == ("SetColor", {})
    result = recognize(["set", "the", "lights", "red"], config=data)
    assert result.best.slots == {"color": "red"}


def test_inline_list_values():
    data = copy.deepcopy(CONFIG)
    data["intents"]["SetColor"]["lists"] = {"color": {"values": ["red", "blue"]}}
    assert recognize(["set", "the", "lights", "blue"], config=data).best.slots == {"color": "blue"}


@pytest.mark.parametrize(
    ("lists", "message"),
    [
        ({"color": "nope"}, "no shared list 'nope'"),
        ({"color": {"values": ["set"]}}, "also a keyword"),
        ({"color": "colors", "other": {"values": ["red"]}}, "also in list 'color'"),
        ({"area": "colors"}, "slot name other than"),
        ({"color": {"list": "colors", "values": ["red"]}}, "either 'list' or 'values'"),
    ],
)
def test_list_errors(lists, message):
    data = copy.deepcopy(CONFIG)
    data["intents"]["SetColor"]["lists"] = lists
    with pytest.raises(ConfigError, match=message):
        parse_config(data)


def test_shared_list_value_said_two_ways_is_an_error():
    data = copy.deepcopy(CONFIG)
    data["lists"]["colors"] = {"red": "red", "blue": "red"}
    with pytest.raises(ConfigError, match="says both"):
        parse_config(data)


@pytest.mark.parametrize(
    ("script", "item"),
    [
        (["add", "milk", "to", "the", "shopping", "list"], "milk"),
        (["add", "the", "milk", "to", "the", "list"], "milk"),
        # Fillers inside the text are part of it; at its edges they are not.
        (["add", "a", "bag", "of", "rice", "to", "the", "shopping", "list"], "bag of rice"),
        (["please", "add", "rice", "to", "the", "list", "please"], "rice"),
    ],
)
def test_bounded_free_text(script, item):
    result = recognize(script)
    assert result.best.intent == "AddItem"
    assert result.best.slots == {"item": item}


def test_required_free_text_is_missing():
    result = recognize(["add", "to", "the", "shopping", "list"])
    assert result.best is None
    assert rejected(result, "AddItem").missing == [{"kind": "text", "slot": "item"}]
    assert rejected(result, "AddItem").reasons == ["no item heard after [add] and before [to]"]


def test_bounded_free_text_needs_both_edges():
    # Nothing after "milk" bounds it, so "milk" stays unexplained.
    result = recognize(["add", "the", "shopping", "list", "milk"])
    assert result.best is None


def test_free_text_to_the_end():
    result = recognize(["remind", "me", "to", "take", "out", "the", "trash"])
    assert result.best.intent == "Remind"
    assert result.best.slots == {"reminder": "take out the trash"}
    assert not result.best.unexplained


@pytest.mark.parametrize(
    ("wildcard", "message"),
    [
        ({"slot": "x", "after": ["add"], "bounded": True}, "needs both 'after' and 'before'"),
        ({"slot": "x", "before": ["to"], "to_end": True}, "needs 'after' keywords"),
        ({"slot": "x", "after": ["add"], "before": ["to"], "bounded": True, "to_end": True}, "either"),
    ],
)
def test_free_text_errors(wildcard, message):
    data = copy.deepcopy(CONFIG)
    data["intents"]["AddItem"]["wildcard"] = wildcard
    with pytest.raises(ConfigError, match=message):
        parse_config(data)


def test_number_and_names_missing_are_structured():
    result = recognize(["set", "the", "brightness"])
    assert result.best is None
    assert rejected(result, "Brightness").missing == [
        {"kind": "number", "range": "0-100", "units": [], "heard": []}
    ]


def test_unexplained_words_are_structured():
    result = recognize(["turn", "on", "movie", "time", "add", "milk"])
    turn_on = rejected(result, "TurnOn")
    assert turn_on.missing[0]["kind"] == "unexplained"
    assert turn_on.heard_groups == 1


def test_description_is_parsed():
    assert parse_config(CONFIG).intents["SetColor"].description == "change the color of the lights"


EXAMPLES = {
    "Movie": {"description": "start movie time", "examples": ["turn on movie time"]},
}


def test_examples_first_wins_over_a_keyword_match():
    data = copy.deepcopy(CONFIG)
    data["intents"].update(copy.deepcopy(EXAMPLES))
    script = ["turn", "on", "movie", "time"]
    # Without examples_first, the keyword intent matches and examples aren't tried.
    result = recognize(script, config=data, max_unexplained_other=2)
    assert result.best.intent == "TurnOn"
    result = recognize(script, config=data, max_unexplained_other=2, examples_first=True)
    assert result.best.intent == "Movie"
    assert result.best.example_source == "CTC"
    assert [m.intent for m in result.matches] == ["Movie", "TurnOn"]
    assert any("comes after the example" in note for note in result.best.notes)


def test_examples_first_only_when_said_word_for_word():
    data = copy.deepcopy(CONFIG)
    data["intents"].update(copy.deepcopy(EXAMPLES))
    result = recognize(["turn", "on", "the", "lights"], config=data, examples_first=True)
    assert result.best.intent == "TurnOn"


def test_boolean_settings():
    data = copy.deepcopy(CONFIG)
    data["settings"] = {"examples_first": "yes"}
    with pytest.raises(ConfigError, match="expected true or false"):
        parse_config(data)
    data["settings"] = {"examples_first": True, "embedding_accept": False}
    settings = parse_config(data).settings
    assert settings.examples_first and not settings.embedding_accept
