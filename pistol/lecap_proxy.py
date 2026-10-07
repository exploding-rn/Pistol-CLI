"""HTTP/1.1 reverse proxy shared by all configured LECAP chambers."""
from __future__ import annotations

import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import socket
import ssl
import threading

from .config import PistolError

HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "transfer-encoding", "upgrade"}
MAX_BODY = 16 * 1024 * 1024


def fingerprint(access) -> str:
    return hashlib.sha256(json.dumps([access.chamber, access.domains, access.values], sort_keys=True).encode()).hexdigest()


def request(name: str, address: str, port: int, *, context=None, path="/", method="GET", timeout=2):
    """Connect to a specific address while verifying the actual DNS name via TLS."""
    connection = http.client.HTTPConnection(address, port, timeout=timeout)
    try:
        connection.connect()
        if context:
            connection.sock = context.wrap_socket(connection.sock, server_hostname=name)
        connection.request(method, path, headers={"Host": name, "Connection": "close"})
        response = connection.getresponse()
        return response.status, dict(response.getheaders())
    finally:
        connection.close()


def probe(access, runtime_state=None) -> str:
    if not access.values["enabled"]:
        return "disabled"
    if access.mode != "proxy":
        return "direct (application owns listener/TLS)"
    from .lecap_runtime import status
    state = status() if runtime_state is None else runtime_state
    if state["active"]:
        return f"active (PID {state['pid']})" + ("; configuration changed, restart required" if state["needs_restart"] else "")
    if state["running"]:
        return f"unhealthy (PID {state['pid']}); {state.get('reason', 'restart required')}"
    return "inactive; " + ("configuration changed, restart required; " if state["needs_restart"] else "") + "run pistol lecap --serve"


def lan_peer(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return ip.is_loopback or any(ip in ipaddress.ip_network(net) for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


class ProxyServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, *args, **kwargs):
        self.slots = threading.BoundedSemaphore(32)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        # Failed handshakes and disconnected clients are diagnosed by --verify;
        # do not emit request-thread tracebacks or client data to the terminal.
        pass

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def get_request(self):
        sock, address = super().get_request()
        sock.settimeout(10)
        if self.tls_context:
            # Handshakes happen in the worker thread, never block the accept loop.
            sock = self.tls_context.wrap_socket(sock, server_side=True, do_handshake_on_connect=False)
        return sock, address


def handler_for(route_map: dict, *, secure: bool = False):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "Pistol-LECAP"
        sys_version = ""

        def log_message(self, *args):
            pass  # No request URLs, query tokens or headers in shared logs.

        def reply(self, status, headers=None):
            self.send_response(status)
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True

        def forward(self):
            hosts = self.headers.get_all("Host", [])
            if len(hosts) != 1:
                return self.reply(400)
            authority = hosts[0].lower()
            if ":" in authority:
                name, port = authority.rsplit(":", 1)
                if port != ("443" if secure else "80"):
                    return self.reply(421)
            else:
                name = authority
            access = route_map.get(name)
            if access is None:
                return self.reply(421)
            remote = self.client_address[0]
            if not ipaddress.ip_address(remote).is_loopback and (not access.values["lan"] or not lan_peer(remote)):
                return self.reply(403)
            if secure and (not access.values["https"] or getattr(self.connection, "lecap_name", None) != name):
                return self.reply(421)
            if not self.path.startswith("/") or self.path.startswith("//"):
                return self.reply(400)
            if self.path == "/.well-known/pistol-lecap":
                return self.reply(200, {"X-Pistol-LECAP": fingerprint(access)})
            if not secure and access.values["https"] and access.values["redirect"]:
                return self.reply(308, {"Location": f"https://{name}{self.path}"})
            if self.headers.get("Upgrade"):
                return self.reply(501)  # No incomplete WebSocket tunneling.
            if self.headers.get_all("Transfer-Encoding"):
                return self.reply(501)
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) > 1 or (lengths and not lengths[0].isascii()) or (lengths and not lengths[0].isdigit()):
                return self.reply(400)
            size = int(lengths[0]) if lengths else 0
            if size > MAX_BODY:
                return self.reply(413)
            body = self.rfile.read(size) if size else None
            if body is not None and len(body) != size:
                return self.reply(400)
            blocked = HOP_HEADERS | {part.strip().lower() for part in self.headers.get("Connection", "").split(",")}
            headers = {key: value for key, value in self.headers.items() if key.lower() not in blocked | {"host", "forwarded", "content-length"} and not key.lower().startswith("x-forwarded-")}
            headers.update({"Host": name, "X-Forwarded-Host": name, "X-Forwarded-Proto": "https" if secure else "http", "X-Forwarded-For": remote, "Connection": "close"})
            backend = http.client.HTTPConnection("127.0.0.1", access.values["port"], timeout=30)
            started = False
            try:
                backend.request(self.command, self.path, body=body, headers=headers)
                response = backend.getresponse()
                self.send_response(response.status, response.reason)
                excluded = HOP_HEADERS | {p.strip().lower() for p in response.getheader("Connection", "").split(",")} | {"x-pistol-route"}
                for key, value in response.getheaders():
                    if key.lower() not in excluded:
                        self.send_header(key, value)
                self.send_header("X-Pistol-Route", fingerprint(access))
                self.send_header("Connection", "close")
                self.end_headers()
                started = True
                if self.command != "HEAD":
                    while chunk := response.read(65536):
                        self.wfile.write(chunk)
            except (OSError, http.client.HTTPException):
                if not started:
                    self.reply(502)
            finally:
                self.close_connection = True
                backend.close()

        do_GET = do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = forward
    return Handler


