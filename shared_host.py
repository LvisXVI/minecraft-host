"""Stopped-server snapshots shared between two trusted Syncthing devices."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.request import Request, urlopen

DEVICE_ID = re.compile(r"^[A-Z2-7]{7}(?:-[A-Z2-7]{7}){7}$")
EXCLUDE_DIRS = {"logs", "crash-reports", ".git", ".cache"}
EXCLUDE_FILES = {"session.lock", "usercache.json", "banned-ips.json.tmp"}


def safe_relative(path: str) -> bool:
    value = Path(path)
    return (bool(path) and not value.is_absolute() and not path.startswith(("/", "\\"))
            and ":" not in path and ".." not in value.parts)


class SharedHost:
    def __init__(self, data_dir: Path, exe_dir: Path, api_port: int = 18384):
        self.data_dir = data_dir
        self.exe_dir = exe_dir
        self.home = data_dir / "syncthing"
        self.shared_dir = data_dir / "shared"
        self.api_port = api_port
        self._key = ""
        self._process = None
        self._lock = threading.RLock()

    def _exe(self) -> Path:
        path = self.exe_dir / "syncthing.exe"
        if not path.is_file():
            raise ValueError("syncthing.exe ist in diesem Minecraft-Host-Paket nicht enthalten.")
        return path

    def ensure(self) -> str:
        """Start the private local REST listener; do not expose its key or GUI."""
        exe = self._exe()
        self.home.mkdir(parents=True, exist_ok=True)
        config = self.home / "config.xml"
        if not config.is_file():
            result = subprocess.run([str(exe), "generate", "-H", str(self.home)],
                                    capture_output=True, text=True, timeout=30)
            if result.returncode or not config.is_file():
                raise ValueError("Syncthing konnte nicht eingerichtet werden.")
        self._key = ET.parse(config).findtext("./gui/apikey") or ""
        if not self._key:
            raise ValueError("Syncthing-API-Schlüssel fehlt.")
        try:
            return self.api("GET", "/rest/system/status")["myID"]
        except Exception:
            pass
        with self._lock:
            if not self._process or self._process.poll() is not None:
                flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                self._process = subprocess.Popen(
                    [str(exe), "serve", "-H", str(self.home), f"--gui-address=127.0.0.1:{self.api_port}",
                     "--no-browser", "--no-upgrade", "--log-level=WARN"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
        for _ in range(40):
            try:
                return self.api("GET", "/rest/system/status")["myID"]
            except Exception:
                time.sleep(0.25)
        raise ValueError("Syncthing antwortet nicht auf diesem PC.")

    def stop(self) -> None:
        """Stop the Syncthing process this app started (on exit and before updates)."""
        with self._lock:
            proc, self._process = self._process, None
        if proc and proc.poll() is None:
            try:
                self.api("POST", "/rest/system/shutdown")
                proc.wait(timeout=10)
            except Exception:
                proc.terminate()

    def api(self, method: str, path: str, data=None):
        payload = None if data is None else json.dumps(data).encode("utf-8")
        request = Request(f"http://127.0.0.1:{self.api_port}" + path, data=payload, method=method,
                          headers={"X-API-Key": self._key, "Content-Type": "application/json"})
        with urlopen(request, timeout=12) as response:
            body = response.read()
        return json.loads(body) if body else {}

    def _folder(self, share_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{24}", share_id):
            raise ValueError("Ungültige Freigabe-ID.")
        return self.shared_dir / share_id

    def _configure(self, share_id: str, peer: str | None, label: str) -> None:
        own = self.ensure()
        if peer and not DEVICE_ID.fullmatch(peer):
            raise ValueError("Ungültige Geräte-ID.")
        if peer and peer != own:
            devices = self.api("GET", "/rest/config/devices")
            if not any(d["deviceID"] == peer for d in devices):
                default = self.api("GET", "/rest/config/defaults/device")
                default.update(deviceID=peer, name="Minecraft Host Partner")
                self.api("POST", "/rest/config/devices", default)
        folders = self.api("GET", "/rest/config/folders")
        folder_id = "mch-" + share_id
        existing = next((f for f in folders if f["id"] == folder_id), None)
        if existing:
            folder = existing
        else:
            folder = self.api("GET", "/rest/config/defaults/folder")
            folder.update(id=folder_id, label=label[:64], path=str(self._folder(share_id)),
                          type="sendreceive", rescanIntervalS=30, fsWatcherEnabled=True,
                          maxConflicts=0)
        folder["devices"] = [{"deviceID": d} for d in dict.fromkeys([own] + ([peer] if peer and peer != own else []))]
        self._folder(share_id).mkdir(parents=True, exist_ok=True)
        self.api("PUT" if existing else "POST", "/rest/config/folders/" + folder_id if existing else "/rest/config/folders", folder)

    def invite(self, p: dict) -> str:
        if p.get("shared_role") and p.get("shared_role") != "owner":
            raise ValueError("Dieser Server ist bereits geteilt.")
        own = self.ensure()
        if p.get("shared_role") == "owner":
            share_id = p["shared_id"]
        else:
            share_id = uuid.uuid4().hex[:24]
            self._configure(share_id, None, p["name"])
            p.update(shared_id=share_id, shared_role="owner", shared_peer="", shared_revision=0,
                     shared_applied="", shared_status="Warte auf die Geräte-ID deines Freundes.")
        payload = {"v": 1, "share": share_id, "owner": own,
                   "name": p["name"], "minecraft": p["minecraft"],
                   "loader": p["loader"], "loader_version": p.get("loader_version", ""),
                   "ram_mb": p["ram_mb"], "port": p["port"]}
        return "MCH1-" + base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")

    def join(self, code: str, profiles: list[dict]) -> tuple[dict, str]:
        if not code.startswith("MCH1-") or len(code) > 3000:
            raise ValueError("Ungültiger Einladungscode.")
        try:
            raw = code[5:]
            data = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        except (ValueError, UnicodeError):
            raise ValueError("Einladungscode kann nicht gelesen werden.")
        if data.get("v") != 1 or not DEVICE_ID.fullmatch(str(data.get("owner", ""))):
            raise ValueError("Einladungscode ist ungültig.")
        share_id = str(data.get("share", ""))
        self._folder(share_id)
        if any(p.get("shared_id") == share_id for p in profiles):
            raise ValueError("Dieser Server ist bereits eingebunden.")
        for field in ("name", "minecraft", "loader"):
            if not isinstance(data.get(field), str) or len(data[field]) > 80 or not data[field]:
                raise ValueError("Serverdaten im Einladungscode sind ungültig.")
        if data["loader"] not in ("fabric", "forge", "neoforge", "vanilla"):
            raise ValueError("Servertyp im Einladungscode ist ungültig.")
        own = self.ensure()
        if own == data["owner"]:
            raise ValueError("Du kannst die eigene Einladung nicht öffnen.")
        self._configure(share_id, data["owner"], data["name"])
        p = {"id": uuid.uuid4().hex[:12], "name": data["name"], "minecraft": data["minecraft"],
             "loader": data["loader"], "loader_version": str(data.get("loader_version", ""))[:40],
             "ram_mb": max(1024, min(65536, int(data.get("ram_mb", 4096)))),
             "port": max(1024, min(65535, int(data.get("port", 25565)))),
             "java_path": "", "public_address": "", "eula": False, "installed": False, "jar": "",
             "shared_id": share_id, "shared_role": "guest", "shared_peer": data["owner"],
             "shared_revision": 0, "shared_applied": "", "shared_status": "Warte auf Serverdateien."}
        return p, own

    def accept_peer(self, p: dict, device_id: str) -> None:
        if p.get("shared_role") != "owner":
            raise ValueError("Nur der Hauptrechner kann den Partner bestätigen.")
        device_id = device_id.strip().upper()
        self._configure(p["shared_id"], device_id, p["name"])
        p["shared_peer"] = device_id
        p["shared_status"] = "Geräte verbunden. Server stoppen, um die Welt zu übertragen."

    def _files(self, root: Path):
        for base, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS and not d.startswith(".sync")]
            for name in files:
                if name in EXCLUDE_FILES or name.endswith((".tmp", ".part")):
                    continue
                source = Path(base) / name
                if source.is_symlink():
                    continue
                yield source.relative_to(root).as_posix(), source

    @staticmethod
    def _hash(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                h.update(block)
        return h.hexdigest()

    def publish(self, p: dict, server_root: Path) -> dict:
        if not p.get("shared_peer"):
            raise ValueError("Bitte zuerst den Partner verbinden.")
        share = self._folder(p["shared_id"])
        snapshot = share / "snapshot"
        snapshot.mkdir(parents=True, exist_ok=True)
        previous = self.manifest(p)
        entries = {}
        for relative, source in self._files(server_root):
            if relative == "manifest.json":
                continue
            destination = snapshot / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            digest = self._hash(source)
            entries[relative] = {"sha256": digest, "size": source.stat().st_size}
            if not destination.is_file() or self._hash(destination) != digest:
                temporary = destination.with_name(destination.name + ".mchtmp")
                shutil.copy2(source, temporary)
                temporary.replace(destination)
        old = set(previous.get("files", {})) if previous else set()
        for relative in old - set(entries):
            if safe_relative(relative):
                (snapshot / relative).unlink(missing_ok=True)
        manifest = {"format": 1, "revision": max(int(p.get("shared_revision", 0)),
                    int(previous.get("revision", 0)) if previous else 0) + 1,
                    "id": uuid.uuid4().hex, "author": self.ensure(), "created": time.time(),
                    "profile": {k: p.get(k) for k in ("jar", "args_file", "minecraft", "loader", "loader_version")},
                    "files": entries}
        tmp = share / "manifest.json.tmp"
        tmp.write_text(json.dumps(manifest, separators=(",", ":")), encoding="utf-8")
        tmp.replace(share / "manifest.json")
        p.update(shared_revision=manifest["revision"], shared_applied=manifest["id"],
                 shared_files=list(entries),
                 shared_status=f"Weltstand {manifest['revision']} wird synchronisiert.")
        return manifest

    def manifest(self, p: dict) -> dict | None:
        path = self._folder(p["shared_id"]) / "manifest.json"
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("format") != 1 or not isinstance(data.get("files"), dict):
            raise ValueError("Ungültiger Weltstand.")
        if len(data["files"]) > 50000 or any(not safe_relative(p) for p in data["files"]):
            raise ValueError("Unsichere Pfade im Weltstand.")
        return data

    def complete(self, p: dict, manifest: dict) -> bool:
        root = self._folder(p["shared_id"]) / "snapshot"
        for relative, info in manifest["files"].items():
            path = root / relative
            if (not isinstance(relative, str) or not safe_relative(relative)
                    or ":" in relative or not isinstance(info, dict) or not path.is_file()
                    or path.stat().st_size != info.get("size") or self._hash(path) != info.get("sha256")):
                return False
        return True

    def peer_online(self, p: dict) -> bool:
        if not p.get("shared_peer"):
            return False
        try:
            self.ensure()
            connections = self.api("GET", "/rest/system/connections").get("connections", {})
            return bool(connections.get(p["shared_peer"], {}).get("connected"))
        except Exception:
            return False

    def peer_active(self, p: dict) -> bool:
        path = self._folder(p["shared_id"]) / "active.json"
        try:
            active = json.loads(path.read_text(encoding="utf-8"))
            return (active.get("device") == p.get("shared_peer") and
                    active.get("share") == p["shared_id"] and
                    (self.peer_online(p) or time.time() - float(active.get("time", 0)) < 60))
        except (OSError, ValueError, TypeError):
            return False

    def mark_active(self, p: dict) -> None:
        own = self.ensure()
        path = self._folder(p["shared_id"]) / "active.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"device": own, "share": p["shared_id"],
                                   "time": time.time()}), encoding="utf-8")
        tmp.replace(path)

    def mark_stopped(self, p: dict) -> None:
        path = self._folder(p["shared_id"]) / "active.json"
        try:
            active = json.loads(path.read_text(encoding="utf-8"))
            if active.get("device") == self.ensure():
                path.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass

    def sync_complete(self, p: dict) -> bool:
        try:
            self.ensure()
            status = self.api("GET", "/rest/db/status?folder=mch-" + p["shared_id"])
            return status.get("needTotalItems", 0) == 0 and status.get("state") not in ("error", "scanning")
        except Exception:
            return False

    def claim(self, p: dict, server_root: Path) -> None:
        self.ensure()
        if not p.get("shared_peer"):
            raise ValueError("Der Partner ist noch nicht verbunden.")
        if p.get("shared_role") == "guest" and self.peer_online(p):
            raise ValueError("Der Hauptrechner ist online. Dein Freund kann dort den Server starten.")
        if self.peer_active(p):
            raise ValueError("Der Server läuft noch auf dem anderen PC. Warte nach dessen Stopp bis zu 60 Sekunden.")
        if not self.sync_complete(p):
            raise ValueError("Die Welt wird noch übertragen. Bitte kurz warten.")
        manifest = self.manifest(p)
        if not manifest:
            raise ValueError("Noch kein Weltstand übertragen. Server auf dem Hauptrechner stoppen.")
        if int(manifest.get("revision", -1)) < int(p.get("shared_revision", 0)):
            raise ValueError("Der empfangene Weltstand ist älter als die lokale Welt.")
        if not self.complete(p, manifest):
            raise ValueError("Der Weltstand ist noch nicht vollständig synchronisiert.")
        if manifest["id"] != p.get("shared_applied"):
            snapshot = self._folder(p["shared_id"]) / "snapshot"
            server_root.mkdir(parents=True, exist_ok=True)
            for relative in manifest["files"]:
                source = snapshot / relative
                destination = server_root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                temporary = destination.with_name(destination.name + ".mchimport")
                shutil.copy2(source, temporary)
                temporary.replace(destination)
            old = p.get("shared_files", [])
            for relative in old:
                if relative not in manifest["files"] and safe_relative(relative):
                    (server_root / relative).unlink(missing_ok=True)
            p.update(shared_applied=manifest["id"], shared_revision=manifest["revision"],
                     shared_files=list(manifest["files"]), installed=True,
                     jar=manifest["profile"].get("jar", ""), args_file=manifest["profile"].get("args_file", ""),
                     shared_status=f"Weltstand {manifest['revision']} bereit.")

    def info(self, p: dict) -> dict:
        if not p.get("shared_role"):
            return {}
        try:
            self.ensure()
            manifest = self.manifest(p)
            return {"device_id": self.api("GET", "/rest/system/status")["myID"],
                    "peer_online": self.peer_online(p), "sync_complete": self.sync_complete(p),
                    "revision": manifest.get("revision") if manifest else None,
                    "snapshot_ready": bool(manifest), "peer_active": self.peer_active(p)}
        except Exception as ex:
            return {"error": str(ex)}
