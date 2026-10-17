# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Subtitld Phonon-2 add-on.

Copyright 2026 Subtitld contributors
SPDX-License-Identifier: Apache-2.0

Produces `dist/phonon2-addon/` (launcher + `_internal/`). The archive step
copies it to `bin/` inside the archive, next to manifest.json.

Most of what is annotated here fails at FIRST TRANSCRIPTION, not at build time:
the build succeeds, the handshake succeeds (it never loads a model), and only a
real decode reveals a missing library. So the frozen binary is run through a
real transcription before anything is published (tests/test_frozen.py).

Run from the add-on root:

    pyinstaller pyinstaller.spec --noconfirm
"""

# ruff: noqa: F821  # PyInstaller injects Analysis/PYZ/EXE/COLLECT at runtime.

from __future__ import annotations

import importlib.util
import os
import platform
import shutil
import subprocess
import sys
import sysconfig
from importlib.metadata import PackageNotFoundError, distribution, version
from pathlib import Path

from packaging.requirements import Requirement
from PyInstaller.utils.hooks import collect_submodules, copy_metadata

SPEC_ROOT = Path(SPECPATH).resolve()


# ---------------------------------------------------------------------------
# Fail the build, loudly, if the engine is not installed. Otherwise PyInstaller
# freezes a binary whose import guard reports `hello_error` on every machine.
# ---------------------------------------------------------------------------
_REQUIRED = ('fermion', 'torch', 'numpy', 'soundfile', 'huggingface_hub',
             'safetensors', 'certifi', 'platformdirs')
_missing = [name for name in _REQUIRED if importlib.util.find_spec(name) is None]
if _missing:
    raise SystemExit(f'FATAL: required package(s) not installed: {", ".join(_missing)}. '
                     f'Install the add-on with its build extra first.')


# ---------------------------------------------------------------------------
# The Phonon-2 CPU kernels.
#
# fermion loads them with ctypes, by path, after it has read the CPU's features
# and picked the tier. PyInstaller cannot see a ctypes load, so they are listed
# here. The directory ships one file per platform and tier, so only this
# platform's files are collected: the -base variants are the baseline-ISA tier
# and are needed on processors without the native one. The mac arm64 and x86_64
# files are never mixed.
#
# A foreign-format file in the bundle (an ELF .so in the macOS build) would fail
# PyInstaller's binary analysis, so the filter must not be loose.
# ---------------------------------------------------------------------------
_KERNEL_DIR = Path(importlib.util.find_spec('fermion').origin).parent / '_speech' / '_engine_phonon2_cpu'
_KERNEL_DEST = 'fermion/_speech/_engine_phonon2_cpu'


def _platform_tag() -> tuple[str, str]:
    """(file-name tag, file suffix) for this build host's kernel files."""
    machine = platform.machine().lower()
    if sys.platform.startswith('linux'):
        return {'x86_64': 'linux-x86_64', 'aarch64': 'linux-aarch64'}.get(machine, ''), '.so'
    if sys.platform == 'darwin':
        return {'arm64': 'macos-arm64', 'x86_64': 'macos-x86_64'}.get(machine, ''), '.dylib'
    if sys.platform == 'win32':
        return 'windows-x64', '.dll'
    return '', ''


def _kernel_binaries() -> list[tuple[str, str]]:
    tag, suffix = _platform_tag()
    if not tag:
        raise SystemExit(f'FATAL: no Phonon-2 kernel library is published for '
                         f'{sys.platform}/{platform.machine()}.')
    found = sorted(p for p in _KERNEL_DIR.iterdir()
                   if f'-{tag}' in p.name and p.name.endswith(suffix))
    if not found:
        raise SystemExit(f'FATAL: no {tag} kernel library under {_KERNEL_DIR}. '
                         f'Is fermion-research==0.2.11 installed?')
    print(f'bundling {len(found)} Phonon-2 kernel file(s) for {tag}: {", ".join(p.name for p in found)}')
    return [(str(p), _KERNEL_DEST) for p in found]


# ---------------------------------------------------------------------------
# fermion's own CPU engine kernel: `fermion/bin/libphonon_cpu-<platform>`.
#
# This is a second native library, separate from the Phonon-2 kernels above.
# fermion's gate loads it through `binary_path()`, which checks it against the
# `.sha256` pin beside it, and refuses to load the engine when either is
# missing. The gate runs before the model loads, so a missing kernel fails every
# transcription.
# ---------------------------------------------------------------------------
_FERMION_BIN = Path(importlib.util.find_spec('fermion').origin).parent / 'bin'
_FERMION_BIN_TAGS = {('linux', 'x86_64'): 'linux-x86_64', ('linux', 'aarch64'): 'linux-aarch64',
                     ('macos', 'arm64'): 'macos-arm64', ('windows', 'amd64'): 'win-x86_64'}


