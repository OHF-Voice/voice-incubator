"""Quick questions, when no command matched: answered exactly, or by a small LLM.

Ported from the smart-home quick-questions benchmark (llama.cpp's
smart-home-qa-bench: router.py and its results; README "Questions"):

1. **Exact** -- spelling, arithmetic, unit conversions, calendar and clock
   arithmetic -- by grammars that must parse the whole question *and* compute
   an answer; a partial parse falls through rather than guessing. On 180
   held-out questions none of their answers was wrong.
2. **Live** -- anything about now or here (weather, news, scores, prices,
   what's open): this app cannot look things up, and says so.
3. **Knowledge** -- the rest, when it reads as a question: a small LLM in one
   short sentence (Granite 4.0 350M: 128 of 148 such questions right, ~0.2 s
   on a desktop CPU).

The order is as in the benchmark's router: spelling first ("how do you spell
weather" is not about the weather), then live, then the other grammars.
"""

from __future__ import annotations

import calendar
import datetime as dt
import logging
import math
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)

# --------------------------------------------------------------------------------------------
# numbers
# --------------------------------------------------------------------------------------------
_UNITS = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
          "sixteen seventeen eighteen nineteen").split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()
WORD_NUM = {w: i for i, w in enumerate(_UNITS)} | {w: 10 * i for i, w in enumerate(_TENS) if w != "_"}
SCALES = {"hundred": 100, "thousand": 1000, "million": 10**6}
FRACTIONS = {"half": 0.5, "a half": 0.5, "one half": 0.5, "quarter": 0.25, "a quarter": 0.25, "third": 1 / 3,
             "a third": 1 / 3, "one third": 1 / 3, "three quarters": 0.75, "two thirds": 2 / 3, "a tenth": 0.1}
