"""The real Phonon-2 engine on a known clip.

Skipped unless PHONON2_TEST_TRANSCRIPTION=1. The first run downloads the
164 MB model and needs torch installed (see README "Development"). Models go
to PHONON2_TEST_MODELS_DIR, or to a temporary folder, never to Subtitld's cache.
"""

import os
from pathlib import Path

import pytest

from phonon2_addon import audio, engine

FIXTURE = Path(__file__).resolve().parent / 'data' / 'jfk.flac'

pytestmark = [
    pytest.mark.transcription,
    pytest.mark.skipif(os.environ.get('PHONON2_TEST_TRANSCRIPTION') != '1',
                       reason='set PHONON2_TEST_TRANSCRIPTION=1 to run the real engine'),
]


def test_transcribes_the_jfk_clip(tmp_path, monkeypatch):
    models = Path(os.environ.get('PHONON2_TEST_MODELS_DIR') or tmp_path / 'models')
    monkeypatch.setenv('FERMION_CACHE_DIR', '')  # restored after the test
    monkeypatch.setattr(engine, '_speech', None)
    wav = tmp_path / 'jfk.16k.wav'
    audio.to_wav(FIXTURE, wav, lambda: False)

    segments, seconds = engine.transcribe(wav, models_dir=models, threads=0,
                                          hotwords=[], on_stage=lambda v, m: None)

    text = ' '.join(segment['text'] for segment in segments).lower()
    assert 'country' in text
    assert seconds > 5
    assert all(segment['end'] >= segment['start'] for segment in segments)
