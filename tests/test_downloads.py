"""The models the app downloads the first time it starts (voice_labs/downloads.py)."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web

from voice_labs import downloads
from voice_labs.downloads import Downloader, File, Model, link, missing, serve_progress

ROOT = Path(__file__).resolve().parent.parent


def test_what_it_downloads_is_what_the_working_copy_has():
    """Each file, where a working copy has it, as the list says: what was
    published is what the app was built with."""
    checked = 0
    for model in downloads.MODELS:
        for file in model.files:
            path = ROOT / "models" / model.folder / file.name
            if not path.is_file():
                continue
            data = path.read_bytes()
            assert (len(data), hashlib.sha256(data).hexdigest()) == (file.size, file.sha256), path
            checked += 1
    if not checked:
        pytest.skip("no models here")


def test_only_the_linked_models_are_downloaded(tmp_path):
    """A working copy's models/ has its own; the image's links its folders to
    where the app downloads them, and only those are fetched."""
    models, data = tmp_path / "models", tmp_path / "data"
    (models / "gliner2.5-small-v1-onnx").mkdir(parents=True)
    assert missing(models) == []
    for model in downloads.MODELS:
        if model.folder != "gliner2.5-small-v1-onnx":
            (models / model.folder).parent.mkdir(parents=True, exist_ok=True)
            (models / model.folder).symlink_to(data / model.folder)
    todo = missing(models)
    assert {path.parent.relative_to(data).as_posix() for path, _ in todo} == {
        "parakeet-tdt-int8", "speech-to-phrase/stt_en_parakeet_tdt_ctc_110m-int8", "embeddinggemma-2", "embeddinggemma-2/onnx"}
    with pytest.raises(SystemExit, match="already there"):
        link(data, models)


def test_downloaded_whole_and_as_published_or_tried_again(tmp_path, monkeypatch):
    asyncio.run(_downloaded(tmp_path, monkeypatch))


async def _downloaded(tmp_path: Path, monkeypatch) -> None:
    served = {"a.onnx": b"the encoder" * 1000, "sub/b.json": b"{}"}
    broken = {"sub/b.json"}

    async def serve(request: web.Request) -> web.Response:
        name = request.match_info["name"]
        return web.Response(body=b"corrupt" if name in broken else served[name])

    application = web.Application()
    application.router.add_get("/{name:.*}", serve)
    runner = web.AppRunner(application)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # pylint: disable=protected-access
    files = tuple(File(name, f"http://127.0.0.1:{port}/{name}", hashlib.sha256(body).hexdigest(), len(body))
                  for name, body in served.items())
    monkeypatch.setattr(downloads, "MODELS", (Model("fake", files),))
    monkeypatch.setattr(downloads, "RETRY_SECONDS", 0.05)
    models, data = tmp_path / "models", tmp_path / "data"
    link(data, models)
    downloader = Downloader(missing(models))
    assert downloader.total == sum(len(body) for body in served.values())
    page = await serve_progress(downloader, "127.0.0.1", 0, ())
    page_port = page.addresses[0][1]
    try:
        task = asyncio.ensure_future(downloader.run())
        for _ in range(100):
            if downloader.error:
                break
            await asyncio.sleep(0.02)
        # Not as published: not kept, said, and tried again.
        assert "b.json" in downloader.error and "SHA-256" in downloader.error
        assert (data / "fake/a.onnx").read_bytes() == served["a.onnx"] and not (data / "fake/sub/b.json").exists()
        assert not list((data / "fake").rglob("*.part"))
        async with aiohttp.ClientSession(f"http://127.0.0.1:{page_port}") as http:
            assert await (await http.get("/health")).text() == "ok"
            text = await (await http.get("/")).text()
        assert "Downloading speech models" in text and "Couldn't download" in text
        broken.clear()
        await asyncio.wait_for(task, 5)
        assert (models / "fake/sub/b.json").read_bytes() == b"{}"  # through the link
        assert missing(models) == [] and downloader.done == downloader.total and downloader.error is None
    finally:
        await page.cleanup()
        await runner.cleanup()


def test_the_app_runs_on_what_it_downloads(tmp_path):
    """The CTC model's folder with its tokens only: the transducer, with the
    CTC model's head beside it, is both (adapt_ctc.hybrid)."""
    from adapt_ctc.hybrid import has_shared_model  # pylint: disable=import-outside-toplevel

    from voice_labs import languages  # pylint: disable=import-outside-toplevel

    real = ROOT / "models"
    if not (real / "parakeet-tdt-int8/ctc_head.npz").is_file():
        pytest.skip("no Parakeet here")
    models = tmp_path / "models"
    ctc = models / "speech-to-phrase/stt_en_parakeet_tdt_ctc_110m-int8"
    ctc.mkdir(parents=True)
    (ctc / "tokens.txt").write_bytes((real / "parakeet-tdt-int8/tokens.txt").read_bytes())
    (models / "parakeet-tdt-int8").symlink_to(real / "parakeet-tdt-int8")
    (models / "piper").symlink_to(real / "piper")
    pack = languages.load("en")
    assert pack.ctc_dir(tmp_path) == ctc and pack.missing(tmp_path) == []
    assert has_shared_model(pack.transducer_dir(tmp_path), ctc)
    (ctc / "tokens.txt").unlink()
    assert pack.missing(tmp_path) == ["models/speech-to-phrase/stt_en_parakeet_tdt_ctc_110m"]
