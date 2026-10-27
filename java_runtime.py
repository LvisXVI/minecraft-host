"""Download and verify a private Temurin runtime for Minecraft Host."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import threading
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

SUPPORTED = (8, 17, 21, 25)
MAX_DOWNLOAD = 800 * 1024**2
MAX_UNPACKED = 2 * 1024**3
INSTALL_LOCK = threading.RLock()


def runtime_path(root: Path, major: int) -> Path:
    return root / f"temurin-{major}" / "bin" / "java.exe"


def _metadata(major: int) -> tuple[str, str, int]:
    if major not in SUPPORTED:
        raise ValueError(f"Automatischer Java-Download für Version {major} ist nicht verfügbar.")
    query = urlencode({"architecture": "x64", "heap_size": "normal", "image_type": "jdk",
                       "jvm_impl": "hotspot", "os": "windows", "vendor": "eclipse"})
    url = f"https://api.adoptium.net/v3/assets/latest/{major}/hotspot?{query}"
    req = Request(url, headers={"User-Agent": "MinecraftHost/1.2", "Accept": "application/json"})
    with urlopen(req, timeout=30) as response:
        assets = json.load(response)
    try:
        asset = next(a for a in assets if a["version"]["major"] == major
                     and a["binary"]["architecture"] == "x64"
                     and a["binary"]["os"] == "windows"
                     and a["binary"]["image_type"] == "jdk")
        package = asset["binary"]["package"]
        link, checksum, size = package["link"], package["checksum"], int(package["size"])
    except (KeyError, TypeError, ValueError, StopIteration) as ex:
        raise ValueError(f"Keine passende Java-{major}-Laufzeit bei Adoptium gefunden.") from ex
    parsed = urlparse(link)
    if (parsed.scheme != "https" or parsed.hostname != "github.com"
            or not parsed.path.startswith(f"/adoptium/temurin{major}-binaries/releases/download/")
            or not parsed.path.lower().endswith(".zip")
            or not re.fullmatch(r"[0-9a-fA-F]{64}", checksum)
            or not 1 <= size <= MAX_DOWNLOAD):
        raise ValueError("Java-Downloadmetadaten sind ungültig.")
    return link, checksum.lower(), size


def _download(link: str, checksum: str, expected_size: int, target: Path, progress) -> None:
    digest = hashlib.sha256()
    received = 0
    req = Request(link, headers={"User-Agent": "MinecraftHost/1.2"})
    try:
        with urlopen(req, timeout=60) as response, target.open("wb") as out:
            if urlparse(response.url).scheme != "https":
                raise ValueError("Java-Download wurde unsicher umgeleitet.")
            while chunk := response.read(1024 * 1024):
                received += len(chunk)
                if received > expected_size or received > MAX_DOWNLOAD:
                    raise ValueError("Java-Download ist größer als angekündigt.")
                digest.update(chunk)
                out.write(chunk)
                progress(f"Java wird geladen: {received * 100 // expected_size} %")
        if received != expected_size or digest.hexdigest() != checksum:
            raise ValueError("Java-Download ist unvollständig oder die SHA-256-Prüfung ist fehlgeschlagen.")
    except Exception:
        target.unlink(missing_ok=True)
        raise


def _extract(archive: Path, stage: Path, progress) -> Path:
    progress("Java wird eingerichtet …")
    total = 0
    roots: set[str] = set()
    with zipfile.ZipFile(archive) as zf:
        entries = zf.infolist()
        if len(entries) > 20000:
            raise ValueError("Java-Archiv enthält zu viele Dateien.")
        for entry in entries:
            name = entry.filename.replace("\\", "/")
            relative = PurePosixPath(name)
            if (name.startswith("/") or re.match(r"^[A-Za-z]:", name)
                    or any(part in ("..", "") for part in relative.parts)
                    or entry.external_attr >> 16 & 0o170000 == 0o120000):
                raise ValueError("Java-Archiv enthält einen unsicheren Pfad.")
            roots.add(relative.parts[0])
            if entry.is_dir():
                continue
            total += entry.file_size
            if entry.file_size > MAX_DOWNLOAD or total > MAX_UNPACKED:
                raise ValueError("Java-Archiv ist zu groß.")
            dest = stage.joinpath(*relative.parts)
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(entry) as src, dest.open("wb") as out:
                shutil.copyfileobj(src, out, 1024 * 1024)
    if len(roots) != 1:
        raise ValueError("Java-Archiv hat kein eindeutiges Hauptverzeichnis.")
    java = stage / next(iter(roots)) / "bin" / "java.exe"
    if not java.is_file():
        raise ValueError("java.exe fehlt im Java-Archiv.")
    return java.parent.parent


def _verified_major(java: Path) -> int | None:
    try:
        result = subprocess.run([str(java), "-version"], capture_output=True, text=True, timeout=10)
        match = re.search(r'"(?:1\.)?(\d+)(?:[._+-]|\")', result.stderr + result.stdout)
        return int(match.group(1)) if result.returncode == 0 and match else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def ensure_runtime(root: Path, major: int, progress) -> Path:
    """Return a verified Java executable; download once per major version."""
    with INSTALL_LOCK:
        return _ensure_runtime(root, major, progress)


def _ensure_runtime(root: Path, major: int, progress) -> Path:
    target = runtime_path(root, major)
    if target.is_file():
        if _verified_major(target) == major:
            return target
        raise ValueError(f"Die vorhandene Java-{major}-Laufzeit ist beschädigt. Bitte ihren Ordner entfernen und erneut starten.")
    root.mkdir(parents=True, exist_ok=True)
    link, checksum, size = _metadata(major)
    archive = root / f".temurin-{major}-{uuid.uuid4().hex}.zip"
    stage = root / f".temurin-{major}-{uuid.uuid4().hex}.stage"
    final = target.parent.parent
    try:
        progress(f"Java {major} wird von Eclipse Adoptium geladen …")
        _download(link, checksum, size, archive, progress)
        source = _extract(archive, stage, progress)
        if _verified_major(source / "bin" / "java.exe") != major:
            raise ValueError("Die geladene Java-Version konnte nicht bestätigt werden.")
        if final.exists():
            return target
        source.rename(final)
        return target
    finally:
        archive.unlink(missing_ok=True)
        # Only the randomly named staging directory directly under our runtime root is removed.
        if stage.parent.resolve() == root.resolve() and stage.exists():
            shutil.rmtree(stage)
