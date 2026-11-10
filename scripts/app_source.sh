#!/usr/bin/env bash
# This checkout's last commit, as the source the Home Assistant app's image is
# built from in place of the release its Dockerfile pins: for building the app,
# or deploying it, from a checkout of OHF-Voice/app-voice-incubator.
#
#   scripts/app_source.sh ~/opt/app-voice-incubator/voice_incubator
#
# Uncommitted changes are not in it.
set -euo pipefail

app="${1:?usage: scripts/app_source.sh <the app folder of an app-voice-incubator checkout>}"
app="${app%/}"
test -f "${app}/config.yaml" || { echo "${app} has no config.yaml: not the app folder" >&2; exit 1; }

cd "$(dirname "$0")/.."
git archive --format=tar.gz --prefix=voice-incubator/ -o "${app}/voice-incubator.tar.gz" HEAD
echo "$(git describe --always --dirty) -> ${app}/voice-incubator.tar.gz"
