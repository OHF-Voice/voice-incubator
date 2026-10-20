"""How a name changes in a sentence, per language.

German inflects names -- "die Lampen des Wohnzimmers", "den kleinen
Ventilator" for a fan named "Kleiner Ventilator" -- and joins a room to what
is in it with a linking letter: "das Küchenlicht" is Küche + n + Licht. The
acoustic model writes all of these as one word, the ending on the name. So a
word of a name may be heard with one of its language's endings added, or in
place of one it ends with (``word_forms``), and one lattice spells them all
(:meth:`graph.PhraseGraph.add`). English has none: its names are said as
written.
"""

from __future__ import annotations

ENDINGS: dict[str, tuple[str, ...]] = {
    # Nouns (des Wohnzimmers, des Hauses, den Lichtern), adjectives (kleine,
    # kleiner, kleines, kleinen, kleinem), and the linking letters of a
    # compound (Küchen-, Arbeits-, Kinder-, Namens-).
    "de": ("e", "s", "n", "en", "er", "es", "em", "ns"),
}
"""The endings a word of a name may take, by language."""

MIN_STEM = 3
"""Letters left of a word when one of its endings is taken off for another:
"Bad" keeps its "d"."""


def endings(language: str) -> tuple[str, ...]:
    """The endings for ``language`` ("de", "de-CH", "de_DE")."""
    return ENDINGS.get(language.replace("_", "-").split("-")[0].lower(), ())


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
