"""How a name changes in a sentence, per language.

German inflects names -- "die Lampen des Wohnzimmers", "den kleinen
Ventilator" for a fan named "Kleiner Ventilator" -- and joins a room to what
is in it with a linking letter: "das Küchenlicht" is Küche + n + Licht. The
acoustic model writes all of these as one word, the ending on the name. So a
word of a name may be heard with one of its language's endings added, or in
place of one it ends with (``word_forms``), and one lattice spells them all
(:meth:`graph.PhraseGraph.add`). English has none: its names are said as
written.

French and Italian join an article or a preposition to a name that starts
with a vowel: "dans l'entrée", "nell'ingresso", "la luce dell'ufficio" --
written as one word, so a name's first word may be heard with it
(``elided``).

Polish, Russian and Ukrainian decline names by case -- "w kuchni" for the
Kuchnia, "в гостиной", "у вітальні" -- with endings as German's; Polish and
Ukrainian also change the consonant before some ("w łazience" for the
Łazienka, "на першому поверсі" for the Перший поверх), which a word's
``ALTERNATIONS`` give.
"""

from __future__ import annotations

import re

ENDINGS: dict[str, tuple[str, ...]] = {
    # Nouns (des Wohnzimmers, des Hauses, den Lichtern), adjectives (kleine,
    # kleiner, kleines, kleinen, kleinem), and the linking letters of a
    # compound (Küchen-, Arbeits-, Kinder-, Namens-).
    "de": ("e", "s", "n", "en", "er", "es", "em", "ns"),
    # Plurals and adjectives (lampen, grote), the genitive and linking -s
    # (woonkamerslampen), -en- (keukenlamp).
    "nl": ("e", "s", "en", "n"),
    # Plurals: les lampes, las lámparas, los ventiladores.
    "fr": ("s",),
    "es": ("s", "es"),
    # A plural or an adjective's agreement takes the last vowel's place:
    # lampada, lampade; faretto, faretti; piccolo, piccola.
    "it": ("a", "e", "i", "o"),
    # Cases, singular and plural, of nouns and the adjectives before them:
    # kuchnia, w kuchni; salon, w salonie; pokój dzienny, w pokoju dziennym;
    # lampa, lampę.
    "pl": ("a", "y", "i", "e", "ę", "ą", "u", "o", "ie", "om", "ach", "ami", "ów", "owi", "em", "ym", "im", "ej",
           "ego", "emu", "ych", "ich", "ymi", "imi"),
    # кухня, на кухне; гостиная, в гостиной; первый этаж, на первом этаже.
    "ru": ("а", "я", "у", "ю", "е", "и", "ы", "ь", "ой", "ей", "ом", "ем", "ам", "ям", "ах", "ях", "ами", "ями",
           "ая", "яя", "ую", "юю", "ое", "ее", "ые", "ие", "ый", "ий", "ых", "их", "ого", "его", "ому", "ему", "ым",
           "им"),
    # кухня, на кухні; вітальня, у вітальні; ванна, у ванній; коридор, у коридорі.
    "uk": ("а", "я", "у", "ю", "і", "ї", "и", "е", "є", "ь", "ою", "ею", "єю", "ій", "ої", "ом", "ем", "ам", "ям",
           "ах", "ях", "ами", "ями", "ові", "еві", "ий", "ого", "ому", "им", "их", "ими", "ую", "юю", "ої"),
}
"""The endings a word of a name may take, by language."""

