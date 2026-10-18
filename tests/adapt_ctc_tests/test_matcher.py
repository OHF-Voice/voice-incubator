"""Intent matching on scripted log-probabilities over a toy vocabulary."""

import copy

import numpy as np
import pytest

from adapt_ctc.config import ConfigError, parse_config
from adapt_ctc.matcher import Recognizer

from .conftest import toy_vocab, utterance

CONFIG = {
    "language": "en",
    "filler": ["the", "a", "please"],
    "numbers": {"detect": {"min": 0, "max": 200}},
    "names": {
        "areas": {
            "kitchen": "Kitchen",
            "bedroom": "Bedroom",
            "living_room": {"name": "Living Room", "aliases": ["Den"]},
        }
    },
    "intents": {
        "TurnOn": {
            "require": [["turn on", "switch on"]],
            "optional": [["light", "lights"]],
            "exclude": ["off"],
        },
        "TurnOff": {"require": [["turn off", "switch off"]], "optional": [["light", "lights"]]},
        "TurnOnArea": {
            "require": [["turn on"]],
            "optional": [["light", "lights"], ["in"]],
            "exclude": ["off"],
            "names": {"area": 1},
        },
        "TurnOnTwoAreas": {
            "require": [["turn on"], ["and"]],
            "optional": [["lights"], ["in"]],
            "names": {"area": 2},
        },
        "Brightness": {
            "require": [["set"], ["brightness"]],
            "optional": [["to"], ["percent"]],
            "names": {"area": {"count": 1, "ids": ["kitchen"]}},
            "number": {"min": 0, "max": 100},
        },
        "Timer": {
            "require": [["set"], ["timer"], ["minutes", "seconds"]],
            "optional": [["for"]],
            "number": {
                "min": 1,
                "max": 100,
                "prior": [
                    {"when": "seconds", "values": [5, 10, 15, 20, 30, 45, 60, 90], "cost": 0},
                    {"when": "seconds", "cost": 3},
                ],
            },
        },
    },
}


def recognize(script, config=None, **settings):
    data = copy.deepcopy(CONFIG)
    data.update(copy.deepcopy(config or {}))
    data["settings"] = settings
    vocab = toy_vocab()
    recognizer = Recognizer(parse_config(data), vocab=vocab)
    return recognizer.recognize_log_probs(utterance(vocab, script), 0.08)


def rejected(result, intent):
    return next(r for r in result.rejected if r.intent == intent)


def test_keywords():
    result = recognize(["turn", "on", "the", "lights"])
    assert result.best.intent == "TurnOn"
    assert [m.intent for m in result.matches] == ["TurnOn"]
    assert result.best.cost == pytest.approx(0.0, abs=0.01)
    assert [u.role for u in result.best.spans] == ["require", "filler", "optional"]


def test_hedged_frame_scores_the_keyword_not_the_transcript():
    # Greedy reads "turn the the lights"; the second frame still says "on".
    result = recognize(["turn", {"the": 0.5, "on": 0.4, "off": 0.05}, "the", "lights"])
    assert result.transcript == "turn the the lights"
    assert result.best.intent == "TurnOn"
    assert result.best.acoustic == pytest.approx(0.223, abs=0.01)
    reasons = rejected(result, "TurnOff").reasons
    assert "outscored by 'turn on'" in reasons[0]


def test_excluded_keyword_vetoes_only_unexplained_audio():
    result = recognize(["turn", "on", "the", "lights", "off"])
    assert "TurnOn" not in [m.intent for m in result.matches]
    assert any("excluded 'off'" in r for r in rejected(result, "TurnOn").reasons)


def test_alias_and_area():
    result = recognize(["turn", "on", "the", "den", "lights"])
    assert result.best.intent == "TurnOnArea"
    assert result.best.slots == {"area": ["living_room"]}
    # The plain intent cannot take the area it would leave unexplained.
    assert [m.intent for m in result.matches] == ["TurnOnArea"]
    reason = rejected(result, "TurnOn").reasons[0]
    assert "'den'" in reason and "area living_room, which this intent cannot take" in reason

    # Not after a name cue either: an area left unexplained rejects an intent
    # that takes none (places_must_fit) -- or, turned off, only costs it.
    result = recognize(["turn", "on", "lights", "den"])
    assert [m.intent for m in result.matches] == ["TurnOnArea"]
    assert rejected(result, "TurnOn").reasons == ["heard area living_room, which this intent cannot take"]
    assert rejected(result, "TurnOn").missing[0] == {"kind": "place", "type": "area", "id": "living_room"}
    result = recognize(["turn", "on", "lights", "den"], places_must_fit=False)
    assert [m.intent for m in result.matches] == ["TurnOnArea", "TurnOn"]


def test_two_areas():
    result = recognize(["turn", "on", "the", "lights", "in", "the", "kitchen", "and", "bedroom"])
    assert result.best.intent == "TurnOnTwoAreas"
    assert sorted(result.best.slots["area"]) == ["bedroom", "kitchen"]
    reasons = rejected(result, "TurnOnArea").reasons
    assert any("recognized word(s) left unused" in r for r in reasons)


