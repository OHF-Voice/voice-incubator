"""``adapt-ctc CONFIG WAV...``: rank a config's intents against recorded commands."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import replace
from pathlib import Path

from .acoustic import CtcModel
from .audio import load_wav
from .config import ConfigError, load_config
from .matcher import Recognizer
from .report import render_text, render_wake, to_json, wake_json
from .embedding import DEFAULT_EMBEDDING_MODEL, load_embedder
from .tdt import DEFAULT_TDT_MODEL, load_transcriber
from .wake import WakeVerifier, recognize_call

# The app's own models directory, next to this package.
MODELS_DIR = Path(__file__).resolve().parent.parent / "models"
DEFAULT_MODEL = (
    MODELS_DIR / "speech-to-phrase/"
    "stt_en_parakeet_tdt_ctc_110m-int8"
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="adapt-ctc",
        description="Rank intents for WAV files by mining Parakeet CTC probabilities.",
    )
    parser.add_argument("config", type=Path, help="YAML intent configuration")
    parser.add_argument("wav", type=Path, nargs="+", help="WAV file(s) to recognize")
    parser.add_argument(
        "--model",
        type=Path,
        default=Path(os.environ.get("ADAPT_CTC_MODEL", DEFAULT_MODEL)),
        help="CTC model directory with model.onnx and tokens.txt "
        "(default: $ADAPT_CTC_MODEL or voice_call's int8 Parakeet)",
    )
    parser.add_argument(
        "--tdt-model",
        type=Path,
        default=Path(os.environ.get("ADAPT_CTC_TDT_MODEL", DEFAULT_TDT_MODEL)),
        help="TDT model directory (encoder/decoder/joiner.onnx) for intents with "
        "example sentences (default: $ADAPT_CTC_TDT_MODEL or voice_call's int8 Parakeet TDT)",
    )
    parser.add_argument(
        "--embedding-model",
        type=Path,
        default=Path(os.environ.get("ADAPT_CTC_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)),
        help="EmbeddingGemma 2 ONNX snapshot (onnx/model_q4.onnx, tokenizer.json), for "
        "examples said in other words (default: $ADAPT_CTC_EMBEDDING_MODEL or the "
        "Hugging Face cache of onnx-community/embeddinggemma-2-ONNX)",
    )
    parser.add_argument("--threads", type=int, default=4, help="ONNX intra-op threads")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of text")
    parser.add_argument(
        "--spans", action="store_true", help="Also list every phrase placement found"
    )
    parser.add_argument(
        "--call",
        action="store_true",
        help="Inputs are whole calls starting with the wake word: verify it in the "
        "pre-roll (the config's `wake` section), then recognize the command after it",
    )
    parser.add_argument(
        "--full-search",
        action="store_true",
        help="Score every phrase everywhere (slower; rejection reasons can then "
        "quote the closest placement of phrases far outside the gate)",
    )
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except (ConfigError, OSError) as err:
        print(f"adapt-ctc: {args.config}: {err}", file=sys.stderr)
        return 2
    if args.full_search:
        config = replace(config, settings=replace(config.settings, search="full"))
    if args.call and config.wake is None:
        print(f"adapt-ctc: {args.config}: --call needs a `wake` section", file=sys.stderr)
        return 2

    started = time.perf_counter()
    model = CtcModel(args.model, num_threads=args.threads)
    transcriber = embedder = None
    if any(intent.examples for intent in config.intents.values()):
        transcriber = load_transcriber(args.tdt_model, args.threads, "adapt-ctc")
        embedder = load_embedder(args.embedding_model, args.threads, "adapt-ctc")
    loaded = time.perf_counter() - started
    recognizer = Recognizer(config, model, transcriber=transcriber, embedder=embedder)
    for phrase in recognizer.unspeakable:
        print(f"adapt-ctc: warning: cannot spell {phrase!r} in model pieces", file=sys.stderr)

    verifier = WakeVerifier(config.wake, model.vocab, config.language) if args.call else None

    outputs = []
    for wav in args.wav:
        wake = None
        if verifier is not None:
            call = recognize_call(recognizer, verifier, load_wav(wav))
            wake, recognition = call.wake, call.command
            timings = call.timings
        else:
            recognition = recognizer.recognize(load_wav(wav))
        if args.json:
            data = to_json(recognition, include_spans=args.spans) if recognition else {}
            if wake is not None:
                data["wake"] = wake_json(wake)
                data["timings"] = {k: round(v, 4) for k, v in timings.items()}
            data["file"] = str(wav)
            outputs.append(data)
        else:
            if len(args.wav) > 1:
                print(f"== {wav}")
            if wake is not None:
                print(render_wake(wake))
                if recognition is None:
                    print(
                        "timing: "
                        + ", ".join(f"{k} {v * 1000:.0f} ms" for k, v in timings.items())
                        + "\n"
                    )
                    continue
                recognition.timings = timings
            print(render_text(recognition, show_spans=args.spans))

    if args.json:
        payload = {
            "model_load_seconds": round(loaded, 3),
            "graph": {**recognizer.graph_stats, "build_seconds": round(recognizer.build_seconds, 3)},
            "results": outputs,
        }
        print(json.dumps(payload, indent=2))
    else:
        stats = recognizer.graph_stats
        print(
            f"(model loaded in {loaded * 1000:.0f} ms; graph of {stats['arcs']} arcs, "
            f"{stats['terminals']} phrase ends, built in {recognizer.build_seconds * 1000:.0f} ms"
            + (
                f"; examples embedded in {recognizer.embed_seconds * 1000:.0f} ms)"
                if recognizer.embed_seconds
                else ")"
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
