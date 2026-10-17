#!/usr/bin/env bash
# Build the Linux frozen add-on and its archive, inside an AlmaLinux 8 container.
#
# Copyright 2026 Subtitld contributors
# SPDX-License-Identifier: Apache-2.0
#
# The bundle carries the build interpreter's libpython, so it runs on any glibc
# at least as new as the build's. The host's glibc is usually newer than the
# oldest distribution Subtitld supports (Ubuntu 22.04 has 2.35), and an
# interpreter built against a new glibc does not start there: the failure is
# `GLIBC_2.38 not found`. The build image (tools/linux-build.Dockerfile) is
# AlmaLinux 8, glibc 2.28, the same floor fermion requires for its Linux
# binaries, with the distribution's Python 3.11, which has the shared libpython
# PyInstaller needs.
#
#     tools/build_linux.sh            # writes phonon2-<version>-linux-x86_64.zip
#
# Needs Docker. Output lands in the repository, owned by you, not root.

set -euo pipefail

BUILD_IMAGE="${BUILD_IMAGE:-subtitld-phonon2-build:almalinux8-py311}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(sed -n 's/^__version__ = .\(.*\).$/\1/p' "$ROOT/phonon2_addon/__init__.py")"
ARCHIVE="phonon2-${VERSION}-linux-x86_64.zip"
# pip's cache survives between builds, so torch is not downloaded every time.
PIP_CACHE="${PIP_CACHE:-$HOME/.cache/subtitld-phonon2-build-pip}"
mkdir -p "$PIP_CACHE"
# The LGPL source packages and tarballs the release attaches, kept the same way.
SOURCES_CACHE="${SOURCES_CACHE:-$HOME/.cache/subtitld-phonon2-build-sources}"
mkdir -p "$SOURCES_CACHE"
QUADMATH_SRC="phonon2-addon-${VERSION}-libquadmath-source.tar.gz"
SNDFILE_SRC="phonon2-addon-${VERSION}-libsndfile-source.tar.gz"

# No build context: the Dockerfile copies nothing in.
docker build -q -t "$BUILD_IMAGE" - < "$ROOT/tools/linux-build.Dockerfile" >/dev/null

docker run --rm \
    --user "$(id -u):$(id -g)" \
    -e HOME=/tmp \
    -v "$ROOT:/src" \
    -v "$PIP_CACHE:/tmp/.cache/pip" \
    -v "$SOURCES_CACHE:/tmp/.cache/sources" \
    -w /src \
    "$BUILD_IMAGE" \
    bash -euo pipefail -c "
        command -v nm >/dev/null || { echo 'nm is required for the libexpat check' >&2; exit 1; }
        # The distribution's interpreter: /usr/local/bin/python3.11 is the image's static one.
        /usr/bin/python3.11 -m venv /tmp/build-venv
        . /tmp/build-venv/bin/activate
        pip install -q --upgrade pip
        # torch's CPU build first, so the CUDA wheel is never resolved.
        pip install -q --index-url https://download.pytorch.org/whl/cpu 'torch==2.14.1'
        pip install -q '.[build]'

        # A failed build must not leave an older archive that looks current.
        rm -rf build dist release-staging '$ARCHIVE' '$ARCHIVE.sha256' \\
            '$QUADMATH_SRC' '$QUADMATH_SRC.sha256' '$SNDFILE_SRC' '$SNDFILE_SRC.sha256'
        pyinstaller pyinstaller.spec --noconfirm

        # Every LGPL library in the bundle must be a build whose source is attached.
        python tools/build_libquadmath_source.py --check-bundle dist/phonon2-addon
        python tools/build_libsndfile_source.py --check-bundle dist/phonon2-addon

        mkdir -p release-staging/bin
        cp -R dist/phonon2-addon/. release-staging/bin/
        cp manifest.json LICENSE NOTICE README.md release-staging/
        python tools/fetch_third_party_licenses.py \
            --bundle dist/phonon2-addon --out release-staging/THIRD_PARTY_LICENSES
        python tools/make_archive.py release-staging '$ARCHIVE'
        sha256sum '$ARCHIVE' > '$ARCHIVE.sha256'

        python tools/build_libquadmath_source.py --srpm-dir /tmp/.cache/sources --out '$QUADMATH_SRC'
        python tools/build_libsndfile_source.py --src-dir /tmp/.cache/sources --out '$SNDFILE_SRC'
        sha256sum '$QUADMATH_SRC' > '$QUADMATH_SRC.sha256'
        sha256sum '$SNDFILE_SRC' > '$SNDFILE_SRC.sha256'
    "

echo "built $ARCHIVE and its LGPL source tarballs"
cat "$ROOT/$ARCHIVE.sha256" "$ROOT/$QUADMATH_SRC.sha256" "$ROOT/$SNDFILE_SRC.sha256"
