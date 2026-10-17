"""The engine adapter, with a fake `fermion` module: no torch, no download."""

import errno
import os
import sys
import types
from types import SimpleNamespace

import pytest

from phonon2_addon import engine
from phonon2_addon.protocol import ERR_DISK_FULL, ERR_MODEL_MISSING, RequestError


def _fake_fermion(monkeypatch, load_speech):
    module = types.ModuleType('fermion')
    module.load_speech = load_speech
    monkeypatch.setitem(sys.modules, 'fermion', module)


def test_runtime_is_ok_when_fermion_provides_load_speech(monkeypatch):
    _fake_fermion(monkeypatch, lambda model, *, quiet=True: object())
    assert engine.runtime_error() is None


def test_runtime_error_when_fermion_lacks_load_speech(monkeypatch):
    monkeypatch.setitem(sys.modules, 'fermion', types.ModuleType('fermion'))
    assert 'load_speech' in engine.runtime_error()


def test_load_points_fermion_at_the_models_folder(monkeypatch, tmp_path):
    seen = {}

    def load_speech(model, *, quiet=True):
        seen.update(model=model, quiet=quiet,
                    cache=os.environ['FERMION_CACHE_DIR'],
                    threads=os.environ.get('FERMION_CPU_THREADS'))
        return object()

    _fake_fermion(monkeypatch, load_speech)
    # monkeypatch restores these after the test; _load sets them directly.
    monkeypatch.setenv('FERMION_CACHE_DIR', '')
    monkeypatch.setenv('FERMION_CPU_THREADS', '')
    engine._load(tmp_path / 'models', threads=4)
    assert seen == {'model': 'phonon-2', 'quiet': True,
                    'cache': str(tmp_path / 'models'), 'threads': '4'}


def test_the_add_on_pins_the_cpu_engine(monkeypatch, tmp_path):
    seen = {}

    def load_speech(model, *, quiet=True):
        seen['device'] = os.environ.get('FERMION_DEVICE')
        return object()

    _fake_fermion(monkeypatch, load_speech)
    monkeypatch.setenv('FERMION_DEVICE', 'placeholder')  # undone at teardown, even after the delenv
    monkeypatch.delenv('FERMION_DEVICE')
    engine._load(tmp_path, threads=0)
    assert seen == {'device': 'cpu'}


def test_an_engine_choice_from_the_environment_is_kept(monkeypatch, tmp_path):
    seen = {}

    def load_speech(model, *, quiet=True):
        seen['device'] = os.environ.get('FERMION_DEVICE')
        return object()

    _fake_fermion(monkeypatch, load_speech)
    monkeypatch.setenv('FERMION_DEVICE', 'mlx')
    engine._load(tmp_path, threads=0)
    assert seen['device'] == 'mlx'


@pytest.mark.parametrize('exc, code', [
    (OSError(errno.ENOSPC, 'No space left on device'), ERR_DISK_FULL),
    (RuntimeError('weights are corrupt'), ERR_MODEL_MISSING),
])
def test_load_failures_become_protocol_errors(monkeypatch, tmp_path, exc, code):
    def load_speech(model, *, quiet=True):
        raise exc

    _fake_fermion(monkeypatch, load_speech)
    with pytest.raises(RequestError) as info:
        engine._load(tmp_path, threads=0)
    assert info.value.code == code


def test_the_model_loads_once_and_empty_segments_are_dropped(monkeypatch, tmp_path):
    loads = []

    class Speech:
        def transcribe_detailed(self, path, *, hotwords):
            self.last = (path, hotwords)
            return SimpleNamespace(
                segments=[{'start': 0.12345, 'end': 2.0, 'text': '  Hello.  '},
                          {'start': 2.0, 'end': 2.5, 'text': '   '}],
                audio_seconds=2.5)

    def fake_load(models_dir, threads):
        loads.append(models_dir)
        return Speech()

    monkeypatch.setattr(engine, '_load', fake_load)
    monkeypatch.setattr(engine, '_speech', None)
    stages = []
    segments, seconds = engine.transcribe('a.wav', models_dir=tmp_path, threads=0,
                                          hotwords=['Ada'], on_stage=lambda v, m: stages.append(m))
    engine.transcribe('b.wav', models_dir=tmp_path, threads=0, hotwords=[],
                      on_stage=lambda v, m: None)

    assert len(loads) == 1
    assert segments == [{'start': 0.123, 'end': 2.0, 'text': 'Hello.'}]
    assert seconds == 2.5
    assert stages[0] == 'Loading phonon-2...'
    assert engine._speech.last == ('b.wav', [])
