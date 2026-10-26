"""What the app says in Dutch: every sentence it makes itself, with English's
names (see voice_call/languages/en/speech.py for what each is).

A place needs its article: "in de keuken", "in het kantoor"; "naar de
keuken" where something goes; a floor is "op": "op de eerste verdieping".
Most rooms are de-words; the het-words are by a name's last part
("Tuinhuis" is a huis). Confirmations are short: "Pizzatimer gestart"."""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------- anywhere

SORRY = "Sorry, dat heb ik niet begrepen."
FAILED = "Sorry, dat is niet gelukt."
NO_SPEAKER = "Sorry, ik weet niet wie er spreekt."
NOT_ON_LIST = "{item} staat niet op de lijst."
WENT_WRONG = "Sorry, er ging iets mis."
NO_COMMANDS = "Er zijn nog geen spraakopdrachten ingesteld. Voeg ze toe in de Voice Call app."
TIMERS_NEED_INTEGRATION = "Voor timers is de Voice Call integratie in Home Assistant nodig."

NO_AREA = 'Ik weet niet in welke ruimte ik ben. Geef deze satelliet een ruimte.'
"""A command for the satellite's area, on a satellite in none."""


def nothing_there(place: str) -> str:
    """An intent found nothing of the kind ``place`` ("in the Kitchen"; "" for a device by name)."""
    return f"Ik heb {place} niets van dat soort gevonden." if place else "Dat heb ik niet gevonden."


def not_exposed(place: str) -> str:
    """What it found there isn't exposed to Assist."""
    return f"Niets daarvan {place} is zichtbaar voor Assist." if place else "Dat is niet zichtbaar voor Assist."


def unavailable(place: str) -> str:
    """What it found there is all unavailable."""
    return f"Alles daarvan {place} is nu niet beschikbaar." if place else "Dat is nu niet beschikbaar."


EXAMPLE_NAMES = {
    "room": "Keuken",
    "player": "Keukenspeaker",
    "other_player": "Woonkamerspeaker",
    "timers": ["pizza", "thee", "lasagne"],
}


def join(parts: list[str]) -> str:
    """"a", "a en b", "a, b en c"."""
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " en " + parts[-1]


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


# ------------------------------------------------------------------ places

_HET = (
    "kantoor", "toilet", "washok", "hok", "terras", "balkon", "atelier", "souterrain", "huis", "portaal",
    "appartement", "achterhuis", "voorhuis", "trappenhuis", "zwembad", "bad", "dak", "erf", "plein",
)
"""Rooms that are het-words, by a name's last part; the others are de-words."""

_BARE = {"boven": "naar boven", "beneden": "naar beneden", "buiten": "naar buiten", "binnen": "naar binnen"}
"""Places said as they are ("boven"), and where to ("naar boven")."""

_ADJECTIVES = {"groot": "grote", "klein": "kleine", "oud": "oude", "nieuw": "nieuwe", "eerst": "eerste"}
"""Adjectives of a room's name inflected after "de"/"het": "het kleine kantoor"."""


def place(places: list[tuple[str, str]], to: bool = False) -> str:
    """Where a command acts: "in de keuken", "in het kantoor", "op de eerste
    verdieping", "boven"; ``to``, where something goes: "naar de keuken",
    "naar boven"."""
    return " en ".join(_place(kind, name, to) for kind, name in places)


def _place(kind: str, name: str, to: bool) -> str:
    lower = name.lower()
    if lower in _BARE:
        return _BARE[lower] if to else lower
    words = name.split()
    if any(word.endswith(("s", "'s")) and word[:1].isupper() and len(words) > 1 for word in words[:-1]):
        return f"{'naar' if to else 'in'} {name}"  # "in Sams kamer"
    article = "het" if lower.endswith(_HET) or lower.endswith("je") else "de"
    said = " ".join([*(_ADJECTIVES.get(w.lower(), w.lower() if w.lower() in _ADJECTIVES.values() else w) for w in words[:-1]), words[-1]])
    preposition = "naar" if to else ("op" if kind == "floor" else "in")
    return f"{preposition} {article} {said}"


