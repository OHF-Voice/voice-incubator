"""Whether a Polish question is about now or here -- pogoda, wiadomości,
wyniki, ceny, co jest otwarte (voice_call/skills/live.py).

Read folded, so a question typed without its diacritics ("jaka bedzie
pogoda") reads as one with them. "Rano", "wieczorem" and "w nocy" are times of day, not
now ("o siódmej rano"); "ostatni" alone is a date ("ostatni poniedziałek
maja"), not the news."""

from __future__ import annotations

import re

from .numbers import fold

LIVE_PATTERNS = [
    (r"\b(pogoda|pogody|pogode|pogodzie|prognoza|prognozy|prognoze|deszcz|deszczu|pada|padac|padal|bedzie padac|snieg|sniegu"
     r"|slonecznie|slonce|pochmurno|wiatr|wieje|burza|burze|grad|mroz|przymrozek|wilgotnosc|parasol|kurtke|kurtka"
     r"|indeks uv|jakosc powietrza|smog|pylki)\b"),
    r"\b(temperatura na zewnatrz|na zewnatrz|na dworze|na polu)\b",
    r"\b(wschod slonca|zachod slonca|wschodzi slonce|zachodzi slonce|przyplyw|odplyw)\b",
    r"\b(wiadomosci|wiadomosc|aktualnosci|naglowki|z ostatniej chwili|najnowsze|najnowszy|najnowsza)\b",
    r"\b(wynik|wyniki|wynikiem|mecz|meczu|tabela|tabeli|liga|ekstraklasa|sklad|w telewizji|w tv)\b",
    r"\bkto (?:wygral|wygra|wygrywa|prowadzi)\b",
    r"\b(aktualny|aktualna|aktualne|aktualnie|obecnie|obecny|obecna|teraz|w tej chwili|na razie|ostatnio)\b",
    (r"\b(dzisiaj|dzis|dzisiejszy|dzisiejsza|jutro|jutrzejszy|wczoraj|pojutrze|przedwczoraj|dzis wieczorem"
     r"|w tym tygodniu|w ten weekend|w tym roku|w tym miesiacu)\b"),
    r"\bkto jest (?:obecnie |teraz )?(?:prezydentem|premierem|krolem|krolowa|papiezem|burmistrzem|prezydentem miasta)\b",
    r"\b(cena|ceny|cene|kosztuje|kosztuja|kurs|kursy|akcje|gielda|bitcoin|krypto|benzyna|paliwo|paliwa|prad)\b",
    r"\b(w poblizu|niedaleko|blisko mnie|w okolicy|najblizszy|najblizsza|najblizsze)\b",
    (r"\b(otwarty|otwarta|otwarte|czynny|czynna|czynne|zamkniety|zamknieta|zamkniete|godziny otwarcia|otwieraja|zamykaja)\b"
     r".*\b(sklep|sklepy|supermarket|restauracja|restauracje|apteka|bank|biblioteka|poczta|galeria|centrum handlowe|biedronka"
     r"|lidl|zabka)\b"),
    (r"\b(sklep|sklepy|supermarket|restauracja|restauracje|apteka|bank|biblioteka|poczta|galeria|centrum handlowe|biedronka"
     r"|lidl|zabka)\b.*\b(otwarty|otwarta|otwarte|czynny|czynna|czynne|zamkniety|zamknieta|zamkniete|godziny otwarcia"
     r"|otwieraja|zamykaja)\b"),
    r"\b(moj lot|lot jest|lotu|opozniony|opoznienie|opoznienia|korek|korki|utrudnienia|awaria|wylaczenie pradu)\b",
    # The time now (not "która godzina będzie 45 minut po 15": the clock skill's).
    (r"^(?:ktora|jaka) (?:jest )?(?:teraz )?godzina\s*[?.!]*\s*$|^jaki (?:jest )?(?:dzis |dzisiaj )?dzien\s*[?.!]*\s*$"
     r"|^jaka (?:jest )?(?:dzis |dzisiaj )?data\s*[?.!]*\s*$|\bktorego (?:dzis|dzisiaj|mamy)\b"),
]
# Words that look live but are not, in questions that never change.
LIVE_EXCEPTIONS = [
    r"\bco (?:znaczy|oznacza)\b.*",
    r"\bjak (?:sie )?(?:pisze|przeliterowac|literuje)\b",
    r"\bprad (?:przemienny|staly)\b",
    r"\b(?:rano|z rana|nad ranem|wieczorem|w nocy|po poludniu|przed poludniem)\b",
]
_CURRENCIES = re.compile(r"\b(zlot\w*|zl|euro|dolar\w*|funt\w*|jen\w*|frank\w*|koron\w*)\b")


def is_live(q):
    s = re.sub(r"[,;]", " ", fold(q.lower()))
    for p in LIVE_EXCEPTIONS:
        s = re.sub(p, " ", s)
    s = " ".join(s.split())
    # Two currencies: an exchange rate ("ile złotych to jeden euro").
    if len({c[:3] for c in _CURRENCIES.findall(s)}) > 1:
        return True
    return any(re.search(p, s) for p in LIVE_PATTERNS)
