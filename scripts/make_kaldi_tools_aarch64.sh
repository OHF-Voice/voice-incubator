#!/usr/bin/env bash
# The Kaldi wake verifier's tools for aarch64 (an HA Green, a Pi):
# models/wake-kaldi-tools-aarch64, laid out as models/wake-kaldi-tools is
# (kaldi/{bin,lib,utils,steps,include}, openfst/{bin,lib,include}). Packed as
# a wake-kaldi-tools/ folder, it is kaldi/tools-aarch64.tar.gz in
# rhasspy/voice-labs-models, which the image's build fetches
# (voice_labs/downloads.py).
#
# From the Kaldi of 2021 that Rhasspy shipped, as kaldi-manylinux built it --
# its aarch64 binaries (dist/kaldi-2021_arm64.tar.gz) and the sources they
# were built from (src/), whose headers the image's build compiles the
# resident decoder (models/wake-kaldi-decoderd) against. Its binaries ask for
# gfortran 3, which Debian no longer has and nothing in them calls (their
# LAPACK is OpenBLAS's, Debian's libopenblas0 in the image): that is taken out.
#
#   scripts/make_kaldi_tools_aarch64.sh [KALDI_MANYLINUX_DIR]   # default ~/opt/kaldi-manylinux
set -euo pipefail
cd "$(dirname "$0")/.."
SRC="${1:-$HOME/opt/kaldi-manylinux}"
OUT=models/wake-kaldi-tools-aarch64
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

tar xzf "$SRC/dist/kaldi-2021_arm64.tar.gz" -C "$WORK"
tar xzf "$SRC/src/kaldi-2021.tar.gz" -C "$WORK" --wildcards 'kaldi/src/*.h'
tar xzf "$SRC/src/openfst-1.6.7.tar.gz" -C "$WORK" --wildcards 'openfst-1.6.7/src/include/*'

rm -rf "$OUT"
mkdir -p "$OUT/kaldi/bin" "$OUT/kaldi/include/kaldi" "$OUT/openfst"
# Binaries and libraries side by side, as they were built (their RUNPATH is
# $ORIGIN); lib/ and openfst/ point there.
find "$WORK/kaldi" -maxdepth 1 \( -type f -o -type l \) -exec cp -a {} "$OUT/kaldi/bin/" \;
ln -s bin "$OUT/kaldi/lib"
cp -a "$WORK/kaldi/egs/wsj/s5/utils" "$OUT/kaldi/utils"
cp -a "$WORK/kaldi/egs/wsj/s5/steps" "$OUT/kaldi/steps"
(cd "$WORK/kaldi/src" && for d in */; do
    d="${d%/}"
    if compgen -G "$d/*.h" > /dev/null; then
        mkdir -p "$OLDPWD/$OUT/kaldi/include/kaldi/$d"
        cp "$d"/*.h "$OLDPWD/$OUT/kaldi/include/kaldi/$d/"
    fi
done)
cp -a "$WORK/openfst-1.6.7/src/include" "$OUT/kaldi/include/openfst"
ln -s ../kaldi/bin "$OUT/openfst/bin"
ln -s ../kaldi/bin "$OUT/openfst/lib"
ln -s ../kaldi/include/openfst "$OUT/openfst/include"

patched=0
for f in "$OUT"/kaldi/bin/*; do
    [[ -L "$f" ]] && continue
    if readelf -d "$f" 2> /dev/null | grep -q 'libgfortran\.so\.3'; then
        patchelf --remove-needed libgfortran.so.3 "$f"
        patched=$((patched + 1))
    fi
done
echo "$OUT: $(du -sh "$OUT" | cut -f1), gfortran 3 taken out of $patched binaries"