# ------------------------------------------------------------------- hints


def hint(description: str, why: str) -> str:
    """The description is the command's own, as what one wants to do: "het
    licht aandoen"."""
    return f"Je wilde zeker {description}, {why}."


NOT_HERE = "maar dat is hier niet ingesteld"
PARTLY = "maar ik heb niet alles verstaan"


def no_room(device: str) -> str:
    return f"maar ik weet niet in welke kamer {device} staat"


def not_in(device: str, room: str) -> str:
    return f"maar {device} staat niet {_place('area', room, False)}"


_WHICH = {"entity": "welk apparaat", "area": "welke kamer", "floor": "welke verdieping"}
_SLOTS = {
    "color": "welke kleur", "percentage": "hoeveel procent", "temperature": "welke temperatuur", "volume": "hoe hard",
    "item": "wat", "reminder": "waaraan", "query": "wat", "timer": "welke timer", "time": "hoe laat", "number": "welk getal",
}


def _not_heard(slot: str) -> str:
    which = _SLOTS.get(slot)
    return f"maar ik heb niet gehoord {which}" if which else "maar ik heb niet alles gehoord"


def missing(first: dict[str, Any], duration: bool = False, number_slot: str = "number") -> str:
    kind = first.get("kind")
    if kind == "require":
        if first.get("slot"):
            return _not_heard(first["slot"])
        return f"maar ik heb „{first['phrases'][0]}” niet gehoord"
    if kind == "name":
        return f"maar ik heb niet gehoord {_WHICH.get(first['type'], 'welke')}"
    if kind == "number":
        if first.get("heard"):
            return f"maar {first['heard'][0]} kan niet"
        if duration:
            return "maar ik heb niet gehoord hoe lang"
        return _not_heard(number_slot)
    if kind == "text":
        return _not_heard(first["slot"])
    if kind == "unexplained":
        words = " ".join(first.get("words") or [])
        return f"maar ik heb „{words}” niet verstaan" if words else PARTLY
    if kind == "unknown_name":
        return f"maar „{' '.join(first.get('words') or [])}” ken ik niet"
    if kind == "place":
        what = {"floor": "geen verdieping", "entity": "geen apparaat"}.get(str(first.get("type")), "geen kamer")
        return f"maar daar kan ik {what} voor kiezen"
    return "maar ik wist het niet zeker"


# ------------------------------------------------------------------ timers

_UNITS = (("uur", "uur"), ("minuut", "minuten"), ("seconde", "seconden"))


