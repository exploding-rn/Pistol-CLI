"""Layered, read-only checks. A configured value is never evidence of success."""
from __future__ import annotations

from datetime import datetime, timezone
import http.client
import socket
import ssl

from cryptography import x509

from .certificates import directory
from .lecap_dns import inspect
from .lecap_proxy import fingerprint, request


def verify(access, *, http_port=80, https_port=443) -> dict:
    checks = []

    def record(name, ok, detail):
        checks.append({"check": name, "ok": ok, "detail": detail})

    def attempt(name, action, hint):
        try:
            detail = action()
            record(name, True, detail)
        except (OSError, ValueError, http.client.HTTPException) as exc:
            record(name, False, f"{exc}. {hint}")

    def reachable(port, address="127.0.0.1"):
        with socket.create_connection((address, port), timeout=2):
            return f"{address}:{port} reachable"

    record("enabled", access.values["enabled"], "LECAP enabled" if access.values["enabled"] else "Enable with --enabled true.")
    for dns in inspect(access):
        record(f'DNS {dns["name"]}', dns["ok"], f'Addresses: {dns["addresses"]}; expected {dns["expected"]}. ' + ("" if dns["ok"] else "Use --dns plan; check hosts/private DNS records."))
    attempt("target TCP", lambda: reachable(access.values["port"]), "Start the chamber application or correct --port.")
    if access.mode == "direct":
        record("direct HTTP", False, "Direct access disabled.") if not access.values["enabled"] else attempt(
            "direct HTTP", lambda: f'HTTP {request(access.domains[0], "127.0.0.1", access.values["port"], method="HEAD")[0]}', "Check the application's HTTP listener.")
        return {"passed": all(c["ok"] for c in checks), "checks": checks, "notes": ["Direct mode: Pistol does not terminate TLS or manage the application listener. Use --proxy true for managed HTTPS."]}

    for name in access.domains:
        def health():
            status, headers = request(name, "127.0.0.1", http_port, path="/.well-known/pistol-lecap")
            if status != 200 or headers.get("X-Pistol-LECAP") != fingerprint(access):
                raise ValueError("Proxy identity/configuration does not match")
            return "Active proxy with matching route configuration"
        attempt(f"proxy {name}", health, "Run/restart pistol lecap --serve.")
        context = None
        if access.values["https"]:
            attempt(f"HTTPS listener {name}", lambda: reachable(https_port), "Run --serve; check port 443.")
            try:
                context = ssl.create_default_context(cafile=str(directory() / "ca.pem"))
            except OSError as exc:
                record(f"CA {name}", False, f"{exc}. Configure HTTPS and start the proxy to issue certificates.")
            if context:
                def handshake():
                    with socket.create_connection(("127.0.0.1", https_port), timeout=2) as sock:
                        with context.wrap_socket(sock, server_hostname=name) as secured:
                            cert = x509.load_der_x509_certificate(secured.getpeercert(binary_form=True))
                            now = datetime.now(timezone.utc)
                            if not cert.not_valid_before_utc <= now < cert.not_valid_after_utc:
                                raise ValueError("Invalid certificate dates")
                            sans = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
                            if name not in sans:
                                raise ValueError("Hostname missing from certificate SANs")
                            return f"{secured.version()}; valid certificate dates, SAN {name}, chain verified against Pistol CA"
                attempt(f"TLS/certificate/SAN {name}", handshake, "Restart proxy to renew certificates; check the Pistol CA.")

            def trust():
                with socket.create_connection(("127.0.0.1", https_port), timeout=2) as sock:
                    with ssl.create_default_context().wrap_socket(sock, server_hostname=name):
                        return "Verified using this Python runtime's default OS trust roots"
            attempt(f"system certificate trust {name}", trust, "Explicitly enroll with pistol config --trust-ca on Windows; browsers/devices may have separate trust stores.")
            if access.values["redirect"]:
                def redirect():
                    status, headers = request(name, "127.0.0.1", http_port, path="/pistol-verify?redirect=1", method="HEAD")
                    if status != 308 or headers.get("Location") != f"https://{name}/pistol-verify?redirect=1":
                        raise ValueError("Expected HTTP 308 redirect preserving path and query")
                    return "HTTP redirects to HTTPS, preserving path and query"
                attempt(f"HTTP redirect {name}", redirect, "Restart proxy; check --redirect true.")

        def route():
            if access.values["https"] and context is None:
                raise ValueError("No trusted CA available for route check")
            status, headers = request(name, "127.0.0.1", https_port if context else http_port, context=context, method="HEAD")
            if headers.get("X-Pistol-Route") != fingerprint(access):
                raise ValueError(f"No matching upstream response (HTTP {status})")
            return f"Upstream answered HEAD / with HTTP {status} through the configured proxy"
        attempt(f"reverse proxy route {name}", route, "Check the target port, application, and proxy route.")
        if access.values["lan"]:
            def lan():
                address = access.values["lan_address"]
                if not address:
                    raise ValueError("No --lan-address configured")
                status, headers = request(name, address, http_port, path="/.well-known/pistol-lecap")
                if status != 200 or headers.get("X-Pistol-LECAP") != fingerprint(access):
                    raise ValueError("No matching listener on the configured LAN address")
                return f"Proxy reachable on {address} from this computer"
            attempt(f"LAN listener {name}", lan, "Set an assigned private --lan-address and restart --serve.")
    return {"passed": all(c["ok"] for c in checks), "checks": checks,
            "notes": ["LAN client DNS, firewall reachability and device/browser trust must also be checked on each client."] if access.values["lan"] else []}
