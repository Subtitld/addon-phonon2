"""Phonon-2, loaded once and kept for the life of the process.

fermion-research loads the engine and the weights. It runs the CPU engine,
downloads the weights from Hugging Face on first use, and checks them against
a sha256 pinned in the package. This module is the only place that imports
fermion, and it does so lazily: a broken install is reported in the handshake
instead of killing the process before it can answer.

Two environment variables are set for fermion, and both are read when the
model loads: FERMION_CACHE_DIR, so the weights land in Subtitld's models
folder rather than the user's home cache, and FERMION_CPU_THREADS.
"""

from __future__ import annotations

import errno
import logging
import os
import threading
from pathlib import Path

from phonon2_addon.protocol import ERR_DISK_FULL, ERR_MODEL_MISSING, ERR_OOM, RequestError

log = logging.getLogger('phonon2_addon.engine')

MODEL_ID = 'phonon-2'
# Phonon-2 is an English model. These are the primary subtags it accepts.
SUPPORTED_LANGUAGES = ('en',)
# Phonon-2 favours at most this many hotwords per call (fermion's docstring).
MAX_HOTWORDS = 25

_lock = threading.Lock()  # fermion's engine is not re-entrant: load and decode share it
_speech = None


def runtime_error() -> str | None:
    """Why the engine cannot be used on this machine, or None when it can.

    Importing fermion is light (torch is loaded with the model), so this is a
    cheap check that the package installed and imports.
    """
    try:
        import fermion
    except Exception as exc:  # noqa: BLE001 — every import failure is reported to the host
        return f'fermion-research could not be imported: {exc}'
    if not callable(getattr(fermion, 'load_speech', None)):
        return 'fermion-research does not provide load_speech(); it must be fermion-research==0.2.11'
    return None


def default_models_dir() -> Path:
    """`$SUBTITLD_MODELS_DIR/phonon2` when the host exports it, else Subtitld's cache folder."""
    base = (os.environ.get('SUBTITLD_MODELS_DIR') or '').strip()
    if base:
        return Path(os.path.expanduser(base)) / 'phonon2'
    import platformdirs
    return Path(platformdirs.user_cache_dir('subtitld')) / 'models' / 'phonon2'


def transcribe(audio_path, *, models_dir: Path, threads: int, hotwords: list[str], on_stage):
    """(segments, audio_seconds) for one audio file.

    `segments` is `[{start, end, text}, ...]` in seconds from the start of the
    file, one per sentence- or pause-sized stretch of speech. `on_stage(value,
    message)` reports the load and the decode as progress frames.

    The model load (which may download about 164 MB) and the decode share one
    lock, so requests run one at a time through this function.
    """
    global _speech
    with _lock:
        if _speech is None:
            on_stage(0.05, f'Loading {MODEL_ID}...')
            _speech = _load(models_dir, threads)
        on_stage(0.5, 'Transcribing...')
        try:
            transcript = _speech.transcribe_detailed(str(audio_path), hotwords=hotwords)
        except MemoryError as exc:
            raise RequestError(ERR_OOM, 'Not enough memory to transcribe this file.') from exc

    segments = []
    for seg in transcript.segments:
        text = str(seg.get('text', '')).strip()
        if text:
            segments.append({'start': round(float(seg['start']), 3),
                             'end': round(float(seg['end']), 3),
                             'text': text})
    return segments, float(transcript.audio_seconds)


def _load(models_dir: Path, threads: int):
    # fermion reads these when it resolves the model cache, the engine and the
    # thread pool, so they are set before the load and not after.
    os.environ['FERMION_CACHE_DIR'] = str(models_dir)
    # This add-on is the CPU engine. Without this, Apple silicon picks MLX, which
    # the bundle does not ship.
    os.environ.setdefault('FERMION_DEVICE', 'cpu')
    if threads:
        os.environ['FERMION_CPU_THREADS'] = str(threads)
    log.info('loading %s (cache %s, threads %s)', MODEL_ID, models_dir, threads or 'auto')
    try:
        from fermion import load_speech
        return load_speech(MODEL_ID, quiet=True)
    except MemoryError as exc:
        raise RequestError(ERR_OOM, 'Not enough memory to load Phonon-2.') from exc
    except OSError as exc:
        if exc.errno == errno.ENOSPC:
            raise RequestError(ERR_DISK_FULL,
                               f'Out of disk space while loading Phonon-2 into {models_dir}: {exc}') from exc
        raise RequestError(ERR_MODEL_MISSING, f'Phonon-2 could not be loaded: {exc}') from exc
    except Exception as exc:  # noqa: BLE001 — a load failure is one error for the user
        raise RequestError(ERR_MODEL_MISSING, f'Phonon-2 could not be loaded: {exc}') from exc
