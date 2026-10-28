"""What the app says in Spanish: every sentence it makes itself, with English's
names (see voice_call/languages/en/speech.py for what each is).

A place needs its preposition and its article, by its gender and number:
"en la cocina", "en el salón", "en la primera planta"; "a la cocina", "al
salón" where something goes. A room's gender is its head noun's -- the first
of its words a room is named by ("Cuarto de baño" is a cuarto, "Primera
planta" a planta) -- from the commonest, else by its ending. Numbers a voice
would read in the wrong gender are written out ("una hora", "veintiún
minutos", "la una"); a time of day is on a 24-hour clock: "las 7 y 30".
Confirmations are short: "Temporizador de pizza iniciado"."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

# --------------------------------------------------------------- anywhere

SORRY = "Lo siento, no lo he entendido."
FAILED = "Lo siento, no ha funcionado."
NO_SPEAKER = "Lo siento, no sé quién habla."

def not_on_list(items: list[str]) -> str:
    """A to-do list command's items that aren't on the list (``on_list``)."""
    return f"{join(items)} {'no está' if len(items) == 1 else 'no están'} en la lista."


def already_on_list(items: list[str]) -> str:
    """What adding to a to-do list found on it already, not checked off
    (``adds_to_list``)."""
    return f"{join(items)} {'ya está' if len(items) == 1 else 'ya están'} en la lista."


NOT_SET = "Eso aún no está configurado."
WENT_WRONG = "Lo siento, algo ha salido mal."
NO_COMMANDS = "Todavía no hay comandos de voz configurados. Añade algunos en la app Voice Call."
TIMERS_NEED_INTEGRATION = "Los temporizadores necesitan la integración Voice Call en Home Assistant."

NO_AREA = 'No sé en qué área estoy. Asigna un área a este satélite.'
"""A command for the satellite's area, on a satellite in none."""


def nothing_there(place: str) -> str:
    """An intent found nothing of the kind ``place`` ("in the Kitchen"; "" for a device by name)."""
    return f"No encontré nada así {place}." if place else "No lo encontré."


def not_exposed(place: str) -> str:
    """What it found there isn't exposed to Assist."""
    return f"Nada de eso {place} está expuesto a Assist." if place else "Eso no está expuesto a Assist."


def unavailable(place: str) -> str:
    """What it found there is all unavailable."""
    return f"Todo eso {place} no está disponible ahora." if place else "Eso no está disponible ahora."


# What a device can't do of what a command needs (its ``supports``), by
# capability; None for what Home Assistant refused without saying.
_CAN = {
    "brightness": "cambiar el brillo",
    "color": "cambiar de color",
    "color_temp": "cambiar la temperatura de color",
    "position": "ponerse en una posición",
    "tilt": "inclinarse",
    "temperature": "ponerse a una temperatura",
    "fan_mode": "ajustar un modo de ventilador",
    "hvac_mode": "cambiar de modo",
    "volume_set": "ponerse a un volumen",
    None: "hacer eso",
}


def cannot(names: list[str], capability: str | None, value: str | None = None) -> str:
    """Devices that can't: "Puerta del garaje no puede ponerse en una
    posición."; that have no ``value`` (a mode)."""
    if value is not None:
        what = f"poner el ventilador en {value}" if capability == "fan_mode" else f"ponerse en {value}"
    else:
        what = _CAN.get(capability, _CAN[None])
    return f"{join(names)} {'no puede' if len(names) == 1 else 'no pueden'} {what}."


def none_can(place: str, capability: str | None) -> str:
    """Nothing of the kind ``place`` can: "Nada en la cocina puede cambiar de color."."""
    what = _CAN.get(capability, _CAN[None])
    return f"Nada {place} puede {what}." if place else f"Nada puede {what}."


# ------------------------------------------------------- other satellites


def announced(place: str) -> dict[str, str]:
    """An announcement, said on the other satellites: every one, or those
    ``place`` ("in the Kitchen", "upstairs")."""
    return {"brief": "Anunciado.", "specific": f"Anunciado {place}." if place else "Anunciado en todas partes."}


def no_satellites(place: str) -> str:
    """No other satellite ``place`` ("in the Kitchen"; "" for anywhere)."""
    return f"No hay otro satélite {place}." if place else "No hay otros satélites."


