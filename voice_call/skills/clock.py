"""Clock arithmetic: "what time is 45 minutes after 3 pm", "what's ninety
minutes before noon". Not the time now: that is a command (what_time). How
it is asked and said is the app's language's (its pack's
``skills/clock.py``), as is the clock it answers in unless set."""

from __future__ import annotations

from dataclasses import dataclass, field
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


def answer(text: str, settings: Settings | None = None) -> Reply | None:
    return languages.grammar("clock").answer(text, settings or Settings())


SKILL = Skill(
    id="clock",
    name="Clock arithmetic",
    description="A time some minutes or hours before or after another.",
    icon="mdi:clock-plus-outline",
    answer=answer,
    settings=Settings,
)
