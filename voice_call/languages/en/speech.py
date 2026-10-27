"""What the app says in English: every sentence it makes itself.

Each language's pack has a speech module with these names (the tests hold
every pack to English's). A sentence's parts come in as they are -- a timer,
seconds, a player's name -- for the language to put in its own order, case
and number. What a command says is its own (its response, the library's), not
this.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------- anywhere

SORRY = "Sorry, I didn't understand that."
"""Nothing understood what was said."""

FAILED = "Sorry, that didn't work."
"""A command's action failed."""

NO_SPEAKER = "Sorry, I don't know who's speaking."
"""A command for the speaker's own thing ("my to-do list") when nobody
knows who is speaking."""


def not_on_list(items: list[str]) -> str:
    """A to-do list command's items that aren't on the list (``on_list``)."""
    verb = "isn't" if len(items) == 1 else "aren't"
    return f"{join(items)} {verb} on the list."


NOT_SET = "That isn't set up yet."
"""A command on the home's own thing (``home``: its weather, thermostat,
shopping list or to-do list) when neither it nor the home has one set."""

WENT_WRONG = "Sorry, something went wrong."
"""The app failed while answering."""

NO_COMMANDS = "No voice commands are set up yet. Add some in the Voice Call app."

TIMERS_NEED_INTEGRATION = "Timers need the Voice Call integration in Home Assistant."

NO_AREA = "I don't know which area I'm in. Give this satellite an area."
"""A command for the satellite's area, on a satellite in none."""


def nothing_there(place: str) -> str:
    """An intent found nothing of the kind ``place`` ("in the Kitchen"; "" for a device by name)."""
    return f"There's nothing like that {place}." if place else "I couldn't find that."


def not_exposed(place: str) -> str:
    """What it found there isn't exposed to Assist."""
    return f"Nothing like that {place} is exposed to Assist." if place else "That isn't exposed to Assist."


def unavailable(place: str) -> str:
    """What it found there is all unavailable."""
    return f"Everything like that {place} is unavailable right now." if place else "That's unavailable right now."


# What a device can't do of what a command needs (its ``supports``), by
# capability: brightness, color, color_temp, position, tilt, temperature,
# fan_mode, hvac_mode; None for what Home Assistant refused without saying.
_CAN = {
    "brightness": "change brightness",
    "color": "change color",
    "color_temp": "change color temperature",
    "position": "be set to a position",
    "tilt": "be tilted",
    "temperature": "be set to a temperature",
    "fan_mode": "set a fan mode",
    "hvac_mode": "change mode",
    None: "do that",
}


def cannot(names: list[str], capability: str | None, value: str | None = None) -> str:
    """Devices that can't: "Garage Door can't be set to a position."; that
    have no ``value`` (a mode): "Ecobee can't be set to dry."."""
    if value is not None:
        what = f"set its fan to {value}" if capability == "fan_mode" else f"be set to {value}"
    else:
        what = _CAN.get(capability, _CAN[None])
    return f"{join(names)} can't {what}."


def none_can(place: str, capability: str | None) -> str:
    """Nothing of the kind ``place`` can: "Nothing in the Kitchen can change color."."""
    what = _CAN.get(capability, _CAN[None])
    return f"Nothing {place} can {what}." if place else f"Nothing can {what}."


EXAMPLE_NAMES = {
    "room": "Kitchen",
    "player": "Kitchen Speaker",
    "other_player": "Living Room Speaker",
    "timers": ["pizza", "tea", "lasagna"],
}
"""What the UI's example sentences name: a room, two music players, three
timers (two running, one asked for and not)."""


def join(parts: list[str]) -> str:
    """"a", "a and b", "a, b and c"."""
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


# ------------------------------------------------------------------ places


def place(places: list[tuple[str, str]], to: bool = False) -> str:
    """Where a command acts, from its ``("area" | "floor", name)`` places:
    "in the Kitchen", "upstairs", "on the First Floor"; ``to``, where
    something goes: "to the Kitchen"."""
    said = []
    for kind, name in places:
        text = _on_floor(name) if kind == "floor" else _in_area(name)
        if to:
            for word in ("in ", "on "):
                if text.startswith(word):
                    text = "to " + text[len(word) :]
                    break
        said.append(text)
    return " and ".join(said)


