"""Unit tests for the self-updater (run: python -m unittest discover tests)."""
import hashlib
import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import updater  # noqa: E402


def release(tag, assets=("MinecraftHost-Portable-Windows.zip", "SHA256SUMS.txt")):
    base = "https://github.com/LvisXVI/minecraft-host/releases/download/" + tag + "/"
    return {"tag_name": tag, "body": "notes", "html_url": "page", "assets": [{"name": n, "browser_download_url": base + n} for n in assets]}


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        updater._status.clear(); updater._status.update({"current": updater.APP_VERSION, "available": False, "checked_at": 0})
        updater._installing = False

    def test_versions_compare_numerically(self):
        self.assertGreater(updater.parse_version("v1.10.0"), updater.parse_version("1.9.3"))
        self.assertEqual(updater.parse_version("1.1.0"), (1, 1, 0))

    def test_onefile_does_not_offer_portable_in_place_update(self):
        with mock.patch.object(updater, "is_frozen", return_value=True), mock.patch.object(updater, "is_onefile", return_value=True):
            with mock.patch.object(updater, "_get_json", return_value=release("v9.0.0")):
                status = updater.check(force=True)
            self.assertTrue(status["available"])
            self.assertFalse(status["installable"])
            with self.assertRaisesRegex(ValueError, "Standalone"):
                updater.install(Path(tempfile.gettempdir()))

    def test_newer_release_with_assets_is_available(self):
        with mock.patch.object(updater, "_get_json", return_value=release("v9.0.0")):
            status = updater.check(force=True)
        self.assertTrue(status["available"]); self.assertEqual(status["latest"], "9.0.0")
        self.assertNotIn("package_url", updater.public_status())

    def test_same_or_incomplete_release_is_not_available(self):
        with mock.patch.object(updater, "_get_json", return_value=release("v" + updater.APP_VERSION)):
            self.assertFalse(updater.check(force=True)["available"])
        with mock.patch.object(updater, "_get_json", return_value=release("v9.0.0", assets=("other.zip",))):
            self.assertFalse(updater.check(force=True)["available"])

    def test_offline_check_keeps_app_usable(self):
        with mock.patch.object(updater, "_get_json", side_effect=OSError("offline")):
            status = updater.check(force=True)
        self.assertFalse(status["available"]); self.assertIn("offline", status["error"])

    def test_sha_line_parsing(self):
        digest = "a" * 64
        self.assertEqual(updater.expected_sha256(f"{digest} *MinecraftHost-Portable-Windows.zip\n{'b'*64}  other"), digest)
        with self.assertRaises(ValueError):
            updater.expected_sha256("nothing here")

    def test_foreign_download_url_is_rejected(self):
        with self.assertRaises(ValueError):
            updater._download("https://example.com/evil.zip", Path(tempfile.gettempdir()) / "x.zip")

    def test_extract_rejects_traversal_and_requires_exe(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.zip"
            with zipfile.ZipFile(bad, "w") as zf:
                zf.writestr("../escape.txt", "x")
            with self.assertRaises(ValueError):
                updater._extract(bad, Path(tmp) / "out")
            no_exe = Path(tmp) / "noexe.zip"
            with zipfile.ZipFile(no_exe, "w") as zf:
                zf.writestr("README.md", "x")
            with self.assertRaises(ValueError):
                updater._extract(no_exe, Path(tmp) / "out2")

    def test_install_refuses_source_builds_and_busy_servers(self):
        with mock.patch.object(updater, "is_frozen", return_value=False):
            with self.assertRaises(ValueError):
                updater.install(Path(tempfile.gettempdir()))
        with mock.patch.object(updater, "is_frozen", return_value=True):
            with self.assertRaisesRegex(ValueError, "stoppen"):
                updater.install(Path(tempfile.gettempdir()), "Bitte zuerst den Server stoppen: Test.")

    def test_install_aborts_on_checksum_mismatch(self):
        def fake_download(url, destination):
            destination.write_text(f"{'0'*64} *MinecraftHost-Portable-Windows.zip" if url.endswith("SHA256SUMS.txt") else "zip", encoding="utf-8")
            return hashlib.sha256(b"zip").hexdigest()
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(updater, "is_frozen", return_value=True), \
                mock.patch.object(updater, "_get_json", return_value=release("v9.0.0")), \
                mock.patch.object(updater, "_download", side_effect=fake_download), \
                mock.patch("subprocess.Popen") as popen:
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                updater.install(Path(tmp))
            popen.assert_not_called()
            self.assertFalse(updater._installing)

    def test_helper_waits_copies_and_restarts_without_browser(self):
        script = updater.helper_script(Path("C:/s"), Path("C:/d"), 1234, Path("C:/d/MinecraftHost.exe"), Path("C:/l.log"))
        self.assertIn(r'(>>"C:\d\MinecraftHost.exe" (call ))', script); self.assertIn(r'robocopy "C:\s" "C:\d"', script); self.assertIn("--no-browser", script)


if __name__ == "__main__":
    unittest.main()
