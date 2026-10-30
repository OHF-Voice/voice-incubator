ARG BUILD_FROM
FROM ${BUILD_FROM}
ARG BUILD_ARCH

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

WORKDIR /usr/src/voice_labs
ENV PIP_BREAK_SYSTEM_PACKAGES=1

COPY requirements.txt ./
RUN \
    apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        libfluidsynth3 \
        libgomp1 \
        libicu-dev \
        libopenblas0 \
        musescore-general-soundfont-small \
        python3 \
        python3-dev \
        python3-pip \
    && pip3 install --no-cache-dir -U --ignore-installed setuptools wheel \
    && pip3 install --no-cache-dir -r requirements.txt \
    && rm -rf /var/lib/apt/lists/*

# Training wake words in the app: TensorFlow and what microWakeWord's training
# needs. microWakeWord itself is vendored (microwakeword/).
COPY requirements-training.txt ./
RUN pip3 install --no-cache-dir -r requirements-training.txt

# What the image carries besides its code (voice_labs/downloads.py, IMAGE):
# the speaker, wake word and Kaldi models, and Kaldi's tools for this
# architecture, each checked against its SHA-256.
COPY voice_labs/__init__.py voice_labs/downloads.py ./voice_labs/
RUN python3 -m voice_labs.downloads fetch models --arch "${BUILD_ARCH}"
# The wake verifier's resident decoder (vendored with wyoming-wake-kaldi),
# compiled against those tools.
COPY wyoming_wake_kaldi/decoderd ./wyoming_wake_kaldi/decoderd
RUN apt-get update \
    && apt-get install -y --no-install-recommends libopenblas-dev \
    && mkdir -p models/wake-kaldi-decoderd \
    && bash wyoming_wake_kaldi/decoderd/build.sh "$PWD/models/wake-kaldi-tools" "$PWD/models/wake-kaldi-decoderd/verify_decoderd" \
    && rm -rf models/wake-kaldi-decoderd/.fst_shadow_include \
    && apt-get purge -y libopenblas-dev && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

COPY setup.py wake.yaml ./
COPY sounds ./sounds
COPY adapt_ctc ./adapt_ctc
COPY wyoming_wake_kaldi ./wyoming_wake_kaldi
COPY sentence_stream ./sentence_stream
COPY microwakeword ./microwakeword
COPY voice_labs ./voice_labs
# adapt-ctc's native Viterbi (several times faster than the numpy fallback).
RUN python3 setup.py build_ext --inplace && rm -rf build
# Parakeet, EmbeddingGemma and GLiNER are downloaded into the app's data the
# first time it starts (voice_labs/downloads.py), and Piper's voices the first
# time one is used (voice_labs/piper_voices.py): linked from models/.
RUN python3 -m voice_labs.downloads link /data/models

WORKDIR /
COPY rootfs /

HEALTHCHECK --start-period=10m \
    CMD curl -sf http://127.0.0.1:8099/health > /dev/null || exit 1
