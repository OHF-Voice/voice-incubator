"""An area's own settings, over the whole home's (``settings.json`` ``areas``).

The Home page has a place for the whole home and one for each area. An area
may have its own of some settings; a satellite in it uses those, and the whole
home's for the rest. A satellite in no area uses the whole home's::

    areas:
      bedroom: {climate: climate.upstairs, at_most: brief}
      nursery: {voice: en_US-amy-medium, sounds: false, at_most: beep}
      garage: {}            # listed on the Home page, with nothing of its own

What an area may have its own of (``KEYS``):

- ``climate``, ``shopping_list``, ``todo_list``: the home's own things that
  commands use without a target of their own (``home``; the weather is the
  whole home's).
- ``voice`` (and its ``speaker``): Piper's voice, which replies are said in.
- ``sounds`` (false: none) and ``instrument``: the call's sounds.
- ``at_most``: the most a command says, ``brief`` or ``beep`` (the whole
  home's may have one too); a question still answers.
- ``silence_seconds``: silence that ends a command (a satellite's own wins).

Commands and skills are placed elsewhere: a command's ``area`` and ``off``
(voice_labs/commands.py), a skill's ``off`` (voice_labs/skills/).
"""

from __future__ import annotations

from typing import Any

from adapt_ctc.config import ConfigError

from .commands import HOME_THINGS, VERBOSITY
from .endpoint import LIMITS

HOME = ("climate", "shopping_list", "todo_list")
"""The home's own things an area may have its own of."""
AT_MOST = ("brief", "beep")
"""The most a command may say, where it is capped."""
KEYS = (*HOME, "voice", "speaker", "sounds", "instrument", "at_most", "silence_seconds")


def own(settings: dict[str, Any], area: str | None) -> dict[str, Any]:
    """``area``'s own settings, as set (none for no area)."""
    entry = (settings.get("areas") or {}).get(area) if area else None
    return {k: v for k, v in entry.items() if k in KEYS} if isinstance(entry, dict) else {}


def listed(settings: dict[str, Any]) -> list[str]:
    """The areas added on the Home page, whether or not they have anything
    of their own."""
    return [str(a) for a in settings.get("areas") or {}]


def whole_sounds(settings: dict[str, Any]) -> bool:
    """The whole home's ``sounds``: on unless false (or, from before,
    ``thinking_sound`` false)."""
    return settings.get("sounds", settings.get("thinking_sound")) is not False


def effective(settings: dict[str, Any], area: str | None) -> dict[str, Any]:
    """What a satellite in ``area`` uses: the area's own, else the whole
    home's. ``voice``/``speaker`` and ``silence_seconds`` are only the
    area's own (None: the whole home's, which is decided elsewhere)."""
    mine = own(settings, area)
    home = {**(settings.get("home") or {}), **{k: mine[k] for k in HOME if mine.get(k)}}
    at_most = mine.get("at_most") or settings.get("at_most")
    return {
        "home": home,
        "voice": mine.get("voice"),
        "speaker": mine.get("speaker") if mine.get("voice") else None,
        "sounds": mine["sounds"] if isinstance(mine.get("sounds"), bool) else whole_sounds(settings),
        "instrument": mine.get("instrument") or settings.get("instrument"),
        "at_most": at_most if at_most in AT_MOST else None,
        "silence_seconds": mine.get("silence_seconds"),
    }


def capped(level: str, at_most: str | None) -> str:
    """``level`` (commands.VERBOSITY), no more than ``at_most``."""
    if at_most in AT_MOST and level in VERBOSITY and VERBOSITY.index(level) > VERBOSITY.index(at_most):
        return at_most
    return level


def at_most_value(value: Any, where: str = "at_most") -> str | None:
    """A ``brief``/``beep`` cap, or None for none."""
    if value in (None, ""):
        return None
    if value not in AT_MOST:
        raise ConfigError(f"{where}: one of {', '.join(AT_MOST)}, or none")
    return str(value)


def put(settings: dict[str, Any], area: str, values: dict[str, Any] | None, voices: dict[str, Any],
        instruments: list[str]) -> dict[str, Any]:
    """Set ``area``'s own (``values``: each key's value, None to use the
    whole home's), keeping it listed; ``values`` None takes it off the list.
    ``voices``: Piper's catalog (piper_voices.catalog), ``instruments``: the
    sounds' instruments. Returns the area's own, as saved. Raises ConfigError."""
    areas = settings.setdefault("areas", {})
    if values is None:
        areas.pop(area, None)
        if not areas:
            settings.pop("areas")
        return {}
    if not isinstance(values, dict):
        raise ConfigError("a mapping of settings")
    unknown = sorted(set(values) - set(KEYS))
    if unknown:
        raise ConfigError(f"{unknown[0]}: not something an area has its own of")
    entry = dict(areas.get(area) or {})
    for key, value in values.items():
        if value in (None, ""):
            entry.pop(key, None)
            if key == "voice":
                entry.pop("speaker", None)
            continue
        if key in HOME:
            domain = HOME_THINGS[key]
            if not isinstance(value, str) or not value.startswith(f"{domain}."):
                raise ConfigError(f"{key}: a {domain} entity")
        elif key == "voice":
            if str(value) not in voices:
                raise ConfigError(f"voice: no voice {value!r}")
            if entry.get("voice") != value:
                entry.pop("speaker", None)
        elif key == "speaker":
            voice = voices.get(str(values.get("voice") or entry.get("voice") or ""))
            if voice is None or str(value) not in (voice.speakers or []):
                raise ConfigError(f"speaker: {voice.key if voice else 'its voice'} has no speaker {value!r}")
        elif key == "sounds":
            if not isinstance(value, bool):
                raise ConfigError("sounds: true or false")
        elif key == "instrument":
            if value not in instruments:
                raise ConfigError(f"instrument: one of {', '.join(instruments)}")
        elif key == "at_most":
            value = at_most_value(value)
        elif key == "silence_seconds":
            low, high = LIMITS["silence_seconds"]
            try:
                value = round(float(value), 2)
            except (TypeError, ValueError) as err:
                raise ConfigError(f"silence_seconds: from {low:g} to {high:g}") from err
            if not low <= value <= high:
                raise ConfigError(f"silence_seconds: from {low:g} to {high:g}")
        entry[key] = value
    areas[area] = entry
    return entry
