from datetime import datetime, timedelta, timezone
import os

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
import pytest

from pistol import certificates, config


def test_ca_shared_and_leaf_sans_and_reuse(tmp_path):
    cert_path, key_path = certificates.leaf(["AI.Laick.Keller", "dashboard.laick.keller"])
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    ca_bytes = certificates.ensure_ca().read_bytes()
    ca = x509.load_pem_x509_certificate(ca_bytes)
    cert.verify_directly_issued_by(ca)
    assert cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName) == ["ai.laick.keller", "dashboard.laick.keller"]
    assert key_path.parent == config.data_dir() / "pki"
    before = cert_path.read_bytes()
    certificates.leaf(["ai.laick.keller", "dashboard.laick.keller"])
    assert cert_path.read_bytes() == before
    certificates.leaf(["other.test"])
    assert certificates.ensure_ca().read_bytes() == ca_bytes
    export = tmp_path / "public-ca.crt"
    certificates.export_ca(export)
    assert export.read_bytes() == ca_bytes
    assert b"PRIVATE KEY" not in export.read_bytes()
    with pytest.raises(FileExistsError):
        certificates.export_ca(key_path)
    if os.name != "nt":
        assert key_path.stat().st_mode & 0o777 == 0o600


def test_corrupt_leaf_replaced_and_corrupt_ca_rejected():
    cert, key = certificates.leaf(["app.test"])
    cert.write_bytes(b"broken certificate")
    certificates.leaf(["app.test"])
    assert x509.load_pem_x509_certificate(cert.read_bytes())
    ca = certificates.directory() / "ca.pem"
    ca.write_bytes(b"broken CA")
    with pytest.raises(config.PistolError, match="invalid"):
        certificates.leaf(["app.test"])
    assert ca.read_bytes() == b"broken CA"


def test_leaf_near_expiry_is_renewed():
    cert_path, key_path = certificates.leaf(["app.test"])
    ca, ca_key = certificates._ca()
    key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    original = x509.load_pem_x509_certificate(cert_path.read_bytes())
    builder = certificates._builder(key, original.subject, ca.subject, 10)
    for ext in original.extensions:
        builder = builder.add_extension(ext.value, ext.critical)
    old = builder.sign(ca_key, hashes.SHA256())
    cert_path.write_bytes(old.public_bytes(serialization.Encoding.PEM))
    certificates.leaf(["app.test"])
    renewed = x509.load_pem_x509_certificate(cert_path.read_bytes())
    assert renewed.serial_number != old.serial_number
    assert renewed.not_valid_after_utc > datetime.now(timezone.utc) + timedelta(days=80)


def test_https_config_initializes_one_global_ca():
    config.update_global({"https": True})
    original = (certificates.directory() / "ca.pem").read_bytes()
    config.update_global({"https": False})
    config.update_global({"https": True})
    assert (certificates.directory() / "ca.pem").read_bytes() == original


def test_pki_refuses_project_or_git_location(tmp_path, monkeypatch):
    root = tmp_path / "repository"
    root.mkdir()
    (root / ".git").mkdir()
    monkeypatch.setattr(certificates, "directory", lambda: root / "pki")
    with pytest.raises(config.PistolError, match="Git"):
        certificates.ensure_ca()
    assert not (root / "pki").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL")
def test_windows_private_keys_have_restricted_acl():
    import json
    import subprocess
    _, key = certificates.leaf(["app.test"])
    script = "$acl=Get-Acl -LiteralPath $env:PISTOL_TEST_KEY; " \
             "$sid=[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value; " \
             "@{current=$sid; rules=@($acl.Access | ForEach-Object { $_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value })} | ConvertTo-Json"
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                            env={**os.environ, "PISTOL_TEST_KEY": str(key)}, capture_output=True, text=True, check=True)
    acl = json.loads(result.stdout)
    assert set(acl["rules"]) == {acl["current"], "S-1-5-18"}


def test_key_mismatch_reissues_leaf():
    from cryptography.hazmat.primitives.asymmetric import ec
    cert, key = certificates.leaf(["app.test"])
    old = cert.read_bytes()
    key.write_bytes(certificates._key_bytes(ec.generate_private_key(ec.SECP256R1())))
    certificates.leaf(["app.test"])
    assert cert.read_bytes() != old


def test_failed_ca_preparation_does_not_persist_https(monkeypatch):
    def fail(_):
        raise config.PistolError("Unable to protect keys")
    monkeypatch.setattr(certificates, "_protect", fail)
    with pytest.raises(config.PistolError, match="protect"):
        config.update_global({"https": True})
    assert config.global_config()["https"] is False
    assert not (certificates.directory() / "ca-key.pem").exists()
