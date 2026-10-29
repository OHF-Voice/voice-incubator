"""The skills in Polish: asked and answered as Polish speakers would, with
Polish units, holidays and the cases of numbers and names."""

from __future__ import annotations

import pytest

from voice_labs import languages, skills
from voice_labs.skills import SKILLS


@pytest.fixture(autouse=True)
def polish():
    languages.use("pl")
    yield
    languages.use(languages.DEFAULT)


@pytest.mark.parametrize(
    ("question", "said", "skill"),
    [
        ("Jak się pisze żółw?", "Żółw pisze się: Zet z kropką, O z kreską, Ł, Wu.", "spelling"),
        ("Przeliteruj chrząszcz", "Chrząszcz pisze się: C, H, R, Zet, A z ogonkiem, S, Zet, C, Zet.", "spelling"),
        ("Ile to jest dwanaście razy osiem?", "To 96.", "arithmetic"),
        ("Ile jest trzysta czterdzieści pięć plus dwanaście?", "To 357.", "arithmetic"),
        ("Ile to jest 15 procent z 80 zł?", "To 12 złotych.", "arithmetic"),
        ("Ile to jest 15% z 80 €?", "To 12 euro.", "arithmetic"),
        ("Ile to 100 przez 7?", "To 14,29.", "arithmetic"),
        ("Pierwiastek z 144", "To 12.", "arithmetic"),
        # Numbers in the genitive, as "średnia z" takes them; "i" joins a list.
        ("Jaka jest średnia z czterech, ośmiu i dwunastu?", "To 8.", "arithmetic"),
        ("Średnia z dwudziestu pięciu i trzydziestu pięciu", "To 30.", "arithmetic"),
        ("Ile to jest dwa i pół razy cztery?", "To 10.", "arithmetic"),
        ("Dwa do potęgi dziesiątej", "To 1024.", "arithmetic"),
        ("Która godzina będzie za 45 minut?", "Będzie siedemnasta zero pięć.", "clock"),
        ("Ile jeszcze do siedemnastej?", "Jeszcze 40 minut.", "clock"),
        ("Która godzina będzie za półtorej godziny?", "Będzie siedemnasta pięćdziesiąt.", "clock"),
        ("Czy 2024 jest przestępny?", "Tak.", "dates"),
        ("Jaki dzień tygodnia będzie 4 lipca 2026?", "To sobota.", "dates"),
        ("Kiedy jest Boże Narodzenie?", "Dwudziestego piątego grudnia.", "dates"),
        ("Kiedy jest Święto Niepodległości?", "Jedenastego listopada.", "dates"),
        ("Kiedy są Andrzejki?", "Dwudziestego dziewiątego listopada.", "dates"),
        ("Ile dni ma luty 2024?", "To 29 dni.", "dates"),
        ("Jaki miesiąc jest po marcu?", "To kwiecień.", "dates"),
        ("Ile tygodni ma rok?", "To 52 tygodnie.", "dates"),
        ("Ile gramów ma funt?", "To 453,6 grama.", "units"),
        ("Ile kilometrów to 5 mil?", "To 8,047 kilometra.", "units"),
        ("100 stopni Fahrenheita na Celsjusza", "To 37,8 stopnia Celsjusza.", "units"),
        ("Ile szklanek jest w litrze?", "To 4 szklanki.", "units"),
        ("Ile mililitrów ma szklanka amerykańska?", "To 236,6 mililitra.", "units"),
        ("Ile dekagramów ma kilogram?", "To 100 dekagramów.", "units"),
        # An ounce beside a volume is a fluid ounce.
        ("Ile uncji jest w szklance?", "To 8,454 uncji płynu.", "units"),
        ("Ile sekund ma rok?", "To 31536000 sekund.", "units"),
        # Typed without diacritics.
        ("Ile gramow ma funt?", "To 453,6 grama.", "units"),
    ],
)
def test_polish_answers(question, said, skill):
    found = skills.answer(question)
    assert (found.text, found.skill) == (said, skill)


