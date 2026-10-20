"""Whether a French question is about now or here -- la météo, les
actualités, les résultats, les prix, ce qui est ouvert
(voice_call/skills/live.py)."""

from __future__ import annotations

import re

LIVE_PATTERNS = [
    r"\b(météo|quel temps|prévisions|pluie|pleuvoir|pleut|neige|neiger|ensoleillé|nuageux|vent|venteux|orage|grêle|gel|verglas|humidité|parapluie|manteau|indice uv|qualité de l'air|pollens?)\b",
    r"\b(dehors|température extérieure|fait il (froid|chaud)|il fait (froid|chaud))\b",
    r"\b(lever du soleil|coucher du soleil|marée|marées)\b",
    r"\b(actualités|actus|nouvelles|infos|journal|gros titres)\b",
    r"\b(score|scores|résultat du match|résultats du match|classement|ligue 1|composition|coup d'envoi|prochain match|à la télé)\b",
    r"\bqui a gagné\b|\bqui gagne\b",
    r"\b(dernier|dernière|derniers|dernières|actuel|actuelle|actuellement|en ce moment|maintenant|jusqu'à présent)\b",
    r"\b(aujourd'hui|demain|hier|après demain|avant hier|ce soir|cette semaine|ce week end|cette année)\b",
    r"\bqui est (le |la )?(actuel |actuelle )?(président|présidente|premier ministre|première ministre|roi|reine|pape|maire)\b",
    r"\b(prix|coûte|coûtent|coût|cours|action|actions|bourse|bitcoin|crypto|taux de change|prix de l'essence)\b",
    r"\b(près d'ici|à proximité|dans le coin|autour de moi|le plus proche|la plus proche)\b",
    r"\b(ouvert|ouverte|fermé|fermée|ferme|horaires)\b.*\b(magasin|supermarché|restaurant|pharmacie|banque|bibliothèque|poste|boulangerie)\b",
    r"\b(magasin|supermarché|restaurant|pharmacie|banque|bibliothèque|poste|boulangerie)\b.*\b(ouvert|ouverte|fermé|fermée|ferme|horaires)\b",
    r"\bmon vol\b|\b(statut|état) (de mon|du) vol\b|\bvol (retardé|à l'heure)\b",
    r"\b(trafic|circulation|bouchon|bouchons|embouteillage|embouteillages|retard|retards|panne|grève)\b",
    # The time now (not "quelle heure est-il 90 minutes avant midi": the clock skill's).
    r"\bquelle heure est il\s*[?.!]*\s*$|\bil est quelle heure\s*[?.!]*\s*$|\bquel jour (sommes nous|on est|est on)\b|\bquelle (est la )?date\b",
]
LIVE_EXCEPTIONS = [
    r"\bque (veut|signifie) dire\b.*|\bqu'est ce que veut dire\b.*|\bque signifie\b.*",
    r"\bcomment (s'écrit|s'épelle|on écrit|on épelle|écrit on|épelle t on)\b.*",
    r"\bcourant (alternatif|continu)\b",
]
CURRENCIES = re.compile(r"\b(euros?|dollars?|livres? sterling|yens?|francs? suisses?)\b")


def is_live(q):
    s = re.sub(r"[,;]|(?<=[a-zà-ÿ])-(?=[a-zà-ÿ])", " ", q.lower().replace("’", "'"))
    for p in LIVE_EXCEPTIONS:
        s = re.sub(p, " ", s)
    if len({c.rstrip("s").split()[0] for c in CURRENCIES.findall(s)}) > 1:
        return True
    return any(re.search(p, s) for p in LIVE_PATTERNS)