def test_name_subset():
    result = recognize(["set", "the", "bedroom", "brightness", "to", "fifty"])
    reasons = rejected(result, "Brightness").reasons
    assert reasons[0].startswith("expected 1 area(s), heard 0")


def test_number_slot_and_out_of_range():
    result = recognize(["set", "the", "kitchen", "brightness", "to", "fifty", "percent"])
    assert result.best.intent == "Brightness"
    assert result.best.slots == {"area": ["kitchen"], "number": 50}

    result = recognize(["set", "the", "kitchen", "brightness", "to", "one", "hundred", "fifty"])
    reasons = rejected(result, "Brightness").reasons
    assert "heard number(s) 150, outside 0-100" in reasons


def test_prior_chooses_between_confusable_numbers():
    hedge = {"fifty": 0.5, "fifteen": 0.45}
    seconds = recognize(["set", "a", "timer", "for", hedge, "seconds"])
    assert seconds.best.slots == {"number": 15}
    minutes = recognize(["set", "a", "timer", "for", hedge, "minutes"])
    assert minutes.best.slots == {"number": 50}


def test_unexplained_speech_limits():
    result = recognize(["turn", "on", "the", "lights", "what", "weather", "tomorrow"])
    reasons = rejected(result, "TurnOn").reasons
    assert any("3 unrecognized word(s), limit 2" in r for r in reasons)


def test_unexpected_number_is_reported():
    result = recognize(["turn", "on", "the", "lights", "fifty"])
    assert result.best.intent == "TurnOn"
    assert result.best.cost == pytest.approx(4.0, abs=0.01)
    assert any("number 50" in note for note in result.best.notes)


def test_global_rejection_threshold():
    result = recognize(["turn", {"the": 0.5, "on": 0.4}, "the", "lights"], max_cost=0.1)
    assert result.best is None
    assert any("rejection threshold" in r for r in rejected(result, "TurnOn").reasons)


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"intents": {"X": {"require": ["a"], "bogus": 1}}}, "unknown key"),
        ({"intents": {"X": {"names": {"area": {"count": 1, "ids": ["attic"]}}}}}, "not in the shared list"),
        (
            {"intents": {"X": {"require": ["set"], "number": {"min": 0, "max": 9, "prior": [{"when": "nope", "cost": 1}]}}}},
            "not required or optional keywords",
        ),
        ({"intents": {"X": {"optional": ["a"]}}}, "needs a required keyword"),
        ({"intents": {"X": {"examples": ["what is the weather"], "optional": ["a"]}}}, "only examples"),
        ({"intents": {"X": {"examples": ["..."]}}}, "no speakable words"),
        (
            {"intents": {"X": {"examples": ["What's the weather?"]}, "Y": {"examples": ["whats the weather"]}}},
            "'whats the weather' is also an example of X",
        ),
        ({"intents": {"X": {"require": ["a"], "number": {"max": 3}}}}, "needs 'min' and 'max'"),
        ({"intents": {"X": {"require": ["a"], "optional": ["turn ... on"]}}}, "only required phrases"),
        ({"intents": {"X": {"require": ["turn ..."]}}}, "words on both sides"),
        ({"name_cues": ["the end"], "intents": {"X": {"require": ["a"]}}}, "single word"),
        (
            {"intents": {"X": {"require": ["set"], "number": {"min": 0, "max": 9, "units": ["hour"]}}}},
            r"number\.units: \['hour'\] are not required",
        ),
    ],
)
def test_config_errors(patch, message):
    data = {"names": {"areas": {"kitchen": "Kitchen"}}, **patch}
    with pytest.raises(ConfigError, match=message):
        parse_config(data)


def test_yaml_keeps_on_and_off_as_words(tmp_path):
    from adapt_ctc.config import load_config

    path = tmp_path / "config.yaml"
    path.write_text(
        "intents:\n"
        "  TurnOn: {require: [[turn on]], exclude: [off, no]}\n"
        "  TurnOff: {require: [[turn off]], exclude: [on, yes]}\n"
        "settings: {max_unexplained_important: 1}\n"
    )
    config = load_config(path)
    assert config.intents["TurnOn"].exclude == ("off", "no")
    assert config.intents["TurnOff"].exclude == ("on", "yes")


def test_boolean_phrase_is_an_error():
    with pytest.raises(ConfigError, match="quote it"):
        parse_config({"intents": {"X": {"require": [True]}}})


def test_a_phrase_inside_another_cannot_outscore_it():
    # "turn" is muddy (greedy hears "the"), "on" is clear: the bare "on" is
    # cheaper on its own frames, but it is part of "turn on", not a rival.
    data = copy.deepcopy(CONFIG)
    data["intents"]["TurnOff"]["exclude"] = ["on"]
    vocab = toy_vocab()
    recognizer = Recognizer(parse_config(data), vocab=vocab)
    result = recognizer.recognize_log_probs(
        utterance(vocab, [{"the": 0.6, "turn": 0.15}, "on", "the", "lights"]), 0.08
    )
    turn_on = next(h for h in result.spotting.hits if h.phrase == "turn on")
    assert turn_on.dominated_by is None
    assert result.best.intent == "TurnOn"


