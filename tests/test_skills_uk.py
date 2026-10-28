"""The skills in Ukrainian: asked and answered as Ukrainian speakers would, in
any case ("від вісімдесяти", "у п'яти милях", "до Різдва"), with Ukrainian
units and holidays, and numbers written as the voice should read them."""

from __future__ import annotations

import datetime as dt

import pytest

from voice_call import languages, skills
from voice_call.skills import SKILLS


@pytest.fixture(autouse=True)
def ukrainian():
    languages.use("uk")
    yield
    languages.use(languages.DEFAULT)


@pytest.mark.parametrize(
    ("question", "said", "skill"),
    [
        ("Як пишеться необхідний?", "Необхідний пишеться Н, Е, О, Бе, Х, І, Д, Н, И, Йот.", "spelling"),
        # The apostrophe as typed ("’") is the model's ("'"), and said.
        ("Продиктуй по буквах слово м’ята", "М'ята пишеться М, апостроф, Я, Т, А.", "spelling"),
        ("Скільки буде дванадцять помножити на вісім?", "Це 96.", "arithmetic"),
        ("Скільки буде триста сорок п'ять плюс дванадцять?", "Це 357.", "arithmetic"),
        ("Скільки буде 15 відсотків від 80 гривень?", "Це 12 гривень.", "arithmetic"),
        ("Скільки буде 100 поділити на 7?", "Це чотирнадцять кома двадцять дев'ять.", "arithmetic"),
        # Numbers in the genitive after "з".
        ("Квадратний корінь зі ста сорока чотирьох", "Це 12.", "arithmetic"),
        ("Середнє з чотирьох, восьми і дванадцяти", "Це 8.", "arithmetic"),
        ("Скільки буде 21 гривня плюс 1 гривня?", "Це двадцять дві гривні.", "arithmetic"),
        ("Два в десятому степені", "Це тисяча двадцять чотири.", "arithmetic"),
        ("Котра година буде через 45 хвилин?", "Буде сімнадцята нуль п'ять.", "clock"),
        ("Скільки ще до сімнадцятої?", "Ще сорок хвилин.", "clock"),
        ("Котра година буде через півтори години?", "Буде сімнадцята п'ятдесят.", "clock"),
        ("Чи 2024 високосний рік?", "Так.", "dates"),
        ("Який день тижня 4 липня 2026 року?", "Це субота.", "dates"),
        ("Коли Різдво?", "Двадцять п'ятого грудня.", "dates"),
        ("Коли День Незалежності?", "Двадцять четвертого серпня.", "dates"),
        ("Скільки днів у лютому 2024 року?", "Це 29 днів.", "dates"),
        ("Який місяць іде після березня?", "Це квітень.", "dates"),
        ("Скільки грамів у фунті?", "Це чотириста п'ятдесят три кома шість грама.", "units"),
        ("Скільки кілометрів у п'яти милях?", "Це вісім кома нуль сорок сім кілометра.", "units"),
        ("100 градусів за Фаренгейтом у Цельсіях", "Це тридцять сім кома вісім градуса Цельсія.", "units"),
        ("Скільки склянок у літрі?", "Це 4 склянки.", "units"),
        ("Скільки мілілітрів в американській склянці?", "Це двісті тридцять шість кома шість мілілітра.", "units"),
        # An ounce beside a volume is a fluid ounce.
        ("Скільки унцій у склянці?", "Це вісім кома чотириста п'ятдесят чотири рідкої унції.", "units"),
        ("Скільки секунд у році?", "Це тридцять один мільйон п'ятсот тридцять шість тисяч секунд.", "units"),
        ("Скільки чайних ложок у столовій ложці?", "Це 3 чайні ложки.", "units"),
        ("Скільки буде 3 фунти в кілограмах?", "Це один кома триста шістдесят один кілограма.", "units"),
    ],
)
def test_ukrainian_answers(question, said, skill):
    found = skills.answer(question)
    assert (found.text, found.skill) == (said, skill)


