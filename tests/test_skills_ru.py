"""The skills in Russian: asked and answered as Russian speakers would, with
units and holidays of Russia -- and what the app says in Russian."""

from __future__ import annotations

import datetime as dt

import pytest

from voice_labs import languages, skills
from voice_labs.skills import SKILLS


@pytest.fixture(autouse=True)
def russian():
    languages.use("ru")
    yield
    languages.use(languages.DEFAULT)


@pytest.mark.parametrize(
    ("question", "said", "skill"),
    [
        ("Как пишется слово молоко?", "Молоко пишется так: эм, о, эль, о, ка, о.", "spelling"),
        ("Продиктуй по буквам ёжик", "Ёжик пишется так: ё, жэ, и, ка.", "spelling"),
        ("По буквам подъезд", "Подъезд пишется так: пэ, о, дэ, твёрдый знак, е, зэ, дэ.", "spelling"),
        ("Сколько будет двенадцать умножить на восемь?", "Это 96.", "arithmetic"),
        ("Сколько будет триста сорок пять плюс двенадцать?", "Это 357.", "arithmetic"),
        ("Двенадцать на восемь", "Это 96.", "arithmetic"),
        ("Сколько будет 15 процентов от 80 €?", "Это 12 евро.", "arithmetic"),
        ("Сколько будет 100 разделить на 7?", "Это 14,29.", "arithmetic"),
        # A number in whatever case it is said in.
        ("Квадратный корень из ста сорока четырёх", "Это 12.", "arithmetic"),
        ("Среднее арифметическое четырёх, восьми и двенадцати", "Это 8.", "arithmetic"),
        ("Два в десятой степени", "Это 1024.", "arithmetic"),
        ("Сколько будет два с половиной умножить на четыре?", "Это 10.", "arithmetic"),
        ("Который час будет через 45 минут?", "Будет 17:05.", "clock"),
        ("Сколько ещё до пяти часов вечера?", "Ещё 40 минут.", "clock"),
        ("Который час будет через полтора часа?", "Будет 17:50.", "clock"),
        ("2024 год високосный?", "Да.", "dates"),
        ("Какой день недели будет 4 июля 2026 года?", "Суббота.", "dates"),
        ("Когда Рождество?", "Седьмое января.", "dates"),
        ("Когда День Победы?", "Девятое мая.", "dates"),
        ("Какого числа День народного единства?", "Четвёртое ноября.", "dates"),
        ("Сколько дней в феврале 2024 года?", "29 дней.", "dates"),
        ("Какой месяц после марта?", "Апрель.", "dates"),
        ("Сколько граммов в фунте?", "Это 453,6 грамма.", "units"),
        ("Сколько километров в пяти милях?", "Это 8,047 километра.", "units"),
        ("Переведи 100 градусов по Фаренгейту в Цельсий", "Это 37,8 градуса Цельсия.", "units"),
        ("Сколько стаканов в литре?", "Это 4 стакана.", "units"),
        ("Сколько миллилитров в американском стакане?", "Это 236,6 миллилитра.", "units"),
        # An ounce beside a volume is a fluid ounce.
        ("Сколько унций в стакане?", "Это 8,454 жидкой унции.", "units"),
        ("Сколько секунд в году?", "Это 31536000 секунд.", "units"),
        # Feminine two, written out for the voice.
        ("Сколько чайных ложек в двух столовых ложках?", "Это 6 чайных ложек.", "units"),
        ("Сколько миль в 3,2187 километра?", "Это две мили.", "units"),
    ],
)
def test_russian_answers(question, said, skill):
    found = skills.answer(question)
    assert (found.text, found.skill) == (said, skill)


def test_said_back_in_full():
    levels = skills.answer("Сколько стаканов в литре?").to_json()["levels"]
    assert levels == {"brief": "4 стакана.", "specific": "Это 4 стакана.", "verbose": "В литре 4 стакана."}
    assert skills.answer("Сколько будет двенадцать умножить на восемь?").to_json()["levels"]["verbose"] == (
        "Двенадцать умножить на восемь будет 96.")
    assert skills.answer("Квадратный корень из 144").to_json()["levels"]["verbose"] == "Квадратный корень из 144 — 12."
    assert skills.answer("Является ли 1900 год високосным?").to_json()["levels"]["verbose"] == (
        "Нет, тысяча девятисотый год — не високосный.")
    assert skills.answer("5 миль в километрах").to_json()["levels"]["verbose"] == "5 миль — это 8,047 километра."
    assert skills.answer("Какой день недели будет 4 июля 2026 года?").to_json()["levels"]["verbose"] == (
        "Четвёртое июля две тысячи двадцать шестого года — суббота.")