def test_said_back_in_full():
    levels = skills.answer("Ile szklanek jest w litrze?").to_json()["levels"]
    assert levels == {"brief": "4 szklanki.", "specific": "To 4 szklanki.", "verbose": "W litrze są 4 szklanki."}
    assert skills.answer("Ile to jest dwanaście razy osiem?").to_json()["levels"]["verbose"] == "Dwanaście razy osiem to 96."
    assert skills.answer("Pomnóż dwanaście przez osiem").to_json()["levels"]["verbose"] == "Dwanaście razy osiem to 96."
    assert skills.answer("Pierwiastek z 144").to_json()["levels"]["verbose"] == "Pierwiastek z 144 to 12."
    assert skills.answer("Czy rok 1900 był przestępny?").to_json()["levels"]["verbose"] == "Nie, rok 1900 nie jest przestępny."
    assert skills.answer("Ile kilometrów to 5 mil?").to_json()["levels"]["verbose"] == "5 mil to 8,047 kilometra."
    assert skills.answer("Przelicz 2 mile na kilometry").to_json()["levels"]["verbose"] == "Dwie mile to 3,219 kilometra."
    assert skills.answer("Ile gramów ma funt?").to_json()["levels"]["verbose"] == "Funt ma 453,6 grama."
    assert skills.answer("Jaki dzień tygodnia będzie 4 lipca 2026?").to_json()["levels"]["verbose"] == (
        "Czwartego lipca dwa tysiące dwudziestego szóstego roku jest sobota.")


def test_days_until(monkeypatch):
    import datetime as dt

    from voice_labs.skills import dates

    class Today(dt.date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 15)

    monkeypatch.setattr(dates.dt, "date", Today)
    # Asked by its name in the genitive, said in the nominative.
    assert skills.answer("Ile dni zostało do Bożego Narodzenia?").to_json()["levels"] == {
        "brief": "71 dni.", "specific": "Zostało 71 dni.", "verbose": "Boże Narodzenie jest za 71 dni."}
    assert skills.answer("Ile tygodni zostało do Dnia Matki?").to_json()["levels"]["verbose"] == (
        "Dzień Matki jest za 31 tygodni.")
    assert skills.answer("Ile dni do Wigilii?").text == "Zostało 70 dni."
    assert skills.answer("Ile dni do 17 października?").to_json()["levels"]["verbose"] == (
        "Do siedemnastego października zostały 2 dni.")
    assert skills.answer("Kiedy jest Wielkanoc?").text == "Dwudziestego ósmego marca dwa tysiące dwudziestego siódmego roku."
    assert skills.answer("W jaki dzień tygodnia wypada Wigilia?").to_json()["levels"]["verbose"] == (
        "Wigilia wypada w czwartek.")


@pytest.mark.parametrize(
    "question",
    ["Jaka będzie jutro pogoda?", "Która godzina?", "Kto wygrał wczoraj mecz?", "Ile złotych to jedno euro?",
     "O której zamykają Biedronkę?", "Jaki jest dzisiaj dzień?"],
)
def test_live_questions(question):
    assert skills.answer(question) is None


def test_morning_and_last_are_not_live():
    # "Rano" is a time of day; "ostatni" a date, not the news.
    assert skills.answer("Ile jeszcze do siódmej rano?").text == "Jeszcze 14 godzin i 40 minut."
    assert skills.answer("Jaki jest ostatni miesiąc roku?").text == "To grudzień."


def test_settings_in_polish():
    for skill_id, skill in SKILLS.items():
        settings = skill.settings.load(None)
        assert skill.settings.store(settings.to_json()) is None, skill_id
    assert SKILLS["clock"].settings.load(None).hours == 24
    units = SKILLS["units"].settings.load(None)
    assert units.volumes == "metric" and units.to_json()["presets"]["metric"]["sizes"]["szklanka"] == 250


def test_holidays_written_in_polish_or_english():
    from voice_labs.skills.dates import Settings

    stored = Settings.store({"holidays": [*(h.to_json() for h in Settings().holidays),
                                          {"name": "Urodziny babci", "when": "12 marca", "also": []},
                                          {"name": "Rocznica", "when": "June 20", "also": []},
                                          {"name": "Majówka", "when": "40 dni po Wielkanocy", "also": []}]})
    assert stored == {"holidays": {"Urodziny babci": "12 marca", "Rocznica": "June 20", "Majówka": "40 dni po Wielkanocy"}}
    settings = Settings.load({"holidays": {"Urodziny babci": "12 marca", "Rocznica": "June 20"}})
    assert skills.answer("Kiedy są urodziny babci?", settings={"dates": settings}).text == "Dwunastego marca."
    assert skills.answer("Kiedy jest rocznica?", settings={"dates": settings}).text == "Dwudziestego czerwca."
    with pytest.raises(ValueError, match="Like 25 grudnia"):
        Settings.store({"holidays": [{"name": "Nigdy", "when": "kiedyś", "also": []}]})