def test_lead_in_word_is_ignored():
    # A stray syllable from the wake word, right at the start.
    result = recognize([{"what": 0.9}, "turn", "on", "the", "lights"], max_unexplained_other=0)
    assert result.lead_in == "what"
    assert result.best.intent == "TurnOn"
    assert result.best.cost == pytest.approx(0.0, abs=0.01)

    # Not when it comes later, or when lead-in is off.
    result = recognize(["turn", "on", "the", "lights", "what"], max_unexplained_other=0)
    assert result.lead_in is None and result.best is None
    result = recognize(["what", "turn", "on", "the", "lights"], max_unexplained_other=0, lead_in=0)
    assert result.best is None


def test_unknown_name_after_a_cue_rejects():
    # "weather" is no name or keyword: after "the", it is a name we lack.
    result = recognize(["turn", "on", "the", "weather", "lights"])
    assert result.best is None
    reason = rejected(result, "TurnOn").reasons[0]
    assert "'weather'" in reason and "after 'the'" in reason

    # Without the cue it is just an unexplained word, and with cues off too.
    assert recognize(["turn", "on", "weather", "lights"]).best.intent == "TurnOn"
    result = recognize(["turn", "on", "the", "weather", "lights"], config={"name_cues": []})
    assert result.best.intent == "TurnOn"

    # A known name, or filler, after the cue is fine.
    assert recognize(["turn", "on", "the", "den", "lights"]).best.intent == "TurnOnArea"
    assert recognize(["turn", "on", "the", "a", "lights"]).best.intent == "TurnOn"


SPLIT_INTENTS = {
    "TurnOn": {"require": [["turn on", "turn ... on"]], "optional": [["light", "lights"]]},
    "TurnOff": {"require": [["turn off", "turn ... off"]], "optional": [["light", "lights"]]},
    "TurnOnArea": {
        "require": [["turn on", "turn ... on"]],
        "optional": [["light", "lights"]],
        "names": {"area": 1},
    },
}


def test_split_phrase_takes_words_between_its_parts():
    result = recognize(["turn", "the", "kitchen", "lights", "on"], config={"intents": SPLIT_INTENTS})
    assert result.best.intent == "TurnOnArea"
    assert result.best.slots == {"area": ["kitchen"]}
    parts = [(u.meaning.phrase, u.part) for u in result.best.spans if u.part is not None]
    assert parts == [("turn", (0, 0, 2)), ("on", (0, 1, 2))]

    result = recognize(["turn", "the", "lights", "off"], config={"intents": SPLIT_INTENTS})
    assert result.best.intent == "TurnOff"
    assert result.best.cost == pytest.approx(0.0, abs=0.01)

    # Together, the whole phrase is still heard.
    result = recognize(["turn", "on", "the", "lights"], config={"intents": SPLIT_INTENTS})
    assert result.best.intent == "TurnOn"


def test_split_phrase_needs_every_part_in_order():
    result = recognize(["turn", "the", "lights"], config={"intents": SPLIT_INTENTS})
    assert result.best is None
    assert rejected(result, "TurnOn").reasons[0].startswith("required [turn on | turn ... on] not heard")

    result = recognize(["on", "the", "lights", "turn"], config={"intents": SPLIT_INTENTS})
    assert "TurnOn" not in [m.intent for m in result.matches]


UNIT_TIMER = {
    "Timer": {
        "require": [["set"], ["timer"], ["minutes", "seconds", "minute", "hour"]],
        "optional": [["for"]],
        "number": {
            "min": 0.5,
            "max": 100,
            "step": 0.5,
            "units": ["minute", "hour"],
            "prior": [{"multiple_of": 1, "cost": 0}, {"cost": 1}],
        },
    },
}


@pytest.mark.parametrize(
    ("script", "slots"),
    [
        (["set", "a", "timer", "for", "a", "minute", "and", "a", "half"], {"minutes": 1.5}),
        (["set", "a", "timer", "for", "an", "hour", "and", "a", "half"], {"hours": 1.5}),
        (["set", "a", "timer", "for", "half", "an", "hour"], {"hours": 0.5}),
        (["set", "a", "timer", "for", "an", "hour"], {"hours": 1}),
        (["set", "a", "timer", "for", "five", "minutes"], {"minutes": 5}),
    ],
)
def test_number_said_with_its_unit(script, slots):
    result = recognize(script, config={"intents": UNIT_TIMER})
    assert result.best.intent == "Timer"
    assert result.best.slots == slots
    assert not result.best.unexplained


def test_unit_inside_a_number_meets_prior_conditions():
    timer = copy.deepcopy(UNIT_TIMER)
    timer["Timer"]["number"]["prior"] = [{"when": "hour", "cost": 2}]
    result = recognize(["set", "a", "timer", "for", "an", "hour"], config={"intents": timer})
    assert result.best.prior == pytest.approx(2)


