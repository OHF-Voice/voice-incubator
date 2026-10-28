"""The skills in Italian: asked and answered as Italian speakers would, with
Italian units and holidays."""

from __future__ import annotations

import pytest

from voice_call import languages, skills
from voice_call.skills import SKILLS


@pytest.fixture(autouse=True)
def italian():
    languages.use("it")
    yield
    languages.use(languages.DEFAULT)


@pytest.mark.parametrize(
    ("question", "said", "skill"),
    [
        ("Come si scrive necessario?", "Necessario si scrive N, E, C, E, S, S, A, R, I, O.", "spelling"),
        ("Fai lo spelling di perché", "Perché si scrive P, E, R, C, H, E con l'accento acuto.", "spelling"),
        ("Quanto fa dodici per otto?", "Fa 96.", "arithmetic"),
        ("Quanto fa dodici moltiplicato per otto?", "Fa 96.", "arithmetic"),
        ("Quanto fa trecentoquarantacinque più dodici?", "Fa 357.", "arithmetic"),
        ("Quanto fa novantanove meno settantuno?", "Fa 28.", "arithmetic"),
        ("Quanto fa il 15 % di 80 €?", "Fa 12 euro.", "arithmetic"),
        ("Quanto fa 100 diviso 7?", "Fa 14,29.", "arithmetic"),
        ("Qual è la media di quattro, otto e dodici?", "Fa 8.", "arithmetic"),
        ("Cinque elevato alla terza", "Fa 125.", "arithmetic"),
        ("Che ore saranno tra 45 minuti?", "Saranno le 17 e 5.", "clock"),
        ("Quanto manca a mezzogiorno?", "Mancano 19 ore e 40 minuti.", "clock"),
        ("Che ore saranno tra un'ora e mezza?", "Saranno le 17 e 50.", "clock"),
        ("Quanto manca alle 17 e 20?", "Manca un'ora.", "clock"),
        ("Il 2024 è bisestile?", "Sì.", "dates"),
        ("Che giorno della settimana è il 4 luglio 2026?", "È sabato.", "dates"),
        ("Quand'è la Festa della Repubblica?", "Il 2 giugno.", "dates"),
        ("Quanti giorni ha febbraio 2024?", "Sono 29 giorni.", "dates"),
        ("Che mese viene dopo marzo?", "È aprile.", "dates"),
        ("Quanti grammi ci sono in un etto?", "Sono 100 grammi.", "units"),
        ("Quanti grammi ha una libbra?", "Sono 453,6 grammi.", "units"),
        ("Quanto fa 5 miglia in chilometri?", "Sono 8,047 chilometri.", "units"),
        ("Converti 5 miglia in chilometri", "Sono 8,047 chilometri.", "units"),
        ("100 gradi Fahrenheit in Celsius", "Sono 37,8 gradi Celsius.", "units"),
        ("Quanti millilitri ha una tazza americana?", "Sono 236,6 millilitri.", "units"),
        # An ounce beside a volume is a fluid ounce.
        ("Quante once ci sono in una tazza?", "Sono 8,454 once liquide.", "units"),
        # "Un'ora" is an hour, not now.
        ("Quanti minuti ci sono in un'ora?", "Sono 60 minuti.", "units"),
        # Typed without accents.
        ("quanto fa ventitre piu trentotto", "Fa 61.", "arithmetic"),
        ("il 2023 e bisestile", "No.", "dates"),
        ("che giorno viene dopo lunedi", "È martedì.", "dates"),
    ],
)
def test_italian_answers(question, said, skill):
    found = skills.answer(question)
    assert (found.text, found.skill) == (said, skill)


def test_said_back_in_full():
    levels = skills.answer("Quanti grammi ci sono in un etto?").to_json()["levels"]
    assert levels == {"brief": "100 grammi.", "specific": "Sono 100 grammi.", "verbose": "In un etto ci sono 100 grammi."}
    assert skills.answer("Quanto fa dodici per otto?").to_json()["levels"]["verbose"] == "Dodici per otto fa 96."
    assert skills.answer("Il 2023 è bisestile?").to_json()["levels"]["verbose"] == "No, il 2023 non è bisestile."
    assert skills.answer("Quanto fa 5 miglia in chilometri?").to_json()["levels"]["verbose"] == (
        "5 miglia sono 8,047 chilometri.")
    assert skills.answer("Quanti millilitri ci sono in mezzo litro?").to_json()["levels"]["verbose"] == (
        "In mezzo litro ci sono 500 millilitri.")
    levels = skills.answer("Quanto manca alle 17?").to_json()["levels"]
    assert levels == {"brief": "40 minuti.", "specific": "Mancano 40 minuti.", "verbose": "Alle 17 mancano 40 minuti."}
    assert skills.answer("Che ore saranno tra mezz'ora?").to_json()["levels"]["brief"] == "Le 16 e 50."


