"""What the app says in Ukrainian: every sentence it makes itself, with
English's names (see voice_labs/languages/en/speech.py for what each is).

A place is said in the locative with its preposition -- "на кухні", "у
вітальні", "у ванній", "на першому поверсі", "нагорі" -- and where something
goes in the accusative: "на кухню", "у вітальню". A room is declined by its
head noun (``_ROOMS``: the commonest rooms and floors), the adjectives before
it agreeing ("у великій спальні", "на літній кухні"); words after it stay as
written ("у кімнаті Олі"). A name of none of them isn't declined: "у кімнаті
Office", "на поверсі Antresol".

The voice reads a written number as a masculine cardinal and a clock time
"7:30" as "сім дві крапки тридцять", so times and counts of feminine
units -- hours, minutes and seconds all are -- are written in words:
"двадцять одна хвилина", "о сьомій тридцять". Quotes are read aloud
("відкриті лапки"), so names are said bare: "таймер піца". Sentences are
impersonal (-но, -ся), which agree with no one: "Таймер піца запущено",
"не вдалося зрозуміти"."""

from __future__ import annotations

import re
from typing import Any

from .numerals import cardinal, counted, ordinal, plural

# --------------------------------------------------------------- anywhere

SORRY = "Вибач, не вдалося зрозуміти."
FAILED = "Вибач, не вийшло."
NO_SPEAKER = "Вибач, я не знаю, хто говорить."

def not_on_list(items: list[str]) -> str:
    """A to-do list command's items that aren't on the list (``on_list``)."""
    return f"У списку немає: {join(items)}."


def already_on_list(items: list[str]) -> str:
    """What adding to a to-do list found on it already, not checked off
    (``adds_to_list``)."""
    return f"У списку вже є: {join(items)}."


NOT_SET = "Це ще не налаштовано."
WENT_WRONG = "Вибач, щось пішло не так."
NO_COMMANDS = "Голосових команд ще немає. Додай їх у застосунку Voice Labs."
TIMERS_NEED_INTEGRATION = "Для таймерів потрібна інтеграція Voice Labs у Home Assistant."

NO_AREA = 'Я не знаю, в якій я зоні. Призначте цьому сателіту зону.'
"""A command for the satellite's area, on a satellite in none."""


def nothing_there(place: str) -> str:
    """An intent found nothing of the kind ``place`` ("in the Kitchen"; "" for a device by name)."""
    return f"Я нічого такого не знайшов {place}." if place else "Я цього не знайшов."


def not_exposed(place: str) -> str:
    """What it found there isn't exposed to Assist."""
    return f"Нічого такого {place} не відкрито для Assist." if place else "Це не відкрито для Assist."


def unavailable(place: str) -> str:
    """What it found there is all unavailable."""
    return f"Усе це {place} зараз недоступне." if place else "Це зараз недоступне."


# What a device can't do of what a command needs (its ``supports``), by
# capability (after "не підтримує"); None for what Home Assistant refused
# without saying.
_SUPPORT = {
    "brightness": "зміну яскравості",
    "color": "зміну кольору",
    "color_temp": "зміну колірної температури",
    "position": "встановлення положення",
    "tilt": "нахил",
    "temperature": "встановлення температури",
    "fan_mode": "режими вентилятора",
    "hvac_mode": "зміну режиму",
    "volume_set": "встановлення гучності",
    None: "це",
}


def cannot(names: list[str], capability: str | None, value: str | None = None) -> str:
    """Devices that can't: "Гаражні ворота не підтримує встановлення
    положення."; that have no ``value`` (a mode)."""
    supports = "не підтримує" if len(names) == 1 else "не підтримують"
    if value is not None:
        what = f"режим вентилятора {value}" if capability == "fan_mode" else f"режим {value}"
    else:
        what = _SUPPORT.get(capability, _SUPPORT[None])
    return f"{join(names)} {supports} {what}."


def none_can(place: str, capability: str | None) -> str:
    """Nothing of the kind ``place`` can: "Ніщо на кухні не підтримує зміну кольору."."""
    what = _SUPPORT.get(capability, _SUPPORT[None])
    return f"Ніщо {place} не підтримує {what}." if place else f"Ніщо не підтримує {what}."