def connecting(to: str) -> str:
    """The intercom, about to connect ``to`` there (``place(to=True)``)."""
    return f"Conectando {to}."


def intercom_busy(place: str) -> str:
    """The satellite there is in a call, ringing or connected."""
    return f"El satélite {place} está ocupado ahora."


INTERCOM_FIRMWARE = "El intercomunicador necesita un firmware más reciente en ambos satélites."
"""Either satellite's firmware can't connect the intercom."""

INTERCOM_HERE = "Ahí es donde estás."
"""The intercom asked for the satellite's own area, with none other there."""


EXAMPLE_NAMES = {
    "room": "Cocina",
    "player": "Altavoz de la cocina",
    "other_player": "Altavoz del salón",
    "timers": ["pizza", "té", "lasaña"],
}


def _fold(text: str) -> str:
    """Lowercase, without accents: "Salón" -> "salon"."""
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")


def _and(following: str) -> str:
    """"y", or "e" before an "i" sound: "Ana e Isabel" (not "y hielo")."""
    return "e" if re.match(r"h?i(?![aeou])", _fold(following)) else "y"


def join(parts: list[str]) -> str:
    """"a", "a y b", "a, b y c"; "e" before an "i" sound."""
    if len(parts) <= 1:
        return "".join(parts)
    return f"{', '.join(parts[:-1])} {_and(parts[-1])} {parts[-1]}"


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


# ------------------------------------------------------------------ places

_GENDER = {
    # Feminine: en la, a la.
    "cocina": "f", "habitación": "f", "oficina": "f", "terraza": "f", "entrada": "f", "planta": "f", "sala": "f",
    "despensa": "f", "bodega": "f", "buhardilla": "f", "galería": "f", "biblioteca": "f", "lavandería": "f",
    "piscina": "f", "casa": "f", "caseta": "f", "escalera": "f", "alcoba": "f", "recámara": "f", "cochera": "f",
    "azotea": "f", "zona": "f", "suite": "f", "sauna": "f", "leñera": "f", "salita": "f",
    "nave": "f", "parcela": "f", "finca": "f", "huerta": "f", "veranda": "f", "barbacoa": "f", "cabaña": "f",
    # Masculine: en el, al.
    "salón": "m", "dormitorio": "m", "baño": "m", "cuarto": "m", "despacho": "m", "comedor": "m",
    "pasillo": "m", "garaje": "m", "jardín": "m", "balcón": "m", "sótano": "m", "ático": "m", "recibidor": "m",
    "estudio": "m", "lavadero": "m", "piso": "m", "patio": "m", "vestidor": "m", "trastero": "m", "aseo": "m",
    "hall": "m", "porche": "m", "gimnasio": "m", "taller": "m", "desván": "m", "vestíbulo": "m", "living": "m",
    "almacén": "m", "cobertizo": "m", "invernadero": "m", "huerto": "m", "corredor": "m", "distribuidor": "m",
    "loft": "m", "apartamento": "m", "nivel": "m", "semisótano": "m", "bajo": "m", "tejado": "m", "cuartito": "m",
}
"""Rooms and floors by gender, by their head noun."""

_LEXICON = {_fold(word): gender for word, gender in _GENDER.items()}

_ADJECTIVES = {_fold(word) for word in (
    "primer", "primera", "primero", "segundo", "segunda", "tercer", "tercera", "tercero", "cuarto", "cuarta",
    "quinto", "quinta", "gran", "grande", "pequeño", "pequeña", "nuevo", "nueva", "viejo", "vieja", "antiguo",
    "antigua", "último", "última", "mini", "mi", "tu", "su", "nuestro", "nuestra",
)}
"""Words before a room's head noun ("Primera planta", "Pequeño salón"):
written lowercase in a sentence."""

_AFTER = {_fold(word) for word in (
    "baja", "bajo", "alta", "alto", "principal", "superior", "inferior", "exterior", "interior", "trasero", "trasera",
    "delantero", "delantera", "grande", "pequeño", "pequeña", "de", "del", "la", "el", "los", "las", "y", "estar",
    "invitados", "matrimonio", "niños", "servicio",
)}
"""Words after a room's head noun written lowercase too: "Planta Baja", "Sala
de Estar". A person's name stays as written ("Dormitorio de Ana")."""

_OWN = {"mi", "tu", "su", "nuestro", "nuestra", "mis", "tus", "sus"}
"""A room named with whose it is ("Mi habitación") takes no article."""

