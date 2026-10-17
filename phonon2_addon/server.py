"""The frame loop: handshake, then one worker thread per transcription.

The host sends `asr.transcribe` requests. Each one is answered with `progress`
frames and then exactly one `result` or `error`. Workers run on their own
threads, so a `cancel` frame is read while a decode is still going.
"""

from __future__ import annotations

import logging
import os
import platform
import sys
import tempfile
import threading
import time
from pathlib import Path

from phonon2_addon import __version__, audio, engine, protocol, tls
from phonon2_addon.protocol import (
    ERR_BAD_PARAMS, ERR_CANCELLED, ERR_INTERNAL, ERR_UNSUPPORTED_LANGUAGE, Cancelled, RequestError,
)

ADDON_ID = 'phonon2'
TASK_TRANSCRIBE = 'asr.transcribe'

log = logging.getLogger('phonon2_addon')


class _Request:
    def __init__(self, req_id: str):
        self.req_id = req_id
        self.cancel = threading.Event()
        self.thread: threading.Thread | None = None


_active_lock = threading.Lock()
_active: dict[str, _Request] = {}


def capabilities() -> list[dict]:
    """What the hello frame advertises. The languages must match manifest.json."""
    return [{
        'task': TASK_TRANSCRIBE,
        'languages': list(engine.SUPPORTED_LANGUAGES),
        # Batch transcription only: the engine decodes the file as one request.
        'streaming': False,
        'voice_clone': False,
    }]


def _env(name: str) -> str:
    return (os.environ.get(name) or '').strip()


def _option(options: dict, key: str, env_name: str, default):
    """Request option, then env var, then default. None and blank strings are unset."""
    value = options.get(key)
    if isinstance(value, str):
        value = value.strip()
    if value not in (None, ''):
        return value
    return _env(env_name) or default


def _check_language(raw) -> None:
    """Reject a language Phonon-2 cannot transcribe. Blank and 'auto' are accepted.

    The host sends tags like `en-us`; only the primary subtag is compared.
    Auto-detect is accepted, but Phonon-2 decodes English whatever the audio is.
    """
    tag = str(raw or '').strip().lower().replace('_', '-')
    if tag in ('', 'auto', 'und', 'mul'):
        return
    primary = tag.split('-', 1)[0]
    if primary not in engine.SUPPORTED_LANGUAGES:
        raise RequestError(ERR_UNSUPPORTED_LANGUAGE,
                           f'Phonon-2 transcribes English only, not {raw!r}. '
                           'Choose another speech engine for this language.')


def _parse_hotwords(raw) -> list[str]:
    items = raw if isinstance(raw, list) else str(raw or '').replace('\n', ',').split(',')
    words: list[str] = []
    for item in items:
        word = str(item).strip()
        if word and word not in words:
            words.append(word)
    if len(words) > engine.MAX_HOTWORDS:
        log.warning('Phonon-2 favours at most %d hotwords; ignoring %d',
                    engine.MAX_HOTWORDS, len(words) - engine.MAX_HOTWORDS)
        words = words[:engine.MAX_HOTWORDS]
    return words


def _resolve_settings(options: dict) -> dict:
    raw_dir = _option(options, 'models_dir', 'PHONON2_MODELS_DIR', '')
    models_dir = Path(os.path.expanduser(str(raw_dir))) if raw_dir else engine.default_models_dir()
    # The Configure dialog's `file` field opens a file picker. A file inside the
    # folder is accepted as the folder.
    if models_dir.is_file():
        models_dir = models_dir.parent
    try:
        threads = int(str(_option(options, 'threads', 'PHONON2_THREADS', 0)).strip() or 0)
    except ValueError:
        log.warning('ignoring non-numeric threads value')
        threads = 0
    return {
        'models_dir': models_dir,
        'threads': max(0, min(threads, 256)),
        'hotwords': _parse_hotwords(_option(options, 'hotwords', 'PHONON2_HOTWORDS', '')),
    }


def _temp_root() -> str:
    raw = (os.environ.get('SUBTITLD_TEMP_DIR') or '').strip()
    return raw if raw and os.path.isdir(raw) else tempfile.gettempdir()


