#!/usr/bin/env bash
# Run the app against tests/fake_hass.py for work on the web UI, with no real
# Home Assistant, so nothing is executed anywhere. With --replay, a fake
# satellite (tests/fake_satellite.py) places those recorded calls, in a loop;
# without it the app is pinned to a port nothing listens on. Other arguments
# go to voice_call (e.g. --data-dir).
#
#   scripts/dev_ui.sh                                      # UI on http://127.0.0.1:8099/
#   scripts/dev_ui.sh --replay calls/call-15788.wav calls/call-19757.wav --
set -euo pipefail
cd "$(dirname "$0")/.."
replay=()
if [[ "${1:-}" == "--replay" ]]; then
    shift
    while (( $# )) && [[ "$1" != "--" ]]; do replay+=("$1"); shift; done
    [[ "${1:-}" == "--" ]] && shift
fi
token="$(mktemp)"
printf fake-token > "$token"
pids=()
trap 'kill "${pids[@]}" 2>/dev/null; rm -f "$token"' EXIT
.venv/bin/python -m tests.fake_hass --port 8124 & pids+=($!)
satellite=(--host 127.0.0.1 --port 1)
if (( ${#replay[@]} )); then
    .venv/bin/python -m tests.fake_satellite --port 6056 --pause 20 --loop "${replay[@]}" & pids+=($!)
    satellite=(--host 127.0.0.1 --port 6056)
fi
.venv/bin/python -m voice_call "${satellite[@]}" \
    --hass-url http://127.0.0.1:8124 --hass-token-file "$token" \
    --data-dir var/dev --http-port 8099 "$@"