_BARE = {"arriba": "arriba", "abajo": "abajo", "fuera": "afuera", "afuera": "afuera", "dentro": "adentro",
         "adentro": "adentro"}
"""Places said as they are ("arriba"), and where to ("afuera")."""

_ARTICLES = {"el": ("m", False), "la": ("f", False), "los": ("m", True), "las": ("f", True)}


def place(places: list[tuple[str, str]], to: bool = False) -> str:
    """Where a command acts: "en la cocina", "en el salón", "en la primera
    planta", "arriba"; ``to``, where something goes: "a la cocina", "al
    salón", "arriba"."""
    return join([_place(name, to) for _kind, name in places])


def _place(name: str, to: bool) -> str:
    lower = name.strip().lower()
    if lower in _BARE:
        return _BARE[lower] if to else lower
    words = name.split()
    preposition = "a" if to else "en"
    if words and words[0].lower() in _OWN:
        return f"{preposition} {' '.join(w.lower() if _common(w) else w for w in words)}"
    if words and words[0].lower() in _ARTICLES and len(words) > 1:
        gender, plural = _ARTICLES[words[0].lower()]
        words = words[1:]
    else:
        gender, plural = _gender(words)
    article = {("m", False): "el", ("f", False): "la", ("m", True): "los", ("f", True): "las"}[(gender, plural)]
    said = " ".join(w.lower() if _common(w) else w for w in words)
    if to and article == "el":
        return f"al {said}"
    return f"{preposition} {article} {said}"


def _common(word: str) -> bool:
    """A word of a room's name written lowercase in a sentence: a room
    ("Cocina": "en la cocina"), an adjective before or after one, "de"; not
    a person's name ("Dormitorio de Ana")."""
    folded = _fold(word)
    return folded in _ADJECTIVES or folded in _AFTER or _known(folded) is not None


def _gender(words: list[str]) -> tuple[str, bool]:
    """A name's gender and whether it is plural, by its head noun: the
    first word a room is named by -- not an adjective before another
    ("Cuarto piso") -- else the first word that isn't an adjective, by its
    ending."""
    folded = [_fold(w) for w in words]
    for index, word in enumerate(folded):
        following = folded[index + 1] if index + 1 < len(folded) else ""
        if word in _ADJECTIVES and (_known(following) or word not in _LEXICON):
            continue
        known = _known(word)
        if known:
            return known
        if word not in _ADJECTIVES:
            return _by_ending(word)
    return _by_ending(folded[-1] if folded else "")


def _known(word: str) -> tuple[str, bool] | None:
    """A room in the lexicon, or its plural ("escaleras", "dormitorios")."""
    if word in _LEXICON:
        return _LEXICON[word], False
    for ending in ("es", "s"):
        if word.endswith(ending) and word[: -len(ending)] in _LEXICON:
            return _LEXICON[word[: -len(ending)]], True
    return None


def _by_ending(word: str) -> tuple[str, bool]:
    """Feminine for -a, -ción, -dad and their kin; plural for -as, -os."""
    if word.endswith("as"):
        return "f", True
    if word.endswith("os"):
        return "m", True
    feminine = word.endswith(("a", "cion", "sion", "dad", "tad", "tud", "umbre"))
    return ("f" if feminine else "m"), False


# ------------------------------------------------------------------- hints


def hint(description: str, why: str) -> str:
    """The description is the command's own, as what one wants to do:
    "encender la luz"."""
    return f"Parece que quieres {description}, {why}."


NOT_HERE = "pero eso no está configurado aquí"
PARTLY = "pero no lo he entendido todo"


def no_room(device: str) -> str:
    return f"pero no sé en qué habitación está {device}"


def not_in(device: str, room: str) -> str:
    return f"pero {device} no está {_place(room, False)}"


def not_here(device: str, rooms: list[str]) -> str:
    """Why: the device said, by a name several share or one heard only in
    its room, isn't in the satellite's, and no room was said: where it is."""
    where = place([("area", room) for room in rooms])
    return f"pero {device} no está aquí" + (", solo " + where if rooms else "")


