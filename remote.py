"""Remote control for the Minecraft Host Android app.

A second HTTP listener, off by default. It serves only an allow-list of the normal API, and every
request must carry the pairing code as a bearer token. Actions that only make sense in front of
the PC (self-update, file dialogs, opening Explorer, firewall prompts, Syncthing pairing) stay
local. Meant for the home network or a VPN such as Tailscale, not for direct exposure to the
internet: the listener speaks plain HTTP.
"""
from __future__ import annotations

import hmac
import ipaddress
import re
import secrets
import socket
import threading
import time
from http.server import ThreadingHTTPServer
from urllib.parse import urlparse

DEFAULT_PORT = 18766
TOKEN_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I, easy to type on a phone
TOKEN_LENGTH = 24  # 24 × 5 bits = 120 bits
MAX_FAILURES = 8
LOCKOUT_SECONDS = 300

ALLOWED_GET = {"/api/state", "/api/remote/ping"}
ALLOWED_POST = {"/api/create", "/api/modrinth/inspect", "/api/modrinth/import", "/api/tunnel/start",
                "/api/tunnel/stop", "/api/update/check"}
PROFILE_ACTIONS = {"start", "stop", "command", "save", "retry"}
PROFILE_PATH = re.compile(r"^/api/profiles/([0-9a-f]{12})/([a-z_]+)$")


def new_token() -> str:
    return "".join(secrets.choice(TOKEN_ALPHABET) for _ in range(TOKEN_LENGTH))


def format_token(token: str) -> str:
    return "-".join(token[i:i + 4] for i in range(0, len(token), 4))


def normalize_token(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def allowed(method: str, path: str) -> bool:
    if method == "GET":
        return path in ALLOWED_GET
    if method == "POST":
        if path in ALLOWED_POST:
            return True
        match = PROFILE_PATH.match(path)
        return bool(match and match.group(2) in PROFILE_ACTIONS)
    return False


def token_matches(expected: str, header: str) -> bool:
    if not expected or not header.startswith("Bearer "):
        return False
    return hmac.compare_digest(normalize_token(header[7:]).encode(), expected.encode())


class Lockout:
    """Counts failed codes per client address and blocks it for a while after too many."""

    def __init__(self, limit: int = MAX_FAILURES, seconds: float = LOCKOUT_SECONDS, clock=time.monotonic) -> None:
        self.limit, self.seconds, self.clock = limit, seconds, clock
        self.failures: dict[str, list[float]] = {}
        self.lock = threading.Lock()

    def blocked(self, client: str) -> bool:
        with self.lock:
            recent = [t for t in self.failures.get(client, []) if self.clock() - t < self.seconds]
            self.failures[client] = recent
            return len(recent) >= self.limit

    def fail(self, client: str) -> None:
        with self.lock:
            self.failures.setdefault(client, []).append(self.clock())

    def clear(self, client: str) -> None:
        with self.lock:
            self.failures.pop(client, None)


def addresses() -> list[dict]:
    """IPv4 addresses the phone can use, Tailscale first (reachable from anywhere)."""
    found: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(info[4][0])
    except OSError:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("1.1.1.1", 80))
            found.add(sock.getsockname()[0])
    except OSError:
        pass
    tailnet = ipaddress.ip_network("100.64.0.0/10")
    result = []
    for value in found:
        ip = ipaddress.ip_address(value)
        if ip.is_loopback or ip.is_link_local:
            continue
        kind = "tailscale" if ip in tailnet else "lan" if ip.is_private else "other"
        result.append({"address": value, "kind": kind})
    order = {"tailscale": 0, "lan": 1, "other": 2}
    return sorted(result, key=lambda item: (order[item["kind"]], item["address"]))


def make_handler(base):
    """Subclass of the local API handler that enforces the code and the allow-list."""

    class RemoteHandler(base):
        lockout: Lockout
        config: dict

        def end_headers(self) -> None:
            # The app's pages load from the APK, so their origin is not this host.
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Max-Age", "600")
            super().end_headers()

        def do_OPTIONS(self) -> None:
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def authorize(self, method: str) -> bool:
            client = self.client_address[0]
            path = urlparse(self.path).path
            if self.lockout.blocked(client):
                self.json({"error": "Zu viele falsche Codes. Bitte in ein paar Minuten erneut versuchen."}, 429)
                return False
            if not token_matches(self.config.get("token", ""), self.headers.get("Authorization", "")):
                self.lockout.fail(client)
                self.json({"error": "Kopplungscode ungültig."}, 401)
                return False
            self.lockout.clear(client)
            if not allowed(method, path):
                self.json({"error": "Diese Aktion ist nur direkt am PC möglich."}, 403)
                return False
            return True

        def do_GET(self) -> None:
            if not self.authorize("GET"):
                return
            if urlparse(self.path).path == "/api/remote/ping":
                return self.json({"ok": True, "name": socket.gethostname()})
            super().do_GET()

        def do_POST(self) -> None:
            if self.authorize("POST"):
                super().do_POST()

        def allowed_origin(self) -> bool:
            return True  # checked in authorize(); requests come from the app, not a browser tab

    return RemoteHandler


class RemoteServer:
    """Starts and stops the remote listener to match the settings."""

    def __init__(self, base_handler) -> None:
        self.handler = make_handler(base_handler)
        self.handler.lockout = Lockout()
        self.server: ThreadingHTTPServer | None = None
        self.error = ""

    def apply(self, config: dict) -> None:
        self.handler.config = config
        wanted = bool(config.get("enabled") and config.get("token"))
        port = int(config.get("port") or DEFAULT_PORT)
        if self.server and (not wanted or self.server.server_address[1] != port):
            self.stop()
        if wanted and not self.server:
            try:
                self.server = ThreadingHTTPServer(("0.0.0.0", port), self.handler)
                self.server.daemon_threads = True
                threading.Thread(target=self.server.serve_forever, daemon=True).start()
                self.error = ""
            except OSError as ex:
                self.server = None
                self.error = f"Port {port} ist belegt oder gesperrt: {ex}"

    def stop(self) -> None:
        if self.server:
            server, self.server = self.server, None
            server.shutdown()
            server.server_close()

    @property
    def running(self) -> bool:
        return self.server is not None