def _parts(seconds: float) -> list[tuple[int, int]]:
    total = round(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    parts = [(value, unit) for unit, value in enumerate((hours, minutes, secs)) if value] or [(0, 2)]
    return parts[:2] if hours else parts


def duration(seconds: float, adjective: bool = False) -> str:
    """"een uur en 5 minuten"; as an adjective, "5-minuten" (the
    "5-minutentimer")."""
    if adjective:
        return "-".join(f"{value}-{_UNITS[unit][1]}" for value, unit in _parts(seconds))
    return join([f"een {_UNITS[unit][0]}" if value == 1 else f"{value} {_UNITS[unit][1]}" for value, unit in _parts(seconds)])


def clock(hour: int, minute: int) -> str:
    """"7 uur 30", "19 uur", "middernacht"."""
    if (hour, minute) == (0, 0):
        return "middernacht"
    return f"{hour} uur {minute}" if minute else f"{hour} uur"


def _named(timer: dict[str, Any]) -> str:
    """Without its article: "pizzatimer", "timer pasta koken", "timer van 5
    minuten"."""
    name = str(timer.get("name") or "")
    if name and " " not in name:
        return f"{name.lower()}timer"
    if name:
        return f"timer {name}"
    return f"timer van {duration(float(timer.get('created_duration') or 0))}"


def _timer(timer: dict[str, Any]) -> str:
    return f"de {_named(timer)}"


def _left(timer: dict[str, Any]) -> str:
    return duration(float(timer.get("remaining") or 0))


def _running(timers: list[dict[str, Any]]) -> str:
    listed = join([_timer(t) for t in timers])
    return f"Er loopt {listed}." if len(timers) == 1 else f"Er lopen {listed}."


TIMER_HOW_LONG = "Hoe lang moet de timer lopen?"
TIMER_HOW_MUCH = "Hoeveel tijd?"
ALARM_WHEN = "Voor hoe laat moet ik de wekker zetten?"
NO_TIMERS = "Er loopt geen timer."


def timer_started(timer: dict[str, Any], ends: tuple[int, int]) -> dict[str, str]:
    specific = f"{_capital(_named(timer))} gestart."
    return {"brief": "Timer gestart.", "specific": specific, "verbose": f"{specific[:-1]}, hij loopt af om {clock(*ends)}."}


def alarm_set(at: tuple[int, int], seconds: float) -> dict[str, str]:
    specific = f"Wekker gezet voor {clock(*at)}."
    return {"brief": "Wekker gezet.", "specific": specific, "verbose": f"{specific[:-1]}, over {duration(seconds)}."}


def timers_cancelled(timers: list[dict[str, Any]]) -> dict[str, str]:
    count = len(timers)
    return {
        "brief": "Timer geannuleerd." if count == 1 else "Timers geannuleerd.",
        "specific": "Timer geannuleerd." if count == 1 else f"{count} timers geannuleerd.",
        "verbose": _capital(f"{join([_timer(t) for t in timers])} geannuleerd."),
    }


def timer_not_found(asked: str | float | None, running: list[dict[str, Any]]) -> str:
    there = _running(running)
    if asked is None:
        return there
    if isinstance(asked, (int, float)):
        name = f"timer van {duration(asked)}"
    else:
        name = asked if asked.lower().endswith("timer") else _named({"name": asked})
    return f"Er is geen {name}. {there}"


def which_timer(timers: list[dict[str, Any]]) -> str:
    return f"Welke timer? {_running(timers)}"


def timer_status(timers: list[dict[str, Any]]) -> str:
    return " ".join(
        _capital(f"{_timer(t)} staat op pauze, nog {_left(t)}.")
        if t.get("status") == "paused"
        else _capital(f"{_timer(t)} heeft nog {_left(t)}.")
        for t in timers
    )


def timer_already(operation: str, timers: list[dict[str, Any]]) -> str:
    if len(timers) == 1:
        state = "staat al op pauze" if operation == "pause" else "loopt al"
        return _capital(f"{_timer(timers[0])} {state}.")
    return "Alle timers staan al op pauze." if operation == "pause" else "Alle timers lopen al."


def timer_changed(operation: str, timer: dict[str, Any], seconds: float, left: float) -> dict[str, str]:
    added = operation == "add"
    said = f"toegevoegd aan {_timer(timer)}" if added else f"afgetrokken van {_timer(timer)}"
    specific = f"{_capital(duration(seconds))} {said}."
    return {
        "brief": "Tijd toegevoegd." if added else "Tijd afgetrokken.",
        "specific": specific,
        "verbose": f"{specific[:-1]}, nog {duration(left)}.",
    }


def timer_done(operation: str, timer: dict[str, Any]) -> dict[str, str]:
    done = {"cancel": "geannuleerd", "pause": "gepauzeerd", "resume": "hervat"}[operation]
    specific = f"{_capital(_named(timer))} {done}."
    left = _left(timer)
    verbose = {
        "cancel": f"{specific[:-1]}, er was nog {left} over.",
        "pause": f"{specific[:-1]}, nog {left} over.",
        "resume": f"{specific[:-1]}, nog {left}.",
    }[operation]
    return {"brief": f"Timer {done}.", "specific": specific, "verbose": verbose}


def timers_ringing(timers: list[dict[str, Any]]) -> str:
    return " ".join(_capital(f"{_timer(t)} is afgelopen.") for t in timers)


# ------------------------------------------------------------------- music

NO_PLAYER = "Hier is geen muziekspeler."
NOTHING_PLAYING = "Er speelt niets."
MUSIC_SAID = {
    "no_player": NO_PLAYER,
    "nothing_playing": NOTHING_PLAYING,
    "missing_query": "Wat moet ik afspelen?",
    "missing_volume": "Welk volume?",
    "already": "Het speelt al.",
}
MOVE_SAID = {
    "nothing_playing": "Er speelt niets om te verplaatsen.",
    "no_player": "Daar is geen muziekspeler.",
    "already": "De muziek speelt daar al.",
}
NOT_KNOWN_PLAYING = "Ik weet niet wat er speelt."


def media(title: str, artist: str | None) -> str:
    return f"{title} van {artist}" if artist else title


def item(name: str, media_type: str, artist: str | None) -> str:
    if media_type == "playlist":
        return name if "lijst" in name.lower() or "playlist" in name.lower() else f"de afspeellijst {name}"
    if artist and media_type in ("track", "album"):
        return f"{name} van {artist}"
    return name


def not_found(query: str) -> str:
    return f"Ik kon {query} niet vinden."


def now_playing(what: str, paused: bool) -> str:
    return f"{_capital(what)} is gepauzeerd." if paused else f"Dit is {what}."


_CONTROL_SAID = {
    "pause": "Gepauzeerd.",
    "resume": "Hervat.",
    "stop": "Gestopt.",
    "next": "Volgend nummer.",
    "previous": "Vorig nummer.",
    "volume_up": "Harder.",
    "volume_down": "Zachter.",
    "volume_set": "Volume ingesteld.",
    "mute": "Gedempt.",
    "unmute": "Geluid aan.",
    "shuffle_on": "Shuffle aan.",
    "shuffle_off": "Shuffle uit.",
    "repeat_all": "Alles wordt herhaald.",
    "repeat_one": "Dit nummer wordt herhaald.",
    "repeat_off": "Herhalen uit.",
}
_DONE = {"pause": "gepauzeerd", "resume": "hervat", "stop": "gestopt"}
_FOUND_SAID = {"play": "Ik speel muziek.", "add": "Toegevoegd aan de wachtrij.", "radio": "Ik speel vergelijkbare muziek."}


def control(operation: str, volume: float | None, what: str | None, players: list[str]) -> dict[str, str]:
    brief = _CONTROL_SAID.get(operation, "Klaar.")
    specific = brief
    if volume is not None:
        specific = f"Volume op {round(volume * 100)} procent."
    elif operation in _DONE and what:
        specific = f"{_capital(what)} {_DONE[operation]}."
    return {"brief": brief, "specific": specific, "verbose": _on_players(specific, players)}


def found(operation: str, what: str, players: list[str]) -> dict[str, str]:
    specific = {"add": f"{_capital(what)} toegevoegd aan de wachtrij.", "radio": f"Ik speel muziek zoals {what}."}.get(
        operation, f"Ik speel {what}."
    )
    return {"brief": _FOUND_SAID.get(operation, "Ik speel."), "specific": specific, "verbose": _on_players(specific, players)}


def moved(what: str | None, source: str, target: str, to: str) -> dict[str, str]:
    return {
        "brief": "Muziek verplaatst.",
        "specific": f"Muziek verplaatst {to}." if to else f"Muziek verplaatst naar {target}.",
        "verbose": f"{_capital(what or 'de muziek')} verplaatst van {source} naar {target}.",
    }


def _on_players(sentence: str, players: list[str]) -> str:
    if not players:
        return sentence
    return f"{sentence[:-1]}, op {join(players)}."
