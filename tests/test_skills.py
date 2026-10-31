"""Skills: questions answered without an LLM, at three levels, on every
satellite or some."""

from __future__ import annotations

import importlib

import pytest

from voice_labs import skills
from voice_labs.skills import SKILLS, enabled, skill_off, skill_response
from voice_labs.skills.common import normalize
from voice_labs.skills.live import is_live


@pytest.mark.parametrize(
    ("question", "said", "skill"),
    [
        ("How do you spell accommodate?", "Accommodate is spelled A, C, C, O, M, M, O, D, A, T, E.", "spelling"),
        ("What is twelve times eight?", "It's 96.", "arithmetic"),
        ("what's 15 percent of 80", "It's 12.", "arithmetic"),
        ("what is the square root of 144", "It's 12.", "arithmetic"),
        ("convert 100 fahrenheit to celsius", "It's 37.8 degrees Celsius.", "units"),
        ("how many cups are in a quart", "It's 4.", "units"),
        ("what is 5 miles in kilometers", "It's 8.047 kilometers.", "units"),
        # An ounce beside a volume is a fluid ounce.
        ("how many ounces are in a cup", "It's 8.", "units"),
        ("what date is christmas", "It's December 25.", "dates"),
        ("Is 2024 a leap year?", "Yes.", "dates"),
        ("What is the last month of the year?", "It's December.", "dates"),
        ("what time will it be in 45 minutes", "It'll be 5:05 PM.", "clock"),
        ("how long until 5 pm", "40 minutes from now.", "clock"),
        # As a transcript writes them.
        ("Is twenty twenty four a leap year?", "Yes.", "dates"),
        ("What time will it be in an hour and a half?", "It'll be 5:50 PM.", "clock"),
        ("How long until five PM?", "40 minutes from now.", "clock"),
        ("What is the average of four? Eight and twelve?", "It's 8.", "arithmetic"),
        ("How should you spell rhythm?", "Rhythm is spelled R, H, Y, T, H, M.", "spelling"),
        ("What is zero degrees Celsius in Fahrenheit?", "It's 32 degrees Fahrenheit.", "units"),
    ],
)
def test_exact_answers(question, said, skill):
    found = skills.answer(question)
    assert (found.kind, found.text, found.skill) == ("exact", said, skill)
    # Turned off: not answered, by it or another.
    assert skills.answer(question, frozenset(SKILLS) - {skill}) is None


@pytest.mark.parametrize(
    ("question", "brief", "verbose"),
    [
        ("What is twelve times eight?", "96.", "Twelve times eight is 96."),
        ("what's 15 percent of $80", "12 dollars.", "15 percent of 80 is 12 dollars."),
        ("how many cups are in a quart", "4.", "There are 4 cups in a quart."),
        ("how many ounces are in a cup", "8.", "There are 8 fluid ounces in a cup."),
        ("what is 5 miles in kilometers", "8.047 kilometers.", "5 miles is 8.047 kilometers."),
        ("convert 100 fahrenheit to celsius", "37.8 degrees Celsius.", "100 degrees Fahrenheit is 37.8 degrees Celsius."),
        ("what time will it be in 45 minutes", "5:05 PM.", "In 45 minutes it'll be 5:05 PM."),
        ("how long until midnight", "7 hours and 40 minutes.", "Midnight is 7 hours and 40 minutes from now."),
        # Said back as asked, where the skill can't say it with the answer.
        ("Is 2024 a leap year?", "Yes.", "Is 2024 a leap year? Yes."),
        ("How do you spell rhythm?", "R, H, Y, T, H, M.", "Rhythm. R, H, Y, T, H, M. Rhythm."),
    ],
)
def test_every_level(question, brief, verbose):
    levels = skills.answer(question).to_json()["levels"]
    assert (levels["brief"], levels["verbose"]) == (brief, verbose)


