"""What the app says in Italian: every sentence it makes itself, with English's
names (see voice_labs/languages/en/speech.py for what each is).

A room is said with a bare "in" when it is one alone ("in cucina", "in
camera da letto"), else with the preposition joined to its article, by the
noun's gender and first sound: "nella camera di Luca", "nel bagno piccolo",
"nello studio grande", "nell'ingresso". A floor is "al": "al primo piano",
"al piano terra", "all'attico". A time of day is on a 24-hour clock: "alle
7 e 30", "all'una", "a mezzogiorno". Timers are "il timer pizza", "il timer
di 5 minuti"."""

from __future__ import annotations

import re
from typing import Any

# --------------------------------------------------------------- anywhere

SORRY = "Scusa, non ho capito."
FAILED = "Scusa, non ha funzionato."
NO_SPEAKER = "Scusa, non so chi sta parlando."

def not_on_list(items: list[str]) -> str:
    """A to-do list command's items that aren't on the list (``on_list``)."""
    return f"{join(items)} {'non è' if len(items) == 1 else 'non sono'} nella lista."


def already_on_list(items: list[str]) -> str:
    """What adding to a to-do list found on it already, not checked off
    (``adds_to_list``)."""
    return f"{join(items)} {'è già' if len(items) == 1 else 'sono già'} nella lista."


NOT_SET = "Non è ancora configurato."
WENT_WRONG = "Scusa, qualcosa è andato storto."
NO_COMMANDS = "Non ci sono ancora comandi vocali. Aggiungine qualcuno nell'app Voice Labs."
TIMERS_NEED_INTEGRATION = "I timer hanno bisogno dell'integrazione Voice Labs in Home Assistant."

NO_AREA = "Non so in quale area mi trovo. Assegna un'area a questo satellite."
"""A command for the satellite's area, on a satellite in none."""


def nothing_there(place: str) -> str:
    """An intent found nothing of the kind ``place`` ("in the Kitchen"; "" for a device by name)."""
    return f"Non ho trovato niente del genere {place}." if place else "Non l'ho trovato."


def not_exposed(place: str) -> str:
    """What it found there isn't exposed to Assist."""
    return f"Niente del genere {place} è esposto ad Assist." if place else "Non è esposto ad Assist."


def unavailable(place: str) -> str:
    """What it found there is all unavailable."""
    return f"Tutto ciò {place} non è disponibile ora." if place else "Non è disponibile ora."


# What a device can't do of what a command needs (its ``supports``), by
# capability; None for what Home Assistant refused without saying.
_CAN = {
    "brightness": "cambiare luminosità",
    "color": "cambiare colore",
    "color_temp": "cambiare temperatura di colore",
    "position": "essere impostato su una posizione",
    "tilt": "essere inclinato",
    "temperature": "essere impostato su una temperatura",
    "fan_mode": "impostare una modalità della ventola",
    "hvac_mode": "cambiare modalità",
    "volume_set": "essere impostato su un volume",
    None: "farlo",
}


def cannot(names: list[str], capability: str | None, value: str | None = None) -> str:
    """Devices that can't: "Porta del garage non può essere impostato su una
    posizione."; that have no ``value`` (a mode)."""
    if value is not None:
        what = f"impostare la ventola su {value}" if capability == "fan_mode" else f"essere impostato su {value}"
    else:
        what = _CAN.get(capability, _CAN[None])
    return f"{join(names)} {'non può' if len(names) == 1 else 'non possono'} {what}."


def none_can(place: str, capability: str | None) -> str:
    """Nothing of the kind ``place`` can: "Niente in cucina può cambiare colore."."""
    what = _CAN.get(capability, _CAN[None])
    return f"Niente {place} può {what}." if place else f"Niente può {what}."


# ------------------------------------------------------- other satellites


def announced(place: str) -> dict[str, str]:
    """An announcement, said on the other satellites: every one, or those
    ``place`` ("in the Kitchen", "upstairs")."""
    return {"brief": "Annunciato.", "specific": f"Annunciato {place}." if place else "Annunciato ovunque."}


def no_satellites(place: str) -> str:
    """No other satellite ``place`` ("in the Kitchen"; "" for anywhere)."""
    return f"Non c'è un altro satellite {place}." if place else "Non ci sono altri satelliti."