ORDINAL_POW = {"square": 2, "squared": 2, "second": 2, "cube": 3, "cubed": 3, "third": 3, "fourth": 4, "fifth": 5,
               "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10}

NUM_RE = r"-?\d[\d,]*(?:\.\d+)?|(?:(?:a|an|zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|and|half|quarter|negative|minus)[\s-]*)+"


def parse_num(s):
    s = s.strip().lower().replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        pass
    neg = s.startswith(("negative ", "minus "))
    if neg:
        s = s.split(" ", 1)[1]
    m = re.fullmatch(r"(.+?)\s+and\s+(a half|a quarter|three quarters)", s)
    if m:
        a = parse_num(m.group(1))
        return None if a is None else (-1 if neg else 1) * (a + FRACTIONS[m.group(2)])
    if s in FRACTIONS:
        return FRACTIONS[s]
    year = _spoken_year(s)
    if year is not None:
        return year
    total = cur = 0
    seen = False
    for w in re.split(r"[\s-]+", s):
        if w in ("and",):
            continue
        if w in ("a", "an"):
            cur = cur or 1
            seen = True
        elif w in WORD_NUM:
            cur += WORD_NUM[w]
            seen = True
        elif w == "hundred":
            cur = (cur or 1) * 100
            seen = True
        elif w in SCALES:
            total += (cur or 1) * SCALES[w]
            cur = 0
            seen = True
        else:
            return None
    if not seen:
        return None
    return (-1 if neg else 1) * (total + cur)


def _spoken_year(s):
    """"twenty twenty four" -> 2024, "nineteen eighty four" -> 1984, "twenty
    oh five" -> 2005: a year said in pairs, as a transcript writes it."""
    words = re.split(r"[\s-]+", s)
    if len(words) < 2 or words[0] not in WORD_NUM or not 10 <= WORD_NUM[words[0]] <= 20:
        return None
    rest = words[1:]
    if rest[0] == "oh" and len(rest) == 2 and rest[1] in WORD_NUM and WORD_NUM[rest[1]] < 10:
        return WORD_NUM[words[0]] * 100 + WORD_NUM[rest[1]]
    if rest[0] not in WORD_NUM or WORD_NUM[rest[0]] < 10:
        return None
    low = WORD_NUM[rest[0]] + (WORD_NUM[rest[1]] if len(rest) == 2 and WORD_NUM.get(rest[1], 10) < 10 else 0)
    if len(rest) > 2 or (len(rest) == 2 and WORD_NUM.get(rest[1], 10) >= 10) or WORD_NUM[rest[0]] % 10 and len(rest) == 2:
        return None
    return WORD_NUM[words[0]] * 100 + low


def fmt(x):
    if isinstance(x, float) and x.is_integer():
        x = int(x)
    if isinstance(x, float):
        return f"{x:.4g}" if abs(x) < 1e6 else f"{x:,.0f}"
    return f"{x:,}" if abs(x) >= 10000 else str(x)


# --------------------------------------------------------------------------------------------
# live / online detection
# --------------------------------------------------------------------------------------------
LIVE_PATTERNS = [
    r"\b(weather|forecast|temperature outside|outside|rain(ing)?|snow(ing)?|sunny|cloudy|windy|wind|humid(ity)?|umbrella|jacket|coat|storm|thunder|hail|frost|uv index|air quality|aqi|pollen|smoke)\b",
    r"\b(sunset|sunrise|dawn|dusk|moon ?rise|high tide|low tide)\b",
    r"\b(news|headlines?|breaking)\b",
    r"\b(score|scores|standings|playoffs?|lineup|kick ?off|tip ?off|game tonight|play next|next (game|match|race)|leading|on tv)\b",
    r"\b(who won|won the|winner of) (the )?(last|latest|most recent|recent|this|yesterday'?s?|today'?s?)",
    r"\b(latest|most recent|newest|upcoming|this (week|weekend|year|season)|tonight|tomorrow|yesterday|last night)\b",
    r"\b(right now|currently|at the moment|these days|so far)\b",
    r"\b(today|today's)\b",
    r"\bcurrent (price|rate|president|prime minister|ceo|leader|population|champion|mayor|governor|status|time|date|exchange)",
    r"\bwho(?: is|'s|s) (the )?(current |now )?(president|prime minister|chancellor|king|queen|pope|ceo|mayor|governor|speaker)\b",
    # Spoken, and found missing on the held-out questions (README "Questions").
    r"\b(won|win|winner|champions?|happened)\b.*\blast (year|season|week|month)\b",
    r"\bdid (the )?[a-z ]+ (win|lose)\b",
    r"\b(euros?|dollars?|pounds?|yen|pesos?)\b.*\b(euros?|dollars?|pounds?|yen|pesos?)\b",
    r"\bwhat time (?:does|do|is) .+ (open|close|closing)\b",
    r"\b(price|prices|cost of|stock|stocks|shares?|bitcoin|crypto|exchange rate|in (euros|dollars|pounds|yen)|gas prices?|gasoline|mortgage rates?)\b",
    r"\b(near me|nearby|around here|closest .* to me|in my area|local)\b",
    r"\b(open|close|closed|hours)\b.*\b(store|shop|restaurant|pharmacy|bank|library|post office|target|walmart|costco)\b",
    r"\b(store|shop|restaurant|pharmacy|bank|library|post office|target|walmart|costco)\b.*\b(open|closed?|hours)\b",
    r"\b(my|the next) flight\b|\bflights? (status|delayed|on time)\b",
    r"\bnext [a-z0-9 ]*(game|match|race|fight|launch|episode|eclipse)\b",
    r"\b(traffic|commute|delays?|launch|canceled|cancelled|school closings?|playing at)\b",
    r"\bwhat time is it\b|\bwhat('?s| is) (the )?(date|time)\b|\bwhat day is it\b",
    r"\bhow (did|are|is) the [a-z ]+ (do|doing|playing)\b",
]
# words that look live but are not, in static questions
LIVE_EXCEPTIONS = [
    r"\b(alternating|direct|electric(al)?) current\b",
    r"\bunit of (electric(al)? )?current\b",
    r"\bocean currents?\b",
    r"\bwhat does .* mean\b",
    r"\bhow do you (spell|say)\b",
    r"\b(what is|what's) (a|an) [a-z]+\??$",
]


def is_live(q):
    # A transcript's commas split phrases apart: "the next formula, one race".
    s = re.sub(r"[,;]", " ", q.lower())
    for p in LIVE_EXCEPTIONS:
        s = re.sub(p, " ", s)
    return any(re.search(p, s) for p in LIVE_PATTERNS)


# --------------------------------------------------------------------------------------------
# deterministic solvers. Each returns an answer string or None.
# --------------------------------------------------------------------------------------------
def _clean(q):
    s = q.lower().strip()
    s = re.sub(r"^(hey|ok|okay|so|um|uh)[,\s]+", "", s)
    s = re.sub(r"^(can you |could you |please |would you )?(tell me |calculate |compute |work out |figure out )?", "", s)
    s = re.sub(r"[?!.]+$", "", s).strip()
    # A transcript breaks a spoken question up: "the average of four? eight and twelve".
    s = re.sub(r"\s*[?!]+\s+|\.\s+", " ", s)
    s = s.replace("×", " times ").replace("÷", " divided by ").replace("%", " percent")
    s = re.sub(r"(\d)\s*\*\s*(\d)", r"\1 times \2", s)
    s = re.sub(r"(\d)\s*/\s*(\d)", r"\1 divided by \2", s)
    s = re.sub(r"(\d)\s*\+\s*(\d)", r"\1 plus \2", s)
    s = re.sub(r"(\d)\s+-\s+(\d)", r"\1 minus \2", s)
    s = re.sub(r"(\d)\s*\^\s*(\d)", r"\1 to the power of \2", s)
    return re.sub(r"\s+", " ", s)


N = rf"({NUM_RE})"


def solve_arithmetic(q):
    s = _clean(q)
    s = re.sub(r"^(what|what's|whats|how much) (is|are)? ?", "", s)
    s = re.sub(r"^(what's|whats) ", "", s)
    s = re.sub(r"\bmultiplied by\b|\bx\b", "times", s)
    s = re.sub(r"\bover\b", "divided by", s)
    s = re.sub(r"\bthe (sum|total) of\b", "sum of", s).strip()

    if m := re.fullmatch(rf"the square root of {N}", s) or re.fullmatch(rf"square root of {N}", s):
        v = parse_num(m.group(1))
        return None if v is None or v < 0 else fmt(math.sqrt(v))
    if m := re.fullmatch(rf"(?:the )?cube root of {N}", s):
        v = parse_num(m.group(1))
        return None if v is None else fmt(round(math.copysign(abs(v) ** (1 / 3), v), 6))
    if m := re.fullmatch(rf"{N} factorial|(?:the )?factorial of {N}", s):
        v = parse_num(m.group(1) or m.group(2))
        return fmt(math.factorial(int(v))) if v is not None and v.is_integer() and 0 <= v <= 20 else None
    if m := re.fullmatch(rf"{N} (squared|cubed)", s):
        v = parse_num(m.group(1))
        return None if v is None else fmt(v ** ORDINAL_POW[m.group(2)])
    if m := re.fullmatch(rf"{N} (?:to the|raised to the) (?:power of )?(\w+)(?: power)?", s):
        v = parse_num(m.group(1))
        e = ORDINAL_POW.get(m.group(2)) or parse_num(re.sub(r"(st|nd|rd|th)$", "", m.group(2)) or "")
        return None if v is None or e is None or e > 64 else fmt(v ** e)
    if m := re.fullmatch(rf"{N} percent of {N}", s):
        a, b = parse_num(m.group(1)), parse_num(m.group(2))
        return None if a is None or b is None else fmt(round(a * b / 100, 6))
    if m := re.fullmatch(rf"(half|a half|one half|a quarter|quarter|a third|one third|two thirds|three quarters|a tenth) of {N}", s):
        b = parse_num(m.group(2))
        return None if b is None else fmt(round(FRACTIONS[m.group(1)] * b, 6))
    if m := re.fullmatch(r"(?:the )?(?:average|mean) of (.+)", s):
        parts = [p for p in re.split(r",?\s+and\s+|,\s*|\s+", m.group(1)) if p and p != "and"]
        vals = [parse_num(p) for p in parts]
        if len(vals) >= 2 and None not in vals:
            return fmt(round(sum(vals) / len(vals), 6))
        return None
    if m := re.fullmatch(r"sum of (.+)", s):
        vals = [parse_num(p) for p in re.split(r",?\s+and\s+|,\s*", m.group(1)) if p]
        return fmt(sum(vals)) if len(vals) >= 2 and None not in vals else None
    # chains of + - * /
    ops = {"plus": "+", "add": "+", "minus": "-", "less": "-", "take away": "-", "times": "*", "divided by": "/"}
    tokens = re.split(r"\s+(plus|minus|times|divided by|take away)\s+", s)
    if len(tokens) >= 3 and len(tokens) % 2 == 1:
        vals = [parse_num(t) for t in tokens[0::2]]
        if None in vals:
            return None
        expr = "".join(f"{v!r}{ops[o]}" for v, o in zip(vals, tokens[1::2])) + repr(vals[-1])
        try:
            return fmt(round(eval(expr, {"__builtins__": {}}), 6))  # expr only has numbers and + - * /
        except ZeroDivisionError:
            return None
    return None


# unit -> (dimension, size in base unit)
UNITS = {
    # length (m)
    "millimeter": ("len", 0.001), "centimeter": ("len", 0.01), "meter": ("len", 1), "kilometer": ("len", 1000),
    "inch": ("len", 0.0254), "foot": ("len", 0.3048), "yard": ("len", 0.9144), "mile": ("len", 1609.344),
    # area (m^2)
    "square foot": ("area", 0.09290304), "square yard": ("area", 0.83612736), "square meter": ("area", 1),
    "square inch": ("area", 0.00064516), "acre": ("area", 4046.8564224), "square mile": ("area", 2589988.110336),
    "hectare": ("area", 10000), "square kilometer": ("area", 1e6),
    # volume (mL), US customary
    "milliliter": ("vol", 1), "liter": ("vol", 1000), "teaspoon": ("vol", 4.92892159375), "tablespoon": ("vol", 14.78676478125),
    "fluid ounce": ("vol", 29.5735295625), "cup": ("vol", 236.5882365), "pint": ("vol", 473.176473),
    "quart": ("vol", 946.352946), "gallon": ("vol", 3785.411784),
    # mass (g)
    "milligram": ("mass", 0.001), "gram": ("mass", 1), "kilogram": ("mass", 1000), "ounce": ("mass", 28.349523125),
    "pound": ("mass", 453.59237), "ton": ("mass", 907184.74), "metric ton": ("mass", 1e6), "stone": ("mass", 6350.29318),
    # time (s)
    "millisecond": ("time", 0.001), "second": ("time", 1), "minute": ("time", 60), "hour": ("time", 3600), "day": ("time", 86400),
    "week": ("time", 604800), "fortnight": ("time", 1209600), "year": ("time", 31536000), "decade": ("time", 315360000),
    "century": ("time", 3153600000),
    # data (bytes)
    "byte": ("data", 1), "kilobyte": ("data", 1000), "megabyte": ("data", 1e6), "gigabyte": ("data", 1e9), "terabyte": ("data", 1e12),
}
ALIASES = {"mm": "millimeter", "cm": "centimeter", "m": "meter", "km": "kilometer", "feet": "foot", "ft": "foot", "in": "inch",
           "inches": "inch", "yd": "yard", "mi": "mile", "ml": "milliliter", "l": "liter", "litre": "liter", "tsp": "teaspoon",
           "tbsp": "tablespoon", "oz": "ounce", "fl oz": "fluid ounce", "lb": "pound", "lbs": "pound", "g": "gram", "kg": "kilogram",
           "kilo": "kilogram", "kilos": "kilogram", "mg": "milligram", "sec": "second", "secs": "second", "min": "minute",
           "mins": "minute", "hr": "hour", "hrs": "hour", "centuries": "century", "square feet": "square foot",
           "tonne": "metric ton", "tonnes": "metric ton"}


def unit(s):
    s = s.strip().lower()
    s = re.sub(r"^(a|an|one|the|us|u\.s\.|american|imperial)\s+", "", s)
    s = re.sub(r"^(us|u\.s\.)\s+", "", s)
    s = re.sub(r"\s+(in us cooking|in cooking|us)$", "", s)
    s = s.replace("metre", "meter").replace("litre", "liter")
    if s in ALIASES:
        return ALIASES[s]
    for cand in (s, re.sub(r"ies$", "y", s), re.sub(r"es$", "", s), re.sub(r"s$", "", s)):
        if cand in UNITS:
            return cand
        if cand in ALIASES:
            return ALIASES[cand]
    return None


def convert(v, a, b):
    if a == b or UNITS[a][0] != UNITS[b][0]:
        return None
    r = v * UNITS[a][1] / UNITS[b][1]
    return round(r, 4) if abs(r) < 100 else round(r, 1)


def plural(u, v):
    if v == 1:
        return u
    return {"foot": "feet", "inch": "inches", "century": "centuries", "square foot": "square feet"}.get(u, u + "s")


def solve_units(q):
    s = _clean(q)
    s = re.sub(r"\s+(in us cooking|in cooking)$", "", s)
    temp = re.fullmatch(rf"(?:what(?:'s| is)|convert|how much is|how hot is|how cold is)?\s*{N} (?:degrees? )?(celsius|centigrade|fahrenheit|c|f|kelvin)\s+(?:in|to|into|as)\s+(?:degrees? )?(celsius|centigrade|fahrenheit|kelvin)", s)
    if temp:
        v = parse_num(temp.group(1))
        a, b = temp.group(2)[0], temp.group(3)[0]
        if v is None or a == b:
            return None
        c = (v - 32) * 5 / 9 if a == "f" else v - 273.15 if a == "k" else v
        out = c * 9 / 5 + 32 if b == "f" else c + 273.15 if b == "k" else c
        name = {"f": "Fahrenheit", "c": "Celsius", "k": "kelvin"}[b]
        return f"{fmt(round(out, 1))} degrees {name}"
    m = (re.fullmatch(r"how many (.+?) (?:are )?(?:there )?(?:in|per|make up|make|in a single) (?:a |an |one )?(.+)", s)
         or re.fullmatch(r"how many (.+?) (?:is|are) (?:in )?(?:a |an |one )?(.+)", s))
    if m:
        b, rest = m.group(1), m.group(2)
        n = 1.0
        if mm := re.fullmatch(rf"{N}\s+(.+)", rest):
            n2 = parse_num(mm.group(1))
            if n2 is not None:
                n, rest = n2, mm.group(2)
        ua, ub = unit(rest), unit(b)
        if ua and ub:
            r = convert(n, ua, ub)
            return None if r is None else fmt(r)
        return None
    m = re.fullmatch(rf"(?:what(?:'s| is)|convert|how much is|how many [a-z ]+ is)\s*{N}\s+(.+?)\s+(?:in|to|into|as)\s+(.+)", s)
    if m:
        v, ua, ub = parse_num(m.group(1)), unit(m.group(2)), unit(m.group(3))
        if v is not None and ua and ub:
            r = convert(v, ua, ub)
            return None if r is None else f"{fmt(r)} {plural(ub, r)}"
    return None


MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
DAYS = [d.lower() for d in calendar.day_name]
FIXED_HOLIDAYS = {"christmas": (12, 25), "christmas day": (12, 25), "christmas eve": (12, 24), "new year's day": (1, 1),
                  "new years day": (1, 1), "new year's eve": (12, 31), "new years eve": (12, 31), "halloween": (10, 31),
                  "valentine's day": (2, 14), "valentines day": (2, 14), "independence day": (7, 4), "the fourth of july": (7, 4),
                  "st. patrick's day": (3, 17), "saint patrick's day": (3, 17), "st patrick's day": (3, 17),
                  "veterans day": (11, 11), "juneteenth": (6, 19), "april fools' day": (4, 1), "april fools day": (4, 1)}


def leap(y):
    return calendar.isleap(y)


def parse_clock(s):
    s = s.strip().lower().replace(".", "")
    if s in ("noon", "midday"):
        return 12 * 60
    if s == "midnight":
        return 0
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm|a m|p m)?", s)
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").replace(" ", "")
    if h > 23 or mi > 59 or (ap and h > 12):
        return None
    if ap == "pm" and h != 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    return h * 60 + mi