OPTIONAL = {
    "Play": {
        "require": [["play"]],
        "optional": [["in"], ["and"]],
        "names": {"area": {"count": 2, "min": 1}},
    },
    "Music": {
        "require": [["play"], ["music"]],
        "optional": [["in"]],
        "names": {"area": {"count": 1, "optional": True}},
    },
    "TimerLeft": {
        "require": [["timer"]],
        "optional": [["minutes"]],
        "number": {"min": 1, "max": 100, "optional": True},
    },
}


@pytest.mark.parametrize(
    ("script", "intent", "slots"),
    [
        (["play", "music"], "Music", {}),
        (["play", "music", "in", "the", "kitchen"], "Music", {"area": ["kitchen"]}),
        (["play", "kitchen"], "Play", {"area": ["kitchen"]}),
        (["play", "kitchen", "and", "bedroom"], "Play", {"area": ["bedroom", "kitchen"]}),
        (["timer"], "TimerLeft", {}),
        (["five", "minutes", "timer"], "TimerLeft", {"number": 5}),
    ],
)
def test_optional_names_and_numbers(script, intent, slots):
    result = recognize(script, config={"intents": OPTIONAL})
    assert result.best.intent == intent
    found = {k: sorted(v) if isinstance(v, list) else v for k, v in result.best.slots.items()}
    assert found == slots


def test_name_minimum_is_required():
    result = recognize(["play"], config={"intents": OPTIONAL})
    assert result.best is None
    assert any("expected 1 area(s)" in r for r in rejected(result, "Play").reasons)


@pytest.mark.parametrize(
    ("names", "message"),
    [
        ({"area": {"count": 1, "min": 2}}, "more than count"),
        ({"area": {"count": 1, "min": 0, "optional": True}}, "either 'min' or 'optional'"),
        ({"area": {"count": 1, "optional": "yes"}}, "true or false"),
    ],
)
def test_name_minimum_errors(names, message):
    data = {"names": {"areas": {"kitchen": "Kitchen"}}, "intents": {"X": {"require": ["a"], "names": names}}}
    with pytest.raises(ConfigError, match=message):
        parse_config(data)


def test_intent_needs_something_required():
    data = {
        "names": {"areas": {"kitchen": "Kitchen"}},
        "intents": {"X": {"names": {"area": {"count": 1, "optional": True}}, "number": {"min": 0, "max": 9, "optional": True}}},
    }
    with pytest.raises(ConfigError, match="needs a required keyword"):
        parse_config(data)


DURATION = {
    "Timer": {
        "require": [["timer"], ["minutes", "minute", "seconds", "second", "hour"]],
        "optional": [["set"], ["for"], ["and"], ["minutes", "minute", "seconds", "second", "hour"]],
        "number": {
            "min": 0.5,
            "max": 100,
            "step": 0.5,
            "units": ["second", "minute", "hour"],
            "parts": 3,
            "prior": [
                {"when": "seconds", "values": [5, 10, 15, 20, 30, 45, 60, 90], "cost": 0},
                {"when": "seconds", "cost": 3},
            ],
        },
    },
}


@pytest.mark.parametrize(
    ("script", "slots"),
    [
        (["set", "a", "timer", "for", "five", "minutes"], {"minutes": 5}),
        (["set", "a", "timer", "for", "an", "hour", "and", "a", "half"], {"hours": 1.5}),
        (
            ["set", "a", "timer", "for", "one", "hour", "and", "fifteen", "seconds"],
            {"hours": 1, "seconds": 15},
        ),
        (
            ["set", "a", "timer", "for", "an", "hour", "five", "minutes", "and", "fifteen", "seconds"],
            {"hours": 1, "minutes": 5, "seconds": 15},
        ),
    ],
)
def test_compound_duration(script, slots):
    result = recognize(script, config={"intents": DURATION})
    assert result.best.intent == "Timer"
    assert result.best.slots == slots
    assert not result.best.unexplained


def test_compound_duration_is_largest_unit_first():
    result = recognize(
        ["set", "a", "timer", "for", "five", "minutes", "one", "hour"], config={"intents": DURATION}
    )
    assert result.best is None or len(result.best.slots) == 1


def test_compound_prior_applies_to_each_part():
    script = ["set", "a", "timer", "for", "one", "hour", "and", {"fifteen": 0.45, "fifty": 0.5}, "seconds"]
    result = recognize(script, config={"intents": DURATION})
    assert result.best.slots == {"hours": 1, "seconds": 15}
    assert result.best.prior == pytest.approx(0)


@pytest.mark.parametrize(
    ("number", "message"),
    [
        ({"min": 1, "max": 9, "parts": 2}, "needs units"),
        ({"min": 1, "max": 9, "parts": 0}, "positive integer"),
    ],
)
def test_compound_number_errors(number, message):
    data = {"intents": {"X": {"require": ["a"], "number": number}}}
    with pytest.raises(ConfigError, match=message):
        parse_config(data)


