"""One installation CA and renewable leaf certificates, always outside projects."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import ssl
import subprocess
import tempfile

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from .config import PistolError, data_dir, hostname, state_lock


def directory() -> Path:
    return data_dir() / "pki"


def _protect(root: Path) -> None:
    from .utils.files import is_link
    if any(is_link(p) for p in (root, *root.parents) if p.exists()):
        raise PistolError("Pistol PKI cannot be stored through a link or junction.")
    if any((p / ".git").exists() for p in (root, *root.parents)):
        raise PistolError("Pistol PKI must be outside Git repositories.")
    from .chamber import list_chambers
    if any(root.resolve().is_relative_to(Path(c.project).resolve()) for c in list_chambers()):
        raise PistolError("Pistol PKI must be outside chamber projects.")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if any(is_link(p) for p in root.iterdir()):
        raise PistolError("Pistol PKI files cannot be links or junctions.")
    if os.name == "nt":
        # Set an exact protected DACL before writing keys. Child files inherit it.
        script = "$ErrorActionPreference='Stop'; $a=[System.IO.Directory]::GetAccessControl($env:PISTOL_PKI_PATH); " \
                 "$a.SetAccessRuleProtection($true,$false); " \
                 "foreach($rule in @($a.Access)) { $a.RemoveAccessRuleSpecific($rule) }; " \
                 "$sid=[System.Security.Principal.WindowsIdentity]::GetCurrent().User; " \
                 "foreach($s in @($sid, [System.Security.Principal.SecurityIdentifier]::new('S-1-5-18'))) { " \
                 "$r=[System.Security.AccessControl.FileSystemAccessRule]::new($s,'FullControl','ContainerInherit,ObjectInherit','None','Allow'); $a.AddAccessRule($r) }; " \
                 "[System.IO.Directory]::SetAccessControl($env:PISTOL_PKI_PATH,$a)"
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                                env={**os.environ, "PISTOL_PKI_PATH": str(root)}, capture_output=True, text=True,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            raise PistolError("Could not restrict Pistol PKI permissions to the current user and SYSTEM: " + result.stderr.strip())
    else:
        root.chmod(0o700)


def _write(path: Path, content: bytes) -> None:
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".pki-")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name != "nt":
            os.chmod(name, 0o600)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def _key_bytes(key) -> bytes:
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


def _builder(key, subject, issuer, days: int):
    now = datetime.now(timezone.utc)
    return (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=days)))


def _usage(ca: bool):
    return x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False,
                         data_encipherment=False, key_agreement=False, key_cert_sign=ca, crl_sign=ca,
                         encipher_only=False, decipher_only=False)


def _valid(cert, key, issuer, *, names=None, days=0):
    now = datetime.now(timezone.utc)
    cert.verify_directly_issued_by(issuer)
    if not cert.not_valid_before_utc <= now < cert.not_valid_after_utc - timedelta(days=days):
        raise ValueError("Certificate is expired, not yet valid, or due for renewal.")
    if cert.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo) != key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo):
        raise ValueError("Certificate and private key do not match.")
    if cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca != (names is None):
        raise ValueError("Incorrect certificate role.")
    usage = cert.extensions.get_extension_for_class(x509.KeyUsage).value
    if not usage.digital_signature or usage.key_cert_sign != (names is None):
        raise ValueError("Incorrect certificate key usage.")
    if names is not None:
        sans = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
        if set(sans) != set(names):
            raise ValueError("Certificate SANs do not match the LECAP names.")
        if ExtendedKeyUsageOID.SERVER_AUTH not in cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value:
            raise ValueError("Certificate does not permit TLS server authentication.")


def _ca():
    root = directory()
    cert_path, key_path = root / "ca.pem", root / "ca-key.pem"
    if cert_path.exists() != key_path.exists():
        raise PistolError("Incomplete Pistol CA. Restore the matching CA certificate/key backup; refusing to silently replace trust.")
    if not cert_path.exists():
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Pistol Local CA")])
        cert = (_builder(key, name, name, 3650)
                .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
                .add_extension(_usage(True), critical=True)
                .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
                .sign(key, hashes.SHA256()))
        _write(key_path, _key_bytes(key))
        _write(cert_path, cert.public_bytes(serialization.Encoding.PEM))
    try:
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        _valid(cert, key, cert, days=91)
    except Exception as exc:
        raise PistolError("Pistol CA is invalid or has less than 91 days remaining. Restore it or explicitly replace and re-enroll trust; it was not replaced.") from exc
    return cert, key


def ensure_ca() -> Path:
    with state_lock():
        _protect(directory())
        _ca()
    return directory() / "ca.pem"


def leaf(names: list[str]) -> tuple[Path, Path]:
    names = sorted(set(hostname(n) for n in names))
    if not names:
        raise PistolError("A leaf certificate requires at least one DNS name.")
    token = hashlib.sha256("\n".join(names).encode()).hexdigest()
    with state_lock():
        _protect(directory())
        ca, ca_key = _ca()
        cert_path = directory() / f"{token}.pem"
        key_path = directory() / f"{token}-key.pem"
        try:
            cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
            key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
            _valid(cert, key, ca, names=names, days=30)
        except (OSError, ValueError, TypeError, x509.ExtensionNotFound, InvalidSignature):
            key = ec.generate_private_key(ec.SECP256R1())
            subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, names[0][:64])])
            cert = (_builder(key, subject, ca.subject, 90)
                    .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                    .add_extension(_usage(False), critical=True)
                    .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                    .add_extension(x509.SubjectAlternativeName([x509.DNSName(n) for n in names]), critical=False)
                    .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
                    .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
                    .sign(ca_key, hashes.SHA256()))
            _valid(cert, key, ca, names=names)
            _write(key_path, _key_bytes(key))
            _write(cert_path, cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path


def server_context(names: list[str]) -> ssl.SSLContext:
    cert, key = leaf(names)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.set_ciphers("ECDHE+AESGCM:ECDHE+CHACHA20")
    context.set_alpn_protocols(["http/1.1"])
    context.load_cert_chain(cert, key)
    return context


def export_ca(destination: Path) -> Path:
    source = ensure_ca()
    # Exclusive creation prevents accidental overwrites, including PKI key files.
    with destination.open("xb") as stream:
        stream.write(source.read_bytes())
    return destination


def ca_exists() -> bool:
    """Read-only check for the existing global CA pair."""
    root = directory()
    return (root / "ca.pem").is_file() and (root / "ca-key.pem").is_file()


def windows_trust_supported() -> bool:
    return os.name == "nt"


def is_ca_trusted() -> bool:
    """Check the exact public CA in Windows CurrentUser/Root; never mutate trust."""
    path = directory() / "ca.pem"
    if not path.is_file() or not windows_trust_supported():
        return False
    try:
        thumbprint = x509.load_pem_x509_certificate(path.read_bytes()).fingerprint(hashes.SHA1()).hex().upper()
    except (OSError, ValueError) as exc:
        raise PistolError("Cannot read the Pistol public CA certificate for the trust check.") from exc
    script = (
        "$ErrorActionPreference='Stop'; "
        "$store=[System.Security.Cryptography.X509Certificates.X509Store]::new("
        "[System.Security.Cryptography.X509Certificates.StoreName]::Root,"
        "[System.Security.Cryptography.X509Certificates.StoreLocation]::CurrentUser); "
        "try { $store.Open([System.Security.Cryptography.X509Certificates.OpenFlags]::ReadOnly); "
        "$found=$store.Certificates.Find("
        "[System.Security.Cryptography.X509Certificates.X509FindType]::FindByThumbprint,"
        "$env:PISTOL_CA_THUMBPRINT,$false); "
        "[Console]::Out.Write($(if($found.Count -gt 0){'true'}else{'false'})) } "
        "finally { $store.Close() }"
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            env={**os.environ, "PISTOL_CA_THUMBPRINT": thumbprint},
            capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except OSError as exc:
        raise PistolError(f"Cannot inspect the Windows current-user Root store: {exc}") from exc
    if result.returncode or result.stdout.strip() not in {"true", "false"}:
        raise PistolError("Cannot inspect the Windows current-user Root store: " + (result.stderr.strip() or "unexpected trust-check result"))
    return result.stdout.strip() == "true"


def trust_ca() -> str:
    source = ensure_ca()
    if not windows_trust_supported():
        raise PistolError(f"Manually enroll the public CA {source} in your OS/browser trust store. Pistol does not change system trust on this platform.")
    if is_ca_trusted():
        return "Pistol Local CA is already trusted in the Windows current-user Root store; no trust-store change was required."
    try:
        result = subprocess.run(["certutil.exe", "-user", "-addstore", "Root", str(source)], capture_output=True, text=True)
    except OSError as exc:
        raise PistolError(f"Cannot enroll Pistol Local CA in the Windows current-user Root store: {exc}") from exc
    if result.returncode:
        raise PistolError("Windows refused current-user CA enrollment. Confirm the Windows prompt or contact your administrator: " + result.stderr.strip())
    if not is_ca_trusted():
        raise PistolError("Windows reported CA enrollment success, but the Pistol Local CA is not visible in the current-user Root store. Retry with pistol config --trust-ca.")
    return "Pistol Local CA enrolled in the Windows current-user Root store. Other devices and browsers with independent stores require manual enrollment."
