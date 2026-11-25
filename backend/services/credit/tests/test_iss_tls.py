"""TLS trust of the ISS client: certifi + the bundled Russian Trusted CA (+ optional extra CA)."""
import hashlib
import re
import ssl

import certifi
import pytest

from credit_service.iss import RUSSIAN_TRUSTED_CA, iss_ssl_context

# SHA-256 of the DER certificates; checked against an independently installed copy (Windows root store)
EXPECTED = {
    "D26D2D0231B7C39F92CC738512BA54103519E4405D68B5BD703E9788CA8ECF31": "Russian Trusted Root CA",
    "2155785036C900DBB5F1BB2A1569C80C55595BD6BF94867A29BBDDBC7D88A3F2": "Russian Trusted Sub CA",
}


def _fingerprints(path) -> set[str]:
    pems = re.findall(r"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----", path.read_text(encoding="ascii"), re.S)
    return {hashlib.sha256(ssl.PEM_cert_to_DER_cert(p)).hexdigest().upper() for p in pems}


def _common_names(ctx: ssl.SSLContext) -> set[str]:
    return {v for c in ctx.get_ca_certs() for rdn in c["subject"] for k, v in rdn if k == "commonName"}


def test_bundle_holds_exactly_the_pinned_certificates():
    assert _fingerprints(RUSSIAN_TRUSTED_CA) == set(EXPECTED)


def test_context_trusts_certifi_and_russian_ca():
    ctx = iss_ssl_context()
    names = _common_names(ctx)
    assert {"Russian Trusted Root CA", "Russian Trusted Sub CA"} <= names
    certifi_roots = len(ssl.create_default_context(cafile=certifi.where()).get_ca_certs())
    assert len(ctx.get_ca_certs()) == certifi_roots + 2
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname


def test_extra_ca_is_added_not_replacing(tmp_path):
    extra = tmp_path / "corporate.crt"
    pem = re.search(r"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----", open(certifi.where(), encoding="ascii").read(), re.S)
    extra.write_text(pem.group(0) + "\n", encoding="ascii")
    ctx = iss_ssl_context(str(extra))
    assert "Russian Trusted Root CA" in _common_names(ctx)
    assert len(ctx.get_ca_certs()) == len(iss_ssl_context().get_ca_certs())  # a certifi root: already there


def test_bad_extra_ca_fails_loudly(tmp_path):
    with pytest.raises(FileNotFoundError):
        iss_ssl_context("/nonexistent/corporate-ca.crt")
    junk = tmp_path / "junk.crt"
    junk.write_text("not a certificate\n", encoding="ascii")
    with pytest.raises(ssl.SSLError):  # the extra file is really read
        iss_ssl_context(str(junk))