VERBS = {
    "names": {
        "areas": {"kitchen": "Kitchen", "bedroom": "Bedroom"},
        "entities": {"light.bee": "Bee", "lock.den": "Den"},
    },
    "intents": {
        "TurnOn": {
            "require": [["turn on"]],
            "names": {"entity": {"count": 1, "optional": True, "domains": ["light"]}},
        },
        "Lock": {"require": [["set"]], "names": {"entity": {"count": 1, "domains": ["lock"]}}},
        "StartTimer": {
            "require": [["timer"], ["minutes", "minute"]],
            "optional": [["set"], ["for"], ["play"]],
            "wildcard": {"slot": "name", "before": ["timer"], "after": ["play"], "max_words": 2},
            "number": {"min": 1, "max": 100, "units": ["minute"]},
        },
    },
}


def test_domains_pick_the_entities_a_verb_acts_on():
    result = recognize(["turn", "on", "the", "bee"], config=VERBS)
    assert (result.best.intent, result.best.slots) == ("TurnOn", {"entity": ["light.bee"]})
    # A lock is not something TurnOn acts on: after "the", it rejects it.
    result = recognize(["turn", "on", "the", "den"], config=VERBS)
    assert result.best is None
    assert any("cannot take" in r for r in rejected(result, "TurnOn").reasons)
    result = recognize(["set", "the", "den"], config=VERBS)
    assert (result.best.intent, result.best.slots) == ("Lock", {"entity": ["lock.den"]})


@pytest.mark.parametrize(
    ("script", "slots"),
    [
        # Free text right before "timer", or right after the `after` keyword.
        (["set", "a", "weather", "timer", "for", "five", "minutes"], {"name": "weather", "minutes": 5}),
        (["set", "a", "okay", "natalie", "timer", "for", "five", "minutes"], {"name": "okay natalie", "minutes": 5}),
        (["set", "a", "timer", "for", "five", "minutes", "play", "music"], {"name": "music", "minutes": 5}),
        # Words a keyword or number explains are not taken.
        (["set", "a", "timer", "for", "five", "minutes"], {"minutes": 5}),
    ],
)
def test_wildcard(script, slots):
    result = recognize(script, config=VERBS)
    assert result.best.intent == "StartTimer"
    assert result.best.slots == slots
    assert not result.best.unexplained


def test_wildcard_is_placed_by_its_keywords():
    # Free text elsewhere is not the name: "weather" is unexplained.
    result = recognize(["set", "a", "timer", "weather", "for", "five", "minutes"], config=VERBS)
    assert "name" not in result.best.slots
    assert [w.text for w in result.best.unexplained] == ["weather"]


@pytest.mark.parametrize(
    ("wildcard", "message"),
    [
        ({"slot": "area", "before": ["timer"]}, "slot name other than"),
        ({"slot": "name"}, "needs 'before' or 'after'"),
        ({"slot": "name", "before": ["clock"]}, "not required or optional keywords"),
    ],
)
def test_wildcard_errors(wildcard, message):
    data = {"intents": {"X": {"require": ["timer"], "wildcard": wildcard}}}
    with pytest.raises(ConfigError, match=message):
        parse_config(data)


def test_domains_errors():
    data = {"names": {"areas": {"kitchen": "Kitchen"}}, "intents": {"X": {"require": ["a"], "names": {"area": {"count": 1, "domains": ["light"]}}}}}
    with pytest.raises(ConfigError, match="only for shared entities"):
        parse_config(data)


def test_include_merges_files(tmp_path):
    from adapt_ctc.config import load_config

    (tmp_path / "core.yaml").write_text(
        "filler: [the]\n"
        "intents:\n"
        "  On: {require: [turn on]}\n"
        "  Timer: {require: [timer]}\n"
    )
    (tmp_path / "extras.yaml").write_text("intents:\n  Timer: {require: [timer, set]}\n")
    (tmp_path / "home.yaml").write_text(
        "include: [core.yaml, extras.yaml]\n"
        "names:\n  areas: {kitchen: Kitchen}\n"
        "settings: {max_cost: 5}\n"
    )
    config = load_config(tmp_path / "home.yaml")
    assert sorted(config.intents) == ["On", "Timer"]
    assert config.intents["Timer"].require == (("timer",), ("set",))
    assert config.filler == ("the",)
    assert list(config.names["area"]) == ["kitchen"]
    assert config.settings.max_cost == 5
    (tmp_path / "loop.yaml").write_text("include: [loop.yaml]\nintents: {On: {require: [on]}}\n")
    with pytest.raises(ConfigError, match="includes itself"):
        load_config(tmp_path / "loop.yaml")


class FakeTranscriber:
    def __init__(self, text):
        self.text = text
        self.calls = 0

    def transcribe(self, audio):
        self.calls += 1
        return self.text


EXAMPLES = {
    "Weather": {"examples": ["What's the weather like tomorrow?", "Is it going to rain"]},
    "Hello": {"require": [["hello"]], "examples": ["good morning"]},
}


def recognize_with_examples(script, transcript, examples=EXAMPLES, **settings):
    data = copy.deepcopy(CONFIG)
    data["intents"].update(copy.deepcopy(examples))
    data["settings"] = settings
    vocab = toy_vocab()
    transcriber = FakeTranscriber(transcript)
    recognizer = Recognizer(parse_config(data), vocab=vocab, transcriber=transcriber)
    result = recognizer.recognize_log_probs(utterance(vocab, script), 0.08)
    return recognizer.match_examples(result, np.zeros(16000, dtype=np.float32)), transcriber


