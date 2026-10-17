# Phonon-2 (offline, English) add-on for Subtitld

Offline **English speech-to-text** for [Subtitld](https://github.com/Subtitld/subtitld),
powered by Phonon-2 from [Fermion Research](https://fermionresearch.com) through the
`fermion-research` package.

- Serves the `asr.transcribe` task over Subtitld's add-on protocol.
- **English only.** Other languages are rejected with `unsupported_language`. Use
  another speech engine for them.
- Runs on the CPU. No cloud account. Every request is decoded to 16 kHz mono WAV
  with the ffmpeg that Subtitld provides, so the video file itself can be sent.
- Segments come from the engine as sentence- or pause-sized stretches of speech,
  so they need no regrouping.

> **Status: Linux x86-64 only.** The frozen build targets glibc 2.28 and newer.
> macOS and Windows builds, the release workflow and the catalog entry are not
> done yet. See [Not yet](#not-yet).

## Model

The model is not in the archive. It downloads once, on first use, from
[FermionResearch/Phonon-2](https://huggingface.co/FermionResearch/Phonon-2) on
Hugging Face, about 164 MB. `fermion-research` checks the download against a
sha256 pinned in the package. After that, transcription is fully offline.

### Where the model is stored

The model lives in Subtitld's shared model cache, never inside the add-on
folder, because Subtitld replaces that folder on every add-on update. The first
match wins:

1. The **Models folder** option (`PHONON2_MODELS_DIR`).
2. `$SUBTITLD_MODELS_DIR/phonon2`, when the host exports it.
3. `<user cache>/subtitld/models/phonon2`:
   - Linux: `~/.cache/subtitld/models/phonon2`
   - macOS: `~/Library/Caches/subtitld/models/phonon2`
   - Windows: `%LOCALAPPDATA%\subtitld\subtitld\Cache\models\phonon2`

The add-on sets `FERMION_CACHE_DIR` to that folder before the model loads, so the
weights go there, not into `~/.cache/fermion`. Uninstalling the add-on does not
delete the model.

## Options

Set these in **Settings ▸ Add-ons ▸ Phonon-2 (offline, English) ▸ Configure**.

| Option | Default | Env var | Notes |
|--------|---------|---------|-------|
| Models folder | *(empty)* | `PHONON2_MODELS_DIR` | Empty = shared cache. |
| CPU threads | `0` | `PHONON2_THREADS` | `0` = auto. Applies when the model loads. |
| Hotwords | *(empty)* | `PHONON2_HOTWORDS` | Names and terms to favour, comma-separated, up to 25. The audio still decides. |

Each request resolves an option in this order: the request's own `options`,
then the env var, then the default.

| Variable | Purpose |
|----------|---------|
| `SUBTITLD_FFMPEG_EXECUTABLE`, `FFMPEG_EXECUTABLE` | ffmpeg to use, checked in that order before `PATH`. |
| `SUBTITLD_TEMP_DIR` | Where the decoded WAV goes (default: system temp). It is always deleted. |
| `SUBTITLD_MODELS_DIR` | Host model cache root (see above). |
| `PHONON2_ADDON_LOG_LEVEL` | Default `INFO`. |
| `SSL_CERT_FILE`, `SSL_CERT_DIR` | CA certificates for the model download, instead of the system's (see below). |

## Behaviour notes

- **The model stays loaded** between requests, so only the first request pays
  the load time, and the first one may also download the model. Changing the
  models folder or the thread count takes effect when the model next loads.
- **Decoding** runs first for every request, through ffmpeg. Audio shorter than
  0.1 s is answered with no segments, without calling the engine.
- **Cancel** is honoured before decoding, after decoding, and after the engine
  returns. Phonon-2 decodes a whole file in one call and cannot be interrupted
  part way, so a cancel during a long file waits for that decode to finish. A
  cancelled request never returns a result.
- **Downloads** use HTTPS. The frozen Linux build carries AlmaLinux's OpenSSL,
  which looks for CA certificates in `/etc/pki/tls`, a folder most distributions
  do not have. So at startup it trusts the CA bundle the running distribution
  keeps (`/etc/ssl/certs/ca-certificates.crt`, `/etc/pki/tls/certs/ca-bundle.crt`
  and the other usual places), or certifi's bundle when it finds none. Set
  `SSL_CERT_FILE` or `SSL_CERT_DIR` to use another, for instance behind a proxy
  that inspects HTTPS. macOS and Windows use the native trust store.
- **Language:** a tag like `en-us` or `en_GB` is accepted. `auto` and blank are
  accepted too, but Phonon-2 decodes English whatever the audio is.
- **Errors** use Subtitld's protocol codes: `model_missing` (the model could not
  be downloaded or loaded), `disk_full`, `oom`, `unsupported_language`,
  `bad_params`, `cancelled` and `internal` (including ffmpeg failures).

## Requirements

- **ffmpeg**, to decode the request's audio. Subtitld provides its own copy. Outside
  Subtitld, put ffmpeg on `PATH`. It is not in the archive.
- Disk space for the model, about 164 MB.
- Memory use is not measured yet.

## Development

Use a dedicated virtual environment, so the engine's dependencies do not reach
another project's environment.

```bash
python3.11 -m venv .venv && . .venv/bin/activate
pip install --no-deps torch --index-url https://download.pytorch.org/whl/cpu   # Linux only, the CPU build
pip install -e '.[dev]'
pytest -q                                                    # no model and no torch needed
PHONON2_TEST_TRANSCRIPTION=1 pytest -q -m transcription      # + real inference (downloads the model once)
```

`pytest -q` runs the protocol, handler, engine-adapter, decoder and manifest
tests. The protocol tests use a fake engine and a stub decoder, so they need
neither torch nor the network. The decoder tests use ffmpeg when it is installed.
The real-inference test transcribes `tests/data/jfk.flac` and checks for the word
"country". Its models go to `PHONON2_TEST_MODELS_DIR`, or to a temporary folder.

### Testing the frozen executable

`tests/test_frozen.py` runs a built executable the way Subtitld does: over stdio,
with a scrubbed Python environment, from outside the repository.

```bash
PHONON2_TEST_BINARY="$PWD/dist/phonon2-addon/phonon2-addon" \
PHONON2_TEST_TRANSCRIPTION=1 \
PHONON2_TEST_MODELS_DIR="$HOME/.cache/phonon2-test-models" \
  pytest -q tests/test_frozen.py
```

The path must be absolute. Without `PHONON2_TEST_BINARY` every frozen test is
skipped.

## Building the frozen binary

```bash
tools/build_linux.sh
```

This runs in an AlmaLinux 8 container (glibc 2.28), as your user, and writes
`phonon2-<version>-linux-x86_64.zip` and its `.sha256` into the repository. The
container image is `tools/linux-build.Dockerfile`, built on first use. The script
builds the bundle with `pyinstaller.spec`, assembles `THIRD_PARTY_LICENSES/` with
`tools/fetch_third_party_licenses.py`, and zips the result with
`tools/make_archive.py`. It also writes the libquadmath and libsndfile source
tarballs (`tools/build_libquadmath_source.py`, `tools/build_libsndfile_source.py`),
after checking that the bundled copies are the builds those sources match. It
deletes any older outputs first, so a failed build never leaves one that looks
current. pip's cache and the downloaded sources are kept under `~/.cache` between
builds.

**Why AlmaLinux 8.** The bundle carries the build interpreter's `libpython`, and
an interpreter built against a newer glibc will not start on an older one. The
host build failed in a clean Ubuntu 22.04 container (glibc 2.35) with `GLIBC_2.38
not found`, because the host's glibc was 2.43. glibc 2.28 is the floor fermion
requires for its own Linux binaries. The image is `manylinux_2_28`, which is
AlmaLinux 8, but its `/opt/python` interpreters are statically linked, and
PyInstaller needs a shared `libpython`. So the build uses the distribution's
Python 3.11 (`/usr/bin/python3.11`). The image's `/usr/local/bin/python3.11`
comes first on `PATH` and is one of the static ones, so it is named explicitly.

On AlmaLinux 8, Python links OpenSSL 1.1.1k, which AlmaLinux maintains for the
life of the release. Its licence is the OpenSSL and SSLeay pair, not OpenSSL 3's
Apache-2.0, and the licence tool has a rule for each.

### Testing the archive

```bash
tools/test_archive.sh ubuntu:22.04
PHONON2_TEST_MODELS_DIR=~/.cache/phonon2-test-models tools/test_archive.sh debian:buster
```

This unpacks the archive the way Subtitld's installer does, in a clean container,
and runs `tests/test_frozen.py` against the unpacked executable. The container
gets only ffmpeg, Python and pytest, from the image's own package manager
(apt, dnf or pacman). With `PHONON2_TEST_MODELS_DIR` the model is copied in;
without it, the frozen binary downloads it, which also tests the download.

**Archive layout** (the same layout as the whisper.cpp add-on):

```
manifest.json                 executable: bin/phonon2-addon
LICENSE                       Apache-2.0
NOTICE                        attribution for the model weights and the Parakeet base
README.md
THIRD_PARTY_LICENSES/         licences of everything in bin/ (see INDEX.txt)
bin/phonon2-addon             PyInstaller onedir launcher (+ bin/_internal/)
```

The bundle is about 950 MB unpacked and 340 MB zipped. torch is 645 MB of that;
transformers and scipy, with scipy's libraries, add about 125 MB.
Subtitld's installer restores the executable bit after extraction.

**What the spec includes, and why:**

- **The Phonon-2 kernels** for this platform (`fermion/_speech/_engine_phonon2_cpu`),
  loaded by ctypes, which PyInstaller cannot see.
- **fermion's CPU kernel** (`fermion/bin/libphonon_cpu-*`) and its `.sha256` pin.
  fermion's availability gate checks both before the model loads.
- **fermion's modules loaded by file path** (`_speech/_engine_phonon2` and
  `_speech/_engine`), bundled as data at their own paths.
- **transformers and scipy.** fermion's CPU gate refuses to load the engine unless
  both are importable, even though the Parakeet graph is then the upstream one.
  Leaving them out made every transcription fail at that gate.
- **Excluded:** `readline` (it pulls in GPL-3 libreadline, which nothing here
  needs), and glibc's `libmvec` (the target system provides its own).
- **libexpat:** a build host with an old expat in `/usr/local/lib` would bundle
  a copy that lacks a symbol libpython needs. The spec substitutes a known-good
  system copy, or fails the build.

**Licences.** `tools/fetch_third_party_licenses.py` fails the build when a shared
library has no licence rule, and when a bundled distribution has no licence file
and no committed text. It writes `INDEX.txt`, which lists every bundled library
and the notice that covers it. The committed texts are under `THIRD_PARTY_LICENSES/`
in this repository.

## Releasing

A release is built and tested locally, then published by hand. There is no CI
release workflow yet.

1. Set the version in `manifest.json`, `pyproject.toml` and
   `phonon2_addon/__init__.py`. `tests/test_manifest.py` fails if they disagree.
2. `tools/build_linux.sh` writes the archive and the two LGPL source tarballs,
   each with a `.sha256`. It fails when a bundled libquadmath or libsndfile is
   not a build whose source it can attach.
3. `tools/test_archive.sh` against Ubuntu 22.04 (with no cached model, so the
   download is tested), Debian 10, Fedora and Arch.
4. Tag `v<version>`, push, and create the GitHub release with these assets and
   their `.sha256` sidecars:
   - `phonon2-<version>-linux-x86_64.zip`, the add-on;
   - `phonon2-addon-<version>-source.tar.gz`, the add-on's own source (`git archive`);
   - `phonon2-addon-<version>-libquadmath-source.tar.gz` and
     `phonon2-addon-<version>-libsndfile-source.tar.gz`, the LGPL sources.
5. Subtitld finds it through the `phonon2` entry in `Subtitld/addons-catalog`.
   The catalog rebuilds when `addons.yml` changes, and every six hours.

## Not yet

- **macOS and Windows builds.** The spec already selects each platform's kernel
  files, but neither build has been run, and neither has been tested on a real
  machine. `manifest.json` lists Linux only until they are.
- **A CI release workflow.** Releases are built locally (see Releasing).
- **Cancel inside a decode.** This needs the engine's windows decoded one at a
  time, which fermion's public API does not expose.
- **Size.** torch is most of the bundle. A smaller engine build would need
  fermion's own packaging to change.
- **Memory and speed on real hardware.** The figures on fermionresearch.com are
  the vendor's.

## Licence

The add-on code is Apache-2.0 (see [LICENSE](LICENSE)). The Phonon engine is
Apache-2.0. The Phonon-2 weights are CC-BY-4.0 and derived from NVIDIA's Parakeet
TDT 0.6B v3, also CC-BY-4.0. [NOTICE](NOTICE) carries the attribution. The
bundled third-party components are listed in `THIRD_PARTY_LICENSES/INDEX.txt`
in the archive.

The archive carries LGPL code in two libraries: libquadmath (two builds, from
numpy's and scipy's OpenBLAS), and libsndfile, which has LAME and mpg123 linked
into it. Their complete corresponding source is attached to every release, from the same page
as the archive: `phonon2-addon-<version>-libquadmath-source.tar.gz` and
`phonon2-addon-<version>-libsndfile-source.tar.gz`. Each holds a README that says
which build it is and how to rebuild it.
