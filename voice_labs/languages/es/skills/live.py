"""Whether a Spanish question is about now or here -- el tiempo, las noticias,
resultados, precios, qué está abierto (voice_labs/skills/live.py).

Read without accents ("que tiempo hace"). "Último" alone is no sign of it:
"el último lunes de mayo" is a date; "mañana" isn't either in "las siete de
la mañana"."""

from __future__ import annotations

import re

from .numbers import fold

LIVE_PATTERNS = [
    r"\bque tiempo (?:hace|hara|va a hacer|tendremos)\b|\b(?:el|del) tiempo (?:que hace|de hoy|para|en)\b|\bcomo esta el tiempo\b",
    r"\b(prevision|pronostico|lluvia|llover|llueve|llovera|nieve|nevar|nieva|nevara|soleado|nublado|viento|tormenta|granizo|helada|humedad|paraguas|abrigo|chaqueta|indice uv|calidad del aire|polen)\b",
    r"\b(temperatura exterior|temperatura fuera|fuera|afuera|en la calle)\b",
    r"\b(amanecer|anochecer|atardecer|puesta de sol|salida del sol|sale el sol|se pone el sol|marea alta|marea baja)\b",
    r"\b(noticias|titulares|ultimas noticias|ultima hora)\b",
    r"\b(resultados|resultado del|marcador|clasificacion|la liga|alineacion|proximo partido|en la tele|en television)\b",
    r"\bquien (?:ha ganado|gano|gana|va ganando)\b",
    r"\b(actual|actuales|actualmente|ahora|ahora mismo|en este momento|hasta ahora|hoy en dia)\b",
    r"\b(hoy|manana|ayer|pasado manana|anteayer|esta noche|esta semana|este fin de semana|este ano)\b",
    r"\bquien es (?:el |la )?(?:actual )?(presidente|presidenta|rey|reina|papa|alcalde|alcaldesa)\b",
    r"\b(precio|precios|cuesta|cuestan|cotizacion|acciones|bitcoin|cripto|tipo de cambio|gasolina|luz hoy)\b",
    r"\b(cerca de mi|por aqui cerca|cerca de aqui|en mi zona|en mi barrio)\b",
    r"\b(abierto|abierta|abiertos|abre|abren|cierra|cierran|cerrado|cerrada|horario)\b.*\b(tienda|supermercado|restaurante|farmacia|banco|biblioteca|correos|centro comercial)\b",
    r"\b(tienda|supermercado|restaurante|farmacia|banco|biblioteca|correos|centro comercial)\b.*\b(abierto|abierta|abre|abren|cierra|cierran|cerrado|cerrada|horario)\b",
    r"\b(mi|el proximo) vuelo\b|\bvuelo (retrasado|a tiempo|cancelado)\b",
    r"\b(trafico|atasco|atascos|retraso|retrasos|averia|corte de luz)\b",
    # The time now (not "qué hora es 45 minutos después de las 3": the clock skill's).
    r"\bque hora es\s*[?.!]*\s*$|\bque dia es\s*[?.!]*\s*$|\bque fecha es\s*[?.!]*\s*$|\ba que (?:dia )?estamos\b",
]
# Words that look live but are not, in questions that never change.
LIVE_EXCEPTIONS = [
    r"\bque significa\b.*",
    r"\bcomo se (?:escribe|deletrea)\b",
    r"\bcorriente (?:alterna|continua)\b",
    r"\b(?:de|por) la manana\b",
    r"\b(?:de|por) la noche\b",
]
_CURRENCIES = re.compile(r"\b(euros?|dolar|dolares|libras? esterlinas?|yenes|yen|pesos?)\b")


def _currency(word: str) -> str:
    """"dólares" and "dólar" are one currency."""
    return re.sub(r"(?:es|s)$", "", word.split()[0]) if not word.startswith("yen") else "yen"


def is_live(q):
    s = re.sub(r"[,;]", " ", fold(q.lower()))
    for p in LIVE_EXCEPTIONS:
        s = re.sub(p, " ", s)
    # Two currencies: an exchange rate ("cuántos dólares son un euro").
    if len({_currency(c) for c in _CURRENCIES.findall(s)}) > 1:
        return True
    return any(re.search(p, s) for p in LIVE_PATTERNS)
