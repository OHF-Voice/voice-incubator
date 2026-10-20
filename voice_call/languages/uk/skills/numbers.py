"""How the Ukrainian skills read numbers and questions, with English's names
(voice_call/languages/en/skills/numbers.py).

Ukrainian declines its numbers: "п'ятнадцять відсотків від вісімдесяти",
"корінь зі ста сорока чотирьох", "у п'яти милях" -- read in any case
(numerals.NUMBER_WORDS). It writes a decimal comma ("3,5", said "три кома
п'ять") and a space between thousands ("31 536 000"); a half as "з
половиною" ("два з половиною"), one and a half as "півтора" or "півтори".

The voice reads digits as a masculine cardinal, a decimal comma not at all,
and numbers from a thousand up wrongly, so what is said writes those in
words (``said``, ``counted``): "Це вісім кома нуль сорок сім кілометра",
"Це двадцять одна склянка". The answer itself keeps its digits."""

from __future__ import annotations

import re

from ..numerals import NUMBER_WORDS, SCALE_WORDS, cardinal, plural, spoken, whole

DECIMAL = ","
"""8,047."""

THOUSANDS = " "
"""31 536 000, with a space that doesn't break (the answer; what is said
has it in words)."""

FRACTIONS = {
    "половина": 0.5, "половину": 0.5, "половини": 0.5, "пів": 0.5, "чверть": 0.25, "чверті": 0.25,
    "три чверті": 0.75, "третина": 1 / 3, "третину": 1 / 3, "третини": 1 / 3, "дві третини": 2 / 3,
    "десята частина": 0.1, "десяту частину": 0.1, "півтора": 1.5, "півтори": 1.5,
}
"""Parts said as words: "чверть", "три чверті", "півтори"."""


def _alternatives(words) -> str:
    return "|".join(sorted((re.escape(w) for w in set(words)), key=len, reverse=True))


_WORD = _alternatives([*NUMBER_WORDS, *SCALE_WORDS])
_WORDS = rf"(?:{_WORD})(?:\s+(?:{_WORD}))*"
_FRACTION = _alternatives(FRACTIONS)
NUM_RE = (
    r"-?\d{1,3}(?:[   ]\d{3})+(?:,\d+)?(?![\d])|-?\d+(?:[,.]\d+)?"
    rf"|(?<![\w'])(?:мінус\s+)?(?:{_WORDS}(?:\s+(?:кома\s+{_WORDS}|з\s+половиною))?|{_FRACTION})(?![\w'])"
)
N = rf"({NUM_RE})"
"""A number in a grammar, as a group."""


def parse_num(s):
    """A number written or said, in any case: "1 000", "3,5", "триста сорок
    п'ять", "п'ятнадцяти", "три кома п'ять", "два з половиною", "півтори",
    "мінус два"; None."""
    s = " ".join(s.strip().lower().replace("’", "'").split())
    if not s:
        return None
    if re.fullmatch(r"-?\d{1,3}(?:[   ]\d{3})+(?:,\d+)?", s):
        return float(re.sub(r"[   ]", "", s).replace(",", "."))
    if re.fullmatch(r"-?\d+(?:[,.]\d+)?", s):
        return float(s.replace(",", "."))
    negative = s.startswith("мінус ")
    if negative:
        s = s.split(None, 1)[1]
    sign = -1 if negative else 1
    if s in FRACTIONS:
        return sign * FRACTIONS[s]
    if m := re.fullmatch(r"(.+?)\s+кома\s+(.+)", s):
        whole_part, words = whole(m.group(1).split()), m.group(2).split()
        zeros = 0
        while zeros < len(words) - 1 and words[zeros] in ("нуль", "ноль"):
            zeros += 1
        rest = whole(words[zeros:])
        if whole_part is None or rest is None:
            return None
        return sign * (whole_part + float(f"0.{'0' * zeros}{rest}"))
    if m := re.fullmatch(r"(.+?)\s+з\s+половиною", s):
        value = whole(m.group(1).split())
        return None if value is None else sign * (value + 0.5)
    value = whole(s.split())
    return None if value is None else sign * value


