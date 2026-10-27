"""Minekube setup installs its required mod and prepares a usable Gate config."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import app


class MinekubeSetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.p = {"id": "test-gate", "name": "Test", "loader": "fabric", "minecraft": "26.2",
                  "port": 25566, "installed": True, "eula": True, "ram_mb": 2048}
        for name, value in (("DATA_DIR", root), ("SERVERS_DIR", root / "servers"),
                            ("CONFIG_FILE", root / "config.json"), ("CONFIG", {"profiles": [self.p]})):
            patcher = mock.patch.object(app, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def versions(url):
        slug = "fabric-api" if "fabric-api" in url else "fabricproxy-lite"
        filename = "fabric-api-test.jar" if slug == "fabric-api" else "FabricProxy-Lite-test.jar"
        return [{"version_type": "release", "files": [{"primary": True, "filename": filename,
                 "url": f"https://cdn.modrinth.com/data/{slug}/{filename}", "hashes": {"sha512": "a" * 128}}]}]

    @staticmethod
    def fake_download(url, destination, hashes=None):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"test jar")

    def test_setup_installs_fabric_api_and_starts_gate(self):
        with mock.patch.object(app, "get_json", side_effect=self.versions), \
             mock.patch.object(app, "download", side_effect=self.fake_download), \
             mock.patch.object(app, "start_gate") as start:
            app.setup_minekube(self.p)
        self.assertTrue(self.p["minekube_enabled"])
        self.assertTrue(app.fabric_api_present(self.p))
        self.assertTrue((app.server_dir(self.p) / "mods" / "FabricProxy-Lite-test.jar").is_file())
        self.assertIn('secret = "', (app.server_dir(self.p) / "config" / "FabricProxy-Lite.toml").read_text())
        self.assertTrue(self.p["public_address"].endswith(".play.minekube.net"))
        start.assert_called_once_with(self.p)

    def test_missing_api_version_does_not_enable_proxy(self):
        def versions(url):
            return [] if "fabric-api" in url else self.versions(url)
        with mock.patch.object(app, "get_json", side_effect=versions), \
             mock.patch.object(app, "download", side_effect=self.fake_download), \
             mock.patch.object(app, "start_gate") as start:
            with self.assertRaisesRegex(ValueError, "Fabric API"):
                app.setup_minekube(self.p)
        self.assertFalse(self.p.get("minekube_enabled", False))
        start.assert_not_called()

    def test_backend_is_loopback_only_when_proxied(self):
        self.p["minekube_enabled"] = True
        app.server_dir(self.p).mkdir(parents=True)
        app.write_properties(self.p)
        props = (app.server_dir(self.p) / "server.properties").read_text()
        self.assertIn("server-ip=127.0.0.1", props)
        self.assertIn("online-mode=false", props)
        self.assertIn("enforce-secure-profile=false", props)


if __name__ == "__main__":
    unittest.main()
