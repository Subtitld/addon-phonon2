"""Decode Subtitld's audio into the 16 kHz mono WAV that Phonon-2 reads.

The host does not always send a WAV. The import panel passes the separated
vocals, a cached FLAC, or the video file itself, so every request is decoded
here with the ffmpeg that Subtitld provides. The engine then never has to
resample.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import wave
from pathlib import Path
from typing import Callable

from phonon2_addon.protocol import ERR_DISK_FULL, ERR_INTERNAL, Cancelled, RequestError

SAMPLE_RATE = 16000
# Shorter than this there is nothing for the engine to decode.
MIN_SECONDS = 0.1
_POLL_SECONDS = 0.25


def find_ffmpeg() -> str | None:
    for candidate in (os.environ.get('SUBTITLD_FFMPEG_EXECUTABLE', '').strip(),
                      os.environ.get('FFMPEG_EXECUTABLE', '').strip()):
        if candidate and (os.path.isfile(candidate) or shutil.which(candidate)):
            return candidate
    return shutil.which('ffmpeg')


def _child_env() -> dict | None:
    """Environment for ffmpeg, or None to inherit ours.

    The frozen Linux bootloader puts the bundle's own libraries first on
    LD_LIBRARY_PATH. A system ffmpeg that inherits that loads the build
    machine's libstdc++ and fails to start on a newer distribution.
    PyInstaller saves the host's value in LD_LIBRARY_PATH_ORIG; restore it.
    """
    if not (getattr(sys, 'frozen', False) and sys.platform.startswith('linux')):
        return None
    env = dict(os.environ)
    original = env.get('LD_LIBRARY_PATH_ORIG')
    if original is not None:
        env['LD_LIBRARY_PATH'] = original
    else:
        env.pop('LD_LIBRARY_PATH', None)
    return env


def _no_window_startupinfo():
    """Keep ffmpeg from opening a console window on Windows GUI hosts."""
    if sys.platform != 'win32':
        return None
    info = subprocess.STARTUPINFO()  # type: ignore[attr-defined]
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW  # type: ignore[attr-defined]
    info.wShowWindow = subprocess.SW_HIDE  # type: ignore[attr-defined]
    return info


def wav_seconds(path: Path) -> float:
    with wave.open(str(path), 'rb') as w:
        return w.getnframes() / float(w.getframerate())


def to_wav(source, dest: Path, cancelled: Callable[[], bool]) -> float:
    """Decode `source` (any media ffmpeg reads) to `dest`, 16 kHz mono 16-bit.

    Returns the duration in seconds. Raises `Cancelled` when `cancelled()`
    turns true, and `RequestError` when ffmpeg is missing or fails.
    """
    if cancelled():
        raise Cancelled()
    ffmpeg = find_ffmpeg()
    if ffmpeg is None:
        raise RequestError(ERR_INTERNAL,
                           'ffmpeg not found. Subtitld provides one; '
                           'set SUBTITLD_FFMPEG_EXECUTABLE to use another.')
    # -nostdin and stdin=DEVNULL: ffmpeg polls its stdin for keystrokes.
    cmd = [ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
           '-i', str(source), '-vn', '-sn', '-dn',
           '-ac', '1', '-ar', str(SAMPLE_RATE), '-c:a', 'pcm_s16le', '-f', 'wav', str(dest)]
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE, env=_child_env(),
                            startupinfo=_no_window_startupinfo())
    while True:
        try:
            _out, stderr = proc.communicate(timeout=_POLL_SECONDS)
            break
        except subprocess.TimeoutExpired:
            if cancelled():
                proc.kill()
                proc.communicate()
                raise Cancelled() from None
    if cancelled():
        raise Cancelled()
    if proc.returncode != 0 or not dest.is_file() or dest.stat().st_size <= 44:
        tail = ' / '.join((stderr or b'').decode('utf-8', 'replace').strip().splitlines()[-5:])
        if 'No space left' in tail:
            raise RequestError(ERR_DISK_FULL, f'Out of disk space while decoding the audio: {tail}')
        raise RequestError(ERR_INTERNAL,
                           f'Could not decode the audio with ffmpeg (exit {proc.returncode}): '
                           f'{tail or "no output"}')
    return wav_seconds(dest)
