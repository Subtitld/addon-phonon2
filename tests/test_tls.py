"""CA trust for the frozen Linux build's model download."""

import sys
import types

import pytest

from phonon2_addon import tls


@pytest.fixture(autouse=True)
def no_cert_env(monkeypatch):
    """Start every test with neither variable set, and restore them afterwards."""
    for name in ('SSL_CERT_FILE', 'SSL_CERT_DIR'):
        monkeypatch.setenv(name, 'placeholder')  # undone at teardown, even after the delenv
        monkeypatch.delenv(name)


def test_the_first_system_bundle_that_exists_is_trusted(monkeypatch, tmp_path):
    present = tmp_path / 'ca-bundle.crt'
    present.write_text('-----BEGIN CERTIFICATE-----\n', encoding='ascii')
    monkeypatch.setattr(tls, 'SYSTEM_CA_FILES', (str(tmp_path / 'missing.crt'), str(present)))
    assert tls.configure_ca_bundle(frozen=True, platform='linux') == str(present)
    assert tls.os.environ['SSL_CERT_FILE'] == str(present)


def test_certifi_is_the_fallback_when_no_system_bundle_exists(monkeypatch, tmp_path):
    monkeypatch.setattr(tls, 'SYSTEM_CA_FILES', (str(tmp_path / 'missing.crt'),))
    fake = types.ModuleType('certifi')
    fake.where = lambda: str(tmp_path / 'cacert.pem')
    monkeypatch.setitem(sys.modules, 'certifi', fake)
    assert tls.configure_ca_bundle(frozen=True, platform='linux') == str(tmp_path / 'cacert.pem')


@pytest.mark.parametrize('name', ['SSL_CERT_FILE', 'SSL_CERT_DIR'])
def test_a_setting_already_made_wins(monkeypatch, name):
    monkeypatch.setenv(name, '/corporate/proxy-ca')
    assert tls.configure_ca_bundle(frozen=True, platform='linux') is None
    assert tls.os.environ[name] == '/corporate/proxy-ca'


@pytest.mark.parametrize('frozen, platform', [(False, 'linux'), (True, 'darwin'), (True, 'win32')])
def test_only_the_frozen_linux_build_is_touched(frozen, platform):
    assert tls.configure_ca_bundle(frozen=frozen, platform=platform) is None
    assert 'SSL_CERT_FILE' not in tls.os.environ
