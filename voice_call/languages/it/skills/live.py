"""Whether an Italian question is about now or here -- il meteo, le notizie,
i risultati, i prezzi, cosa è aperto (voice_call/skills/live.py).

Read without accents ("che tempo fara"). "Ora" is now only at the end of a
question and not counted ("chi è il presidente ora", not "quanti minuti ha
un'ora"); "ultimo" is no sign of it ("l'ultimo lunedì di maggio" is a date),
nor "risultato" of a sum ("il risultato di dodici per otto")."""

from __future__ import annotations

import re

from .numbers import fold, parse_num

LIVE_PATTERNS = [
    (
        r"\b(meteo|che tempo (?:fa|fara|faceva|ha fatto|c'e)|previsioni|pioggia|piovere|piove|piovera|pioviggina"
        r"|neve|nevicare|nevica|nevichera|sole|soleggiato|nuvoloso|nuvole|vento|ventoso|temporale|temporali|grandine"
        r"|gelo|nebbia|umidita|ombrello|cappotto|giacca|indice uv|qualita dell'aria|polline|pollini)\b"
    ),
    r"\b(temperatura esterna|temperatura fuori|fuori|fa (?:freddo|caldo)|fara (?:freddo|caldo))\b",
    r"\b(alba|tramonto|sorge il sole|tramonta il sole|marea|maree)\b",
    r"\b(notizie|telegiornale|tg|titoli|giornale radio|cronaca|ultim[ae] or[ae])\b",
    (
        r"\b(partita|partite|classifica|serie a|serie b|campionato|champions|formazione|calcio d'inizio|in tv"
        r"|in televisione|gol|derby|scudetto|gran premio|formula 1)\b"
    ),
    (
        r"\brisultat[oi] (?:della|delle|del|dell'|dei|degli|di ieri|di oggi|di stasera)\b"
        r"(?! ?(?:somma|moltiplicazione|divisione|sottrazione|operazione|conto|radice|media|calcolo|espressione))"
    ),
    r"\bchi (?:ha vinto|vince|sta vincendo|ha perso|vincera)\b",
    (
        r"\b(adesso|in questo momento|attualmente|attuale|attuali|al momento|ora come ora|per ora|proprio ora|finora"
        r"|oggigiorno)\b"
    ),
    (
        r"\b(oggi|domani|ieri|dopodomani|l'altro ieri|l'altroieri|stasera|stanotte|stamattina|stamani|stamane"
        r"|questa sera|questa notte|questa mattina|questa settimana|questo weekend|questo fine settimana|quest'anno"
        r"|questo mese)\b"
    ),
    (
        r"\bchi e (?:il |la |l'|lo )?(?:attuale |nuovo |nuova )?(presidente|presidentessa|premier|primo ministro"
        r"|prima ministra|re|regina|papa|pontefice|sindaco|sindaca|ministro|ministra|allenatore|ct|governatore)\b"
    ),
    (
        r"\b(prezzo|prezzi|costa|costano|costo|quotazione|quotazioni|azioni|borsa|bitcoin|cripto|criptovalut[ae]"
        r"|tasso di cambio|cambio|benzina|gasolio|bolletta|spread|mutuo|mutui|inflazione)\b"
    ),
    (
        r"\b(qui vicino|qua vicino|nei dintorni|vicino a me|vicino a casa|in zona|piu vicin[oaie]|da queste parti"
        r"|nelle vicinanze|nel mio quartiere)\b"
    ),
    (
        r"\b(apert[oaie]|apre|aprono|chius[oaie]|chiude|chiudono|orari|orario)\b.*\b(negozio|negozi|supermercato"
        r"|farmacia|farmacie|banca|ristorante|ristoranti|posta|ufficio postale|biblioteca|panetteria|panificio|bar"
        r"|centro commerciale|museo|tabaccheria|edicola|benzinaio|distributore)\b"
    ),
    (
        r"\b(negozio|negozi|supermercato|farmacia|farmacie|banca|ristorante|ristoranti|posta|ufficio postale|biblioteca"
        r"|panetteria|panificio|bar|centro commerciale|museo|tabaccheria|edicola|benzinaio|distributore)\b"
        r".*\b(apert[oaie]|apre|aprono|chius[oaie]|chiude|chiudono|orari|orario)\b"
    ),
    (
        r"\b(?:il mio|il prossimo) volo\b|\bvol[oi] (?:in ritardo|in orario|cancellat[oi])\b"
        r"|\btren[oi] (?:in ritardo|cancellat[oi])\b"
    ),
    r"\b(traffico|coda|code|incidente|incidenti|ritardo|ritardi|sciopero|scioperi|lavori in corso|blackout)\b",
    # The time or date now (not "che ore sono 45 minuti dopo le 15": the
    # clock skill's).
    (
        r"\bche or[ae] (?:e|sono|fai|fa|abbiamo)\s*$|\bche giorno (?:e|siamo|abbiamo)\s*$|\bin che giorno siamo\b"
        r"|\bche data (?:e|abbiamo)\s*$|\bquanti ne abbiamo\b|\b(?:dimmi|sai|mi dici) l'ora\b"
    ),
]
# Words that look live but are not, in questions that never change.
LIVE_EXCEPTIONS = [
    r"\b(?:che cosa|cosa|che) (?:vuol dire|significa)\b.*",
    r"\bcome si (?:scrive|scrivono|compita|pronuncia)\b.*",
    r"\bspelling\b.*",
    r"\bcorrente (?:alternata|continua)\b",
    r"\bda oggi (?=a |ad |al |alla |all'|allo |ai |agli |alle |fino )",
]
_CURRENCIES = re.compile(r"\b(euro|dollar[oi]|sterlin[ae]|yen|franch?[oi] svizzer[oi]|rubl[oi]|yuan)\b")

_AN_HOUR = {"ogni", "che", "quale", "mezza", "sola", "intera", "prima", "dopo", "quest", "quell", "nell", "dell"}
"""Before "ora", the hour ("un'ora", "ogni ora", "che ora"), not now."""


def _now(s: str) -> bool:
    """"Ora" at the end, as now: "chi è il presidente ora"."""
    words = s.split()
    if len(words) < 2 or words[-1] != "ora":
        return False
    before = words[-2]
    return before not in _AN_HOUR and parse_num(before) is None


def is_live(q):
    s = re.sub(r"[,;]", " ", fold(q.lower().replace("’", "'")))
    s = re.sub(r"\s*[?!.]+\s*$", "", s)
    for p in LIVE_EXCEPTIONS:
        s = re.sub(p, " ", s)
    s = " ".join(s.split())
    # Two currencies: an exchange rate ("quanti dollari fa un euro").
    if len({c[:5] for c in _CURRENCIES.findall(s)}) > 1:
        return True
    return _now(s) or any(re.search(p, s) for p in LIVE_PATTERNS)
