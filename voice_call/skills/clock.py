"""Times counted from now: "what time will it be in 45 minutes" (the time
then), "how long until 5 pm" (until it next comes round). Not the time now:
that is a command (what_time). How it is asked and said is the app's
language's (its pack's ``skills/clock.py``, given now), as is the clock it
answers in unless set.

Now is the machine's local time -- in the app, the home's time zone, as the
dates skill's today is -- to the minute: what a clock on the wall shows."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field, replace
from typing import Any

from .. import languages
from . import common
from .common import Reply, Skill, options


def _hours() -> int:
    return languages.grammar("clock").HOURS


@dataclass(frozen=True)
class Settings(common.Settings):
    hours: int = field(default_factory=_hours)
    """The clock it answers in: 12 ("4:45 PM") or 24 ("16:45")."""

    @classmethod
    def load(cls, stored: Any) -> Settings:
        hours = options(stored, ("hours",)).get("hours", _hours())
        if hours not in (12, 24) or isinstance(hours, bool):
            raise ValueError("hours: 12 or 24")
        return cls(int(hours))

    def to_json(self) -> dict[str, Any]:
        return {"hours": self.hours}

    @classmethod
    def store(cls, edited: Any) -> Any:
        settings = cls.load(edited)
        return {"hours": settings.hours} if settings != cls() else None


def now() -> dt.datetime:
    """The time here, to the minute."""
    return dt.datetime.now().replace(second=0, microsecond=0)


def later(start: dt.datetime, minutes: float) -> tuple[int, int]:
    """The time of day (hour, minute) ``minutes`` after ``start``."""
    then = start + dt.timedelta(minutes=minutes)
    return then.hour, then.minute


def until(start: dt.datetime, hour: int, minute: int, meridiem: str | None) -> int:
    """Minutes from ``start`` until a time of day said next comes round:
    today's if still to come, else tomorrow's. Said without am or pm, "5"
    is whichever of 5:00 and 17:00 comes first; 0 when it is that time."""
    if meridiem == "pm":
        hours = [hour % 12 + 12]
    elif meridiem == "am":
        hours = [hour % 12]
    elif hour <= 12:
        hours = [hour % 12, hour % 12 + 12]
    else:
        hours = [hour]
    waits = []
    for h in hours:
        then = start.replace(hour=h % 24, minute=minute)
        if then < start:
            then += dt.timedelta(days=1)
        waits.append(round((then - start).total_seconds() / 60))
    return min(waits)


def answer(text: str, settings: Settings | None = None) -> Reply | None:
    found = languages.grammar("clock").answer(text, settings or Settings(), now())
    # Said alone, the time or the wait starts a sentence: "Le 17 e 5.".
    return found and replace(found, brief=found.brief[:1].upper() + found.brief[1:])


SKILL = Skill(
    id="clock",
    name="Time from now",
    description="The time some minutes or hours from now, and how long until a time.",
    icon="mdi:clock-plus-outline",
    answer=answer,
    settings=Settings,
)
