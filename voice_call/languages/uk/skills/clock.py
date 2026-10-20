"""How Ukrainian asks clock arithmetic ("котра година буде через 45 хвилин після
п'ятнадцятої", "котра година була за 90 хвилин до полудня") and says a time:
on a 24-hour clock, its hour an ordinal, "шістнадцята сорок п'ять", as the
app says one (voice_call/skills/clock.py)."""

from __future__ import annotations

import re

from voice_call.skills.clock import Settings
from voice_call.skills.common import reply

from .. import reading
from ..numerals import ordinal
from ..speech import _minutes as minute_words
from ..speech import clock as said_clock
from .numbers import N, clean, parse_num, said

EXAMPLES = ("котра година буде через 45 хвилин після п'ятнадцятої", "котра година була за 90 хвилин до полудня")

HOURS = 24
"""The clock it answers in unless set: "шістнадцята сорок п'ять"."""


def parse_clock(s):
    """Minutes into the day of a time said ("15", "третьої дня", "пів на
    третю", "полудня"); None."""
    found = reading.clock(s)
    if found is None:
        return None
    hour, minute, meridiem = found
    if meridiem == "pm" and hour < 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    return hour * 60 + minute


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


_UNITS = r"(хвилин[уи]?|годин[уи]?|півгодини|секунд[уи]?)"


def solve(q, hours=24):
    """The time, what was asked ("через 45 хвилин після 15"), and whether it
    was asked of the past; None."""
    s = clean(q)
    m = re.fullmatch(
        rf"(?:(?:котра|яка) (?:зараз )?(?:година )?|о котрій (?:годині )?)(?:(буде|була|є) )?(?:година )?"
        rf"((через |за )?(?:{N} )?{_UNITS}( з половиною)? (після|до|перед) (.+))",
        s,
    )
    if not m:
        return None
    tense, asked, shift, number, unit, half, rel, at = m.groups()
    value = parse_num(number) if number else (0.5 if unit == "півгодини" else 1.0)
    t = parse_clock(at)
    if value is None or t is None or (shift == "через " and rel != "після") or (shift == "за " and rel == "після"):
        return None
    minutes = value * (60 if unit.startswith(("годин", "півгодини")) else 1 / 60 if unit.startswith("секунд") else 1)
    minutes += (30 if unit.startswith("годин") else 0.5) if half else 0
    if unit == "півгодини":
        minutes = 30
    found = show_clock(round(t + (minutes if rel == "після" else -minutes)), hours)
    return found, asked, tense == "була"


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


def answer(text: str, settings: Settings):
    found = solve(text, settings.hours)
    if not found:
        return None
    time, asked, past = found
    verb = "була" if past else "буде"
    return reply(time, text, f"{said(_hours_said(asked))} {verb} {time}", brief=f"{time[:1].upper()}{time[1:]}.",
                 specific=f"{verb.capitalize()} {time}.")
