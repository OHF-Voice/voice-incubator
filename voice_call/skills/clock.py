"""Clock arithmetic: "what time is 45 minutes after 3 pm", "what's ninety
minutes before noon". Not the time now: that is a command (what_time)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from . import common
from .common import N, NUM_RE, WORD_NUM, Skill, clean, options, parse_num, reply

NUM_RE_GROUPLESS = rf"(?:{NUM_RE})"


@dataclass(frozen=True)
class Settings(common.Settings):
    hours: int = 12
    """The clock it answers in: 12 ("4:45 PM") or 24 ("16:45")."""

    @classmethod
    def load(cls, stored: Any) -> Settings:
        hours = options(stored, ("hours",)).get("hours", 12)
        if hours not in (12, 24) or isinstance(hours, bool):
            raise ValueError("hours: 12 or 24")
        return cls(int(hours))

    def to_json(self) -> dict[str, Any]:
        return {"hours": self.hours}

    @classmethod
    def store(cls, edited: Any) -> Any:
        settings = cls.load(edited)
        return {"hours": settings.hours} if settings != cls() else None


def parse_clock(s):
    s = s.strip().lower().replace(".", "")
    if s in ("noon", "midday"):
        return 12 * 60
    if s == "midnight":
        return 0
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm|a m|p m)?", s)
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").replace(" ", "")
    if h > 23 or mi > 59 or (ap and h > 12):
        return None
    if ap == "pm" and h != 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    return h * 60 + mi


def _clock_digits(s):
    """"two pm" -> "2 pm", "seven thirty am" -> "7:30 am", for parse_clock."""
    words = s.strip().split()
    meridiem = [w for w in words if w.replace(".", "") in ("am", "pm")]
    numbers = [w for w in words if w not in meridiem]
    if not numbers or any(w not in WORD_NUM for w in numbers):
        return s
    hour = WORD_NUM[numbers[0]]
    minute = sum(WORD_NUM[w] for w in numbers[1:])
    return f"{hour}:{minute:02d} {' '.join(meridiem)}".strip()


def show_clock(mins, hours=12):
    mins %= 1440
    h, m = divmod(mins, 60)
    if hours == 24:
        return f"{h}:{m:02d}"
    return f"{(h % 12) or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"


def solve(q, hours=12):
    """The time, and what was asked ("45 minutes after 3 pm"); None."""
    s = clean(q)
    if m := re.fullmatch(rf"(?:what(?: is|'s)|what time is) ({NUM_RE_GROUPLESS} (?:minutes?|hours?) (?:before|after|from|past) .+)", s):
        inner = re.fullmatch(rf"{N} (minutes?|hours?) (before|after|from|past) (.+)", m.group(1))
        v, u, rel, t = parse_num(inner.group(1)), inner.group(2), inner.group(3), parse_clock(_clock_digits(inner.group(4)))
        if v is not None and t is not None:
            delta = v * (60 if u.startswith("hour") else 1)
            return show_clock(int(t + (-delta if rel == "before" else delta)), hours), m.group(1)
    return None


DEFAULT = Settings()


def answer(text: str, settings: Settings = DEFAULT):
    found = solve(text, settings.hours)
    return reply(found[0], text, f"{found[1]} is {found[0]}") if found else None


SKILL = Skill(
    id="clock",
    name="Clock arithmetic",
    description="A time some minutes or hours before or after another.",
    examples=("what time is 45 minutes after 3 pm", "what's ninety minutes before noon"),
    icon="mdi:clock-plus-outline",
    answer=answer,
    settings=Settings,
)
