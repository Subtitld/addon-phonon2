"""JSON-line frames on the add-on's own stdin and stdout.

One JSON object per line. The host owns the real stdin and stdout of this
process: `claim_stdio` moves them to private descriptors, so nothing else can
read a host frame or write into the frame stream. Not a stray print(), not a
native library, not a child process that inherits fd 1. Logging goes to stderr.

Frame shapes follow `subtitld/modules/addons/protocol.py` in the Subtitld repo.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading

PROTOCOL_VERSION = 1

# Mirrors subtitld.modules.addons.protocol.ErrorCode. Inlined so the frozen
# binary takes no runtime dependency on the host package.
ERR_MODEL_MISSING = 'model_missing'
ERR_UNSUPPORTED_LANGUAGE = 'unsupported_language'
ERR_BAD_PARAMS = 'bad_params'
ERR_INTERNAL = 'internal'
ERR_CANCELLED = 'cancelled'
ERR_OOM = 'oom'
ERR_DISK_FULL = 'disk_full'

log = logging.getLogger('phonon2_addon.protocol')


class RequestError(Exception):
    """A failure that already knows its protocol error code."""

    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class Cancelled(Exception):
    """Raised between the steps of a request once its cancel flag is set."""


_reader = None
_writer = None
_write_lock = threading.Lock()


def claim_stdio() -> None:
    """Give the protocol exclusive use of the real stdin and stdout."""
    try:
        sys.stdout.flush()
        in_fd = os.dup(0)
        out_fd = os.dup(1)
        devnull = os.open(os.devnull, os.O_RDONLY)
        try:
            os.dup2(devnull, 0)
        finally:
            os.close(devnull)
        os.dup2(2, 1)
        use_streams(os.fdopen(in_fd, 'rb'), os.fdopen(out_fd, 'wb'))
    except (OSError, ValueError, AttributeError):
        use_streams(sys.stdin.buffer, sys.stdout.buffer)
    sys.stdin = open(os.devnull, encoding='utf-8')  # a stray input() ends with EOFError
    sys.stdout = sys.stderr                        # a stray print() goes to the log


def use_streams(reader, writer) -> None:
    """Send and read frames on these binary streams. Tests use this as well."""
    global _reader, _writer
    _reader = reader
    _writer = writer


def emit(frame: dict) -> None:
    data = (json.dumps(frame, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')
    out = _writer if _writer is not None else sys.stdout.buffer
    with _write_lock:
        try:
            out.write(data)
            out.flush()
        except (BrokenPipeError, OSError, ValueError):
            pass  # the host went away; the read loop ends on its next empty read


def read_frame() -> dict | None:
    """The next frame. `{}` for a line that is not one; None at end of input."""
    source = _reader if _reader is not None else sys.stdin.buffer
    line = source.readline()
    if not line:
        return None
    text = line.decode('utf-8', errors='replace').strip().lstrip('﻿')
    if not text:
        return {}
    try:
        frame = json.loads(text)
    except json.JSONDecodeError as exc:
        log.warning('malformed frame %r: %s', line[:200], exc)
        return {}
    return frame if isinstance(frame, dict) else {}


def hello(addon_id: str, version: str, capabilities: list) -> None:
    emit({'type': 'hello', 'protocol': PROTOCOL_VERSION, 'addon': addon_id,
          'version': version, 'capabilities': capabilities})


def hello_error(message: str, code: str = ERR_INTERNAL) -> None:
    emit({'type': 'hello_error', 'code': code, 'message': message})


def progress(req_id: str, value: float, message: str = '') -> None:
    emit({'id': req_id, 'type': 'progress',
          'value': max(0.0, min(1.0, float(value))), 'message': message})


def result(req_id: str, data: dict) -> None:
    emit({'id': req_id, 'type': 'result', 'data': data})


def error(req_id: str, code: str, message: str, retryable: bool = False) -> None:
    emit({'id': req_id, 'type': 'error', 'code': code,
          'message': message, 'retryable': retryable})