@pytest.mark.parametrize(
    "question",
    ["what's the weather like tomorrow", "who won the game last night?", "what are the top news headlines",
     "how many euros is a dollar", "what time does Cosco close?", "how much is 1 bitcoin in dollars"],
)
def test_live_questions(question):
    # Not for the skills (a number in it is not arithmetic): the agent's, or
    # the Unknown command's.
    assert is_live(question)
    assert skills.answer(question) is None


@pytest.mark.parametrize(
    ("question", "said", "skill"),
    [
        # As typed, or as a speech-to-text that formats what it hears writes it.
        ("What is 50°F in Celsius?", "It's 10 degrees Celsius.", "units"),
        ("What is 50 °F in °C?", "It's 10 degrees Celsius.", "units"),
        ("What is 50℉ in Celsius?", "It's 10 degrees Celsius.", "units"),
        ("What is −5°C in Fahrenheit?", "It's 23 degrees Fahrenheit.", "units"),
        ("What is 50F in C?", "It's 10 degrees Celsius.", "units"),
        ("What is 300K in Celsius?", "It's 26.9 degrees Celsius.", "units"),
        ("What’s 12 × 8?", "It's 96.", "arithmetic"),
        ("What is 12·8?", "It's 96.", "arithmetic"),
        ("What is 12x8?", "It's 96.", "arithmetic"),
        ("What is 10 – 3?", "It's 7.", "arithmetic"),
        ("What is 5 + 3 =", "It's 8.", "arithmetic"),
        ("What is 15% of $80?", "It's 12 dollars.", "arithmetic"),
        ("What's $5 plus $3?", "It's 8 dollars.", "arithmetic"),
        ("What is ½ of 10?", "It's 5.", "arithmetic"),
        ("What is 1/2 of 10?", "It's 5.", "arithmetic"),
        ("What is 5²?", "It's 25.", "arithmetic"),
        ("What is √144?", "It's 12.", "arithmetic"),
        ("How many tablespoons are in ¼ cup?", "It's 4.", "units"),
        ("How many cups in 1½ quarts?", "It's 6.", "units"),
        ("How many cups in 1 1/2 quarts?", "It's 6.", "units"),
        ("How many square feet are in 10 m²?", "It's 107.6.", "units"),
        ("Convert 100km to miles", "It's 62.14 miles.", "units"),
        ("What is 5lbs in kilograms?", "It's 2.268 kilograms.", "units"),
        ("What's 3.5 kg in pounds?", "It's 7.716 pounds.", "units"),  # pounds, not money
        ("How do you spell “necessary”?", "Necessary is spelled N, E, C, E, S, S, A, R, Y.", "spelling"),
        ("How do you spell ‘rhythm’?", "Rhythm is spelled R, H, Y, T, H, M.", "spelling"),
        ("When is April Fools’ Day?", "It's April 1.", "dates"),
        ("What day of the week is July 4th, 2026?", "It's Saturday.", "dates"),
        ("How long until 5 p.m.?", "40 minutes from now.", "clock"),
        ("What time will it be in 1½ hours?", "It'll be 5:50 PM.", "clock"),
    ],
)
def test_written_out(question, said, skill):
    found = skills.answer(question)
    assert (found.text, found.skill) == (said, skill)


@pytest.mark.parametrize(
    ("question", "said"),
    [
        # As the speech models transcribe them, "in" merged or misheard.
        ("What is fifty degrees FN Celsius?", "It's 10 degrees Celsius."),
        ("What's thirty CNF?", "It's 86 degrees Fahrenheit."),
        ("What's thirty CN F.", "It's 86 degrees Fahrenheit."),
        ("What is fifty? Degrees F and degrees C.", "It's 10 degrees Celsius."),
        ("What is ninety eight point six degrees F in Celsius?", "It's 37 degrees Celsius."),
        ("How many cups in one and a half quartz?", "It's 6."),
        ("How many cups in one a half quarts?", "It's 6."),
        ("What's three point five kG in pounds?", "It's 7.716 pounds."),
    ],
)
def test_transcribed(question, said):
    assert skills.answer(question).text == said