ALTERNATIONS: dict[str, tuple[tuple[str, str], ...]] = {
    # łazienka, w łazience; biuro, w biurze; gabinet, w gabinecie; ogród, w
    # ogrodzie; pokój, w pokoju; stół, na stole; parter, na parterze.
    "pl": (("ka", "ce"), ("ga", "dze"), ("cha", "sze"), ("ra", "rze"), ("ro", "rze"), ("r", "rze"), ("ta", "cie"),
           ("to", "cie"), ("t", "cie"), ("da", "dzie"), ("do", "dzie"), ("d", "dzie"), ("ła", "le"), ("ło", "le"),
           ("ł", "le"), ("sta", "ście"), ("sto", "ście"), ("st", "ście"), ("zd", "ździe"), ("ój", "oju"),
           ("ód", "odzie"), ("ół", "ole"), ("ór", "orze"), ("óz", "ozie"), ("ók", "oku")),
    # потолок, на потолке: a vowel that drops.
    "ru": (("ок", "ке"), ("ок", "ка"), ("ец", "це"), ("ец", "ца")),
    # бібліотека, у бібліотеці; поверх, на поверсі; двір, у дворі; дорога, на дорозі.
    "uk": (("ка", "ці"), ("к", "ці"), ("га", "зі"), ("г", "зі"), ("ха", "сі"), ("х", "сі"), ("ір", "орі"),
           ("іл", "олі"), ("ік", "оку")),
}
"""How a word's end may change, besides an ending, by language: (as written,
as said)."""

ELISIONS: dict[str, tuple[str, ...]] = {
    "fr": ("l'", "d'"),
    "it": ("l'", "dell'", "nell'", "all'", "sull'", "dall'", "un'"),
}
"""What a name's first word may be heard joined to, by language, when it
starts with a vowel or an h: "l'entrée", "d'Isabelle"."""

MIN_STEM = 3
"""Letters left of a word when one of its endings is taken off for another:
"Bad" keeps its "d"."""


def endings(language: str) -> tuple[str, ...]:
    """The endings for ``language`` ("de", "de-CH", "de_DE")."""
    return ENDINGS.get(language.replace("_", "-").split("-")[0].lower(), ())


SINGLED = ("uk",)
"""Languages whose model hears a doubled letter as one: Ukrainian's writes
"у ванній" as "у ваній" (the Ванна кімната)."""


def singled(forms: tuple[str, ...], language: str) -> tuple[str, ...]:
    """``forms`` and, in a language of ``SINGLED``, each with its doubled
    letters said once: "ванній" -> also "ваній"."""
    if language.replace("_", "-").split("-")[0].lower() not in SINGLED:
        return forms
    return tuple(dict.fromkeys([*forms, *(re.sub(r"(\w)\1", r"\1", form) for form in forms)]))


def alternations(language: str) -> tuple[tuple[str, str], ...]:
    """The alternations for ``language`` ("pl", "uk-UA")."""
    return ALTERNATIONS.get(language.replace("_", "-").split("-")[0].lower(), ())


def alternated(forms: tuple[str, ...], changes: tuple[tuple[str, str], ...]) -> tuple[str, ...]:
    """``forms`` and, for the word as written (the first), each of
    ``changes`` its end takes: "łazienka" -> also "łazience"."""
    if not forms:
        return forms
    word = forms[0]
    extra = [word[: -len(end)] + said for end, said in changes if word.endswith(end) and len(word) - len(end) >= 2]
    return tuple(dict.fromkeys([*forms, *extra]))


def elisions(language: str) -> tuple[str, ...]:
    """The elisions for ``language`` ("fr", "fr-CA")."""
    return ELISIONS.get(language.replace("_", "-").split("-")[0].lower(), ())


def elided(forms: tuple[str, ...], prefixes: tuple[str, ...]) -> tuple[str, ...]:
    """``forms`` and, for those starting with a vowel or an h, each with a
    prefix: "entrée" -> "entrée", "l'entrée", "d'entrée"."""
    vowels = "aeiouyhàâäéèêëîïôöùûü"
    return forms + tuple(prefix + form for prefix in prefixes for form in forms if form[:1] in vowels)


def word_forms(word: str, endings: tuple[str, ...]) -> tuple[str, ...]:
    """``word`` (lowercase) and every form of it with ``endings``: each one
    added, or put in place of one it ends with -- "kleiner" is also
    "kleinen", "küche" also "küchen" and "küch" (Küchenlicht, Schul-). The
    word as written comes first; without endings it is the only one."""
    if not endings:
        return (word,)
    stems = [word] + [
        word[: -len(ending)]
        for ending in endings
        if word.endswith(ending) and len(word) - len(ending) >= MIN_STEM
    ]
    forms = dict.fromkeys([word])
    for stem in stems:
        for ending in ("", *endings):
            forms[stem + ending] = None
    return tuple(forms)
