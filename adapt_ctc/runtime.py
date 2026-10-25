"""onnxruntime sessions that give memory back, and the C heap giving it back.

onnxruntime's CPU memory arena keeps the largest activations a session has
needed for the life of the process: after one 16 s call, Parakeet's encoder
holds 590 MB rather than the 355 MB it loads at (measured on an HA Green,
Cortex-A55). Without the arena, those activations are plain malloc'd memory,
which glibc keeps in turn until ``release_memory`` hands it back: the encoder is
then at 252 MB, Piper at 87 MB rather than 146, speaker ID at 46 rather than
112, GLiNER at 214 rather than 349 -- at the same speed, on the Green and on
x86. (Turning off weight prepacking saves 86 MB more of the encoder but makes
it 55% slower on the Green.)
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging

_LOGGER = logging.getLogger(__name__)

_malloc_trim = None
try:
    _malloc_trim = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6").malloc_trim
    _malloc_trim.argtypes = [ctypes.c_size_t]
except (AttributeError, OSError):  # not glibc
    pass


def session_options(threads: int = 0):
    """``threads`` intra-op threads (0: onnxruntime's default, one a core),
    one inter-op thread, no CPU memory arena."""
    import onnxruntime as ort  # pylint: disable=import-outside-toplevel

    options = ort.SessionOptions()
    options.inter_op_num_threads = 1
    options.intra_op_num_threads = threads
    options.enable_cpu_mem_arena = False
    return options


def release_memory() -> None:
    """Return the heap's free pages to the system (glibc's ``malloc_trim``; a
    few milliseconds). Without it, what a call freed stays the process's."""
    if _malloc_trim is not None:
        _malloc_trim(0)