def test_normalize():
    assert normalize("What’s 50°F in °C?") == "What's 50 degrees fahrenheit in degrees celsius?"
    assert normalize("½ cup, 1½ cups, 2 1/4 cups, 1/3 cup") == "a half cup, 1.5 cups, 2.25 cups, a third cup"
    assert normalize("what is 1/2") == "what is 1/2"  # a division, left to arithmetic
    assert normalize("80 € in $") == "80 euros in $"
    assert normalize("the 4th at 3pm") == "the 4th at 3pm"  # ordinals and times stay as they are


def test_money_is_live_when_converted():
    assert is_live("how many euros is a dollar")
    assert is_live(normalize("what is 80 € in dollars"))
    assert is_live("what is 5 pounds in dollars")
    assert not is_live(normalize("what's $5 plus $3"))
    assert not is_live("what's 3.5 kg in pounds")


def test_spelling_comes_first():
    # "Weather" reads as live; spelling it is not.
    assert skills.answer("how do you spell weather").skill == "spelling"


def test_not_questions():
    for text in ("thank you", "turn on the lights", "we need milk", "what's the capital of France", ""):
        assert skills.answer(text) is None


def test_everywhere_or_off_in_some_areas():
    assert enabled() == frozenset(SKILLS)
    # Each everywhere unless set: off everywhere, or in some areas.
    assert enabled({"units": False}, "kitchen") == frozenset(SKILLS) - {"units"}
    assert enabled({"units": {"disabled": True, "off": ["nursery"]}}, "kitchen") == frozenset(SKILLS) - {"units"}
    assert enabled({"units": {"off": ["nursery"]}}, "kitchen") == frozenset(SKILLS)
    assert enabled({"units": {"off": ["nursery"]}}, "nursery") == frozenset(SKILLS) - {"units"}
    # In no area (or typed on the web page with none): only off everywhere counts.
    assert enabled({"units": {"off": ["nursery"]}}) == frozenset(SKILLS)
    assert skill_off({"units": {"off": ["nursery"]}}, "units") == ("nursery",) and skill_off({}, "units") == ()
    assert skill_off({"units": False}, "units") is True
    # Anything else counts as on; unknown skills are ignored.
    assert enabled({"units": "no", "spelling": ["sat1"], "nope": False}, "kitchen") == frozenset(SKILLS)


def test_each_skill_is_its_own_module():
    for skill_id, skill in SKILLS.items():
        module = importlib.import_module(f"voice_labs.skills.{skill_id}")
        assert module.SKILL is skill and skill.answer.__module__ == module.__name__
        assert skill.name and skill.description and skill.examples and skill.icon.startswith("mdi:")
        # Each says its own examples are its own.
        for example in skill.examples:
            found = skills.answer(example)
            assert found is not None and found.skill == skill_id, example


def test_days_until(monkeypatch):
    import datetime as dt

    from voice_labs.skills import dates

    class Today(dt.date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 15)

    monkeypatch.setattr(dates.dt, "date", Today)
    assert skills.answer("how many days until Christmas").text == "It's 71 days."
    assert skills.answer("how many days until october 16th").text == "It's 1 day."
    assert skills.answer("how many weeks until halloween").text == "It's 2 weeks."
    assert skills.answer("how many days until valentine's day").text == "It's 122 days."
    # Under a week, in days; a week, one; on the day, today.
    assert skills.answer("how many weeks until october 20").text == "It's 5 days."
    assert skills.answer("how many weeks until october 24").text == "It's 1 week."
    assert skills.answer("how many days until october 15").text == "It's today."