def test_days_until(monkeypatch):
    from voice_labs.skills import dates

    class Today(dt.date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 15)

    monkeypatch.setattr(dates.dt, "date", Today)
    # Asked in the genitive, said back in it.
    assert skills.answer("Сколько дней до Нового года?").to_json()["levels"]["verbose"] == (
        "До Нового года осталось 78 дней.")
    assert skills.answer("Сколько недель осталось до Дня Победы?").to_json()["levels"]["verbose"] == (
        "До Дня Победы осталось 29 недель.")
    assert skills.answer("Сколько дней до 31 декабря?").to_json()["levels"]["verbose"] == (
        "До тридцать первого декабря осталось 77 дней.")
    # Orthodox Easter, and the feasts by it.
    assert skills.answer("Когда Пасха?").text == "Второе мая две тысячи двадцать седьмого года."
    assert skills.answer("Когда Масленица?").to_json()["levels"]["verbose"] == (
        "Масленица начинается восьмого марта две тысячи двадцать седьмого года.")
    assert skills.answer("Когда католическая Пасха?").text == "Двадцать восьмое марта две тысячи двадцать седьмого года."


def test_orthodox_easter():
    from voice_labs.languages.ru.skills.dates import orthodox_easter

    assert [orthodox_easter(y) for y in (2024, 2025, 2026, 2027)] == [
        dt.date(2024, 5, 5), dt.date(2025, 4, 20), dt.date(2026, 4, 12), dt.date(2027, 5, 2)]


@pytest.mark.parametrize(
    "question",
    ["Какая погода завтра?", "Который час?", "Кто выиграл вчера матч?", "Сколько рублей в долларе?",
     "Во сколько закрывается магазин?", "Сколько стоит биткоин?"],
)
def test_live_questions(question):
    assert skills.answer(question) is None


def test_morning_and_weather_words_are_not_live():
    # "Утра" is a time of day; "как пишется погода" a spelling.
    assert skills.answer("Сколько ещё до семи утра?").text == "Ещё 14 часов и 40 минут."
    assert skills.answer("Как пишется слово погода?").text == "Погода пишется так: пэ, о, гэ, о, дэ, а."
    assert skills.answer("Какой последний месяц года?").text == "Декабрь."


def test_settings_in_russian():
    for skill_id, skill in SKILLS.items():
        settings = skill.settings.load(None)
        assert skill.settings.store(settings.to_json()) is None, skill_id
    assert SKILLS["clock"].settings.load(None).hours == 24
    units = SKILLS["units"].settings.load(None)
    assert units.volumes == "metric" and units.to_json()["presets"]["metric"]["sizes"]["стакан"] == 250


def test_holidays_written_in_russian_or_english():
    from voice_labs.skills.dates import Settings

    stored = Settings.store({"holidays": [*(h.to_json() for h in Settings().holidays),
                                          {"name": "День рождения бабушки", "when": "12 марта", "also": []},
                                          {"name": "Годовщина", "when": "June 20", "also": []},
                                          {"name": "Отпуск", "when": "40 дней после Пасхи", "also": []}]})
    assert stored == {"holidays": {"День рождения бабушки": "12 марта", "Годовщина": "June 20",
                                   "Отпуск": "40 дней после Пасхи"}}
    settings = Settings.load({"holidays": {"День рождения бабушки": "12 марта", "Годовщина": "June 20"}})
    assert skills.answer("Когда день рождения бабушки?", settings={"dates": settings}).text == "Двенадцатое марта."
    assert skills.answer("Когда годовщина?", settings={"dates": settings}).text == "Двадцатое июня."
    with pytest.raises(ValueError, match="Like 25 декабря"):
        Settings.store({"holidays": [{"name": "Никогда", "when": "когда-нибудь", "also": []}]})


