FROM ubuntu:24.04@sha256:4fdf0125919d24aec972544669dcd7d6a26a8ad7e6561c73d5549bd6db258ac2 AS build

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=UTC

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN apt-get update && \
    apt-get install -y \
        ca-certificates \
        wget \
        curl \
        git \
        tzdata \
        python3.12 \
        python3.12-venv \
        python3-dev \
        python3-pip \
        autoconf \
        automake \
        build-essential \
        cmake \
        libass-dev \
        libbz2-dev \
        libfontconfig-dev \
        libfreetype-dev \
        libfribidi-dev \
        libharfbuzz-dev \
        libjansson-dev \
        liblzma-dev \
        libmp3lame-dev \
        libnuma-dev \
        libogg-dev \
        libopus-dev \
        libsamplerate0-dev \
        libspeex-dev \
        libtheora-dev \
        libtool \
        libtool-bin \
        libturbojpeg0-dev \
        libvorbis-dev \
        libx264-dev \
        libxml2-dev \
        libvpx-dev \
        m4 \
        make \
        meson \
        nasm \
        ninja-build \
        patch \
        pkg-config \
        tar \
        zlib1g-dev \
        libssl-dev \
        clang && \
    rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    mkdir -p /tmp/ffmpeg_static_install && \
    cd /tmp/ffmpeg_static_install && \
    wget -O ffmpeg.tar.xz \
        https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2025-11-30-12-53/ffmpeg-n7.1.3-7-gf65fc0b137-linux64-gpl-7.1.tar.xz && \
    tar -xf ffmpeg.tar.xz && \
    cd ffmpeg-*/bin && \
    strip ffmpeg ffprobe || true && \
    mkdir -p /opt/bin && \
    cp ffmpeg ffprobe /opt/bin/ && \
    cd / && rm -rf /tmp/ffmpeg_static_install

RUN set -eux; \
    DOVI_VERSION="2.3.1"; \
    mkdir -p /opt/bin && \
    cd /tmp && \
    wget -O dovi_tool.tar.gz \
        https://github.com/quietvoid/dovi_tool/releases/download/${DOVI_VERSION}/dovi_tool-${DOVI_VERSION}-x86_64-unknown-linux-musl.tar.gz && \
    tar -xzf dovi_tool.tar.gz && \
    cp dovi_tool /opt/bin/ && \
    chmod +x /opt/bin/dovi_tool && \
    /opt/bin/dovi_tool --version && \
    rm -rf /tmp/dovi_tool*

RUN set -eux; \
    handbrake_version="1.10.2"; \
    mkdir -p /tmp/handbrake && \
    cd /tmp/handbrake && \
    wget -O HandBrake.tar.bz2 \
        https://github.com/HandBrake/HandBrake/releases/download/${handbrake_version}/HandBrake-${handbrake_version}-source.tar.bz2 && \
    tar -xvjf HandBrake.tar.bz2 && \
    cd HandBrake-* && \
    ./configure --disable-gtk --enable-cli && \
    cd build && \
    make -j"$(nproc)" && \
    strip HandBrakeCLI || true && \
    mkdir -p /opt/bin && \
    cp HandBrakeCLI /opt/bin/ && \
    cd / && rm -rf /tmp/handbrake

WORKDIR /pre
COPY requirements.txt /pre/requirements.txt

RUN python3.12 -m venv /pre/venv && \
    /pre/venv/bin/pip install --upgrade pip && \
    /pre/venv/bin/pip install -r /pre/requirements.txt


FROM ubuntu:24.04@sha256:4fdf0125919d24aec972544669dcd7d6a26a8ad7e6561c73d5549bd6db258ac2

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=UTC

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN apt-get update && \
    apt-get install -y \
        software-properties-common \
        ca-certificates \
        wget \
        curl \
        gnupg \
        tzdata \
        python3.12 \
        python3.12-venv \
        python3-pip \
        mkvtoolnix \
        unrar \
        mono-complete \
        libhunspell-dev \
        libmpv-dev \
        vlc \
        libgtk2.0-0 \
        libsndfile1 \
        libcanberra-gtk-module \
        libturbojpeg \
        git \
        xvfb \
        x11-utils \
        flatpak \
        vim \
        mediainfo \
        gosu && \
    add-apt-repository ppa:alex-p/tesseract-ocr5 -y && \
    apt-get update && \
    apt-get install -y --no-install-recommends tesseract-ocr && \
    apt-get update && \
    apt-cache search tesseract-ocr | grep -v 'ocr-script' | grep -v 'old' | awk '{print $1}' | xargs apt-get install -y && \
    rm -rf /var/lib/apt/lists/*

COPY --from=build /opt/bin/ffmpeg /usr/local/bin/ffmpeg
COPY --from=build /opt/bin/ffprobe /usr/local/bin/ffprobe
COPY --from=build /opt/bin/HandBrakeCLI /usr/local/bin/HandBrakeCLI
COPY --from=build /opt/bin/dovi_tool /usr/local/bin/dovi_tool
COPY --from=build /pre/venv /pre/venv

ENV VIRTUAL_ENV=/pre/venv
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

WORKDIR /mkv-auto
COPY modules /mkv-auto/modules
COPY utilities /mkv-auto/utilities
COPY defaults.ini /mkv-auto/
COPY subliminal_defaults.toml /mkv-auto/
COPY mkv-auto.py /mkv-auto/
COPY entrypoint.sh /mkv-auto/
COPY service-entrypoint.sh /mkv-auto/
COPY service-entrypoint-inner.sh /mkv-auto/

RUN chmod +x /mkv-auto/*.sh && \
    mkdir -p /mkv-auto/files/.cache

USER root
ENTRYPOINT ["/mkv-auto/entrypoint.sh"]
