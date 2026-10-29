"""How the Polish skills read numbers and questions, with English's names
(voice_labs/languages/en/skills/numbers.py), and say them.

Polish says a number in words as several ("trzysta czterdzieści pięć", "dwa
tysiące dwadzieścia cztery"), and declines them: a question may have any
case ("średnia z czterech, ośmiu i dwunastu", "pomnóż przez dwanaście"),
so every case reads as its number. A decimal comma ("3,5", "trzy przecinek
pięć"), halves said "i pół" ("dwa i pół"), "półtora". A noun after a number
takes its form by the number (``plural``): "1 minuta", "2 minuty", "5
minut", "22 minuty", "2,5 minuty".

A question typed may leave out its diacritics ("ile gramow ma funt"), so
the grammars read it folded (``fold``: one letter for one, so a match's
span is the same in both, and what is said back keeps them)."""

from __future__ import annotations

import re

DECIMAL = ","
"""8,047."""

THOUSANDS = ""
"""31536000: said whole, as a voice reads no separator Polish writes."""

_FOLD = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")


def fold(text: str) -> str:
    """Without diacritics, letter for letter: "ile gramów" -> "ile gramow"
    (for matching only)."""
    return text.translate(_FOLD)


def _table(rows: dict[int, str]) -> dict[str, int]:
    return {word: value for value, words in rows.items() for word in words.split()}


_ONES = _table({
    0: "zero zera zeru zerem", 1: "jeden jedna jedno jednego jednej jednemu jednym",
    2: "dwa dwie dwoch dwu dwom dwoma dwiema", 3: "trzy trzech trzem trzema", 4: "cztery czterech czterem czterema",
    5: "piec pieciu piecioma", 6: "szesc szesciu szescioma", 7: "siedem siedmiu siedmioma", 8: "osiem osmiu osmioma",
    9: "dziewiec dziewieciu dziewiecioma", 10: "dziesiec dziesieciu dziesiecioma",
    11: "jedenascie jedenastu jedenastoma", 12: "dwanascie dwunastu dwunastoma", 13: "trzynascie trzynastu trzynastoma",
    14: "czternascie czternastu czternastoma", 15: "pietnascie pietnastu pietnastoma",
    16: "szesnascie szesnastu szesnastoma", 17: "siedemnascie siedemnastu siedemnastoma",
    18: "osiemnascie osiemnastu osiemnastoma", 19: "dziewietnascie dziewietnastu dziewietnastoma",
})
_TENS = _table({
    20: "dwadziescia dwudziestu dwudziestoma", 30: "trzydziesci trzydziestu trzydziestoma",
    40: "czterdziesci czterdziestu czterdziestoma", 50: "piecdziesiat piecdziesieciu piecdziesiecioma",
    60: "szescdziesiat szescdziesieciu szescdziesiecioma", 70: "siedemdziesiat siedemdziesieciu siedemdziesiecioma",
    80: "osiemdziesiat osiemdziesieciu osiemdziesiecioma", 90: "dziewiecdziesiat dziewiecdziesieciu dziewiecdziesiecioma",
})
_HUNDREDS = _table({
    100: "sto stu stoma", 200: "dwiescie dwustu dwustoma", 300: "trzysta trzystu trzystoma",
    400: "czterysta czterystu czterystoma", 500: "piecset pieciuset", 600: "szescset szesciuset",
    700: "siedemset siedmiuset", 800: "osiemset osmiuset", 900: "dziewiecset dziewieciuset",
})
_SCALES = _table({
    1000: "tysiac tysiaca tysiace tysiecy tysiacem tysiacami tysiacach",
    10**6: "milion miliona miliony milionow milionem", 10**9: "miliard miliarda miliardy miliardow",
})

FRACTIONS = {
    "pol": 0.5, "poltora": 1.5, "poltorej": 1.5, "cwierc": 0.25, "jedna druga": 0.5, "jedna trzecia": 1 / 3,
    "dwie trzecie": 2 / 3, "jedna czwarta": 0.25, "trzy czwarte": 0.75, "jedna piata": 0.2, "jedna dziesiata": 0.1,
}
"""Parts said as words (folded): "pół", "półtora", "trzy czwarte"."""


