"""The frozen PyInstaller binary, driven the way Subtitld drives it: over stdio.

Set PHONON2_TEST_BINARY to the ABSOLUTE path of the executable (the
`bin/phonon2-addon` of an unpacked archive, or `dist/phonon2-addon/phonon2-addon`
straight out of PyInstaller). Without it every test here is skipped.

The environment is scrubbed of Python variables and the test's working
directory is outside the repository, so the binary cannot be using this
checkout's interpreter or packages. Only ffmpeg, which Subtitld provides, is
found through the PATH.

Transcription needs the model: set PHONON2_TEST_TRANSCRIPTION=1 to download it
once into PHONON2_TEST_MODELS_DIR (or a temporary folder).
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JFK = ROOT / 'tests' / 'data' / 'jfk.flac'
BINARY = os.environ.get('PHONON2_TEST_BINARY') or None
TRANSCRIBE = os.environ.get('PHONON2_TEST_TRANSCRIPTION') == '1'

needs_binary = pytest.mark.skipif(BINARY is None, reason='set PHONON2_TEST_BINARY to the frozen executable')
needs_model = pytest.mark.skipif(
    BINARY is None or not TRANSCRIBE or shutil.which('ffmpeg') is None,
    reason='set PHONON2_TEST_TRANSCRIPTION=1 (downloads the model) and have ffmpeg on PATH')


class Frozen:
    def __init__(self, workdir: Path, models_dir: Path):
        assert BINARY is not None and Path(BINARY).is_absolute(), 'PHONON2_TEST_BINARY must be absolute'
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(('PYTHON', 'VIRTUAL_ENV', 'CONDA'))}
        env.pop('LD_LIBRARY_PATH', None)
        env['PHONON2_MODELS_DIR'] = str(models_dir)
        env['PHONON2_ADDON_LOG_LEVEL'] = 'INFO'
        self.proc = subprocess.Popen([BINARY], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, env=env, cwd=str(workdir))
        self._lines: queue.Queue = queue.Queue()
        self._stderr: list[bytes] = []
        threading.Thread(target=self._pump_stdout, daemon=True).start()
        threading.Thread(target=self._pump_stderr, daemon=True).start()

    def _pump_stdout(self) -> None:
        for line in iter(self.proc.stdout.readline, b''):
            self._lines.put(line)
        self._lines.put(None)

    def _pump_stderr(self) -> None:
        for line in iter(self.proc.stderr.readline, b''):
            self._stderr.append(line)

    @property
    def stderr(self) -> str:
        return b''.join(self._stderr).decode('utf-8', 'replace')

    def send(self, frame: dict) -> None:
        self.proc.stdin.write((json.dumps(frame) + '\n').encode('utf-8'))
        self.proc.stdin.flush()

    def read(self, timeout: float = 240.0) -> dict:
        line = self._lines.get(timeout=timeout)
        if line is None:
            raise AssertionError(f'the add-on closed its stdout. stderr:\n{self.stderr}')
        # Every stdout line must be a frame: anything else is a native library or
        # a print() that reached the protocol channel.
        return json.loads(line.decode('utf-8'))

    def until_terminal(self, req_id: str) -> list[dict]:
        frames = []
        while True:
            frame = self.read()
            if frame.get('id') != req_id:
                frames.append(frame)
                continue
            frames.append(frame)
            if frame['type'] in ('result', 'error'):
                return frames

    def close(self) -> None:
        try:
            self.send({'type': 'shutdown'})
            self.proc.wait(timeout=10)
        except Exception:  # noqa: BLE001 — teardown must not hide the test's own failure
            self.proc.kill()


@pytest.fixture
def frozen(tmp_path):
    addon = Frozen(tmp_path, Path(os.environ.get('PHONON2_TEST_MODELS_DIR') or tmp_path / 'models'))
    try:
        yield addon
    finally:
        addon.close()


@needs_binary
def test_handshake_advertises_english_asr(frozen):
    hello = frozen.read()
    assert hello['type'] == 'hello', frozen.stderr
    assert hello['protocol'] == 1
    assert hello['addon'] == 'phonon2'
    assert hello['capabilities'][0]['task'] == 'asr.transcribe'
    assert hello['capabilities'][0]['languages'] == ['en']


@needs_binary
def test_a_missing_file_is_answered_not_dropped(frozen):
    frozen.read()  # hello
    frozen.send({'id': 'r1', 'type': 'asr.transcribe', 'params': {'audio_path': '/nonexistent.wav'}})
    frame = frozen.read()
    assert frame['id'] == 'r1' and frame['type'] == 'error' and frame['code'] == 'bad_params'


@needs_model
def test_real_transcription_through_the_frozen_binary(frozen):
    frozen.read()  # hello
    frozen.send({'id': 'r2', 'type': 'asr.transcribe',
                 'params': {'audio_path': str(JFK), 'language': 'en-us', 'options': {}}})
    frames = frozen.until_terminal('r2')
    terminal = frames[-1]
    assert terminal['type'] == 'result', f'{terminal}\n{frozen.stderr}'
    text = ' '.join(segment['text'] for segment in terminal['data']['segments']).lower()
    assert 'country' in text
    assert terminal['data']['model'] == 'phonon-2'
    assert any(f['type'] == 'progress' for f in frames)
