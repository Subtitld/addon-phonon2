#!/usr/bin/env python3
"""Assemble THIRD_PARTY_LICENSES/ for the frozen bundle, and fail if one is missing.

Copyright 2026 Subtitld contributors
SPDX-License-Identifier: Apache-2.0

The frozen bundle carries every Python distribution it imports, CPython and its
native libraries, fermion's kernels, torch's own runtime, and the system
libraries PyInstaller copies from the build host (libgomp, libstdc++, libsndfile,
libquadmath, OpenSSL, ...). Their notices must travel with the archive.

Two sources:

  * **Harvested** from the bundle itself: every `*.dist-info` under `_internal/`
    carries its licence files (PyInstaller copies the whole dist-info), copied
    with their relative paths, plus PyInstaller's own licence (bootloader
    exception), taken from the build environment.
  * **Committed** in this repository's THIRD_PARTY_LICENSES/: texts that no wheel
    carries (the GCC runtime, libquadmath, libsndfile, the system libraries).

Every shared library in the bundle must match a rule below. An unknown one fails
the build, because an unlisted library is an unlisted licence.

    python tools/fetch_third_party_licenses.py --bundle dist/phonon2-addon \\
        --out release-staging/THIRD_PARTY_LICENSES
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC = REPO_ROOT / 'THIRD_PARTY_LICENSES'

_LICENCE_FILE = re.compile(r'^(LICEN[CS]E|COPYING|NOTICE)', re.IGNORECASE)
# A library's name ends in its suffix. OpenSSL's FIPS checksums (`.libssl.so.1.1.hmac`)
# and fermion's `.sha256` pins are not libraries.
_NATIVE = re.compile(r'\.(so(\.\d+)*|dylib|dll)$', re.IGNORECASE)
# Python extension modules: their licence is their package's (harvested below).
_PY_EXTENSION = re.compile(r'(\.cpython-\d+[\w-]*\.so|\.abi3\.so|\.pyd)$')

# Shared library -> the notice that covers it. A python-dist entry means the
# library ships inside that distribution, whose own licence files cover it.
_NATIVE_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r'^(libgomp|libstdc\+\+|libgcc_s|libgfortran)'), 'gcc-runtime'),
    (re.compile(r'^libquadmath'), 'libquadmath'),
    (re.compile(r'^libsndfile'), 'libsndfile'),
    # OpenSSL 3 is Apache-2.0; OpenSSL 1.1.1 (the AlmaLinux 8 build) is the OpenSSL
    # and SSLeay licences. Any other release needs its own rule and text.
    (re.compile(r'^(libcrypto|libssl)\.so\.3'), 'python-runtime-libs'),
    (re.compile(r'^(libcrypto|libssl)\.so\.1\.1'), 'openssl-1.1'),
    (re.compile(r'^(libexpat|libffi|libz\.so|libbz2|liblzma)'), 'python-runtime-libs'),
    (re.compile(r'^libpython'), 'python-runtime-libs'),
    (re.compile(r'^libuuid'), 'libuuid'),
    (re.compile(r'^libmpdec'), 'mpdecimal'),
    (re.compile(r'^libzstd'), 'libzstd'),
    (re.compile(r'^libsqlite3'), 'libsqlite3'),
    (re.compile(r'^libze_loader'), 'level-zero'),
    (re.compile(r'^(libtorch|libc10|libshm|libaoti|libbackend_with|libjitbackend|libtorchbind)'),
     'python-dist:torch'),
    (re.compile(r'^libscipy_openblas'), 'python-dist:scipy'),
    (re.compile(r'^libphonon'), 'python-dist:fermion-research'),
    (re.compile(r'^libphonon_cpu'), 'python-dist:fermion-research'),
]

# Distributions whose wheel carries no licence file. Each maps to a committed text.
_DIST_LICENCE_OVERRIDE = {
    'tokenizers': 'APACHE-2.0.txt',  # the Apache-2.0 text, as the project declares
}

# Committed static groups that must exist in THIRD_PARTY_LICENSES/.


def _licence_files(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob('*') if p.is_file() and _LICENCE_FILE.match(p.name))


def _harvest_dists(internal: Path, out: Path) -> dict[str, list[str]]:
    """Copy each bundled dist-info's licence files. Returns {dist: [files]}; fails on gaps."""
    gaps: list[str] = []
    found: dict[str, list[str]] = {}
    for info in sorted(internal.glob('*.dist-info')):
        name = info.name[:-len('.dist-info')]
        files = _licence_files(info)
        if not files:
            if name.split('-')[0] in _DIST_LICENCE_OVERRIDE:
                target = out / 'python' / name
                target.mkdir(parents=True, exist_ok=True)
                shutil.copy2(STATIC / _DIST_LICENCE_OVERRIDE[name.split('-')[0]], target / 'LICENSE')
                found[name] = ['LICENSE (committed text)']
                continue
            gaps.append(name)
            continue
        target = out / 'python' / name
        for src in files:
            dest = target / src.relative_to(info)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
        found[name] = [str(p.relative_to(info)) for p in files]
    if gaps:
        raise SystemExit('FATAL: bundled distribution(s) with no licence file and no committed text: '
                         + ', '.join(gaps))
    return found


