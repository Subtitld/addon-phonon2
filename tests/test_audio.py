"""The decoder, with the real ffmpeg. Skipped where ffmpeg is not installed."""

import shutil
import subprocess
import wave
from pathlib import Path

import pytest

from phonon2_addon import audio
from phonon2_addon.protocol import ERR_INTERNAL, Cancelled, RequestError

JFK = Path(__file__).resolve().parent / 'data' / 'jfk.flac'
ffmpeg = pytest.mark.skipif(shutil.which('ffmpeg') is None, reason='ffmpeg not on PATH')


def _never() -> bool:
    return False


def _format(path) -> tuple[int, int, int]:
    with wave.open(str(path), 'rb') as w:
        return w.getnchannels(), w.getsampwidth(), w.getframerate()


@ffmpeg
def test_flac_is_decoded_to_16k_mono_wav(tmp_path):
    dest = tmp_path / 'out.wav'
    seconds = audio.to_wav(JFK, dest, _never)
    assert _format(dest) == (1, 2, 16000)
    assert 10.5 < seconds < 11.5


@ffmpeg
def test_video_container_is_decoded_by_its_audio_track(tmp_path):
    source = tmp_path / 'clip.mp4'
    subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                    '-f', 'lavfi', '-i', 'testsrc=duration=2:size=64x64:rate=10',
                    '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2',
                    '-shortest', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac',
                    str(source)], check=True)
    dest = tmp_path / 'out.wav'
    seconds = audio.to_wav(source, dest, _never)
    assert _format(dest) == (1, 2, 16000)
    assert 1.5 < seconds < 2.5


@ffmpeg
def test_a_file_that_is_not_media_is_an_internal_error(tmp_path):
    source = tmp_path / 'notes.txt'
    source.write_text('not audio at all', encoding='utf-8')
    with pytest.raises(RequestError) as info:
        audio.to_wav(source, tmp_path / 'out.wav', _never)
    assert info.value.code == ERR_INTERNAL


@ffmpeg
def test_cancel_before_decoding_starts_nothing(tmp_path):
    dest = tmp_path / 'out.wav'
    with pytest.raises(Cancelled):
        audio.to_wav(JFK, dest, lambda: True)
    assert not dest.exists()


def test_missing_ffmpeg_is_an_internal_error(tmp_path, monkeypatch):
    monkeypatch.setattr(audio, 'find_ffmpeg', lambda: None)
    with pytest.raises(RequestError) as info:
        audio.to_wav(JFK, tmp_path / 'out.wav', _never)
    assert info.value.code == ERR_INTERNAL
    assert 'SUBTITLD_FFMPEG_EXECUTABLE' in info.value.message
