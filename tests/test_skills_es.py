"""The skills in Spanish: asked and answered as Spanish speakers would, with
Spanish units and holidays."""

from __future__ import annotations

import pytest

from voice_call import languages, skills
from voice_call.skills import SKILLS


@pytest.fixture(autouse=True)
def spanish():
    languages.use("es")
    yield
    languages.use(languages.DEFAULT)


@pytest.mark.parametrize(
    ("question", "said", "skill"),
    [
        ("¿Cómo se escribe necesario?", "Necesario se escribe N, E, C, E, S, A, R, I, O.", "spelling"),
        ("¿Puedes deletrearme cigüeña?", "Cigüeña se escribe C, I, G, U con diéresis, E, Eñe, A.", "spelling"),
        ("¿Cuánto es doce por ocho?", "Son 96.", "arithmetic"),
        ("¿Cuánto es trescientos cuarenta y cinco más doce?", "Son 357.", "arithmetic"),
        ("¿Cuánto es el 15 por ciento de 80 €?", "Es 12 euros.", "arithmetic"),
        ("¿Cuánto es 100 entre 7?", "Son 14,29.", "arithmetic"),
        ("La raíz cuadrada de 144", "Es 12.", "arithmetic"),
        ("¿Cuál es la media de cuatro, ocho y doce?", "Es 8.", "arithmetic"),
        # "Y" joins tens and units, and a list.
        ("¿Cuál es la media de treinta y cinco y cuarenta y cinco?", "Es 40.", "arithmetic"),
        ("¿Qué hora es 45 minutos después de las 15?", "Son las 15 y 45.", "clock"),
        ("¿Qué hora será 90 minutos antes del mediodía?", "Son las 10 y 30.", "clock"),
        ("¿Qué hora es una hora y media después de las tres de la tarde?", "Son las 16 y 30.", "clock"),
        ("¿2024 es bisiesto?", "Sí.", "dates"),
        ("¿Qué día de la semana es el 4 de julio de 2026?", "Es sábado.", "dates"),
        ("¿Cuándo es Navidad?", "El 25 de diciembre.", "dates"),
        ("¿Cuándo es la Fiesta Nacional?", "El 12 de octubre.", "dates"),
        ("¿Cuántos días tiene febrero de 2024?", "Son 29 días.", "dates"),
        ("¿Qué mes va después de marzo?", "Es abril.", "dates"),
        ("¿Cuántos gramos tiene una libra?", "Son 453,6 gramos.", "units"),
        ("¿Cuántos kilómetros son 5 millas?", "Son 8,047 kilómetros.", "units"),
        ("100 grados Fahrenheit a Celsius", "Son 37,8 grados Celsius.", "units"),
        ("¿Cuántas tazas hay en un litro?", "Son 4 tazas.", "units"),
        ("¿Cuántos mililitros tiene una taza americana?", "Son 236,6 mililitros.", "units"),
        # An ounce beside a volume is a fluid ounce.
        ("¿Cuántas onzas hay en una taza?", "Son 8,454 onzas líquidas.", "units"),
        ("¿Cuantos segundos tiene un año?", "Son 31536000 segundos.", "units"),
    ],
)
def test_spanish_answers(question, said, skill):
    found = skills.answer(question)
    assert (found.text, found.skill) == (said, skill)


def test_said_back_in_full():
    levels = skills.answer("¿Cuántas tazas hay en un litro?").to_json()["levels"]
    assert levels == {"brief": "4 tazas.", "specific": "Son 4 tazas.", "verbose": "En un litro hay 4 tazas."}
    assert skills.answer("¿Cuánto es doce por ocho?").to_json()["levels"]["verbose"] == "Doce por ocho son 96."
    assert skills.answer("La raíz cuadrada de 144").to_json()["levels"]["verbose"] == "La raíz cuadrada de 144 es 12."
    assert skills.answer("¿Es 1900 un año bisiesto?").to_json()["levels"]["verbose"] == "No, 1900 no es bisiesto."
    assert skills.answer("¿Cuántos kilómetros son 5 millas?").to_json()["levels"]["verbose"] == (
        "5 millas son 8,047 kilómetros.")