def test_is_it_a_holiday(monkeypatch):
    import datetime as dt

    from voice_labs.skills import dates

    class Today(dt.date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 31)

    monkeypatch.setattr(dates.dt, "date", Today)
    assert answered("Is today Halloween?") == "Yes, today is Halloween."
    assert answered("is it halloween") == "Yes, today is Halloween."
    assert answered("how many days until halloween") == "It's today."
    assert answered("when is halloween") == "It's today."
    assert answered("is it halloween tomorrow") == "No, Halloween is today."
    assert answered("Is it Christmas tomorrow?") == "No, Christmas is in 55 days."
    assert answered("is today a holiday") == "Yes, today is Halloween."
    assert answered("is tomorrow a holiday") == "No, the next one is Veterans Day, in 11 days."
    assert answered("is today october 31") == "Yes, today is October 31."
    # About now, but no holiday or date: not the skill's.
    assert answered("is it raining today") is None
    assert answered("will it be sunny tomorrow") is None
    monkeypatch.setattr(Today, "today", classmethod(lambda cls: cls(2026, 12, 24)))
    assert answered("is christmas tomorrow") == "Yes, tomorrow is Christmas."
    assert answered("is it christmas eve today") == "Yes, today is Christmas Eve."
    monkeypatch.setattr(Today, "today", classmethod(lambda cls: cls(2026, 11, 1)))
    assert answered("is today halloween") == "No, Halloween was yesterday."


def test_responses_as_set():
    assert skill_response({}, "arithmetic") == {"verbosity": "specific", "response": {}}
    settings = {"skill_responses": {"arithmetic": {"verbosity": "verbose", "response": {"brief": "{{ answer }}!"}}}}
    # A level without one says the closest written.
    assert skill_response(settings, "arithmetic") == {
        "verbosity": "verbose",
        "response": {"brief": "{{ answer }}!", "specific": "{{ answer }}!", "verbose": "{{ answer }}!"},
    }
    settings = {"skill_responses": {"dates": {"verbosity": "loud", "response": "{{ response }}"}}}
    assert skill_response(settings, "dates") == {
        "verbosity": "specific",
        "response": {"brief": "{{ response }}", "specific": "{{ response }}", "verbose": "{{ response }}"},
    }


# ------------------------------------------------------------------ settings


def answered(question, **stored):
    found = skills.answer(question, settings=skills.configure(stored))
    return found.text if found else None


def test_settings_as_built_in():
    for skill_id, skill in SKILLS.items():
        settings = skill.settings.load(None)
        # The page's, sent back unchanged, stores nothing.
        assert skill.settings.store(settings.to_json()) is None, skill_id
        assert skill.settings.load(skill.settings.store(settings.to_json())) == settings


def test_units_added_changed_and_taken_out():
    from voice_labs.skills.units import Settings

    page = Settings().to_json()
    assert page["measures"]["volume"] == "milliliter" and page["removed"] == []
    edited = [u for u in page["units"] if u["name"] != "stone"]
    for unit in edited:
        if unit["name"] == "pint":
            unit["size"] = 568.26125  # imperial
    edited.append({"name": "nautical mile", "measures": "length", "size": 1852, "plural": "", "also": ["nmi"]})
    edited.append({"name": "Rankine", "measures": "temperature", "freezes": 491.67, "boils": 671.67, "also": ["R"]})
    stored = Settings.store({"units": edited, "digits": 4})
    assert stored == {"units": {
        "stone": False,
        "pint": {"measures": "volume", "size": 568.26125},
        "nautical mile": {"measures": "length", "size": 1852, "also": ["nmi"]},
        "Rankine": {"measures": "temperature", "freezes": 491.67, "boils": 671.67, "also": ["R"]},
    }}
    assert answered("how many fluid ounces are in a pint", units=stored) == "It's 19.22."
    assert answered("what is 2 nmi in kilometers", units=stored) == "It's 3.704 kilometers."
    assert answered("what is 100 celsius in rankine", units=stored) == "It's 671.7 degrees Rankine."
    assert answered("what is 10 stone in pounds", units=stored) is None
    assert answered("what is 10 stone in pounds") == "It's 140 pounds."
    assert [u["name"] for u in Settings.load(stored).to_json()["removed"]] == ["stone"]


