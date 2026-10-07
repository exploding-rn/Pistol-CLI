from contextlib import contextmanager
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
import ssl
import threading

import pytest

from pistol import certificates, chamber, lecap
from pistol.config import PistolError
from pistol.lecap_proxy import Proxy, fingerprint, lan_peer, request


@contextmanager
def running(project, *, https=False):
    class App(BaseHTTPRequestHandler):
        def do_HEAD(self):
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()
        def do_GET(self):
            body = self.path.encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Test-Host", self.headers["Host"])
            self.send_header("X-Test-Proto", self.headers["X-Forwarded-Proto"])
            self.end_headers()
            self.wfile.write(body)
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(201)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    app = ThreadingHTTPServer(("127.0.0.1", 0), App)
    thread = threading.Thread(target=app.serve_forever, daemon=True)
    thread.start()
    item = chamber.create("api", project, create_venv=False)
    access = lecap.effective(item, {"domain": "app.test", "https": https, "port": app.server_port})
    proxy = Proxy([access], http_port=0, https_port=0)
    try:
        proxy.start()
        yield access, proxy
    finally:
        proxy.close()
        app.shutdown()
        app.server_close()
        thread.join()


def test_real_http_routing_preserves_path_query_body_and_rejects_unknown_host(project):
    with running(project) as (access, proxy):
        port = proxy.servers[0].server_port
        connection = http.client.HTTPConnection("127.0.0.1", port)
        connection.request("GET", "/v1?a=1", headers={"Host": "app.test", "X-Forwarded-Proto": "spoofed"})
        response = connection.getresponse()
        assert response.status == 200
        assert response.read() == b"/v1?a=1"
        assert response.getheader("X-Pistol-Route") == fingerprint(access)
        assert response.getheader("X-Test-Host") == "app.test"
        assert response.getheader("X-Test-Proto") == "http"
        connection.close()
        connection = http.client.HTTPConnection("127.0.0.1", port)
        connection.request("POST", "/v1", body=b"hello", headers={"Host": "app.test"})
        response = connection.getresponse()
        assert response.status == 201 and response.read() == b"hello"
        connection.close()
        assert request("unknown.test", "127.0.0.1", port)[0] == 421


def test_real_tls_verified_ca_hostname_and_redirect(project):
    with running(project, https=True) as (access, proxy):
        http_port, tls_port = [s.server_port for s in proxy.servers]
        status, headers = request("app.test", "127.0.0.1", http_port, path="/v1?a=1")
        assert status == 308 and headers["Location"] == "https://app.test/v1?a=1"
        context = ssl.create_default_context(cafile=str(certificates.directory() / "ca.pem"))
        status, headers = request("app.test", "127.0.0.1", tls_port, context=context, path="/v1")
        assert status == 200 and headers["X-Pistol-Route"] == fingerprint(access)
        with pytest.raises(ssl.SSLError):
            request("app.test", "127.0.0.1", tls_port, context=ssl.create_default_context())
        with pytest.raises(ssl.SSLError):
            request("wrong.test", "127.0.0.1", tls_port, context=context)


def test_requires_explicit_assigned_lan_address(project):
    item = chamber.create("api", project, create_venv=False)
    proxy = Proxy([lecap.effective(item, {"domain": "app.test", "lan": True})], http_port=0)
    with pytest.raises(PistolError, match="lan-address"):
        proxy.start()
    assert not proxy.servers


def test_public_peers_rejected():
    assert lan_peer("127.0.0.1")
    assert lan_peer("192.168.1.5")
    assert not lan_peer("8.8.8.8")


def test_verification_checks_real_tls_and_reports_unenrolled_trust(project, monkeypatch):
    from pistol import lecap_verify
    # DNS is the only network layer simulated; no system hosts entries are installed.
    monkeypatch.setattr(lecap_verify, "inspect", lambda access: [{"name": "app.test", "addresses": ["127.0.0.1"], "expected": ["127.0.0.1"], "ok": True}])
    with running(project, https=True) as (access, proxy):
        result = lecap_verify.verify(access, http_port=proxy.servers[0].server_port, https_port=proxy.servers[1].server_port)
        failures = [c for c in result["checks"] if not c["ok"]]
        assert not result["passed"]
        assert len(failures) == 1
        assert failures[0]["check"] == "system certificate trust app.test"
        assert any(c["check"] == "reverse proxy route app.test" and c["ok"] for c in result["checks"])


def test_http_verification_success(project, monkeypatch):
    from pistol import lecap_verify
    monkeypatch.setattr(lecap_verify, "inspect", lambda access: [{"name": "app.test", "addresses": ["127.0.0.1"], "expected": ["127.0.0.1"], "ok": True}])
    with running(project) as (access, proxy):
        assert lecap_verify.verify(access, http_port=proxy.servers[0].server_port)["passed"]


def test_binding_failure_does_not_leave_partial_listeners(project):
    item = chamber.create("api", project, create_venv=False)
    access = lecap.effective(item, {"domain": "app.test"})
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        proxy = Proxy([access], http_port=occupied.getsockname()[1])
        with pytest.raises(PistolError, match="Cannot bind"):
            proxy.start()
        assert proxy.servers == []


def test_serve_delegates_to_background_lifecycle(monkeypatch):
    from pistol import lecap_proxy, lecap_runtime
    actions = []
    monkeypatch.setattr(lecap_runtime, "control", lambda action: actions.append(action))
    lecap_proxy.serve()
    lecap_proxy.serve("start")
    lecap_proxy.serve("stop")
    assert actions == ["toggle", "start", "stop"]