def _alternatives(words) -> str:
    return "|".join(sorted((re.escape(w) for w in words), key=len, reverse=True))


_WORD = _alternatives([*_ONES, *_TENS, *_HUNDREDS, *_SCALES])
_WORDS = rf"(?:{_WORD})(?:\s+(?:{_WORD}))*"
_FRACTION = _alternatives(FRACTIONS)
NUM_RE = (
    r"-?\d+(?:[,.]\d+)?"
    rf"|(?<!\w)(?:minus\s+)?(?:{_WORDS}(?:\s+(?:przecinek\s+{_WORDS}|i\s+(?:pol|cwierc)))?|{_FRACTION})(?!\w)"
)
N = rf"({NUM_RE})"
"""A number in a grammar (read folded), as a group."""


def _whole(words: list[str]) -> int | None:
    """A whole number said in words (folded), in any case: "trzysta
    czterdziesci piec", "dwa tysiace dwadziescia cztery", "osmiu"."""
    total = current = 0
    seen = False
    for word in words:
        if word in _ONES:
            current += _ONES[word]
        elif word in _TENS:
            current += _TENS[word]
        elif word in _HUNDREDS:
            current += _HUNDREDS[word]
        elif word in _SCALES:
            total += (current or 1) * _SCALES[word]
            current = 0
        else:
            return None
        seen = True
    return total + current if seen else None


def parse_num(s):
    """A number written or said: "3,5", "trzysta czterdzieści pięć", "trzy
    przecinek pięć", "dwa i pół", "półtora", "trzy czwarte", "minus dwa",
    "ośmiu"; None."""
    s = fold(" ".join(str(s).lower().split()))
    if not s:
        return None
    if re.fullmatch(r"-?\d+(?:[,.]\d+)?", s):
        return float(s.replace(",", "."))
    sign = 1
    if s.startswith("minus "):
        sign, s = -1, s[len("minus "):]
    if s in FRACTIONS:
        return sign * FRACTIONS[s]
    if m := re.fullmatch(r"(.+?)\s+przecinek\s+(.+)", s):
        whole, words = _whole(m.group(1).split()), m.group(2).split()
        zeros = 0
        while zeros < len(words) - 1 and words[zeros] == "zero":
            zeros += 1
        rest = _whole(words[zeros:])
        if whole is None or rest is None:
            return None
        return sign * (whole + float(f"0.{'0' * zeros}{rest}"))
    if m := re.fullmatch(r"(.+?)\s+i\s+(pol|cwierc)", s):
        whole = _whole(m.group(1).split())
        return None if whole is None else sign * (whole + FRACTIONS[m.group(2)])
    whole = _whole(s.split())
    return None if whole is None else sign * whole


# ------------------------------------------------------------- ordinals

_ORDINAL_STEMS = {
    1: "pierwsz", 2: "drug", 3: "trzec", 4: "czwart", 5: "piąt", 6: "szóst", 7: "siódm", 8: "ósm", 9: "dziewiąt",
    10: "dziesiąt", 11: "jedenast", 12: "dwunast", 13: "trzynast", 14: "czternast", 15: "piętnast", 16: "szesnast",
    17: "siedemnast", 18: "osiemnast", 19: "dziewiętnast", 20: "dwudziest", 30: "trzydziest", 40: "czterdziest",
    50: "pięćdziesiąt", 60: "sześćdziesiąt", 70: "siedemdziesiąt", 80: "osiemdziesiąt", 90: "dziewięćdziesiąt",
    100: "setn", 200: "dwusetn", 300: "trzechsetn", 400: "czterechsetn", 500: "pięćsetn", 600: "sześćsetn",
    700: "siedemsetn", 800: "osiemsetn", 900: "dziewięćsetn",
}
_ENDINGS = {
    # By case and gender: hard stems, "drug" (-i-, but "druga"), "trzec" (-i- throughout).
    "nom": ("y", "i", "i"), "gen": ("ego", "iego", "iego"),
    "nom_f": ("a", "a", "ia"), "loc_f": ("ej", "iej", "iej"), "acc_f": ("ą", "ą", "ią"),
}