# ------------------------------------------------------- other satellites


def announced(place: str) -> dict[str, str]:
    """An announcement, said on the other satellites: every one, or those
    ``place`` ("in the Kitchen", "upstairs")."""
    return {"brief": "Оголошено.", "specific": f"Оголошено {place}." if place else "Оголошено всюди."}


def no_satellites(place: str) -> str:
    """No other satellite ``place`` ("in the Kitchen"; "" for anywhere)."""
    return f"Іншого пристрою {place} немає." if place else "Інших пристроїв немає."


def connecting(to: str) -> str:
    """The intercom, about to connect ``to`` there (``place(to=True)``)."""
    return f"З'єдную {to}."


def intercom_busy(place: str) -> str:
    """The satellite there is in a call, ringing or connected."""
    return f"Пристрій {place} зараз зайнятий."


INTERCOM_FIRMWARE = "Для інтеркому потрібна новіша прошивка на обох пристроях."
"""Either satellite's firmware can't connect the intercom."""

INTERCOM_HERE = "Ви вже там."
"""The intercom asked for the satellite's own area, with none other there."""


EXAMPLE_NAMES = {
    "room": "Кухня",
    "player": "Кухонна колонка",
    "other_player": "Колонка у вітальні",
    "timers": ["піца", "чай", "лазанья"],
}


def join(parts: list[str]) -> str:
    """"a", "a і b", "a, b і c"; "" for none (a satellite in no area)."""
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " і " + parts[-1]


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


# ------------------------------------------------------------------ places

_ROOMS: dict[str, tuple[str, str, str, str]] = {
    # The room: its preposition ("у" or "на"), locative, accusative, gender.
    "кухня": ("на", "кухні", "кухню", "f"),
    "вітальня": ("у", "вітальні", "вітальню", "f"),
    "спальня": ("у", "спальні", "спальню", "f"),
    "кімната": ("у", "кімнаті", "кімнату", "f"),
    "ванна": ("у", "ванній", "ванну", "f"),
    "дитяча": ("у", "дитячій", "дитячу", "f"),
    "гостьова": ("у", "гостьовій", "гостьову", "f"),
    "ігрова": ("у", "ігровій", "ігрову", "f"),
    "гардеробна": ("у", "гардеробній", "гардеробну", "f"),
    "душова": ("у", "душовій", "душову", "f"),
    "прихожа": ("у", "прихожій", "прихожу", "f"),
    "їдальня": ("у", "їдальні", "їдальню", "f"),
    "вбиральня": ("у", "вбиральні", "вбиральню", "f"),
    "пральня": ("у", "пральні", "пральню", "f"),
    "котельня": ("у", "котельні", "котельню", "f"),
    "майстерня": ("у", "майстерні", "майстерню", "f"),
    "лазня": ("у", "лазні", "лазню", "f"),
    "комора": ("у", "коморі", "комору", "f"),
    "кладовка": ("у", "кладовці", "кладовку", "f"),
    "бібліотека": ("у", "бібліотеці", "бібліотеку", "f"),
    "студія": ("у", "студії", "студію", "f"),
    "сауна": ("у", "сауні", "сауну", "f"),
    "квартира": ("у", "квартирі", "квартиру", "f"),
    "теплиця": ("у", "теплиці", "теплицю", "f"),
    "альтанка": ("в", "альтанці", "альтанку", "f"),
    "тераса": ("на", "терасі", "терасу", "f"),
    "веранда": ("на", "веранді", "веранду", "f"),
    "лоджія": ("на", "лоджії", "лоджію", "f"),
    "мансарда": ("на", "мансарді", "мансарду", "f"),
    "дача": ("на", "дачі", "дачу", "f"),
    "ділянка": ("на", "ділянці", "ділянку", "f"),
    "вулиця": ("на", "вулиці", "вулицю", "f"),
    "коридор": ("у", "коридорі", "коридор", "m"),
    "передпокій": ("у", "передпокої", "передпокій", "m"),
    "хол": ("у", "холі", "хол", "m"),
    "вестибюль": ("у", "вестибюлі", "вестибюль", "m"),
    "кабінет": ("у", "кабінеті", "кабінет", "m"),
    "офіс": ("в", "офісі", "офіс", "m"),
    "туалет": ("у", "туалеті", "туалет", "m"),
    "санвузол": ("у", "санвузлі", "санвузол", "m"),
    "гардероб": ("у", "гардеробі", "гардероб", "m"),
    "гараж": ("у", "гаражі", "гараж", "m"),
    "підвал": ("у", "підвалі", "підвал", "m"),
    "льох": ("у", "льоху", "льох", "m"),
    "погріб": ("у", "погребі", "погріб", "m"),
    "сарай": ("у", "сараї", "сарай", "m"),
    "зал": ("у", "залі", "зал", "m"),
    "спортзал": ("у", "спортзалі", "спортзал", "m"),
    "басейн": ("у", "басейні", "басейн", "m"),
    "будинок": ("у", "будинку", "будинок", "m"),
    "сад": ("у", "саду", "сад", "m"),
    "двір": ("у", "дворі", "двір", "m"),
    "балкон": ("на", "балконі", "балкон", "m"),
    "ганок": ("на", "ганку", "ганок", "m"),
    "город": ("на", "городі", "город", "m"),
    "поверх": ("на", "поверсі", "поверх", "m"),
    "горище": ("на", "горищі", "горище", "n"),
    "подвір'я": ("на", "подвір'ї", "подвір'я", "n"),
    "сходи": ("на", "сходах", "сходи", "pl"),
}
"""Rooms and floors by their head noun (nominative, lowercase): the
preposition they take, and the noun in the locative ("у вітальні") and the
accusative ("у вітальню"), with its gender, which adjectives before it agree
with."""