def test_each_skill_answers_its_examples():
    for skill_id, skill in SKILLS.items():
        for example in skill.examples:
            found = skills.answer(example)
            assert found is not None and found.skill == skill_id, example


def test_a_clock_time_said_as_russian_says_it():
    from voice_labs.languages.ru.reading import clock

    assert clock("семь тридцать") == clock("в 7:30") == clock("половина восьмого") == (7, 30, None)
    assert clock("без четверти восемь вечера") == (7, 45, "pm")
    assert clock("без десяти восемь") == (7, 50, None)
    assert clock("четверть восьмого") == clock("пятнадцать минут восьмого") == (7, 15, None)
    assert clock("девятнадцать ноль ноль") == (19, 0, None)
    assert clock("в час дня") == (1, 0, "pm")
    assert clock("в двенадцать ночи") == clock("полночь") == (0, 0, "am")
    assert clock("в полдень") == (12, 0, "pm")


def test_places_said_in_their_case():
    say = languages.speech()
    assert say.place([("area", "Кухня")]) == "на кухне"
    assert say.place([("area", "Гостиная")]) == "в гостиной"
    assert say.place([("area", "Гостиная")], to=True) == "в гостиную"
    assert say.place([("area", "Детская комната")]) == "в детской комнате"
    assert say.place([("floor", "Первый этаж")]) == "на первом этаже"
    assert say.place([("floor", "Третий этаж")], to=True) == "на третий этаж"
    assert say.place([("floor", "Наверху")]) == "наверху"
    assert say.place([("area", "Двор")]) == "во дворе"
    assert say.place([("area", "Вторая спальня")]) == "во второй спальне"
    assert say.place([("area", "Кухня-гостиная")]) == "на кухне-гостиной"
    assert say.place([("area", "Мамина спальня")]) == "в маминой спальне"
    assert say.place([("area", "Комната Саши")]) == "в комнате Саши"
    # A name of no room known is said as written.
    assert say.place([("area", "Living room")]) == "в комнате «Living room»"
    assert say.place([("area", "Кухня"), ("area", "Гостиная")]) == "на кухне и в гостиной"


def test_counts_and_times_said():
    say = languages.speech()
    assert say.duration(60) == "одна минута" and say.duration(60, adjective=True) == "одну минуту"
    assert say.duration(21 * 60) == "двадцать одна минута" and say.duration(22) == "двадцать две секунды"
    assert say.duration(5 * 60) == "5 минут" and say.duration(3600 + 300) == "1 час и 5 минут"
    assert say.duration(2 * 3600) == "2 часа"
    assert say.clock(7, 5) == "7:05" and say.clock(19, 30) == "19:30" and say.clock(0, 0) == "полночь"


def test_timers_said():
    say = languages.speech()
    pizza = {"name": "пицца", "created_duration": 600, "remaining": 412, "status": "active"}
    plain = {"name": None, "created_duration": 300, "remaining": 61, "status": "active"}
    tea = {"name": "чай", "created_duration": 240, "remaining": 90, "status": "paused"}
    assert say.timer_started(pizza, (19, 45)) == {
        "brief": "Таймер запущен.", "specific": "Таймер «пицца» запущен.",
        "verbose": "Таймер «пицца» запущен, сработает в 19:45."}
    assert say.timer_status([pizza, plain, tea]) == (
        "У таймера «пицца» осталось 6 минут и пятьдесят две секунды. "
        "У таймера на 5 минут осталась одна минута и одна секунда. "
        "Таймер «чай» на паузе, осталась одна минута и 30 секунд.")
    assert say.timer_not_found("лазанья", [pizza, tea]) == (
        "Таймера «лазанья» нет. Есть таймер «пицца» и таймер «чай».")
    assert say.timer_changed("add", pizza, 120, 532)["specific"] == "Таймер «пицца» продлён на две минуты."
    assert say.timers_cancelled([pizza, plain, tea])["specific"] == "Отменены 3 таймера."
    assert say.alarm_set((7, 30), 3600 + 60)["verbose"] == "Будильник поставлен на 7:30, через 1 час и одну минуту."
    assert say.timers_ringing([{"name": "alarm"}]) == "Будильник сработал."
