"""What the app says in Polish: every sentence it makes itself, with English's
names (see voice_labs/languages/en/speech.py for what each is).

A place is said in the case its preposition takes: "w kuchni", "w salonie",
"na pierwszym piętrze", "w pokoju dziennym"; where something goes, "do
kuchni", "na parter". A room is known by its head noun -- the first of its
words a room is named by ("Pokój dzienny" is a pokój, "Pierwsze piętro" a
piętro) -- with the adjectives around it agreeing; a name of none of them
stays as it is, after a noun that declines: "w pomieszczeniu Garderoba
Ani". A number's noun takes its form by the number ("1 minuta", "2 minuty",
"5 minut"), and numbers a voice would read in the masculine before a
feminine noun are written out ("jedna minuta", "dwie minuty"). A time of
day is on a 24-hour clock, its hour said as Polish says it: "siódma
trzydzieści", "o dziewiętnastej". The app says nothing in the past tense of
the first person, which in Polish would be a man's or a woman's
("Dodano", "Nie rozumiem"), and a timer is a masculine "minutnik" whatever
its name: "Ustawiono minutnik pizza"."""

from __future__ import annotations

import re
from typing import Any

from .skills.numbers import count, fold, ordinal, plural, spell

# --------------------------------------------------------------- anywhere

SORRY = "Przepraszam, nie rozumiem."
FAILED = "Przepraszam, to się nie udało."
NO_SPEAKER = "Przepraszam, nie wiem, kto mówi."

def not_on_list(items: list[str]) -> str:
    """A to-do list command's items that aren't on the list (``on_list``)."""
    return f"Na liście nie ma: {join(items)}."


def already_on_list(items: list[str]) -> str:
    """What adding to a to-do list found on it already, not checked off
    (``adds_to_list``)."""
    return f"Na liście już jest: {join(items)}."


NOT_SET = "To nie jest jeszcze skonfigurowane."
WENT_WRONG = "Przepraszam, coś poszło nie tak."
NO_COMMANDS = "Nie ma jeszcze żadnych poleceń głosowych. Dodaj je w aplikacji Voice Labs."
TIMERS_NEED_INTEGRATION = "Minutniki wymagają integracji Voice Labs w Home Assistant."

NO_AREA = 'Nie wiem, w jakim obszarze jestem. Przypisz temu satelicie obszar.'
"""A command for the satellite's area, on a satellite in none."""


def nothing_there(place: str) -> str:
    """An intent found nothing of the kind ``place`` ("in the Kitchen"; "" for a device by name)."""
    return f"Nie znalazłem niczego takiego {place}." if place else "Nie znalazłem tego."


def not_exposed(place: str) -> str:
    """What it found there isn't exposed to Assist."""
    return f"Nic takiego {place} nie jest udostępnione dla Assist." if place else "To nie jest udostępnione dla Assist."


def unavailable(place: str) -> str:
    """What it found there is all unavailable."""
    return f"Wszystko to {place} jest teraz niedostępne." if place else "To jest teraz niedostępne."


# What a device can't do of what a command needs (its ``supports``), by
# capability (after "nie obsługuje": the genitive); None for what Home
# Assistant refused without saying.
_SUPPORT = {
    "brightness": "zmiany jasności",
    "color": "zmiany koloru",
    "color_temp": "zmiany temperatury barwowej",
    "position": "ustawiania pozycji",
    "tilt": "pochylania",
    "temperature": "ustawiania temperatury",
    "fan_mode": "trybów wentylatora",
    "hvac_mode": "zmiany trybu",
    "volume_set": "ustawiania głośności",
    None: "tego",
}


def cannot(names: list[str], capability: str | None, value: str | None = None) -> str:
    """Devices that can't: "Brama garażowa nie obsługuje ustawiania
    pozycji."; that have no ``value`` (a mode)."""
    if value is not None:
        has = "nie ma" if len(names) == 1 else "nie mają"
        what = f"trybu wentylatora {value}" if capability == "fan_mode" else f"trybu {value}"
        return f"{join(names)} {has} {what}."
    supports = "nie obsługuje" if len(names) == 1 else "nie obsługują"
    return f"{join(names)} {supports} {_SUPPORT.get(capability, _SUPPORT[None])}."


