"""Private Minecraft TCP forwarding through the installed Tailscale client.

Only ports created by Minecraft Host are changed. Tailscale owns the encrypted
network and any relay; this module never exposes the dashboard or a public port.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path


TAILNET = ipaddress.ip_network("100.64.0.0/10")
FORWARD = re.compile(r"^(?:tcp://)?(?:127\.0\.0\.1|localhost):(\d{1,5})/?$", re.I)


def find_tailscale() -> str:
    found = shutil.which("tailscale.exe") or shutil.which("tailscale")
    paths = [found, Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tailscale" / "tailscale.exe",
             Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Tailscale" / "tailscale.exe",
             Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Tailscale" / "tailscale.exe"]
    return next((str(path) for path in paths if path and Path(path).is_file()), "")


def target_port(handler: object) -> int | None:
    if not isinstance(handler, dict):
        return None
    match = FORWARD.fullmatch(str(handler.get("TCPForward", "")))
    return int(match.group(1)) if match and 1 <= int(match.group(1)) <= 65535 else None


class PrivateTunnel:
    def __init__(self, executable: str | None = None, runner=None, clock=None) -> None:
        self.executable = executable
        self.runner = runner or subprocess.run
        self.clock = clock or time.monotonic
        self._cached: tuple[float, dict] | None = None

    def _command(self, *args: str, timeout: int = 12) -> str:
        exe = self.executable if self.executable is not None else find_tailscale()
        if not exe:
            raise ValueError("Tailscale fehlt. Installiere es auf beiden PCs und melde dich an.")
        try:
            result = self.runner([exe, *args], capture_output=True, text=True, encoding="utf-8",
                                 errors="replace", timeout=timeout,
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except subprocess.TimeoutExpired as error:
            raise ValueError("Tailscale antwortet nicht. Prüfe, ob der Dienst läuft.") from error
        except OSError as error:
            raise ValueError("Tailscale konnte nicht gestartet werden.") from error
        if result.returncode:
            detail = (result.stderr or result.stdout or "").strip().splitlines()
            raise ValueError("Tailscale: " + (detail[-1][:240] if detail else "Befehl fehlgeschlagen."))
        return result.stdout

    def _json(self, *args: str) -> dict:
        try:
            value = json.loads(self._command(*args) or "{}")
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except (json.JSONDecodeError, ValueError) as error:
            if isinstance(error, ValueError) and str(error):
                raise
            raise ValueError("Tailscale lieferte einen ungültigen Status.") from error

    def status(self, force: bool = False) -> dict:
        if not force and self._cached and self.clock() - self._cached[0] < 8:
            return self._cached[1]
        info = {"installed": False, "online": False, "ip": "", "ports": {}, "error": ""}
        exe = self.executable if self.executable is not None else find_tailscale()
        if not exe:
            return info
        info["installed"] = True
        try:
            status = self._json("status", "--json")
            self_info = status.get("Self") or {}
            ips = [str(value) for value in (self_info.get("TailscaleIPs") or [])]
            info["ip"] = next((value for value in ips if ipaddress.ip_address(value) in TAILNET), "")
            info["online"] = status.get("BackendState") == "Running" and bool(info["ip"]) and self_info.get("Online", True) is not False
            if not info["online"]:
                info["error"] = "Tailscale ist noch nicht verbunden. Melde dich in der Tailscale-App an."
            else:
                serve = self._json("serve", "status", "--json")
                info["ports"] = {int(port): target_port(handler) for port, handler in (serve.get("TCP") or {}).items()
                                 if str(port).isdigit() and 1 <= int(port) <= 65535}
        except (ValueError, TypeError) as error:
            info["online"] = False
            info["error"] = str(error)
        self._cached = (self.clock(), info)
        return info

    def for_profile(self, record: dict | None, p: dict, status: dict | None = None) -> dict:
        info = status or self.status()
        listen = int(record.get("listen_port", 0)) if isinstance(record, dict) else 0
        expected = int(record.get("target_port", 0)) if isinstance(record, dict) else 0
        active = bool(info["online"] and listen and info["ports"].get(listen) == expected)
        address = f'{info["ip"]}:{listen}' if active else ""
        return {"configured": bool(listen), "active": active, "address": address,
                "listen_port": listen, "target_port": expected,
                "needs_update": bool(listen and expected != int(p["port"])),
                "error": info["error"] if listen and not active else ""}

    @staticmethod
    def _ready(info: dict) -> None:
        if not info["installed"]:
            raise ValueError("Tailscale fehlt. Installiere es auf beiden PCs und melde dich an.")
        if not info["online"]:
            raise ValueError(info["error"] or "Tailscale ist nicht verbunden.")

    def create(self, p: dict, records: dict) -> dict:
        if p.get("minekube_enabled"):
            raise ValueError("Minekube Gate zuerst ausschalten. Der private Tunnel braucht den direkten Minecraft-Server.")
        pid, target = p["id"], int(p["port"])
        if not 1024 <= target <= 65535:
            raise ValueError("Ungültiger Minecraft-Port.")
        if pid in records:
            return self.for_profile(records[pid], p, self.status(force=True))
        info = self.status(force=True)
        self._ready(info)
        reserved = {int(value["listen_port"]) for value in records.values() if isinstance(value, dict)}
        choices = [target, *range(25565, 25666)]
        listen = next((port for port in choices if port not in reserved and port not in info["ports"]), None)
        if listen is None:
            raise ValueError("Kein freier Tailscale-Port gefunden (25565–25665).")
        self._command("serve", "--bg", f"--tcp={listen}", f"tcp://127.0.0.1:{target}", timeout=30)
        self._cached = None
        verified = self.status(force=True)
        if verified["ports"].get(listen) != target:
            raise ValueError("Der Tailscale-Tunnel konnte nicht bestätigt werden. Prüfe die Tailscale-Freigabe.")
        record = {"listen_port": listen, "target_port": target}
        records[pid] = record
        return self.for_profile(record, p, verified)

    def remove(self, p: dict, records: dict) -> None:
        record = records.get(p["id"])
        if not record:
            return
        listen, target = int(record["listen_port"]), int(record["target_port"])
        info = self.status(force=True)
        self._ready(info)
        actual = info["ports"].get(listen)
        if actual is not None and actual != target:
            raise ValueError("Der Port wird inzwischen anders verwendet. Der fremde Tailscale-Dienst bleibt unverändert.")
        if actual == target:
            self._command("serve", f"--tcp={listen}", "off")
            self._cached = None
            if listen in self.status(force=True)["ports"]:
                raise ValueError("Der Tailscale-Tunnel wurde nicht entfernt.")
        records.pop(p["id"], None)

    def retarget(self, p: dict, records: dict, new_port: int) -> None:
        record = records.get(p["id"])
        if not record or int(record["target_port"]) == new_port:
            return
        listen, old_port = int(record["listen_port"]), int(record["target_port"])
        info = self.status(force=True)
        self._ready(info)
        if info["ports"].get(listen) != old_port:
            raise ValueError("Der bestehende Tailscale-Tunnel wurde verändert. Bitte zuerst prüfen.")
        self._command("serve", "--bg", f"--tcp={listen}", f"tcp://127.0.0.1:{new_port}", timeout=30)
        self._cached = None
        if self.status(force=True)["ports"].get(listen) != new_port:
            try:
                self._command("serve", "--bg", f"--tcp={listen}", f"tcp://127.0.0.1:{old_port}", timeout=30)
            finally:
                self._cached = None
            raise ValueError("Der Tunnel konnte nicht auf den neuen Serverport umgestellt werden.")
        record["target_port"] = new_port
