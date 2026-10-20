"""The skills in Dutch: asked and answered as Dutch speakers would, with
Dutch units and holidays."""

from __future__ import annotations

import pytest

from voice_call import languages, skills
from voice_call.skills import SKILLS


@pytest.fixture(autouse=True)
def dutch():
    languages.use("nl")
    yield
    languages.use(languages.DEFAULT)


@pytest.mark.parametrize(
    ("question", "said", "skill"),
    [
        ("Hoe spel je noodzakelijk?", "Noodzakelijk spel je N, O, O, D, Z, A, K, E, L, I, J, K.", "spelling"),
        ("Wat is twaalf keer acht?", "Dat is 96.", "arithmetic"),
        ("Wat is driehonderdvijfenveertig plus twaalf?", "Dat is 357.", "arithmetic"),
        ("Hoeveel is 15 procent van 80 €?", "Dat is 12 euro.", "arithmetic"),
        ("Wat is 100 gedeeld door 7?", "Dat is 14,29.", "arithmetic"),
        ("Wat is het gemiddelde van vier, acht en twaalf?", "Dat is 8.", "arithmetic"),
        ("Wat is 45 minuten na 15 uur?", "Dat is 15 uur 45.", "clock"),
        ("Is 2024 een schrikkeljaar?", "Ja.", "dates"),
        ("Welke dag van de week is 4 juli 2026?", "Dat is een zaterdag.", "dates"),
        ("Wanneer is Koningsdag?", "Op 27 april.", "dates"),
        ("Hoeveel dagen heeft februari 2024?", "Dat zijn 29 dagen.", "dates"),
        ("Hoeveel gram is een pond?", "Dat is 500 gram.", "units"),
        ("Wat is 5 mijl in kilometer?", "Dat is 8,047 kilometer.", "units"),
        ("100 graden Fahrenheit in Celsius", "Dat is 37,8 graden Celsius.", "units"),
        ("Hoeveel kopjes gaan er in een liter?", "Dat is 4 kopjes.", "units"),
    ],
)
def test_dutch_answers(question, said, skill):
    found = skills.answer(question)
    assert (found.text, found.skill) == (said, skill)


def test_days_until(monkeypatch):
    import datetime as dt

    from voice_call.skills import dates

    class Today(dt.date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 15)

    monkeypatch.setattr(dates.dt, "date", Today)
    assert skills.answer("Hoeveel dagen nog tot kerst?").to_json()["levels"]["verbose"] == "Het is nog 71 dagen tot Kerst."
    assert skills.answer("Wanneer is Pasen?").text == "Op 28 maart 2027."


@pytest.mark.parametrize("question", ["Hoe is het weer morgen?", "Hoe laat is het?", "Hoeveel dollar is een euro?"])
def test_live_questions(question):
    assert skills.answer(question) is None


def test_settings_in_dutch():
    for skill_id, skill in SKILLS.items():
        settings = skill.settings.load(None)
        assert skill.settings.store(settings.to_json()) is None, skill_id
    assert SKILLS["clock"].settings.load(None).hours == 24


def test_each_skill_answers_its_examples():
    for skill_id, skill in SKILLS.items():
        for example in skill.examples:
            found = skills.answer(example)
            assert found is not None and found.skill == skill_id, example


def test_a_clock_time_written_as_one():
    from voice_call.languages.nl.reading import clock

    assert clock("halfzeven") == clock("half zeven") == (6, 30, None)
    assert clock("kwart over zeven 's avonds") == (7, 15, "pm")