def test_days_until(monkeypatch):
    import datetime as dt

    from voice_call.skills import dates

    class Today(dt.date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 15)

    monkeypatch.setattr(dates.dt, "date", Today)
    assert skills.answer("Quanti giorni mancano a Natale?").to_json()["levels"]["verbose"] == (
        "Mancano 71 giorni a Natale.")
    assert skills.answer("Quanti giorni mancano all'Epifania?").to_json()["levels"]["verbose"] == (
        "Mancano 83 giorni all'Epifania.")
    assert skills.answer("Quante settimane mancano alla Festa della mamma?").to_json()["levels"]["verbose"] == (
        "Mancano 29 settimane alla Festa della mamma.")
    assert skills.answer("Quando è Pasqua?").text == "Il 28 marzo 2027."
    assert skills.answer("Quando è Pasquetta?").to_json()["levels"]["verbose"] == "Pasquetta è il 29 marzo 2027."
    assert skills.answer("Quando è l'Immacolata?").text == "L'8 dicembre."
    assert skills.answer("Quando è la festa dei lavoratori?").text == "Il primo maggio."
    # The second Sunday of May.
    assert skills.answer("Quando è la festa della mamma?").text == "Il 9 maggio 2027."


@pytest.mark.parametrize(
    "question",
    ["Che tempo fa domani?", "Che ore sono?", "Che ora è?", "Che giorno è oggi?", "Chi ha vinto la partita ieri?",
     "Quanti dollari fa un euro?", "A che ora chiude il supermercato?", "Chi è il presidente ora?",
     "Quanto costa un bitcoin?"],
)
def test_live_questions(question):
    assert skills.answer(question) is None


def test_hour_and_last_are_not_live():
    # "Un'ora" is an hour, "l'ultimo" a date: neither is the news.
    assert skills.answer("Quanti secondi ci sono in un ora?").text == "Sono 3600 secondi."
    assert skills.answer("Qual è l'ultimo mese dell'anno?").text == "È dicembre."
    assert skills.answer("Qual è il risultato di dodici per otto?").text == "Fa 96."


def test_settings_in_italian():
    for skill_id, skill in SKILLS.items():
        settings = skill.settings.load(None)
        assert skill.settings.store(settings.to_json()) is None, skill_id
    assert SKILLS["clock"].settings.load(None).hours == 24
    units = SKILLS["units"].settings.load(None)
    assert units.volumes == "metric" and units.to_json()["presets"]["metric"]["sizes"]["tazza"] == 250


def test_holidays_written_in_italian_or_english():
    from voice_call.skills.dates import Settings

    stored = Settings.store({"holidays": [*(h.to_json() for h in Settings().holidays),
                                          {"name": "Compleanno della nonna", "when": "12 marzo", "also": []},
                                          {"name": "Anniversario", "when": "June 20", "also": []},
                                          {"name": "Ponte", "when": "40 giorni dopo Pasqua", "also": []}]})
    assert stored == {"holidays": {"Compleanno della nonna": "12 marzo", "Anniversario": "June 20",
                                   "Ponte": "40 giorni dopo Pasqua"}}
    settings = Settings.load({"holidays": {"Compleanno della nonna": "12 marzo", "Anniversario": "June 20"}})
    assert skills.answer("Quando è il compleanno della nonna?", settings={"dates": settings}).text == "Il 12 marzo."
    assert skills.answer("Quando è l'anniversario?", settings={"dates": settings}).text == "Il 20 giugno."
    with pytest.raises(ValueError, match="Like 25 dicembre"):
        Settings.store({"holidays": [{"name": "Mai", "when": "un giorno", "also": []}]})


def test_italian_plurals():
    from voice_call.languages.it.skills.units import forms, plural_of

    assert [plural_of(n) for n in ("tazza", "metro quadrato", "oncia liquida", "cucchiaio", "cucchiaino da tè", "ora")] == [
        "tazze", "metri quadrati", "once liquide", "cucchiai", "cucchiaini da tè", "ore"]
    assert "metro quadrato" in forms("metri quadrati") and "oncia liquida" in forms("once liquide")


def test_each_skill_answers_its_examples():
    for skill_id, skill in SKILLS.items():
        for example in skill.examples:
            found = skills.answer(example)
            assert found is not None and found.skill == skill_id, example


@pytest.mark.parametrize(
    ("said", "number"),
    [("trecentoquarantacinque", 345), ("ventuno", 21), ("ventotto", 28), ("trentatré", 33), ("trentatre", 33),
     ("duemilaventisei", 2026), ("millenovecentonovantanove", 1999), ("centottanta", 180), ("un milione", 1e6),
     ("tre virgola cinque", 3.5), ("due e mezzo", 2.5), ("tre quarti", 0.75), ("meno due", -2), ("1.000", 1000),
     ("1 000", 1000), ("3,5", 3.5)],
)
def test_italian_numbers(said, number):
    from voice_call.languages.it.skills.numbers import parse_num

    assert parse_num(said) == number


def test_a_clock_time_said_as_italian_says_it():
    from voice_call.languages.it.reading import clock

    assert clock("le sette e mezza") == clock("7:30") == (7, 30, None)
    assert clock("le otto meno un quarto di sera") == (7, 45, "pm")
    assert clock("le diciannove e trenta") == (19, 30, None)
    assert clock("l'una e un quarto") == (1, 15, None)
    assert clock("mezzogiorno") == (12, 0, "pm")
