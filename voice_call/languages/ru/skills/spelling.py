"""How Russian asks for a spelling ("как пишется слово молоко?", "по буквам
молоко", "продиктуй по буквам корабль") and says one: each letter by its
name ("эм", "о", "эл"; "мягкий знак"), as a voice can't say a letter alone
(voice_call/skills/spelling.py). A Latin letter is said as Russian speakers
name it ("би", "дабл-ю")."""

from __future__ import annotations

import re

from voice_call.skills.common import Reply
from voice_call.skills.spelling import Settings, key

EXAMPLES = ("как пишется слово молоко", "по буквам корабль")

ASK_RE = re.compile(
    r"^(?:слушай |скажи |подскажи |а )?(?:"
    r"(?:как (?:правильно )?(?:пишется|написать|пишут|писать)|(?:продиктуй|произнеси|назови|скажи|прочитай)"
    r" по буквам|по буквам|как (?:это )?(?:будет )?по буквам)"
    r"\s+(?:слово\s+)?(?P<asked>.+?)"
    r"|(?:слово )?(?P<how_is>.+?),? (?:как (?:правильно )?(?:пишется|написать)|по буквам)"
    r")[?.!]*$",
    re.IGNORECASE,
)
WORD_RE = re.compile(r"[a-zа-яё][a-zа-яё'\-]*", re.IGNORECASE)

SAID = {" ": "пробел", "-": "дефис", "'": "апостроф"}
"""What isn't a letter or a digit, said."""

LETTERS = {
    "а": "а", "б": "бэ", "в": "вэ", "г": "гэ", "д": "дэ", "е": "е", "ё": "ё", "ж": "жэ", "з": "зэ", "и": "и",
    "й": "и краткое", "к": "ка", "л": "эль", "м": "эм", "н": "эн", "о": "о", "п": "пэ", "р": "эр", "с": "эс",
    "т": "тэ", "у": "у", "ф": "эф", "х": "ха", "ц": "цэ", "ч": "че", "ш": "ша", "щ": "ща", "ъ": "твёрдый знак",
    "ы": "ы", "ь": "мягкий знак", "э": "э", "ю": "ю", "я": "я",
    "a": "эй", "b": "би", "c": "си", "d": "ди", "e": "и", "f": "эф", "g": "джи", "h": "эйч", "i": "ай",
    "j": "джей", "k": "кей", "l": "эл", "m": "эм", "n": "эн", "o": "оу", "p": "пи", "q": "кью", "r": "ар",
    "s": "эс", "t": "ти", "u": "ю", "v": "ви", "w": "дабл-ю", "x": "экс", "y": "уай", "z": "зед",
}
"""Each letter by its name."""


def letters(spelled: str) -> str:
    """Each letter by its name, a digit as it is, and a space, hyphen or
    apostrophe said -- with commas, so the voice says each."""
    out: list[str] = []
    for c in spelled.strip():
        if c.lower() in LETTERS:
            out.append(LETTERS[c.lower()])
        elif c.isalnum():
            out.append(c)
        elif c in SAID and out and out[-1] not in SAID.values():
            out.append(SAID[c])
    while out and out[-1] in SAID.values():
        out.pop()
    return ", ".join(out)


def answer(text: str, settings: Settings) -> Reply | None:
    m = ASK_RE.match(text.strip())
    if not m:
        return None
    asked = key(m.group("asked") or m.group("how_is"))
    spelled = settings.words.get(asked)
    if spelled is None:
        if not WORD_RE.fullmatch(asked):
            return None
        spelled = asked
    said = letters(spelled)
    if not said:
        return None
    word = spelled[:1].upper() + spelled[1:]
    capital = said[:1].upper() + said[1:]
    return Reply(said, f"{capital}.", f"{word} пишется так: {said}.", f"{word}. {capital}. {word}.")
