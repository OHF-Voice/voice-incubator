"""Render utterances with Home Assistant Cloud (Azure) TTS for capture.

Levels are matched to a VPE capture known to wake the device: TTS renders near
full scale, and the clipping that causes is on its own enough to stop
micro_wake_word firing.
"""

import argparse
import io
import json
import os
import subprocess
import urllib.request
import wave
from pathlib import Path

import numpy as np
import soundfile as sf
import soxr

REFERENCE = Path(__file__).resolve().parent.parent / "tests/okay_nabu_turn_on_the_lights.wav"


def reference_peak() -> float:
    with wave.open(str(REFERENCE)) as wav_file:
        audio = np.frombuffer(
            wav_file.readframes(wav_file.getnframes()), dtype=np.int16
        ).astype(np.float32)
    return float(np.abs(audio).max())


def synth(message: str, voice: str, hass: str, token: str) -> np.ndarray:
    request = urllib.request.Request(
        f"{hass}/api/tts_get_url",
        data=json.dumps(
            {
                "engine_id": "tts.home_assistant_cloud",
                "message": message,
                "language": "en-US",
                "options": {"voice": voice},
            }
        ).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        url = json.load(response)["url"]
    with urllib.request.urlopen(url, timeout=60) as response:
        mp3 = response.read()

    decoded = subprocess.run(
        ["ffmpeg", "-v", "quiet", "-i", "pipe:0", "-f", "wav", "-ac", "1",
         "-ar", "16000", "pipe:1"],
        input=mp3,
        stdout=subprocess.PIPE,
        check=True,
    ).stdout
    audio, rate = sf.read(io.BytesIO(decoded), dtype="float32")
    if rate != 16000:
        audio = soxr.resample(audio, rate, 16000)
    return audio


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("cloud"))
    parser.add_argument("--voices", nargs="+", required=True)
    parser.add_argument("--commands", nargs="+", required=True)
    parser.add_argument("--gain", type=float, default=1.0)
    parser.add_argument("--hass", default=os.environ.get("HASS_URL", "http://localhost:8123"))
    parser.add_argument("--token-file", type=Path, help="a long-lived access token, when HASS_TOKEN doesn't")
    args = parser.parse_args()
    token = os.environ.get("HASS_TOKEN")
    if not token and not args.token_file:
        parser.error("Home Assistant needs a long-lived access token: HASS_TOKEN or --token-file")
    token = token or args.token_file.expanduser().read_text().strip()

    args.out.mkdir(parents=True, exist_ok=True)
    peak = reference_peak() * args.gain

    for command in args.commands:
        for voice in args.voices:
            audio = synth(f"okay nabu, {command}", voice, args.hass, token)
            audio = audio / max(float(np.abs(audio).max()), 1e-9) * peak
            audio = np.concatenate([audio, np.zeros(8000, dtype=np.float32)])
            slug = command.replace(" ", "_").replace("'", "")
            safe = voice.replace("||", "-")
            name = args.out / f"{slug}--{safe}.wav"
            sf.write(name, audio.astype(np.int16), 16000, subtype="PCM_16")
            print(f"{name}  {len(audio) / 16000:.2f}s  peak {peak:.0f}")


main()