def none_can(place: str, capability: str | None) -> str:
    """Nothing of the kind ``place`` can: "Nic w kuchni nie obsługuje zmiany koloru."."""
    what = _SUPPORT.get(capability, _SUPPORT[None])
    return f"Nic {place} nie obsługuje {what}." if place else f"Nic nie obsługuje {what}."


# ------------------------------------------------------- other satellites


def announced(place: str) -> dict[str, str]:
    """An announcement, said on the other satellites: every one, or those
    ``place`` ("in the Kitchen", "upstairs")."""
    return {"brief": "Ogłoszone.", "specific": f"Ogłoszone {place}." if place else "Ogłoszone wszędzie."}


def no_satellites(place: str) -> str:
    """No other satellite ``place`` ("in the Kitchen"; "" for anywhere)."""
    return f"Nie ma innego satelity {place}." if place else "Nie ma innych satelitów."


def connecting(to: str) -> str:
    """The intercom, about to connect ``to`` there (``place(to=True)``)."""
    return f"Łączę {to}."


def intercom_busy(place: str) -> str:
    """The satellite there is in a call, ringing or connected."""
    return f"Satelita {place} jest teraz zajęty."


INTERCOM_FIRMWARE = "Interkom wymaga nowszego firmware'u na obu satelitach."
"""Either satellite's firmware can't connect the intercom."""

INTERCOM_HERE = "Przecież tu jesteś."
"""The intercom asked for the satellite's own area, with none other there."""


EXAMPLE_NAMES = {
    "room": "Kuchnia",
    "player": "Głośnik w kuchni",
    "other_player": "Głośnik w salonie",
    "timers": ["pizza", "herbata", "lazania"],
}


def join(parts: list[str]) -> str:
    """"a", "a i b", "a, b i c"."""
    return "".join(parts) if len(parts) <= 1 else ", ".join(parts[:-1]) + " i " + parts[-1]


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


# ------------------------------------------------------------------ places

