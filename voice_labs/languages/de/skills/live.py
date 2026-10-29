"""Whether a German question is about now or here -- das Wetter, Nachrichten,
Ergebnisse, Preise, was geöffnet hat (voice_labs/skills/live.py)."""

from __future__ import annotations

import re

LIVE_PATTERNS = [
    r"\b(wetter|vorhersage|wettervorhersage|regen|regnet|regnen|schnee|schneit|sonnig|bewölkt|wind|windig|sturm|gewitter|hagel|frost|luftfeuchtigkeit|regenschirm|jacke|uv-index|luftqualität|pollen)\b",
    r"\b(temperatur draußen|draußen)\b",
    r"\b(sonnenaufgang|sonnenuntergang|mondaufgang|flut|ebbe)\b",
    r"\b(nachrichten|schlagzeilen|neuigkeiten)\b",
    r"\b(ergebnis|ergebnisse|spielstand|tabelle|bundesliga|aufstellung|anstoß|nächstes spiel|im fernsehen)\b",
    r"\bwer hat\b.*\bgewonnen\b|\bwer gewinnt\b",
    r"\b(neueste|neuesten|aktuell|aktuelle|aktuellen|derzeit|momentan|im moment|gerade|zurzeit|jetzt|bisher)\b",
    r"\b(heute|heutige|heutigen|morgen|gestern|übermorgen|vorgestern|heute abend|diese woche|dieses wochenende|dieses jahr)\b",
    r"\bwer ist (der |die )?(aktuelle |jetzige )?(bundeskanzler|kanzler|kanzlerin|präsident|präsidentin|premierminister|könig|königin|papst|bürgermeister)\b",
    r"\b(preis|preise|kostet|kosten|kurs|aktie|aktien|bitcoin|krypto|wechselkurs|benzinpreis|spritpreis)\b",
    r"\b(in der nähe|hier in der gegend|in meiner nähe|in der umgebung)\b",
    r"\b(geöffnet|offen|schließt|öffnet|öffnungszeiten|zu)\b.*\b(laden|geschäft|supermarkt|restaurant|apotheke|bank|bibliothek|post)\b",
    r"\b(laden|geschäft|supermarkt|restaurant|apotheke|bank|bibliothek|post)\b.*\b(geöffnet|offen|schließt|öffnet|öffnungszeiten)\b",
    r"\b(mein|der nächste) flug\b|\bflug (status|verspätet|pünktlich)\b",
    r"\b(verkehr|stau|staus|verspätung|verspätungen|ausfall|fällt aus)\b",
    # The time now (not "wie spät ist es 90 Minuten vor Mittag": the clock skill's).
    r"\bwie (spät|viel uhr) ist es\s*[?.!]*\s*$|\bwelcher tag ist heute\b|\bwelches datum (ist|haben wir)\b",
]
# Words that look live but are not, in questions that never change.
LIVE_EXCEPTIONS = [
    r"\bwas bedeutet\b.*",
    r"\bwie (schreibt|buchstabiert) man\b",
    r"\b(wechselstrom|gleichstrom)\b",
]
CURRENCIES = re.compile(r"\b(euro|dollar|franken|yen|pfund sterling|britische pfund)\b")


def is_live(q):
    s = re.sub(r"[,;]", " ", q.lower())
    for p in LIVE_EXCEPTIONS:
        s = re.sub(p, " ", s)
    # Two currencies: an exchange rate ("wie viel Dollar sind ein Euro").
    if len(set(CURRENCIES.findall(s))) > 1:
        return True
    return any(re.search(p, s) for p in LIVE_PATTERNS)
