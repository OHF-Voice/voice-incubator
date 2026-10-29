#!/usr/bin/env python3
"""Write voice_labs/piper_voices.json: the voices rhasspy/piper-voices has
for the app's languages, at one revision of it -- each one's name, language,
quality, speakers (in the order of their ids) and files with their sizes and
MD5 digests, which voice_labs/piper_voices.py downloads and checks.

    python3 scripts/piper_voices.py [--revision <commit>] [--language en_US ...]
"""

from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "voice_labs/piper_voices.json"
REPO = "https://huggingface.co/rhasspy/piper-voices"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--revision", help="a commit of rhasspy/piper-voices (default: main's latest)")
    parser.add_argument("--language", action="append", help="a language code to keep, as en_US (default: en_US, en_GB)")
    args = parser.parse_args()
    revision = args.revision
    if revision is None:
        with urllib.request.urlopen(f"https://huggingface.co/api/models/rhasspy/piper-voices") as response:
            revision = json.load(response)["sha"]
    keep = set(args.language or ("en_US", "en_GB"))
    with urllib.request.urlopen(f"{REPO}/resolve/{revision}/voices.json") as response:
        catalog = json.load(response)
    voices = {}
    for key, voice in sorted(catalog.items()):
        if voice["language"]["code"] not in keep:
            continue
        speakers = sorted(voice.get("speaker_id_map") or {}, key=lambda name: voice["speaker_id_map"][name])
        voices[key] = {
            "name": voice["name"],
            "language": voice["language"]["code"],
            "quality": voice["quality"],
            "speakers": speakers,
            "files": {path: [f["size_bytes"], f["md5_digest"]] for path, f in voice["files"].items()},
        }
    OUT.write_text(json.dumps({"revision": revision, "voices": voices}, indent=1) + "\n", encoding="utf-8")
    print(f"{OUT}: {len(voices)} voices at {revision}")


if __name__ == "__main__":
    main()
