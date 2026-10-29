"""The skills in German: asked and answered as German speakers would, with
German units and holidays."""

from __future__ import annotations

import pytest

from voice_labs import languages, skills
from voice_labs.skills import SKILLS


@pytest.fixture(autouse=True)
def german():
    languages.use("de")
    yield
    languages.use(languages.DEFAULT)


@pytest.mark.parametrize(
    ("question", "said", "skill"),
    [
        ("Wie schreibt man notwendig?", "Notwendig schreibt man N, O, T, W, E, N, D, I, G.", "spelling"),
        ("Kannst du mir Straße buchstabieren?", "Straße schreibt man S, T, R, A, Eszett, E.", "spelling"),
        ("Was ist zwölf mal acht?", "Das ergibt 96.", "arithmetic"),
        ("Was ist dreihundertfünfundvierzig plus zwölf?", "Das ergibt 357.", "arithmetic"),
        ("Wie viel sind 15 Prozent von 80 €?", "Das ergibt 12 Euro.", "arithmetic"),
        ("Was ist 100 geteilt durch 7?", "Das ergibt 14,29.", "arithmetic"),
        ("Die Wurzel aus 144", "Das ergibt 12.", "arithmetic"),
        ("Was ist der Durchschnitt von vier, acht und zwölf?", "Das ergibt 8.", "arithmetic"),
        ("Wie spät ist es in 45 Minuten?", "Dann ist es 17 Uhr 5.", "clock"),
        ("Wie lange noch bis 17 Uhr?", "Noch 40 Minuten.", "clock"),
        ("Wie spät ist es in anderthalb Stunden?", "Dann ist es 17 Uhr 50.", "clock"),
        ("Ist 2024 ein Schaltjahr?", "Ja.", "dates"),
        ("Welcher Wochentag ist der 4. Juli 2026?", "Das ist ein Samstag.", "dates"),
        ("Wann ist Weihnachten?", "Am 25. Dezember.", "dates"),
        ("Wann ist der Tag der Deutschen Einheit?", "Am 3. Oktober.", "dates"),
        ("Wie viele Tage hat der Februar 2024?", "Das sind 29 Tage.", "dates"),
        ("Welcher Monat kommt nach März?", "Das ist der April.", "dates"),
        ("Wie viele Gramm hat ein Pfund?", "Das sind 500 Gramm.", "units"),
        ("Was sind 5 Meilen in Kilometern?", "Das sind 8,047 Kilometer.", "units"),
        ("100 Grad Fahrenheit in Celsius", "Das sind 37,8 Grad Celsius.", "units"),
        ("Wie viele Milliliter hat ein Esslöffel?", "Das sind 15 Milliliter.", "units"),
        ("Wie viele Milliliter hat eine amerikanische Tasse?", "Das sind 236,6 Milliliter.", "units"),
        # An ounce beside a volume is a fluid ounce.
        ("Wie viele Unzen sind in einer Tasse?", "Das sind 8,454 Flüssigunzen.", "units"),
        ("Wie viele Sekunden hat ein Jahr?", "Das sind 31536000 Sekunden.", "units"),
    ],
)
def test_german_answers(question, said, skill):
    found = skills.answer(question)
    assert (found.text, found.skill) == (said, skill)


def test_said_back_in_full():
    levels = skills.answer("Wie viele Tassen sind in einem Liter?").to_json()["levels"]
    assert levels == {"brief": "4 Tassen.", "specific": "Das sind 4 Tassen.", "verbose": "In einem Liter sind 4 Tassen."}
    assert skills.answer("Wie viel sind 15 Prozent von 80?").to_json()["levels"]["verbose"] == "15 Prozent von 80 ist 12."
    assert skills.answer("Ist 1900 ein Schaltjahr?").to_json()["levels"]["verbose"] == "Nein, 1900 ist kein Schaltjahr."


def test_days_until(monkeypatch):
    import datetime as dt

    from voice_labs.skills import dates

    class Today(dt.date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 15)

    monkeypatch.setattr(dates.dt, "date", Today)
    assert skills.answer("Wie viele Tage noch bis Weihnachten?").to_json()["levels"]["verbose"] == (
        "Bis Weihnachten sind es noch 71 Tage.")
    assert skills.answer("Wie viele Wochen bis zum Muttertag?").text == "Noch 29 Wochen."
    assert skills.answer("Wann ist Ostern?").text == "Am 28. März 2027."


@pytest.mark.parametrize(
    "question",
    ["Wie ist das Wetter morgen?", "Wie spät ist es?", "Wer hat gestern das Spiel gewonnen?",
     "Wie viel Dollar sind ein Euro?", "Wann schließt der Supermarkt?"],
)
def test_live_questions(question):
    assert skills.answer(question) is None


def test_settings_in_german():
    for skill_id, skill in SKILLS.items():
        settings = skill.settings.load(None)
        assert skill.settings.store(settings.to_json()) is None, skill_id
    assert SKILLS["clock"].settings.load(None).hours == 24
    units = SKILLS["units"].settings.load(None)
    assert units.volumes == "metric" and units.to_json()["presets"]["metric"]["sizes"]["tasse"] == 250
    # Imperial: its sizes, and stored as only that.
    assert SKILLS["units"].settings.store({**units.to_json(), "volumes": "imperial", "units": [
        u for u in SKILLS["units"].settings.load({"volumes": "imperial"}).to_json()["units"]]}) == {"volumes": "imperial"}


def test_holidays_written_in_german_or_english():
    from voice_labs.skills.dates import Settings

    stored = Settings.store({"holidays": [*(h.to_json() for h in Settings().holidays),
                                          {"name": "Omas Geburtstag", "when": "12. März", "also": []},
                                          {"name": "Jahrestag", "when": "June 20", "also": []},
                                          {"name": "Brückentag", "when": "40 Tage nach Ostern", "also": []}]})
    assert stored == {"holidays": {"Omas Geburtstag": "12. März", "Jahrestag": "June 20",
                                   "Brückentag": "40 Tage nach Ostern"}}
    settings = Settings.load({"holidays": {"Omas Geburtstag": "12. März", "Jahrestag": "June 20"}})
    assert skills.answer("Wann ist Omas Geburtstag?", settings={"dates": settings}).text == "Am 12. März."
    assert skills.answer("Wann ist Jahrestag?", settings={"dates": settings}).text == "Am 20. Juni."
    with pytest.raises(ValueError, match="Like 25. Dezember"):
        Settings.store({"holidays": [{"name": "Nie", "when": "irgendwann", "also": []}]})


def test_each_skill_answers_its_examples():
    for skill_id, skill in SKILLS.items():
        for example in skill.examples:
            found = skills.answer(example)
            assert found is not None and found.skill == skill_id, example