def test_imperial_volumes():
    from voice_labs.skills.units import VOLUMES, Settings

    page = Settings().to_json()
    assert page["volumes"] == "us" and page["presets"]["imperial"]["name"] == "Imperial (UK)"
    # The page picks imperial: its sizes, set on the volumes it has.
    sizes = page["presets"]["imperial"]["sizes"]
    edited = [u | {"size": sizes[u["name"]]} if u["name"] in sizes else u for u in page["units"]]
    stored = Settings.store({"volumes": "imperial", "units": edited, "digits": 4})
    assert stored == {"volumes": "imperial"}
    imperial = Settings.load(stored)
    assert all(u["built_in"] for u in imperial.to_json()["units"])
    assert answered("how many fluid ounces are in a pint", units=stored) == "It's 20."
    assert answered("how many pints are in a gallon", units=stored) == "It's 8."
    assert answered("how many milliliters are in a cup", units=stored) == "It's 250."
    # Said with its measures, a volume is theirs, whichever are set.
    assert answered("how many milliliters are in a US pint", units=stored) == "It's 473.2."
    assert answered("how many milliliters are in an imperial pint") == "It's 568.3."
    assert answered("how many US fluid ounces are in a British pint") == "It's 19.22."
    # A change on top of imperial is stored as one.
    edited = [u | {"size": 240} if u["name"] == "cup" else u for u in imperial.to_json()["units"]]
    assert Settings.store({"volumes": "imperial", "units": edited}) == {
        "volumes": "imperial", "units": {"cup": {"measures": "volume", "size": 240}}}
    assert set(VOLUMES["us"]) == set(VOLUMES["imperial"])
    with pytest.raises(ValueError, match="volumes: one of us, imperial"):
        Settings.load({"volumes": "metric"})


@pytest.mark.parametrize(
    ("units", "error"),
    [
        ([{"name": "smoot", "measures": "length", "size": 0}], "smoot: its size, a number above 0"),
        ([{"name": "smoot", "measures": "length", "size": "tall"}], "smoot: its size: a number"),
        ([{"name": "smoot", "measures": "vibes", "size": 1}], "smoot: it measures one of"),
        ([{"name": "", "measures": "length", "size": 1}], "A unit needs a name"),
        ([{"name": "x", "measures": "temperature", "freezes": 5, "boils": 5}], "freeze and boil"),
        ([{"name": "smoot", "measures": "length", "size": 1, "also": ["mile"]}], "“mile” is both mile and smoot"),
        ([{"name": "cup", "measures": "volume", "size": 250}, {"name": "Cup", "measures": "volume", "size": 240}], "cup is given twice"),
    ],
)
def test_units_refused(units, error):
    from voice_labs.skills.units import UNITS, Settings

    with pytest.raises(ValueError, match=error):
        Settings.store({"units": [u.to_json() for u in UNITS] + units})


def test_precision():
    assert answered("what is 5 miles in kilometers", units={"digits": 2}) == "It's 8 kilometers."
    assert answered("what is 100 divided by 7", arithmetic={"digits": 6}) == "It's 14.2857."
    assert answered("what is 100 divided by 7", arithmetic={"digits": 2}) == "It's 14."
    # Whole numbers in full; never in e-notation.
    assert answered("how many centimeters are in a mile") == "It's 160,934."
    assert answered("how many seconds are in a year") == "It's 31,536,000."
    assert answered("what is 12345.6 plus 1") == "It's 12,347."
    with pytest.raises(ValueError, match="precision"):
        SKILLS["arithmetic"].settings.load({"digits": 0})


