"""What the app says in German: every sentence it makes itself, with English's
names (see voice_labs/languages/en/speech.py for what each is).

German needs a place's gender for its article ("in der Küche", "im
Wohnzimmer"; "in die Küche", "ins Wohnzimmer") and a noun's case for its own
("der Pizza-Timer", "den Pizza-Timer"). A room's gender is its last part's
("Kinderzimmer" is a Zimmer), from the commonest; a name of none of them is
"im Bereich …". Confirmations are short, as Home Assistant's are: "Licht
eingeschaltet", "Pizza-Timer gestartet".
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------- anywhere

SORRY = "Entschuldigung, das habe ich nicht verstanden."
FAILED = "Entschuldigung, das hat nicht geklappt."
NO_SPEAKER = "Entschuldigung, ich weiß nicht, wer spricht."

def not_on_list(items: list[str]) -> str:
    """A to-do list command's items that aren't on the list (``on_list``)."""
    return f"{join(items)} {'steht' if len(items) == 1 else 'stehen'} nicht auf der Liste."


def already_on_list(items: list[str]) -> str:
    """What adding to a to-do list found on it already, not checked off
    (``adds_to_list``)."""
    return f"{join(items)} {'steht' if len(items) == 1 else 'stehen'} schon auf der Liste."


NOT_SET = "Das ist noch nicht eingerichtet."
WENT_WRONG = "Entschuldigung, da ist etwas schiefgelaufen."
NO_COMMANDS = "Es sind noch keine Sprachbefehle eingerichtet. Füge welche in der Voice Labs App hinzu."
TIMERS_NEED_INTEGRATION = "Für Timer braucht es die Voice Labs Integration in Home Assistant."

NO_AREA = 'Ich weiß nicht, in welchem Bereich ich bin. Gib diesem Satelliten einen Bereich.'
"""A command for the satellite's area, on a satellite in none."""


def nothing_there(place: str) -> str:
    """An intent found nothing of the kind ``place`` ("in the Kitchen"; "" for a device by name)."""
    return f"Ich habe {place} nichts dergleichen gefunden." if place else "Das habe ich nicht gefunden."


def not_exposed(place: str) -> str:
    """What it found there isn't exposed to Assist."""
    return f"Nichts davon {place} ist für Assist freigegeben." if place else "Das ist für Assist nicht freigegeben."


def unavailable(place: str) -> str:
    """What it found there is all unavailable."""
    return f"Alles davon {place} ist gerade nicht verfügbar." if place else "Das ist gerade nicht verfügbar."


# What a device can't do of what a command needs (its ``supports``), by
# capability; None for what Home Assistant refused without saying.
_CAN = {
    "brightness": "die Helligkeit ändern",
    "color": "die Farbe ändern",
    "color_temp": "die Farbtemperatur ändern",
    "position": "auf eine Position gestellt werden",
    "tilt": "gekippt werden",
    "temperature": "auf eine Temperatur gestellt werden",
    "fan_mode": "einen Lüftermodus einstellen",
    "hvac_mode": "den Modus wechseln",
    "volume_set": "auf eine Lautstärke gestellt werden",
    None: "das",
}
_CANNOT = {
    "brightness": "die Helligkeit nicht ändern",
    "color": "die Farbe nicht ändern",
    "color_temp": "die Farbtemperatur nicht ändern",
    "position": "nicht auf eine Position gestellt werden",
    "tilt": "nicht gekippt werden",
    "temperature": "nicht auf eine Temperatur gestellt werden",
    "fan_mode": "keinen Lüftermodus einstellen",
    "hvac_mode": "den Modus nicht wechseln",
    "volume_set": "nicht auf eine Lautstärke gestellt werden",
    None: "das nicht",
}


