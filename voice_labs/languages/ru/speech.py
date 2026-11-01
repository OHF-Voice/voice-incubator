"""What the app says in Russian: every sentence it makes itself, with English's
names (see voice_labs/languages/en/speech.py for what each is).

A place is said with its preposition, in the prepositional case: "на
кухне", "в гостиной", "на первом этаже", "наверху"; where something goes,
in the accusative: "на кухню", "в гостиную". A room is known by its head
noun -- the first of its words a room is named by ("Детская комната" is a
комната, "Первый этаж" an этаж), else an adjective standing for one
("Гостиная") -- and the adjectives before it agree with it; a name of none of
them is said without being declined: "в комнате «Чулан Пети»".

Counts follow Russian's plural (1, 21 минута; 2-4 минуты; 5-20 минут), and
a count Piper would read in the wrong gender is written out ("двадцать одна
минута", "две секунды"); a time of day is on a 24-hour clock, "7:30".
Nothing the app says of itself takes a speaker's gender ("не получилось",
not "я не смог"), and a timer's name is said as it was given, in quotes:
"Таймер «пицца» запущен"."""

from __future__ import annotations

import re
from typing import Any

from .numerals import cardinal, ordinal, plural

# --------------------------------------------------------------- anywhere

SORRY = "Извините, я не понимаю."
FAILED = "Извините, не получилось."
NO_SPEAKER = "Извините, я не знаю, кто говорит."

def not_on_list(items: list[str]) -> str:
    """A to-do list command's items that aren't on the list (``on_list``)."""
    return f"В списке нет: {join(items)}."


def already_on_list(items: list[str]) -> str:
    """What adding to a to-do list found on it already, not checked off
    (``adds_to_list``)."""
    return f"В списке уже есть: {join(items)}."


NOT_SET = "Это ещё не настроено."
WENT_WRONG = "Извините, что-то пошло не так."


def confirm_question(description: str) -> str:
    return f"{description[:1].upper()}{description[1:]}?"


CONFIRM_NO = "Хорошо."
CONFIRM_UNCLEAR = "Извините, ответ непонятен."
NO_COMMANDS = "Голосовые команды ещё не настроены. Добавьте их в приложении Voice Labs."
TIMERS_NEED_INTEGRATION = "Для таймеров нужна интеграция Voice Labs в Home Assistant."

NO_AREA = 'Я не знаю, в какой я зоне. Назначьте этому сателлиту зону.'
"""A command for the satellite's area, on a satellite in none."""


def nothing_there(place: str) -> str:
    """An intent found nothing of the kind ``place`` ("in the Kitchen"; "" for a device by name)."""
    return f"Я ничего такого не нашёл {place}." if place else "Я этого не нашёл."


def not_exposed(place: str) -> str:
    """What it found there isn't exposed to Assist."""
    return f"Ничего такого {place} не открыто для Assist." if place else "Это не открыто для Assist."


def unavailable(place: str) -> str:
    """What it found there is all unavailable."""
    return f"Всё это {place} сейчас недоступно." if place else "Это сейчас недоступно."


# What a device can't do of what a command needs (its ``supports``), by
# capability (after "не поддерживает"); None for what Home Assistant refused
# without saying.
_SUPPORT = {
    "brightness": "изменение яркости",
    "color": "изменение цвета",
    "color_temp": "изменение цветовой температуры",
    "position": "установку положения",
    "tilt": "наклон",
    "temperature": "установку температуры",
    "fan_mode": "режимы вентилятора",
    "hvac_mode": "смену режима",
    "volume_set": "установку громкости",
    None: "это",
}


def cannot(names: list[str], capability: str | None, value: str | None = None) -> str:
    """Devices that can't: "Гаражные ворота не поддерживает установку
    положения."; that have no ``value`` (a mode)."""
    supports = "не поддерживает" if len(names) == 1 else "не поддерживают"
    if value is not None:
        what = f"режим вентилятора {value}" if capability == "fan_mode" else f"режим {value}"
    else:
        what = _SUPPORT.get(capability, _SUPPORT[None])
    return f"{join(names)} {supports} {what}."


def none_can(place: str, capability: str | None) -> str:
    """Nothing of the kind ``place`` can: "Ничто на кухне не поддерживает изменение цвета."."""
    what = _SUPPORT.get(capability, _SUPPORT[None])
    return f"Ничто {place} не поддерживает {what}." if place else f"Ничто не поддерживает {what}."