_BARE = {"нагорі": "нагору", "вгорі": "нагору", "внизу": "вниз", "унизу": "вниз", "надворі": "надвір",
         "вдома": "додому", "удома": "додому"}
"""Places said as they are ("нагорі"), and where to ("нагору")."""

_ADJECTIVE_ENDINGS = {
    # (nominative ending, gender): (locative, accusative)
    ("ий", "m"): ("ому", "ий"), ("ій", "m"): ("ьому", "ій"), ("ин", "m"): ("иному", "ин"),
    ("ів", "m"): ("овому", "ів"), ("їв", "m"): ("євому", "їв"),
    ("а", "f"): ("ій", "у"), ("я", "f"): ("ій", "ю"),
    ("е", "n"): ("ому", "е"), ("є", "n"): ("ьому", "є"),
    ("і", "pl"): ("их", "і"),
}
"""How an adjective before a room agrees with it: "перший поверх", "на
першому поверсі"; "літня кухня", "на літній кухні"; "Мамин кабінет", "у
Маминому кабінеті"."""

_VOWELS = "аеєиіїоуюя"


def place(places: list[tuple[str, str]], to: bool = False) -> str:
    """Where a command acts: "на кухні", "у вітальні", "на першому поверсі",
    "нагорі"; ``to``, where something goes: "на кухню", "у вітальню",
    "нагору"."""
    return join([_place(kind, name, to) for kind, name in places])


def _place(kind: str, name: str, to: bool = False) -> str:
    lower = " ".join(_plain(name).split())
    if lower in _BARE:
        return _BARE[lower] if to else lower
    words = name.split()
    found = _head(words)
    before = [_agreeing(word, found[2][3], to) for word in words[: found[0]]] if found else [None]
    if found is None or None in before:
        # Not declined: "у кімнаті Office", "на поверсі Antresol".
        _preposition, locative, accusative, _gender = _ROOMS["поверх" if kind == "floor" else "кімната"]
        said = f"{accusative if to else locative} {name}"
        return f"на {said}" if kind == "floor" else f"у {said}"
    _start, end, (preposition, locative, accusative, _gender) = found
    said = " ".join([*before, accusative if to else locative, *words[end:]])
    if preposition == "на":
        return f"на {said}"
    return f"{'в' if said[:1].lower() in _VOWELS else 'у'} {said}"


def _plain(text: str) -> str:
    """Lowercase, with the apostrophe the model writes: "Подвір’я" ->
    "подвір'я"."""
    return text.lower().replace("’", "'").replace("ʼ", "'")


