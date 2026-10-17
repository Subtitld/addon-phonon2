"""The protocol and the request handler, driven with a fake engine.

No torch and no model download: `engine.transcribe` is replaced by FakeEngine,
and frames go through in-memory streams.
"""

import io
import json
import threading
import time
import wave

import pytest

from phonon2_addon import audio, engine, protocol, server
from phonon2_addon.protocol import (
    ERR_BAD_PARAMS, ERR_CANCELLED, ERR_INTERNAL, ERR_MODEL_MISSING, ERR_UNSUPPORTED_LANGUAGE,
    Cancelled, RequestError,
)


class FakeDecoder:
    """Stands in for ffmpeg (tested in test_audio.py): writes `seconds` of silence."""

    def __init__(self, seconds: float = 0.5):
        self.seconds = seconds
        self.calls: list = []

    def __call__(self, source, dest, cancelled):
        self.calls.append(source)
        if cancelled():
            raise Cancelled()
        _wav(dest, self.seconds)
        return self.seconds


class FakeEngine:
    def __init__(self, segments=None, error=None, gate=None):
        self.segments = segments if segments is not None else [{'start': 0.0, 'end': 1.2, 'text': 'Hello there.'}]
        self.error = error
        self.gate = gate
        self.calls = []

    def __call__(self, audio_path, *, models_dir, threads, hotwords, on_stage):
        self.calls.append({'audio_path': audio_path, 'models_dir': models_dir,
                           'threads': threads, 'hotwords': hotwords})
        on_stage(0.5, 'Transcribing...')
        if self.gate is not None:
            self.gate.wait(5)
        if self.error is not None:
            raise self.error
        return list(self.segments), 1.2


def _wav(path, seconds=0.5):
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b'\x00\x00' * int(16000 * seconds))
    return path


def _frames(buf: io.BytesIO) -> list[dict]:
    return [json.loads(line) for line in buf.getvalue().decode('utf-8').splitlines() if line]