_ROOMS = {
    # Head noun: gender (m, f, n; p plural), where ("w kuchni"), where to ("do kuchni").
    "kuchnia": ("f", "w kuchni", "do kuchni"), "salon": ("m", "w salonie", "do salonu"),
    "salonik": ("m", "w saloniku", "do saloniku"), "łazienka": ("f", "w łazience", "do łazienki"),
    "sypialnia": ("f", "w sypialni", "do sypialni"), "pokój": ("m", "w pokoju", "do pokoju"),
    "pokoik": ("m", "w pokoiku", "do pokoiku"), "przedpokój": ("m", "w przedpokoju", "do przedpokoju"),
    "gabinet": ("m", "w gabinecie", "do gabinetu"), "biuro": ("n", "w biurze", "do biura"),
    "korytarz": ("m", "na korytarzu", "na korytarz"), "hol": ("m", "w holu", "do holu"),
    "hall": ("m", "w hallu", "do hallu"), "jadalnia": ("f", "w jadalni", "do jadalni"),
    "garaż": ("m", "w garażu", "do garażu"), "piwnica": ("f", "w piwnicy", "do piwnicy"),
    "strych": ("m", "na strychu", "na strych"), "poddasze": ("n", "na poddaszu", "na poddasze"),
    "ogród": ("m", "w ogrodzie", "do ogrodu"), "ogródek": ("m", "w ogródku", "do ogródka"),
    "taras": ("m", "na tarasie", "na taras"), "balkon": ("m", "na balkonie", "na balkon"),
    "weranda": ("f", "na werandzie", "na werandę"), "ganek": ("m", "na ganku", "na ganek"),
    "loggia": ("f", "na loggii", "na loggię"), "pralnia": ("f", "w pralni", "do pralni"),
    "spiżarnia": ("f", "w spiżarni", "do spiżarni"), "spiżarka": ("f", "w spiżarce", "do spiżarki"),
    "garderoba": ("f", "w garderobie", "do garderoby"), "toaleta": ("f", "w toalecie", "do toalety"),
    "kotłownia": ("f", "w kotłowni", "do kotłowni"), "warsztat": ("m", "w warsztacie", "do warsztatu"),
    "siłownia": ("f", "w siłowni", "do siłowni"), "pracownia": ("f", "w pracowni", "do pracowni"),
    "wiatrołap": ("m", "w wiatrołapie", "do wiatrołapu"), "przedsionek": ("m", "w przedsionku", "do przedsionka"),
    "schody": ("p", "na schodach", "na schody"), "klatka": ("f", "na klatce", "na klatkę"),
    "dom": ("m", "w domu", "do domu"), "domek": ("m", "w domku", "do domku"),
    "mieszkanie": ("n", "w mieszkaniu", "do mieszkania"), "pomieszczenie": ("n", "w pomieszczeniu", "do pomieszczenia"),
    "antresola": ("f", "na antresoli", "na antresolę"), "altana": ("f", "w altanie", "do altany"),
    "basen": ("m", "na basenie", "na basen"), "podwórko": ("n", "na podwórku", "na podwórko"),
    "podwórze": ("n", "na podwórzu", "na podwórze"), "podjazd": ("m", "na podjeździe", "na podjazd"),
    "sień": ("f", "w sieni", "do sieni"), "sala": ("f", "w sali", "do sali"), "studio": ("n", "w studiu", "do studia"),
    "strefa": ("f", "w strefie", "do strefy"), "kino": ("n", "w kinie", "do kina"), "sauna": ("f", "w saunie", "do sauny"),
    "szopa": ("f", "w szopie", "do szopy"), "komórka": ("f", "w komórce", "do komórki"),
    "schowek": ("m", "w schowku", "do schowka"), "suterena": ("f", "w suterenie", "do sutereny"),
    # Floors.
    "parter": ("m", "na parterze", "na parter"), "piętro": ("n", "na piętrze", "na piętro"),
    "półpiętro": ("n", "na półpiętrze", "na półpiętro"), "poziom": ("m", "na poziomie", "na poziom"),
    "kondygnacja": ("f", "na kondygnacji", "na kondygnację"), "góra": ("f", "na górze", "na górę"),
    "dół": ("m", "na dole", "na dół"),
}
"""Rooms and floors by their head noun."""

_LEXICON = {fold(noun): noun for noun in _ROOMS}

_BARE = {"na zewnątrz": "na zewnątrz", "zewnątrz": "na zewnątrz", "na dworze": "na dworze", "dwór": "na dworze",
         "na górze": "na górze", "na dole": "na dole", "w domu": "w domu"}
"""Places said as they are (where to as well): "na zewnątrz"."""

_PREPOSITIONS = ("w", "we", "na", "przy", "u", "pod", "nad", "za")

_ADJECTIVES = {fold(stem) for stem in (
    "dzienn", "gościnn", "dziecięc", "pierwsz", "drug", "trzec", "czwart", "piąt", "główn", "duż", "mał", "wielk",
    "górn", "doln", "tyln", "przedn", "now", "star", "schodow", "rodzinn", "kuchenn", "łazienkow", "zimow", "letn",
    "zachodn", "wschodn", "północn", "południow", "środkow", "boczn", "wewnętrzn", "zewnętrzn", "prywatn", "wspóln",
    "służbow", "domow", "jasn", "ciemn", "niebiesk", "zielon", "żółt", "czerwon", "biał", "szar", "różow",
    "fioletow", "ostatn", "średn", "sypialn", "nocn", "gospodarcz", "techniczn", "ogrodow",
)}
"""Stems of adjectives a room's name may have before or after its noun
("Pierwsze piętro", "Pokój dzienny"), inflected with it. Other lowercase
words with an adjective's ending (-ny, -wy, -ski) are taken for one too;
anything else ("Pokój Ani", "Sypialnia rodziców") stays as named."""