def cannot(names: list[str], capability: str | None, value: str | None = None) -> str:
    """Devices that can't: "Garagentor kann nicht auf eine Position gestellt
    werden."; that have no ``value`` (a mode)."""
    if value is not None:
        what = f"den Lüfter nicht auf {value} stellen" if capability == "fan_mode" else f"nicht auf {value} gestellt werden"
    else:
        what = _CANNOT.get(capability, _CANNOT[None])
    return f"{join(names)} {'kann' if len(names) == 1 else 'können'} {what}."


def none_can(place: str, capability: str | None) -> str:
    """Nothing of the kind ``place`` can: "Nichts in der Küche kann die Farbe ändern."."""
    what = _CAN.get(capability, _CAN[None])
    return f"Nichts {place} kann {what}." if place else f"Nichts kann {what}."


# ------------------------------------------------------- other satellites


def announced(place: str) -> dict[str, str]:
    """An announcement, said on the other satellites: every one, or those
    ``place`` ("in the Kitchen", "upstairs")."""
    return {"brief": "Durchgesagt.", "specific": f"Durchgesagt {place}." if place else "Überall durchgesagt."}


def no_satellites(place: str) -> str:
    """No other satellite ``place`` ("in the Kitchen"; "" for anywhere)."""
    return f"Es gibt keinen anderen Satelliten {place}." if place else "Es gibt keine anderen Satelliten."


def connecting(to: str) -> str:
    """The intercom, about to connect ``to`` there (``place(to=True)``)."""
    return f"Ich verbinde {to}."


def intercom_busy(place: str) -> str:
    """The satellite there is in a call, ringing or connected."""
    return f"Der Satellit {place} ist gerade beschäftigt."


INTERCOM_FIRMWARE = "Die Gegensprechanlage braucht neuere Firmware auf beiden Satelliten."
"""Either satellite's firmware can't connect the intercom."""

INTERCOM_HERE = "Da bist du gerade."
"""The intercom asked for the satellite's own area, with none other there."""


EXAMPLE_NAMES = {
    "room": "Küche",
    "player": "Küchenlautsprecher",
    "other_player": "Wohnzimmerlautsprecher",
    "timers": ["pizza", "tee", "lasagne"],
}


def join(parts: list[str]) -> str:
    """"a", "a und b", "a, b und c"."""
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " und " + parts[-1]


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


# ------------------------------------------------------------------ places

_GENDER = {
    # Neuter: im, ins.
    "zimmer": "n", "bad": "n", "büro": "n", "haus": "n", "studio": "n", "atelier": "n", "labor": "n",
    "wc": "n", "klo": "n", "foyer": "n", "geschoss": "n", "stockwerk": "n", "apartment": "n", "lager": "n",
    "loft": "n", "souterrain": "n", "dach": "n", "kino": "n", "gym": "n", "esszimmer": "n",
    # Masculine: im, in den.
    "flur": "m", "keller": "m", "garten": "m", "boden": "m", "balkon": "m", "gang": "m", "raum": "m",
    "hof": "m", "eingang": "m", "bereich": "m", "korridor": "m", "speicher": "m", "carport": "m",
    "pool": "m", "schuppen": "m", "stock": "m", "windfang": "m", "stall": "m", "saal": "m", "salon": "m",
    "vorplatz": "m", "platz": "m",
    # Feminine: in der, in die.
    "küche": "f", "garage": "f", "terrasse": "f", "diele": "f", "toilette": "f", "kammer": "f",
    "werkstatt": "f", "veranda": "f", "galerie": "f", "sauna": "f", "einfahrt": "f", "auffahrt": "f",
    "etage": "f", "ebene": "f", "wohnung": "f", "hütte": "f", "laube": "f", "ankleide": "f",
    "bibliothek": "f", "stube": "f", "halle": "f", "loggia": "f", "empore": "f", "treppe": "f",
    "lounge": "f", "bar": "f", "dusche": "f", "einliegerwohnung": "f",
}
"""Rooms and floors by gender, by a name's last part: the longest that ends it."""

_ADVERBS = {"oben": "nach oben", "unten": "nach unten", "draußen": "nach draußen", "drinnen": "nach drinnen"}
"""Places said as they are ("oben"), and where to ("nach oben")."""

