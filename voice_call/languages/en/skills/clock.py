"""How English asks times from now ("what time will it be in 45 minutes", "how
long until 5 pm") and says them ("It'll be 5:05 PM.", "40 minutes from now.";
voice_call/skills/clock.py)."""

from __future__ import annotations

import re

from voice_call.skills.clock import Settings, later, until
from voice_call.skills.common import reply

from .. import reading
from ..speech import clock as said_clock
from ..speech import duration
from .numbers import NUM_RE, clean, parse_num

EXAMPLES = ("what time will it be in 45 minutes", "how long until 5 pm")

HOURS = 12
"""The clock it answers in unless set: "4:45 PM"."""

_N = rf"(?:{NUM_RE})"
_AMOUNT = re.compile(
    rf"(?P<n>{_N}) ?(?P<unit>minutes?|hours?)(?: and (?P<half>a half|a quarter))?(?: and (?P<more>{_N}) minutes?)?"
)
_WORDS = {"half an hour": 30, "a half hour": 30, "half hour": 30, "a quarter of an hour": 15,
          "quarter of an hour": 15, "a quarter hour": 15, "three quarters of an hour": 45}
_IN = re.compile(
    r"(?:what time (?:will|would) it be in (?P<a>.+)"
    r"|what time (?:will|would) it be (?P<b>.+) from now"
    r"|what time is it (?:going to be )?in (?P<c>.+)"
    r"|what(?:'s| is) the time (?:going to be )?in (?P<d>.+)"
    r"|what time is (?P<e>.+) from now"
    r"|in (?P<f>.+?),? what time (?:will|would) it be)"
)
_UNTIL = re.compile(
    r"how (?:long|much time|much longer|many minutes|many hours) (?:is it |is there |do (?:i|we) have |have (?:i|we) got )?"
    r"(?:until|till|til|before|to go until) (?:it(?:'s| is) )?(?:(?:.+ )?at )?(?P<time>.+)"
)


def minutes_in(s):
    """"45 minutes", "an hour and a half", "2 hours and 10 minutes", "half an
    hour" as minutes; None."""
    s = s.strip()
    if s in _WORDS:
        return _WORDS[s]
    m = _AMOUNT.fullmatch(s)
    if not m:
        return None
    n = parse_num(m.group("n"))
    if n is None:
        return None
    n += {"a half": 0.5, "a quarter": 0.25}.get(m.group("half") or "", 0)
    minutes = n * (60 if m.group("unit").startswith("hour") else 1)
    if m.group("more"):
        more = parse_num(m.group("more"))
        if more is None:
            return None
        minutes += more
    return minutes if minutes > 0 else None


def show_clock(hour, minute, hours=12):
    """"5:05 PM", "noon"; on 24 hours, "17:05"."""
    return f"{hour}:{minute:02d}" if hours == 24 else said_clock(hour, minute)


def answer(text: str, settings: Settings, now):
    s = clean(text)
    if m := _IN.fullmatch(s):
        amount = next(g for g in m.groups() if g)
        minutes = minutes_in(amount)
        if minutes is None:
            return None
        then = show_clock(*later(now, minutes), settings.hours)
        return reply(then, text, f"In {amount} it'll be {then}", specific=f"It'll be {then}.")
    if m := _UNTIL.fullmatch(s):
        found = reading.clock(m.group("time"))
        if found is None:
            return None
        hour, minute, meridiem = found
        wait = until(now, hour, minute, meridiem)
        if wait == 0:
            return reply("Now", text, "It's that time now", specific="It's that time now.")
        then = show_clock(*later(now, wait), settings.hours)
        said = duration(wait * 60)
        return reply(said, text, f"{then} is {said} from now", specific=f"{said[:1].upper()}{said[1:]} from now.")
    return None
