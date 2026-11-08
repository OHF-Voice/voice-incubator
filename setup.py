"""Build adapt_ctc's optional native searches in place:

    python setup.py build_ext --inplace

Without a compiler the recognizer uses the equivalent numpy implementation,
which gives bit-identical scores but is several times slower; the grammar's
search (adapt_ctc/grammar.py) falls back on Python, too slow for every call.
"""

from setuptools import Extension, setup

setup(
    name="voice_labs",
    packages=[],
    ext_modules=[
        Extension(
            "adapt_ctc._viterbi",
            ["adapt_ctc/_viterbi.c"],
            define_macros=[("Py_LIMITED_API", "0x030B0000")],
            py_limited_api=True,
            optional=True,
        ),
        Extension(
            "adapt_ctc._grammar",
            ["adapt_ctc/_grammar.c"],
            define_macros=[("Py_LIMITED_API", "0x030B0000")],
            py_limited_api=True,
            optional=True,
        ),
    ],
    options={"bdist_wheel": {"py_limited_api": "cp311"}},
)