_ADJECTIVAL = {"ванна", "дитяча", "гостьова", "ігрова", "гардеробна", "душова", "прихожа"}
"""Rooms named by an adjective alone ("у ванній"), which before another room
are its adjective ("у ванній кімнаті")."""


def _head(words: list[str]) -> tuple[int, int, tuple[str, str, str, str]] | None:
    """Where a name's head noun is (its word, the one after) and the room it
    is: "Велика спальня" is a спальня, "Ванна кімната" a кімната, "Кухня
    -вітальня" a кухня and a вітальня."""
    lower = [_plain(w) for w in words]
    for index, word in enumerate(lower):
        following = lower[index + 1] if index + 1 < len(lower) else ""
        if word in _ROOMS and not (word in _ADJECTIVAL and following in _ROOMS):
            return index, index + 1, _ROOMS[word]
        parts = word.split("-")
        if len(parts) == 2 and all(p in _ROOMS for p in parts):
            first, second = _ROOMS[parts[0]], _ROOMS[parts[1]]
            return index, index + 1, (first[0], f"{first[1]}-{second[1]}", f"{first[2]}-{second[2]}", first[3])
    return None


def _agreeing(word: str, gender: str, to: bool) -> str | None:
    """An adjective before a room, agreeing with it ("першому", "літній"), or
    a number as its ordinal ("2 поверх": "на другому поверсі"); None when it
    is neither."""
    number = re.fullmatch(r"(\d{1,2})(?:-?(?:й|ий|а|е|ій))?", word)
    if number:
        n = int(number.group(1))
        if gender == "pl":
            return None
        if gender == "f":
            return ordinal(n, "f_acc" if to else "f_loc")
        return ordinal(n, ("n" if gender == "n" else "m") if to else "loc")
    lower = _plain(word)
    for (ending, of), (locative, accusative) in sorted(_ADJECTIVE_ENDINGS.items(), key=lambda e: -len(e[0][0])):
        if of == gender and lower.endswith(ending) and len(lower) > len(ending) + 2:
            said = word[: len(word) - len(ending)] + (accusative if to else locative)
            # A person's ("Мамин кабінет", "Олина кімната") keeps its capital.
            own = word[:1].isupper() and re.search(r"(?:ин|їн)(?:а|е)?$", lower)
            return said if own else said.lower()
    return None


# ------------------------------------------------------------------- hints


def hint(description: str, why: str) -> str:
    """The description is the command's own, as what one wants to do:
    "увімкнути світло"."""
    return f"Схоже, ти хочеш {description}, {why}."


PARTLY = "але не все вдалося зрозуміти"


def no_room(device: str) -> str:
    return f"але я не знаю, в якій кімнаті {device}"


def not_in(device: str, room: str) -> str:
    return f"але {device} не {_place('area', room)}"


def not_here(device: str, rooms: list[str]) -> str:
    """Why: the device said, by a name several share or one heard only in
    its room, isn't in the satellite's, and no room was said: where it is."""
    where = place([("area", room) for room in rooms])
    return f"але {device} не тут" + (", лише " + where if rooms else "")


def several(device: str) -> str:
    """Why: the name said is more than one device's where it is heard (a
    name of the area's two devices, or a whole house name two have)."""
    return f"але пристроїв з назвою {device} кілька"


_WHICH = {"entity": "який пристрій", "area": "яка кімната", "floor": "який поверх", "scene": "яка сцена", "script": "який скрипт"}
_SLOTS = {
    "color": "який колір", "percentage": "скільки відсотків", "temperature": "яка температура",
    "volume": "яка гучність", "item": "що саме", "reminder": "про що нагадати", "query": "що ввімкнути",
    "timer": "який таймер", "time": "на котру годину", "number": "яке число",
}
"""What a slot asks, by its name in the library: "який колір"."""


def _not_heard(slot: str) -> str:
    which = _SLOTS.get(slot)
    return f"але незрозуміло, {which}" if which else "але не все вдалося розчути"