def connecting(to: str) -> str:
    """The intercom, about to connect ``to`` there (``place(to=True)``)."""
    return f"Mi collego {to}."


def intercom_busy(place: str) -> str:
    """The satellite there is in a call, ringing or connected."""
    return f"Il satellite {place} è occupato in questo momento."


INTERCOM_FIRMWARE = "L'interfono richiede un firmware più recente su entrambi i satelliti."
"""Either satellite's firmware can't connect the intercom."""

INTERCOM_HERE = "È qui che ti trovi."
"""The intercom asked for the satellite's own area, with none other there."""


EXAMPLE_NAMES = {
    "room": "Cucina",
    "player": "Altoparlante della cucina",
    "other_player": "Altoparlante del soggiorno",
    "timers": ["pizza", "tè", "lasagne"],
}


def join(parts: list[str]) -> str:
    """"a", "a e b", "a, b e c"."""
    return "".join(parts) if len(parts) <= 1 else ", ".join(parts[:-1]) + " e " + parts[-1]


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


# ------------------------------------------------------------------ places

_GENDER = {
    **dict.fromkeys(
        ["cucina", "camera", "cameretta", "sala", "stanza", "cantina", "mansarda", "taverna", "lavanderia", "terrazza",
         "veranda", "casa", "dispensa", "soffitta", "scala", "palestra", "piscina", "camerina", "cabina", "serra",
         "loggia", "tavernetta", "zona", "dependance"], "f"),
    **dict.fromkeys(
        ["soggiorno", "salotto", "salone", "bagno", "ufficio", "studio", "corridoio", "ingresso", "garage", "giardino",
         "balcone", "terrazzo", "seminterrato", "sottotetto", "piano", "pianterreno", "ripostiglio", "disimpegno",
         "box", "attico", "solaio", "guardaroba", "portico", "cortile", "atrio", "bagnetto", "living", "open space",
         "salottino", "magazzino", "laboratorio", "patio", "loft", "appartamento"], "m"),
}
"""Rooms and floors by gender, by noun."""

_BARE = {"sopra": "di sopra", "di sopra": "di sopra", "sotto": "di sotto", "di sotto": "di sotto", "fuori": "fuori",
         "giù": "giù", "su": "su"}
"""Places said as they are: "di sopra"."""

_VOWELS = "aeiouhàèéìòù"


def place(places: list[tuple[str, str]], to: bool = False) -> str:
    """Where a command acts: "in cucina", "nella camera di Luca", "al primo
    piano", "di sopra"; ``to``, where something goes: the same."""
    return join([_place(kind, name) for kind, name in places])


def _noun(name: str) -> tuple[str | None, int]:
    """The room a name is named by and which of its words that is: "Camera
    da letto" is a camera, "Bagno piccolo" a bagno, "Piano terra" a piano."""
    words = [w.strip("'") for w in name.lower().replace("’", "'").split()]
    for index, word in enumerate(words):
        if word in _GENDER:
            return word, index
        for noun in _GENDER:
            if word.endswith("'" + noun):
                return noun, index
    return None, -1


def _article(word: str, gender: str, before: str) -> str:
    """The preposition ``before`` ("in", "a") joined to the article ``word``
    takes: "nella", "nel", "nello", "nell'"; "alla", "al", "allo", "all'"."""
    stem = {"in": "nel", "a": "al", "da": "dal", "di": "del", "su": "sul"}[before]
    if word[:1] in _VOWELS:
        return stem + "l'"
    if gender == "f":
        return stem + "la"
    if re.match(r"s[^aeiouàèéìòù]|z|gn|ps|pn|x|y|i[aeiou]", word):
        return stem + "lo"
    return stem


def _place(kind: str, name: str) -> str:
    lower = name.strip().lower()
    if lower in _BARE:
        return _BARE[lower]
    noun, index = _noun(name)
    gender = _GENDER.get(noun or "", "m")
    words = lower.split()
    # A room alone, or a room "da" something ("Camera da letto", "Sala da
    # pranzo"): a bare "in".
    if kind != "floor" and noun and index == 0 and (len(words) == 1 or words[1] == "da"):
        return f"in {name}"
    article = _article(lower, gender, "a" if kind == "floor" else "in")
    return f"{article}{name}" if article.endswith("'") else f"{article} {name}"


# ------------------------------------------------------------------- hints