_DEGREES = {"f": "Фаренгейта", "c": "Цельсія", "k": "Кельвіна"}
_CURRENCY = {"$": "доларів", "€": "євро", "£": "фунтів стерлінгів", "₴": "гривень", "¥": "єн"}
_VULGAR = {"½": 0.5, "¼": 0.25, "¾": 0.75, "⅓": 1 / 3, "⅔": 2 / 3, "⅕": 0.2, "⅛": 0.125, "⅜": 0.375, "⅝": 0.625, "⅞": 0.875}
_SQUARE_UNITS = {"мм": "квадратних міліметрів", "см": "квадратних сантиметрів", "м": "квадратних метрів",
                 "км": "квадратних кілометрів", "mm": "квадратних міліметрів", "cm": "квадратних сантиметрів",
                 "m": "квадратних метрів", "km": "квадратних кілометрів"}


def _decimal(value: float) -> str:
    return f"{round(value, 4):g}".replace(".", ",")


def apostrophes(text: str) -> str:
    """"п’ять", "пʼять" as the model writes it: "п'ять"."""
    return re.sub(r"[’ʼ‘`´′]", "'", text)


def normalize(text: str) -> str:
    """A question as typed, or written by a speech-to-text, in the words the
    grammars read: "Скільки буде 12 × 8?" -> "Скільки буде 12 помножити на
    8?", "50°F" -> "50 градусів Фаренгейта", "1½ склянки" -> "1,5 склянки",
    "10 м²" -> "10 квадратних метрів", "5²" -> "5 у квадраті", "√144" ->
    "квадратний корінь з 144", "80 ₴" -> "80 гривень", "100км" -> "100 км"."""
    s = apostrophes(text)
    s = s.translate(str.maketrans({"“": '"', "”": '"', "„": '"', "«": '"', "»": '"'}))
    s = s.replace('"', " ")
    s = re.sub(r"(?<![^\W\d_])'|'(?![^\W\d_])", " ", s)
    s = re.sub(r"[    ]", " ", s)
    s = re.sub(r"[−‒–—]", "-", s)
    s = s.replace("℉", "°F").replace("℃", "°C")
    s = re.sub(r"\s*°\s*([FCKfckФЦКфцк])(?![^\W\d_])",
               lambda m: f" градусів {_DEGREES[{'ф': 'f', 'ц': 'c', 'к': 'k'}.get(m.group(1).lower(), m.group(1).lower())]}", s)
    s = re.sub(r"\s*°", " градусів", s)
    s = re.sub(r"(?<![^\W\d_])(мм|см|км|м|mm|cm|km|m)²", lambda m: _SQUARE_UNITS[m.group(1)], s)
    s = re.sub(r"\s*²", " у квадраті", s)
    s = re.sub(r"\s*³", " у кубі", s)
    vulgar = "".join(_VULGAR)
    s = re.sub(rf"(\d+)\s*([{vulgar}])", lambda m: _decimal(int(m.group(1)) + _VULGAR[m.group(2)]), s)
    s = re.sub(rf"[{vulgar}]", lambda m: f" {_decimal(_VULGAR[m.group(0)])} ", s)
    s = re.sub(r"\b(\d+)\s+(\d+)/(\d+)\b", lambda m: _decimal(int(m.group(1)) + int(m.group(2)) / int(m.group(3))), s)
    # Thousands as Ukrainian writes them: "31 536 000".
    s = re.sub(r"\b\d{1,3}(?: \d{3})+\b(?!,\d)", lambda m: m.group(0).replace(" ", ""), s)
    s = re.sub(r"(\d)\s*[xх×✕✖]\s*(\d)", r"\1 помножити на \2", s)
    s = re.sub(r"\s*[×✕✖·∙⋅]\s*", " помножити на ", s)
    s = re.sub(r"\s*÷\s*", " поділити на ", s)
    s = re.sub(r"√\s*", "квадратний корінь з ", s)
    s = re.sub(r"∛\s*", "кубічний корінь з ", s)
    s = re.sub(r"\s*=\s*\??\s*$", "", s)
    symbols = re.escape("".join(_CURRENCY))
    s = re.sub(rf"([{symbols}])\s*(\d[\d.,]*)", lambda m: f"{m.group(2)} {_CURRENCY[m.group(1)]}", s)
    s = re.sub(rf"(\d)\s*([{symbols}])", lambda m: f"{m.group(1)} {_CURRENCY[m.group(2)]}", s)
    s = re.sub(r"(\d)\s*грн\.?(?![^\W\d_])", r"\1 гривень", s)
    # A unit written on its number: "100км", "5кг".
    s = re.sub(r"(\d)([^\W\d_]+)", r"\1 \2", s)
    s = re.sub(r"\s+([?.!,])", r"\1", s)
    return re.sub(r"\s+", " ", s).strip()


