#!/usr/bin/env python3
"""Build the libsndfile source tarball attached to every release.

Copyright 2026 Subtitld contributors
SPDX-License-Identifier: Apache-2.0

Why: the archive ships soundfile's libsndfile (`_soundfile_data/libsndfile_x86_64.so`),
which fermion reads audio through. It is LGPL-2.1-or-later, and it statically
contains LAME (LGPL-2.0-or-later) and mpg123 (LGPL-2.1-or-later), with libFLAC,
libogg, libvorbis and Opus (BSD-style). LGPL-2.1 section 4 lets object code be
distributed by offering the complete corresponding source from the same place,
with no written-offer shortcut, so the release attaches this tarball.

Which source: soundfile 0.14.0's wheel ships, unmodified, the Linux binary of
bastibe/libsndfile-binaries at commit 39f3e270 ("Linux binary replaced with
old-glibc version", 2025-01-02). That commit's linux_build.sh builds it from the
upstream releases pinned below. The tarball holds the script and those releases,
each checked against its pinned sha256.

    python tools/build_libsndfile_source.py --out phonon2-addon-0.0.1-libsndfile-source.tar.gz
    python tools/build_libsndfile_source.py --check-bundle dist/phonon2-addon
    python tools/build_libsndfile_source.py --licences THIRD_PARTY_LICENSES/libsndfile

`--check-bundle` fails when the bundled library is not that binary (a soundfile
upgrade must update the constants below, the tarball and the licence texts).
`--licences` writes each component's licence file, for THIRD_PARTY_LICENSES.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

SHIPPED = '_soundfile_data/libsndfile_x86_64.so'
SHIPPED_SHA256 = 'c872fb84af2afc0e96b603e0000f0f407af86c49ad1126f2785e5722c7bb27d7'
BINARIES_REPO = 'https://github.com/bastibe/libsndfile-binaries'
BINARIES_COMMIT = '39f3e270a4649576c72630db081658524ce7bea8'
SCRIPT_URL = f'https://raw.githubusercontent.com/bastibe/libsndfile-binaries/{BINARIES_COMMIT}/linux_build.sh'
SCRIPT_SHA256 = '90709951b06174c4eebc201419e3671e65ec4ef38a279c34a1e9d3dd2bf33bde'
_MTIME = 1735822026  # 2025-01-02, the libsndfile-binaries commit: reproducible output


@dataclass(frozen=True)
class Component:
    name: str
    version: str
    url: str
    sha256: str
    licence: str
    licence_files: tuple[str, ...]  # inside the tarball's top directory

    @property
    def filename(self) -> str:
        return self.url.rsplit('/', 1)[1]

    @property
    def tree(self) -> str:
        return f'{self.name}-{self.version}'


# The URLs are linux_build.sh's own, at BINARIES_COMMIT.
COMPONENTS = (
    Component('libsndfile', '1.2.2',
              'https://github.com/libsndfile/libsndfile/releases/download/1.2.2/libsndfile-1.2.2.tar.xz',
              '3799ca9924d3125038880367bf1468e53a1b7e3686a934f098b7e1d286cdb80e',
              'LGPL-2.1-or-later', ('COPYING',)),
    Component('lame', '3.100',
              'https://sourceforge.net/projects/lame/files/lame/3.100/lame-3.100.tar.gz',
              'ddfe36cab873794038ae2c1210557ad34857a4b6bdc515785d1da9e175b1da1e',
              'LGPL-2.0-or-later', ('COPYING', 'LICENSE')),
    Component('mpg123', '1.32.3',
              'https://sourceforge.net/projects/mpg123/files/mpg123/1.32.3/mpg123-1.32.3.tar.bz2',
              '2d9913a57d4ee8f497a182c6e82582602409782a4fb481e989feebf4435867b4',
              'LGPL-2.1-or-later', ('COPYING',)),
    Component('flac', '1.4.3',
              'https://downloads.xiph.org/releases/flac/flac-1.4.3.tar.xz',
              '6c58e69cd22348f441b861092b825e591d0b822e106de6eb0ee4d05d27205b70',
              'BSD-3-Clause (libFLAC, under COPYING.Xiph)', ('COPYING.Xiph',)),
    Component('libogg', '1.3.5',
              'https://downloads.xiph.org/releases/ogg/libogg-1.3.5.tar.gz',
              '0eb4b4b9420a0f51db142ba3f9c64b333f826532dc0f48c6410ae51f4799b664',
              'BSD-3-Clause', ('COPYING',)),
    Component('libvorbis', '1.3.7',
              'https://downloads.xiph.org/releases/vorbis/libvorbis-1.3.7.tar.gz',
              '0e982409a9c3fc82ee06e08205b1355e5c6aa4c36bca58146ef399621b0ce5ab',
              'BSD-3-Clause', ('COPYING',)),
    Component('opus', '1.4',
              'https://downloads.xiph.org/releases/opus/opus-1.4.tar.gz',
              'c9b32b4253be5ae63d1ff16eea06b94b5f0f2951b7a02aceef58e3a3ce49c51f',
              'BSD-3-Clause', ('COPYING',)),
)


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def check_bundle(root: Path) -> int:
    found = sorted(p for p in root.rglob('libsndfile*') if p.is_file() and not p.is_symlink())
    if not found:
        _log(f'no libsndfile in {root}; nothing to check')
        return 0
    bad = 0
    for path in found:
        ok = path.as_posix().endswith(SHIPPED) and _sha256(path) == SHIPPED_SHA256
        bad += not ok
        _log(f'{"ok " if ok else "BAD"} {path.relative_to(root)}')
    if bad:
        _log(f'\nFATAL: the bundled libsndfile is not libsndfile-binaries@{BINARIES_COMMIT[:8]} '
             f'(sha256 {SHIPPED_SHA256}), whose source the release attaches. Find the new '
             f'binary\'s build and update tools/build_libsndfile_source.py and '
             f'THIRD_PARTY_LICENSES/libsndfile/.')
        return 1
    return 0


def _fetch(url: str, sha256: str, target: Path, src_dir: Path | None) -> Path:
    """A verified copy of `url`: from `src_dir` when it holds one, else downloaded."""
    if src_dir is not None and (src_dir / target.name).is_file():
        local = src_dir / target.name
        if _sha256(local) != sha256:
            raise SystemExit(f'FATAL: {local} does not match its pinned sha256 {sha256}')
        return local
    if src_dir is not None:  # keep the download for the next build
        target = src_dir / target.name
        target.parent.mkdir(parents=True, exist_ok=True)
    _log(f'fetching {url}')
    req = urllib.request.Request(url, headers={'User-Agent': 'subtitld-addon-phonon2'})
    with urllib.request.urlopen(req, timeout=300) as resp, open(target, 'wb') as out:
        shutil.copyfileobj(resp, out, 1 << 20)
    digest = _sha256(target)
    if digest != sha256:
        raise SystemExit(f'FATAL: {url} has sha256 {digest}, not the pinned {sha256}')
    return target


def _add_bytes(tar: tarfile.TarFile, name: str, data: bytes, mode: int = 0o644) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = mode
    info.mtime = _MTIME
    tar.addfile(info, io.BytesIO(data))


def build(out: Path, src_dir: Path | None, version: str) -> int:
    prefix = out.name.removesuffix('.tar.gz')
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        script = _fetch(SCRIPT_URL, SCRIPT_SHA256, work / 'linux_build.sh', src_dir)
        sources = [(c, _fetch(c.url, c.sha256, work / c.filename, src_dir)) for c in COMPONENTS]
        try:
            with open(out, 'wb') as raw, \
                    gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as gz, \
                    tarfile.open(fileobj=gz, mode='w', format=tarfile.PAX_FORMAT) as tar:
                _add_bytes(tar, f'{prefix}/linux_build.sh', script.read_bytes(), 0o755)
                for component, path in sources:
                    _add_bytes(tar, f'{prefix}/{component.filename}', path.read_bytes())
                rows = '\n'.join(f'| {c.name} | {c.version} | {c.licence} | `{c.filename}` | `{c.sha256}` |'
                                 for c in COMPONENTS)
                readme = _README.format(version=version, shipped=SHIPPED, shipped_sha=SHIPPED_SHA256,
                                        repo=BINARIES_REPO, commit=BINARIES_COMMIT,
                                        script_sha=SCRIPT_SHA256, rows=rows)
                _add_bytes(tar, f'{prefix}/README.md', readme.encode('utf-8'))
        except BaseException:
            out.unlink(missing_ok=True)
            raise
    _log(f'wrote {out} ({out.stat().st_size / 2**20:.1f} MiB)')
    return 0


def write_licences(dest: Path, src_dir: Path | None) -> int:
    """Each component's licence files, into dest/<name>/."""
    with tempfile.TemporaryDirectory() as tmp:
        for c in COMPONENTS:
            path = _fetch(c.url, c.sha256, Path(tmp) / c.filename, src_dir)
            target = dest / c.name
            target.mkdir(parents=True, exist_ok=True)
            with tarfile.open(path, 'r:*') as tar:
                for name in c.licence_files:
                    member = tar.getmember(f'{c.tree}/{name}')
                    (target / name).write_bytes(tar.extractfile(member).read())
            _log(f'{c.name} {c.version}: {", ".join(c.licence_files)} -> {target}')
    return 0