# ------------------------------------------------------- other satellites


def announced(place: str) -> dict[str, str]:
    """An announcement, said on the other satellites: every one, or those
    ``place`` ("in the Kitchen", "upstairs")."""
    return {"brief": "Объявлено.", "specific": f"Объявлено {place}." if place else "Объявлено везде."}


def no_satellites(place: str) -> str:
    """No other satellite ``place`` ("in the Kitchen"; "" for anywhere)."""
    return f"Другого устройства {place} нет." if place else "Других устройств нет."


def do_not_disturb(place: str) -> str:
    """Kept out of where do not disturb is on, ``place`` ("in the Bedroom")."""
    return f"Режим «Не беспокоить» включён {place}."


def connecting(to: str) -> str:
    """The intercom, about to connect ``to`` there (``place(to=True)``)."""
    return f"Соединяю {to}."


def intercom_busy(place: str) -> str:
    """The satellite there is in a call, ringing or connected."""
    return f"Устройство {place} сейчас занято."


INTERCOM_FIRMWARE = "Для интеркома нужна более новая прошивка на обоих устройствах."
"""Either satellite's firmware can't connect the intercom."""

INTERCOM_HERE = "Вы уже там."
"""The intercom asked for the satellite's own area, with none other there."""


EXAMPLE_NAMES = {
    "room": "Кухня",
    "player": "Колонка на кухне",
    "other_player": "Колонка в гостиной",
    "timers": ["пицца", "чай", "лазанья"],
}


def join(parts: list[str]) -> str:
    """"a", "a и b", "a, b и c"."""
    return "".join(parts) if len(parts) <= 1 else ", ".join(parts[:-1]) + " и " + parts[-1]


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


def _fold(word: str) -> str:
    """Lowercase, "ё" as "е": as a room is looked up."""
    return word.lower().replace("ё", "е")


# ------------------------------------------------------------------ places

_ROOMS = {
    # Rooms and floors by their head noun: gender, the preposition, and the
    # prepositional case ("на кухне", "в саду").
    "кухня": ("f", "на", "кухне"), "спальня": ("f", "в", "спальне"), "комната": ("f", "в", "комнате"),
    "коридор": ("m", "в", "коридоре"), "холл": ("m", "в", "холле"), "зал": ("m", "в", "зале"),
    "кабинет": ("m", "в", "кабинете"), "офис": ("m", "в", "офисе"), "балкон": ("m", "на", "балконе"),
    "лоджия": ("f", "на", "лоджии"), "терраса": ("f", "на", "террасе"), "веранда": ("f", "на", "веранде"),
    "крыльцо": ("n", "на", "крыльце"), "двор": ("m", "в", "дворе"), "сад": ("m", "в", "саду"),
    "огород": ("m", "на", "огороде"), "участок": ("m", "на", "участке"), "гараж": ("m", "в", "гараже"),
    "подвал": ("m", "в", "подвале"), "погреб": ("m", "в", "погребе"), "чердак": ("m", "на", "чердаке"),
    "мансарда": ("f", "в", "мансарде"), "этаж": ("m", "на", "этаже"), "уровень": ("m", "на", "уровне"),
    "цоколь": ("m", "в", "цоколе"), "туалет": ("m", "в", "туалете"), "санузел": ("m", "в", "санузле"),
    "душ": ("m", "в", "душе"), "баня": ("f", "в", "бане"), "сауна": ("f", "в", "сауне"),
    "библиотека": ("f", "в", "библиотеке"), "дом": ("m", "в", "доме"), "дача": ("f", "на", "даче"),
    "квартира": ("f", "в", "квартире"), "студия": ("f", "в", "студии"), "лестница": ("f", "на", "лестнице"),
    "площадка": ("f", "на", "площадке"), "тамбур": ("m", "в", "тамбуре"), "кладовка": ("f", "в", "кладовке"),
    "чулан": ("m", "в", "чулане"), "гардероб": ("m", "в", "гардеробе"), "бассейн": ("m", "в", "бассейне"),
    "улица": ("f", "на", "улице"), "беседка": ("f", "в", "беседке"), "теплица": ("f", "в", "теплице"),
    "сарай": ("m", "в", "сарае"), "пристройка": ("f", "в", "пристройке"), "спортзал": ("m", "в", "спортзале"),
    "кинозал": ("m", "в", "кинозале"), "мезонин": ("m", "в", "мезонине"), "помещение": ("n", "в", "помещении"),
    "зона": ("f", "в", "зоне"), "угол": ("m", "в", "углу"), "лофт": ("m", "в", "лофте"), "бар": ("m", "в", "баре"),
    "патио": ("n", "в", "патио"), "прихожка": ("f", "в", "прихожке"), "спаленка": ("f", "в", "спаленке"),
    "комнатка": ("f", "в", "комнатке"), "кухонька": ("f", "на", "кухоньке"),
}