def _in_area(name: str) -> str:
    """"in the Kitchen", "in Sam's Room", "outside"."""
    lower = name.lower()
    if lower in ("upstairs", "downstairs", "outside", "outdoors", "inside", "indoors"):
        return lower
    if lower.startswith("the ") or "'s" in lower:
        return f"in {name}"
    return f"in the {name}"


def _on_floor(name: str) -> str:
    """"upstairs", "on the First Floor", "in the Basement"."""
    lower = name.lower()
    if lower in ("upstairs", "downstairs"):
        return lower
    if "floor" in lower or "level" in lower:
        return f"on the {name}" if not lower.startswith("the ") else f"on {name}"
    return _in_area(name)


# ------------------------------------------------------------------- hints
# What a command looked meant for, said when it was not run (the Unknown
# command's ``hint``): hint(description, why), why being one of these.


def hint(description: str, why: str) -> str:
    """"It looks like you're trying to turn on the lights, but …"; the
    description is the command's own."""
    return f"It looks like you're trying to {description}, {why}."


NOT_HERE = "but that isn't set up here"
"""Why: the command doesn't run on this satellite."""

PARTLY = "but I didn't understand all of it"
"""Why: only some of it was understood."""


def no_room(device: str) -> str:
    """Why: a room was said with a device that is in none."""
    return f"but I don't know which room {device} is in"


def not_in(device: str, room: str) -> str:
    """Why: the device said is not in the room said."""
    return f"but there's no {device} {_in_area(room)}"


_WHICH = {"entity": "which device", "area": "which room", "floor": "which floor", "scene": "which scene", "script": "which script"}


def missing(first: dict[str, Any], duration: bool = False, number_slot: str = "number") -> str:
    """Why: what the closest command was missing (the matcher's first
    ``missing``). ``duration``: the command takes a length; ``number_slot``,
    the number it takes."""
    kind = first.get("kind")
    if kind == "require":
        if first.get("slot"):
            return f"but I didn't hear which {first['slot'].replace('_', ' ')}"
        return f"but I didn't hear \"{first['phrases'][0]}\""
    if kind == "name":
        return f"but I didn't hear {_WHICH.get(first['type'], 'which one')}"
    if kind == "number":
        if first.get("heard"):
            return f"but {first['heard'][0]} is out of range"
        if duration:
            return "but I didn't hear how long"
        what = number_slot.replace("_", " ")
        return "but I didn't hear a number" if what == "number" else f"but I didn't hear the {what}"
    if kind == "text":
        return f"but I didn't hear the {first['slot'].replace('_', ' ')}"
    if kind == "unexplained":
        words = " ".join(first.get("words") or [])
        return f"but I didn't understand \"{words}\"" if words else PARTLY
    if kind == "unknown_name":
        return f"but I don't know \"{' '.join(first.get('words') or [])}\""
    if kind == "place":
        # A room, floor or device it can't be pointed at: "play music in the office".
        what = {"floor": "floor", "entity": "device"}.get(str(first.get("type")), "room")
        return f"but I can't pick a {what} for it"
    return "but I wasn't sure"


# ------------------------------------------------------------------ timers
# A timer is as voice_timer_list reports it: ``name`` (or None),
# ``created_duration`` and ``remaining`` in seconds. What is done is said by
# level of verbosity: {"brief", "specific", "verbose"}.


