"""How a name changes in a sentence, per language.

German inflects names -- "die Lampen des Wohnzimmers", "den kleinen
Ventilator" for a fan named "Kleiner Ventilator" -- and joins a room to what
is in it with a linking letter: "das Küchenlicht" is Küche + n + Licht. The
acoustic model writes all of these as one word, the ending on the name. So a
word of a name may be heard with one of its language's endings added, or in
place of one it ends with (``word_forms``), and one lattice spells them all
(:meth:`graph.PhraseGraph.add`). English has none: its names are said as
written.

French joins an article or "de" to a name that starts with a vowel: "dans
l'entrée", "la lumière de l'atelier" -- written as one word, so a name's
first word may be heard with it (``elided``).
"""

from __future__ import annotations

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
}
"""The endings a word of a name may take, by language."""

ELISIONS: dict[str, tuple[str, ...]] = {
    "fr": ("l'", "d'"),
}
"""What a name's first word may be heard joined to, by language, when it
starts with a vowel or an h: "l'entrée", "d'Isabelle"."""

MIN_STEM = 3
"""Letters left of a word when one of its endings is taken off for another:
"Bad" keeps its "d"."""


def endings(language: str) -> tuple[str, ...]:
    """The endings for ``language`` ("de", "de-CH", "de_DE")."""
    return ENDINGS.get(language.replace("_", "-").split("-")[0].lower(), ())


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