def _clock_digits(s):
    """"two pm" -> "2 pm", "seven thirty am" -> "7:30 am", for parse_clock."""
    words = s.strip().split()
    meridiem = [w for w in words if w.replace(".", "") in ("am", "pm")]
    numbers = [w for w in words if w not in meridiem]
    if not numbers or any(w not in WORD_NUM for w in numbers):
        return s
    hour = WORD_NUM[numbers[0]]
    minute = sum(WORD_NUM[w] for w in numbers[1:])
    return f"{hour}:{minute:02d} {' '.join(meridiem)}".strip()


def show_clock(mins):
    mins %= 1440
    h, m = divmod(mins, 60)
    return f"{(h % 12) or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"


def parse_date(s):
    s = s.strip().lower().replace(",", "")
    s = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", s)
    m = re.fullmatch(r"(?:the )?(\d{1,2}) (?:of )?([a-z]+) (\d{4})", s) or None
    if m:
        d, mo, y = int(m.group(1)), MONTHS.get(m.group(2)), int(m.group(3))
    else:
        m = re.fullmatch(r"([a-z]+) (\d{1,2}) (\d{4})", s)
        if not m:
            return None
        mo, d, y = MONTHS.get(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return dt.date(y, mo, d) if mo else None
    except ValueError:
        return None


def _day_of(text, today):
    """The next date a holiday or "month day" falls on, from ``today``."""
    text = re.sub(r"^the ", "", text.strip().lower()) if not text.strip().lower().startswith("the fourth") else text.strip().lower()
    text = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", text)
    found = FIXED_HOLIDAYS.get(text)
    if found is None:
        m = re.fullmatch(r"([a-z]+) (\d{1,2})", text) or re.fullmatch(r"(\d{1,2}) (?:of )?([a-z]+)", text)
        if not m:
            return None
        month, day = (m.group(1), m.group(2)) if m.group(1).isalpha() else (m.group(2), m.group(1))
        if month not in MONTHS:
            return None
        found = (MONTHS[month], int(day))
    for year in (today.year, today.year + 1):
        try:
            date = dt.date(year, *found)
        except ValueError:
            return None
        if date >= today:
            return date
    return None


def solve_calendar(q):
    s = _clean(q)
    if m := re.fullmatch(rf"(?:is|was|will) {N} (?:be )?a leap year", s):
        year = parse_num(m.group(1))
        if year is not None and float(year).is_integer():
            return "Yes" if leap(int(year)) else "No"
    if re.fullmatch(r"how many days (?:are )?(?:there )?in a leap year", s):
        return "366"
    if re.fullmatch(r"how many days (?:are )?(?:there )?in a (?:normal |regular |common |non-leap )?year", s):
        return "365"
    if m := re.fullmatch(rf"how many days (?:are )?(?:there )?in ([a-z]+)(?: (?:of |in )?{N})?", s):
        mo = MONTHS.get(m.group(1))
        year = parse_num(m.group(2)) if m.group(2) else None
        if mo and (year is None or 1 <= year <= 9999):
            if mo == 2 and year is None:
                return "28, or 29 in a leap year"
            return str(calendar.monthrange(int(year or 2001), mo)[1])
    if re.fullmatch(r"how many (months|weeks|days) (?:are )?(?:there )?in a (year|week)", s):
        a, b = re.fullmatch(r"how many (months|weeks|days) (?:are )?(?:there )?in a (year|week)", s).groups()
        return {("months", "year"): "12", ("weeks", "year"): "52", ("days", "week"): "7", ("days", "year"): "365"}.get((a, b))
    if m := re.fullmatch(r"(?:what|which) day (?:of the week )?(?:is|was|will be|does) (.+?)(?: fall on| be| land on)?", s):
        d = parse_date(m.group(1))
        if d:
            return calendar.day_name[d.weekday()]
    if m := re.fullmatch(r"(?:what|which) (?:date|day) is (.+)|when is (.+)", s):
        h = (m.group(1) or m.group(2)).strip()
        h = re.sub(r"^the ", "", h) if h.startswith("the fourth") is False else h
        if h in FIXED_HOLIDAYS:
            mo, d = FIXED_HOLIDAYS[h]
            return f"{calendar.month_name[mo]} {d}"
    if m := re.fullmatch(r"how many (days|weeks|sleeps) (?:are )?(?:left )?(?:until|till|til|to|before) (.+)", s):
        target = _day_of(m.group(2), dt.date.today())
        if target is not None:
            days = (target - dt.date.today()).days
            return f"{days // 7} weeks" if m.group(1) == "weeks" else f"{days} day{'s' if days != 1 else ''}"
    if m := re.fullmatch(r"(?:what|which) month (?:comes|is) (after|before) ([a-z]+)", s):
        mo = MONTHS.get(m.group(2))
        if mo:
            return calendar.month_name[(mo % 12) + 1 if m.group(1) == "after" else ((mo - 2) % 12) + 1]
    if m := re.fullmatch(r"what(?: is|'s) the (?:next )?day (after|before) ([a-z]+)|what day comes (after|before) ([a-z]+)", s):
        rel, day = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        if day in DAYS:
            return calendar.day_name[(DAYS.index(day) + (1 if rel == "after" else -1)) % 7]
    if m := re.fullmatch(r"what(?: is|'s) the (first|last) (month|day) of the (year|week)", s):
        first = m.group(1) == "first"
        if m.group(2) == "month" and m.group(3) == "year":
            return "January" if first else "December"
    if m := re.fullmatch(rf"(?:what(?: is|'s)|what time is) {N} (minutes?|hours?) (before|after|from|past) (.+)", s):
        v, u, rel, t = parse_num(m.group(1)), m.group(2), m.group(3), parse_clock(_clock_digits(m.group(4)))
        if v is not None and t is not None:
            delta = v * (60 if u.startswith("hour") else 1)
            return show_clock(int(t + (-delta if rel == "before" else delta)))
    return None


SPELL_RE = re.compile(r"^(?:hey |ok |okay )?(?:how (?:do|should|would|can) (?:you|i|we) spell|how is|can you spell|spell|what is the spelling of|what's the spelling of)\s+(?:the word\s+)?\"?([a-z][a-z'\-]*)\"?(?: spelled)?[?.!]*$", re.I)


def solve_spelling(q):
    m = SPELL_RE.match(q.strip())
    if not m:
        return None
    word = m.group(1)
    return "-".join(c.upper() for c in word if c.isalpha())


SOLVERS = [("spelling", solve_spelling), ("calendar_time", solve_calendar), ("unit_conversion", solve_units),
           ("arithmetic", solve_arithmetic)]



SOLVERS = [
    ("calendar", solve_calendar),
    ("units", solve_units),
    ("arithmetic", solve_arithmetic),
]


@dataclass
class Answer:
    """What a question comes to."""

    kind: str
    """exact | live | knowledge"""
    text: str | None = None
    """What is said; None when nothing can be (a knowledge question with no LLM)."""
    solver: str | None = None
    seconds: float | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "text": self.text,
            "solver": self.solver,
            "seconds": None if self.seconds is None else round(self.seconds, 3),
        }