_STANDING = {
    "гостиная", "детская", "ванная", "столовая", "кладовая", "гардеробная", "мастерская", "прачечная",
    "котельная", "игровая", "душевая", "парная", "гостевая", "уборная", "постирочная", "серверная", "прихожая",
    "спальная", "приемная", "сушильная", "бойлерная",
}
"""Adjectives standing for a room ("в гостиной"): feminine, as комната."""

_BARE = {"наверху": "наверх", "внизу": "вниз", "снаружи": "наружу", "внутри": "внутрь", "везде": "везде"}
"""Places said as they are ("наверху"), and where to ("наверх")."""

_COMMON = {"мамин", "мамина", "мамино", "папин", "папина", "папино", "бабушкин", "бабушкина", "дедушкин",
           "дедушкина", "дочкин", "дочкина", "сынов", "сынова"}
"""Whose a room is, written lowercase in a sentence: "в маминой спальне"."""

_NUMBERED = re.compile(r"^(\d{1,2})(?:-?(?:й|ый|ой|ий|я|ая|е|ое))?$")
"""A floor's number: "1 этаж", "2-й этаж": "на первом этаже"."""


def place(places: list[tuple[str, str]], to: bool = False) -> str:
    """Where a command acts: "на кухне", "в гостиной", "на первом этаже",
    "наверху"; ``to``, where something goes: "на кухню", "в гостиную",
    "наверх"."""
    return join([_place(name, kind, to) for kind, name in places])


def _place(name: str, kind: str = "area", to: bool = False) -> str:
    folded = _fold(name.strip())
    if folded in _BARE:
        return _BARE[folded] if to else folded
    said = _declined(name.split(), to)
    if said is None:
        if kind == "floor":
            return f"на этаж «{name}»" if to else f"на этаже «{name}»"
        return f"в комнату «{name}»" if to else f"в комнате «{name}»"
    preposition, words = said
    return f"{_preposition(preposition, words)} {words}"


def _preposition(preposition: str, following: str) -> str:
    """"в", or "во" before "в" or "ф" and a consonant ("во второй спальне"),
    and before "дворе"."""
    if preposition == "в" and re.match(r"[вф][^аеёиоуыэюя]|двор", following.lower()):
        return "во"
    return preposition


def _declined(words: list[str], to: bool) -> tuple[str, str] | None:
    """The preposition and the words of a room's name declined ("кухне",
    "первом этаже"), or None when it has no head noun to go by. Words after
    the head stay as they are ("в комнате для гостей")."""
    if not words:
        return None
    folded = [_fold(w) for w in words]
    head = next((i for i, w in enumerate(folded) if _known_head(w)), None)
    if head is None:
        head = next((i for i, w in reversed(list(enumerate(folded))) if w in _STANDING), None)
    if head is None:
        return None
    gender, preposition = _head(folded[head])
    said = []
    for index, word in enumerate(words):
        if index < head:
            said.append(_modifier(word, gender, to))
        elif index == head:
            said.append(_head_said(word, to))
        else:
            said.append(word)
    return preposition, " ".join(said)


def _known_head(word: str) -> bool:
    if "-" in word:  # "кухня-гостиная": its first part
        return _known_head(word.split("-", 1)[0])
    return word in _ROOMS


def _head(word: str) -> tuple[str, str]:
    """The gender and preposition of a head noun (or of the first part of
    one joined with a hyphen); an adjective standing for a room is a
    комната's."""
    word = word.split("-", 1)[0]
    if word in _ROOMS:
        return _ROOMS[word][:2]
    return "f", "в"


def _head_said(word: str, to: bool) -> str:
    """The head noun declined, lowercase; each part of "кухня-гостиная"."""
    parts = []
    for part in _fold(word).split("-"):
        if part in _ROOMS:
            gender, _, prepositional = _ROOMS[part]
            parts.append(_accusative(part, gender) if to else prepositional)
        elif part in _STANDING:
            parts.append(_adjective(part, "f", "acc" if to else "prep") or part)
        else:
            parts.append(part)
    return "-".join(parts)


