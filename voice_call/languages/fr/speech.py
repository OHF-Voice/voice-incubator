"""What the app says in French: every sentence it makes itself, with English's
names (see voice_call/languages/en/speech.py for what each is).

A place needs its article, by the gender of its noun: "dans la cuisine",
"dans le salon", "dans l'entrée", "dans les toilettes"; a floor is "à":
"au premier étage", "à l'étage". Its noun is the first of the name's words
that is a room ("Chambre d'amis", "Grande chambre"); a name of none is
masculine. Timers are "le minuteur pizza", "au minuteur", "du minuteur"."""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------- anywhere

SORRY = "Désolé, je n'ai pas compris."
FAILED = "Désolé, ça n'a pas marché."
NO_SPEAKER = "Désolé, je ne sais pas qui parle."
NOT_ON_LIST = "{item} n'est pas sur la liste."
NOT_SET = "Ce n'est pas encore configuré."
WENT_WRONG = "Désolé, quelque chose s'est mal passé."
NO_COMMANDS = "Aucune commande vocale n'est encore configurée. Ajoutez-en dans l'application Voice Call."
TIMERS_NEED_INTEGRATION = "Les minuteurs ont besoin de l'intégration Voice Call dans Home Assistant."

NO_AREA = 'Je ne sais pas dans quelle pièce je suis. Donnez une pièce à ce satellite.'
"""A command for the satellite's area, on a satellite in none."""


def nothing_there(place: str) -> str:
    """An intent found nothing of the kind ``place`` ("in the Kitchen"; "" for a device by name)."""
    return f"Je n'ai rien trouvé de tel {place}." if place else "Je ne l'ai pas trouvé."


def not_exposed(place: str) -> str:
    """What it found there isn't exposed to Assist."""
    return f"Rien de tel {place} n'est exposé à Assist." if place else "Ce n'est pas exposé à Assist."


def unavailable(place: str) -> str:
    """What it found there is all unavailable."""
    return f"Tout cela {place} est indisponible pour le moment." if place else "C'est indisponible pour le moment."


# What a device can't do of what a command needs (its ``supports``), by
# capability; None for what Home Assistant refused without saying.
_CAN = {
    "brightness": "changer de luminosité",
    "color": "changer de couleur",
    "color_temp": "changer de température de couleur",
    "position": "être réglé sur une position",
    "tilt": "être incliné",
    "temperature": "être réglé sur une température",
    "fan_mode": "régler un mode de ventilation",
    "hvac_mode": "changer de mode",
    None: "faire ça",
}


def cannot(names: list[str], capability: str | None, value: str | None = None) -> str:
    """Devices that can't: "Porte de garage ne peut pas être réglé sur une
    position."; that have no ``value`` (a mode)."""
    if value is not None:
        what = f"régler le ventilateur sur {value}" if capability == "fan_mode" else f"être réglé sur {value}"
    else:
        what = _CAN.get(capability, _CAN[None])
    return f"{join(names)} {'ne peut pas' if len(names) == 1 else 'ne peuvent pas'} {what}."


def none_can(place: str, capability: str | None) -> str:
    """Nothing of the kind ``place`` can: "Rien dans la cuisine ne peut changer de couleur."."""
    what = _CAN.get(capability, _CAN[None])
    return f"Rien {place} ne peut {what}." if place else f"Rien ne peut {what}."


EXAMPLE_NAMES = {
    "room": "Cuisine",
    "player": "Enceinte de la cuisine",
    "other_player": "Enceinte du salon",
    "timers": ["pizza", "thé", "lasagnes"],
}


def join(parts: list[str]) -> str:
    """"a", "a et b", "a, b et c"."""
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " et " + parts[-1]


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


# ------------------------------------------------------------------ places

_GENDER = {
    **dict.fromkeys(
        ["cuisine", "chambre", "salle", "entrée", "terrasse", "cave", "buanderie", "véranda", "bibliothèque", "pièce",
         "maison", "cour", "piscine", "mezzanine", "cuisinette", "lingerie", "suite", "dépendance", "cabane", "serre",
         "remise", "loggia", "galerie", "chaufferie", "allée", "chambrette"], "f"),
    **dict.fromkeys(
        ["salon", "séjour", "bureau", "couloir", "garage", "jardin", "balcon", "grenier", "cellier", "sous-sol",
         "rez-de-chaussée", "étage", "dressing", "atelier", "studio", "palier", "vestibule", "hall", "débarras",
         "patio", "abri", "cabinet", "loft", "appartement", "living", "porche", "escalier", "coin"], "m"),
    **dict.fromkeys(["toilettes", "wc", "combles", "escaliers"], "p"),
}
"""Rooms and floors by gender (masculine, feminine, plural), by noun."""