LIVE_REPLY = "Sorry, I can't look that up."

_QUESTION = re.compile(
    r"^(?:(?:in|at|on|from|by|for|to) (?:what|which|whom|whose)|"
    r"what|what's|whats|who|who's|whom|whose|when|where|why|how|which|is|are|was|were|can|could|"
    r"do|does|did|will|would|should|define|explain|tell me|name|list)\b",
    re.I,
)


def looks_like_a_question(text: str) -> bool:
    """A question, not a request or a remark: a knowledge LLM answers the one
    and would only make something up for the others ("thank you")."""
    text = text.strip()
    return text.endswith("?") or bool(_QUESTION.match(_strip_lead(text)))


def _strip_lead(text: str) -> str:
    return re.sub(r"^(?:hey|ok|okay|so|um|uh|and|but|please)[,\s]+", "", text.strip(), flags=re.I)


def solve(text: str) -> Answer | None:
    """An exact answer, or None."""
    spelled = solve_spelling(text)
    if spelled is not None:
        word = SPELL_RE.match(text.strip()).group(1)
        # Commas, so the voice says each letter: "A-C-C" comes out as "AC".
        return Answer("exact", f"{word.capitalize()} is spelled {spelled.replace('-', ', ')}.", "spelling")
    if is_live(text):
        return None
    for name, solver in SOLVERS:
        try:
            found = solver(text)
        except (ValueError, OverflowError, KeyError, ZeroDivisionError):
            found = None
        if found is not None:
            return Answer("exact", _sentence(found), name)
    return None