_ADJECTIVES = {
    "groß", "klein", "ober", "unter", "erst", "zweit", "dritt", "viert", "vorder", "hinter", "mittler",
    "neu", "alt", "blau", "rot", "grün", "gelb", "weiß", "grau", "hell", "link", "recht", "äußer", "inner",
    "nördlich", "südlich", "östlich", "westlich", "warm", "kalt", "gemütlich",
}
"""Stems of adjectives a room's name may have ("Großes Bad", "Erster
Stock"): inflected with it ("im großen Bad"). Other words stay as named."""


def place(places: list[tuple[str, str]], to: bool = False) -> str:
    """Where a command acts: "in der Küche", "im Wohnzimmer", "im ersten
    Stock", "oben"; ``to``, where something goes: "in die Küche", "ins
    Wohnzimmer", "nach oben"."""
    return " und ".join(_place(name, to) for _kind, name in places)


def _place(name: str, to: bool) -> str:
    lower = name.lower()
    if lower in _ADVERBS:
        return _ADVERBS[lower] if to else lower
    words = name.split()
    if any(_possessive(word) for word in words[:-1]):
        return f"in {name}"  # "in Sams Zimmer"
    gender = _gender(words[-1])
    if gender is None:
        return f"in den Bereich {name}" if to else f"im Bereich {name}"
    said = " ".join([*(_inflected(word, gender, to) for word in words[:-1]), words[-1]])
    if to:
        return {"m": f"in den {said}", "n": f"ins {said}", "f": f"in die {said}"}[gender]
    return f"in der {said}" if gender == "f" else f"im {said}"


def _gender(word: str) -> str | None:
    lower = word.lower()
    for part in sorted(_GENDER, key=len, reverse=True):
        if lower.endswith(part):
            return _GENDER[part]
    return None


def _possessive(word: str) -> bool:
    """"Sams", "Max'": a person's."""
    return word[:1].isupper() and (word.endswith(("'", "’")) or (word.endswith("s") and not word.endswith("es")))


def _inflected(word: str, gender: str, to: bool) -> str:
    """An adjective of a room's name after "im"/"in der" (-en) or "ins"/"in
    die" (-e; -en for "in den"), lowercase as in a sentence: "Erster Stock"
    is "im ersten Stock"."""
    lower = word.lower()
    for ending in ("es", "er", "em", "en", "e", ""):
        stem = lower[: len(lower) - len(ending)] if ending else lower
        if lower.endswith(ending) and stem in _ADJECTIVES:
            return stem + ("e" if to and gender in ("n", "f") else "en")
    return word


# ------------------------------------------------------------------- hints


def hint(description: str, why: str) -> str:
    """The description is the command's own, as what one wants to do: "das
    Licht einschalten"."""
    return f"Es sieht so aus, als wolltest du {description}, {why}."


NOT_HERE = "aber das ist hier nicht eingerichtet"
PARTLY = "aber ich habe nicht alles verstanden"


def no_room(device: str) -> str:
    return f"aber ich weiß nicht, in welchem Raum {device} ist"


def not_in(device: str, room: str) -> str:
    return f"aber {device} ist nicht {_place(room, False)}"


def not_here(device: str, rooms: list[str]) -> str:
    """Why: the device said, by a name several share or one heard only in
    its room, isn't in the satellite's, and no room was said: where it is."""
    where = place([("area", room) for room in rooms])
    return f"aber {device} ist nicht hier" + (", nur " + where if rooms else "")


def several(device: str) -> str:
    """Why: the name said is more than one device's where it is heard (a
    name of the area's two devices, or a whole house name two have)."""
    return f"aber mehr als ein Gerät heißt {device}"