def clean(q):
    """A question as the grammars read it: lowercase, no lead-in ("слухай,
    скажи, будь ласка"), no closing punctuation or "будь ласка", symbols as
    words."""
    s = apostrophes(q).lower().strip()
    s = re.sub(r"^(?:слухай|скажи|підкажи|скажіть|підкажіть|а|ну|ок|окей|гей|привіт)[,\s]+", "", s)
    s = re.sub(r"^(?:будь ласка,? |мені,? |ти не знаєш,? |не знаєш,? |чи знаєш,? |ти знаєш,? )+", "", s)
    s = re.sub(r"^(?:порахуй|обчисли|розрахуй|визнач|скажи)[,:]?\s+", "", s)
    s = re.sub(r"[?!.]+$", "", s).strip()
    s = re.sub(r",?\s+будь ласка$", "", s)
    s = re.sub(r"\s*[?!]+\s*|\.\s+(?=\D)", " ", s)
    s = s.replace("×", " помножити на ").replace("÷", " поділити на ").replace("%", " відсотків")
    s = re.sub(r"(\d)\s*\*\s*(\d)", r"\1 помножити на \2", s)
    s = re.sub(r"(\d)\s*/\s*(\d)", r"\1 поділити на \2", s)
    s = re.sub(r"(\d)\s*\+\s*(\d)", r"\1 плюс \2", s)
    s = re.sub(r"(\d)\s+-\s+(\d)", r"\1 мінус \2", s)
    s = re.sub(r"(\d)\s*\^\s*(\d)", r"\1 у степені \2", s)
    return re.sub(r"\s+", " ", s).strip()


def counted(number: str, forms: tuple[str, ...], gender: str = "m") -> tuple[str, str]:
    """A number as written ("453,6", "21", "31 536 000") with its noun in
    the form it takes (``forms``: after 1, after 2-4, after 5 and more, and
    after a fraction) -- as written, and as said: ("21 склянка", "двадцять
    одна склянка"), ("8,047 кілометра", "вісім кома нуль сорок сім
    кілометра")."""
    plain = re.sub(r"[   ]", "", number).replace(",", ".")
    value = float(plain)
    noun = plural(value, forms[0], forms[1], forms[2], forms[3] if len(forms) > 3 else None)
    written = f"{number} {noun}"
    if value.is_integer() and abs(value) < 1000:
        n = int(value)
        last = abs(n) % 100
        if gender != "m" and last % 10 in (1, 2) and last not in (11, 12):
            return written, f"{cardinal(n, gender)} {noun}"
        return written, written
    return written, said(written)


def said(text: str) -> str:
    """A sentence as the voice should read it: a decimal and a number from a
    thousand up in words."""
    return spoken(text)


def sentence(answer: str | None) -> str | None:
    """"96" -> "Це 96."; "Так" -> "Так."; "8,047" -> "Це вісім кома нуль сорок
    сім."."""
    if answer is None:
        return None
    answer = answer.strip()
    if answer in ("Так", "Ні"):
        return answer + "."
    if re.match(r"-?\d", answer):
        return f"Це {said(answer)}."
    answer = said(answer[:1].upper() + answer[1:])
    return answer if answer.endswith(".") else answer + "."