def classify(text: str) -> str | None:
    """live | knowledge | None (not a question to answer)."""
    if is_live(text):
        return "live"
    if looks_like_a_question(text):
        return "knowledge"
    return None


def _sentence(answer: str) -> str:
    """"96" -> "It's 96."; "Yes" -> "Yes."."""
    answer = answer.strip()
    if answer in ("Yes", "No"):
        return answer + "."
    if answer[:1].isdigit() or answer[:1] == "-" or answer.split(" ")[0] in calendar.month_name or answer in calendar.day_name:
        return f"It's {answer}."
    return answer if answer.endswith(".") else answer + "."


# ------------------------------------------------------------------ the LLM

QA_MODELS = {
    # id: (name, Hugging Face repository, file, MB). On the benchmark's 148
    # knowledge questions (Q8_0, plain prompt): Granite 128 right in ~0.2 s on
    # a desktop CPU and ~0.45 s on a Pi 5; Gemma 3 1B 137 in ~0.6 s on a
    # desktop, for a faster machine than a Pi (an N100).
    "granite-4.0-350m": ("Granite 4.0 350M", "ibm-granite/granite-4.0-350m-GGUF", "granite-4.0-350m-Q8_0.gguf", 370),
    "gemma-3-1b": ("Gemma 3 1B", "ggml-org/gemma-3-1b-it-GGUF", "gemma-3-1b-it-Q8_0.gguf", 1070),
}
DEFAULT_QA_MODEL = "granite-4.0-350m"
QA_PROMPT = "You are a voice assistant in a smart home. Answer the user's question in one short sentence."
QA_MAX_TOKENS = 64