def missing(first: dict[str, Any], duration: bool = False, number_slot: str = "number") -> str:
    kind = first.get("kind")
    if kind == "require":
        if first.get("slot"):
            return _not_heard(first["slot"])
        return f"але не прозвучало: {first['phrases'][0]}"
    if kind == "name":
        return f"але незрозуміло, {_WHICH.get(first['type'], 'що саме')}"
    if kind == "number":
        if first.get("heard"):
            return f"але {first['heard'][0]} не підходить"
        if duration:
            return "але незрозуміло, на скільки"
        return _not_heard(number_slot)
    if kind == "text":
        return _not_heard(first["slot"])
    if kind == "unexplained":
        words = " ".join(first.get("words") or [])
        return f"але незрозуміло: {words}" if words else PARTLY
    if kind == "unknown_name":
        return f"але назва {' '.join(first.get('words') or [])} незнайома"
    if kind == "place":
        what = {"floor": "поверх", "entity": "пристрій"}.get(str(first.get("type")), "кімнату")
        return f"але для цього не можна вибрати {what}"
    return "але певності немає"


# ------------------------------------------------------------------ timers

_UNITS = (("година", "години", "годин"), ("хвилина", "хвилини", "хвилин"), ("секунда", "секунди", "секунд"))
"""Hours, minutes and seconds: after 1, 2-4, 5 and more. All feminine."""


