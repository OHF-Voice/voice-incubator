"""Wyoming wake service entry point."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
from functools import partial
from pathlib import Path

from wyoming.info import Attribution, Info, WakeModel, WakeProgram
from wyoming.server import AsyncServer

from . import __version__
from .config import ConfigError, ServiceConfig, load_config
from .handler import WakeKaldiEventHandler
from .service import WakeService

_LOGGER = logging.getLogger(__name__)

_ATTRIBUTION = Attribution(
    name="rhasspy",
    url="https://github.com/rhasspy/en-us_kaldi-rhasspy",
)


def build_info(config: ServiceConfig) -> Info:
    models = [
        WakeModel(
            name=wake_word.id,
            description=wake_word.description or wake_word.phrase_text,
            attribution=_ATTRIBUTION,
            installed=True,
            languages=[config.models[wake_word.model].language],
            phrase=wake_word.phrase_text,
            version=None,
        )
        for wake_word in config.wake_words.values()
    ]
    return Info(
        wake=[
            WakeProgram(
                name="wake-kaldi",
                description=(
                    "Kaldi wake word verification; the detection timestamp is "
                    "the wake/command split point in milliseconds"
                ),
                attribution=Attribution(
                    name="Open Home Foundation",
                    url="https://github.com/OHF-Voice",
                ),
                installed=True,
                version=__version__,
                models=models,
            )
        ]
    )


async def main() -> None:
    parser = argparse.ArgumentParser(prog="wyoming-wake-kaldi")
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to the YAML configuration (default: config.yaml)",
    )
    parser.add_argument(
        "--uri",
        default="tcp://0.0.0.0:10400",
        help="unix:// or tcp:// URI to listen on (default: tcp://0.0.0.0:10400)",
    )
    parser.add_argument(
        "--decoder-daemon",
        default=None,
        help="Path to verify_decoderd (default: decoderd/verify_decoderd)",
    )
    parser.add_argument(
        "--debug-dir",
        default=None,
        help="Write each verified window (wav + json) under this directory",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate the config, build the graphs, and exit",
    )
    parser.add_argument("--debug", action="store_true", help="Log DEBUG messages")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        config = load_config(args.config)
    except ConfigError as err:
        parser.error(str(err))
        return

    _LOGGER.info(
        "Loaded %d wake word(s) from %s: %s",
        len(config.wake_words),
        args.config,
        ", ".join(sorted(config.wake_words)),
    )

    debug_dir = Path(args.debug_dir).absolute() if args.debug_dir else None
    if debug_dir is not None:
        debug_dir.mkdir(parents=True, exist_ok=True)

    service = WakeService(
        config,
        daemon_bin=Path(args.decoder_daemon) if args.decoder_daemon else None,
    )
    await service.start()

    if args.check:
        _LOGGER.info("Configuration OK")
        await service.stop()
        return

    wyoming_info = build_info(config)
    server = AsyncServer.from_uri(args.uri)
    _LOGGER.info("Listening on %s", args.uri)
    try:
        await server.run(
            partial(WakeKaldiEventHandler, wyoming_info, service, debug_dir)
        )
    finally:
        await service.stop()


def run() -> None:
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())


if __name__ == "__main__":
    run()
