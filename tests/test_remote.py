import json
import socket
import sys
import unittest
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import remote  # noqa: E402


class StubHandler(BaseHTTPRequestHandler):
    """Stands in for app.Handler: answers every request it is handed."""

    def log_message(self, *args):
        pass

    def json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.json({"served": self.path})

    def do_POST(self):
        self.json({"served": self.path})


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class RemoteRulesTest(unittest.TestCase):
    def test_allow_list(self):
        self.assertTrue(remote.allowed("GET", "/api/state"))
        self.assertTrue(remote.allowed("POST", "/api/profiles/0123456789ab/start"))
        self.assertTrue(remote.allowed("POST", "/api/profiles/0123456789ab/command"))
        for path in ("/api/update/install", "/api/tunnel/select", "/api/remote/rotate", "/api/remote/config",
                     "/api/profiles/0123456789ab/folder", "/api/profiles/0123456789ab/firewall", "/api/import"):
            self.assertFalse(remote.allowed("POST", path), path)
        self.assertFalse(remote.allowed("GET", "/index.html"))
        self.assertFalse(remote.allowed("DELETE", "/api/state"))

    def test_tokens(self):
        token = remote.new_token()
        self.assertEqual(len(token), remote.TOKEN_LENGTH)
        self.assertTrue(set(token) <= set(remote.TOKEN_ALPHABET))
        shown = remote.format_token(token)
        self.assertTrue(remote.token_matches(token, "Bearer " + shown.lower()))
        self.assertFalse(remote.token_matches(token, "Bearer " + remote.new_token()))
        self.assertFalse(remote.token_matches("", "Bearer "))
        self.assertFalse(remote.token_matches(token, token))

    def test_lockout(self):
        now = [0.0]
        lock = remote.Lockout(limit=3, seconds=60, clock=lambda: now[0])
        for _ in range(3):
            self.assertFalse(lock.blocked("a"))
            lock.fail("a")
        self.assertTrue(lock.blocked("a"))
        self.assertFalse(lock.blocked("b"))
        now[0] = 61
        self.assertFalse(lock.blocked("a"))


class RemoteServerTest(unittest.TestCase):
    def setUp(self):
        self.port = free_port()
        self.token = remote.new_token()
        self.server = remote.RemoteServer(StubHandler)
        self.server.apply({"enabled": True, "port": self.port, "token": self.token})
        self.addCleanup(self.server.stop)

    def call(self, path, token=None, method="GET"):
        req = Request(f"http://127.0.0.1:{self.port}{path}", method=method, data=b"{}" if method == "POST" else None)
        if token:
            req.add_header("Authorization", "Bearer " + token)
        try:
            with urlopen(req, timeout=5) as res:
                return res.status, json.loads(res.read() or b"{}"), res.headers
        except HTTPError as err:
            return err.code, json.loads(err.read() or b"{}"), err.headers

    def test_requires_code_and_allow_list(self):
        self.assertTrue(self.server.running)
        self.assertEqual(self.call("/api/state")[0], 401)
        status, body, headers = self.call("/api/state", remote.format_token(self.token))
        self.assertEqual((status, body["served"]), (200, "/api/state"))
        self.assertEqual(headers["Access-Control-Allow-Origin"], "*")
        self.assertEqual(self.call("/api/update/install", self.token, "POST")[0], 403)
        self.assertEqual(self.call("/api/profiles/0123456789ab/stop", self.token, "POST")[0], 200)
        self.assertEqual(self.call("/api/remote/ping", self.token)[1]["ok"], True)

    def test_blocks_after_wrong_codes(self):
        for _ in range(remote.MAX_FAILURES):
            self.assertEqual(self.call("/api/state", "WRONG")[0], 401)
        self.assertEqual(self.call("/api/state", self.token)[0], 429)

    def test_disable_stops_listener(self):
        self.server.apply({"enabled": False, "port": self.port, "token": self.token})
        self.assertFalse(self.server.running)


if __name__ == "__main__":
    unittest.main()