def test_examples_match_the_tdt_transcript_when_no_keyword_intent_does():
    # Case, punctuation, apostrophes and word breaks don't matter.
    result, transcriber = recognize_with_examples(
        ["what", "weather", "tomorrow"], "Whats the weather like to morrow."
    )
    assert transcriber.calls == 1
    assert result.best.intent == "Weather"
    assert result.best.example == "What's the weather like tomorrow?"
    assert result.tdt_transcript == "Whats the weather like to morrow."
    assert "tdt" in result.timings
    # An intent with keywords and examples matches on either.
    result, _ = recognize_with_examples(["what", "weather", "tomorrow"], "Good morning.")
    assert result.best.intent == "Hello"


def test_examples_are_not_tried_when_a_keyword_intent_matches():
    result, transcriber = recognize_with_examples(
        ["turn", "on", "the", "lights"], "What's the weather like tomorrow?"
    )
    assert transcriber.calls == 0
    assert result.best.intent == "TurnOn"
    assert result.tdt_transcript is None
    assert rejected(result, "Weather").reasons == [
        "examples only: tried when no keyword intent matches"
    ]


def test_transcript_that_is_no_example_matches_nothing():
    result, _ = recognize_with_examples(["what", "weather", "tomorrow"], "What's the weather today?")
    assert result.best is None
    assert rejected(result, "Weather").reasons == [
        "no example close in the CTC evidence",
        "TDT heard 'What's the weather today?', none of its examples",
    ]
    # A keyword intent keeps its own reasons first.
    reasons = rejected(result, "Hello").reasons
    assert reasons[0].startswith("required [hello] not heard")
    assert reasons[-1].startswith("TDT heard")


def test_examples_without_a_transcriber():
    data = copy.deepcopy(CONFIG)
    data["intents"].update(copy.deepcopy(EXAMPLES))
    vocab = toy_vocab()
    recognizer = Recognizer(parse_config(data), vocab=vocab)
    result = recognizer.recognize_log_probs(utterance(vocab, ["what", "weather"]), 0.08)
    result = recognizer.match_examples(result, np.zeros(0, dtype=np.float32))
    assert result.best is None
    assert rejected(result, "Weather").reasons[-1] == "examples not tried: no TDT or embedding model loaded"


CTC_EXAMPLES = {
    "Forecast": {"examples": ["what weather tomorrow"]},
    "Weather": {"examples": ["what weather"]},
}


def test_examples_are_searched_for_in_the_ctc_evidence_before_tdt():
    result, transcriber = recognize_with_examples(
        ["what", "weather", "tomorrow"], "something else", CTC_EXAMPLES
    )
    assert transcriber.calls == 0
    assert result.best.intent == "Forecast"
    assert result.best.example == "what weather tomorrow"
    assert result.best.example_source == "CTC"
    assert result.best.cost == pytest.approx(0.0, abs=0.01)
    assert [u.meaning.phrase for u in result.best.spans] == ["what weather tomorrow"]
    assert "examples" in result.timings
    # "what weather" leaves "tomorrow" unexplained, a word the other example
    # recognized: 4.0 more.
    assert rejected(result, "Weather").reasons == []


def test_an_example_said_whole_only():
    # "turn on" said in "turn on the lights": with examples first, the short
    # sentence took it from the keywords; said whole only, the keywords have it.
    script = ["turn", "on", "the", "lights"]
    short = {"Hello": {"examples": ["turn on"]}}
    result, _ = recognize_with_examples(script, "", short, examples_first=True)
    assert result.best.intent == "Hello" and result.best.example_source == "CTC"
    result, _ = recognize_with_examples(script, "", short, examples_first=True, examples_whole=True)
    assert result.best.intent == "TurnOn"
    # Said whole, it still matches, ahead of the keywords.
    result, _ = recognize_with_examples(["turn", "on"], "", short, examples_first=True, examples_whole=True)
    assert result.best.intent == "Hello" and result.best.example_source == "CTC"
    # An example-only intent: not that sentence, and said why.
    result, _ = recognize_with_examples(["what", "weather", "tomorrow"], "", {"Weather": {"examples": ["what weather"]}}, examples_whole=True)
    assert result.best is None
    assert rejected(result, "Weather").reasons[0] == "example 'what weather' is not all that was said: tomorrow besides"


def test_a_template_is_every_sentence_it_stands_for():
    # Found in the CTC evidence, or in the transcript, as any of them.
    lights = {"Lights": {"examples": ["(turn|switch) on [the] light[s]"]}}
    result, _ = recognize_with_examples(["switch", "on", "light"], "", lights, examples_first=True, examples_whole=True)
    assert result.best.intent == "Lights" and result.best.example == "switch on light" and result.best.example_source == "CTC"
    result, _ = recognize_with_examples(["what", "weather"], "switch on the lights", lights, examples_whole=True)
    assert result.best.intent == "Lights" and result.best.example == "switch on the lights"