def _wait_idle(timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while server._active and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not server._active, 'a worker did not finish'


@pytest.fixture
def wire():
    out = io.BytesIO()
    protocol.use_streams(io.BytesIO(), out)
    return out


@pytest.fixture
def wav(tmp_path):
    return _wav(tmp_path / 'speech.wav')


@pytest.fixture(autouse=True)
def decoder(monkeypatch):
    fake_decoder = FakeDecoder()
    monkeypatch.setattr(audio, 'to_wav', fake_decoder)
    return fake_decoder


@pytest.fixture
def fake(monkeypatch):
    fake_engine = FakeEngine()
    monkeypatch.setattr(engine, 'transcribe', fake_engine)
    return fake_engine


def _run(params: dict, cancel: bool = False, req_id: str = 'r1') -> None:
    state = server._Request(req_id)
    if cancel:
        state.cancel.set()
    server._handle_transcribe(state, params)


def test_hello_frame_advertises_english_asr(wire):
    protocol.hello(server.ADDON_ID, '0.0.1', server.capabilities())
    (hello,) = _frames(wire)
    assert hello['type'] == 'hello'
    assert hello['protocol'] == protocol.PROTOCOL_VERSION
    assert hello['addon'] == 'phonon2'
    assert hello['capabilities'] == [{'task': 'asr.transcribe', 'languages': ['en'],
                                      'streaming': False, 'voice_clone': False}]


def test_transcribe_sends_progress_then_one_result(wire, wav, fake):
    _run({'audio_path': str(wav), 'language': 'en-us'})
    frames = _frames(wire)
    assert frames[-2] == {'id': 'r1', 'type': 'progress', 'value': 1.0, 'message': ''}
    assert frames[-1] == {'id': 'r1', 'type': 'result', 'data': {
        'segments': [{'start': 0.0, 'end': 1.2, 'text': 'Hello there.'}],
        'duration_sec': 1.2,
        'model': 'phonon-2',
    }}
    assert [f['type'] for f in frames].count('result') == 1


def test_missing_audio_path_is_bad_params(wire, fake):
    _run({})
    (error,) = _frames(wire)
    assert error['type'] == 'error' and error['code'] == ERR_BAD_PARAMS
    assert fake.calls == []


def test_audio_path_that_is_not_a_file_is_bad_params(wire, fake, tmp_path):
    _run({'audio_path': str(tmp_path / 'missing.wav')})
    (error,) = _frames(wire)
    assert error['code'] == ERR_BAD_PARAMS
    assert fake.calls == []


@pytest.mark.parametrize('language', ['', 'auto', 'en', 'en_US', 'EN-gb', None])
def test_english_and_auto_languages_are_accepted(wire, wav, fake, language):
    _run({'audio_path': str(wav), 'language': language})
    assert _frames(wire)[-1]['type'] == 'result'


def test_other_languages_are_rejected_before_any_work(wire, wav, fake):
    _run({'audio_path': str(wav), 'language': 'pt-br'})
    (error,) = _frames(wire)[-1:]
    assert error['code'] == ERR_UNSUPPORTED_LANGUAGE
    assert fake.calls == []


def test_hotwords_are_split_deduplicated_and_capped(wire, wav, fake):
    _run({'audio_path': str(wav), 'options': {'hotwords': 'Ada, Quillon,Ada\nNeutrino'}})
    assert fake.calls[0]['hotwords'] == ['Ada', 'Quillon', 'Neutrino']

    many = ', '.join(f'word{i}' for i in range(30))
    _run({'audio_path': str(wav), 'options': {'hotwords': many}}, req_id='r2')
    assert len(fake.calls[1]['hotwords']) == engine.MAX_HOTWORDS


@pytest.mark.parametrize('raw, expected', [(None, 0), ('many', 0), ('4', 4), ('999', 256), ('-3', 0)])
def test_threads_option_is_clamped_and_falls_back_to_auto(wire, wav, fake, raw, expected):
    _run({'audio_path': str(wav), 'options': {'threads': raw}})
    assert fake.calls[0]['threads'] == expected


def test_cancel_before_decode_sends_cancelled_and_never_decodes(wire, wav, fake):
    _run({'audio_path': str(wav)}, cancel=True)
    (error,) = _frames(wire)
    assert error['code'] == ERR_CANCELLED
    assert fake.calls == []


def test_cancel_during_decode_discards_the_result(wire, wav, monkeypatch):
    gate = threading.Event()
    fake = FakeEngine(gate=gate)
    monkeypatch.setattr(engine, 'transcribe', fake)
    state = server._Request('r1')
    worker = threading.Thread(target=server._handle_transcribe,
                              args=(state, {'audio_path': str(wav)}))
    worker.start()
    state.cancel.set()
    gate.set()
    worker.join(5)
    frames = _frames(wire)
    assert [f['type'] for f in frames if f['type'] in ('result', 'error')] == ['error']
    assert frames[-1]['code'] == ERR_CANCELLED


def test_every_request_is_decoded_first(wire, wav, fake, decoder):
    _run({'audio_path': str(wav)})
    assert decoder.calls == [str(wav)]
    assert str(fake.calls[0]['audio_path']).endswith('audio.16k.wav')


def test_audio_shorter_than_the_minimum_skips_the_engine(wire, wav, fake, decoder):
    decoder.seconds = 0.05
    _run({'audio_path': str(wav)})
    assert fake.calls == []
    assert _frames(wire)[-1]['data']['segments'] == []


def test_decode_failure_is_reported_with_its_code(wire, wav, fake, monkeypatch):
    def broken(source, dest, cancelled):
        raise RequestError(ERR_INTERNAL, 'Could not decode the audio with ffmpeg (exit 1)')

    monkeypatch.setattr(audio, 'to_wav', broken)
    _run({'audio_path': str(wav)})
    error = _frames(wire)[-1]
    assert error['code'] == ERR_INTERNAL and 'ffmpeg' in error['message']
    assert fake.calls == []


def test_engine_error_keeps_its_code_and_retry_flag(wire, wav, monkeypatch):
    monkeypatch.setattr(engine, 'transcribe',
                        FakeEngine(error=RequestError(ERR_MODEL_MISSING, 'no weights', retryable=True)))
    _run({'audio_path': str(wav)})
    error = _frames(wire)[-1]
    assert (error['code'], error['message'], error['retryable']) == (ERR_MODEL_MISSING, 'no weights', True)


def test_unexpected_engine_error_is_internal_with_its_text(wire, wav, monkeypatch):
    monkeypatch.setattr(engine, 'transcribe', FakeEngine(error=RuntimeError('boom')))
    _run({'audio_path': str(wav)})
    error = _frames(wire)[-1]
    assert error['code'] == ERR_INTERNAL
    assert 'boom' in error['message']


def test_main_loop_answers_unknown_frames_and_stops_on_shutdown(wire):
    lines = (b'{"id":"x1","type":"nonsense"}\n'
             b'{"type":"ready","host":"subtitld","host_version":"1","protocol":1}\n'
             b'{"type":"shutdown"}\n')
    protocol.use_streams(io.BytesIO(lines), wire)
    assert server._main_loop() == 0
    (error,) = _frames(wire)
    assert error['id'] == 'x1' and error['code'] == ERR_BAD_PARAMS


def test_main_loop_runs_a_request_to_its_result(wire, wav, fake):
    request = {'id': 'r2', 'type': 'asr.transcribe', 'params': {'audio_path': str(wav)}}
    lines = (json.dumps(request) + '\n' + '{"type":"shutdown"}\n').encode('utf-8')
    protocol.use_streams(io.BytesIO(lines), wire)
    assert server._main_loop() == 0
    _wait_idle()
    assert _frames(wire)[-1]['type'] == 'result'
    assert _frames(wire)[-1]['id'] == 'r2'


def test_cancel_frame_reaches_a_running_request(wire, wav, monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(engine, 'transcribe', FakeEngine(gate=gate))
    request = {'id': 'r3', 'type': 'asr.transcribe', 'params': {'audio_path': str(wav)}}
    cancel = {'id': 'c1', 'type': 'cancel', 'target': 'r3'}
    lines = '\n'.join(json.dumps(f) for f in (request, cancel, {'type': 'shutdown'})) + '\n'
    protocol.use_streams(io.BytesIO(lines.encode('utf-8')), wire)
    assert server._main_loop() == 0  # the cancel is read while r3 waits at the gate
    gate.set()
    _wait_idle()
    frames = _frames(wire)
    assert frames[-1]['id'] == 'r3' and frames[-1]['code'] == ERR_CANCELLED
    assert not any(f['type'] == 'result' for f in frames)