def _ordinal_word(value: int, case: str) -> str:
    stem = _ORDINAL_STEMS[value]
    kind = 1 if stem == "drug" else 2 if stem == "trzec" else 0
    return stem + _ENDINGS[case][kind]


def _ordinal_small(value: int, case: str) -> str:
    """1..999 as an ordinal: "dwudziesty piąty", "sto dwudziesty"."""
    hundreds, rest = divmod(value, 100)
    if not rest:
        return _ordinal_word(hundreds * 100, case)
    said = [spell(hundreds * 100)] if hundreds else []
    if rest < 20 or rest % 10 == 0:
        return " ".join([*said, _ordinal_word(rest, case)])
    return " ".join([*said, _ordinal_word(rest - rest % 10, case), _ordinal_word(rest % 10, case)])


_THOUSANDTH = {1: "", 2: "dwu", 3: "trzy", 4: "cztero", 5: "pięcio", 6: "sześcio", 7: "siedmio", 8: "ośmio",
               9: "dziewięcio"}


def ordinal(value: int, case: str = "nom") -> str:
    """An ordinal in words, masculine in ``case`` ("nom", "gen") or feminine
    ("nom_f", "loc_f", "acc_f"): "dwudziesty piąty", "dwudziestego
    piątego", "dwa tysiące dwudziestego szóstego", "siódmej"."""
    thousands, rest = divmod(value, 1000)
    if thousands and not rest:
        ending = {"nom": "y", "gen": "ego", "nom_f": "a", "loc_f": "ej", "acc_f": "ą"}[case]
        return f"{_THOUSANDTH.get(thousands, spell(thousands) + ' ')}tysięczn{ending}"
    said = [spell(thousands * 1000)] if thousands else []
    return " ".join([*said, _ordinal_small(rest, case)])


_ORDINAL_FOLDED = {fold(stem): value for value, stem in _ORDINAL_STEMS.items() if value <= 30}
_ORDINAL_ENDING = re.compile(r"^(.+?)(iego|ego|iemu|emu|iej|ej|ich|ych|im|ym|ia|ie|a|e|y|i)$")


def read_ordinal(text: str) -> int | None:
    """An ordinal in words, any case or gender (folded or not): "piąty",
    "dwudziestego piątego", "siódmej", "trzecią" -> 1..39; None."""
    values = []
    for word in fold(text.lower()).split():
        m = _ORDINAL_ENDING.match(word)
        value = _ORDINAL_FOLDED.get(m.group(1)) if m else None
        if value is None:
            return None
        values.append(value)
    if len(values) == 1:
        return values[0]
    if len(values) == 2 and values[0] in (20, 30) and values[1] < 10:
        return values[0] + values[1]
    return None


# ------------------------------------------------------------ in words

_SAY_ONES = [
    "zero", "jeden", "dwa", "trzy", "cztery", "pięć", "sześć", "siedem", "osiem", "dziewięć", "dziesięć", "jedenaście",
    "dwanaście", "trzynaście", "czternaście", "piętnaście", "szesnaście", "siedemnaście", "osiemnaście", "dziewiętnaście",
]
_SAY_TENS = ["", "", "dwadzieścia", "trzydzieści", "czterdzieści", "pięćdziesiąt", "sześćdziesiąt", "siedemdziesiąt",
             "osiemdziesiąt", "dziewięćdziesiąt"]
_SAY_HUNDREDS = ["", "sto", "dwieście", "trzysta", "czterysta", "pięćset", "sześćset", "siedemset", "osiemset", "dziewięćset"]


def plural(count: float, one: str, few: str, many: str, part: str | None = None) -> str:
    """The form a noun takes after ``count``: "minuta" (1), "minuty" (2-4,
    22-24, ... not 12-14), "minut" (0, 5-21, 25 ...), "minuty" (``part``,
    after a fraction: 2,5 minuty)."""
    if count != int(count):
        return part or many
    count = abs(int(count))
    if count == 1:
        return one
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return few
    return many