def test_examples_too_close_to_another_intents_go_to_tdt():
    result, transcriber = recognize_with_examples(
        ["what", "weather", "tomorrow"], "What weather?", CTC_EXAMPLES, example_margin=5
    )
    assert transcriber.calls == 1
    assert result.best.intent == "Weather"
    assert result.best.example_source == "TDT"
    assert rejected(result, "Forecast").reasons[0] == (
        "example 'what weather tomorrow' costs 0.00 in the CTC evidence, within 5 of Weather (4.00)"
    )


def test_example_cost_limit_counts_unexplained_words():
    script = ["what", "weather", "tomorrow", "kitchen"]
    # A room the intent cannot take: not that sentence (places_must_fit).
    result, _ = recognize_with_examples(script, "", CTC_EXAMPLES)
    assert result.best is None
    assert "leaves area kitchen, which this intent cannot take" in rejected(result, "Forecast").reasons[0]

    result, _ = recognize_with_examples(script, "", CTC_EXAMPLES, places_must_fit=False)
    assert result.best.intent == "Forecast"
    # Names and numbers are not listened for here: "kitchen" is just a word.
    assert result.best.cost == pytest.approx(1.5, abs=0.01)
    assert [w.text for w in result.best.unexplained] == ["kitchen"]

    result, _ = recognize_with_examples(script, "", CTC_EXAMPLES, example_max_cost=1, places_must_fit=False)
    assert result.best is None
    assert rejected(result, "Forecast").reasons[0] == (
        "example 'what weather tomorrow' costs 1.50 in the CTC evidence, limit 1"
    )


class FakeEmbedder:
    """Vectors by meaning, for the sentences a test names; anything else
    (the built-in everyday requests) gets a vector of its own, near nothing."""

    def __init__(self, meanings):
        self.meanings = meanings
        self.calls = []

    def embed(self, texts, document=False):
        self.calls.append((list(texts), document))
        out = []
        for text in texts:
            vector = np.zeros(64)
            if text in self.meanings:
                for axis, weight in self.meanings[text].items():
                    vector[axis] = weight
            else:
                vector[16 + hash(text) % 48] = 1.0
            out.append(vector / np.linalg.norm(vector))
        return np.array(out)


PARAPHRASES = {
    "Umbrella": {"examples": ["will I need an umbrella tomorrow?"]},
    "Spell": {"examples": ["how do you spell necessary"]},
}
MEANINGS = {
    "will I need an umbrella tomorrow?": {0: 1.0},
    "how do you spell necessary": {1: 1.0},
    "Do I need a membrella tomorrow?": {0: 0.9, 1: 0.1},  # TDT
    "do I need an umbrella tomorrow": {0: 0.95, 1: 0.05},  # CTC
    "between the two": {0: 1.0, 1: 0.9},
    "turn on the lights please": {2: 1.0},
}


def recognize_by_embedding(script, tdt, ctc, **settings):
    """A recognition whose CTC transcript is ``ctc`` and TDT one ``tdt``."""
    data = copy.deepcopy(CONFIG)
    data["intents"].update(copy.deepcopy(PARAPHRASES))
    data["settings"] = settings
    vocab = toy_vocab()
    embedder = FakeEmbedder(MEANINGS)
    recognizer = Recognizer(
        parse_config(data), vocab=vocab, transcriber=FakeTranscriber(tdt), embedder=embedder
    )
    result = recognizer.recognize_log_probs(utterance(vocab, script), 0.08)
    result.transcript = ctc
    return recognizer.match_examples(result, np.zeros(16000, dtype=np.float32)), recognizer, embedder


def test_examples_said_in_other_words_match_by_embedding():
    result, recognizer, embedder = recognize_by_embedding(
        ["what", "weather"], "Do I need a membrella tomorrow?", "do I need an umbrella tomorrow"
    )
    assert result.best.intent == "Umbrella"
    assert result.best.example == "will I need an umbrella tomorrow?"
    assert result.best.example_source == "embedding"
    # The closer of the two transcripts counts.
    assert result.best.similarity == pytest.approx(0.95 / np.hypot(0.95, 0.05), abs=1e-6)
    assert result.best.notes[0].startswith("next: Spell at ")
    assert "embedding" in result.timings
    # Examples and everyday requests are embedded once, as documents;
    # transcripts as queries.
    assert embedder.calls[0] == (["will I need an umbrella tomorrow?", "how do you spell necessary"], True)
    assert embedder.calls[1][1] and len(embedder.calls[1][0]) > 50
    assert embedder.calls[2] == (["Do I need a membrella tomorrow?", "do I need an umbrella tomorrow"], False)


def test_embedding_needs_a_margin_over_the_next_intent():
    result, _, _ = recognize_by_embedding(["what", "weather"], "between the two", "")
    assert result.best is None
    assert rejected(result, "Umbrella").reasons[-1] == (
        "closest example by embedding 'will I need an umbrella tomorrow?', similarity 0.74, "
        "within 0.08 of Spell at 0.67"
    )
    assert rejected(result, "Spell").reasons[-1].startswith("closest example by embedding")


