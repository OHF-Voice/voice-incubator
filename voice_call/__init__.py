"""Experimental voice_call app: ESPHome device -> wake verify -> STT -> HA -> TTS."""

# llama.cpp (the LLM fallback, voice_call/fallback.py) and pysilero-vad
# (endpointing) each ship a libggml under the same soname, and whichever is
# loaded first serves both. Only llama.cpp's has everything both need, so it
# goes first when it is installed.
try:
    import llama_cpp  # noqa: F401  # pylint: disable=unused-import
except ImportError:
    pass