def _accusative(noun: str, gender: str) -> str:
    """Where to: "кухня" -> "кухню", "комната" -> "комнату"; others as they
    are ("кабинет", "крыльцо")."""
    if gender == "f" and noun.endswith("а"):
        return noun[:-1] + "у"
    if gender == "f" and noun.endswith("я"):
        return noun[:-1] + "ю"
    return noun


def _modifier(word: str, gender: str, to: bool) -> str:
    """A word before the head: an adjective agreeing with it ("первом",
    "маминой", lowercase), a number of a floor ("1 этаж": "первом"), or
    another word, as it is."""
    case = "acc" if to else "prep"
    if number := _NUMBERED.match(word):
        return ordinal(int(number.group(1)), gender, case)
    folded = _fold(word)
    said = _adjective(folded, gender, case)
    if said is None:
        return word
    # Lowercase in a sentence, but a person's: "в Сашиной комнате".
    personal = re.search(r"(?:ин|ина|ино)$", folded) and folded not in _COMMON
    return _capital(said) if personal and word[:1].isupper() else said


_THIRD = {("m", "prep"): "третьем", ("f", "prep"): "третьей", ("n", "prep"): "третьем",
          ("m", "acc"): "третий", ("f", "acc"): "третью", ("n", "acc"): "третье"}


def _adjective(word: str, gender: str, case: str) -> str | None:
    """An adjective (lowercase, nominative) in ``case`` ("prep", "acc"),
    agreeing with a noun of ``gender``; None when it isn't one."""
    if word in ("третий", "третья", "третье"):
        return _THIRD[(gender, case)]
    stem, ending = word[:-2], word[-2:]
    if len(stem) < 2:
        return None
    if re.search(r"(?:ин|ина|ино)$", word) and len(word) > 4:  # мамин, мамина
        stem = word[:-1] if word.endswith(("а", "о")) else word
        if case == "acc":
            return stem + ("у" if gender == "f" else "о" if gender == "n" else "")
        return stem + ("ой" if gender == "f" else "ом")
    if ending not in ("ый", "ой", "ий", "ая", "яя", "ое", "ее"):
        return None
    if case == "acc":
        if gender == "f":
            return stem + ("юю" if ending == "яя" else "ую")
        if gender == "n":
            return stem + ("ее" if ending in ("ий", "яя", "ее") and not stem.endswith(("к", "г", "х")) else "ое")
        return word if ending in ("ый", "ой", "ий") else stem + ("ий" if ending in ("яя", "ее") else "ый")
    soft = ending in ("яя", "ее") or (ending == "ий" and not stem.endswith(("к", "г", "х")))
    hushing = stem.endswith(("ж", "ш", "ч", "щ", "ц")) and ending in ("ая", "ий", "ее") and word not in ("большая",)
    if gender == "f":
        return stem + ("ей" if soft or hushing else "ой")
    return stem + ("ем" if soft or hushing else "ом")


# ------------------------------------------------------------------- hints


def hint(description: str, why: str) -> str:
    """The description is the command's own, as what one wants to do:
    "включить свет"."""
    return f"Похоже, вы хотите {description}, {why}."


PARTLY = "но не всё удалось понять"


def no_room(device: str) -> str:
    return f"но неизвестно, в какой комнате {device}"


def not_in(device: str, room: str) -> str:
    return f"но {device} не {_place(room)}"


def not_here(device: str, rooms: list[str]) -> str:
    """Why: the device said, by a name several share or one heard only in
    its room, isn't in the satellite's, and no room was said: where it is."""
    where = place([("area", room) for room in rooms])
    return f"но {device} не здесь" + (", только " + where if rooms else "")


def several(device: str) -> str:
    """Why: the name said is more than one device's where it is heard (a
    name of the area's two devices, or a whole house name two have)."""
    return f"но устройств с названием {device} несколько"


_WHICH = {"entity": "какое устройство", "area": "какая комната", "floor": "какой этаж", "scene": "какая сцена", "script": "какой скрипт"}
_SLOTS = {
    "color": "какой цвет", "percentage": "сколько процентов", "temperature": "какая температура",
    "volume": "какая громкость", "item": "что именно", "reminder": "о чём напомнить", "query": "что включить",
    "timer": "какой таймер", "time": "на какое время", "number": "какое число", "device_class": "что именно",
}
"""What a slot asks, by its name in the library: "какой цвет"."""