def test_a_template_is_embedded_by_the_few_that_say_each_alternative():
    data = copy.deepcopy(CONFIG)
    data["intents"]["Lights"] = {"examples": ["(turn|switch) on [the] light[s]"]}
    embedder = FakeEmbedder({})
    recognizer = Recognizer(parse_config(data), vocab=toy_vocab(), embedder=embedder)
    assert [t for i, t in recognizer._example_owners if i == "Lights"] == ["turn on the lights", "switch on light"]
    assert len(recognizer.examples) >= 8  # every sentence for matching


def test_everyday_requests_stand_for_no_example():
    # Closest to a built-in request ("turn on the lights"): no example matches.
    MEANINGS["turn on the lights"] = {2: 1.0}
    try:
        result, recognizer, _ = recognize_by_embedding(["what", "weather"], "turn on the lights please", "")
    finally:
        del MEANINGS["turn on the lights"]
    assert "turn on the lights" in recognizer._distractors
    assert result.best is None
    assert "'turn on the lights' (no example)" in rejected(result, "Umbrella").reasons[-1]


def test_everyday_requests_close_to_an_example_are_dropped():
    MEANINGS["what's the weather"] = {0: 1.0}
    try:
        _, recognizer, _ = recognize_by_embedding(["what"], "", "")
    finally:
        del MEANINGS["what's the weather"]
    assert "what's the weather" not in recognizer._distractors
    assert "turn on the lights" in recognizer._distractors


PLAY = {
    "intents": {
        "Play": {"require": [["play"]], "wildcard": {"slot": "query", "after": ["play"], "to_end": True, "max_words": 4, "required": True}},
        "PlayIn": {
            "require": [["play"]],
            "optional": [["in"]],
            "names": {"area": 1},
            "wildcard": {"slot": "query", "after": ["play"], "to_end": True, "max_words": 4, "required": True},
        },
    }
}


def test_free_text_stops_before_a_place_it_ends_on():
    config = {**PLAY, "intents": {**CONFIG["intents"], **PLAY["intents"]}}
    result = recognize(["play", "music", "in", "the", "kitchen"], config)
    assert result.best.intent == "PlayIn"
    assert result.best.slots == {"area": ["kitchen"], "query": "music"}
    # Text that swallows the place it ends on leaves it out: not that intent.
    assert rejected(result, "Play").reasons == ["heard area kitchen, which this intent cannot take"]
    # A room inside the text is the text: "play kitchen music".
    result = recognize(["play", "kitchen", "music"], config)
    assert result.best.intent == "Play" and result.best.slots == {"query": "kitchen music"}
    # Off: the text runs to the end, as before.
    result = recognize(["play", "music", "in", "the", "kitchen"], config, places_must_fit=False)
    assert "Play" in [m.intent for m in result.matches]


def test_free_text_stops_before_a_device_it_ends_on():
    config = {
        "names": {
            **CONFIG["names"],
            "entities": {"media_player.natalie": "Natalie", "climate.ecobee": "EcoBee"},
        },
        "intents": {
            **CONFIG["intents"],
            "Play": PLAY["intents"]["Play"],
            "PlayOn": {
                "require": [["play"]],
                "optional": [["on"]],
                "names": {"entity": {"count": 1, "domains": ["media_player"]}},
                "wildcard": {"slot": "query", "after": ["play"], "to_end": True, "max_words": 4, "required": True},
            },
        },
    }
    result = recognize(["play", "music", "on", "the", "natalie"], config)
    assert result.best.intent == "PlayOn"
    assert result.best.slots == {"entity": ["media_player.natalie"], "query": "music"}
    # Text that swallows the device it ends on: not an intent that can't take it.
    assert rejected(result, "Play").reasons == ["heard entity media_player.natalie, which this intent cannot take"]
    # A device inside the text is the text.
    result = recognize(["play", "natalie", "music"], config)
    assert result.best.intent == "Play" and result.best.slots == {"query": "natalie music"}
    # A device of a kind it does not take is no place for it either.
    result = recognize(["play", "music", "on", "the", "eco", "+bee"], config)
    assert result.best is None
    assert rejected(result, "Play").reasons == ["heard entity climate.ecobee, which this intent cannot take"]
    assert rejected(result, "PlayOn").missing[0]["kind"] == "name"  # no media player heard
    # Off: the text runs to the end, as before.
    result = recognize(["play", "music", "on", "the", "natalie"], config, places_must_fit=False)
    assert "Play" in [m.intent for m in result.matches]


def test_the_closest_by_meaning_has_no_rival_in_its_own_group():
    # Intents that are one thing said different ways (a command's versions)
    # are passed over for the runner-up.
    _, recognizer, _ = recognize_by_embedding(["what", "weather"], "between the two", "")
    near = recognizer.closest_by_meaning(["between the two"])
    assert near["intent"] == "Umbrella" and near["next"] == "Spell"
    near = recognizer.closest_by_meaning(["between the two"], group=lambda intent: "one")
    assert near["intent"] == "Umbrella" and near["next"] != "Spell" and near["next_similarity"] < 0.5