def test_said_back_in_full():
    levels = skills.answer("Скільки склянок у літрі?").to_json()["levels"]
    assert levels == {"brief": "4 склянки.", "specific": "Це 4 склянки.", "verbose": "У літрі 4 склянки."}
    found = skills.answer("Скільки буде дванадцять помножити на вісім?")
    assert found.reply.answer == "96" and found.to_json()["levels"]["verbose"] == "Дванадцять помножити на вісім буде 96."
    assert skills.answer("Чи високосний 1900 рік?").to_json()["levels"]["verbose"] == (
        "Ні, тисяча дев'ятисотий рік не високосний.")
    assert skills.answer("Переведи 5 миль у кілометри").to_json()["levels"]["verbose"] == (
        "5 миль — це вісім кома нуль сорок сім кілометра.")
    # The answer keeps its digits; what is said has them in words.
    found = skills.answer("Скільки секунд у році?")
    assert found.reply.answer == "31 536 000 секунд"
    # A time in digits is said back in words, in its case.
    levels = skills.answer("Скільки залишилося до 23:45?").to_json()["levels"]
    assert levels["brief"] == "Сім годин і двадцять п'ять хвилин."
    assert levels["verbose"] == "До двадцять третьої сорок п'ять ще сім годин і двадцять п'ять хвилин."


def test_days_until(monkeypatch):
    from voice_call.skills import dates

    class Today(dt.date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 15)

    monkeypatch.setattr(dates.dt, "date", Today)
    # The holiday as it was asked for, in the genitive.
    assert skills.answer("Скільки днів до Різдва?").to_json()["levels"]["verbose"] == "До Різдва ще 71 день."
    assert skills.answer("Скільки тижнів до Дня матері?").to_json()["levels"]["verbose"] == (
        "До Дня матері ще 29 тижнів.")
    assert skills.answer("Скільки днів залишилось до Нового року?").text == "Ще 78 днів."
    # Orthodox Easter, and Trinity seven weeks after it.
    assert skills.answer("Коли Великдень?").text == "Другого травня дві тисячі двадцять сьомого року."
    assert skills.answer("Коли Трійця?").to_json()["levels"]["verbose"] == (
        "Трійця — двадцятого червня дві тисячі двадцять сьомого року.")
    assert skills.answer("На який день тижня припадає Різдво?").to_json()["levels"]["verbose"] == "Різдво — у п'ятницю."


def test_orthodox_easter():
    from voice_call.languages.uk.skills.dates import easter

    assert [easter(y) for y in (2024, 2025, 2026, 2027)] == [
        dt.date(2024, 5, 5), dt.date(2025, 4, 20), dt.date(2026, 4, 12), dt.date(2027, 5, 2)]


@pytest.mark.parametrize(
    "question",
    ["Яка погода завтра?", "Котра година?", "Хто виграв учора матч?", "Скільки гривень коштує долар?",
     "Коли зачиняється аптека?", "Який курс євро?"],
)
def test_live_questions(question):
    assert skills.answer(question) is None


def test_morning_and_last_are_not_live():
    # "Ранку" is a time of day; "останній" a date, not the news.
    assert skills.answer("Скільки ще до сьомої ранку?").text == "Ще чотирнадцять годин і сорок хвилин."
    assert skills.answer("Який останній місяць року?").text == "Це грудень."


def test_settings_in_ukrainian():
    for skill_id, skill in SKILLS.items():
        settings = skill.settings.load(None)
        assert skill.settings.store(settings.to_json()) is None, skill_id
    assert SKILLS["clock"].settings.load(None).hours == 24
    units = SKILLS["units"].settings.load(None)
    assert units.volumes == "metric" and units.to_json()["presets"]["metric"]["sizes"]["склянка"] == 250