def duration(seconds: float, adjective: bool = False) -> str:
    """"1 hour and 5 minutes"; as an adjective, "1 hour 5 minute"."""
    total = round(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    parts = []
    for value, unit in ((hours, "hour"), (minutes, "minute"), (secs, "second")):
        if value or (unit == "second" and not parts):
            plural = "" if adjective or value == 1 else "s"
            parts.append(f"{value} {unit}{plural}")
    if hours:
        parts = parts[:2]  # an hour and some: seconds don't matter
    if adjective:
        return " ".join(parts)
    return join(parts)


def clock(hour: int, minute: int) -> str:
    """A time of day: "7:30 AM", "noon"."""
    if (hour, minute) == (12, 0):
        return "noon"
    if (hour, minute) == (0, 0):
        return "midnight"
    suffix = "AM" if hour < 12 else "PM"
    return f"{hour % 12 or 12}:{minute:02d} {suffix}"


def _timer(timer: dict[str, Any], capital: bool = False) -> str:
    """"the pizza timer", or "the 5 minute timer"."""
    name = timer.get("name")
    if name:
        text = f"the {name} timer"
    else:
        text = f"the {duration(float(timer.get('created_duration') or 0), adjective=True)} timer"
    return _capital(text) if capital else text


def _left(timer: dict[str, Any]) -> str:
    return duration(float(timer.get("remaining") or 0))


TIMER_HOW_LONG = "How long should the timer be?"
"""Starting a timer, no length heard."""

TIMER_HOW_MUCH = "How much time?"
"""Adding or taking off time, no length heard."""

ALARM_WHEN = "What time should the alarm be for?"
"""Setting an alarm, no time heard."""

NO_TIMERS = "There are no timers."


def timer_started(timer: dict[str, Any], ends: tuple[int, int]) -> dict[str, str]:
    """A timer started; it ends at ``ends`` (hour, minute)."""
    specific = f"{_timer(timer, capital=True)} started."
    return {"brief": "Timer started.", "specific": specific, "verbose": f"{specific[:-1]}, finishing at {clock(*ends)}."}


def alarm_set(at: tuple[int, int], seconds: float) -> dict[str, str]:
    """An alarm set for ``at`` (hour, minute), ``seconds`` from now."""
    specific = f"Alarm set for {clock(*at)}."
    return {"brief": "Alarm set.", "specific": specific, "verbose": f"{specific[:-1]}, {duration(seconds)} from now."}


def timers_cancelled(timers: list[dict[str, Any]]) -> dict[str, str]:
    """Every timer cancelled."""
    count = len(timers)
    specific = "Cancelled the timer." if count == 1 else f"Cancelled {count} timers."
    return {
        "brief": "Cancelled the timers." if count > 1 else "Cancelled the timer.",
        "specific": specific,
        "verbose": f"Cancelled {join([_timer(t) for t in timers])}.",
    }


def timer_not_found(asked: str | float | None, running: list[dict[str, Any]]) -> str:
    """No running timer is the one asked for: by name, or (seconds) by its
    length; None, asked for none."""
    listed = join([_timer(t) for t in running])
    if asked is None:
        return f"There's {listed}."
    if isinstance(asked, (int, float)):
        name = f"{duration(asked, adjective=True)} timer"
    else:
        name = asked if asked.endswith("timer") else f"{asked} timer"
    return f"There's no {name}. There's {listed}."


def which_timer(timers: list[dict[str, Any]]) -> str:
    """Several timers fit."""
    return f"Which timer? There's {join([_timer(t) for t in timers])}."


def timer_status(timers: list[dict[str, Any]]) -> str:
    """How long is left on each."""
    said = join([
        f"{_timer(t)} is paused with {_left(t)} left" if t.get("status") == "paused" else f"{_left(t)} left on {_timer(t)}"
        for t in timers
    ])
    return _capital(said) + "."


def timer_already(operation: str, timers: list[dict[str, Any]]) -> str:
    """Pausing paused timers, or resuming running ones."""
    state = "paused" if operation == "pause" else "running"
    if len(timers) == 1:
        return f"{_timer(timers[0], capital=True)} is already {state}."
    return f"All the timers are already {state}."


def timer_changed(operation: str, timer: dict[str, Any], seconds: float, left: float) -> dict[str, str]:
    """Time added to a timer (``add``) or taken off (``subtract``); ``left``
    on it now."""
    verb = "Added" if operation == "add" else "Took"
    prep = "to" if operation == "add" else "off"
    specific = f"{verb} {duration(seconds)} {prep} {_timer(timer)}."
    return {
        "brief": "Added the time." if operation == "add" else "Took the time off.",
        "specific": specific,
        "verbose": f"{specific[:-1]}; {duration(left)} left.",
    }


def timer_done(operation: str, timer: dict[str, Any]) -> dict[str, str]:
    """A timer cancelled, paused or resumed."""
    done = {"cancel": "cancelled", "pause": "paused", "resume": "resumed"}[operation]
    specific = f"{_timer(timer, capital=True)} {done}."
    left = _left(timer)
    verbose = f"{specific[:-1]}, with {left} left." if operation != "resume" else f"{specific[:-1]}; {left} left."
    return {"brief": f"Timer {done}.", "specific": specific, "verbose": verbose}


def timers_ringing(timers: list[dict[str, Any]]) -> str:
    """Timers that are done, as they ring."""
    return " ".join(f"{_timer(t, capital=True)} is done." for t in timers)


# ------------------------------------------------------------------- music
# Players come in by name; what is playing as media() says it.

NO_PLAYER = "There's no music player here."
NOTHING_PLAYING = "Nothing is playing."
MUSIC_SAID = {
    "no_player": NO_PLAYER,
    "nothing_playing": NOTHING_PLAYING,
    "missing_query": "What should I play?",
    "missing_volume": "What volume?",
    "already": "It's already playing.",
}
"""What a music command says when it can't be done, by outcome."""

MOVE_SAID = {
    "nothing_playing": "Nothing is playing to move.",
    "no_player": "There's no music player there.",
    "already": "The music is already playing there.",
}
"""Moving the music: what it says when it can't be done (where it goes is
the command's)."""

NOT_KNOWN_PLAYING = "I don't know what's playing."


def media(title: str, artist: str | None) -> str:
    """What is playing: "So What by Miles Davis"."""
    return f"{title} by {artist}" if artist else title


def item(name: str, media_type: str, artist: str | None) -> str:
    """A search result: "the Jazz playlist", "Kind of Blue by Miles Davis";
    a genre: "jazz music"."""
    if media_type == "genre":
        return name if name.lower().endswith("music") else f"{name} music"
    if media_type == "playlist":
        return name if "playlist" in name.lower() else f"the {name} playlist"
    if artist and media_type in ("track", "album"):
        return f"{name} by {artist}"
    return name


def not_found(query: str) -> str:
    """Nothing found for what was asked for, as heard."""
    return f"I couldn't find {query}."


def now_playing(what: str, paused: bool) -> str:
    """What's playing (``media``)."""
    return f"{_capital(what)} is paused." if paused else f"This is {what}."


_CONTROL_SAID = {
    "pause": "Paused.",
    "resume": "Resumed.",
    "stop": "Stopped.",
    "next": "Next track.",
    "previous": "Previous track.",
    "volume_up": "Volume up.",
    "volume_down": "Volume down.",
    "volume_set": "Volume set.",
    "mute": "Muted.",
    "unmute": "Unmuted.",
    "shuffle_on": "Shuffle on.",
    "shuffle_off": "Shuffle off.",
    "repeat_all": "Repeating everything.",
    "repeat_one": "Repeating this track.",
    "repeat_off": "Repeat off.",
}
_FOUND_SAID = {"play": "Playing music.", "add": "Added it to the queue.", "radio": "Playing similar music."}


def control(operation: str, volume: float | None, what: str | None, players: list[str]) -> dict[str, str]:
    """A control that worked (brief is fixed, for the text-to-speech
    cache): ``volume`` the level it set (0..1), ``what`` was playing."""
    brief = _CONTROL_SAID.get(operation, "Done.")
    specific = brief
    if volume is not None:
        specific = f"Volume at {round(volume * 100)}%."
    elif operation in ("pause", "resume", "stop") and what:
        specific = f"{brief[:-1]} {what}."
    return {"brief": brief, "specific": specific, "verbose": _on_players(specific, players)}


def found(operation: str, what: str, players: list[str]) -> dict[str, str]:
    """A search result played, queued (``add``) or played like (``radio``):
    ``what``, as item() says it."""
    specific = {"add": f"Added {what} to the queue.", "radio": f"Playing music like {what}."}.get(
        operation, f"Playing {what}."
    )
    return {"brief": _FOUND_SAID.get(operation, "Playing."), "specific": specific, "verbose": _on_players(specific, players)}


def moved(what: str | None, source: str, target: str, to: str) -> dict[str, str]:
    """The music moved from player ``source`` to ``target``: ``what`` was
    playing, ``to`` where, as heard (place(to=True)), if it was."""
    return {
        "brief": "Moved the music.",
        "specific": f"Moved the music {to}." if to else f"Moved the music to {target}.",
        "verbose": f"Moved {what or 'the music'} from {source} to {target}.",
    }


def _on_players(sentence: str, players: list[str]) -> str:
    """"Paused So What by Miles Davis on Kitchen Speaker."."""
    if not players:
        return sentence
    return f"{sentence[:-1]} on {join(players)}."