def test_days_until(monkeypatch):
    import datetime as dt

    from voice_call.skills import dates

    class Today(dt.date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 15)

    monkeypatch.setattr(dates.dt, "date", Today)
    assert skills.answer("¿Cuántos días faltan para Navidad?").to_json()["levels"]["verbose"] == (
        "Faltan 71 días para Navidad.")
    assert skills.answer("¿Cuántas semanas faltan para el Día de la Madre?").to_json()["levels"]["verbose"] == (
        "Faltan 28 semanas para el Día de la Madre.")
    assert skills.answer("¿Cuándo es el Domingo de Resurrección?").text == "El 28 de marzo de 2027."
    assert skills.answer("¿Cuándo es la Semana Santa?").to_json()["levels"]["verbose"] == (
        "La Semana Santa empieza el 21 de marzo de 2027.")


@pytest.mark.parametrize(
    "question",
    ["¿Qué tiempo hace mañana?", "¿Qué hora es?", "¿Quién ganó ayer el partido?", "¿Cuántos dólares son un euro?",
     "¿A qué hora cierra el supermercado?"],
)
def test_live_questions(question):
    assert skills.answer(question) is None


def test_morning_and_last_are_not_live():
    # "De la mañana" is a time of day; "el último" a date, not the news.
    assert skills.answer("¿Qué hora es 30 minutos después de las siete de la mañana?").text == "Son las 7 y 30."
    assert skills.answer("¿Cuál es el último mes del año?").text == "Es diciembre."


def test_settings_in_spanish():
    for skill_id, skill in SKILLS.items():
        settings = skill.settings.load(None)
        assert skill.settings.store(settings.to_json()) is None, skill_id
    assert SKILLS["clock"].settings.load(None).hours == 24
    units = SKILLS["units"].settings.load(None)
    assert units.volumes == "metric" and units.to_json()["presets"]["metric"]["sizes"]["taza"] == 250


def test_holidays_written_in_spanish_or_english():
    from voice_call.skills.dates import Settings

    stored = Settings.store({"holidays": [*(h.to_json() for h in Settings().holidays),
                                          {"name": "Cumpleaños de la abuela", "when": "12 de marzo", "also": []},
                                          {"name": "Aniversario", "when": "June 20", "also": []},
                                          {"name": "Puente", "when": "40 días después de Pascua", "also": []}]})
    assert stored == {"holidays": {"Cumpleaños de la abuela": "12 de marzo", "Aniversario": "June 20",
                                   "Puente": "40 días después de Pascua"}}
    settings = Settings.load({"holidays": {"Cumpleaños de la abuela": "12 de marzo", "Aniversario": "June 20"}})
    assert skills.answer("¿Cuándo es el cumpleaños de la abuela?", settings={"dates": settings}).text == "El 12 de marzo."
    assert skills.answer("¿Cuándo es el aniversario?", settings={"dates": settings}).text == "El 20 de junio."
    with pytest.raises(ValueError, match="Like 25 de diciembre"):
        Settings.store({"holidays": [{"name": "Nunca", "when": "algún día", "also": []}]})


def test_each_skill_answers_its_examples():
    for skill_id, skill in SKILLS.items():
        for example in skill.examples:
            found = skills.answer(example)
            assert found is not None and found.skill == skill_id, example


def test_a_clock_time_said_as_spanish_says_it():
    from voice_call.languages.es.reading import clock

    assert clock("las siete y media") == clock("7:30") == (7, 30, None)
    assert clock("las ocho menos cuarto de la tarde") == (7, 45, "pm")
    assert clock("las ocho menos diez") == (7, 50, None)
    assert clock("la una y cuarto") == (1, 15, None)
    assert clock("las diecinueve treinta") == (19, 30, None)
    assert clock("a las doce de la noche") == clock("medianoche") == (0, 0, "am")
    assert clock("mediodía") == (12, 0, "pm")


def test_places_said_with_their_article():
    say = languages.speech()
    assert say.place([("area", "Cocina")]) == "en la cocina"
    assert say.place([("area", "Salón")], to=True) == "al salón"
    assert say.place([("floor", "Primera planta")]) == "en la primera planta"
    assert say.place([("area", "Cuarto de baño")], to=True) == "al cuarto de baño"
    assert say.place([("floor", "Arriba")]) == "arriba"
    assert say.duration(21 * 60) == "veintiún minutos" and say.duration(3600 + 300) == "una hora y 5 minutos"
    assert say.clock(1, 5) == "la una y 5" and say.clock(19, 30) == "las 19 y 30"
