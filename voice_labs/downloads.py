"""The models the app downloads the first time it starts: Parakeet (English
speech, with its CTC head beside it: adapt_ctc.hybrid), the CTC model's
tokens, EmbeddingGemma 2 (hints by meaning) and GLiNER (the command
fallback) -- about 470 MB, from rhasspy/voice-labs-models and
onnx-community/embeddinggemma-2-ONNX, each file checked against its SHA-256 --
and the Piper voices in use (voice_labs/piper_voices.py).

As an app they go in its data folder (``/data/models``, kept across updates),
which the image's models/ links each of them to (``link``): the code finds
them where they always were. Where they are already there (a working copy),
nothing is downloaded. While it downloads, a page of its own answers on the
web UI's port -- how far it got, and Home Assistant's health check.

    python3 -m voice_labs.downloads link /data/models    # in the image's build
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import html
import logging
from dataclasses import dataclass
from pathlib import Path

import aiohttp
from aiohttp import web

_LOGGER = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
MODELS_REPO = "https://huggingface.co/rhasspy/voice-labs-models/resolve/08648b9dfb0014d9c73af8718e674d54deca3f1c"
EMBEDDING_REPO = (
    "https://huggingface.co/onnx-community/embeddinggemma-2-ONNX/resolve/daa72c51243991dfcaf9f9137d2c573d8f7790c0"
)
RETRY_SECONDS = 30.0
"""How long after a failed download it tries again."""
CHUNK = 1 << 20


@dataclass(frozen=True)
class File:
    name: str
    """Its path in its model's folder."""
    url: str
    digest: str
    size: int
    algorithm: str = "sha256"
    """``digest``'s (hashlib's name): Piper's voices are listed with MD5."""


@dataclass(frozen=True)
class Model:
    folder: str
    """Its folder under models/."""
    files: tuple[File, ...]


PARAKEET = f"{MODELS_REPO}/parakeet-tdt_ctc-110m-en-int8"
GLINER = f"{MODELS_REPO}/gliner2.5-small-v1-onnx"
MODELS = (
    Model("parakeet-tdt-int8", (
        File("encoder.onnx", f"{PARAKEET}/encoder.onnx",
             "8e19f44028ee520c9734b0cd85582a1b2aab992e4fcafac0dbd835fba4ed445c", 171777188),
        File("decoder.onnx", f"{PARAKEET}/decoder.onnx",
             "3da156bde41a04c94ef783e0bd92928e9974e08645b976a22d0c3e1063510249", 15753086),
        File("joiner.onnx", f"{PARAKEET}/joiner.onnx",
             "b603765c0724a0768c378a23326dabbeb9cfea932d260e4fcc14384fa5fd5aff", 5596854),
        File("tokens.txt", f"{PARAKEET}/tokens.txt",
             "450e56bd2f036fe5b6aa821865838cc5aa9d8b0106134ce9a9ba0664abe6cd10", 9953),
        File("ctc_head.npz", f"{PARAKEET}/ctc_head.npz",
             "5459bf21d35af241ac604ef0007805f2b0ddc91ef6f3c2f04eb1ab5851d9cda8", 2104188),
    )),
    # The CTC model's own folder: its tokens and its name are all the shared
    # encoder needs of it (adapt_ctc.hybrid.has_shared_model).
    Model("speech-to-phrase/stt_en_parakeet_tdt_ctc_110m-int8", (
        File("tokens.txt", f"{PARAKEET}/tokens.txt",
             "450e56bd2f036fe5b6aa821865838cc5aa9d8b0106134ce9a9ba0664abe6cd10", 9953),
    )),
    Model("embeddinggemma-2", (
        File("tokenizer.json", f"{EMBEDDING_REPO}/tokenizer.json",
             "4d777ef5bdc1aa36227abdfb77c3e49e7b9c892d16e1b6bda41c393504828be4", 32170510),
        File("onnx/model_q4.onnx", f"{EMBEDDING_REPO}/onnx/model_q4.onnx",
             "f9eeba97acddf139b8ee2ddf04bc30dceafa88de93fadf74d7644e0d61a477a9", 490742),
        File("onnx/model_q4.onnx_data", f"{EMBEDDING_REPO}/onnx/model_q4.onnx_data",
             "c3975f2d1ab7a1878ae31a7d7a9b7804a827aff3800b60dfceafce21cac3df49", 174028800),
    )),
    Model("gliner2.5-small-v1-onnx", (
        File("boundary_a.onnx", f"{GLINER}/boundary_a.onnx",
             "195407f84cd9d2ff4df3f31c4e75e54c44d9667732874151e0fb27a1e6aabc8b", 2804235),
        File("boundary_b.onnx", f"{GLINER}/boundary_b.onnx",
             "02608968667810944d75bf106d219bbb02962c25cbb6549d1a36afcc1769d020", 804090),
        File("classifier.npz", f"{GLINER}/classifier.npz",
             "3cbf33fc7459211f34906fafad06d7edf1ef787040040efd0689bf5c947fae34", 1186762),
        File("encoder-w8.onnx", f"{GLINER}/encoder-w8.onnx",
             "959bda1f5d0aeb0b9370eef0d2c1351170190a3b8710edbb03f0a51d4d0dcdfd", 68428247),
        File("gliner2_onnx.json", f"{GLINER}/gliner2_onnx.json",
             "c39edc1d8e90fd65d0cca316ac514a1546ab2a4edd954fb295a431894bf2eb0e", 690),
        File("tokenizer.json", f"{GLINER}/tokenizer.json",
             "cbc8ae6037812709c9c26f2a160f8dc48b0440bcb79c8141804259ae2d6adac3", 8341713),
    )),
)


