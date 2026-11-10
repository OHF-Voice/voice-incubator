# The standalone image, from Debian itself, started directly: see README.md,
# "Standalone". The Home Assistant app's image is built from a release of this
# source by its own Dockerfile (OHF-Voice/app-voice-incubator), in the same
# steps: a change here belongs there too.
#
# What is compiled -- adapt-ctc's Viterbi, the wake verifier's decoder,
# FluidSynth -- and the Python packages are built in the first stage, so the
# image has no compiler. TRAINING=false leaves out wake word training
# (TensorFlow, 2 GB).
ARG BUILD_FROM=debian:trixie-slim

FROM ${BUILD_FROM} AS builder
ARG BUILD_ARCH
ARG TRAINING=true

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

WORKDIR /usr/src/voice_labs
ENV PIP_BREAK_SYSTEM_PACKAGES=1

RUN \
    apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        cmake \
        curl \
        libopenblas-dev \
        libsndfile1-dev \
        pkg-config \
        python3 \
        python3-dev \
        python3-pip \
    && rm -rf /var/lib/apt/lists/*

# FluidSynth plays the call's sounds (voice_labs/instruments.py): only its
# synth, so without the audio drivers Debian's is built with -- SDL, which
# brings Mesa and LLVM (200 MB), PulseAudio, PipeWire, JACK. libsndfile reads
# the SoundFont's compressed samples.
ARG FLUIDSYNTH_VERSION=2.6.1
ARG FLUIDSYNTH_SHA256=3d258a3bf97cc20c59eeebfe62c2432fae88adda74d3ad098681c76e0ebf446b
RUN \
    curl -sSfL -o /tmp/fluidsynth.tar.gz \
        "https://github.com/FluidSynth/fluidsynth/archive/refs/tags/v${FLUIDSYNTH_VERSION}.tar.gz" \
    && echo "${FLUIDSYNTH_SHA256}  /tmp/fluidsynth.tar.gz" | sha256sum -c - \
    && mkdir /tmp/fluidsynth && tar xzf /tmp/fluidsynth.tar.gz -C /tmp/fluidsynth --strip-components 1 \
    && cmake -S /tmp/fluidsynth -B /tmp/fluidsynth/build -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/usr/local -DCMAKE_INSTALL_LIBDIR=lib \
        -Denable-alsa=off -Denable-aufile=off -Denable-dbus=off -Denable-ipv6=off \
        -Denable-jack=off -Denable-ladspa=off -Denable-libinstpatch=off -Denable-midishare=off \
        -Denable-network=off -Denable-oss=off -Denable-pipewire=off -Denable-portaudio=off \
        -Denable-pulseaudio=off -Denable-readline=off -Denable-sdl3=off -Denable-systemd=off \
        -Denable-signalsmith=off -Denable-libsndfile=on \
    && cmake --build /tmp/fluidsynth/build -j "$(nproc)" \
    && cmake --install /tmp/fluidsynth/build \
    && rm -rf /tmp/fluidsynth /tmp/fluidsynth.tar.gz /usr/local/bin/fluidsynth /usr/local/include /usr/local/share

COPY requirements.txt requirements-training.txt ./
RUN \
    pip3 install --no-cache-dir -U --ignore-installed setuptools wheel \
    && pip3 install --no-cache-dir -r requirements.txt \
    && if [ "${TRAINING}" = "true" ]; then pip3 install --no-cache-dir -r requirements-training.txt; fi

# What the image carries besides its code (voice_labs/downloads.py, IMAGE):
# the speaker, wake word and Kaldi models, and Kaldi's tools for this
# architecture, each checked against its SHA-256.
COPY voice_labs/__init__.py voice_labs/downloads.py ./voice_labs/
RUN python3 -m voice_labs.downloads fetch models --arch "${BUILD_ARCH:-$(dpkg --print-architecture | sed 's/arm64/aarch64/')}"

# The wake verifier's resident decoder, compiled against those tools; then
# only what the verifier, the decoder and phonetisaurus (Kaldi mode's guessed
# pronunciations) use of them is kept (wyoming_wake_kaldi/prune.py).
COPY wyoming_wake_kaldi/__init__.py wyoming_wake_kaldi/kaldi.py wyoming_wake_kaldi/prune.py ./wyoming_wake_kaldi/
COPY wyoming_wake_kaldi/decoderd ./wyoming_wake_kaldi/decoderd
RUN \
    mkdir -p models/wake-kaldi-decoderd \
    && bash wyoming_wake_kaldi/decoderd/build.sh "$PWD/models/wake-kaldi-tools" "$PWD/models/wake-kaldi-decoderd/verify_decoderd" \
    && rm -rf models/wake-kaldi-decoderd/.fst_shadow_include \
    && python3 -m wyoming_wake_kaldi.prune models/wake-kaldi-tools models/wake-kaldi-decoderd/verify_decoderd models/wake-kaldi-tools/phonetisaurus

# adapt-ctc's native Viterbi (several times faster than the numpy fallback).
COPY setup.py ./
COPY adapt_ctc ./adapt_ctc
RUN python3 setup.py build_ext --inplace && rm -rf build


# Perl: Kaldi's scripts, which build a wake word's graphs (Encode and the rest
# of Perl's modules, not only the minimal Perl Debian always has).
FROM ${BUILD_FROM} AS runtime

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

WORKDIR /usr/src/voice_labs

RUN \
    apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        libgomp1 \
        libopenblas0 \
        libsndfile1 \
        musescore-general-soundfont-small \
        perl \
        python3 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local /usr/local
RUN ldconfig
COPY --from=builder /usr/src/voice_labs/models ./models
COPY wake.yaml ./
COPY sounds ./sounds
COPY --from=builder /usr/src/voice_labs/adapt_ctc ./adapt_ctc
COPY wyoming_wake_kaldi ./wyoming_wake_kaldi
COPY sentence_stream ./sentence_stream
COPY microwakeword ./microwakeword
COPY voice_labs ./voice_labs
# Parakeet, EmbeddingGemma and GLiNER are downloaded into the app's data the
# first time it starts (voice_labs/downloads.py), and Piper's voices the first
# time one is used (voice_labs/piper_voices.py): linked from models/.
RUN python3 -m voice_labs.downloads link /data/models

HEALTHCHECK --start-period=10m \
    CMD curl -sf http://127.0.0.1:8099/health > /dev/null || exit 1


# Standalone: Home Assistant's address and a long-lived access token from
# HASS_URL and HASS_TOKEN; the app's data in /data. The web UI (8099) and the
# Wyoming server (10800) listen on every address the container has; what
# reaches them is the container's network's to say. More options after the
# image name (`--help`).
FROM runtime AS standalone
VOLUME /data
EXPOSE 8099 10800
ENTRYPOINT ["python3", "-m", "voice_labs", "--data-dir", "/data", "--http-host", "0.0.0.0", "--wyoming-host", "0.0.0.0"]