_BARE = {"en haut": "en haut", "en bas": "en bas", "dehors": "dehors", "à l'extérieur": "à l'extérieur"}

_VOWELS = "aeiouyhàâäéèêëîïôöùûü"


def place(places: list[tuple[str, str]], to: bool = False) -> str:
    """Where a command acts: "dans la cuisine", "dans l'entrée", "au premier
    étage", "en haut"; ``to``, where something goes: the same ("dans la
    cuisine", "au premier étage")."""
    return " et ".join(_place(kind, name) for kind, name in places)


def _gender(name: str) -> str:
    words = [w.strip("'") for w in name.lower().replace("’", "'").split()]
    for word in words:
        for noun, gender in _GENDER.items():
            if word == noun or word.endswith("'" + noun):
                return gender
    return "m"


def _place(kind: str, name: str) -> str:
    lower = name.lower()
    if lower in _BARE:
        return _BARE[lower]
    gender = _gender(name)
    if gender == "p":
        article = "aux" if kind == "floor" else "les"
    elif lower[:1] in _VOWELS:
        article = "à l'" if kind == "floor" else "l'"
    elif gender == "f":
        article = "à la" if kind == "floor" else "la"
    else:
        article = "au" if kind == "floor" else "le"
    said = f"{article}{name}" if article.endswith("'") else f"{article} {name}"
    return said if kind == "floor" else f"dans {said}"


# ------------------------------------------------------------------- hints


def hint(description: str, why: str) -> str:
    """The description is the command's own, as what one wants to do:
    "allumer la lumière"."""
    return f"On dirait que vous voulez {description}, {why}."


NOT_HERE = "mais ce n'est pas configuré ici"
PARTLY = "mais je n'ai pas tout compris"


def no_room(device: str) -> str:
    return f"mais je ne sais pas dans quelle pièce se trouve {device}"


def not_in(device: str, room: str) -> str:
    return f"mais {device} n'est pas {_place('area', room)}"


_WHICH = {"entity": "quel appareil", "area": "quelle pièce", "floor": "quel étage"}
_SLOTS = {
    "color": "quelle couleur", "percentage": "quel pourcentage", "temperature": "quelle température",
    "volume": "quel volume", "item": "quoi", "reminder": "de quoi", "query": "quoi", "timer": "quel minuteur",
    "time": "à quelle heure", "number": "quel nombre",
}


def _not_heard(slot: str) -> str:
    which = _SLOTS.get(slot)
    return f"mais je n'ai pas entendu {which}" if which else "mais je n'ai pas tout entendu"


def missing(first: dict[str, Any], duration: bool = False, number_slot: str = "number") -> str:
    kind = first.get("kind")
    if kind == "require":
        if first.get("slot"):
            return _not_heard(first["slot"])
        return f"mais je n'ai pas entendu « {first['phrases'][0]} »"
    if kind == "name":
        return f"mais je n'ai pas entendu {_WHICH.get(first['type'], 'lequel')}"
    if kind == "number":
        if first.get("heard"):
            return f"mais {first['heard'][0]}, ce n'est pas possible"
        if duration:
            return "mais je n'ai pas entendu combien de temps"
        return _not_heard(number_slot)
    if kind == "text":
        return _not_heard(first["slot"])
    if kind == "unexplained":
        words = " ".join(first.get("words") or [])
        return f"mais je n'ai pas compris « {words} »" if words else PARTLY
    if kind == "unknown_name":
        return f"mais je ne connais pas « {' '.join(first.get('words') or [])} »"
    if kind == "place":
        what = {"floor": "aucun étage", "entity": "aucun appareil"}.get(str(first.get("type")), "aucune pièce")
        return f"mais je ne peux choisir {what} pour ça"
    return "mais je n'en étais pas sûr"


# ------------------------------------------------------------------ timers

_UNITS = (("heure", "heures"), ("minute", "minutes"), ("seconde", "secondes"))


