"""Run the voice_call app against a device on the local network."""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from .server import Options, VoiceCallApp

_LOGGER = logging.getLogger(__name__)


async def main() -> None:
    parser = argparse.ArgumentParser(prog="voice_call")
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
    parser.add_argument("--language", default="en")
    parser.add_argument("--silence", type=float, default=1.0)
    parser.add_argument("--max-command", type=float, default=15.0)
    parser.add_argument(
        "--max-score",
        type=float,
        help="per-token acoustic penalty gate (default: speech-to-phrase's "
        "calibrated value for the backend)",
    )
    parser.add_argument("--save-dir", help="write each call's audio here")
    parser.add_argument(
        "--no-second-pass",
        action="store_true",
        help="skip the Parakeet TDT re-transcription (saves ~590 MB)",
    )
    parser.add_argument("--tdt-threads", type=int, default=2)
    parser.add_argument(
        "--wake-model",
        action="append",
        metavar="NAME",
        help="wake word model to push to the device, by file stem in "
        "models/wake (repeatable; default: okay_nabu and vad)",
    )
    parser.add_argument(
        "--wake-cutoff",
        type=float,
        metavar="P",
        help="override every wake word model's probability cutoff; the stock "
        "0.85 expects a voice at the device, a loudspeaker across a room "
        "needs roughly 0.5",
    )
    parser.add_argument(
        "--wake-ratio",
        type=float,
        metavar="R",
        help="per-frame threshold for the second opinion on a wake word the "
        "Kaldi verifier rejected on its phrase or last-word test (default "
        "1.2; negative disables it, and a confusable rejection always "
        "stands)",
    )
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    token = Path(args.hass_token_file).read_text(encoding="utf-8").strip()

    app = VoiceCallApp(
        Options(
            host=args.host,
            port=args.port,
            hass_url=args.hass_url,
            hass_token=token,
            language=args.language,
            silence_seconds=args.silence,
            max_command_seconds=args.max_command,
            max_score=args.max_score,
            save_dir=Path(args.save_dir) if args.save_dir else None,
            second_pass=not args.no_second_pass,
            tdt_threads=args.tdt_threads,
            wake_models=tuple(args.wake_model) if args.wake_model else ("okay_nabu", "vad"),
            wake_probability_cutoff=args.wake_cutoff,
            wake_ratio=args.wake_ratio,
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
