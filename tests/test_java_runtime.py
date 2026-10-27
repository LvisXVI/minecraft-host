"""Checks for the private Java installer without downloading a full JDK."""
import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import java_runtime


class JavaRuntimeTest(unittest.TestCase):
    def test_metadata_requires_official_zip_and_sha256(self):
        asset = {"version": {"major": 21}, "binary": {"architecture": "x64", "os": "windows",
                 "image_type": "jdk", "package": {"link": "https://github.com/adoptium/temurin21-binaries/releases/download/test/jdk.zip",
                 "checksum": "a" * 64, "size": 123}}}
        with patch.object(java_runtime, "urlopen", return_value=io.BytesIO(__import__("json").dumps([asset]).encode())):
            self.assertEqual(java_runtime._metadata(21), (asset["binary"]["package"]["link"], "a" * 64, 123))
        asset["binary"]["package"]["link"] = "https://example.com/java.zip"
        with patch.object(java_runtime, "urlopen", return_value=io.BytesIO(__import__("json").dumps([asset]).encode())):
            with self.assertRaisesRegex(ValueError, "ungültig"):
                java_runtime._metadata(21)

    def test_download_rejects_bad_sha256_and_removes_file(self):
        class Response(io.BytesIO):
            url = "https://github.com/adoptium/temurin21-binaries/releases/download/test/jdk.zip"
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "java.zip"
            with patch.object(java_runtime, "urlopen", return_value=Response(b"content")):
                with self.assertRaisesRegex(ValueError, "SHA-256"):
                    java_runtime._download("https://github.com/test.zip", "0" * 64, 7, target, lambda _: None)
            self.assertFalse(target.exists())

    def test_extract_blocks_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "jdk.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("../outside.exe", b"bad")
            with self.assertRaisesRegex(ValueError, "unsicheren Pfad"):
                java_runtime._extract(archive, Path(tmp) / "stage", lambda _: None)
            self.assertFalse((Path(tmp) / "outside.exe").exists())


if __name__ == "__main__":
    unittest.main()