def _fermion_bin_files() -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """(binaries, datas) for this platform's fermion kernel and its pin."""
    system = {'linux': 'linux', 'darwin': 'macos', 'win32': 'windows'}.get(sys.platform, sys.platform)
    tag = _FERMION_BIN_TAGS.get((system, platform.machine().lower()))
    if tag is None:
        raise SystemExit(f'FATAL: fermion publishes no CPU kernel for {sys.platform}/{platform.machine()}.')
    libs = sorted(p for p in _FERMION_BIN.iterdir()
                  if tag in p.name and p.suffix in ('.so', '.dylib', '.dll'))
    lib_names = {p.name for p in libs}
    pins = sorted(p for p in _FERMION_BIN.iterdir()
                  if p.name.endswith('.sha256') and p.name[:-len('.sha256')] in lib_names)
    if not libs or len(pins) != len(libs):
        raise SystemExit(f'FATAL: the {tag} kernel or its .sha256 pin is missing under {_FERMION_BIN}.')
    print(f'bundling fermion kernel for {tag}: {", ".join(p.name for p in libs)}')
    return ([(str(p), 'fermion/bin') for p in libs],
            [(str(p), 'fermion/bin') for p in pins])


_FERMION_BIN_LIBS, _FERMION_BIN_PINS = _fermion_bin_files()


# ---------------------------------------------------------------------------
# Distribution metadata for the runtime closure.
#
# fermion and several of its dependencies read their own version through
# importlib.metadata, and PyInstaller only copies a dist-info when a hook asks
# for it. The closure is walked from fermion-research with the markers evaluated
# for this build host.
# ---------------------------------------------------------------------------
# setuptools is pulled in by a dependency's metadata but no module of it is used.
_NOT_BUNDLED: set[str] = {'setuptools'}


def _runtime_closure(roots: tuple[str, ...]) -> list[str]:
    seen: dict[str, str] = {}
    todo = list(roots)
    while todo:
        name = todo.pop()
        key = name.lower().replace('_', '-')
        if key in seen or key in _NOT_BUNDLED:
            continue
        try:
            dist = distribution(name)
        except PackageNotFoundError:
            continue
        seen[key] = dist.metadata['Name']
        for text in dist.requires or []:
            req = Requirement(text)
            if req.marker is not None:
                if 'extra' in str(req.marker):
                    continue  # optional extras are not installed, so not bundled
                if not req.marker.evaluate():
                    continue
            todo.append(req.name)
    return sorted(seen.values())


_DISTRIBUTIONS = _runtime_closure(('fermion-research', 'platformdirs', 'certifi'))
print(f'bundling metadata for {len(_DISTRIBUTIONS)} distribution(s): {", ".join(_DISTRIBUTIONS)}')
datas = []
for _name in _DISTRIBUTIONS:
    datas += copy_metadata(_name)
datas.append((str(_KERNEL_DIR / 'SHA256SUMS.txt'), _KERNEL_DEST))  # fermion checks the kernels against these pins
print(f'fermion-research {version("fermion-research")} in the bundle')


# ---------------------------------------------------------------------------
# fermion's runtime modules that it loads by FILE PATH (importlib's
# spec_from_file_location), not by import. PyInstaller never sees them, so the
# frozen binary would fail at the first decode with "No such file or directory:
# .../_engine_phonon2/fermion_container.py". They ship as source files beside
# their package and are read at run time, so they are bundled as data at the
# same relative path. Their siblings import one another by bare name, which
# fermion resolves through sys.modules, so the set is taken whole.
# ---------------------------------------------------------------------------
_FERMION_ROOT = Path(importlib.util.find_spec('fermion').origin).parent
for _rel in ('_speech/_engine_phonon2', '_speech/_engine'):
    for _src in sorted((_FERMION_ROOT / _rel).glob('*.py')):
        datas.append((str(_src), f'fermion/{_rel}'))


a = Analysis(
    [str(SPEC_ROOT / 'phonon2_addon' / '__main__.py')],
    pathex=[str(SPEC_ROOT)],
    binaries=_kernel_binaries() + _FERMION_BIN_LIBS,
    datas=datas + _FERMION_BIN_PINS,
    # fermion's modules are imported by name inside functions (`fermion.cli` for
    # the cache folder, `fermion._speech` for the engine). Collected whole, so a
    # lazy import cannot silently drop one. Their optional third-party imports are
    # reported by PyInstaller, and only what is installed is bundled. Phonon-1's
    # Qwen backend and the language-model loader are not on the Phonon-2 path, and
    # the first of them fails to import during the build with the installed
    # transformers, so they are not collected.
    hiddenimports=collect_submodules(
        'fermion',
        filter=lambda name: not name.startswith(('fermion._vendor.qwen3_asr_backend',
                                                  'fermion._vendor.hf_ternary')),
    ) + ['_cffi_backend'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # MLX: Apple-silicon-only, and the add-on pins the CPU engine.
        'mlx', 'mlx_audio', 'mlx_lm',
        # readline links GPL-3 libreadline (and libtinfo). Nothing here reads a
        # terminal line, so the module is left out rather than shipped.
        'readline',
        # Not used at run time.
        'PySide6', 'PyQt6', 'PyQt5', 'tkinter', '_tkinter', 'matplotlib', 'IPython',
        'pytest', 'setuptools', 'pkg_resources', 'numpy.f2py', 'numpy.distutils',
        'numpy.testing',
    ],
    noarchive=False,
)