def missing(models: Path) -> list[tuple[Path, File]]:
    """What isn't under ``models`` yet of the models linked to where the app
    downloads them (``link``), each where it goes. A working copy's models/
    links none: it has its own."""
    out = []
    for model in MODELS:
        if not (models / model.folder).is_symlink():
            continue
        for file in model.files:
            path = models / model.folder / file.name
            if not path.is_file():
                out.append((path.parent.resolve() / path.name, file))
    return out


class Downloader:
    """Downloads what is missing, trying again until it has it all, and says
    how far it got: ``done`` and ``total`` bytes, and the last ``error``."""

    def __init__(self, todo: list[tuple[Path, File]]) -> None:
        self.todo = todo
        self.total = sum(file.size for _, file in todo)
        self.done = 0
        self.error: str | None = None

    async def run(self) -> None:
        _LOGGER.info("Downloading %d model file(s), %.0f MB", len(self.todo), self.total / 1e6)
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(sock_read=60)) as http:
            while self.todo:
                path, file = self.todo[0]
                try:
                    await self._fetch(http, path, file)
                except (aiohttp.ClientError, OSError, TimeoutError, ValueError) as err:
                    self.error = f"{file.url.rsplit('/', 1)[-1]}: {err}"
                    _LOGGER.warning("Couldn't download %s (%s); trying again in %.0f s", file.url, err, RETRY_SECONDS)
                    await asyncio.sleep(RETRY_SECONDS)
                    continue
                self.todo.pop(0)
                self.done += file.size
                self.error = None
        _LOGGER.info("Models downloaded")

    async def _fetch(self, http: aiohttp.ClientSession, path: Path, file: File) -> None:
        """One file, whole and as published, or not at all."""
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(path.name + ".part")
        digest = hashlib.new(file.algorithm)
        got = 0
        try:
            async with http.get(file.url) as response:
                response.raise_for_status()
                with partial.open("wb") as out:
                    async for chunk in response.content.iter_chunked(CHUNK):
                        out.write(chunk)
                        digest.update(chunk)
                        got += len(chunk)
                        self.done += len(chunk)
            if digest.hexdigest() != file.digest:
                spelled = {"sha256": "SHA-256", "md5": "MD5"}.get(file.algorithm, file.algorithm)
                raise ValueError(f"not as published (its {spelled} differs)")
            partial.replace(path)
        finally:
            self.done -= got
            partial.unlink(missing_ok=True)


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="refresh" content="3">
<title>Voice Labs</title><style>
:root {{ color-scheme: light dark; --bg: #fff; --fg: #1d1d1f; --muted: #5f6368; --accent: #18bbf2; --track: #e3e3e3; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg: #17181a; --fg: #e8e8e8; --muted: #a0a4aa; --track: #2c2e31; }} }}
body {{ background: var(--bg); color: var(--fg); font: 15px/1.45 system-ui, sans-serif; margin: 0 auto; padding: 32px 16px; max-width: 520px; }}
h1 {{ font-size: 20px; margin: 0 0 8px; }} p {{ margin: 8px 0; color: var(--muted); }}
.bar {{ height: 8px; border-radius: 4px; background: var(--track); overflow: hidden; }}
.bar div {{ height: 100%; width: {percent:.1f}%; background: var(--accent); }}
</style></head><body><h1>Downloading speech models</h1>
<div class="bar"><div></div></div><p>{done:.0f} of {total:.0f} MB. Only the first start needs this.</p>{error}
</body></html>"""


async def serve_progress(downloader: Downloader, host: str, port: int, allow: tuple[str, ...]) -> web.AppRunner:
    """The progress page and the health check (web.py's), on the web UI's
    port until the web UI takes it."""
    from .web import HEALTH, LOOPBACK  # pylint: disable=import-outside-toplevel

    allowed = set(allow)

    @web.middleware
    async def only_allowed(request: web.Request, handler):
        if allowed and request.remote not in allowed and not (request.path == HEALTH and request.remote in LOOPBACK):
            raise web.HTTPForbidden(text="Only Home Assistant's ingress may connect")
        return await handler(request)

    async def health(_request: web.Request) -> web.Response:
        return web.Response(text="ok")

    async def page(_request: web.Request) -> web.Response:
        error = f"<p>Couldn't download {html.escape(downloader.error)}. Trying again.</p>" if downloader.error else ""
        total = max(downloader.total, 1)
        text = PAGE.format(percent=100 * downloader.done / total, done=downloader.done / 1e6, total=total / 1e6, error=error)
        return web.Response(text=text, content_type="text/html")

    application = web.Application(middlewares=[only_allowed])
    application.router.add_get(HEALTH, health)
    application.router.add_get("/{tail:.*}", page)
    runner = web.AppRunner(application, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    return runner


VOICES = "piper"
"""Where Piper's voices go under models/, each downloaded when it is first
used (voice_labs/piper_voices.py)."""


def link(data: Path, models: Path = ROOT / "models") -> None:
    """Each downloaded model's folder under ``models`` -- and Piper's voices'
    -- a link to its place under ``data``, where the app downloads it."""
    for folder in (*(model.folder for model in MODELS), VOICES):
        place = models / folder
        place.parent.mkdir(parents=True, exist_ok=True)
        if place.is_symlink() or place.exists():
            raise SystemExit(f"{place} is already there: the image should not carry it")
        place.symlink_to(data / folder, target_is_directory=True)
        print(f"{place} -> {data / folder}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="voice_labs.downloads", description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    linking = commands.add_parser("link", help="link the image's models/ folders to where the app downloads them")
    linking.add_argument("data", type=Path, help="where the app downloads them (the app: /data/models)")
    args = parser.parse_args()
    if args.command == "link":
        link(args.data)


if __name__ == "__main__":
    main()