def hint(description: str, why: str) -> str:
    """The description is the command's own, as what one wants to do:
    "accendere la luce"."""
    return f"Sembra che tu voglia {description}, {why}."


PARTLY = "ma non ho capito tutto"


def no_room(device: str) -> str:
    return f"ma non so in quale stanza si trova {device}"


def not_in(device: str, room: str) -> str:
    return f"ma {device} non è {_place('area', room)}"


def not_here(device: str, rooms: list[str]) -> str:
    """Why: the device said, by a name several share or one heard only in
    its room, isn't in the satellite's, and no room was said: where it is."""
    where = place([("area", room) for room in rooms])
    return f"ma {device} non è qui" + (", solo " + where if rooms else "")


def several(device: str) -> str:
    """Why: the name said is more than one device's where it is heard (a
    name of the area's two devices, or a whole house name two have)."""
    return f"ma più di un dispositivo si chiama {device}"


_WHICH = {"entity": "quale dispositivo", "area": "quale stanza", "floor": "quale piano", "scene": "quale scena", "script": "quale script"}
_SLOTS = {
    "color": "quale colore", "percentage": "quale percentuale", "temperature": "quale temperatura",
    "volume": "quale volume", "item": "cosa", "reminder": "cosa ricordarti", "query": "cosa", "timer": "quale timer",
    "time": "a che ora", "number": "quale numero",
}


def _not_heard(slot: str) -> str:
    which = _SLOTS.get(slot)
    return f"ma non ho sentito {which}" if which else "ma non ho sentito tutto"


def missing(first: dict[str, Any], duration: bool = False, number_slot: str = "number") -> str:
    kind = first.get("kind")
    if kind == "require":
        if first.get("slot"):
            return _not_heard(first["slot"])
        return f"ma non ho sentito «{first['phrases'][0]}»"
    if kind == "name":
        return f"ma non ho sentito {_WHICH.get(first['type'], 'quale')}"
    if kind == "number":
        if first.get("heard"):
            return f"ma {first['heard'][0]} non è possibile"
        if duration:
            return "ma non ho sentito per quanto tempo"
        return _not_heard(number_slot)
    if kind == "text":
        return _not_heard(first["slot"])
    if kind == "unexplained":
        words = " ".join(first.get("words") or [])
        return f"ma non ho capito «{words}»" if words else PARTLY
    if kind == "unknown_name":
        return f"ma non conosco «{' '.join(first.get('words') or [])}»"
    if kind == "place":
        what = {"floor": "nessun piano", "entity": "nessun dispositivo"}.get(str(first.get("type")), "nessuna stanza")
        return f"ma non posso scegliere {what} per questo"
    return "ma non ne ero sicuro"


# ------------------------------------------------------------------ timers

_UNITS = (("ora", "ore", True), ("minuto", "minuti", False), ("secondo", "secondi", False))
"""Each unit, its plural, and whether it is feminine."""