_ADJECTIVE_LIKE = re.compile(r"(?:[nwł]|sk|ck)(?:y|i|a|ia|e|ie)$")

_NOMINATIVE = {"m": ("y", "i"), "f": ("a", "ia"), "n": ("e", "ie"), "p": ("e", "ie")}
_POSSESSIVE = {"mój": "m", "moja": "f", "moje": "n"}


def place(places: list[tuple[str, str]], to: bool = False) -> str:
    """Where a command acts: "w kuchni", "w salonie", "na pierwszym
    piętrze", "na górze"; ``to``, where something goes: "do kuchni", "do
    salonu", "na pierwsze piętro"."""
    return join([_place(kind, name, to) for kind, name in places])


def _place(kind: str, name: str, to: bool) -> str:
    lower = " ".join(name.lower().split())
    if lower in _BARE:
        return _BARE[lower]
    words = name.split()
    if words and words[0].lower() in _PREPOSITIONS and len(words) > 1:
        return " ".join([words[0].lower(), *words[1:]])  # named as a place already: "Na górze"
    head = next((i for i, word in enumerate(words) if fold(word.lower()) in _LEXICON), None)
    if head is not None:
        noun = _LEXICON[fold(words[head].lower())]
        gender, where, where_to = _ROOMS[noun]
        preposition, said = (where_to if to else where).split(" ", 1)
        case = ("gen" if preposition == "do" else "acc") if to else "loc"
        # A capitalized word past the first is a name unless a known adjective: "Pokój Ani".
        before = [_agreeing(w, gender, case, known_only=i > 0 and w[:1].isupper()) for i, w in enumerate(words[:head])]
        after = [_agreeing(word, gender, case, known_only=word[:1].isupper()) for word in words[head + 1:]]
        if None not in before:
            # Once a word isn't an adjective, the rest stay as named: "Pokój dla gości".
            rest, adjectives = [], True
            for word, inflected in zip(words[head + 1:], after):
                adjectives = adjectives and inflected is not None
                rest.append(inflected if adjectives else word)
            phrase = " ".join([*before, said, *rest])
            if preposition == "w":
                preposition = _w(phrase)
            return f"{preposition} {phrase}"
    if kind == "floor":
        return f"na poziom {name}" if to else f"na poziomie {name}"
    return f"do pomieszczenia {name}" if to else f"w pomieszczeniu {name}"


def _w(following: str) -> str:
    """"we" before a w or f and a consonant ("we wnęce"), else "w"."""
    return "we" if re.match(r"[wf][^aąeęioóuy]", fold(following.lower())) else "w"


def _agreeing(word: str, gender: str, case: str, known_only: bool = False) -> str | None:
    """An adjective of a room's name agreeing with its noun in ``case``
    ("loc", "gen" after "do", "acc" after "na"), lowercase: "pierwsze" ->
    "pierwszym", "dzienny" -> "dziennego"; None when it isn't one."""
    lower = word.lower()
    if lower in _POSSESSIVE and _POSSESSIVE[lower] == gender or (gender == "p" and lower == "moje"):
        return {"loc": "moich" if gender == "p" else "mojej" if gender == "f" else "moim",
                "gen": "moich" if gender == "p" else "mojej" if gender == "f" else "mojego",
                "acc": "moją" if gender == "f" else lower}[case]
    for ending in sorted(_NOMINATIVE[gender], key=len, reverse=True):
        if lower.endswith(ending) and len(lower) > len(ending) + 1:
            stem = lower[: -len(ending)]
            break
    else:
        return None
    known = fold(stem) in _ADJECTIVES
    if not known and (known_only or not _ADJECTIVE_LIKE.search(lower)):
        return None
    soft = ending.startswith("i") and len(ending) > 1 or ending == "i"
    i = "i" if soft or stem.endswith(("k", "g")) else ""
    if gender == "p":
        return stem + ("ich" if i else "ych") if case in ("loc", "gen") else lower
    if gender == "f":
        return stem + ("iej" if i else "ej") if case in ("loc", "gen") else stem + ("ią" if soft else "ą")
    if case == "loc":
        return stem + ("im" if i else "ym")
    if case == "gen":
        return stem + ("iego" if i else "ego")
    return lower  # masculine (inanimate) and neuter accusative: as named