def _hundreds(value: int) -> list[str]:
    hundreds, rest = divmod(value, 100)
    said = [_SAY_HUNDREDS[hundreds]] if hundreds else []
    if rest >= 20:
        said.append(_SAY_TENS[rest // 10])
        if rest % 10:
            said.append(_SAY_ONES[rest % 10])
    elif rest:
        said.append(_SAY_ONES[rest])
    return said


def spell(value: int, feminine: bool = False) -> str:
    """A whole number in words, as counted ("dwadzieścia dwa", "dwa tysiące
    dwadzieścia sześć"); ``feminine``, before a feminine noun: "jedna",
    "dwie", "dwadzieścia dwie" ("dwadzieścia jeden" stays)."""
    if value < 0:
        return "minus " + spell(-value, feminine)
    if value == 0:
        return "zero"
    if feminine and value == 1:
        return "jedna"
    said: list[str] = []
    for scale, forms in ((10**9, ("miliard", "miliardy", "miliardów")), (10**6, ("milion", "miliony", "milionów")),
                         (1000, ("tysiąc", "tysiące", "tysięcy"))):
        count, value = divmod(value, scale)
        if count:
            said += [forms[0]] if count == 1 else [*_hundreds(count), plural(count, *forms)]
    said += _hundreds(value)
    if feminine and value % 10 == 2 and value % 100 != 12:
        said[-1] = "dwie"
    return " ".join(said)


def count(value: float, one: str, few: str, many: str, part: str | None = None, feminine: bool = False) -> str:
    """A number and its noun, as a voice should read it: "5 minut", "22
    stopnie", "2,5 minuty"; before a feminine noun, a number a voice would
    read in the masculine is written out ("jedna minuta", "dwie szklanki",
    "dwadzieścia dwie minuty")."""
    noun = plural(value, one, few, many, part)
    whole = value == int(value)
    if whole and feminine and (int(value) == 1 or (int(value) % 10 == 2 and int(value) % 100 != 12)) and abs(value) < 10**6:
        return f"{spell(int(value), feminine=True)} {noun}"
    number = str(int(value)) if whole else f"{round(value, 4):g}".replace(".", DECIMAL)
    return f"{number} {noun}"


# ------------------------------------------------------------ questions

_DEGREES = {"f": "Fahrenheita", "c": "Celsjusza", "k": "Kelvina"}
_CURRENCY = {"$": "dolarów", "€": "euro", "£": "funtów", "¥": "jenów"}
_VULGAR = {"½": 0.5, "¼": 0.25, "¾": 0.75, "⅓": 1 / 3, "⅔": 2 / 3, "⅕": 0.2, "⅛": 0.125, "⅜": 0.375, "⅝": 0.625, "⅞": 0.875}
_SQUARE_UNITS = {"mm": "milimetrów kwadratowych", "cm": "centymetrów kwadratowych", "m": "metrów kwadratowych",
                 "km": "kilometrów kwadratowych"}


def _decimal(value: float) -> str:
    return f"{round(value, 4):g}".replace(".", ",")


def normalize(text: str) -> str:
    """A question as typed, or written by a speech-to-text, in the words the
    grammars read: "Ile to jest 12 × 8?" -> "Ile to jest 12 razy 8?",
    "50°F" -> "50 stopni Fahrenheita", "1½ szklanki" -> "1,5 szklanki", "10
    m²" -> "10 metrów kwadratowych", "5²" -> "5 do kwadratu", "√144" ->
    "pierwiastek z 144", "80 zł" -> "80 złotych", "100km" -> "100 km"."""
    s = text
    s = s.translate(str.maketrans({"’": "'", "‘": "'", "‛": "'", "`": "'", "´": "'", "“": '"', "”": '"', "„": '"',
                                   "«": '"', "»": '"'}))
    s = s.replace('"', " ")
    s = re.sub(r"(?<![^\W\d_])'|'(?![^\W\d_])", " ", s)
    s = re.sub(r"[    ]", " ", s)
    s = re.sub(r"[−‒–—]", "-", s)
    s = s.replace("℉", "°F").replace("℃", "°C")
    s = re.sub(r"\s*°\s*([FCKfck])\b", lambda m: f" stopni {_DEGREES[m.group(1).lower()]}", s)
    s = re.sub(r"\s*°", " stopni", s)
    s = re.sub(r"\b(mm|cm|km|m)²", lambda m: _SQUARE_UNITS[m.group(1)], s)
    s = re.sub(r"\s*²", " do kwadratu", s)
    s = re.sub(r"\s*³", " do sześcianu", s)
    vulgar = "".join(_VULGAR)
    s = re.sub(rf"(\d+)\s*([{vulgar}])", lambda m: _decimal(int(m.group(1)) + _VULGAR[m.group(2)]), s)
    s = re.sub(rf"[{vulgar}]", lambda m: f" {_decimal(_VULGAR[m.group(0)])} ", s)
    s = re.sub(r"\b(\d+)\s+(\d+)/(\d+)\b", lambda m: _decimal(int(m.group(1)) + int(m.group(2)) / int(m.group(3))), s)
    s = re.sub(r"(\d)\s*[x×✕✖]\s*(\d)", r"\1 razy \2", s)
    s = re.sub(r"\s*[×✕✖·∙⋅]\s*", " razy ", s)
    s = re.sub(r"\s*÷\s*", " przez ", s)
    s = re.sub(r"(\d)\s+:\s+(\d)", r"\1 przez \2", s)  # "12 : 4", not a time of day
    s = re.sub(r"√\s*", "pierwiastek z ", s)
    s = re.sub(r"∛\s*", "pierwiastek sześcienny z ", s)
    s = re.sub(r"\s*=\s*\??\s*$", "", s)
    symbols = re.escape("".join(_CURRENCY))
    s = re.sub(rf"(?:US)?([{symbols}])\s*(\d[\d.,]*)", lambda m: f"{m.group(2)} {_CURRENCY[m.group(1)]}", s)
    s = re.sub(rf"(\d)\s*([{symbols}])", lambda m: f"{m.group(1)} {_CURRENCY[m.group(2)]}", s)
    s = re.sub(r"(\d)\s*(?:zł|PLN)(?![^\W\d_])", r"\1 złotych", s)
    # A unit written on its number: "100km", "5kg".
    s = re.sub(r"(\d)([^\W\d_]+)\b", r"\1 \2", s)
    s = re.sub(r"\s+([?.!,])", r"\1", s)
    return re.sub(r"\s+", " ", s).strip()


def clean(q):
    """A question as the grammars read it: lowercase, no lead-in ("hej,
    powiedz mi"), no closing punctuation or "proszę", symbols as words."""
    s = q.lower().strip()
    s = re.sub(r"^(?:hej|ok|okej|no|dobra|słuchaj|halo)[,\s]+", "", s)
    s = re.sub(
        r"^(?:czy )?(?:możesz mi powiedzieć|mógłbyś mi powiedzieć|mogłabyś mi powiedzieć|powiedz mi|powiedz"
        r"|czy wiesz|wiesz|proszę)?,?\s*(?:oblicz|policz|wylicz)?,?\s*",
        "",
        s,
    )
    s = re.sub(r"[?!.]+$", "", s).strip()
    s = re.sub(r",?\s+proszę$", "", s)
    s = re.sub(r"\s*[?!]+\s*|\.\s+(?=\D)", " ", s)
    s = s.replace("×", " razy ").replace("÷", " przez ").replace("%", " procent")
    s = re.sub(r"(\d)\s*\*\s*(\d)", r"\1 razy \2", s)
    s = re.sub(r"(\d)\s*/\s*(\d)", r"\1 przez \2", s)
    s = re.sub(r"(\d)\s*\+\s*(\d)", r"\1 plus \2", s)
    s = re.sub(r"(\d)\s+-\s+(\d)", r"\1 minus \2", s)
    s = re.sub(r"(\d)\s*\^\s*(\d)", r"\1 do potęgi \2", s)
    return re.sub(r"\s+", " ", s).strip()


def sentence(answer: str | None) -> str | None:
    """"96" -> "To 96."; "Tak" -> "Tak."; "sobota" -> "To sobota."."""
    if answer is None:
        return None
    answer = answer.strip()
    if answer in ("Tak", "Nie"):
        return answer + "."
    answer = answer if answer.endswith(".") else answer + "."
    return f"To {answer}"
