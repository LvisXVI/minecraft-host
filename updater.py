"""Self-update for the portable Minecraft Host build.

The app asks GitHub for the latest release, downloads the portable ZIP only after
an explicit click, verifies it against the release's SHA256SUMS.txt and hands the
file swap to a small helper script that runs once this process has exited.
Server data in %LOCALAPPDATA%\\MinecraftHost is never touched.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse
from urllib.request import Request, urlopen

APP_VERSION = "1.1.1"
REPO = "LvisXVI/minecraft-host"
RELEASE_API = f"https://api.github.com/repos/{REPO}/releases/latest"
ASSET_NAME = "MinecraftHost-Portable-Windows.zip"
SUMS_NAME = "SHA256SUMS.txt"
ASSET_PREFIX = f"https://github.com/{REPO}/releases/download/"
MAX_PACKAGE = 400 * 1024**2
CHECK_INTERVAL = 6 * 3600

_lock = threading.Lock()
_status: dict = {"current": APP_VERSION, "available": False, "checked_at": 0}
_installing = False


def parse_version(text: str) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", text or "")
    return tuple(int(n) for n in numbers[:3]) or (0,)


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def install_dir() -> Path:
    return Path(sys.executable).resolve().parent


def _get_json(url: str) -> dict:
    req = Request(url, headers={"User-Agent": f"MinecraftHost/{APP_VERSION}", "Accept": "application/vnd.github+json"})
    with urlopen(req, timeout=20) as response:
        return json.load(response)


def check(force: bool = False) -> dict:
    """Return the cached update status, refreshing it when stale or forced."""
    with _lock:
        if not force and time.time() - _status.get("checked_at", 0) < CHECK_INTERVAL:
            return dict(_status)
    try:
        release = _get_json(RELEASE_API)
        latest = str(release.get("tag_name", "")).lstrip("v")
        assets = {a.get("name"): a.get("browser_download_url", "") for a in release.get("assets", [])}
        result = {
            "current": APP_VERSION, "latest": latest,
            "available": parse_version(latest) > parse_version(APP_VERSION) and ASSET_NAME in assets and SUMS_NAME in assets,
            "notes": str(release.get("body", ""))[:4000], "page": release.get("html_url", ""),
            "package_url": assets.get(ASSET_NAME, ""), "sums_url": assets.get(SUMS_NAME, ""),
            "installable": is_frozen(), "checked_at": time.time(),
        }
    except Exception as ex:  # offline or rate-limited: keep the app usable
        result = {**_status, "current": APP_VERSION, "error": f"Update-Prüfung fehlgeschlagen: {ex}", "checked_at": time.time()}
    with _lock:
        _status.clear()
        _status.update(result)
        return dict(_status)


def start_background_checks() -> None:
    def loop() -> None:
        time.sleep(5)
        while True:
            check(force=True)
            time.sleep(CHECK_INTERVAL)
    threading.Thread(target=loop, daemon=True, name="update-check").start()


def public_status() -> dict:
    with _lock:
        status = {k: v for k, v in _status.items() if k not in ("package_url", "sums_url")}
    status["installing"] = _installing
    return status


def _download(url: str, destination: Path) -> str:
    if not url.startswith(ASSET_PREFIX):
        raise ValueError("Update-Quelle ist kein Release dieses Projekts.")
    digest = hashlib.sha256()
    size = 0
    req = Request(url, headers={"User-Agent": f"MinecraftHost/{APP_VERSION}"})
    with urlopen(req, timeout=60) as response, destination.open("wb") as out:
        if urlparse(response.url).scheme != "https":
            raise ValueError("Update wurde auf eine unsichere Adresse umgeleitet.")
        while chunk := response.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_PACKAGE:
                raise ValueError("Das Update-Paket ist unerwartet groß.")
            out.write(chunk)
            digest.update(chunk)
    return digest.hexdigest().lower()


def expected_sha256(sums_text: str, name: str = ASSET_NAME) -> str:
    for line in sums_text.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[-1].lstrip("*").split("/")[-1] == name and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            return parts[0].lower()
    raise ValueError(f"{name} fehlt in {SUMS_NAME}.")


def _extract(package: Path, target: Path) -> None:
    with zipfile.ZipFile(package) as zf:
        for info in zf.infolist():
            name = PurePosixPath(info.filename.replace("\\", "/"))
            if name.is_absolute() or ".." in name.parts or (name.parts and ":" in name.parts[0]):
                raise ValueError(f"Unsicherer Pfad im Update: {info.filename}")
            out = target.joinpath(*name.parts)
            if info.is_dir():
                out.mkdir(parents=True, exist_ok=True)
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, out.open("wb") as dst:
                shutil.copyfileobj(src, dst)
    if not (target / "MinecraftHost.exe").is_file():
        raise ValueError("Das Update enthält keine MinecraftHost.exe.")


def helper_script(staging: Path, destination: Path, pid: int, exe: Path, log: Path) -> str:
    """Batch script: wait for this process, copy the new files over, restart without a new browser tab."""
    return "\r\n".join([
        "@echo off",
        "setlocal",
        # keep the data folder and port of this run for the restarted app
        *[f'set "{name}={os.environ[name]}"' for name in ("LOCALAPPDATA", "MINECRAFT_HOST_PORT") if os.environ.get(name)],
        f'echo [%date% %time%] waiting for process {pid} > "{log}.steps"',
        # The helper has no console, so console tools (timeout, find on a pipe, PowerShell) are unreliable.
        # A running EXE cannot be opened for writing: retry that once a second, for at most 120 s.
        "set /a tries=0",
        ":wait",
        f'2>nul (>>"{exe}" (call )) && goto ready',
        "set /a tries+=1",
        "if %tries% GEQ 120 goto ready",
        "ping -n 2 127.0.0.1 > nul",
        "goto wait",
        ":ready",
        # robocopy retries while a bundled tool still holds its file; exit codes < 8 mean success
        f'echo [%date% %time%] copying files >> "{log}.steps"',
        f'robocopy "{staging}" "{destination}" /E /R:30 /W:2 /NFL /NDL /NJH /NJS /NP < nul > "{log}" 2>&1',
        f'echo [%date% %time%] robocopy exit %ERRORLEVEL% >> "{log}.steps"',
        "if %ERRORLEVEL% GEQ 8 goto failed",
        f'rmdir /S /Q "{staging}"',
        f'start "" "{exe}" --no-browser',
        f'echo [%date% %time%] restarted >> "{log}.steps"',
        "exit /b 0",
        ":failed",
        f'echo Update fehlgeschlagen, siehe {log} >> "{log}"',
        f'start "" "{exe}"',
        "exit /b 1",
    ]) + "\r\n"


def launch_helper(script: Path) -> None:
    """Start the swap helper in its own hidden console so it outlives this process.

    A console-less (DETACHED_PROCESS) cmd.exe dies as soon as it runs console tools such
    as robocopy, so the helper gets a real console that is simply never shown.
    """
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0  # SW_HIDE
    base = subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP
    breakaway = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)
    try:
        subprocess.Popen(["cmd.exe", "/c", str(script)], creationflags=base | breakaway, startupinfo=startup, close_fds=True)
    except OSError:  # a job that forbids breakaway
        subprocess.Popen(["cmd.exe", "/c", str(script)], creationflags=base, startupinfo=startup, close_fds=True)


def install(data_dir: Path, busy_reason: str = "") -> dict:
    """Download, verify and stage the update, then start the swap helper. Returns status for the UI."""
    global _installing
    if not is_frozen():
        raise ValueError("Automatische Updates gibt es nur in der EXE-Version. Im Quellcode bitte die neue Version von GitHub laden.")
    if busy_reason:
        raise ValueError(busy_reason)
    status = check(force=True)
    if not status.get("available"):
        raise ValueError("Es ist kein neueres Update verfügbar.")
    with _lock:
        if _installing:
            raise ValueError("Das Update wird bereits installiert.")
        _installing = True
    try:
        root = data_dir / "update"
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        sums = root / SUMS_NAME
        package = root / ASSET_NAME
        _download(status["sums_url"], sums)
        expected = expected_sha256(sums.read_text(encoding="utf-8", errors="replace"))
        actual = _download(status["package_url"], package)
        if actual != expected:
            raise ValueError("SHA-256-Prüfung des Updates fehlgeschlagen. Es wurde nichts verändert.")
        staging = root / "staging"
        _extract(package, staging)
        package.unlink(missing_ok=True)
        script = root / "apply-update.cmd"
        exe = install_dir() / "MinecraftHost.exe"
        script.write_text(helper_script(staging, install_dir(), os.getpid(), exe, root / "update.log"), encoding="utf-8")
        launch_helper(script)
        return {"version": status["latest"]}
    except Exception:
        with _lock:
            _installing = False
        raise
