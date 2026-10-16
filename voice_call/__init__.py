"""Experimental voice_call app: ESPHome device -> wake verify -> STT -> HA -> TTS."""

# llama.cpp (the LLM fallback, voice_call/fallback.py) and pysilero-vad
# (endpointing) each ship a libggml under the same soname, and whichever is
# loaded first serves both. Only llama.cpp's has everything both need, so it
# goes first when it is installed.
try:
    import llama_cpp  # noqa: F401  # pylint: disable=unused-import
    from llama_cpp import _internals as _llama_internals

    class _KeepStderr:
        """llama.cpp's Python binding points the whole process's stdout and
        stderr at /dev/null while it loads a model -- and the models load in
        the background, so other threads' log lines vanished meanwhile. Its
        own messages go through a logger that is quiet anyway."""

        def __init__(self, disable: bool = True) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_exc) -> None:
            return None

    _llama_internals.suppress_stdout_stderr = _KeepStderr
except ImportError:
    pass