def _not_said(slot: str) -> str:
    which = _SLOTS.get(slot)
    return f"но не указано, {which}" if which else "но сказано не всё"


def missing(first: dict[str, Any], duration: bool = False, number_slot: str = "number") -> str:
    kind = first.get("kind")
    if kind == "require":
        if first.get("slot"):
            return _not_said(first["slot"])
        return f"но не прозвучало «{first['phrases'][0]}»"
    if kind == "name":
        return f"но не указано, {_WHICH.get(first['type'], 'что именно')}"
    if kind == "number":
        if first.get("heard"):
            return f"но {first['heard'][0]} — вне допустимых значений"
        if duration:
            return "но не указано, на сколько"
        return _not_said(number_slot)
    if kind == "text":
        return _not_said(first["slot"])
    if kind == "unexplained":
        words = " ".join(first.get("words") or [])
        return f"но непонятно, что значит «{words}»" if words else PARTLY
    if kind == "unknown_name":
        return f"но я не знаю, что такое «{' '.join(first.get('words') or [])}»"
    if kind == "place":
        what = {"floor": "этаж", "entity": "устройство"}.get(str(first.get("type")), "комнату")
        return f"но для этого нельзя выбрать {what}"
    return "но полной уверенности нет"


# ------------------------------------------------------------------ timers

_UNITS = (("час", "часа", "часов", "m"), ("минута", "минуты", "минут", "f"), ("секунда", "секунды", "секунд", "f"))
"""Each unit after 1, 2-4 and 5 and on, and its gender."""

_ACCUSATIVE = {"минута": "минуту", "секунда": "секунду"}


