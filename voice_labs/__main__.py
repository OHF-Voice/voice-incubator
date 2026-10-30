"""Run the voice_labs app against devices on the local network.

As a Home Assistant app (``SUPERVISOR_TOKEN`` is set) it talks to Home
Assistant through the Supervisor's proxy, keeps its data in /data, serves the
web UI to ingress only, and reads its options from /data/options.json.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from pathlib import Path

from adapt_ctc.embedding import DEFAULT_EMBEDDING_MODEL

from .server import Options, VoiceLabsApp

_LOGGER = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
SUPERVISOR_URL = "http://supervisor/core"
SUPERVISOR_WEBSOCKET = "ws://supervisor/core/websocket"
INGRESS_PROXY = "172.30.32.2"
OPTIONS_FILE = Path("/data/options.json")


def _default_embedding() -> Path:
    """models/embeddinggemma-2 when it is there -- or linked to where the app
    downloads it (voice_labs/downloads.py) -- else the default."""
    bundled = ROOT / "models/embeddinggemma-2"
    return bundled if bundled.is_symlink() or (bundled / "onnx/model_q4.onnx").exists() else DEFAULT_EMBEDDING_MODEL


async def main() -> None:
    parser = argparse.ArgumentParser(prog="voice_labs")
    parser.add_argument(
        "--host",
        help="pin to one device instead of serving every satellite mDNS finds; "
        "useful across subnets and for testing one device in isolation",
    )
    parser.add_argument("--port", type=int, default=6055)
    parser.add_argument("--hass-url", default="http://localhost:8123")
    parser.add_argument(
        "--hass-token-file",
        default="/home/hansenm/opt/homeassistant/token",
        help="file holding a long-lived access token",
    )
    parser.add_argument(
        "--data-dir",
        help="commands, satellites, settings and saved calls (default var/, "
        "or /data as an app)",
    )
    parser.add_argument("--http-host", default="127.0.0.1", help="web UI address")
    parser.add_argument("--http-port", type=int, default=8099, help="web UI port")
    parser.add_argument("--language", default="en", help="what it hears and says (voice_labs/languages)")
    parser.add_argument("--silence", type=float, default=1.0)
    parser.add_argument("--max-command", type=float, default=15.0)
    parser.add_argument("--save-dir", help="also write each call's audio here, per device")
    parser.add_argument(
        "--no-tdt",
        action="store_true",
        help="skip Parakeet's TDT head (saves ~350 MB; examples are then only "
        "found in the CTC evidence, and free text is spelled as the CTC head heard it)",
    )
    parser.add_argument(
        "--embedding-model",
        default=str(_default_embedding()),
        help="EmbeddingGemma 2 ONNX snapshot, used only to name the closest "
        "command when nothing matched ('none' to skip it)",
    )
    parser.add_argument("--threads", type=int, default=2, help="ONNX threads per model")
    parser.add_argument(
        "--wake-model",
        action="append",
        metavar="NAME",
        help="a satellite's wake word when none is picked in the UI, by id "
        "(repeatable; default: okay_nabu)",
    )
    parser.add_argument(
        "--wake-words-dir",
        metavar="DIR",
        help="custom wake words: microWakeWord manifests (.json) and models "
        "(.tflite) (default wake_words/, or /config/wake_words as an app)",
    )
    parser.add_argument(
        "--wake-training-kit",
        action="append",
        metavar="DIR",
        help="where the wake word training kit may be (scripts/make_wake_training_kit.py; "
        "repeatable; default models/wake-training, and /config/wake_training as an app)",
    )
    parser.add_argument(
        "--wake-trainer-python",
        metavar="PYTHON",
        help="a Python with TensorFlow and microWakeWord to train wake words with (default: this one)",
    )
    parser.add_argument(
        "--micro-wake-word",
        metavar="DIR",
        help="another microWakeWord's source to train with (default: the one vendored with the app)",
    )
    parser.add_argument("--wake-training-clips", type=int, metavar="N", help=argparse.SUPPRESS)
    parser.add_argument("--wake-training-steps", type=int, metavar="N", help=argparse.SUPPRESS)
    parser.add_argument(
        "--wake-cutoff",
        type=float,
        metavar="P",
        help="override the first-stage cutoff of every verified wake word "
        "(default: each its own -- okay_nabu 0.2; custom wake words keep their "
        "manifests'). The manifests ship 0.85 and up, which "
        "expects a voice at the device; the verifier is what refuses what a "
        "lower cutoff lets through",
    )
    parser.add_argument(
        "--wake-tail",
        type=float,
        metavar="S",
        help="seconds of audio from after the detection to add before "
        "verifying again, when the first attempt put the wake phrase at the "
        "very end of the pre-roll (default 0.3; 0 disables the retry)",
    )
    parser.add_argument(
        "--wake-ratio",
        type=float,
        metavar="R",
        help="per-frame threshold for the second opinion on a wake word the "
        "Kaldi verifier rejected on its phrase or last-word test (default "
        "1.3; negative disables it, and a confusable rejection always "
        "stands)",
    )
    parser.add_argument(
        "--no-speaker-id",
        action="store_true",
        help="do not identify speakers (no speaker in the processing event)",
    )
    parser.add_argument(
        "--speaker-profiles",
        metavar="PATH",
        help="speaker profiles file (default var/speakers.json)",
    )
    parser.add_argument(
        "--speaker-threshold",
        type=float,
        default=0.45,
        help="cosine score to accept a speaker (default 0.45: in benchmarking on "
        "VPE audio, ~0.5%% of an un-enrolled speaker's commands get through)",
    )
    parser.add_argument(
        "--speaker-channel",
        choices=("aec", "agc"),
        default="aec",
        help="capture to identify from (default aec; it needs the firmware's "
        "aec_microphone gain_factor, see voice-pe-alt-firmware's voice-labs.yaml)",
    )
    parser.add_argument(
        "--no-speaker-learn",
        action="store_true",
        help="keep profiles fixed instead of learning from confident calls",
    )
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    supervisor_token = os.environ.get("SUPERVISOR_TOKEN")
    app_options: dict = {}
    if supervisor_token and OPTIONS_FILE.exists():
        app_options = json.loads(OPTIONS_FILE.read_text(encoding="utf-8"))
    if app_options.get("debug_logging"):
        args.debug = True
    for option, attribute in (
        ("wake_cutoff", "wake_cutoff"),
        ("speaker_threshold", "speaker_threshold"),
        ("silence_seconds", "silence"),
    ):
        if app_options.get(option) is not None:
            setattr(args, attribute, app_options[option])

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    if supervisor_token:
        hass = {
            "hass_url": SUPERVISOR_URL,
            "hass_token": supervisor_token,
            "hass_websocket_url": SUPERVISOR_WEBSOCKET,
            "data_dir": Path(args.data_dir or "/data"),
            "http_host": "0.0.0.0",
            "http_port": args.http_port,
            "http_allow": (INGRESS_PROXY,),
        }
    else:
        hass = {
            "hass_url": args.hass_url,
            "hass_token": Path(args.hass_token_file).read_text(encoding="utf-8").strip(),
            "data_dir": Path(args.data_dir) if args.data_dir else None,
            "http_host": args.http_host,
            "http_port": args.http_port,
        }
    embedding = None if args.embedding_model in ("", "none") else Path(args.embedding_model)

    app = VoiceLabsApp(
        Options(
            host=args.host,
            port=args.port,
            **hass,
            language=args.language,
            silence_seconds=args.silence,
            max_command_seconds=args.max_command,
            save_dir=Path(args.save_dir) if args.save_dir else None,
            tdt=not args.no_tdt,
            embedding_dir=embedding,
            threads=args.threads,
            wake_models=tuple(args.wake_model) if args.wake_model else ("okay_nabu",),
            wake_words_dir=(
                Path(args.wake_words_dir)
                if args.wake_words_dir
                else (Path("/config/wake_words") if supervisor_token else None)
            ),
            wake_training_dirs=tuple(
                Path(d) for d in (args.wake_training_kit or (["/config/wake_training"] if supervisor_token else []))
            ),
            wake_trainer_python=args.wake_trainer_python,
            micro_wake_word=Path(args.micro_wake_word) if args.micro_wake_word else None,
            wake_training_clips=args.wake_training_clips,
            wake_training_steps=args.wake_training_steps,
            **({} if args.wake_cutoff is None else {"wake_probability_cutoff": args.wake_cutoff}),
            wake_ratio=args.wake_ratio,
            **({} if args.wake_tail is None else {"wake_tail": args.wake_tail}),
            speaker_id=not args.no_speaker_id,
            speaker_profiles=Path(args.speaker_profiles) if args.speaker_profiles else None,
            speaker_threshold=args.speaker_threshold,
            speaker_channel=args.speaker_channel,
            speaker_learn=not args.no_speaker_learn,
        )
    )
    await app.start()
    try:
        await app.run_forever()
    finally:
        await app.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