_WHICH = {"entity": "qué dispositivo", "area": "qué habitación", "floor": "qué planta", "scene": "qué escena", "script": "qué script"}
_SLOTS = {
    "color": "qué color", "percentage": "qué porcentaje", "temperature": "qué temperatura", "volume": "qué volumen",
    "item": "qué", "reminder": "qué recordarte", "query": "qué poner", "timer": "qué temporizador",
    "time": "a qué hora", "number": "qué número",
}
"""What a slot asks, by its name in the library: "qué color"."""


def _not_heard(slot: str) -> str:
    which = _SLOTS.get(slot)
    return f"pero no he oído {which}" if which else "pero no lo he oído todo"


def missing(first: dict[str, Any], duration: bool = False, number_slot: str = "number") -> str:
    kind = first.get("kind")
    if kind == "require":
        if first.get("slot"):
            return _not_heard(first["slot"])
        return f"pero no he oído «{first['phrases'][0]}»"
    if kind == "name":
        return f"pero no he oído {_WHICH.get(first['type'], 'cuál')}"
    if kind == "number":
        if first.get("heard"):
            return f"pero {first['heard'][0]} está fuera de rango"
        if duration:
            return "pero no he oído cuánto tiempo"
        return _not_heard(number_slot)
    if kind == "text":
        return _not_heard(first["slot"])
    if kind == "unexplained":
        words = " ".join(first.get("words") or [])
        return f"pero no he entendido «{words}»" if words else PARTLY
    if kind == "unknown_name":
        return f"pero no conozco «{' '.join(first.get('words') or [])}»"
    if kind == "place":
        what = {"floor": "ninguna planta", "entity": "ningún dispositivo"}.get(str(first.get("type")), "ninguna habitación")
        return f"pero no puedo elegir {what} para eso"
    return "pero no lo tenía claro"


# ------------------------------------------------------------------ timers

_UNITS = (("hora", "horas", True), ("minuto", "minutos", False), ("segundo", "segundos", False))
"""Each unit, its plural, and whether it is feminine."""

_TENS = {20: "veinti", 30: "treinta y ", 40: "cuarenta y ", 50: "cincuenta y ", 60: "sesenta y ", 70: "setenta y ",
         80: "ochenta y ", 90: "noventa y "}


def _count(value: int, feminine: bool) -> str:
    """A count before its noun, as a voice should read it: "un", "una",
    "veintiún", "treinta y una"; others in digits ("5")."""
    one = "una" if feminine else "un"
    if value == 1:
        return one
    if 20 < value < 100 and value % 10 == 1:
        return _TENS[value - 1] + ("una" if feminine else "ún" if value == 21 else "un")
    return str(value)