class Answerer:
    """A small LLM for knowledge questions, in llama.cpp. ``answer`` is slow:
    call it from a thread."""

    def __init__(self, model_path: Path, n_threads: int = 2, model: str = DEFAULT_QA_MODEL) -> None:
        from llama_cpp import Llama  # pylint: disable=import-outside-toplevel

        self.model = model
        started = time.perf_counter()
        self.llm = Llama(model_path=str(model_path), n_ctx=512, n_threads=n_threads, verbose=False)
        self.load_seconds = time.perf_counter() - started
        self._lock = threading.Lock()
        _LOGGER.info("Question model ready: %s (%.1f s)", Path(model_path).name, self.load_seconds)

    @staticmethod
    def find_model(
        model: str = DEFAULT_QA_MODEL, download: bool = False, cache_dir: Path | None = None
    ) -> Path | None:
        from .fallback import find_gguf  # pylint: disable=import-outside-toplevel

        _name, repo, filename, _size = QA_MODELS[model]
        return find_gguf(repo, filename, download, cache_dir)

    def answer(self, question: str) -> tuple[str, float]:
        with self._lock:
            started = time.perf_counter()
            response = self.llm.create_chat_completion(
                messages=[{"role": "system", "content": QA_PROMPT}, {"role": "user", "content": question.strip()}],
                temperature=0.0,
                max_tokens=QA_MAX_TOKENS,
            )
            seconds = time.perf_counter() - started
        text = (response["choices"][0]["message"].get("content") or "").strip()
        return _first_sentence(text), seconds


def _first_sentence(text: str) -> str:
    """Said, not read: no markdown, and one sentence (a small model rambles)."""
    text = re.sub(r"[*_#`]+", "", text).strip()
    found = re.match(r"(.+?[.!?])(\s|$)", text, re.S)
    return (found.group(1) if found else text).strip()


class Questions:
    """Exact answers, the live notice, and (with an ``Answerer``) the rest."""

    def __init__(self, answerer: Answerer | None = None) -> None:
        self.answerer = answerer

    def exact(self, text: str) -> Answer | None:
        return solve(text) if text.strip() else None

    def answer(self, text: str) -> Answer | None:
        """For what no command took: the live notice, or the LLM's answer to a
        question; None when it is not a question (or there is no LLM)."""
        kind = classify(text) if text.strip() else None
        if kind == "live":
            return Answer("live", LIVE_REPLY)
        if kind == "knowledge" and self.answerer is not None:
            said, seconds = self.answerer.answer(text)
            if said:
                return Answer("knowledge", said, seconds=seconds)
        return None
