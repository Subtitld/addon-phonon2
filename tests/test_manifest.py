"""manifest.json, pyproject.toml and the code must describe the same add-on."""

import json
import re
from pathlib import Path

from phonon2_addon import __version__, engine, protocol, server

ROOT = Path(__file__).resolve().parent.parent


def _manifest() -> dict:
    return json.loads((ROOT / 'manifest.json').read_text(encoding='utf-8'))


def test_version_agrees_everywhere():
    pyproject = (ROOT / 'pyproject.toml').read_text(encoding='utf-8')
    assert _manifest()['version'] == __version__
    assert re.search(rf'^version = "{re.escape(__version__)}"$', pyproject, re.MULTILINE)


def test_identity_task_and_executable():
    manifest = _manifest()
    assert manifest['id'] == server.ADDON_ID
    assert manifest['protocol'] == protocol.PROTOCOL_VERSION
    assert manifest['tasks'] == [server.TASK_TRANSCRIBE]
    assert manifest['executable'] == 'bin/phonon2-addon'


def test_languages_are_the_engine_languages_and_the_hello_frame():
    manifest = _manifest()
    assert manifest['languages'] == list(engine.SUPPORTED_LANGUAGES)
    assert server.capabilities()[0]['languages'] == manifest['languages']


def test_the_model_listed_is_the_one_the_engine_loads():
    ids = [model['id'] for model in _manifest()['models']]
    assert engine.MODEL_ID in ids


def test_config_schema_uses_known_field_types_and_unique_keys():
    fields = _manifest()['config_schema']['fields']
    assert {field['type'] for field in fields} <= {'bool', 'select', 'int', 'number', 'file', 'string'}
    keys = [field['key'] for field in fields]
    assert len(keys) == len(set(keys))
