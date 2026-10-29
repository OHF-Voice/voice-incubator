"""Synthesize the end-to-end test commands with Piper.

Piper is not a dependency of this package. Run with an environment that has
``piper-tts`` and ``soxr``, e.g. voice_labs's:

    ~/opt/ohf-apps-experimental/voice_labs/.venv/bin/python \
        tests/fixtures/make_fixtures.py \
        ~/opt/ohf-apps-experimental/voice_labs/models/piper/en_GB-jenny_dioco-medium.onnx
"""

import sys
import wave
from pathlib import Path

import numpy as np
import soxr
from piper import PiperVoice

SENTENCES = {
    "turn_on_lights_living_room": "Turn on the lights in the living room.",
    "turn_on_den_lights": "Turn on the den lights.",
    "turn_on_kitchen_and_bedroom": "Turn on the lights in the kitchen and bedroom.",
    "turn_off_lights": "Turn off the lights.",
    "turn_on_tv": "Turn on the TV.",
    "turn_on_ecobee": "Turn on the ecobee.",
    "brightness_kitchen_50": "Set the kitchen brightness to fifty percent.",
    "brightness_bedroom_15": "Set the bedroom brightness to fifteen percent.",
    "brightness_office_75": "Change the office brightness to seventy five percent.",
    "thermostat_72_5": "Set the ecobee to seventy two and a half degrees.",
    "thermostat_68": "Set the thermostat to sixty eight degrees.",
    "timer_13_minutes": "Set a timer for thirteen minutes.",
    "timer_30_seconds": "Set a timer for thirty seconds.",
    "timer_1_5_hours": "Start a timer for one and a half hours.",
    "play_music": "Play some music.",
    "weather": "What's the weather like tomorrow?",
    "lights_and_brightness": "Turn on the kitchen lights and set the brightness to fifty.",
    "timer_named_popcorn": "Set a timer for ten minutes named popcorn.",
    "timer_cookie": "Set a cookie timer for twelve minutes.",
    "cancel_pizza_timer": "Cancel the pizza timer.",
}

RATE = 16000


def main() -> None:
    voice = PiperVoice.load(sys.argv[1])
    out_dir = Path(__file__).parent / "audio"
    out_dir.mkdir(exist_ok=True)
    silence = np.zeros(int(0.3 * RATE), dtype=np.float32)
    for name, text in SENTENCES.items():
        chunks = list(voice.synthesize(text))
        audio = np.concatenate(
            [np.frombuffer(c.audio_int16_bytes, dtype="<i2") for c in chunks]
        ).astype(np.float32) / 32768.0
        audio = soxr.resample(audio, chunks[0].sample_rate, RATE)
        audio = np.concatenate([silence, audio, silence])
        pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2")
        with wave.open(str(out_dir / f"{name}.wav"), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(RATE)
            wav_file.writeframes(pcm.tobytes())
        print(f"{name}.wav  {len(audio) / RATE:.2f}s  {text}")


if __name__ == "__main__":
    main()