def test_holidays_written_in_ukrainian_or_english():
    from voice_call.skills.dates import Settings

    stored = Settings.store({"holidays": [*(h.to_json() for h in Settings().holidays),
                                          {"name": "День народження бабусі", "when": "12 березня", "also": []},
                                          {"name": "Річниця", "when": "June 20", "also": []},
                                          {"name": "Вихідний", "when": "40 днів після Великодня", "also": []}]})
    assert stored == {"holidays": {"День народження бабусі": "12 березня", "Річниця": "June 20",
                                   "Вихідний": "40 днів після Великодня"}}
    settings = Settings.load({"holidays": {"День народження бабусі": "12 березня", "Річниця": "June 20"}})
    # Asked for in the genitive: "Дня народження бабусі".
    assert skills.answer("Коли День народження бабусі?", settings={"dates": settings}).text == "Дванадцятого березня."
    assert skills.answer("Коли річниця?", settings={"dates": settings}).text == "Двадцятого червня."
    with pytest.raises(ValueError, match="Like 25 грудня"):
        Settings.store({"holidays": [{"name": "Ніколи", "when": "колись", "also": []}]})


def test_each_skill_answers_its_examples():
    for skill_id, skill in SKILLS.items():
        for example in skill.examples:
            found = skills.answer(example)
            assert found is not None and found.skill == skill_id, example


def test_a_clock_time_said_as_ukrainian_says_it():
    from voice_call.languages.uk.reading import clock

    assert clock("сьома тридцять") == clock("о сьомій тридцять") == clock("7:30") == (7, 30, None)
    assert clock("пів на восьму") == clock("напів на восьму") == (7, 30, None)
    assert clock("за чверть восьма") == clock("без п'ятнадцяти вісім") == (7, 45, None)
    assert clock("десять хвилин на восьму") == (7, 10, None)
    assert clock("о шостій вечора") == (6, 0, "pm")
    assert clock("о дев'ятнадцятій нуль п'ять") == (19, 5, None)
    assert clock("опівночі") == clock("дванадцята ночі") == (0, 0, "am")
    assert clock("полудень") == (12, 0, "pm")
    assert clock("тридцять") is None


def test_places_said_in_their_case():
    say = languages.speech()
    assert say.place([("area", "Кухня")]) == "на кухні" and say.place([("area", "Кухня")], to=True) == "на кухню"
    assert say.place([("area", "Вітальня")]) == "у вітальні" and say.place([("area", "Офіс")]) == "в офісі"
    assert say.place([("area", "Ванна")]) == "у ванній" and say.place([("area", "Ванна кімната")]) == "у ванній кімнаті"
    assert say.place([("floor", "Перший поверх")]) == "на першому поверсі"
    assert say.place([("floor", "Третій поверх")], to=True) == "на третій поверх"
    assert say.place([("floor", "2 поверх")]) == "на другому поверсі"
    assert say.place([("area", "Літня кухня"), ("floor", "Нагорі")]) == "на літній кухні і нагорі"
    assert say.place([("area", "Кімната Олі")]) == "у кімнаті Олі"
    # A name of no known room isn't declined.
    assert say.place([("area", "Office")]) == "у кімнаті Office"


def test_numbers_said_in_words():
    say = languages.speech()
    assert say.duration(21 * 60) == "двадцять одна хвилина"
    assert say.duration(3600 + 300) == "одна година і п'ять хвилин"
    assert say.duration(120, adjective=True) == "дві хвилини" and say.duration(60, adjective=True) == "одну хвилину"
    assert say.clock(7, 30) == "сьома тридцять" and say.clock(19, 5) == "дев'ятнадцята нуль п'ять"
    assert say.clock(0, 0) == "північ"
    timer = {"name": None, "created_duration": 300, "remaining": 61, "status": "active"}
    assert say.timer_started({"name": "піца"}, (19, 45))["verbose"] == (
        "Таймер піца запущено, спрацює о дев'ятнадцятій сорок п'ять.")
    assert say.timer_status([timer]) == "На таймері на п'ять хвилин ще одна хвилина і одна секунда."
    assert say.alarm_set((7, 30), 3600)["specific"] == "Будильник поставлено на сьому тридцять."
    assert say.control("volume_set", 0.22, None, [])["specific"] == "Гучність 22 відсотки."
