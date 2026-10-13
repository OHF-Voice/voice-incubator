"""Compare fp32 and int8 speech-to-phrase on every saved command."""
import asyncio, sys, time, wave
from pathlib import Path
sys.path.insert(0, ".")
from voice_call.server import Options, VoiceCallApp

WAVS = sorted(Path(p) for d in sys.argv[2:] for p in Path(d).glob("command-*.wav"))

def pcm(path):
    with wave.open(str(path), "rb") as w:
        return w.readframes(w.getnframes())

def rss_mb():
    for line in open("/proc/self/status"):
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) / 1024

async def main():
    model_dir = sys.argv[1]
    opts = Options(host="x", hass_url="http://192.168.68.75:8123",
                   hass_token=Path("token").read_text().strip(), second_pass=False)
    app = VoiceCallApp(opts)
    app.recognizer.model_dir = Path(model_dir)

    sentences = await app.hass.sentence_triggers()
    await app.recognizer.start(sentences.plain)
    app._has_grammar = bool(sentences.plain)
    app.wildcards.train(sentences.wildcard)
    await app._train_grammars(sentences.wildcard)
    print(f"# model={model_dir} rss={rss_mb():.0f}MB", flush=True)

    for wav in WAVS:
        audio = pcm(wav)
        t0 = time.monotonic()
        text, how = await app._recognize(audio)
        ms = (time.monotonic() - t0) * 1000
        print(f"{wav.name}\t{text!r}\t{how}\t{ms:.0f}ms", flush=True)
    print(f"# peak rss={rss_mb():.0f}MB", flush=True)

asyncio.run(main())