def _handle_transcribe(state: _Request, params: dict) -> None:
    req_id = state.req_id
    options = params.get('options') if isinstance(params.get('options'), dict) else {}
    audio_path = params.get('audio_path')
    if not isinstance(audio_path, str) or not audio_path.strip():
        protocol.error(req_id, ERR_BAD_PARAMS, 'audio_path is required')
        return
    if not os.path.isfile(audio_path):
        protocol.error(req_id, ERR_BAD_PARAMS, f'audio_path is not a file: {audio_path!r}')
        return

    try:
        _check_language(params.get('language'))
        settings = _resolve_settings(options)
        if state.cancel.is_set():
            raise Cancelled()

        def on_stage(value: float, message: str) -> None:
            protocol.progress(req_id, value, message)

        # The decoded WAV lives in a temporary folder that is removed before
        # the terminal frame, so a host that sees `result` or `error` never
        # finds it still on disk.
        with tempfile.TemporaryDirectory(prefix='phonon2-', dir=_temp_root()) as workdir:
            protocol.progress(req_id, 0.0, 'Decoding audio...')
            wav = Path(workdir) / 'audio.16k.wav'
            seconds = audio.to_wav(audio_path, wav, state.cancel.is_set)
            if state.cancel.is_set():
                raise Cancelled()
            protocol.progress(req_id, 0.05, 'Decoded audio')

            if seconds < audio.MIN_SECONDS:
                segments, audio_seconds = [], seconds
            else:
                segments, audio_seconds = engine.transcribe(
                    wav, models_dir=settings['models_dir'], threads=settings['threads'],
                    hotwords=settings['hotwords'], on_stage=on_stage)
            # Phonon-2 decodes the whole file in one call and offers no abort, so
            # a cancel is honoured here, before the result is sent. A cancelled
            # request never returns a result.
            if state.cancel.is_set():
                raise Cancelled()
    except Cancelled:
        log.info('transcribe %s: cancelled', req_id)
        protocol.error(req_id, ERR_CANCELLED, 'cancelled')
        return
    except RequestError as exc:
        log.warning('transcribe %s failed: [%s] %s', req_id, exc.code, exc.message)
        protocol.error(req_id, exc.code, exc.message, exc.retryable)
        return
    except Exception as exc:  # noqa: BLE001 — every request ends in one terminal frame
        log.exception('transcribe %s failed', req_id)
        protocol.error(req_id, ERR_INTERNAL, f'transcription failed: {exc}')
        return

    protocol.progress(req_id, 1.0, '')
    protocol.result(req_id, {
        'segments': segments,
        'duration_sec': round(audio_seconds, 3),
        'model': engine.MODEL_ID,
    })


def _spawn(req_id: str, target, *args) -> None:
    state = _Request(req_id)
    with _active_lock:
        _active[req_id] = state

    def run() -> None:
        try:
            target(state, *args)
        except BaseException:  # noqa: BLE001
            log.exception('worker crashed')
            protocol.error(req_id, ERR_INTERNAL, 'worker crashed; see the add-on log')
        finally:
            with _active_lock:
                _active.pop(req_id, None)

    state.thread = threading.Thread(target=run, name=f'phonon2-{req_id}', daemon=True)
    state.thread.start()


def _main_loop() -> int:
    while True:
        frame = protocol.read_frame()
        if frame is None:
            log.info('stdin closed; exiting')
            return 0
        if not frame:
            continue
        ftype = frame.get('type')

        if ftype == 'shutdown':
            log.info('shutdown requested')
            return 0
        if ftype == 'ready':
            log.info('handshake complete; host=%s host_version=%s',
                     frame.get('host'), frame.get('host_version'))
            continue
        if ftype == 'cancel':
            target = frame.get('target')
            with _active_lock:
                state = _active.get(target) if isinstance(target, str) else None
            if state is not None:
                log.info('cancel %s', target)
                state.cancel.set()
            continue

        req_id = frame.get('id')
        if not isinstance(req_id, str) or not req_id:
            log.warning('ignoring frame %r without an id', ftype)
            continue
        if ftype == TASK_TRANSCRIBE:
            params = frame.get('params', {})
            if not isinstance(params, dict):
                protocol.error(req_id, ERR_BAD_PARAMS, 'params must be an object')
                continue
            _spawn(req_id, _handle_transcribe, params)
            continue
        # An unknown task still gets an answer, so the host's request does not
        # sit until its timeout.
        protocol.error(req_id, ERR_BAD_PARAMS, f'unsupported frame type {ftype!r}')


def _stop_workers(budget_sec: float = 3.5) -> None:
    """Cancel the running requests and wait a little for them. The host allows
    about 5 s between `shutdown` and SIGTERM, so this stays under that."""
    with _active_lock:
        states = list(_active.values())
    for state in states:
        state.cancel.set()
    deadline = time.monotonic() + budget_sec
    for state in states:
        if state.thread is not None:
            state.thread.join(timeout=max(0.0, deadline - time.monotonic()))


def main() -> int:
    protocol.claim_stdio()
    logging.basicConfig(
        level=(_env('PHONON2_ADDON_LOG_LEVEL') or 'INFO').upper(),
        format='%(asctime)s %(levelname)s %(name)s: %(message)s',
        stream=sys.stderr,
    )
    log.info('phonon2-addon %s on %s %s, python %s',
             __version__, platform.system(), platform.machine(), platform.python_version())
    ca_bundle = tls.configure_ca_bundle()
    if ca_bundle:
        log.info('model download trusts the CA bundle at %s', ca_bundle)

    problem = engine.runtime_error()
    if problem is not None:
        log.error(problem)
        protocol.hello_error(problem)
        return 1

    protocol.hello(ADDON_ID, __version__, capabilities())
    rc = 1
    try:
        rc = _main_loop()
    except KeyboardInterrupt:
        rc = 130
    except Exception:  # noqa: BLE001
        log.exception('main loop crashed')
        rc = 1
    finally:
        _stop_workers()
    return rc