# ---------------------------------------------------------------------------
# libexpat check (from addon-whispercpp, made exact).
#
# PyInstaller bundles whichever libexpat the build interpreter resolves. On a
# build host with an OLD expat in /usr/local/lib (1.8.10 here) shadowing the
# distribution's, that stale copy was bundled, and the frozen binary died before
# Python started:
#     Failed to load libpython3.11.so.1.0: undefined symbol: XML_SetReparseDeferralEnabled
# Which XML_* symbols are needed depends on the expat the interpreter was built
# against, so the binaries are asked: every XML_* symbol that libpython or
# pyexpat imports must be exported by the bundled libexpat. If it is not, a
# system copy that exports them all is substituted; if none does, the build fails.
# ---------------------------------------------------------------------------
_EXPAT_CANDIDATES = ('/usr/lib/x86_64-linux-gnu/libexpat.so.1', '/usr/lib64/libexpat.so.1',
                     '/lib/x86_64-linux-gnu/libexpat.so.1')


def _dynamic_symbols(path: str, which: str) -> set[str]:
    """Names of an ELF file's dynamic symbols; `which` is 'defined' or 'undefined'."""
    out = subprocess.run(['nm', '-D', f'--{which}-only', path],
                         capture_output=True, text=True, timeout=60, check=True)
    return {line.split()[-1].split('@')[0] for line in out.stdout.splitlines() if line.strip()}


def _expat_consumers(entries) -> list[str]:
    """libpython and pyexpat: the bundled files that import expat's symbols."""
    paths = [entry[1] for entry in entries
             if Path(entry[0]).name.startswith(('libpython', 'pyexpat'))]
    # The interpreter's own libpython too, in case PyInstaller adds it after Analysis.
    libdir, ldlibrary = sysconfig.get_config_var('LIBDIR'), sysconfig.get_config_var('LDLIBRARY')
    if libdir and ldlibrary and (Path(libdir) / ldlibrary).is_file():
        paths.append(str(Path(libdir) / ldlibrary))
    return paths


def _fix_libexpat(entries):
    if not sys.platform.startswith('linux'):
        return entries
    if shutil.which('nm') is None:
        raise SystemExit('FATAL: nm (binutils) is required to check the bundled libexpat.')
    needed: set[str] = set()
    for path in _expat_consumers(entries):
        needed |= {name for name in _dynamic_symbols(path, 'undefined') if name.startswith('XML_')}
    out, bundled = [], False
    for entry in entries:
        name, src = entry[0], entry[1]
        if not Path(name).name.startswith('libexpat.so'):
            out.append(entry)
            continue
        bundled = True
        missing = needed - _dynamic_symbols(src, 'defined')
        if not missing:
            out.append(entry)
            continue
        replacement = next((cand for cand in _EXPAT_CANDIDATES if os.path.exists(cand)
                            and not needed - _dynamic_symbols(cand, 'defined')), None)
        if replacement is None:
            raise SystemExit(f'FATAL: the bundled libexpat ({src}) lacks {", ".join(sorted(missing))}, '
                             f'which libpython imports, and no system copy exports them. The frozen '
                             f'binary would not start. A stale expat in /usr/local/lib is the usual cause.')
        print(f'WARNING: build host libexpat at {src} lacks {", ".join(sorted(missing))}; '
              f'substituting {replacement}')
        out.append((name, replacement) + tuple(entry[2:]))
    if needed and not bundled:
        print(f'WARNING: libpython imports {len(needed)} expat symbol(s) but no libexpat is bundled; '
              f'the target system\'s copy will be used')
    else:
        print(f'libexpat check: {len(needed)} XML_* symbol(s) imported, all exported by the bundled copy')
    return out


a.binaries = _fix_libexpat(a.binaries)


# ---------------------------------------------------------------------------
# glibc components are the system's to provide. The build host's libmvec
# (LGPL, part of glibc) would shadow the target's own, so it is left out. The
# frozen binary already needs the target's glibc, and libmvec ships with it.
# ---------------------------------------------------------------------------
def _drop_glibc_components(entries):
    if not sys.platform.startswith('linux'):
        return entries
    kept = [entry for entry in entries if not Path(entry[0]).name.startswith('libmvec.so')]
    if len(kept) != len(entries):
        print(f'dropped {len(entries) - len(kept)} glibc component(s) (libmvec)')
    return kept


a.binaries = _drop_glibc_components(a.binaries)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='phonon2-addon',
    debug=False,
    bootloader_ignore_signals=False,
    # strip=False: stripping the torch and BLAS libraries saves little and breaks
    # macOS code signatures.
    strip=False,
    upx=False,
    console=True,  # stdio is the protocol channel
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='phonon2-addon',
)
