#!/usr/bin/env bash
# Run tests/test_frozen.py against the release archive, in a clean container.
#
# Copyright 2026 Subtitld contributors
# SPDX-License-Identifier: Apache-2.0
#
# The archive is unpacked the way Subtitld's installer does it (zipfile's
# extractall, then the executable bit), into a distribution that has none of the
# build's packages. The container gets only what the tests need: ffmpeg (Subtitld
# provides its own), Python and pytest to drive the binary. The binary itself runs
# with Python's variables scrubbed (see tests/test_frozen.py).
#
#     tools/test_archive.sh ubuntu:22.04
#     tools/test_archive.sh fedora:latest phonon2-0.0.1-linux-x86_64.zip
#
# Set PHONON2_TEST_MODELS_DIR to a folder that already holds the model to skip the
# download: it is mounted read-only and copied in. Needs Docker and the network.

set -euo pipefail

IMAGE="${1:?usage: tools/test_archive.sh <image> [archive]}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(sed -n 's/^__version__ = .\(.*\).$/\1/p' "$ROOT/phonon2_addon/__init__.py")"
ARCHIVE="$(realpath "${2:-$ROOT/phonon2-${VERSION}-linux-x86_64.zip}")"
[ -f "$ARCHIVE" ] || { echo "no archive at $ARCHIVE; run tools/build_linux.sh" >&2; exit 1; }

MOUNTS=(-v "$ARCHIVE:/artifact/addon.zip:ro" -v "$ROOT/tests:/repo/tests:ro")
if [ -n "${PHONON2_TEST_MODELS_DIR:-}" ]; then
    MOUNTS+=(-v "$(realpath "$PHONON2_TEST_MODELS_DIR"):/models:ro")
fi

docker run --rm "${MOUNTS[@]}" "$IMAGE" bash -euo pipefail -c '
    if command -v apt-get >/dev/null; then
        if grep -q "^10" /etc/debian_version 2>/dev/null; then
            # Debian 10 (glibc 2.28) is archived; its packages live on archive.debian.org.
            echo "deb http://archive.debian.org/debian buster main" > /etc/apt/sources.list
            APT_OPTS="-o Acquire::Check-Valid-Until=false"
        fi
        apt-get ${APT_OPTS:-} update -qq >/dev/null
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ffmpeg python3 python3-pytest >/dev/null
    elif command -v dnf >/dev/null; then
        dnf install -y -q ffmpeg-free python3 python3-pytest >/dev/null
    elif command -v pacman >/dev/null; then
        # Arch has no partial upgrades: refresh the keyring, then upgrade fully.
        pacman -Sy --noconfirm archlinux-keyring >/dev/null
        pacman -Syu --noconfirm --needed ffmpeg python python-pytest >/dev/null
    else
        echo "no supported package manager in this image" >&2; exit 1
    fi

    . /etc/os-release
    echo "image: $PRETTY_NAME, $(ldd --version | head -1)"
    echo "ffmpeg: $(ffmpeg -version | head -1 | cut -d" " -f1-3)"

    python3 - <<PY
import os, stat, zipfile
zipfile.ZipFile("/artifact/addon.zip").extractall("/opt/addon")
exe = "/opt/addon/bin/phonon2-addon"
os.chmod(exe, os.stat(exe).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
PY

    # Which CA bundle the binary trusts here: the add-on logs it at startup.
    trust=$(printf "{\"type\":\"shutdown\"}\n" | /opt/addon/bin/phonon2-addon 2>&1 >/dev/null \
            | grep -o "trusts the CA bundle at .*" || true)
    echo "download ${trust:-trust: none set (SSL_CERT_FILE given, or no bundle found)}"

    mkdir -p /tmp/models
    [ -d /models ] && cp -r /models/. /tmp/models/
    cd /tmp
    PHONON2_TEST_BINARY=/opt/addon/bin/phonon2-addon \
    PHONON2_TEST_TRANSCRIPTION=1 \
    PHONON2_TEST_MODELS_DIR=/tmp/models \
        python3 -m pytest -q -p no:cacheprovider -rs /repo/tests/test_frozen.py
'