_WHICH = {"entity": "welches Gerät", "area": "welchen Raum", "floor": "welches Stockwerk", "scene": "welche Szene", "script": "welches Skript"}
_SLOTS = {
    "color": "welche Farbe", "percentage": "wie viel Prozent", "temperature": "welche Temperatur",
    "volume": "wie laut", "item": "was", "reminder": "woran", "query": "was", "timer": "welcher Timer",
    "time": "wann", "number": "welche Zahl",
}
"""What a slot asks, by its name in the library: "welche Farbe"."""


def _not_heard(slot: str) -> str:
    which = _SLOTS.get(slot)
    return f"aber ich habe nicht gehört, {which}" if which else "aber ich habe nicht alles gehört"


def missing(first: dict[str, Any], duration: bool = False, number_slot: str = "number") -> str:
    kind = first.get("kind")
    if kind == "require":
        if first.get("slot"):
            return _not_heard(first["slot"])
        return f"aber ich habe „{first['phrases'][0]}“ nicht gehört"
    if kind == "name":
        return f"aber ich habe nicht gehört, {_WHICH.get(first['type'], 'welches')}"
    if kind == "number":
        if first.get("heard"):
            return f"aber {first['heard'][0]} geht nicht"
        if duration:
            return "aber ich habe nicht gehört, wie lange"
        return _not_heard(number_slot)
    if kind == "text":
        return _not_heard(first["slot"])
    if kind == "unexplained":
        words = " ".join(first.get("words") or [])
        return f"aber ich habe „{words}“ nicht verstanden" if words else PARTLY
    if kind == "unknown_name":
        return f"aber „{' '.join(first.get('words') or [])}“ kenne ich nicht"
    if kind == "place":
        what = {"floor": "kein Stockwerk", "entity": "kein Gerät"}.get(str(first.get("type")), "keinen Raum")
        return f"aber dafür kann ich {what} wählen"
    return "aber ich war mir nicht sicher"


# ------------------------------------------------------------------ timers

_UNITS = (("Stunde", "Stunden"), ("Minute", "Minuten"), ("Sekunde", "Sekunden"))


