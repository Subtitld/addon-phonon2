# Build environment for the Linux frozen add-on (tools/build_linux.sh builds it).
#
# Copyright 2026 Subtitld contributors
# SPDX-License-Identifier: Apache-2.0
#
# AlmaLinux 8.10, glibc 2.28: the base of manylinux_2_28, and the floor fermion
# requires for its Linux binaries. PyInstaller needs a Python with a shared
# libpython. The manylinux image's own /opt/python interpreters are static, so
# the distribution's Python 3.11 is installed instead. It is /usr/bin/python3.11:
# the image's /usr/local/bin/python3.11 comes first on PATH and is the static one.
FROM quay.io/pypa/manylinux_2_28_x86_64:2025.11.09-2

RUN dnf install -y --setopt=install_weak_deps=False python3.11 python3.11-pip \
    && dnf clean all \
    && /usr/bin/python3.11 -c "import sysconfig, sys; sys.exit(0 if sysconfig.get_config_var('Py_ENABLE_SHARED') else '/usr/bin/python3.11 has no shared libpython')"
