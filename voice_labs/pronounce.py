"""Pronunciations for the wake word verifier.

The verifier (wyoming-wake-kaldi) never guesses: every word of a wake phrase
or a confusable must be in its model's dictionary, or be given a
pronunciation in the model's own phones. For a word it does not have -- a
name, "nabu" -- the Wake words page offers a guess from espeak-ng, which
Piper bundles, written in those phones for the person to keep or correct. The
guess is saved as given: nothing is guessed at run time.

The model's phones are IPA with the stress mark on the vowel ("n ˈæ b u");
espeak's are close to them, with long vowels, diphthongs and affricates
spelled out.
"""

from __future__ import annotations

import logging
import re
import threading
from functools import lru_cache
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

VOICE = "en-us"

STRESS = ("ˈ", "ˌ")

VOWELS = frozenset({"ɑ", "æ", "ɛ", "i", "ɪ", "ɔ", "ʊ", "ʌ", "u", "ə", "ɚ", "eɪ", "aɪ", "oʊ", "ɔɪ", "aʊ"})

# espeak's symbols, longest first, as the model's phones ("" drops one).
_MAPPING: tuple[tuple[str, tuple[str, ...]], ...] = tuple(
    sorted(
        {
            "eɪ": ("eɪ",),
            "aɪ": ("aɪ",),
            "ɑɪ": ("aɪ",),
            "oʊ": ("oʊ",),
            "əʊ": ("oʊ",),
            "ɔɪ": ("ɔɪ",),
            "aʊ": ("aʊ",),
            "dʒ": ("d͡ʒ",),
            "d͡ʒ": ("d͡ʒ",),
            "tʃ": ("t͡ʃ",),
            "t͡ʃ": ("t͡ʃ",),
            "ɜɹ": ("ɚ",),
            "ɝ": ("ɚ",),
            "ɜ": ("ɚ",),
            "ɚ": ("ɚ",),
            "iə": ("i", "ə"),
            "ɑ": ("ɑ",),
            "ɒ": ("ɑ",),
            "a": ("æ",),
            "æ": ("æ",),
            "ɐ": ("ə",),
            "ə": ("ə",),
            "ɛ": ("ɛ",),
            "e": ("ɛ",),
            "i": ("i",),
            "ɪ": ("ɪ",),
            "ᵻ": ("ɪ",),
            "ɨ": ("ɪ",),
            "ɔ": ("ɔ",),
            "o": ("oʊ",),
            "ʊ": ("ʊ",),
            "ʌ": ("ʌ",),
            "u": ("u",),
            "y": ("u",),
            "p": ("p",),
            "b": ("b",),
            "t": ("t",),
            "d": ("d",),
            "ɾ": ("t",),
            "k": ("k",),
            "ɡ": ("ɡ",),
            "g": ("ɡ",),
            "f": ("f",),
            "v": ("v",),
            "θ": ("θ",),
            "ð": ("ð",),
            "s": ("s",),
            "z": ("z",),
            "ʃ": ("ʃ",),
            "ʒ": ("ʒ",),
            "h": ("h",),
            "ç": ("h",),
            "x": ("k",),
            "l": ("l",),
            "ɫ": ("l",),
            "m": ("m",),
            "n": ("n",),
            "ŋ": ("ŋ",),
            "ɹ": ("ɹ",),
            "r": ("ɹ",),
            "w": ("w",),
            "j": ("j",),
            "ʔ": (),
        }.items(),
        key=lambda kv: -len(kv[0]),
    )
)

# Length marks, syllabic and other diacritics, and syllable breaks: dropped.
_DROP = re.compile("[ːˑʲ\u0329\u0325\u032f\u0361.‿-]")

_lock = threading.Lock()


@lru_cache(maxsize=1)
def _phonemizer():
    try:
        from piper.phonemize_espeak import EspeakPhonemizer  # pylint: disable=import-outside-toplevel
    except ImportError:
        return None
    try:
        return EspeakPhonemizer()
    except Exception:  # noqa: BLE001 - no guesses is not an error
        _LOGGER.exception("espeak-ng is unavailable: no pronunciation guesses")
        return None


def espeak(word: str) -> str | None:
    """espeak-ng's IPA for ``word`` (American English), or None."""
    phonemizer = _phonemizer()
    if phonemizer is None:
        return None
    with _lock:  # one espeak instance
        sentences = phonemizer.phonemize(VOICE, word)
    return "".join("".join(s) for s in sentences).strip() or None


def to_phones(ipa: str) -> list[str] | None:
    """espeak IPA as the model's phones, the stress mark on the next vowel;
    None if a symbol has no counterpart."""
    text = _DROP.sub("", ipa.replace(" ", ""))
    phones: list[str] = []
    stress = ""
    at = 0
    while at < len(text):
        if text[at] in STRESS:
            stress = text[at]
            at += 1
            continue
        for symbol, mapped in _MAPPING:
            if text.startswith(symbol, at):
                for phone in mapped:
                    if stress and phone in VOWELS:
                        phone, stress = stress + phone, ""
                    phones.append(phone)
                at += len(symbol)
                break
        else:
            return None
    # "ɚ" already says the r, and a consonant is said once ("rural": ɹˈʊɹɹəl).
    out: list[str] = []
    for phone in phones:
        last = out[-1].lstrip("".join(STRESS)) if out else ""
        if (phone == "ɹ" and last == "ɚ") or (phone == last and phone not in VOWELS):
            continue
        out.append(phone)
    return out or None


def guess(word: str, inventory: set[str] | frozenset[str] | None = None) -> str | None:
    """A pronunciation of ``word`` in the model's phones, from espeak, or
    None. With ``inventory``, only one every phone of which it has."""
    ipa = espeak(word)
    if ipa is None:
        return None
    phones = to_phones(ipa)
    if phones is None:
        _LOGGER.debug("No phones for %r (espeak: %s)", word, ipa)
        return None
    if inventory is not None and any(p not in inventory for p in phones):
        return None
    return " ".join(phones)


def model_phones(model_dir: Path) -> list[tuple[str, str, str]]:
    """(phone, example word, its pronunciation) for each of the model's
    phones, from its ``phoneme_examples.txt``."""
    path = Path(model_dir) / "phoneme_examples.txt"
    out = []
    try:
        for line in path.read_text("utf-8").splitlines():
            parts = line.split(maxsplit=2)
            if len(parts) == 3:
                out.append((parts[0], parts[1], parts[2]))
    except OSError:
        pass
    return out
