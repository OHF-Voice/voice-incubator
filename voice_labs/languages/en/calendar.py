"""Alarms and reminders on the calendar, in English: when one is for -- read
from what was said ("7:30 tomorrow", "every weekday at 6", "in 20 minutes",
"at 5 pm on Friday") -- and what the app says about them. A language with
this module keeps its alarms and timed reminders on Home Assistant's Local
Calendar (voice_labs/alarms.py); one without keeps alarms as timers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from . import reading
from .skills.numbers import parse_num
from .speech import _capital, clock, duration, join

# ----------------------------------------------------------------- reading

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
CODES = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
_DAY = rf"(?:{'|'.join(DAYS)})"

_NUMBER_WORDS = {
    "a", "an", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
    "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty", "thirty",
    "forty", "fifty", "oh", "zero", "hundred", "half", "quarter",
}
_WHEN_WORDS = {
    "at", "on", "in", "for", "this", "next", "every", "each", "the", "and", "by", "of",
    "am", "pm", "a.m.", "p.m.", "o'clock", "oclock", "noon", "midday", "midnight", "past",
    "today", "tonight", "tomorrow", "morning", "afternoon", "evening", "night", "daily",
    "day", "days", "weekday", "weekdays", "weekend", "weekends", "week",
    "minute", "minutes", "hour", "hours", *DAYS, *(d + "s" for d in DAYS), *_NUMBER_WORDS,
}
_EDGE = {"at", "on", "in", "for", "this", "next", "the", "and", "by", "of", "a", "an"}
"""Words a time is said with, but which alone are no time."""
_STARTS = {"at", "on", "by", "this", "next", "every", "each", "today", "tonight", "tomorrow", *DAYS}
"""Words a time said at the end of a reminder may start with."""


@dataclass
class When:
    """When an alarm or a reminder is: its first time (local), how it repeats
    (an RRULE, or None), and what was said besides (a reminder's text)."""

    start: datetime
    rrule: str | None = None
    rest: str = ""
    relative: bool = False
    """Said from now ("in 20 minutes")."""
    said: str = ""
    """The words that said when."""


def _is_when_word(word: str) -> bool:
    w = word.lower().strip(",.!?")
    return w in _WHEN_WORDS or bool(re.fullmatch(r"\d{1,2}(:\d{2})?(am|pm)?", w))


def _run(words: list[str], from_end: bool) -> tuple[int, int]:
    """The longest run of time words at the start or the end: (begin, end)."""
    n = len(words)

    def ok(i: int) -> bool:
        w = words[i].lower().strip(",.!?")
        if w == "to":  # "quarter to eight", "ten to eight": not "to call mom"
            before = words[i - 1].lower() if i > 0 else ""
            after = words[i + 1].lower().strip(",.!?") if i + 1 < n else ""
            return (before in _NUMBER_WORDS or before.isdigit()) and (after in _NUMBER_WORDS or after.isdigit())
        return _is_when_word(words[i])

    if from_end:
        begin = n
        while begin > 0 and ok(begin - 1):
            begin -= 1
        end = n
    else:
        end = 0
        while end < n and ok(end):
            end += 1
        begin = 0
    # Not "the" or "and" at its edges: they belong to what was said besides.
    while begin < end and words[begin].lower() in ("the", "and"):
        begin += 1
    return begin, end


def _joined(text: str) -> str:
    """The day words speech-to-text splits: "to morrow" (or "morrow", its
    "to" taken for the reminder's), and "to night" or "to day" before a time
    or at the end."""
    text = re.sub(r"(?:\bto\s+|^\s*)morrow\b", "tomorrow", text, flags=re.IGNORECASE)
    return re.sub(
        r"\bto\s+(night|day)\b(?=\s*(?:$|[,.!?]|at\b|by\b|\d|" + "|".join(sorted(_NUMBER_WORDS)) + r"\b))",
        r"to\1", text, flags=re.IGNORECASE,
    )


def when(text: str, now: datetime, alarm: bool = False) -> When | None:
    """When ``text`` says: a time (and a day, or how it repeats), or a time
    from now; None when it says none. ``now`` is aware local time; an
    ``alarm`` said for another day without am or pm is in the morning."""
    words = _joined(text).split()
    best: tuple[When, int] | None = None
    for from_end in (False, True):
        begin, end = _run(words, from_end)
        found = None
        while begin < end:
            found = _read(" ".join(words[begin:end]), now, alarm)
            if found is not None:
                break
            # Not all of it is the time ("to rest for 10 minutes at 5 pm"):
            # less of it, from where it meets the rest, to a word a time
            # starts or ends with.
            if from_end:
                begin += 1
                while begin < end and words[begin].lower().strip(",.!?") not in _STARTS:
                    begin += 1
            else:
                end -= 1
        if found is not None and (best is None or end - begin > best[1]):
            found.rest = " ".join(words[:begin] + words[end:]).strip(" ,")
            found.said = " ".join(words[begin:end])
            best = (found, end - begin)
    return best[0] if best else None


def _read(said: str, now: datetime, alarm: bool) -> When | None:
    """A run of time words as a When; None when it isn't one (or names a day
    but no time: see ``wants_time``)."""
    s = " " + said.lower().replace(".", "").replace(",", " ") + " "
    s = re.sub(r"(\d)(am|pm)\b", r"\1 \2", s)

    # From now: "in 20 minutes", "in an hour", "in half an hour" -- or a
    # length alone: speech-to-text, or the free text's edge, can drop the "in".
    found = re.search(r" (?:in )?(half an?|an?|\S+(?: \S+)?) (minutes?|hours?) ", s)
    if found:
        said_amount = found.group(1)
        amount = 0.5 if said_amount.startswith("half") else 1.0 if said_amount in ("a", "an") else parse_num(said_amount)
        if not amount or s.replace(found.group(0), " ").strip():
            return None
        unit = 60 if found.group(2).startswith("minute") else 3600
        return When(now + timedelta(seconds=float(amount) * unit), relative=True)

    rule, days = None, None
    if re.search(r" (every day|daily|every morning|every night|every evening|each day) ", s):
        rule = "FREQ=DAILY"
    elif re.search(r" (every weekday|weekdays|on weekdays) ", s):
        rule, days = "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR", [0, 1, 2, 3, 4]
    elif re.search(r" (every weekend|weekends|on weekends) ", s):
        rule, days = "FREQ=WEEKLY;BYDAY=SA,SU", [5, 6]
    else:
        repeated = [i for i, d in enumerate(DAYS) if re.search(rf" (every|each) (?:\w+ and )*{d}s? | {d}s ", s)]
        if repeated:
            rule, days = "FREQ=WEEKLY;BYDAY=" + ",".join(CODES[i] for i in repeated), repeated

    date = _date(s, now) if rule is None else None

    numbers = sorted(_NUMBER_WORDS - {"a", "an", "half", "quarter"})
    if not re.search(r"\d|noon|midday|midnight| (?:" + "|".join(numbers) + ") ", s):
        return None  # no time: a day alone is not enough
    time = reading.clock(s)
    if time is None:
        return None
    hour, minute, meridiem = time
    if " morning " in s:
        meridiem = "am"
    elif re.search(r" (afternoon|evening|tonight) ", s) or (" night " in s and hour != 12):
        meridiem = "pm"
    if meridiem == "pm" and hour < 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0

    def at(day: Any, h: int) -> datetime:
        return now.replace(year=day.year, month=day.month, day=day.day, hour=h % 24, minute=minute, second=0, microsecond=0)

    if meridiem is None and 1 <= hour <= 12:
        if date is None and rule is None:
            # The next time it comes round, am or pm.
            options = [at(now.date(), h) for h in (hour, hour + 12)]
            return When(min(o if o > now else o + timedelta(days=1) for o in options))
        # Another day: an alarm is in the morning; a reminder from 7 to 11
        # in the morning, else in the afternoon.
        if hour != 12 and not alarm and hour < 7:
            hour += 12
    if rule is not None:
        start = at(now.date(), hour)
        while start <= now or (days is not None and start.weekday() not in days):
            start += timedelta(days=1)
        return When(start, rule)
    if date is None:
        start = at(now.date(), hour)
        return When(start if start > now else start + timedelta(days=1))
    start = at(date, hour)
    if start <= now and date == now.date() and not re.search(r" (today|tonight) ", s):
        start += timedelta(days=7)  # "on Monday" on a Monday, the time gone: next week's
    return When(start) if start > now else None


def _date(s: str, now: datetime) -> Any:
    """The day ``s`` (lowercase, spaced at its ends) names, if it names one."""
    if " tomorrow " in s:
        return (now + timedelta(days=1)).date()
    if re.search(r" (today|tonight) ", s):
        return now.date()
    date = None
    found = re.search(rf" (?:on |this |next )?({_DAY}) ", s)
    if found:
        ahead = (DAYS.index(found.group(1)) - now.weekday()) % 7
        date = (now + timedelta(days=ahead or (7 if f" next {found.group(1)} " in s else 0))).date()
    found = re.search(r" in (\S+) days? ", s)
    if found and parse_num(found.group(1)):
        date = (now + timedelta(days=int(parse_num(found.group(1))))).date()
    return date


def day(text: str, now: datetime) -> Any:
    """The day ``text`` names without a time ("tomorrow's", "on Friday"), or None."""
    s = " " + re.sub(r"'s\b", "", _joined(text).lower().replace(".", "").replace(",", " ")) + " "
    return _date(s, now)


_NOT_ABOUT = {
    "the", "a", "an", "my", "your", "to", "for", "about", "of", "that", "and", "me", "you", "reminder", "reminders",
    "one", "s",
}


def about(text: str) -> list[str]:
    """What a reminder is about, as words to compare: "my reminder to call
    mom tomorrow" -> ["call", "mom"]."""
    words = [w.removesuffix("'s") for w in re.findall(r"[\w']+", _joined(text).lower())]
    return [w for w in words if w not in _NOT_ABOUT and not _is_when_word(w)]


def wants_time(text: str) -> bool:
    """Whether ``text`` names a day or a repeat but no time ("tomorrow", "every
    Monday"): asked what time."""
    s = " " + text.lower() + " "
    return bool(re.search(rf" (tomorrow|today|tonight|every|daily|weekdays|weekends|{_DAY}s?) ", s))


def what(text: str) -> str:
    """A reminder's text, as it is said back: "to call my mom" -> "call your
    mom"."""
    s = re.sub(r"^(?:to|that|about|of)\b\s*", "", text.strip(" ,.?!"), flags=re.IGNORECASE)
    swaps = {"my": "your", "me": "you", "i": "you", "i'm": "you're", "myself": "yourself", "mine": "yours"}
    return " ".join(swaps.get(w.lower(), w) for w in s.split())


# ---------------------------------------------------------------- speaking

ALARM_WHEN = "What time should the alarm be for?"
REMINDER_WHEN = "When should I remind you?"
REMINDER_WHAT = "What should I remind you of?"
NO_ALARMS = "No alarms are set."
NO_REMINDERS = "You have no reminders."
CALENDAR_NEEDED = "Alarms and reminders need Home Assistant's Local Calendar, and I couldn't set it up."
ALARM_TITLE = "Alarm"
"""An alarm's event, on the calendar."""


def said_when(start: datetime, rrule: str | None, now: datetime) -> str:
    """"7:30 AM", "7:30 AM tomorrow", "9 AM on Friday", "7 AM every weekday",
    "9 AM on October 30"."""
    time = clock(start.hour, start.minute)
    if rrule:
        days = re.search(r"BYDAY=([A-Z,]+)", rrule)
        if not days:
            return f"{time} every day"
        codes = days.group(1).split(",")
        if codes == CODES[:5]:
            return f"{time} every weekday"
        if codes == CODES[5:]:
            return f"{time} every weekend"
        return f"{time} every {join([DAYS[CODES.index(c)].capitalize() for c in codes])}"
    ahead = (start.date() - now.date()).days
    if ahead == 0:
        return time
    if ahead == 1:
        return f"{time} tomorrow"
    if ahead < 7:
        return f"{time} on {DAYS[start.weekday()].capitalize()}"
    return f"{time} on {start.strftime('%B')} {start.day}"


def _from_now(start: datetime, now: datetime) -> str:
    return duration(max(60, round((start - now).total_seconds() / 60) * 60))


def alarm_set(start: datetime, rrule: str | None, now: datetime) -> dict[str, str]:
    specific = f"Set an alarm for {said_when(start, rrule, now)}."
    return {"brief": "Set an alarm.", "specific": specific, "verbose": f"{specific[:-1]}, in {_from_now(start, now)}."}


def reminder_set(what_said: str, start: datetime, rrule: str | None, now: datetime, relative: bool) -> dict[str, str]:
    at = f"in {_from_now(start, now)}" if relative else f"at {said_when(start, rrule, now)}"
    specific = f"I'll remind you to {what_said} {at}." if what_said else f"I'll remind you {at}."
    verbose = specific if relative else f"{specific[:-1]}, in {_from_now(start, now)}."
    return {"brief": "I'll remind you.", "specific": specific, "verbose": verbose}


def reminder_listed(what_said: str) -> str:
    """A reminder without a time, put on the to-do list."""
    return f"Added {what_said} to your reminders."


def alarm_due(title: str, start: datetime) -> str:
    """What an alarm says when it rings: "It's 7:30 AM."; a named one first
    says its name."""
    said = f"It's {clock(start.hour, start.minute)}."
    return said if title.strip().lower() in ("", ALARM_TITLE.lower()) else f"{_capital(title.strip())}. {said}"


def reminder_due(text: str) -> str:
    return f"Reminder: {text.strip().rstrip('.')}."


def _listed(events: list[dict[str, Any]], now: datetime) -> list[str]:
    return [said_when(e["start"], e.get("rrule"), now) for e in events]


def alarms_listed(alarms: list[dict[str, Any]], now: datetime) -> str:
    said = _listed(alarms, now)
    if len(said) == 1:
        return f"You have an alarm at {said[0]}."
    return f"You have {len(said)} alarms: {join(said)}."


def reminders_listed(reminders: list[dict[str, Any]], now: datetime) -> str:
    said = [f"{r['text']} at {s}" for r, s in zip(reminders, _listed(reminders, now), strict=True)]
    if len(said) == 1:
        return f"You have one reminder: {said[0]}."
    return f"You have {len(said)} reminders: {join(said)}."


def alarm_cancelled(alarms: list[dict[str, Any]], now: datetime) -> dict[str, str]:
    said = _listed(alarms, now)
    if len(said) == 1:
        specific = f"Cancelled the alarm at {said[0]}."
    else:
        specific = f"Cancelled {len(said)} alarms."
    return {"brief": "Cancelled the alarm." if len(said) == 1 else "Cancelled the alarms.", "specific": specific,
            "verbose": f"Cancelled the alarms at {join(said)}." if len(said) > 1 else specific}


def which_alarm(alarms: list[dict[str, Any]], now: datetime) -> str:
    return f"Which alarm? You have {join(_listed(alarms, now))}."


def alarm_not_found(asked: str, alarms: list[dict[str, Any]], now: datetime) -> str:
    return f"There's no alarm at {asked}. {alarms_listed(alarms, now)}" if alarms else NO_ALARMS


def reminder_cancelled(reminders: list[dict[str, Any]], now: datetime) -> dict[str, str]:
    said = [f"{r['text']} at {when}" for r, when in zip(reminders, _listed(reminders, now), strict=True)]
    if len(said) == 1:
        specific = f"Cancelled the reminder at {_listed(reminders, now)[0]}: {reminders[0]['text']}."
    else:
        specific = f"Cancelled {len(said)} reminders."
    return {"brief": "Cancelled the reminder." if len(said) == 1 else "Cancelled the reminders.", "specific": specific,
            "verbose": f"Cancelled the reminders: {join(said)}." if len(said) > 1 else specific}


def which_reminder(reminders: list[dict[str, Any]], now: datetime) -> str:
    said = [f"{r['text']} at {when}" for r, when in zip(reminders, _listed(reminders, now), strict=True)]
    return f"Which reminder? You have {join(said)}."


def reminder_not_found(reminders: list[dict[str, Any]], now: datetime) -> str:
    return f"There's no reminder like that. {reminders_listed(reminders, now)}" if reminders else NO_REMINDERS
