"""How Ukrainian asks times from now ("котра година буде через 45 хвилин",
"скільки ще до сімнадцятої") and says them: on a 24-hour clock, its hour an
ordinal, "сімнадцята п'ять", as the app says one; "ще сорок хвилин"
(voice_call/skills/clock.py)."""

from __future__ import annotations

import re

from voice_call.skills.clock import Settings, later, until
from voice_call.skills.common import reply

from .. import reading
from ..numerals import ordinal
from ..speech import _minutes as minute_words
from ..speech import clock as said_clock
from ..speech import duration
from .numbers import N, clean, parse_num, said

EXAMPLES = ("котра година буде через 45 хвилин", "скільки ще до сімнадцятої")

HOURS = 24
"""The clock it answers in unless set: "шістнадцята сорок п'ять"."""

_AMOUNT = re.compile(
    rf"(?:(?P<n>{N}) )?(?P<unit>хвилин[уи]?|годин[уи]?|півгодини)(?P<half> з половиною)?"
    rf"(?: (?:і|й) (?P<more>{N}) хвилин[уи]?)?"
)
_ASK = r"(?:котра|яка) (?:зараз )?(?:година )?буде(?: година)?"
_IN = re.compile(rf"{_ASK} через (?P<a>.+)|через (?P<b>.+?),? {_ASK}")
_UNTIL = re.compile(
    r"скільки(?: (?:ще|залишилося|залишилось|лишилося|лишилось|часу|хвилин|годин|у мене|у нас))* до (?P<time>.+)"
)


def show_clock(mins, hours=24):
    """"шістнадцята сорок п'ять"; on 12 hours, "четверта сорок п'ять дня"."""
    h, m = divmod(mins % 1440, 60)
    if hours == 24:
        return said_clock(h, m)
    if (h, m) == (12, 0):
        return "полудень"
    if (h, m) == (0, 0):
        return said_clock(0, 0)
    part = "ночі" if h < 4 else "ранку" if h < 12 else "дня" if h < 17 else "вечора"
    return f"{said_clock((h % 12) or 12, m)} {part}"


def minutes_in(s):
    """"45 хвилин", "годину", "півтори години", "півгодини", "2 години і 10
    хвилин" as minutes; None."""
    m = _AMOUNT.fullmatch(s)
    if not m:
        return None
    if m.group("unit") == "півгодини":
        return 30
    value = parse_num(m.group("n")) if m.group("n") else 1.0  # "через годину"
    more = parse_num(m.group("more")) if m.group("more") else 0
    if value is None or more is None:
        return None
    value += 0.5 if m.group("half") else 0
    minutes = value * (60 if m.group("unit").startswith("годин") else 1) + more
    return minutes if minutes > 0 else None


def _hours_said(text: str) -> str:
    """A time written in digits after "після" or "до", as said: "після 23:45"
    -> "після двадцять третьої сорок п'ять" (the voice reads "23:45" as "23
    дві крапки 45")."""

    def said(m: re.Match[str]) -> str:
        hour, minute = int(m.group(2)), int(m.group(3) or 0)
        if hour > 24 or minute > 59:
            return m.group(0)
        words = ordinal(hour % 24, "f_gen") if hour % 24 else "нуля"
        return f"{m.group(1)} {words}" + (f" {minute_words(minute)}" if minute else "")

    return re.sub(r"\b(після|до) (\d{1,2})(?:[:.](\d{2}))?(?![\d,])", said, text)


def answer(text: str, settings: Settings, now):
    s = clean(text)
    if m := _IN.fullmatch(s):
        amount = m.group("a") or m.group("b")
        minutes = minutes_in(amount)
        if minutes is None:
            return None
        hour, minute = later(now, minutes)
        then = show_clock(hour * 60 + minute, settings.hours)
        return reply(then, text, f"{said(f'Через {amount}')} буде {then}", specific=f"Буде {then}.")
    if m := _UNTIL.fullmatch(s):
        found = reading.clock(m.group("time"))
        if found is None:
            return None
        wait = until(now, *found)
        if wait == 0:
            return reply("Зараз", text, "Це вже зараз", specific="Це вже зараз.")
        time = said(_hours_said(f"до {m.group('time')}"))
        spoken = duration(wait * 60)
        return reply(spoken, text, f"{time[:1].upper()}{time[1:]} ще {spoken}", specific=f"Ще {spoken}.")
    return None