def test_each_skill_answers_its_examples():
    for skill_id, skill in SKILLS.items():
        for example in skill.examples:
            found = skills.answer(example)
            assert found is not None and found.skill == skill_id, example


def test_numbers_in_words():
    from voice_labs.languages.pl.skills.numbers import ordinal, parse_num, plural, spell

    assert parse_num("trzysta czterdzieści pięć") == 345 and parse_num("ośmiu") == 8
    assert parse_num("dwa tysiące dwadzieścia cztery") == 2024 and parse_num("trzy przecinek pięć") == 3.5
    assert parse_num("półtora") == 1.5 and parse_num("dwa i pół") == 2.5
    assert [plural(n, "minuta", "minuty", "minut", "minuty") for n in (1, 2, 5, 12, 22, 2.5)] == [
        "minuta", "minuty", "minut", "minut", "minuty", "minuty"]
    assert spell(22, feminine=True) == "dwadzieścia dwie" and spell(21, feminine=True) == "dwadzieścia jeden"
    assert ordinal(25, "gen") == "dwudziestego piątego" and ordinal(2026, "gen") == "dwa tysiące dwudziestego szóstego"
    assert ordinal(2000, "gen") == "dwutysięcznego" and ordinal(21, "loc_f") == "dwudziestej pierwszej"


def test_a_clock_time_said_as_polish_says_it():
    from voice_labs.languages.pl.reading import clock

    assert clock("siódma trzydzieści") == clock("7:30") == clock("wpół do ósmej") == (7, 30, None)
    assert clock("o siódmej") == clock("na siódmą") == (7, 0, None)
    assert clock("za kwadrans ósma") == (7, 45, None) and clock("kwadrans po siódmej") == (7, 15, None)
    assert clock("dwudziesta pierwsza zero pięć") == (21, 5, None)
    assert clock("o trzeciej po południu") == (3, 0, "pm") and clock("siódma trzydzieści rano") == (7, 30, "am")
    assert clock("o jedenastej w nocy") == (11, 0, "pm") and clock("o drugiej w nocy") == (2, 0, "am")
    assert clock("o północy") == (0, 0, "am") and clock("w południe") == (12, 0, "pm")


def test_places_said_in_their_case():
    say = languages.speech()
    assert say.place([("area", "Kuchnia")]) == "w kuchni"
    assert say.place([("area", "Łazienka")]) == "w łazience"
    assert say.place([("area", "Salon")], to=True) == "do salonu"
    assert say.place([("area", "Pokój dzienny")]) == "w pokoju dziennym"
    assert say.place([("area", "Pokój dzienny")], to=True) == "do pokoju dziennego"
    assert say.place([("floor", "Pierwsze piętro")]) == "na pierwszym piętrze"
    assert say.place([("floor", "Parter")], to=True) == "na parter"
    assert say.place([("area", "Duża łazienka"), ("area", "Taras")]) == "w dużej łazience i na tarasie"
    assert say.place([("area", "Pokój Ani")]) == "w pokoju Ani"
    assert say.place([("area", "Wspólny pokój")]) == "we wspólnym pokoju"
    # A name it doesn't know stays as named, after a noun that declines.
    assert say.place([("area", "Office")]) == "w pomieszczeniu Office"
    assert say.place([("floor", "Antresola górna")], to=True) == "na antresolę górną"


def test_durations_and_times_said_as_polish_says_them():
    say = languages.speech()
    assert say.duration(60) == "jedna minuta" and say.duration(60, adjective=True) == "jedną minutę"
    assert say.duration(120) == "dwie minuty" and say.duration(300) == "5 minut"
    assert say.duration(22 * 60) == "dwadzieścia dwie minuty" and say.duration(3600 + 300) == "jedna godzina i 5 minut"
    assert say.clock(7, 30) == "siódma trzydzieści" and say.clock(21, 5) == "dwudziesta pierwsza zero pięć"
    assert say.clock(0, 0) == "północ"
    timer = {"name": "pizzę", "created_duration": 600, "remaining": 412, "status": "active"}
    assert say.timer_started(timer, (22, 5))["verbose"] == "Ustawiono minutnik pizza, zadzwoni o dwudziestej drugiej zero pięć."
    assert say.timer_status([timer]) == "Do końca minutnika pizza zostało 6 minut i pięćdziesiąt dwie sekundy."
    assert say.alarm_set((7, 30), 3600)["specific"] == "Ustawiono budzik na siódmą trzydzieści."