def _parts(seconds: float) -> list[tuple[int, int]]:
    """(value, unit index) to say, largest first."""
    total = round(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    parts = [(value, unit) for unit, value in enumerate((hours, minutes, secs)) if value] or [(0, 2)]
    return parts[:2] if hours else parts  # an hour and some: seconds don't matter


def count(value: int, gender: str = "m", case: str = "nom") -> str:
    """A count before its noun, as a voice should read it: in digits ("5",
    "21 час"), in words where Piper would read the wrong gender ("двадцать
    одна", "две", "одну")."""
    if gender == "f" and value % 10 in (1, 2) and value % 100 not in (11, 12):
        return cardinal(value, "f", case)
    return str(value)


def duration(seconds: float, adjective: bool = False) -> str:
    """"1 час и 5 минут", "двадцать одна минута"; as an adjective, as said
    after "на" or "через" (the accusative): "на одну минуту", "через 5
    минут"."""
    case = "acc" if adjective else "nom"
    said = []
    for value, unit in _parts(seconds):
        one, few, many, gender = _UNITS[unit]
        noun = plural(value, one, few, many)
        if case == "acc":
            noun = _ACCUSATIVE.get(noun, noun)
        said.append(f"{count(value, gender, case)} {noun}")
    return join(said)


def _agreeing(seconds: float, masculine: str, feminine: str, neuter: str) -> str:
    """A verb agreeing with a duration, by its first part: "остался 1 час",
    "осталась одна минута", "осталось 5 минут"."""
    value, unit = _parts(seconds)[0]
    if value % 10 == 1 and value % 100 != 11:
        return masculine if _UNITS[unit][3] == "m" else feminine
    return neuter


def _left(seconds: float, past: bool = False) -> str:
    """"осталось 5 минут", "осталась одна минута"; ``past``, "оставалось"."""
    if past:
        verb = _agreeing(seconds, "оставался", "оставалась", "оставалось")
    else:
        verb = _agreeing(seconds, "остался", "осталась", "осталось")
    return f"{verb} {duration(seconds)}"


def clock(hour: int, minute: int) -> str:
    """A time of day: "7:30", "19:05", "полночь", "полдень"."""
    if (hour, minute) == (0, 0):
        return "полночь"
    if (hour, minute) == (12, 0):
        return "полдень"
    return f"{hour}:{minute:02d}"


_HEADS = {"таймер": ("таймера", "таймеру"), "будильник": ("будильника", "будильнику")}


def _named(timer: dict[str, Any]) -> str:
    """In the nominative, lowercase: "таймер «пицца»", "таймер на 5 минут";
    an alarm is "будильник"."""
    name = " ".join(str(timer.get("name") or "").split())
    if _fold(name) in ("alarm", "будильник"):
        return "будильник"
    if name and _fold(name).split()[0] in _HEADS:
        return name[:1].lower() + name[1:]
    if name:
        return f"таймер «{name}»"
    return f"таймер на {duration(float(timer.get('created_duration') or 0), adjective=True)}"


def _case(timer: dict[str, Any], case: str) -> str:
    """In the genitive ("таймера «пицца»") or dative ("таймеру «пицца»")."""
    named = _named(timer)
    head, _, rest = named.partition(" ")
    forms = _HEADS.get(_fold(head))
    if forms is None:
        return named
    said = forms[0] if case == "gen" else forms[1]
    return f"{said} {rest}".strip()


def _there(timers: list[dict[str, Any]]) -> str:
    """"Есть таймер «пицца» и таймер на 5 минут."."""
    return f"Есть {join([_named(t) for t in timers])}."


TIMER_HOW_LONG = "На сколько поставить таймер?"
TIMER_HOW_MUCH = "На сколько?"
ALARM_WHEN = "На какое время поставить будильник?"
NO_TIMERS = "Таймеров нет."


def timer_started(timer: dict[str, Any], ends: tuple[int, int]) -> dict[str, str]:
    specific = f"{_capital(_named(timer))} запущен."
    return {"brief": "Таймер запущен.", "specific": specific, "verbose": f"{specific[:-1]}, сработает в {clock(*ends)}."}


def alarm_set(at: tuple[int, int], seconds: float) -> dict[str, str]:
    specific = f"Будильник поставлен на {clock(*at)}."
    return {"brief": "Будильник поставлен.", "specific": specific,
            "verbose": f"{specific[:-1]}, через {duration(seconds, adjective=True)}."}


def timers_cancelled(timers: list[dict[str, Any]]) -> dict[str, str]:
    n = len(timers)
    return {
        "brief": "Таймер отменён." if n == 1 else "Таймеры отменены.",
        "specific": "Таймер отменён." if n == 1 else (
            f"{plural(n, 'Отменён', 'Отменены', 'Отменено')} {n} {plural(n, 'таймер', 'таймера', 'таймеров')}."),
        "verbose": f"{'Отменён' if n == 1 else 'Отменены'} {join([_named(t) for t in timers])}.",
    }


def timer_not_found(asked: str | float | None, running: list[dict[str, Any]]) -> str:
    there = _there(running)
    if asked is None:
        return there
    if isinstance(asked, (int, float)):
        name = f"таймера на {duration(asked, adjective=True)}"
    else:
        name = _case({"name": asked}, "gen")
    return f"{_capital(name)} нет. {there}"


def which_timer(timers: list[dict[str, Any]]) -> str:
    return f"Какой таймер? {_there(timers)}"


def timer_status(timers: list[dict[str, Any]]) -> str:
    said = []
    for t in timers:
        left = float(t.get("remaining") or 0)
        if t.get("status") == "paused":
            said.append(f"{_capital(_named(t))} на паузе, {_left(left)}.")
        else:
            said.append(f"У {_case(t, 'gen')} {_left(left)}.")
    return " ".join(said)


def timer_already(operation: str, timers: list[dict[str, Any]]) -> str:
    if len(timers) == 1:
        state = "уже на паузе" if operation == "pause" else "уже идёт"
        return f"{_capital(_named(timers[0]))} {state}."
    return "Все таймеры уже на паузе." if operation == "pause" else "Все таймеры уже идут."


def timer_changed(operation: str, timer: dict[str, Any], seconds: float, left: float) -> dict[str, str]:
    added = operation == "add"
    specific = f"{_capital(_named(timer))} {'продлён' if added else 'сокращён'} на {duration(seconds, adjective=True)}."
    return {
        "brief": "Время добавлено." if added else "Время убавлено.",
        "specific": specific,
        "verbose": f"{specific[:-1]}, {_left(left)}.",
    }


def timer_done(operation: str, timer: dict[str, Any]) -> dict[str, str]:
    done = {"cancel": "отменён", "pause": "поставлен на паузу", "resume": "снова запущен"}[operation]
    specific = f"{_capital(_named(timer))} {done}."
    left = float(timer.get("remaining") or 0)
    verbose = f"{specific[:-1]}, {_left(left, past=operation == 'cancel')}."
    return {"brief": f"Таймер {done}.", "specific": specific, "verbose": verbose}


def timers_ringing(timers: list[dict[str, Any]]) -> str:
    return " ".join(f"{_capital(_named(t))} сработал." for t in timers)


# ------------------------------------------------------------------- music

NO_PLAYER = "Здесь нет музыкального плеера."
NOTHING_PLAYING = "Сейчас ничего не играет."
MUSIC_SAID = {
    "no_player": NO_PLAYER,
    "nothing_playing": NOTHING_PLAYING,
    "missing_query": "Что включить?",
    "missing_volume": "Какую громкость поставить?",
    "already": "Уже играет.",
}
MOVE_SAID = {
    "nothing_playing": "Сейчас ничего не играет, переносить нечего.",
    "no_player": "Там нет музыкального плеера.",
    "already": "Музыка уже играет там.",
}
NOT_KNOWN_PLAYING = "Не знаю, что сейчас играет."


def media(title: str, artist: str | None) -> str:
    """"«So What» в исполнении Miles Davis"."""
    return f"«{title}» в исполнении {artist}" if artist else f"«{title}»"


def item(name: str, media_type: str, artist: str | None) -> str:
    """"плейлист «Джаз»", "«Kind of Blue» в исполнении Miles Davis"; an
    artist or a station by its name."""
    if media_type == "playlist":
        return name if "плейлист" in name.lower() else f"плейлист «{name}»"
    if media_type in ("track", "album"):
        return media(name, artist)
    return name


def not_found(query: str) -> str:
    return f"Не удалось найти «{query}»."


def now_playing(what: str, paused: bool) -> str:
    return f"{_capital(what)} на паузе." if paused else f"Сейчас играет {what}."


_CONTROL_SAID = {
    "pause": "Пауза.",
    "resume": "Продолжаю.",
    "stop": "Остановлено.",
    "next": "Следующий трек.",
    "previous": "Предыдущий трек.",
    "volume_up": "Громче.",
    "volume_down": "Тише.",
    "volume_set": "Громкость изменена.",
    "mute": "Звук выключен.",
    "unmute": "Звук включён.",
    "shuffle_on": "Перемешивание включено.",
    "shuffle_off": "Перемешивание выключено.",
    "repeat_all": "Повтор всего.",
    "repeat_one": "Повтор этого трека.",
    "repeat_off": "Повтор выключен.",
}
_DONE = {"pause": "На паузе:", "resume": "Продолжаю", "stop": "Остановлено:"}
_FOUND_SAID = {"play": "Включаю музыку.", "add": "Добавлено в очередь.", "radio": "Включаю похожую музыку."}


def control(operation: str, volume: float | None, what: str | None, players: list[str]) -> dict[str, str]:
    brief = _CONTROL_SAID.get(operation, "Готово.")
    specific = brief
    if volume is not None:
        percent = round(volume * 100)
        specific = f"Громкость {percent} {plural(percent, 'процент', 'процента', 'процентов')}."
    elif operation in _DONE and what:
        specific = f"{_DONE[operation]} {what}."
    return {"brief": brief, "specific": specific, "verbose": _on_players(specific, players)}


def found(operation: str, what: str, players: list[str]) -> dict[str, str]:
    specific = {"add": f"Добавлено в очередь: {what}.", "radio": f"Включаю музыку, похожую на {what}."}.get(
        operation, f"Включаю {what}."
    )
    return {"brief": _FOUND_SAID.get(operation, "Включаю."), "specific": specific, "verbose": _on_players(specific, players)}


def moved(what: str | None, source: str, target: str, to: str) -> dict[str, str]:
    return {
        "brief": "Музыка перенесена.",
        "specific": f"Музыка перенесена {to}." if to else f"Музыка перенесена на устройство «{target}».",
        "verbose": f"Музыка перенесена с устройства «{source}» на устройство «{target}»"
                   + (f": {what}." if what else "."),
    }


def _on_players(sentence: str, players: list[str]) -> str:
    """"Пауза: «So What» в исполнении Miles Davis, на устройстве «Колонка на
    кухне»."."""
    if not players:
        return sentence
    where = "на устройстве" if len(players) == 1 else "на устройствах"
    return f"{sentence[:-1]}, {where} {join([f'«{p}»' for p in players])}."


def not_available(place: str) -> str:
    """A command heard that isn't heard here: another area's own, or the
    whole home's turned off in this one (``place``: "in the Kitchen"; "" for
    a satellite in no area)."""
    return f"Эта команда недоступна {place or 'здесь'}."