class Proxy:
    """Also usable with ephemeral ports in non-destructive integration tests."""
    def __init__(self, accesses, *, http_port=80, https_port=443):
        self.accesses = accesses
        self.http_port, self.https_port = http_port, https_port
        self.servers = []
        self.threads = []
        self.contexts = {}
        self.route_map = {name: access for access in accesses for name in access.domains}

    def refresh_certificates(self):
        from .certificates import server_context
        contexts = {}
        for access in self.accesses:
            if access.values["https"]:
                context = server_context(access.domains)
                contexts.update({name: context for name in access.domains})
        self.contexts = contexts

    def start(self):
        import psutil
        assigned = {a.address for addresses in psutil.net_if_addrs().values() for a in addresses}
        bindings = {"127.0.0.1"}
        for access in self.accesses:
            if access.values["lan"]:
                address = access.values["lan_address"]
                if not address or address not in assigned:
                    raise PistolError(f"{access.chamber}: --lan true requires --lan-address set to an assigned private IPv4 interface.")
                bindings.add(address)
        self.refresh_certificates()
        root_context = None
        if self.contexts:
            root_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            root_context.minimum_version = ssl.TLSVersion.TLSv1_2

            def sni(sock, name, context):
                selected = self.contexts.get(name)
                if selected is None:
                    return ssl.ALERT_DESCRIPTION_UNRECOGNIZED_NAME
                sock.context = selected
                sock.lecap_name = name
            root_context.set_servername_callback(sni)
        try:
            for address in sorted(bindings):
                for port, secure in ((self.http_port, False), (self.https_port, True)):
                    if secure and not root_context:
                        continue
                    server = ProxyServer((address, port), handler_for(self.route_map, secure=secure))
                    server.tls_context = root_context if secure else None
                    self.servers.append(server)
            for server in self.servers:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                self.threads.append(thread)
        except OSError as exc:
            self.close()
            raise PistolError(f"Cannot bind LECAP listeners: {exc}. Free ports 80/443; if permission was denied, explicitly run an elevated terminal. No firewall changes were made.") from exc
        return self

    def close(self):
        for index, server in enumerate(self.servers):
            if index < len(self.threads):
                server.shutdown()
            server.server_close()
        for thread in self.threads:
            thread.join(timeout=2)
        self.servers.clear()
        self.threads.clear()


def serve(action="toggle"):
    from .lecap_runtime import control
    return control(action)
