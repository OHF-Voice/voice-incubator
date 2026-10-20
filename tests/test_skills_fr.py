"""The skills in French: asked and answered as French speakers would, with
French units and holidays."""

from __future__ import annotations

import pytest

from voice_call import languages, skills
from voice_call.skills import SKILLS


@pytest.fixture(autouse=True)
def french():
    languages.use("fr")
    yield
    languages.use(languages.DEFAULT)


@pytest.mark.parametrize(
    ("question", "said", "skill"),
    [
        ("Comment s'écrit nécessaire ?", "Nécessaire s'écrit N, E accent aigu, C, E, S, S, A, I, R, E.", "spelling"),
        ("Combien font douze fois huit ?", "Ça fait 96.", "arithmetic"),
        ("Combien font quatre-vingt-dix-neuf moins soixante et onze ?", "Ça fait 28.", "arithmetic"),
        ("Combien font 15 % de 80 € ?", "Ça fait 12 euros.", "arithmetic"),
        ("Combien font 100 divisé par 7 ?", "Ça fait 14,29.", "arithmetic"),
        ("Quelle est la moyenne de quatre, huit et douze ?", "Ça fait 8.", "arithmetic"),
        ("Quelle heure est-il 45 minutes après 15 heures ?", "Ça donne 15 heures 45.", "clock"),
        ("2024 est-elle une année bissextile ?", "Oui.", "dates"),
        ("Quel jour de la semaine tombe le 4 juillet 2026 ?", "C'est un samedi.", "dates"),
        ("C'est quand la Toussaint ?", "Le 1er novembre.", "dates"),
        ("Combien de jours y a-t-il en février 2024 ?", "Ça fait 29 jours.", "dates"),
        ("Combien de grammes dans une livre ?", "Ça fait 500 grammes.", "units"),
        ("Combien font 5 miles en kilomètres ?", "Ça fait 8,047 kilomètres.", "units"),
        ("100 degrés Fahrenheit en Celsius", "Ça fait 37,8 degrés Celsius.", "units"),
        ("Combien de cuillères à café dans une cuillère à soupe ?", "Ça fait 3 cuillères à café.", "units"),
    ],
)
def test_french_answers(question, said, skill):
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
    assert skills.answer("Combien de jours avant Noël ?").to_json()["levels"]["verbose"] == "Il reste 71 jours avant Noël."
    assert skills.answer("Quand tombe Pâques ?").text == "Le 28 mars 2027."
    # The last Sunday of May, or the first of June when Pentecost takes it.
    assert skills.answer("Quand est la fête des mères ?").text == "Le 30 mai 2027."


def test_mothers_day_gives_way_to_pentecost():
    from voice_call.languages.fr.skills.dates import when

    assert when("dernier dimanche de mai, sauf Pentecôte").on(2034).isoformat() == "2034-06-04"
    assert when("dernier dimanche de mai, sauf Pentecôte").on(2026).isoformat() == "2026-05-31"


@pytest.mark.parametrize("question", ["Quel temps fait-il demain ?", "Quelle heure est-il ?", "Combien de dollars vaut un euro ?"])
def test_live_questions(question):
    assert skills.answer(question) is None


def test_settings_in_french():
    for skill_id, skill in SKILLS.items():
        settings = skill.settings.load(None)
        assert skill.settings.store(settings.to_json()) is None, skill_id
    assert SKILLS["clock"].settings.load(None).hours == 24


def test_each_skill_answers_its_examples():
    for skill_id, skill in SKILLS.items():
        for example in skill.examples:
            found = skills.answer(example)
            assert found is not None and found.skill == skill_id, example


@pytest.mark.parametrize(
    ("said", "number"),
    [("trois cent quarante-cinq", 345), ("quatre-vingt-dix-neuf", 99), ("soixante et onze", 71), ("septante-sept", 77),
     ("deux mille vingt-six", 2026), ("trois virgule cinq", 3.5), ("1 000", 1000)],
)
def test_french_numbers(said, number):
    from voice_call.languages.fr.skills.numbers import parse_num

    assert parse_num(said) == number


def test_a_clock_time_said_in_french():
    from voice_call.languages.fr.reading import clock

    assert clock("sept heures et demie du matin") == (7, 30, "am")
    assert clock("huit heures moins le quart") == (7, 45, None)
    assert clock("19h30") == (19, 30, None)