def _harvest_pyinstaller(out: Path) -> None:
    """PyInstaller's licence (with the bootloader exception) is not in the bundle's metadata."""
    try:
        dist = distribution('pyinstaller')
    except PackageNotFoundError:
        raise SystemExit('FATAL: PyInstaller is not installed in the build environment.')
    target = out / 'pyinstaller'
    target.mkdir(parents=True, exist_ok=True)
    info = next(Path(dist.locate_file('')).glob('pyinstaller-*.dist-info'))
    files = _licence_files(info)
    if not files:
        raise SystemExit('FATAL: PyInstaller ships no licence file in its dist-info.')
    for src in files:
        dest = target / src.relative_to(info)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)


def _classify_natives(internal: Path) -> dict[str, str]:
    """{relative library path: group}. Fails on any library no rule covers."""
    groups: dict[str, str] = {}
    unknown: list[str] = []
    for path in sorted(internal.rglob('*')):
        if not path.is_file() or not _NATIVE.search(path.name) or _PY_EXTENSION.search(path.name):
            continue
        rel = str(path.relative_to(internal))
        for pattern, group in _NATIVE_RULES:
            if pattern.match(path.name):
                groups[rel] = group
                break
        else:
            unknown.append(rel)
    if unknown:
        raise SystemExit('FATAL: shared librar(y/ies) with no licence rule — add one to '
                         '_NATIVE_RULES and a committed text, or drop the library: '
                         + ', '.join(unknown))
    return groups


def _copy_static(out: Path, used_groups: set[str]) -> None:
    """Copy the committed texts of every static group the bundle uses."""
    static_used = {g for g in used_groups if not g.startswith('python-dist:')}
    missing = [g for g in sorted(static_used) if not (STATIC / g).is_dir()]
    if missing:
        raise SystemExit('FATAL: committed licence text missing for: ' + ', '.join(missing))
    for group in sorted(static_used):
        shutil.copytree(STATIC / group, out / group, dirs_exist_ok=True)
    # CPython's own licence (PSF) travels with the runtime whatever else is bundled.
    shutil.copytree(STATIC / 'python', out / 'python-runtime', dirs_exist_ok=True)
    shutil.copy2(STATIC / 'APACHE-2.0.txt', out / 'APACHE-2.0.txt')


def _check_python_dists(used_groups: set[str], dists: dict[str, list[str]]) -> None:
    """A `python-dist:<name>` rule only holds when that distribution is in the bundle."""
    bundled = {name.lower().replace('_', '-') for name in dists}
    absent = sorted(g.split(':', 1)[1] for g in used_groups if g.startswith('python-dist:')
                    and not any(b == g.split(':', 1)[1].lower().replace('_', '-')
                                or b.startswith(g.split(':', 1)[1].lower().replace('_', '-') + '-')
                                for b in bundled))
    if absent:
        raise SystemExit('FATAL: a library is covered by a distribution that is not in the bundle: '
                         + ', '.join(absent))


def _write_index(out: Path, natives: dict[str, str], dists: dict[str, list[str]]) -> None:
    lines = ['Third-party licences in this archive', '=' * 36, '',
             'Shared libraries bundled, and the notice that covers each:', '']
    for rel, group in natives.items():
        lines.append(f'  {rel:<60} {group}')
    lines += ['', 'Python distributions bundled, with their licence files:', '']
    for name, files in dists.items():
        lines.append(f'  {name}: {", ".join(files)}')
    (out / 'INDEX.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--bundle', required=True, type=Path, help='the PyInstaller onedir folder')
    parser.add_argument('--out', required=True, type=Path, help='the THIRD_PARTY_LICENSES folder to write')
    args = parser.parse_args(argv)

    internal = args.bundle / '_internal'
    if not internal.is_dir():
        raise SystemExit(f'FATAL: {internal} is not a PyInstaller onedir bundle')
    if args.out.exists():
        shutil.rmtree(args.out)
    args.out.mkdir(parents=True)

    dists = _harvest_dists(internal, args.out)
    _harvest_pyinstaller(args.out)
    natives = _classify_natives(internal)
    used = set(natives.values())
    _check_python_dists(used, dists)
    _copy_static(args.out, used)
    shutil.copy2(REPO_ROOT / 'NOTICE', args.out / 'NOTICE')
    _write_index(args.out, natives, dists)
    print(f'{len(dists)} distribution licence set(s), {len(natives)} native librar(y/ies) covered, '
          f'written to {args.out}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
