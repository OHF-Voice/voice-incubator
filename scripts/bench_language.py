#!/usr/bin/env python3
"""A language pack's library on its bench sentences (voice_call/languages/
<code>/bench.yaml): each said, recognized as a call's command is (the pack's
CTC model, its transducer for free text), and scored against the command and
slots it must become.

    scripts/bench_language.py de                  # spoken by the pack's Piper voice
    scripts/bench_language.py de --typed          # no audio: the text, as heard word for word
    scripts/bench_language.py de --hass http://homeassistant.local:8123 --token-file ~/token \\
        --tts tts.home_assistant_cloud --voice KatjaNeural --voice ConradNeural

Home Assistant voices are asked in the pack's language (``--tts-language``,
default ``<code>-<CODE>``, or ``uk-UA``). Speech is cached under ``--cache``
(``<cache>/<voice>/<n>.wav``), so a run after a change only recognizes.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import re
import sys
import wave
from pathlib import Path
from typing import Any

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from voice_call import languages  # noqa: E402
from voice_call.protocol import SAMPLE_RATE  # noqa: E402

DEFAULT_CACHE = Path.home() / ".cache/voice_call/bench"

TTS_LOCALES = {"en": "en-US", "uk": "uk-UA"}
"""A pack's Home Assistant TTS language where it isn't ``<code>-<CODE>``."""

LEAD_SECONDS = 0.3
"""Silence before each sentence, as a call's command has after the wake word:
some voices start at the very first sample (Piper's Davefx), and a word cut
there is not one a call would have."""


def bench(code: str, path: Path | None = None) -> dict[str, Any]:
    path = path or languages.load(code).directory / "bench.yaml"
    if not path.is_file():
        raise SystemExit(f"{code} has no bench sentences ({path})")
    return yaml.safe_load(path.read_text("utf-8"))


def engine_for(data: dict[str, Any], threads: int = 4):
    """The pack's models with its whole library, in the bench's home."""
    # pylint: disable=import-outside-toplevel
    from voice_call.engine import Engine
    from voice_call.store import compile_store
    from voice_call.web import load_library

    pack = languages.current()
    entries = load_library()
    store = {
        "language": pack.code,
        "names": data["names"],
        "lists": {name: values for e in entries for name, values in e["lists"].items()},
        "commands": {e["id"]: e["spec"] for e in entries},
    }
    engine = Engine(pack.ctc_dir(ROOT), pack.transducer_dir(ROOT), None, threads=threads)
    engine.build(compile_store(store))
    rooms = data.get("rooms") or {}
    engine.entity_area = rooms.get
    return engine


def satellite_for(data: dict[str, Any]):
    from voice_call.engine import Satellite  # pylint: disable=import-outside-toplevel

    area = data.get("satellite")
    name = (data["names"].get("areas") or {}).get(area) if area else None
    name = name["name"] if isinstance(name, dict) else name
    return Satellite("bench", "Bench", area, name)


def _text(value: Any) -> str:
    return " ".join(re.sub(r"[^\w' ]+", " ", str(value).lower()).split())


def scored(decision, want_command: str, want_slots: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
    """Whether the decision is the sentence's command and slots; what it was.
    A duration is compared by its seconds, free text as words."""
    got = decision.command.id if decision.command else None
    slots = {}
    for key, value in (decision.slots or {}).items():
        if isinstance(value, dict) and "total_seconds" in value:
            value = value["total_seconds"]
        elif isinstance(value, str):
            value = _text(value)
        slots[key] = value
    want = {k: _text(v) if isinstance(v, str) and k not in ("color", "device_class", "asked") else v for k, v in want_slots.items()}
    if "time" in want and isinstance(slots.get("time"), str):
        # A clock time is right when it reads as the same time: "halfzeven".
        clock = languages.reading().clock
        if clock(slots["time"]) is not None and clock(slots["time"]) == clock(want["time"]):
            slots["time"] = want["time"]
    return got == want_command and slots == want, got or decision.message or "", slots


def write_wav(path: Path, samples: np.ndarray, rate: int) -> None:
    from voice_call.tts import _to_device_pcm  # pylint: disable=import-outside-toplevel

    if samples.ndim > 1:
        samples = samples.mean(axis=1)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        out.writeframes(_to_device_pcm(samples.astype(np.float32), rate))


def read_wav(path: Path) -> np.ndarray:
    """A sentence's audio, with ``LEAD_SECONDS`` of silence before it."""
    with wave.open(str(path)) as audio:
        samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2").astype(np.float32) / 32768
    return np.concatenate([np.zeros(int(LEAD_SECONDS * SAMPLE_RATE), dtype=np.float32), samples])


def piper(texts: list[str], folder: Path) -> None:
    from piper import PiperVoice  # pylint: disable=import-outside-toplevel

    voice = PiperVoice.load(str(ROOT / "models/piper" / languages.current().piper))
    for index, text in enumerate(texts):
        path = folder / f"{index:02d}.wav"
        if not path.exists():
            chunks = list(voice.synthesize(text))
            write_wav(path, np.concatenate([c.audio_float_array for c in chunks]), chunks[0].sample_rate)


async def hass_tts(texts: list[str], folder: Path, args, voice: str) -> None:
    import aiohttp  # pylint: disable=import-outside-toplevel
    import soundfile as sf  # pylint: disable=import-outside-toplevel

    token = Path(args.token_file).expanduser().read_text().strip()
    code = languages.current().code
    language = args.tts_language or TTS_LOCALES.get(code, f"{code}-{code.upper()}")
    async with aiohttp.ClientSession(headers={"Authorization": f"Bearer {token}"}) as http:
        for index, text in enumerate(texts):
            path = folder / f"{index:02d}.wav"
            if path.exists():
                continue
            body = {"engine_id": args.tts, "message": text, "language": language, "options": {"voice": voice}}
            async with http.post(f"{args.hass}/api/tts_get_url", json=body) as response:
                response.raise_for_status()
                url = (await response.json())["url"]
            async with http.get(url) as response:
                response.raise_for_status()
                samples, rate = sf.read(io.BytesIO(await response.read()), dtype="float32")
            write_wav(path, samples, rate)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("language", help="a language pack: de")
    parser.add_argument("--typed", action="store_true", help="the text as heard word for word, no audio")
    parser.add_argument("--bench", type=Path, help="other sentences than the pack's bench.yaml")
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--hass", help="Home Assistant's URL, for its TTS voices")
    parser.add_argument("--token-file", default="~/opt/homeassistant/token-main")
    parser.add_argument("--tts", default="tts.home_assistant_cloud", help="the TTS entity")
    parser.add_argument("--voice", action="append", default=[], help="a voice of it (repeat)")
    parser.add_argument("--tts-language")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--json", type=Path, help="write every result here")
    args = parser.parse_args()

    pack = languages.use(args.language)
    data = bench(pack.code, args.bench)
    sentences = data["sentences"]
    texts = [s[0] for s in sentences]
    engine = engine_for(data, args.threads)
    satellite = satellite_for(data)

    voices: dict[str, Path] = {}
    if not args.typed:
        cache = args.cache / pack.code
        voices["piper"] = cache / Path(pack.piper).stem
        piper(texts, voices["piper"])
        for voice in args.voice if args.hass else []:
            voices[voice] = cache / voice
            asyncio.run(hass_tts(texts, voices[voice], args, voice))

    rows = []
    for index, (text, command, slots, shows) in enumerate(sentences):
        heard = []
        if args.typed:
            heard.append(("typed", engine.recognize_text(text, satellite)))
        for voice, folder in voices.items():
            heard.append((voice, engine.recognize_command(read_wav(folder / f"{index:02d}.wav"), satellite)))
        for voice, decision in heard:
            ok, got, got_slots = scored(decision, command, slots or {})
            rows.append({"n": index, "text": text, "shows": shows, "voice": voice, "ok": ok, "got": got,
                         "slots": got_slots, "heard": decision.recognition.get("transcript")})

    right = sum(r["ok"] for r in rows)
    print(f"{pack.name}: {right}/{len(rows)} right ({', '.join(voices) or 'typed'})\n")
    for index, (text, _command, _slots, shows) in enumerate(sentences):
        mine = [r for r in rows if r["n"] == index]
        print(f"{index:02d} {sum(r['ok'] for r in mine)}/{len(mine)}  {text}  [{shows}]")
        for r in mine:
            if not r["ok"]:
                print(f"      {r['voice']:14s} got {r['got']} {r['slots']}  heard {r['heard']!r}")
    if args.json:
        args.json.write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