def _parts(seconds: float) -> list[tuple[int, int]]:
    """(value, unit index) to say, largest first."""
    total = round(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    parts = [(value, unit) for unit, value in enumerate((hours, minutes, secs)) if value] or [(0, 2)]
    return parts[:2] if hours else parts  # an hour and some: seconds don't matter


def duration(seconds: float, adjective: bool = False) -> str:
    """"una hora y 5 minutos", "un minuto", "veintiún segundos". Spanish
    has no adjective of it: a timer is "de 5 minutos", so ``adjective``
    changes nothing."""
    said = []
    for value, unit in _parts(seconds):
        single, plural, feminine = _UNITS[unit]
        said.append(f"{_count(value, feminine)} {single if value == 1 else plural}")
    return join(said)


def clock(hour: int, minute: int) -> str:
    """"las 7 y 30", "las 19", "la una y 5", "medianoche"."""
    if (hour, minute) == (0, 0):
        return "medianoche"
    said = {1: "la una", 21: "las veintiuna"}.get(hour, f"las {hour}")
    return f"{said} y {minute}" if minute else said


def _left_verb(left: str) -> str:
    """"queda un minuto", "quedan 5 minutos"."""
    return "queda" if left.startswith(("un ", "una ")) else "quedan"


def _named(timer: dict[str, Any]) -> tuple[str, bool]:
    """Without its article, and whether it is feminine: "temporizador de
    pizza", "temporizador de 5 minutos"; an alarm is "alarma"."""
    name = re.sub(r"^(?:el|la|los|las)\s+", "", str(timer.get("name") or "").strip(), flags=re.IGNORECASE)
    if _fold(name) in ("alarm", "alarma"):
        return "alarma", True
    if name and _fold(name).startswith(("temporizador", "alarma")):
        return name[:1].lower() + name[1:], _fold(name).startswith("alarma")
    if name:
        return f"temporizador de {name}", False
    return f"temporizador de {duration(float(timer.get('created_duration') or 0))}", False


def _timer(timer: dict[str, Any]) -> str:
    """With its article: "el temporizador de pizza", "la alarma"."""
    named, feminine = _named(timer)
    return f"{'la' if feminine else 'el'} {named}"


def _done(word: str, timer: dict[str, Any]) -> str:
    """A participle agreeing with it: "cancelado", "cancelada"."""
    return word[:-1] + ("a" if _named(timer)[1] else "o")


def _left(timer: dict[str, Any]) -> str:
    return duration(float(timer.get("remaining") or 0))


def _running(timers: list[dict[str, Any]]) -> str:
    listed = join([_timer(t) for t in timers])
    return f"Está en marcha {listed}." if len(timers) == 1 else f"Están en marcha {listed}."


TIMER_HOW_LONG = "¿Para cuánto tiempo pongo el temporizador?"
TIMER_HOW_MUCH = "¿Cuánto tiempo?"
ALARM_WHEN = "¿Para qué hora pongo la alarma?"
NO_TIMERS = "No hay ningún temporizador."


def timer_started(timer: dict[str, Any], ends: tuple[int, int]) -> dict[str, str]:
    specific = f"{_capital(_named(timer)[0])} {_done('iniciado', timer)}."
    return {"brief": "Temporizador iniciado.", "specific": specific, "verbose": f"{specific[:-1]}; sonará a {clock(*ends)}."}


def alarm_set(at: tuple[int, int], seconds: float) -> dict[str, str]:
    specific = f"Alarma puesta para {clock(*at)}."
    return {"brief": "Alarma puesta.", "specific": specific, "verbose": f"{specific[:-1]}, dentro de {duration(seconds)}."}


def timers_cancelled(timers: list[dict[str, Any]]) -> dict[str, str]:
    count = len(timers)
    return {
        "brief": "Temporizador cancelado." if count == 1 else "Temporizadores cancelados.",
        "specific": "Temporizador cancelado." if count == 1 else f"{count} temporizadores cancelados.",
        "verbose": f"He cancelado {join([_timer(t) for t in timers])}.",
    }


def timer_not_found(asked: str | float | None, running: list[dict[str, Any]]) -> str:
    there = _running(running)
    if asked is None:
        return there
    if isinstance(asked, (int, float)):
        name = f"temporizador de {duration(asked)}"
    else:
        name = _named({"name": asked})[0]
    return f"No hay ningún {name}. {there}" if not name.startswith("alarma") else f"No hay ninguna {name}. {there}"


def which_timer(timers: list[dict[str, Any]]) -> str:
    return f"¿Qué temporizador? {_running(timers)}"


def timer_status(timers: list[dict[str, Any]]) -> str:
    said = []
    for t in timers:
        left = _left(t)
        if t.get("status") == "paused":
            said.append(_capital(f"{_timer(t)} está en pausa; le {_left_verb(left)} {left}."))
        else:
            said.append(_capital(f"{_to(t)} le {_left_verb(left)} {left}."))
    return " ".join(said)


def timer_already(operation: str, timers: list[dict[str, Any]]) -> str:
    if len(timers) == 1:
        state = "ya está en pausa" if operation == "pause" else "ya está en marcha"
        return _capital(f"{_timer(timers[0])} {state}.")
    return "Todos los temporizadores ya están en pausa." if operation == "pause" else "Todos los temporizadores ya están en marcha."


def _to(timer: dict[str, Any]) -> str:
    """"al temporizador de pizza", "a la alarma"."""
    said = _timer(timer)
    return f"al {said[3:]}" if said.startswith("el ") else f"a {said}"


def timer_changed(operation: str, timer: dict[str, Any], seconds: float, left: float) -> dict[str, str]:
    added = operation == "add"
    specific = f"He {'añadido' if added else 'quitado'} {duration(seconds)} {_to(timer)}."
    remaining = duration(left)
    return {
        "brief": "Tiempo añadido." if added else "Tiempo quitado.",
        "specific": specific,
        "verbose": f"{specific[:-1]}; {_left_verb(remaining)} {remaining}.",
    }


def timer_done(operation: str, timer: dict[str, Any]) -> dict[str, str]:
    done = {"cancel": "cancelado", "pause": "pausado", "resume": "reanudado"}[operation]
    specific = f"{_capital(_named(timer)[0])} {_done(done, timer)}."
    left = _left(timer)
    verbose = {
        "cancel": f"{specific[:-1]}; le {'quedaba' if _left_verb(left) == 'queda' else 'quedaban'} {left}.",
        "pause": f"{specific[:-1]}; le {_left_verb(left)} {left}.",
        "resume": f"{specific[:-1]}; {_left_verb(left)} {left}.",
    }[operation]
    return {"brief": f"Temporizador {done}.", "specific": specific, "verbose": verbose}


def timers_ringing(timers: list[dict[str, Any]]) -> str:
    return " ".join(_capital(f"{_timer(t)} ha terminado.") for t in timers)


# ------------------------------------------------------------------- music

NO_PLAYER = "Aquí no hay ningún reproductor de música."
NOTHING_PLAYING = "No está sonando nada."
MUSIC_SAID = {
    "no_player": NO_PLAYER,
    "nothing_playing": NOTHING_PLAYING,
    "missing_query": "¿Qué quieres que ponga?",
    "missing_volume": "¿Qué volumen?",
    "already": "Ya está sonando.",
}
MOVE_SAID = {
    "nothing_playing": "No está sonando nada que pueda mover.",
    "no_player": "Allí no hay ningún reproductor de música.",
    "already": "La música ya suena allí.",
}
NOT_KNOWN_PLAYING = "No sé qué está sonando."


def media(title: str, artist: str | None) -> str:
    return f"{title} de {artist}" if artist else title


def item(name: str, media_type: str, artist: str | None) -> str:
    if media_type == "playlist":
        return name if "lista" in name.lower() or "playlist" in name.lower() else f"la lista {name}"
    if artist and media_type in ("track", "album"):
        return f"{name} de {artist}"
    return name


def not_found(query: str) -> str:
    return f"No he encontrado {query}."


def now_playing(what: str, paused: bool) -> str:
    return f"{_capital(what)} está en pausa." if paused else f"Está sonando {what}."


_CONTROL_SAID = {
    "pause": "En pausa.",
    "resume": "Reanudado.",
    "stop": "Detenido.",
    "next": "Siguiente canción.",
    "previous": "Canción anterior.",
    "volume_up": "Más alto.",
    "volume_down": "Más bajo.",
    "volume_set": "Volumen ajustado.",
    "mute": "Silenciado.",
    "unmute": "Sonido activado.",
    "shuffle_on": "Aleatorio activado.",
    "shuffle_off": "Aleatorio desactivado.",
    "repeat_all": "Repitiendo todo.",
    "repeat_one": "Repitiendo esta canción.",
    "repeat_off": "Repetición desactivada.",
}
_DONE = {"pause": "He pausado", "resume": "He reanudado", "stop": "He detenido"}
_FOUND_SAID = {"play": "Reproduciendo música.", "add": "Añadido a la cola.", "radio": "Reproduciendo música parecida."}


def control(operation: str, volume: float | None, what: str | None, players: list[str]) -> dict[str, str]:
    brief = _CONTROL_SAID.get(operation, "Hecho.")
    specific = brief
    if volume is not None:
        specific = f"Volumen al {round(volume * 100)} por ciento."
    elif operation in _DONE and what:
        specific = f"{_DONE[operation]} {what}."
    return {"brief": brief, "specific": specific, "verbose": _on_players(specific, players)}


def found(operation: str, what: str, players: list[str]) -> dict[str, str]:
    specific = {"add": f"He añadido {what} a la cola.", "radio": f"Reproduciendo música como {what}."}.get(
        operation, f"Reproduciendo {what}."
    )
    return {"brief": _FOUND_SAID.get(operation, "Reproduciendo."), "specific": specific, "verbose": _on_players(specific, players)}


def moved(what: str | None, source: str, target: str, to: str) -> dict[str, str]:
    return {
        "brief": "Música movida.",
        "specific": f"He movido la música {to}." if to else f"He movido la música a {target}.",
        "verbose": f"He movido {what or 'la música'} de {source} a {target}.",
    }


def _on_players(sentence: str, players: list[str]) -> str:
    """"He pausado So What de Miles Davis, en Altavoz de la cocina."."""
    if not players:
        return sentence
    return f"{sentence[:-1]}, en {join(players)}."
