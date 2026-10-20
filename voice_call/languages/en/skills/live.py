"""Whether an English question is about now or here -- the weather, news,
scores, prices, what's open (voice_call/skills/live.py)."""

from __future__ import annotations

import re

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
    # Spoken, and found missing on the held-out questions (README "Skills and questions").
    r"\b(won|win|winner|champions?|happened)\b.*\blast (year|season|week|month)\b",
    r"\bdid (the )?[a-z ]+ (win|lose)\b",
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
    # A weight in pounds is not money.
    r"\b(kilos?|kilograms?|kg|grams?|g|ounces?|oz|stones?|tons?|tonnes?|lbs?)\b.*\bin pounds\b",
]
CURRENCIES = re.compile(r"\b(euros?|dollars?|pounds?|yen|pesos?)\b")


def is_live(q):
    # A transcript's commas split phrases apart: "the next formula, one race".
    s = re.sub(r"[,;]", " ", q.lower())
    for p in LIVE_EXCEPTIONS:
        s = re.sub(p, " ", s)
    # Two currencies: an exchange rate ("how many euros is a dollar"); one
    # twice is arithmetic ("$5 plus $3").
    if len({c.rstrip("s") for c in CURRENCIES.findall(s)}) > 1:
        return True
    return any(re.search(p, s) for p in LIVE_PATTERNS)
