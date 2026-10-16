"""Questions no command and no skill took: a small LLM answers them in one
short sentence (Granite 4.0 350M: 128 of 148 such questions right on the
smart-home quick-questions benchmark, ~0.2 s on a desktop CPU, ~0.45 s on a
Pi 5).

Only what reads as a question is asked ("thank you" is not), and never one
about now or here (skills/live.py): it cannot know, and would make something
up. Exact answers -- spelling, arithmetic, units, dates -- are skills
(voice_call/skills/).
"""

from __future__ import annotations

import logging
import re
import threading
import time
from pathlib import Path

from .skills import Answer
from .skills.live import is_live

_LOGGER = logging.getLogger(__name__)

_QUESTION = re.compile(
    r"^(?:(?:in|at|on|from|by|for|to) (?:what|which|whom|whose)|"
    r"what|what's|whats|who|who's|whom|whose|when|where|why|how|which|is|are|was|were|can|could|"
    r"do|does|did|will|would|should|define|explain|tell me|name|list)\b",
    re.I,
)


def looks_like_a_question(text: str) -> bool:
    """A question, not a request or a remark: a knowledge LLM answers the one
    and would only make something up for the others ("thank you")."""
    text = text.strip()
    return text.endswith("?") or bool(_QUESTION.match(_strip_lead(text)))


def _strip_lead(text: str) -> str:
    return re.sub(r"^(?:hey|ok|okay|so|um|uh|and|but|please)[,\s]+", "", text.strip(), flags=re.I)


def classify(text: str) -> str | None:
    """live | knowledge | None (not a question to answer)."""
    if is_live(text):
        return "live"
    if looks_like_a_question(text):
        return "knowledge"
    return None


# ------------------------------------------------------------------ the LLM

QA_MODELS = {
    # id: (name, Hugging Face repository, file, MB). On the benchmark's 148
    # knowledge questions (Q8_0, plain prompt): Granite 128 right in ~0.2 s on
    # a desktop CPU and ~0.45 s on a Pi 5; Gemma 3 1B 137 in ~0.6 s on a
    # desktop, for a faster machine than a Pi (an N100).
    "granite-4.0-350m": ("Granite 4.0 350M", "ibm-granite/granite-4.0-350m-GGUF", "granite-4.0-350m-Q8_0.gguf", 370),
    "gemma-3-1b": ("Gemma 3 1B", "ggml-org/gemma-3-1b-it-GGUF", "gemma-3-1b-it-Q8_0.gguf", 1070),
}
DEFAULT_QA_MODEL = "granite-4.0-350m"
QA_PROMPT = "You are a voice assistant in a smart home. Answer the user's question in one short sentence."
QA_MAX_TOKENS = 64


class Answerer:
    """A small LLM for knowledge questions, in llama.cpp. ``answer`` is slow:
    call it from a thread."""

    def __init__(self, model_path: Path, n_threads: int = 2, model: str = DEFAULT_QA_MODEL) -> None:
        from llama_cpp import Llama  # pylint: disable=import-outside-toplevel

        self.model = model
        started = time.perf_counter()
        self.llm = Llama(model_path=str(model_path), n_ctx=512, n_threads=n_threads, verbose=False)
        self.load_seconds = time.perf_counter() - started
        self._lock = threading.Lock()
        _LOGGER.info("Question model ready: %s (%.1f s)", Path(model_path).name, self.load_seconds)

    @staticmethod
    def find_model(
        model: str = DEFAULT_QA_MODEL, download: bool = False, cache_dir: Path | None = None
    ) -> Path | None:
        from .fallback import find_gguf  # pylint: disable=import-outside-toplevel

        _name, repo, filename, _size = QA_MODELS[model]
        return find_gguf(repo, filename, download, cache_dir)

    def answer(self, question: str) -> tuple[str, float]:
        with self._lock:
            started = time.perf_counter()
            response = self.llm.create_chat_completion(
                messages=[{"role": "system", "content": QA_PROMPT}, {"role": "user", "content": question.strip()}],
                temperature=0.0,
                max_tokens=QA_MAX_TOKENS,
            )
            seconds = time.perf_counter() - started
        text = (response["choices"][0]["message"].get("content") or "").strip()
        return _first_sentence(text), seconds


def _first_sentence(text: str) -> str:
    """Said, not read: no markdown, and one sentence (a small model rambles)."""
    text = re.sub(r"[*_#`]+", "", text).strip()
    found = re.match(r"(.+?[.!?])(\s|$)", text, re.S)
    return (found.group(1) if found else text).strip()


class Questions:
    """The question model's answers, with an ``Answerer``."""

    def __init__(self, answerer: Answerer | None = None) -> None:
        self.answerer = answerer

    def answer(self, text: str) -> Answer | None:
        """The LLM's answer to a question; None when it is not one, or about
        now or here, or there is no LLM."""
        kind = classify(text) if text.strip() else None
        if kind == "knowledge" and self.answerer is not None:
            said, seconds = self.answerer.answer(text)
            if said:
                return Answer("knowledge", said, seconds=seconds)
        return None
