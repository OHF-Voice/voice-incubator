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
        libgomp1 \
        libicu-dev \
        libopenblas0 \
        python3 \
        python3-dev \
        python3-pip \
    && pip3 install --no-cache-dir -U --ignore-installed setuptools wheel \
    && pip3 install --no-cache-dir -r requirements.txt \
    && rm -rf /var/lib/apt/lists/*

# Staged by script/stage_build: the Kaldi wake verifier and the models.
COPY local/wyoming-wake-kaldi /usr/src/wyoming-wake-kaldi
RUN pip3 install --no-cache-dir /usr/src/wyoming-wake-kaldi
# sentence-stream from source (staged), until the release has its fixes.
COPY local/sentence-stream /usr/src/sentence-stream
RUN pip3 install --no-cache-dir --no-deps /usr/src/sentence-stream
# Training wake words in the app: TensorFlow, and microWakeWord (staged by
# script/stage_build) without its audio stack.
COPY requirements-training.txt ./
COPY local/micro-wake-word /usr/src/micro-wake-word
RUN pip3 install --no-cache-dir -r requirements-training.txt \
    && pip3 install --no-cache-dir --no-deps /usr/src/micro-wake-word
# Its layers/ and audio/ have no __init__.py, so pip (find_packages) leaves
# them out: the whole package, over what it installed.
RUN cp -r /usr/src/micro-wake-word/microwakeword/. \
        "$(python3 -c 'import microwakeword, os; print(os.path.dirname(microwakeword.__file__))')" \
    && python3 -c 'from microwakeword import mixednet, train, utils'
COPY local/models ./models
# The wake verifier's resident decoder, when it was staged as source (aarch64).
RUN if [ ! -x models/wake-kaldi-decoderd/verify_decoderd ]; then \
        apt-get update \
        && apt-get install -y --no-install-recommends libopenblas-dev \
        && bash models/wake-kaldi-decoderd/build.sh "$PWD/models/wake-kaldi-tools" \
        && rm -rf models/wake-kaldi-decoderd/.fst_shadow_include \
        && apt-get purge -y libopenblas-dev && apt-get autoremove -y \
        && rm -rf /var/lib/apt/lists/*; \
    fi

COPY setup.py wake.yaml ./
COPY sounds ./sounds
COPY adapt_ctc ./adapt_ctc
COPY voice_labs ./voice_labs
# adapt-ctc's native Viterbi (several times faster than the numpy fallback).
RUN python3 setup.py build_ext --inplace && rm -rf build

WORKDIR /
COPY rootfs /

HEALTHCHECK --start-period=10m \
    CMD curl -sf http://127.0.0.1:8099/health > /dev/null || exit 1
