from decimal import Decimal

import pytest

from adapt_ctc.numbers import on_grid, spoken_forms, unit_forms, unit_words, value_grid
from adapt_ctc.text import name_units, spoken_words


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("105", {"one hundred five", "one hundred and five", "a hundred five", "a hundred and five"}),
        ("72.5", {"seventy two point five", "seventy two and a half"}),
        ("0.5", {"zero point five", "half", "a half", "one half", "point five"}),
        ("1.5", {"one point five", "one and a half"}),
        ("-5", {"minus five", "negative five"}),
        ("21", {"twenty one"}),
        ("2700", {"two thousand seven hundred", "two thousand and seven hundred", "twenty seven hundred"}),
        ("1000", {"one thousand", "a thousand"}),
    ],
)
def test_spoken_forms(value, expected):
    assert set(spoken_forms(Decimal(value), "en")) == expected


def test_grid_with_halves():
    grid = value_grid(Decimal("40"), Decimal("41"), Decimal("0.5"))
    assert grid == [Decimal("40"), Decimal("40.5"), Decimal("41.0")]
    assert on_grid(Decimal("40.5"), Decimal(40), Decimal(90), Decimal("0.5"))
    assert not on_grid(Decimal("40.25"), Decimal(40), Decimal(90), Decimal("0.5"))
    assert not on_grid(Decimal("95"), Decimal(40), Decimal(90), Decimal("0.5"))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("EcoBee", [["eco", "bee"]]),
        ("Eco-Bee", [["eco", "bee"]]),
        ("Zone 2", [["zone", "two"]]),
        ("2nd Floor", [["second", "floor"]]),
        ("TV & Sound", [["tv", "and", "sound"]]),
        ("Kid’s Room", [["kid's", "room"], ["kids", "room"]]),
        ("  Living   Room ", [["living", "room"]]),
    ],
)
def test_spoken_words(text, expected):
    assert spoken_words(text, "en") == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("EcoBee", [["ecobee"]]),
        ("Eco-Bee", [["ecobee"]]),
        ("Eco Bee", [["eco", "bee"]]),
        ("Kitchen Ceiling Light", [["kitchen", "ceiling", "light"]]),
        ("Zone 2", [["zone", "two"]]),
        ("Kid's Room", [["kid's", "room"], ["kids", "room"]]),
    ],
)
def test_name_units(text, expected):
    assert name_units(text, "en") == expected


def test_unit_forms():
    forms = dict(unit_forms("hour", "en"))
    assert forms["an hour"] == 1
    assert forms["an hour and a half"] == Decimal("1.5")
    assert forms["one hour and a half"] == Decimal("1.5")
    assert forms["half an hour"] == Decimal("0.5")
    german = dict(unit_forms("hour", "de"))
    assert german["eine stunde"] == 1 and german["eineinhalb stunden"] == Decimal("1.5")
    assert german["eine halbe stunde"] == Decimal("0.5")
    assert unit_forms("hour", "it") == []
    assert dict(unit_forms("hour", "nl"))["een kwartier"] == Decimal("0.25")
    assert dict(unit_forms("hour", "es"))["una hora y media"] == Decimal("1.5")
    assert unit_words("minute", "en") == ("minute", "minutes") and unit_words("minute", "de-AT") == ("minute", "minuten")