_README = """# libsndfile source for the Subtitld Phonon-2 add-on {version}

The archive of this add-on release ships `bin/_internal/{shipped}`: libsndfile,
licensed under the GNU Lesser General Public License 2.1 or later, with LAME and
mpg123 (both LGPL) and libFLAC, libogg, libvorbis and Opus (BSD-style) linked into
it statically. This tarball is its complete corresponding source, offered from the
same place as the binaries (LGPL-2.1 section 4). The licence texts are in the
archive's `THIRD_PARTY_LICENSES/libsndfile/`.

## Which build

The file comes, unmodified, from the soundfile 0.14.0 wheel, which ships the Linux
binary of {repo} at commit `{commit}`:

| | |
|---|---|
| sha256 of the shipped file | `{shipped_sha}` |
| build script | `linux_build.sh` at that commit, sha256 `{script_sha}` |

The release refuses to publish a bundle whose libsndfile is a different file
(`tools/build_libsndfile_source.py --check-bundle`).

## Contents

| Component | Version | Licence | File | sha256 |
|---|---|---|---|---|
{rows}

Each is the upstream release tarball that `linux_build.sh` downloads, from the URL
the script names.

## Rebuilding

`linux_build.sh` builds the static codec libraries and then libsndfile against
them. It downloads the same tarballs as it goes. To build from the copies here
instead, run it in this directory with the `curl -LO` lines removed:

    sed -i '/curl -LO/d' linux_build.sh
    sh linux_build.sh

The result is `libsndfile.so`. Copy it over the shipped file, keeping the shipped
file name.
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument('--out', type=Path, help='tarball to write (*.tar.gz)')
    mode.add_argument('--check-bundle', type=Path, metavar='DIR',
                      help='verify the libsndfile under DIR is the pinned binary')
    mode.add_argument('--licences', type=Path, metavar='DIR',
                      help='write each component\'s licence files into DIR/<component>/')
    ap.add_argument('--src-dir', type=Path,
                    help='sources folder: used when they are there, downloaded into it when not')
    ap.add_argument('--version', help='add-on version for the README (default: manifest.json)')
    args = ap.parse_args(argv)
    if args.check_bundle:
        return check_bundle(args.check_bundle)
    if args.licences:
        return write_licences(args.licences, args.src_dir)
    if not args.out.name.endswith('.tar.gz'):
        ap.error('--out must end in .tar.gz')
    version = args.version or json.loads(
        (Path(__file__).resolve().parent.parent / 'manifest.json').read_text(encoding='utf-8'))['version']
    return build(args.out, args.src_dir, version)


if __name__ == '__main__':
    raise SystemExit(main())