# ------------------------------------------------------------------- hints


def hint(description: str, why: str) -> str:
    """The description is the command's own, as what one wants to do:
    "włączyć światło"."""
    return f"Wygląda na to, że chcesz {description}, {why}."


PARTLY = "ale nie wszystko było zrozumiałe"


def no_room(device: str) -> str:
    return f"ale nie wiem, w którym pomieszczeniu jest {device}"


def not_in(device: str, room: str) -> str:
    return f"ale {device} nie znajduje się {_place('area', room, False)}"


def not_here(device: str, rooms: list[str]) -> str:
    """Why: the device said, by a name several share or one heard only in
    its room, isn't in the satellite's, and no room was said: where it is."""
    where = place([("area", room) for room in rooms])
    return f"ale {device} nie znajduje się tutaj" + (", tylko " + where if rooms else "")


def several(device: str) -> str:
    """Why: the name said is more than one device's where it is heard (a
    name of the area's two devices, or a whole house name two have)."""
    return f"ale więcej niż jedno urządzenie nazywa się {device}"


_WHICH = {"entity": "które urządzenie", "area": "które pomieszczenie", "floor": "które piętro", "scene": "która scena", "script": "który skrypt"}
_SLOTS = {
    "color": "jaki kolor", "percentage": "ile procent", "temperature": "jaka temperatura", "volume": "jaka głośność",
    "item": "co", "reminder": "o czym przypomnieć", "query": "co puścić", "timer": "który minutnik",
    "time": "o której", "number": "jaka liczba",
}
"""What a slot asks, by its name in the library: "jaki kolor"."""


def _not_heard(slot: str) -> str:
    which = _SLOTS.get(slot)
    return f"ale nie wiem, {which}" if which else "ale czegoś zabrakło"


def missing(first: dict[str, Any], duration: bool = False, number_slot: str = "number") -> str:
    kind = first.get("kind")
    if kind == "require":
        if first.get("slot"):
            return _not_heard(first["slot"])
        return f"ale brakuje słowa „{first['phrases'][0]}”"
    if kind == "name":
        return f"ale nie wiem, {_WHICH.get(first['type'], 'które')}"
    if kind == "number":
        if first.get("heard"):
            return f"ale {first['heard'][0]} jest poza zakresem"
        if duration:
            return "ale nie wiem, na jak długo"
        return _not_heard(number_slot)
    if kind == "text":
        return _not_heard(first["slot"])
    if kind == "unexplained":
        words = " ".join(first.get("words") or [])
        return f"ale nie rozumiem „{words}”" if words else PARTLY
    if kind == "unknown_name":
        return f"ale nie znam „{' '.join(first.get('words') or [])}”"
    if kind == "place":
        what = {"floor": "piętra", "entity": "urządzenia"}.get(str(first.get("type")), "pomieszczenia")
        return f"ale nie mogę do tego wybrać {what}"
    return "ale nie mam pewności"


# ------------------------------------------------------------------ timers

_UNITS = (("godzina", "godziny", "godzin", "godzinę"), ("minuta", "minuty", "minut", "minutę"),
          ("sekunda", "sekundy", "sekund", "sekundę"))
"""Each unit (all feminine): one, two to four, five and more, one as an
object ("na minutę")."""