def _parts(seconds: float) -> list[tuple[int, int]]:
    total = round(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    parts = [(value, unit) for unit, value in enumerate((hours, minutes, secs)) if value] or [(0, 2)]
    return parts[:2] if hours else parts


def duration(seconds: float, adjective: bool = False) -> str:
    """"un'ora e 5 minuti", "un minuto"; as an adjective, the same (Italian
    says "di 5 minuti")."""
    said = []
    for value, unit in _parts(seconds):
        single, plural, feminine = _UNITS[unit]
        said.append(("un'" if feminine else "un ") + single if value == 1 else f"{value} {plural}")
    return join(said)


def clock(hour: int, minute: int) -> str:
    """"le 7 e 30", "le 19", "l'una e 5", "mezzogiorno", "mezzanotte"."""
    if (hour, minute) == (12, 0):
        return "mezzogiorno"
    if (hour, minute) == (0, 0):
        return "mezzanotte"
    said = "l'una" if hour == 1 else f"le {hour}"
    return f"{said} e {minute}" if minute else said


def _at(hour: int, minute: int) -> str:
    """"alle 7 e 30", "all'una", "a mezzogiorno"."""
    said = clock(hour, minute)
    if said.startswith("le "):
        return "al" + said
    if said.startswith("l'"):
        return "al" + said
    return f"a {said}"


def _named(timer: dict[str, Any]) -> str:
    """Without its article: "timer pizza", "timer di 5 minuti"."""
    name = str(timer.get("name") or "")
    if name:
        return name if name.lower().startswith("timer") else f"timer {name}"
    return f"timer di {duration(float(timer.get('created_duration') or 0))}"


def _timer(timer: dict[str, Any], before: str = "il") -> str:
    """With its article -- "il", or joined to "a" ("al"), "da" ("dal")."""
    return f"{before} {_named(timer)}"


def _left(timer: dict[str, Any]) -> str:
    return duration(float(timer.get("remaining") or 0))


def _missing(left: str, past: bool = False) -> str:
    """"manca un minuto", "mancano 5 minuti"; ``past``: "mancava",
    "mancavano"."""
    one = left.startswith(("un ", "un'"))
    return ("mancava" if one else "mancavano") if past else ("manca" if one else "mancano")


def _running(timers: list[dict[str, Any]]) -> str:
    listed = join([_timer(t) for t in timers])
    return f"C'è {listed}." if len(timers) == 1 else f"Ci sono {listed}."


TIMER_HOW_LONG = "Per quanto tempo?"
TIMER_HOW_MUCH = "Quanto tempo?"
ALARM_WHEN = "Per che ora devo impostare la sveglia?"
NO_TIMERS = "Non ci sono timer."


def timer_started(timer: dict[str, Any], ends: tuple[int, int]) -> dict[str, str]:
    specific = f"{_capital(_named(timer))} avviato."
    return {"brief": "Timer avviato.", "specific": specific, "verbose": f"{specific[:-1]}, suonerà {_at(*ends)}."}


def alarm_set(at: tuple[int, int], seconds: float) -> dict[str, str]:
    specific = f"Sveglia impostata per {clock(*at)}."
    return {"brief": "Sveglia impostata.", "specific": specific, "verbose": f"{specific[:-1]}, tra {duration(seconds)}."}


def timers_cancelled(timers: list[dict[str, Any]]) -> dict[str, str]:
    count = len(timers)
    return {
        "brief": "Timer annullato." if count == 1 else "Timer annullati.",
        "specific": "Timer annullato." if count == 1 else f"{count} timer annullati.",
        "verbose": f"Ho annullato {join([_timer(t) for t in timers])}.",
    }


def timer_not_found(asked: str | float | None, running: list[dict[str, Any]]) -> str:
    there = _running(running)
    if asked is None:
        return there
    if isinstance(asked, (int, float)):
        name = f"timer di {duration(asked)}"
    else:
        name = _named({"name": asked})
    return f"Non c'è nessun {name}. {there}"


def which_timer(timers: list[dict[str, Any]]) -> str:
    return f"Quale timer? {_running(timers)}"


def timer_status(timers: list[dict[str, Any]]) -> str:
    said = []
    for t in timers:
        left = _left(t)
        if t.get("status") == "paused":
            said.append(_capital(f"{_timer(t)} è in pausa, {_missing(left)} {left}."))
        else:
            said.append(_capital(f"{_timer(t, 'al')} {_missing(left)} {left}."))
    return " ".join(said)


def timer_already(operation: str, timers: list[dict[str, Any]]) -> str:
    if len(timers) == 1:
        state = "è già in pausa" if operation == "pause" else "è già in corso"
        return _capital(f"{_timer(timers[0])} {state}.")
    return "Tutti i timer sono già in pausa." if operation == "pause" else "Tutti i timer sono già in corso."


def timer_changed(operation: str, timer: dict[str, Any], seconds: float, left: float) -> dict[str, str]:
    added = operation == "add"
    specific = (f"Ho aggiunto {duration(seconds)} {_timer(timer, 'al')}." if added
                else f"Ho tolto {duration(seconds)} {_timer(timer, 'dal')}.")
    remaining = duration(left)
    return {
        "brief": "Tempo aggiunto." if added else "Tempo tolto.",
        "specific": specific,
        "verbose": f"{specific[:-1]}, {_missing(remaining)} {remaining}.",
    }


def timer_done(operation: str, timer: dict[str, Any]) -> dict[str, str]:
    done = {"cancel": "annullato", "pause": "messo in pausa", "resume": "ripreso"}[operation]
    specific = f"{_capital(_named(timer))} {done}."
    left = _left(timer)
    verbose = {
        "cancel": f"{specific[:-1]}, {_missing(left, past=True)} {left}.",
        "pause": f"{specific[:-1]}, {_missing(left)} {left}.",
        "resume": f"{specific[:-1]}, {_missing(left)} {left}.",
    }[operation]
    return {"brief": f"Timer {done}.", "specific": specific, "verbose": verbose}


def timers_ringing(timers: list[dict[str, Any]]) -> str:
    return " ".join(_capital(f"{_timer(t)} è scaduto.") for t in timers)


# ------------------------------------------------------------------- music

NO_PLAYER = "Qui non c'è nessun lettore musicale."
NOTHING_PLAYING = "Non c'è niente in riproduzione."
MUSIC_SAID = {
    "no_player": NO_PLAYER,
    "nothing_playing": NOTHING_PLAYING,
    "missing_query": "Cosa devo mettere?",
    "missing_volume": "Quale volume?",
    "already": "È già in riproduzione.",
}
MOVE_SAID = {
    "nothing_playing": "Non c'è niente in riproduzione da spostare.",
    "no_player": "Lì non c'è nessun lettore musicale.",
    "already": "La musica è già lì.",
}
NOT_KNOWN_PLAYING = "Non so cosa sta suonando."


def media(title: str, artist: str | None) -> str:
    return f"{title} di {artist}" if artist else title


def item(name: str, media_type: str, artist: str | None) -> str:
    if media_type == "playlist":
        return name if "playlist" in name.lower() else f"la playlist {name}"
    if artist and media_type in ("track", "album"):
        return f"{name} di {artist}"
    return name


def not_found(query: str) -> str:
    return f"Non ho trovato {query}."


def now_playing(what: str, paused: bool) -> str:
    return f"{_capital(what)} è in pausa." if paused else f"Sta suonando {what}."


_CONTROL_SAID = {
    "pause": "In pausa.",
    "resume": "Ripreso.",
    "stop": "Fermato.",
    "next": "Brano successivo.",
    "previous": "Brano precedente.",
    "volume_up": "Più alto.",
    "volume_down": "Più basso.",
    "volume_set": "Volume impostato.",
    "mute": "Audio disattivato.",
    "unmute": "Audio riattivato.",
    "shuffle_on": "Riproduzione casuale attivata.",
    "shuffle_off": "Riproduzione casuale disattivata.",
    "repeat_all": "Ripeto tutto.",
    "repeat_one": "Ripeto questo brano.",
    "repeat_off": "Ripetizione disattivata.",
}
_DONE = {"pause": "in pausa", "resume": "ripreso", "stop": "fermato"}
_FOUND_SAID = {"play": "Metto la musica.", "add": "Aggiunto alla coda.", "radio": "Metto musica simile."}


def control(operation: str, volume: float | None, what: str | None, players: list[str]) -> dict[str, str]:
    brief = _CONTROL_SAID.get(operation, "Fatto.")
    specific = brief
    if volume is not None:
        specific = f"Volume al {round(volume * 100)} per cento."
    elif operation in _DONE and what:
        specific = f"{_capital(what)} {_DONE[operation]}."
    return {"brief": brief, "specific": specific, "verbose": _on_players(specific, players)}


def found(operation: str, what: str, players: list[str]) -> dict[str, str]:
    specific = {"add": f"{_capital(what)} aggiunto alla coda.", "radio": f"Metto musica come {what}."}.get(
        operation, f"Metto {what}."
    )
    return {"brief": _FOUND_SAID.get(operation, "Metto la musica."), "specific": specific,
            "verbose": _on_players(specific, players)}


def moved(what: str | None, source: str, target: str, to: str) -> dict[str, str]:
    return {
        "brief": "Musica spostata.",
        "specific": f"Musica spostata {to}." if to else f"Musica spostata su {target}.",
        "verbose": f"{_capital(what or 'la musica')} spostata da {source} a {target}.",
    }


def _on_players(sentence: str, players: list[str]) -> str:
    if not players:
        return sentence
    return f"{sentence[:-1]}, su {join(players)}."


def not_available(place: str) -> str:
    """A command heard that isn't heard here: another area's own, or the
    whole home's turned off in this one (``place``: "in the Kitchen"; "" for
    a satellite in no area)."""
    return f"Quel comando non è disponibile {place or 'qui'}."