def test_spelling_your_way():
    from voice_labs.skills.spelling import Settings

    stored = Settings.store({"words": {"Color": "colour", "my last name": "Hansen", "new york": "New York"}})
    assert stored == {"words": {"color": "colour", "my last name": "Hansen", "new york": "New York"}}
    assert answered("how do you spell color", spelling=stored) == "Colour is spelled C, O, L, O, U, R."
    assert answered("How do you spell my last name?", spelling=stored) == "Hansen is spelled H, A, N, S, E, N."
    assert answered("how is new york spelled", spelling=stored) == "New York is spelled N, E, W, space, Y, O, R, K."
    # A phrase that isn't yours isn't spelled; "how is" asks how it's spelled only with "spelled".
    assert answered("how do you spell my last name") is None
    assert answered("how is mom") is None
    assert answered("how do you spell o'clock") == "O'clock is spelled O, apostrophe, C, L, O, C, K."
    with pytest.raises(ValueError, match="needs a letter"):
        Settings.store({"words": {"x": "--"}})


def test_holidays(monkeypatch):
    import datetime as dt

    from voice_labs.skills import dates

    class Today(dt.date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 15)

    monkeypatch.setattr(dates.dt, "date", Today)
    # Built in: fixed, the nth weekday of a month, from Easter.
    assert answered("when is thanksgiving") == "It's November 26."
    assert answered("when is memorial day") == "It's May 31, 2027."
    assert answered("when is good friday") == "It's March 26, 2027."
    assert answered("what day of the week is christmas") == "It's Friday."
    # "What day" may be either: both.
    assert answered("what day is christmas") == "It's Friday, December 25."
    assert answered("what day is thanksgiving") == "It's Thursday, November 26."
    assert answered("what date is christmas") == "It's December 25."
    assert answered("how many days until the fourth of july") == "It's 262 days."
    page = dates.Settings().to_json()
    assert {"name": "Thanksgiving", "when": "fourth Thursday of November", "also": ["Thanksgiving Day"],
            "next": "2026-11-26", "built_in": True} in page["holidays"]
    edited = [h for h in page["holidays"] if h["name"] != "Juneteenth"]
    for holiday in edited:
        if holiday["name"] == "Thanksgiving":
            holiday["when"] = "second Monday of October"  # Canada's
    edited += [{"name": "My birthday", "when": "June 3", "also": ["my bday"]},
               {"name": "the wedding", "when": "June 20 2027", "also": []}]
    stored = dates.Settings.store({"holidays": edited})
    assert stored == {"holidays": {
        "Juneteenth": False,
        "Thanksgiving": {"when": "second Monday of October", "also": ["Thanksgiving Day"]},
        "My birthday": {"when": "June 3", "also": ["my bday"]},
        "the wedding": "June 20 2027",
    }}
    assert answered("when is thanksgiving", dates=stored) == "It's October 11, 2027."
    assert answered("how many days until my bday", dates=stored) == "It's 231 days."
    assert answered("when is the wedding", dates=stored) == "It's June 20, 2027."
    assert answered("when is juneteenth", dates=stored) is None
    for when, error in (("someday", "when is “someday”"), ("February 30", "when is")):
        with pytest.raises(ValueError, match=error):
            dates.Settings.store({"holidays": [{"name": "x", "when": when}]})
    with pytest.raises(ValueError, match="is both"):
        dates.Settings.store({"holidays": [{"name": "Xmas", "when": "December 25"},
                                           {"name": "Christmas", "when": "December 25", "also": ["xmas"]}]})


def test_24_hour_clock():
    assert answered("what time will it be in 45 minutes", clock={"hours": 24}) == "It'll be 17:05."
    assert answered("what time will it be in 8 hours", clock={"hours": 24}) == "It'll be 0:20."
    assert SKILLS["clock"].settings.store({"hours": 12}) is None


def test_settings_that_cant_be_used():
    # As stored by hand: the built-in ones, and why.
    settings, error = skills.load("units", {"units": {"cup": {"measures": "volume"}}})
    assert settings == skills.BUILT_IN["units"] and error == "cup: its size: a number"
    assert skills.configure({"clock": {"hours": 13}, "nope": {}})["clock"] == skills.BUILT_IN["clock"]
