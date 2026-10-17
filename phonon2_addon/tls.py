"""Which CA certificates the model download trusts, in the frozen Linux build.

The frozen bundle carries the build machine's OpenSSL (AlmaLinux 8), which looks
for CA certificates where AlmaLinux keeps them: OPENSSLDIR /etc/pki/tls. On
Debian, Ubuntu, Arch and others that folder does not exist, and the model
download fails with "CERTIFICATE_VERIFY_FAILED: unable to get local issuer
certificate". The download goes through huggingface_hub's HTTP client, which
trusts SSL_CERT_FILE when it is set and otherwise asks OpenSSL's default path.

So the frozen Linux build points SSL_CERT_FILE at the CA bundle this machine's
distribution keeps. That bundle is the system store, including certificates an
administrator added. certifi's bundle is the fallback. An SSL_CERT_FILE or
SSL_CERT_DIR already set always wins (a proxy that inspects HTTPS needs its own).
macOS and Windows are left alone: there the client reads the native trust store.
"""

from __future__ import annotations

import os
import sys

# Where Linux distributions keep their CA bundle: the list Go's crypto/x509 checks.
SYSTEM_CA_FILES = (
    '/etc/ssl/certs/ca-certificates.crt',                 # Debian, Ubuntu, Arch, Gentoo
    '/etc/pki/tls/certs/ca-bundle.crt',                   # Fedora, RHEL
    '/etc/ssl/ca-bundle.pem',                             # openSUSE
    '/etc/pki/tls/cacert.pem',                            # OpenELEC
    '/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem',  # CentOS, RHEL 7
    '/etc/ssl/cert.pem',                                  # Alpine
)


def configure_ca_bundle(frozen: bool | None = None, platform: str | None = None) -> str | None:
    """Set SSL_CERT_FILE for the frozen Linux build. Returns the bundle chosen, or None."""
    frozen = getattr(sys, 'frozen', False) if frozen is None else frozen
    platform = sys.platform if platform is None else platform
    if not frozen or not platform.startswith('linux'):
        return None
    if os.environ.get('SSL_CERT_FILE') or os.environ.get('SSL_CERT_DIR'):
        return None
    for path in SYSTEM_CA_FILES:
        if os.path.isfile(path):
            os.environ['SSL_CERT_FILE'] = path
            return path
    try:
        import certifi
    except ImportError:
        return None
    os.environ['SSL_CERT_FILE'] = certifi.where()
    return os.environ['SSL_CERT_FILE']