def _parts(seconds: float) -> list[tuple[int, int]]:
    """(value, unit index) to say, largest first."""
    total = round(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    parts = [(value, unit) for unit, value in enumerate((hours, minutes, secs)) if value]
    parts = parts or [(0, 2)]
    return parts[:2] if hours else parts  # an hour and some: seconds don't matter


def _duration(seconds: float, dative: bool = False) -> str:
    """"eine Stunde und 5 Minuten"; ``dative`` after "in": "in einer Stunde"."""
    one = "einer" if dative else "eine"
    return join([f"{one} {_UNITS[unit][0]}" if value == 1 else f"{value} {_UNITS[unit][1]}" for value, unit in _parts(seconds)])


def duration(seconds: float, adjective: bool = False) -> str:
    """"eine Stunde und 5 Minuten"; as an adjective, "5-Minuten" (the
    "5-Minuten-Timer")."""
    if adjective:
        return "-".join(f"{'Ein' if value == 1 else value}-{_UNITS[unit][1]}" for value, unit in _parts(seconds))
    return _duration(seconds)


def clock(hour: int, minute: int) -> str:
    """"7 Uhr 30", "19 Uhr", "Mitternacht"."""
    if (hour, minute) == (0, 0):
        return "Mitternacht"
    return f"{hour} Uhr {minute}" if minute else f"{hour} Uhr"


def _named(timer: dict[str, Any]) -> str:
    """Without its article: "Pizza-Timer", "Timer Nudeln kochen", "Timer für
    5 Minuten"."""
    name = str(timer.get("name") or "")
    if name and " " not in name:
        return f"{_capital(name)}-Timer"
    if name:
        return f"Timer {name}"
    return f"Timer für {duration(float(timer.get('created_duration') or 0))}"


_ARTICLE = {"nom": "der", "acc": "den", "dat": "dem"}


def _timer(timer: dict[str, Any], case: str = "nom") -> str:
    """With its article in ``case`` (nom, acc, dat): "der Pizza-Timer"."""
    return f"{_ARTICLE[case]} {_named(timer)}"


def _left(timer: dict[str, Any]) -> str:
    return duration(float(timer.get("remaining") or 0))


def _running(timers: list[dict[str, Any]]) -> str:
    listed = join([_timer(t) for t in timers])
    return f"Es läuft {listed}." if len(timers) == 1 else f"Es laufen {listed}."


TIMER_HOW_LONG = "Wie lange soll der Timer laufen?"
TIMER_HOW_MUCH = "Wie viel Zeit?"
ALARM_WHEN = "Für wann soll ich den Wecker stellen?"
NO_TIMERS = "Es läuft kein Timer."


def timer_started(timer: dict[str, Any], ends: tuple[int, int]) -> dict[str, str]:
    specific = f"{_named(timer)} gestartet."
    return {"brief": "Timer gestartet.", "specific": specific, "verbose": f"{specific[:-1]}, er endet um {clock(*ends)}."}


def alarm_set(at: tuple[int, int], seconds: float) -> dict[str, str]:
    specific = f"Wecker für {clock(*at)} gestellt."
    return {"brief": "Wecker gestellt.", "specific": specific, "verbose": f"{specific[:-1]}, in {_duration(seconds, dative=True)}."}


def timers_cancelled(timers: list[dict[str, Any]]) -> dict[str, str]:
    count = len(timers)
    return {
        "brief": "Timer abgebrochen.",
        "specific": "Timer abgebrochen." if count == 1 else f"{count} Timer abgebrochen.",
        "verbose": _capital(f"{join([_timer(t, 'acc') for t in timers])} abgebrochen."),
    }


def timer_not_found(asked: str | float | None, running: list[dict[str, Any]]) -> str:
    there = _running(running)
    if asked is None:
        return there
    if isinstance(asked, (int, float)):
        name = f"Timer für {duration(asked)}"
    else:
        name = asked if asked.lower().endswith("timer") else _named({"name": asked})
    return f"Es gibt keinen {name}. {there}"


def which_timer(timers: list[dict[str, Any]]) -> str:
    return f"Welcher Timer? {_running(timers)}"


def timer_status(timers: list[dict[str, Any]]) -> str:
    return " ".join(
        _capital(f"{_timer(t)} ist pausiert, noch {_left(t)}.")
        if t.get("status") == "paused"
        else _capital(f"{_timer(t)} läuft noch {_left(t)}.")
        for t in timers
    )


def timer_already(operation: str, timers: list[dict[str, Any]]) -> str:
    if len(timers) == 1:
        state = "ist schon pausiert" if operation == "pause" else "läuft schon"
        return _capital(f"{_timer(timers[0])} {state}.")
    return "Alle Timer sind schon pausiert." if operation == "pause" else "Alle Timer laufen schon."


def timer_changed(operation: str, timer: dict[str, Any], seconds: float, left: float) -> dict[str, str]:
    added = operation == "add"
    said = f"zum {_named(timer)} hinzugefügt" if added else f"vom {_named(timer)} abgezogen"
    specific = f"{_capital(duration(seconds))} {said}."
    return {
        "brief": "Zeit hinzugefügt." if added else "Zeit abgezogen.",
        "specific": specific,
        "verbose": f"{specific[:-1]}, noch {duration(left)}.",
    }


def timer_done(operation: str, timer: dict[str, Any]) -> dict[str, str]:
    done = {"cancel": "abgebrochen", "pause": "pausiert", "resume": "fortgesetzt"}[operation]
    specific = f"{_named(timer)} {done}."
    left = _left(timer)
    verbose = {
        "cancel": f"{specific[:-1]}, es waren noch {left}.",
        "pause": f"{specific[:-1]}, noch {left} übrig.",
        "resume": f"{specific[:-1]}, noch {left}.",
    }[operation]
    return {"brief": f"Timer {done}.", "specific": specific, "verbose": verbose}


def timers_ringing(timers: list[dict[str, Any]]) -> str:
    return " ".join(_capital(f"{_timer(t)} ist abgelaufen.") for t in timers)


# ------------------------------------------------------------------- music

NO_PLAYER = "Hier gibt es keinen Musikplayer."
NOTHING_PLAYING = "Gerade läuft nichts."
MUSIC_SAID = {
    "no_player": NO_PLAYER,
    "nothing_playing": NOTHING_PLAYING,
    "missing_query": "Was soll ich spielen?",
    "missing_volume": "Welche Lautstärke?",
    "already": "Es läuft schon.",
}
MOVE_SAID = {
    "nothing_playing": "Gerade läuft nichts, was ich verschieben könnte.",
    "no_player": "Dort gibt es keinen Musikplayer.",
    "already": "Die Musik läuft dort schon.",
}
NOT_KNOWN_PLAYING = "Ich weiß nicht, was gerade läuft."


def media(title: str, artist: str | None) -> str:
    return f"{title} von {artist}" if artist else title


def item(name: str, media_type: str, artist: str | None) -> str:
    if media_type == "playlist":
        return name if "playlist" in name.lower() else f"die Playlist {name}"
    if artist and media_type in ("track", "album"):
        return f"{name} von {artist}"
    return name


def not_found(query: str) -> str:
    return f"Ich habe {query} nicht gefunden."


def now_playing(what: str, paused: bool) -> str:
    return f"{_capital(what)} ist pausiert." if paused else f"Das ist {what}."


_CONTROL_SAID = {
    "pause": "Pausiert.",
    "resume": "Fortgesetzt.",
    "stop": "Gestoppt.",
    "next": "Nächster Titel.",
    "previous": "Vorheriger Titel.",
    "volume_up": "Lauter.",
    "volume_down": "Leiser.",
    "volume_set": "Lautstärke eingestellt.",
    "mute": "Stummgeschaltet.",
    "unmute": "Ton an.",
    "shuffle_on": "Zufallswiedergabe an.",
    "shuffle_off": "Zufallswiedergabe aus.",
    "repeat_all": "Alles wird wiederholt.",
    "repeat_one": "Dieser Titel wird wiederholt.",
    "repeat_off": "Wiederholen aus.",
}
_DONE = {"pause": "pausiert", "resume": "fortgesetzt", "stop": "gestoppt"}
_FOUND_SAID = {"play": "Ich spiele Musik.", "add": "Zur Warteschlange hinzugefügt.", "radio": "Ich spiele ähnliche Musik."}


def control(operation: str, volume: float | None, what: str | None, players: list[str]) -> dict[str, str]:
    brief = _CONTROL_SAID.get(operation, "Erledigt.")
    specific = brief
    if volume is not None:
        specific = f"Lautstärke auf {round(volume * 100)} Prozent."
    elif operation in _DONE and what:
        specific = f"{_capital(what)} {_DONE[operation]}."
    return {"brief": brief, "specific": specific, "verbose": _on_players(specific, players)}


def found(operation: str, what: str, players: list[str]) -> dict[str, str]:
    specific = {"add": f"{_capital(what)} zur Warteschlange hinzugefügt.", "radio": f"Ich spiele Musik wie {what}."}.get(
        operation, f"Ich spiele {what}."
    )
    return {"brief": _FOUND_SAID.get(operation, "Ich spiele."), "specific": specific, "verbose": _on_players(specific, players)}


def moved(what: str | None, source: str, target: str, to: str) -> dict[str, str]:
    return {
        "brief": "Musik verschoben.",
        "specific": f"Musik {to} verschoben." if to else f"Musik auf {target} verschoben.",
        "verbose": f"{_capital(what or 'die Musik')} von {source} auf {target} verschoben.",
    }


def _on_players(sentence: str, players: list[str]) -> str:
    """"So What von Miles Davis pausiert, auf Küchenlautsprecher."."""
    if not players:
        return sentence
    return f"{sentence[:-1]}, auf {join(players)}."