def _parts(seconds: float) -> list[tuple[int, int]]:
    """(value, unit index) to say, largest first."""
    total = round(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    parts = [(value, unit) for unit, value in enumerate((hours, minutes, secs)) if value] or [(0, 2)]
    return parts[:2] if hours else parts  # an hour and some: seconds don't matter


def _amount(value: int, unit: int, accusative: bool) -> str:
    one, few, many, as_object = _UNITS[unit]
    if value == 1:
        return f"jedną {as_object}" if accusative else f"jedna {one}"
    return count(value, one, few, many, feminine=True)


def duration(seconds: float, adjective: bool = False) -> str:
    """"jedna godzina i 5 minut", "dwie minuty", "21 sekund"; ``adjective``,
    as a timer is named by it, after "na" (the accusative): "minutnik na
    jedną minutę"."""
    return join([_amount(value, unit, adjective) for value, unit in _parts(seconds)])


def _left_verb(seconds: float) -> str:
    """"została jedna minuta", "zostały dwie minuty", "zostało 5 minut"."""
    value = _parts(seconds)[0][0]
    return {"one": "została", "few": "zostały", "many": "zostało"}[plural(value, "one", "few", "many")]


def _clock(hour: int, minute: int, case: str = "nom") -> str:
    """A time of day in ``case`` ("nom", "loc" after "o", "acc" after
    "na"): "siódma trzydzieści", "siódmej zero pięć", "północ"."""
    if (hour, minute) == (0, 0):
        return {"nom": "północ", "loc": "północy", "acc": "północ"}[case]
    said = "zero" if hour == 0 else ordinal(hour, {"nom": "nom_f", "loc": "loc_f", "acc": "acc_f"}[case])
    if minute:
        said += f" zero {spell(minute)}" if minute < 10 else f" {spell(minute)}"
    return said


def clock(hour: int, minute: int) -> str:
    """"siódma trzydzieści", "dziewiętnasta", "dwudziesta pierwsza zero
    pięć", "północ"."""
    return _clock(hour, minute)


_NOMINATIVE_ENDS = (("ę", "a"), ("ą", "a"))


def _name(name: str) -> str:
    """A timer's name as heard, its last word back in the nominative when it
    was heard as an object: "pizzę" -> "pizza"."""
    words = name.split()
    for heard, said in _NOMINATIVE_ENDS:
        if words and len(words[-1]) > 3 and words[-1].endswith(heard):
            words[-1] = words[-1][: -len(heard)] + said
    return " ".join(words)


def _alarm(timer: dict[str, Any]) -> bool:
    return fold(str(timer.get("name") or "").strip().lower()) in ("alarm", "budzik")


def _timer(timer: dict[str, Any], case: str = "nom") -> str:
    """"minutnik pizza", "minutnika na 5 minut", "budzik": in ``case``
    ("nom", "gen")."""
    word = ("budzik" if _alarm(timer) else "minutnik") + ("a" if case == "gen" else "")
    name = re.sub(r"^(?:minutnik|timer|budzik)\w*\s+", "", str(timer.get("name") or "").strip(), flags=re.IGNORECASE)
    if _alarm(timer):
        return word
    if name:
        return f"{word} {_name(name)}"
    return f"{word} na {duration(float(timer.get('created_duration') or 0), adjective=True)}"


def _left(timer: dict[str, Any]) -> str:
    return duration(float(timer.get("remaining") or 0))


def _running(timers: list[dict[str, Any]]) -> str:
    listed = join([_timer(t) for t in timers])
    return f"Działa {listed}." if len(timers) == 1 else f"Działają {listed}."


TIMER_HOW_LONG = "Na jak długo ustawić minutnik?"
TIMER_HOW_MUCH = "Ile czasu?"
ALARM_WHEN = "Na którą godzinę ustawić budzik?"
NO_TIMERS = "Nie ma żadnych minutników."


def timer_started(timer: dict[str, Any], ends: tuple[int, int]) -> dict[str, str]:
    specific = f"Ustawiono {_timer(timer)}."
    return {"brief": "Minutnik ustawiony.", "specific": specific,
            "verbose": f"{specific[:-1]}, zadzwoni o {_clock(*ends, case='loc')}."}


def alarm_set(at: tuple[int, int], seconds: float) -> dict[str, str]:
    specific = f"Ustawiono budzik na {_clock(*at, case='acc')}."
    return {"brief": "Budzik ustawiony.", "specific": specific,
            "verbose": f"{specific[:-1]}, za {duration(seconds, adjective=True)}."}


def timers_cancelled(timers: list[dict[str, Any]]) -> dict[str, str]:
    number = len(timers)
    return {
        "brief": "Anulowano minutnik." if number == 1 else "Anulowano minutniki.",
        "specific": "Anulowano minutnik." if number == 1 else f"Anulowano {count(number, 'minutnik', 'minutniki', 'minutników')}.",
        "verbose": f"Anulowano {join([_timer(t) for t in timers])}.",
    }


def timer_not_found(asked: str | float | None, running: list[dict[str, Any]]) -> str:
    there = _running(running)
    if asked is None:
        return there
    if isinstance(asked, (int, float)):
        name = _timer({"created_duration": asked}, "gen")
    else:
        name = _timer({"name": asked}, "gen")
    return f"Nie ma {name}. {there}"


def which_timer(timers: list[dict[str, Any]]) -> str:
    return f"Który minutnik? {_running(timers)}"


def timer_status(timers: list[dict[str, Any]]) -> str:
    said = []
    for t in timers:
        seconds = float(t.get("remaining") or 0)
        if t.get("status") == "paused":
            said.append(_capital(f"{_timer(t)} jest wstrzymany, {_left_verb(seconds)} {_left(t)}."))
        else:
            said.append(f"Do końca {_timer(t, 'gen')} {_left_verb(seconds)} {_left(t)}.")
    return " ".join(said)


def timer_already(operation: str, timers: list[dict[str, Any]]) -> str:
    if len(timers) == 1:
        state = "jest już wstrzymany" if operation == "pause" else "już działa"
        return _capital(f"{_timer(timers[0])} {state}.")
    return "Wszystkie minutniki są już wstrzymane." if operation == "pause" else "Wszystkie minutniki już działają."


def timer_changed(operation: str, timer: dict[str, Any], seconds: float, left: float) -> dict[str, str]:
    added = operation == "add"
    amount = duration(seconds, adjective=True)
    specific = f"Dodano {amount} do {_timer(timer, 'gen')}." if added else f"Odjęto {amount} od {_timer(timer, 'gen')}."
    return {
        "brief": "Dodano czas." if added else "Odjęto czas.",
        "specific": specific,
        "verbose": f"{specific[:-1]}, {_left_verb(left)} {duration(left)}.",
    }


def timer_done(operation: str, timer: dict[str, Any]) -> dict[str, str]:
    done = {"cancel": "Anulowano", "pause": "Wstrzymano", "resume": "Wznowiono"}[operation]
    specific = f"{done} {_timer(timer)}."
    seconds = float(timer.get("remaining") or 0)
    verbose = {
        "cancel": f"{specific[:-1]}, który miał jeszcze {duration(seconds, adjective=True)}.",
        "pause": f"{specific[:-1]}, {_left_verb(seconds)} {_left(timer)}.",
        "resume": f"{specific[:-1]}, {_left_verb(seconds)} {_left(timer)}.",
    }[operation]
    return {"brief": f"{done} minutnik.", "specific": specific, "verbose": verbose}


def timers_ringing(timers: list[dict[str, Any]]) -> str:
    return " ".join("Dzwoni budzik." if _alarm(t) else _capital(f"{_timer(t)} skończył odliczanie.") for t in timers)


# ------------------------------------------------------------------- music

NO_PLAYER = "Tu nie ma odtwarzacza muzyki."
NOTHING_PLAYING = "Nic nie gra."
MUSIC_SAID = {
    "no_player": NO_PLAYER,
    "nothing_playing": NOTHING_PLAYING,
    "missing_query": "Co mam puścić?",
    "missing_volume": "Jaka głośność?",
    "already": "Już gra.",
}
MOVE_SAID = {
    "nothing_playing": "Nic nie gra, więc nie ma czego przenieść.",
    "no_player": "Tam nie ma odtwarzacza muzyki.",
    "already": "Muzyka już tam gra.",
}
NOT_KNOWN_PLAYING = "Nie wiem, co teraz gra."


def media(title: str, artist: str | None) -> str:
    """"So What, Miles Davis": an artist's name stays as it is, which "w
    wykonaniu" would decline."""
    return f"{title}, {artist}" if artist else title


def item(name: str, media_type: str, artist: str | None) -> str:
    """As what is played: "playlistę Jazz", "Kind of Blue, Miles Davis"."""
    if media_type == "playlist":
        return name if "playlist" in name.lower() else f"playlistę {name}"
    if artist and media_type in ("track", "album"):
        return f"{name}, {artist}"
    return name


def not_found(query: str) -> str:
    return f"Nie znaleziono: {query}."


def now_playing(what: str, paused: bool) -> str:
    return f"Wstrzymano {what}." if paused else f"Teraz gra {what}."


_CONTROL_SAID = {
    "pause": "Wstrzymano.",
    "resume": "Wznowiono.",
    "stop": "Zatrzymano.",
    "next": "Następny utwór.",
    "previous": "Poprzedni utwór.",
    "volume_up": "Głośniej.",
    "volume_down": "Ciszej.",
    "volume_set": "Ustawiono głośność.",
    "mute": "Wyciszono.",
    "unmute": "Włączono dźwięk.",
    "shuffle_on": "Losowa kolejność włączona.",
    "shuffle_off": "Losowa kolejność wyłączona.",
    "repeat_all": "Powtarzanie wszystkiego.",
    "repeat_one": "Powtarzanie tego utworu.",
    "repeat_off": "Powtarzanie wyłączone.",
}
_DONE = {"pause": "Wstrzymano", "resume": "Wznowiono", "stop": "Zatrzymano"}
_FOUND_SAID = {"play": "Odtwarzam muzykę.", "add": "Dodano do kolejki.", "radio": "Odtwarzam podobną muzykę."}


def control(operation: str, volume: float | None, what: str | None, players: list[str]) -> dict[str, str]:
    brief = _CONTROL_SAID.get(operation, "Gotowe.")
    specific = brief
    if volume is not None:
        specific = f"Ustawiono głośność na {round(volume * 100)} procent."
    elif operation in _DONE and what:
        specific = f"{_DONE[operation]} {what}."
    return {"brief": brief, "specific": specific, "verbose": _on_players(specific, players)}


def found(operation: str, what: str, players: list[str]) -> dict[str, str]:
    specific = {"add": f"Dodano do kolejki {what}.", "radio": f"Odtwarzam muzykę podobną: {what}."}.get(
        operation, f"Odtwarzam {what}."
    )
    return {"brief": _FOUND_SAID.get(operation, "Odtwarzam."), "specific": specific, "verbose": _on_players(specific, players)}


def moved(what: str | None, source: str, target: str, to: str) -> dict[str, str]:
    """A player is named after a noun that declines ("na urządzenie
    Głośnik kuchenny"), as its name may not."""
    return {
        "brief": "Przeniesiono muzykę.",
        "specific": f"Przeniesiono muzykę {to}." if to else f"Przeniesiono muzykę na urządzenie {target}.",
        "verbose": f"Przeniesiono {what or 'muzykę'} z urządzenia {source} na urządzenie {target}.",
    }


def _on_players(sentence: str, players: list[str]) -> str:
    """"Wstrzymano So What, Miles Davis, na urządzeniu Głośnik w kuchni."."""
    if not players:
        return sentence
    on = "na urządzeniu" if len(players) == 1 else "na urządzeniach"
    return f"{sentence[:-1]}, {on} {join(players)}."


def not_available(place: str) -> str:
    """A command heard that isn't heard here: another area's own, or the
    whole home's turned off in this one (``place``: "in the Kitchen"; "" for
    a satellite in no area)."""
    return f"To polecenie nie jest dostępne {place or 'tutaj'}."
