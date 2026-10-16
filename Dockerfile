ARG BUILD_FROM
FROM ${BUILD_FROM}
ARG BUILD_ARCH

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

WORKDIR /usr/src/voice_call
ENV PIP_BREAK_SYSTEM_PACKAGES=1

COPY requirements.txt ./
RUN \
    apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        libgomp1 \
        libicu-dev \
        python3 \
        python3-dev \
        python3-pip \
    && pip3 install --no-cache-dir -U setuptools wheel \
    && pip3 install --no-cache-dir -r requirements.txt \
    && rm -rf /var/lib/apt/lists/*

# llama.cpp, for the LLM fallback (Hammer 2.1 0.5B, downloaded when it is
# turned on). Built from source, as script-agent's is: the ARM64 build needs
# ARMv8.2 with FP16 and dot products (Raspberry Pi 5, not 4).
COPY requirements.llm.txt ./
RUN \
    apt-get update \
    && apt-get install -y --no-install-recommends cmake ninja-build \
    && llama_cmake_args='-DCMAKE_BUILD_TYPE=Release' \
    && if [[ "${BUILD_ARCH}" == "aarch64" ]]; then \
        llama_cmake_args+=' -DGGML_NATIVE=OFF -DGGML_CPU_ARM_ARCH=armv8.2-a+fp16+dotprod'; \
    fi \
    && CMAKE_ARGS="${llama_cmake_args}" pip3 install --no-cache-dir -r requirements.llm.txt \
    && apt-get purge -y cmake ninja-build && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# Staged by script/stage_build: the Kaldi wake verifier and the models.
COPY local/wyoming-wake-kaldi /usr/src/wyoming-wake-kaldi
RUN pip3 install --no-cache-dir /usr/src/wyoming-wake-kaldi
COPY local/models ./models

COPY setup.py wake.yaml ./
COPY sounds ./sounds
COPY adapt_ctc ./adapt_ctc
COPY voice_call ./voice_call
# adapt-ctc's native Viterbi (several times faster than the numpy fallback).
RUN python3 setup.py build_ext --inplace && rm -rf build

WORKDIR /
COPY rootfs /

HEALTHCHECK --start-period=10m \
    CMD curl -sf http://127.0.0.1:8099/api/status > /dev/null || exit 1
