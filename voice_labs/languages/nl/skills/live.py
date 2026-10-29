"""Whether a Dutch question is about now or here -- het weer, het nieuws,
uitslagen, prijzen, wat open is (voice_labs/skills/live.py)."""

from __future__ import annotations

import re

LIVE_PATTERNS = [
    r"\b(weer|weerbericht|voorspelling|weersverwachting|regen|regent|regenen|sneeuw|sneeuwt|zonnig|bewolkt|wind|winderig|storm|onweer|hagel|vorst|luchtvochtigheid|paraplu|jas|uv-index|luchtkwaliteit|pollen)\b",
    r"\b(temperatuur buiten|buiten)\b",
    r"\b(zonsopgang|zonsondergang|eb|vloed)\b",
    r"\b(nieuws|krantenkoppen|laatste nieuws)\b",
    r"\b(uitslag|uitslagen|stand|eredivisie|opstelling|aftrap|volgende wedstrijd|op tv)\b",
    r"\bwie heeft\b.*\bgewonnen\b|\bwie wint\b",
    r"\b(nieuwste|actueel|actuele|huidige|momenteel|op dit moment|nu|tot nu toe)\b",
    r"\b(vandaag|morgen|gisteren|overmorgen|eergisteren|vanavond|deze week|dit weekend|dit jaar)\b",
    r"\bwie is (de )?(huidige )?(premier|minister-president|koning|koningin|paus|president|burgemeester)\b",
    r"\b(prijs|prijzen|kost|kosten|koers|aandeel|aandelen|bitcoin|crypto|wisselkoers|benzineprijs)\b",
    r"\b(in de buurt|hier in de buurt|dichtbij|in mijn omgeving)\b",
    r"\b(open|geopend|gesloten|sluit|openingstijden)\b.*\b(winkel|supermarkt|restaurant|apotheek|bank|bibliotheek|postkantoor)\b",
    r"\b(winkel|supermarkt|restaurant|apotheek|bank|bibliotheek|postkantoor)\b.*\b(open|geopend|gesloten|sluit|openingstijden)\b",
    r"\b(mijn|de volgende) vlucht\b|\bvlucht (status|vertraagd|op tijd)\b",
    r"\b(verkeer|file|files|vertraging|vertragingen|storing)\b",
    # The time now (not "hoe laat is het 90 minuten voor 12 uur": the clock skill's).
    r"\bhoe laat is het\s*[?.!]*\s*$|\bwelke dag is het\b|\bwelke datum is het\b",
]
LIVE_EXCEPTIONS = [
    r"\bwat betekent\b.*",
    r"\bhoe (spel|schrijf) je\b",
    r"\b(wisselstroom|gelijkstroom)\b",
]
CURRENCIES = re.compile(r"\b(euro|dollar|pond sterling|britse pond|yen|frank)\b")


def is_live(q):
    s = re.sub(r"[,;]", " ", q.lower())
    for p in LIVE_EXCEPTIONS:
        s = re.sub(p, " ", s)
    if len(set(CURRENCIES.findall(s))) > 1:
        return True
    return any(re.search(p, s) for p in LIVE_PATTERNS)
