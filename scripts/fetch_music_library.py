#!/usr/bin/env python3
"""Pull artist and album names out of Music Assistant.

A closed-vocabulary recognizer (Kaldi, Coqui) cannot decode a wildcard it has
never seen, so `{artist}` and `{album}` have to be enumerated before training.
Music Assistant already knows the library, which is what makes that practical.

Names are filtered to what a speech recognizer can actually be trained on:
a grapheme-to-phoneme pass has to produce a pronunciation for every word, so
entries that are punctuation, digits or non-Latin script are dropped rather
than poisoning the lexicon.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
import urllib.request
from pathlib import Path

PAGE = 500

# Keep letters, digits, spaces and the few marks that survive G2P intact.
_STRIP = re.compile(r"[^\w\s'&-]", re.UNICODE)
# Underscores read as word characters but are spoken as separators.
_UNDERSCORE = re.compile(r"_+")
_SPACES = re.compile(r"\s+")


def fetch(
    url: str, token: str, entry_id: str, media_type: str, extra: dict | None = None
) -> list[str]:
    names: list[str] = []
    offset = 0
    while True:
        payload = json.dumps(
            {
                "config_entry_id": entry_id,
                "media_type": media_type,
                "limit": PAGE,
                "offset": offset,
                **(extra or {}),
            }
        ).encode()
        request = urllib.request.Request(
            f"{url}/api/services/music_assistant/get_library?return_response=true",
            data=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            items = json.load(response)["service_response"]["items"]
        if not items:
            break
        names.extend(item["name"] for item in items)
        if len(items) < PAGE:
            break
        offset += PAGE
    return names


def clean(name: str) -> str | None:
    """Normalize a name, or return None if it cannot be spoken."""
    name = unicodedata.normalize("NFKC", name)
    # Drop anything the English lexicon has no hope of pronouncing.
    if not re.search(r"[A-Za-z]", name):
        return None
    if any(ord(ch) > 0x24F for ch in name):
        return None
    name = _SPACES.sub(" ", _UNDERSCORE.sub(" ", _STRIP.sub(" ", name))).strip().lower()
    if len(name) < 2 or len(name.split()) > 10:
        return None
    return name


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hass-url", default="http://localhost:8123")
    parser.add_argument("--hass-token-file", default="token")
    parser.add_argument("--config-entry-id", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--max-per-list",
        type=int,
        default=0,
        help="cap each list; 0 keeps everything. Training cost grows with the "
        "vocabulary, so this is the dial if the graph gets too big.",
    )
    parser.add_argument(
        "--album-artists-only",
        action="store_true",
        help="skip featured and compilation artists, which are most of a "
        "library and almost none of what anyone asks for by name",
    )
    args = parser.parse_args()

    token = Path(args.hass_token_file).read_text(encoding="utf-8").strip()

    lists: dict[str, list[str]] = {}
    for media_type, list_name in (("artist", "artist"), ("album", "album")):
        extra = (
            {"album_artists_only": True}
            if media_type == "artist" and args.album_artists_only
            else None
        )
        raw = fetch(args.hass_url, token, args.config_entry_id, media_type, extra)
        seen: dict[str, None] = {}
        for name in raw:
            cleaned = clean(name)
            if cleaned is not None:
                seen.setdefault(cleaned, None)
        names = list(seen)
        if args.max_per_list:
            names = names[: args.max_per_list]
        lists[list_name] = names
        print(
            f"{media_type}: {len(raw)} in library, {len(names)} usable",
            file=sys.stderr,
        )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as out:
        out.write('language: "en"\nlists:\n')
        for list_name, names in lists.items():
            out.write(f"  {list_name}:\n    values:\n")
            for name in names:
                out.write(f'      - "{name}"\n')
    print(f"wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