def _parts(seconds: float) -> list[tuple[int, int]]:
    """(value, unit index) to say, largest first."""
    total = round(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    parts = [(value, unit) for unit, value in enumerate((hours, minutes, secs)) if value] or [(0, 2)]
    return parts[:2] if hours else parts  # an hour and some: seconds don't matter


def duration(seconds: float, adjective: bool = False) -> str:
    """"одна година і п'ять хвилин", "двадцять одна секунда", in words. As
    an adjective -- a timer is "на п'ять хвилин", a time "через годину" --
    in the accusative: "одну годину і п'ять хвилин"."""
    return join([counted(value, _UNITS[unit], "f", accusative=adjective) for value, unit in _parts(seconds)])


def _minutes(minute: int) -> str:
    """A clock time's minutes: "тридцять", "нуль п'ять", "двадцять одна"."""
    return f"нуль {cardinal(minute, 'f')}" if minute < 10 else cardinal(minute, "f")


def _clock(hour: int, minute: int, form: str = "f") -> str:
    """A time of day on a 24-hour clock, its hour an ordinal in ``form``
    (f, f_acc, f_loc): "сьома тридцять", "на сьому тридцять", "о сьомій
    тридцять"; midnight is "північ"."""
    if (hour, minute) == (0, 0):
        return "опівночі" if form == "f_loc" else "північ"
    said = "нуль" if hour == 0 else ordinal(hour, form)
    if minute:
        return f"{said} {_minutes(minute)}"
    return said if hour else f"{said} {_minutes(minute)}"


def clock(hour: int, minute: int) -> str:
    """A time of day, as it is named: "сьома тридцять", "дев'ятнадцята нуль
    п'ять", "двадцять перша", "північ"."""
    return _clock(hour, minute)


def _at(hour: int, minute: int) -> str:
    """When: "о сьомій тридцять", "об одинадцятій", "опівночі"."""
    said = _clock(hour, minute, "f_loc")
    if said == "опівночі":
        return said
    return f"{'об' if said[:1] in 'оаеуі' else 'о'} {said}"


def _named(timer: dict[str, Any]) -> str | None:
    """The name a timer was given, or "alarm" for an alarm (None: none)."""
    name = " ".join(str(timer.get("name") or "").split())
    if name.lower() in ("alarm", "будильник"):
        return "alarm"
    return name or None


_CASES = {"nom": ("таймер", "будильник"), "gen": ("таймера", "будильника"), "loc": ("таймері", "будильнику")}


def _timer(timer: dict[str, Any], case: str = "nom") -> str:
    """"таймер піца", "таймер на п'ять хвилин", "будильник"; in ``case``
    (nom, gen, loc): "таймера піца", "на таймері піца"."""
    word, alarm = _CASES[case]
    name = _named(timer)
    if name == "alarm":
        return alarm
    if name and name.lower().startswith("таймер"):
        return word + name[len("таймер") :] if case != "nom" else name
    if name:
        return f"{word} {name}"
    return f"{word} на {duration(float(timer.get('created_duration') or 0), adjective=True)}"


def _left(timer: dict[str, Any]) -> str:
    return duration(float(timer.get("remaining") or 0))


def _there(timers: list[dict[str, Any]]) -> str:
    return f"Є {join([_timer(t) for t in timers])}."


TIMER_HOW_LONG = "На скільки поставити таймер?"
TIMER_HOW_MUCH = "Скільки часу?"
ALARM_WHEN = "На котру годину поставити будильник?"
NO_TIMERS = "Таймерів немає."


def timer_started(timer: dict[str, Any], ends: tuple[int, int]) -> dict[str, str]:
    specific = f"{_capital(_timer(timer))} запущено."
    return {"brief": "Таймер запущено.", "specific": specific, "verbose": f"{specific[:-1]}, спрацює {_at(*ends)}."}


def alarm_set(at: tuple[int, int], seconds: float) -> dict[str, str]:
    specific = f"Будильник поставлено на {_clock(*at, 'f_acc')}."
    return {"brief": "Будильник поставлено.", "specific": specific,
            "verbose": f"{specific[:-1]}, це через {duration(seconds, adjective=True)}."}


def timers_cancelled(timers: list[dict[str, Any]]) -> dict[str, str]:
    count = len(timers)
    many = f"Скасовано {count} {plural(count, 'таймер', 'таймери', 'таймерів')}."
    return {
        "brief": "Таймер скасовано." if count == 1 else "Таймери скасовано.",
        "specific": "Таймер скасовано." if count == 1 else many,
        "verbose": f"Скасовано {join([_timer(t) for t in timers])}.",
    }


def timer_not_found(asked: str | float | None, running: list[dict[str, Any]]) -> str:
    there = _there(running)
    if asked is None:
        return there
    if isinstance(asked, (int, float)):
        name = _timer({"created_duration": asked}, "gen")
    else:
        name = _timer({"name": asked}, "gen")
    return f"{_capital(name)} немає. {there}"


def which_timer(timers: list[dict[str, Any]]) -> str:
    return f"Який таймер? {_there(timers)}"


def timer_status(timers: list[dict[str, Any]]) -> str:
    said = []
    for t in timers:
        if t.get("status") == "paused":
            said.append(f"{_capital(_timer(t))} на паузі, на ньому ще {_left(t)}.")
        else:
            said.append(f"На {_timer(t, 'loc')} ще {_left(t)}.")
    return " ".join(said)


def timer_already(operation: str, timers: list[dict[str, Any]]) -> str:
    if len(timers) == 1:
        named = _capital(_timer(timers[0]))
        already = "вже" if named[-1:].lower() in _VOWELS else "уже"  # "піца вже", "чай уже"
        return f"{named} {already} {'на паузі' if operation == 'pause' else 'працює'}."
    return "Усі таймери вже на паузі." if operation == "pause" else "Усі таймери вже працюють."


def timer_changed(operation: str, timer: dict[str, Any], seconds: float, left: float) -> dict[str, str]:
    added = operation == "add"
    amount = duration(seconds, adjective=True)
    specific = f"Додано {amount} до {_timer(timer, 'gen')}." if added else f"Знято {amount} з {_timer(timer, 'gen')}."
    return {
        "brief": "Час додано." if added else "Час знято.",
        "specific": specific,
        "verbose": f"{specific[:-1]}, тепер на ньому {duration(left)}.",
    }


def timer_done(operation: str, timer: dict[str, Any]) -> dict[str, str]:
    done = {"cancel": "скасовано", "pause": "призупинено", "resume": "відновлено"}[operation]
    specific = f"{_capital(_timer(timer))} {done}."
    left = float(timer.get("remaining") or 0)
    verbose = {
        "cancel": f"{specific[:-1]} за {duration(left, adjective=True)} до кінця.",
        "pause": f"{specific[:-1]}, на ньому ще {duration(left)}.",
        "resume": f"{specific[:-1]}, на ньому ще {duration(left)}.",
    }[operation]
    return {"brief": f"Таймер {done}.", "specific": specific, "verbose": verbose}


def timers_ringing(timers: list[dict[str, Any]]) -> str:
    return " ".join("Будильник дзвонить." if _named(t) == "alarm" else f"{_capital(_timer(t))} завершено."
                    for t in timers)


# ------------------------------------------------------------------- music

NO_PLAYER = "Тут немає музичного програвача."
NOTHING_PLAYING = "Зараз нічого не грає."
MUSIC_SAID = {
    "no_player": NO_PLAYER,
    "nothing_playing": NOTHING_PLAYING,
    "missing_query": "Що ввімкнути?",
    "missing_volume": "Яку гучність?",
    "already": "Уже грає.",
}
MOVE_SAID = {
    "nothing_playing": "Нічого не грає, тож нічого переносити.",
    "no_player": "Там немає музичного програвача.",
    "already": "Музика вже грає там.",
}
NOT_KNOWN_PLAYING = "Не знаю, що зараз грає."


def media(title: str, artist: str | None) -> str:
    """"So What — Miles Davis": a name can't be declined after "у
    виконанні", and the voice pauses at the dash."""
    return f"{title} — {artist}" if artist else title


def item(name: str, media_type: str, artist: str | None) -> str:
    if media_type == "playlist":
        return name if "плейлист" in name.lower() or "playlist" in name.lower() else f"плейлист {name}"
    if artist and media_type in ("track", "album"):
        return f"{name} — {artist}"
    return name


def not_found(query: str) -> str:
    return f"Не вдалося знайти {query}."


def now_playing(what: str, paused: bool) -> str:
    return f"{_capital(what)} на паузі." if paused else f"Зараз грає {what}."


def _percent(value: int) -> str:
    """"30 відсотків", "1 відсоток", "22 відсотки"."""
    return f"{value} {plural(value, 'відсоток', 'відсотки', 'відсотків')}"


_CONTROL_SAID = {
    "pause": "Призупинено.",
    "resume": "Відновлено.",
    "stop": "Зупинено.",
    "next": "Наступний трек.",
    "previous": "Попередній трек.",
    "volume_up": "Гучніше.",
    "volume_down": "Тихіше.",
    "volume_set": "Гучність змінено.",
    "mute": "Звук вимкнено.",
    "unmute": "Звук увімкнено.",
    "shuffle_on": "Перемішування увімкнено.",
    "shuffle_off": "Перемішування вимкнено.",
    "repeat_all": "Повтор усього.",
    "repeat_one": "Повтор цього треку.",
    "repeat_off": "Повтор вимкнено.",
}
_FOUND_SAID = {"play": "Вмикаю музику.", "add": "Додано в чергу.", "radio": "Вмикаю схожу музику."}


def control(operation: str, volume: float | None, what: str | None, players: list[str]) -> dict[str, str]:
    brief = _CONTROL_SAID.get(operation, "Готово.")
    specific = brief
    if volume is not None:
        specific = f"Гучність {_percent(round(volume * 100))}."
    elif operation in ("pause", "resume", "stop") and what:
        specific = f"{brief[:-1]} {what}."
    return {"brief": brief, "specific": specific, "verbose": _on_players(specific, players)}


def found(operation: str, what: str, players: list[str]) -> dict[str, str]:
    specific = {"add": f"Додано в чергу: {what}.", "radio": f"Вмикаю музику, схожу на {what}."}.get(
        operation, f"Вмикаю {what}."
    )
    return {"brief": _FOUND_SAID.get(operation, "Вмикаю."), "specific": specific, "verbose": _on_players(specific, players)}


def moved(what: str | None, source: str, target: str, to: str) -> dict[str, str]:
    return {
        "brief": "Музику перенесено.",
        "specific": f"Музику перенесено {to}." if to else f"Музику перенесено на пристрій {target}.",
        "verbose": f"Перенесено {what or 'музику'}: з пристрою {source} на пристрій {target}.",
    }


def _on_players(sentence: str, players: list[str]) -> str:
    """"Призупинено So What — Miles Davis, на пристрої Кухонна колонка."."""
    if not players:
        return sentence
    where = "на пристрої" if len(players) == 1 else "на пристроях"
    return f"{sentence[:-1]}, {where} {join(players)}."


def not_available(place: str) -> str:
    """A command heard that isn't heard here: another area's own, or the
    whole home's turned off in this one (``place``: "in the Kitchen"; "" for
    a satellite in no area)."""
    return f"Ця команда недоступна {place or 'тут'}."
