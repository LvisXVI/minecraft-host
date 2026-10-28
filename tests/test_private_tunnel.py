import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen

from private_tunnel import PrivateTunnel
import app
import remote


class FakeTailscale:
    def __init__(self):
        self.online = True
        self.ports = {}
        self.commands = []
        self.fail_next = False

    def run(self, argv, **kwargs):
        self.commands.append(argv[1:])
        assert kwargs["capture_output"] and kwargs["timeout"] <= 30
        if self.fail_next:
            self.fail_next = False
            return subprocess.CompletedProcess(argv, 1, "", "CLI error")
        args = argv[1:]
        if args == ["status", "--json"]:
            body = {"BackendState": "Running" if self.online else "NoState",
                    "Self": {"TailscaleIPs": ["100.101.102.103"] if self.online else None,
                             "Online": self.online}}
        elif args == ["serve", "status", "--json"]:
            body = {"TCP": {str(port): {"TCPForward": f"127.0.0.1:{target}"}
                            for port, target in self.ports.items()}}
        elif args[:2] == ["serve", "--bg"]:
            port = int(args[2].split("=")[1])
            target = int(args[3].rsplit(":", 1)[1])
            self.ports[port] = target
            body = None
        elif len(args) == 3 and args[0] == "serve" and args[2] == "off":
            self.ports.pop(int(args[1].split("=")[1]), None)
            body = None
        else:
            raise AssertionError(args)
        return subprocess.CompletedProcess(argv, 0, json.dumps(body) if body is not None else "", "")


class PrivateTunnelTests(unittest.TestCase):
    def setUp(self):
        self.cli = FakeTailscale()
        self.tunnel = PrivateTunnel(executable="tailscale.exe", runner=self.cli.run)
        self.profile = {"id": "abc123", "port": 25565, "minekube_enabled": False}
        self.records = {}

    def test_create_and_remove_only_owned_port(self):
        self.cli.ports[30000] = 8080
        info = self.tunnel.create(self.profile, self.records)
        self.assertEqual(info["address"], "100.101.102.103:25565")
        self.assertTrue(info["active"])
        self.assertEqual(self.cli.ports, {30000: 8080, 25565: 25565})
        self.assertIn(["serve", "--bg", "--tcp=25565", "tcp://127.0.0.1:25565"], self.cli.commands)
        self.tunnel.remove(self.profile, self.records)
        self.assertEqual(self.cli.ports, {30000: 8080})
        self.assertEqual(self.records, {})

    def test_conflict_selects_another_listen_port(self):
        self.cli.ports[25565] = 9000
        info = self.tunnel.create(self.profile, self.records)
        self.assertEqual(info["address"], "100.101.102.103:25566")
        self.assertEqual(self.cli.ports[25565], 9000)
        self.assertEqual(self.records["abc123"]["listen_port"], 25566)

    def test_offline_and_gate_do_not_change_serve(self):
        self.cli.online = False
        with self.assertRaisesRegex(ValueError, "verbunden"):
            self.tunnel.create(self.profile, self.records)
        self.assertEqual(self.cli.ports, {})
        self.cli.online = True
        self.profile["minekube_enabled"] = True
        with self.assertRaisesRegex(ValueError, "Minekube"):
            self.tunnel.create(self.profile, self.records)
        self.assertEqual(self.cli.ports, {})

    def test_remove_refuses_to_change_another_service(self):
        self.tunnel.create(self.profile, self.records)
        self.cli.ports[25565] = 12345
        with self.assertRaisesRegex(ValueError, "fremde"):
            self.tunnel.remove(self.profile, self.records)
        self.assertEqual(self.cli.ports[25565], 12345)
        self.assertIn("abc123", self.records)

    def test_retarget_preserves_friend_address(self):
        self.tunnel.create(self.profile, self.records)
        self.tunnel.retarget(self.profile, self.records, 25570)
        self.profile["port"] = 25570
        self.assertEqual(self.cli.ports[25565], 25570)
        self.assertEqual(self.records["abc123"]["target_port"], 25570)
        self.assertEqual(self.tunnel.for_profile(self.records["abc123"], self.profile,
                                                 self.tunnel.status(force=True))["address"],
                         "100.101.102.103:25565")

    def test_retarget_cli_failure_preserves_old_port(self):
        self.tunnel.create(self.profile, self.records)
        self.cli.fail_next = True
        with self.assertRaisesRegex(ValueError, "CLI error"):
            self.tunnel.retarget(self.profile, self.records, 25570)
        self.assertEqual(self.cli.ports[25565], 25565)
        self.assertEqual(self.records["abc123"]["target_port"], 25565)

    def test_cli_failure_does_not_record_tunnel(self):
        self.cli.fail_next = True
        with self.assertRaisesRegex(ValueError, "CLI error"):
            self.tunnel.create(self.profile, self.records)
        self.assertEqual(self.records, {})

    def test_remote_control_cannot_toggle_private_tunnel(self):
        self.assertFalse(remote.allowed("POST", "/api/private-tunnel/check"))
        self.assertFalse(remote.allowed("POST", "/api/profiles/abcdef123456/private_tunnel_start"))
        self.assertFalse(remote.allowed("POST", "/api/profiles/abcdef123456/private_tunnel_stop"))

    def test_local_http_start_and_stop_persist_record(self):
        with tempfile.TemporaryDirectory() as folder:
            config = {"profiles": [self.profile], "private_tunnels": {}}
            server = app.LocalHTTPServer(("127.0.0.1", 0), app.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            with patch.object(app, "PORT", server.server_port), patch.object(app, "CONFIG", config), \
                 patch.object(app, "DATA_DIR", Path(folder)), \
                 patch.object(app, "CONFIG_FILE", Path(folder) / "config.json"), \
                 patch.object(app, "PRIVATE_TUNNEL", self.tunnel):
                thread.start()
                try:
                    def post(path):
                        request = Request(f"http://127.0.0.1:{server.server_port}{path}",
                                          data=b"{}", headers={"Content-Type": "application/json"})
                        with urlopen(request, timeout=5) as response:
                            return json.load(response)

                    created = post("/api/profiles/abc123/private_tunnel_start")
                    self.assertEqual(created["address"], "100.101.102.103:25565")
                    self.assertEqual(json.loads((Path(folder) / "config.json").read_text())["private_tunnels"],
                                     {"abc123": {"listen_port": 25565, "target_port": 25565}})
                    checked = post("/api/private-tunnel/check")
                    self.assertTrue(checked["online"])
                    self.assertNotIn("ports", checked)
                    post("/api/profiles/abc123/private_tunnel_stop")
                    self.assertEqual(json.loads((Path(folder) / "config.json").read_text())["private_tunnels"], {})
                    self.assertEqual(self.cli.ports, {})
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