def _parts(seconds: float) -> list[tuple[int, int]]:
    total = round(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    parts = [(value, unit) for unit, value in enumerate((hours, minutes, secs)) if value] or [(0, 2)]
    return parts[:2] if hours else parts


def duration(seconds: float, adjective: bool = False) -> str:
    """"une heure et 5 minutes"; as an adjective, the same (French says "de
    5 minutes")."""
    return join([f"une {_UNITS[unit][0]}" if value == 1 else f"{value} {_UNITS[unit][1]}" for value, unit in _parts(seconds)])


def clock(hour: int, minute: int) -> str:
    """"7 heures 30", "19 heures", "midi", "minuit"."""
    if (hour, minute) == (12, 0):
        return "midi"
    if (hour, minute) == (0, 0):
        return "minuit"
    said = f"{hour} heure" + ("" if hour == 1 else "s")
    return f"{said} {minute}" if minute else said


def _named(timer: dict[str, Any]) -> str:
    """Without its article: "minuteur pizza", "minuteur de 5 minutes"."""
    name = str(timer.get("name") or "")
    if name:
        return f"minuteur {name}"
    return f"minuteur de {duration(float(timer.get('created_duration') or 0))}"


def _timer(timer: dict[str, Any], before: str = "le") -> str:
    """With its article -- "le", or after "à" ("au") or "de" ("du")."""
    return f"{before} {_named(timer)}"


def _left(timer: dict[str, Any]) -> str:
    return duration(float(timer.get("remaining") or 0))


def _running(timers: list[dict[str, Any]]) -> str:
    return f"Il y a {join([_timer(t) for t in timers])}."


TIMER_HOW_LONG = "Pour combien de temps ?"
TIMER_HOW_MUCH = "Combien de temps ?"
ALARM_WHEN = "Pour quelle heure dois-je régler le réveil ?"
NO_TIMERS = "Il n'y a aucun minuteur."


def timer_started(timer: dict[str, Any], ends: tuple[int, int]) -> dict[str, str]:
    specific = f"{_capital(_named(timer))} lancé."
    return {"brief": "Minuteur lancé.", "specific": specific, "verbose": f"{specific[:-1]}, il sonnera à {clock(*ends)}."}


def alarm_set(at: tuple[int, int], seconds: float) -> dict[str, str]:
    specific = f"Réveil réglé pour {clock(*at)}."
    return {"brief": "Réveil réglé.", "specific": specific, "verbose": f"{specific[:-1]}, dans {duration(seconds)}."}


def timers_cancelled(timers: list[dict[str, Any]]) -> dict[str, str]:
    count = len(timers)
    return {
        "brief": "Minuteur annulé." if count == 1 else "Minuteurs annulés.",
        "specific": "Minuteur annulé." if count == 1 else f"{count} minuteurs annulés.",
        "verbose": f"J'ai annulé {join([_timer(t) for t in timers])}.",
    }


def timer_not_found(asked: str | float | None, running: list[dict[str, Any]]) -> str:
    there = _running(running)
    if asked is None:
        return there
    if isinstance(asked, (int, float)):
        name = f"minuteur de {duration(asked)}"
    else:
        name = asked if asked.lower().startswith("minuteur") else _named({"name": asked})
    return f"Il n'y a pas de {name}. {there}"


def which_timer(timers: list[dict[str, Any]]) -> str:
    return f"Quel minuteur ? {_running(timers)}"


def timer_status(timers: list[dict[str, Any]]) -> str:
    return " ".join(
        _capital(f"{_timer(t)} est en pause, il reste {_left(t)}.")
        if t.get("status") == "paused"
        else f"Il reste {_left(t)} sur {_timer(t)}."
        for t in timers
    )


def timer_already(operation: str, timers: list[dict[str, Any]]) -> str:
    if len(timers) == 1:
        state = "est déjà en pause" if operation == "pause" else "tourne déjà"
        return _capital(f"{_timer(timers[0])} {state}.")
    return "Tous les minuteurs sont déjà en pause." if operation == "pause" else "Tous les minuteurs tournent déjà."


def timer_changed(operation: str, timer: dict[str, Any], seconds: float, left: float) -> dict[str, str]:
    added = operation == "add"
    specific = (f"J'ai ajouté {duration(seconds)} {_timer(timer, 'au')}." if added
                else f"J'ai retiré {duration(seconds)} {_timer(timer, 'du')}.")
    return {
        "brief": "Temps ajouté." if added else "Temps retiré.",
        "specific": specific,
        "verbose": f"{specific[:-1]}, il reste {duration(left)}.",
    }


def timer_done(operation: str, timer: dict[str, Any]) -> dict[str, str]:
    done = {"cancel": "annulé", "pause": "mis en pause", "resume": "relancé"}[operation]
    specific = f"{_capital(_named(timer))} {done}."
    left = _left(timer)
    verbose = {
        "cancel": f"{specific[:-1]}, il restait {left}.",
        "pause": f"{specific[:-1]}, il reste {left}.",
        "resume": f"{specific[:-1]}, il reste {left}.",
    }[operation]
    return {"brief": f"Minuteur {done}.", "specific": specific, "verbose": verbose}


def timers_ringing(timers: list[dict[str, Any]]) -> str:
    return " ".join(_capital(f"{_timer(t)} est terminé.") for t in timers)


# ------------------------------------------------------------------- music

NO_PLAYER = "Il n'y a pas de lecteur de musique ici."
NOTHING_PLAYING = "Rien n'est en cours de lecture."
MUSIC_SAID = {
    "no_player": NO_PLAYER,
    "nothing_playing": NOTHING_PLAYING,
    "missing_query": "Qu'est-ce que je dois jouer ?",
    "missing_volume": "Quel volume ?",
    "already": "C'est déjà en cours de lecture.",
}
MOVE_SAID = {
    "nothing_playing": "Rien n'est en cours de lecture à déplacer.",
    "no_player": "Il n'y a pas de lecteur de musique là-bas.",
    "already": "La musique joue déjà là-bas.",
}
NOT_KNOWN_PLAYING = "Je ne sais pas ce qui joue."


def media(title: str, artist: str | None) -> str:
    return f"{title} de {artist}" if artist else title


def item(name: str, media_type: str, artist: str | None) -> str:
    if media_type == "playlist":
        return name if "playlist" in name.lower() else f"la playlist {name}"
    if artist and media_type in ("track", "album"):
        return f"{name} de {artist}"
    return name


def not_found(query: str) -> str:
    return f"Je n'ai pas trouvé {query}."


def now_playing(what: str, paused: bool) -> str:
    return f"{_capital(what)} est en pause." if paused else f"C'est {what}."


_CONTROL_SAID = {
    "pause": "En pause.",
    "resume": "Reprise.",
    "stop": "Arrêté.",
    "next": "Morceau suivant.",
    "previous": "Morceau précédent.",
    "volume_up": "Plus fort.",
    "volume_down": "Moins fort.",
    "volume_set": "Volume réglé.",
    "mute": "Son coupé.",
    "unmute": "Son rétabli.",
    "shuffle_on": "Lecture aléatoire activée.",
    "shuffle_off": "Lecture aléatoire désactivée.",
    "repeat_all": "Tout est répété.",
    "repeat_one": "Ce morceau est répété.",
    "repeat_off": "Répétition désactivée.",
}
_DONE = {"pause": "en pause", "resume": "reprend", "stop": "arrêté"}
_FOUND_SAID = {"play": "Je joue de la musique.", "add": "Ajouté à la file d'attente.", "radio": "Je joue de la musique similaire."}


def control(operation: str, volume: float | None, what: str | None, players: list[str]) -> dict[str, str]:
    brief = _CONTROL_SAID.get(operation, "C'est fait.")
    specific = brief
    if volume is not None:
        specific = f"Volume à {round(volume * 100)} pour cent."
    elif operation in _DONE and what:
        specific = f"{_capital(what)} {_DONE[operation]}."
    return {"brief": brief, "specific": specific, "verbose": _on_players(specific, players)}


def found(operation: str, what: str, players: list[str]) -> dict[str, str]:
    specific = {"add": f"{_capital(what)} ajouté à la file d'attente.", "radio": f"Je joue de la musique comme {what}."}.get(
        operation, f"Je joue {what}."
    )
    return {"brief": _FOUND_SAID.get(operation, "Je joue."), "specific": specific, "verbose": _on_players(specific, players)}


def moved(what: str | None, source: str, target: str, to: str) -> dict[str, str]:
    return {
        "brief": "Musique déplacée.",
        "specific": f"Musique déplacée {to}." if to else f"Musique déplacée sur {target}.",
        "verbose": f"{_capital(what or 'la musique')} déplacée de {source} vers {target}.",
    }


def _on_players(sentence: str, players: list[str]) -> str:
    if not players:
        return sentence
    return f"{sentence[:-1]}, sur {join(players)}."
