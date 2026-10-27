"""Minecraft Host: local-only web UI and Minecraft Java server supervisor."""
from __future__ import annotations

import hashlib
import ctypes
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
import zipfile
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote, urlencode, urlparse
from urllib.request import Request, urlopen
from shared_host import SharedHost
import remote
import updater

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "MinecraftHost"
SERVERS_DIR = DATA_DIR / "servers"
CONFIG_FILE = DATA_DIR / "config.json"
MAX_ARCHIVE = 4 * 1024**3
MAX_UNPACKED = 12 * 1024**3
MAX_FILE = 2 * 1024**3
HOST = "127.0.0.1"
PORT = int(os.environ.get("MINECRAFT_HOST_PORT", "18765"))
LOCK = threading.RLock()
PROCESSES: dict[str, subprocess.Popen] = {}
LOGS: dict[str, deque] = {}
JOBS: dict[str, dict] = {}
TUNNEL: subprocess.Popen | None = None
TUNNEL_CLAIM = ""
TUNNEL_LOGS: deque[str] = deque(maxlen=60)
GATE: subprocess.Popen | None = None
GATE_PROFILE_ID = ""
GATE_CONNECTED = False
GATE_LOGS: deque[str] = deque(maxlen=60)
METRICS: dict[str, dict] = {}
TICK_PENDING: dict[str, float] = {}
STOP_REQUESTED: set[str] = set()
STOP_RESENT: set[str] = set()
READ_THREADS: dict[str, threading.Thread] = {}
SHARED = SharedHost(DATA_DIR, Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else APP_DIR.parent)
SHARED_INFO_CACHE: dict[str, tuple[float, dict]] = {}
SHARED_PUBLISHING: set[str] = set()
SERVER: "LocalHTTPServer | None" = None
WORLD_LOCK = threading.RLock()


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        return {"profiles": [], "playit_path": ""}
    try:
        result = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        return result if isinstance(result, dict) else {"profiles": [], "playit_path": ""}
    except (OSError, ValueError):
        return {"profiles": [], "playit_path": ""}


CONFIG = load_config()
CONFIG.setdefault("profiles", [])
CONFIG.setdefault("playit_path", "")
CONFIG.setdefault("remote", {"enabled": False, "port": remote.DEFAULT_PORT, "token": ""})
REMOTE: "remote.RemoteServer | None" = None
if not CONFIG["playit_path"]:
    bundled_agent = Path(sys.executable).resolve().parent / "playit-agent.exe" if getattr(sys, "frozen", False) else APP_DIR.parent / "playit-agent.exe"
    if bundled_agent.is_file():
        CONFIG["playit_path"] = str(bundled_agent)
JAVA_CACHE: tuple[float, list[dict]] = (0, [])
RADMIN_CACHE: tuple[float, dict] = (0, {})


def save_config() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(CONFIG, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CONFIG_FILE)


def profile(pid: str) -> dict:
    result = next((p for p in CONFIG["profiles"] if p["id"] == pid), None)
    if result is None:
        raise ValueError("Serverprofil nicht gefunden.")
    return result


def server_dir(p: dict) -> Path:
    return SERVERS_DIR / p["id"]


def add_log(pid: str, line: str) -> None:
    with LOCK:
        LOGS.setdefault(pid, deque(maxlen=700)).append(line.rstrip()[:2000])


def read_stream(pid: str, process: subprocess.Popen) -> None:
    try:
        for line in process.stdout:
            add_log(pid, line)
            pending = TICK_PENDING.get(pid, 0)
            if pending and time.monotonic() - pending < 8:
                match = re.search(r"(?:average|avg\.?|durchschnitt|mean)[^\n]{0,80}?([0-9]+(?:[.,][0-9]+)?)\s*ms", line, re.I)
                if not match:
                    match = re.search(r"([0-9]+(?:[.,][0-9]+)?)\s*mspt", line, re.I)
                if match:
                    mspt = float(match.group(1).replace(",", "."))
                    if mspt > 0:
                        with LOCK:
                            METRICS.setdefault(pid, {})["mspt"] = round(mspt, 1)
                            METRICS[pid]["tps_estimate"] = round(min(20.0, 1000.0 / mspt), 1)
                            TICK_PENDING.pop(pid, None)
    except (OSError, ValueError):
        pass
    finally:
        code = process.wait()
        add_log(pid, f"[Minecraft Host] Prozess beendet (Code {code}).")
        with LOCK:
            if PROCESSES.get(pid) is process:
                PROCESSES.pop(pid, None)
            METRICS.pop(pid, None)
            TICK_PENDING.pop(pid, None)
        stop_gate(pid)
        requested_stop = pid in STOP_REQUESTED
        STOP_REQUESTED.discard(pid)
        STOP_RESENT.discard(pid)
        if requested_stop and code == 0:
            try:
                p = profile(pid)
                if p.get("shared_role") and p.get("shared_peer"):
                    SHARED_PUBLISHING.add(pid)
                    add_log(pid, "[Minecraft Host] Gespeicherte Welt wird für den anderen PC vorbereitet …")
                    p["shared_status"] = "Welt wird für den anderen PC vorbereitet …"
                    with WORLD_LOCK:
                        SHARED.publish(p, server_dir(p))
                        SHARED.mark_stopped(p)
                        with LOCK:
                            save_config()
                    add_log(pid, "[Minecraft Host] Weltstand veröffentlicht. Die Übertragung kann noch dauern.")
            except Exception as ex:
                add_log(pid, "[Minecraft Host] Weltübergabe fehlgeschlagen: " + str(ex))
            finally:
                SHARED_PUBLISHING.discard(pid)
        else:
            SHARED_PUBLISHING.discard(pid)


def read_tunnel(process: subprocess.Popen) -> None:
    global TUNNEL_CLAIM
    try:
        for line in process.stdout:
            clean = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", line).strip()
            if not clean:
                continue
            with LOCK:
                TUNNEL_LOGS.append(clean[:1000])
                match = re.search(r"https://playit\.gg/claim/[A-Za-z0-9_-]+", clean)
                if match:
                    TUNNEL_CLAIM = match.group(0)
    except (OSError, ValueError):
        pass


def minekube_dir(p: dict) -> Path:
    return DATA_DIR / "gate" / p["id"]


def gate_local_port(p: dict) -> int:
    return int(p["port"]) - 1 if int(p["port"]) > 1024 else int(p["port"]) + 1


def setup_minekube(p: dict) -> None:
    if p.get("minekube_incompatible"):
        raise ValueError("Dieser Modpack-Server konnte über Minekube Gate nicht betreten werden. Nutze eine direkte TCP-Verbindung oder einen rohen TCP-Tunnel.")
    if p.get("loader") != "fabric" or not p.get("installed"):
        raise ValueError("Minekube ist hier für installierte Fabric-Server verfügbar.")
    if p["id"] in PROCESSES and PROCESSES[p["id"]].poll() is None:
        raise ValueError("Server vor der Minekube-Einrichtung stoppen.")
    versions = get_json("https://api.modrinth.com/v2/project/fabricproxy-lite/version?" + urlencode({
        "game_versions": json.dumps([p["minecraft"]]), "loaders": json.dumps(["fabric"])}))
    version = next((v for v in versions if any(f.get("filename", "").lower().endswith(".jar") for f in v.get("files", []))), None)
    if not version:
        raise ValueError("Für diese Minecraft-Version gibt es noch kein FabricProxy-Lite.")
    file = next(f for f in version["files"] if f.get("filename", "").lower().endswith(".jar"))
    if urlparse(file["url"]).hostname != "cdn.modrinth.com":
        raise ValueError("Proxy-Mod liegt nicht auf dem Modrinth-CDN.")
    root = minekube_dir(p)
    root.mkdir(parents=True, exist_ok=True)
    secret_file = root / "velocity-secret.txt"
    secret = secret_file.read_text(encoding="ascii").strip() if secret_file.exists() else secrets.token_hex(32)
    if not secret_file.exists():
        secret_file.write_text(secret, encoding="ascii")
    endpoint_file = root / "endpoint.txt"
    endpoint = endpoint_file.read_text(encoding="ascii").strip() if endpoint_file.exists() else "mc-" + secrets.token_hex(6)
    if not endpoint_file.exists():
        endpoint_file.write_text(endpoint, encoding="ascii")
    if not re.fullmatch(r"[a-z0-9-]{4,32}", endpoint):
        raise ValueError("Ungültiger Minekube-Endpunktname.")
    mod_path = server_dir(p) / "mods" / file["filename"]
    disabled_mod_path = mod_path.with_name(mod_path.name + ".disabled")
    if not mod_path.exists() and disabled_mod_path.exists():
        disabled_mod_path.replace(mod_path)
    if mod_path.exists() and file.get("hashes", {}).get("sha512") and hashlib.sha512(mod_path.read_bytes()).hexdigest() != file["hashes"]["sha512"]:
        mod_path.unlink()
    if not mod_path.exists():
        download(file["url"], mod_path, file.get("hashes"))
    proxy_config = server_dir(p) / "config" / "FabricProxy-Lite.toml"
    proxy_config.parent.mkdir(parents=True, exist_ok=True)
    proxy_config.write_text('hackOnlineMode = false\nhackEarlySend = false\nhackMessageChain = false\n'
                            'disconnectMessage = "Use the public server address."\n'
                            f'secret = "{secret}"\n', encoding="utf-8")
    with LOCK:
        p.update(minekube_enabled=True, minekube_endpoint=endpoint,
                 minekube_mod_filename=file["filename"],
                 public_address=endpoint + ".play.minekube.net")
        save_config()


def disable_minekube(p: dict) -> None:
    if p["id"] in PROCESSES and PROCESSES[p["id"]].poll() is None:
        raise ValueError("Server vor dem Wechsel der Verbindungsart stoppen.")
    stop_gate(p["id"])
    name = p.get("minekube_mod_filename", "")
    if name and Path(name).name == name:
        mod_path = server_dir(p) / "mods" / name
        if mod_path.is_file():
            mod_path.replace(mod_path.with_name(name + ".disabled"))
    with LOCK:
        if p.get("public_address") == p.get("minekube_endpoint", "") + ".play.minekube.net":
            p["public_address"] = ""
        p["minekube_enabled"] = False
        save_config()


def read_gate(process: subprocess.Popen, pid: str) -> None:
    global GATE_CONNECTED
    try:
        for line in process.stdout:
            clean = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", line).strip()
            if not clean:
                continue
            with LOCK:
                GATE_LOGS.append(clean[:1000])
                if "established tunnel for session" in clean:
                    GATE_CONNECTED = True
                elif "session watcher disconnected" in clean:
                    GATE_CONNECTED = False
    except (OSError, ValueError):
        pass
    finally:
        GATE_CONNECTED = False
        code = process.wait()
        add_log(pid, f"[Minecraft Host] Minekube-Gate beendet (Code {code}).")


def start_gate(p: dict) -> None:
    global GATE, GATE_PROFILE_ID, GATE_CONNECTED
    if not p.get("minekube_enabled"):
        return
    exe = Path(sys.executable).resolve().parent / "gate.exe" if getattr(sys, "frozen", False) else APP_DIR.parent / "gate.exe"
    if not exe.is_file():
        raise ValueError("gate.exe fehlt neben MinecraftHost.exe.")
    if GATE and GATE.poll() is None:
        if GATE_PROFILE_ID == p["id"]:
            return
        raise ValueError("Minekube läuft bereits für einen anderen Server.")
    root = minekube_dir(p)
    secret_file = root / "velocity-secret.txt"
    if not secret_file.is_file():
        raise ValueError("Minekube ist noch nicht eingerichtet.")
    secret = secret_file.read_text(encoding="ascii").strip()
    config = {"config": {"bind": f"127.0.0.1:{gate_local_port(p)}", "onlineMode": True,
                         "servers": {"fabric": f"127.0.0.1:{p['port']}"}, "try": ["fabric"],
                         "forwarding": {"mode": "velocity", "velocitySecret": secret},
                         "status": {"motd": p["name"]}},
              "connect": {"enabled": True, "name": p["minekube_endpoint"]}}
    (root / "config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    GATE_CONNECTED = False
    GATE_LOGS.clear()
    GATE = subprocess.Popen([str(exe), "-c", str(root / "config.json")], cwd=root,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", bufsize=1, creationflags=flags)
    GATE_PROFILE_ID = p["id"]
    threading.Thread(target=read_gate, args=(GATE, p["id"]), daemon=True).start()


def stop_gate(pid: str = "") -> None:
    global GATE_CONNECTED
    if GATE and GATE.poll() is None and (not pid or GATE_PROFILE_ID == pid):
        GATE.terminate()
    GATE_CONNECTED = False


def get_json(url: str) -> dict | list:
    req = Request(url, headers={"User-Agent": "MinecraftHost/0.1 (local server manager)", "Accept": "application/json"})
    with urlopen(req, timeout=30) as response:
        return json.load(response)


def parse_modrinth_link(link: str) -> tuple[str, str | None]:
    parsed = urlparse(link.strip())
    if parsed.scheme != "https" or parsed.hostname not in ("modrinth.com", "www.modrinth.com"):
        raise ValueError("Bitte einen https://modrinth.com/modpack/...-Link einfügen.")
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    if len(parts) < 2 or parts[0] != "modpack" or not re.fullmatch(r"[A-Za-z0-9!@$().,_+'-]{1,80}", parts[1]):
        raise ValueError("Dies ist kein Modrinth-Modpack-Link.")
    if len(parts) == 2:
        return parts[1], None
    if len(parts) == 4 and parts[2] == "version" and len(parts[3]) <= 100:
        return parts[1], parts[3]
    raise ValueError("Unbekanntes Modrinth-Linkformat.")


def inspect_modrinth(link: str) -> dict:
    slug, requested = parse_modrinth_link(link)
    project = get_json("https://api.modrinth.com/v2/project/" + quote(slug, safe=""))
    if project.get("project_type") != "modpack":
        raise ValueError("Der Link verweist nicht auf ein Modpack.")
    versions = get_json("https://api.modrinth.com/v2/project/" + quote(project["id"], safe="") + "/version?include_changelog=false")
    choices = []
    for version in sorted(versions, key=lambda v: v.get("date_published", ""), reverse=True):
        if not any(f.get("filename", "").lower().endswith(".mrpack") for f in version.get("files", [])):
            continue
        choices.append({"id": version["id"], "name": version.get("name", ""),
                        "version_number": version.get("version_number", ""),
                        "game_versions": version.get("game_versions", []),
                        "loaders": version.get("loaders", []),
                        "version_type": version.get("version_type", "release"),
                        "date_published": version.get("date_published", "")})
        if len(choices) >= 40:
            break
    if not choices:
        raise ValueError("Für dieses Modpack wurde keine .mrpack-Version gefunden.")
    preferred = next((v["id"] for v in choices if requested in (v["id"], v["version_number"])), None) if requested else choices[0]["id"]
    if requested and not preferred:
        exact = get_json("https://api.modrinth.com/v2/project/" + quote(project["id"], safe="") + "/version/" + quote(requested, safe=""))
        preferred = exact["id"]
        choices.insert(0, {"id": exact["id"], "name": exact.get("name", ""),
                           "version_number": exact.get("version_number", ""),
                           "game_versions": exact.get("game_versions", []),
                           "loaders": exact.get("loaders", []),
                           "version_type": exact.get("version_type", "release"),
                           "date_published": exact.get("date_published", "")})
    return {"project_id": project["id"], "title": project["title"], "versions": choices, "preferred": preferred}


def download(url: str, destination: Path, expected: dict | None = None) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Downloads müssen HTTPS-URLs sein.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_name(destination.name + ".part")
    sha1 = hashlib.sha1()
    sha512 = hashlib.sha512()
    size = 0
    try:
        req = Request(url, headers={"User-Agent": "MinecraftHost/0.1"})
        with urlopen(req, timeout=45) as response, tmp.open("wb") as out:
            final = urlparse(response.url)
            if final.scheme != "https":
                raise ValueError("Download wurde auf eine unsichere URL umgeleitet.")
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_FILE:
                    raise ValueError("Eine einzelne Datei ist größer als 2 GB.")
                out.write(chunk)
                sha1.update(chunk)
                sha512.update(chunk)
        if expected:
            if expected.get("sha1") and sha1.hexdigest().lower() != expected["sha1"].lower():
                raise ValueError(f"SHA1-Prüfung fehlgeschlagen: {destination.name}")
            if expected.get("sha512") and sha512.hexdigest().lower() != expected["sha512"].lower():
                raise ValueError(f"SHA512-Prüfung fehlgeschlagen: {destination.name}")
        tmp.replace(destination)
    finally:
        tmp.unlink(missing_ok=True)


def safe_relative(name: str) -> Path:
    name = name.replace("\\", "/")
    posix = PurePosixPath(name)
    if not name or name.startswith("/") or re.match(r"^[A-Za-z]:", name) or any(part in ("..", "") for part in posix.parts):
        raise ValueError(f"Unsicherer Archivpfad: {name[:100]}")
    return Path(*posix.parts)


def safe_extract(zf: zipfile.ZipFile, root: Path, prefix: str = "", skip_client: bool = False) -> int:
    total = 0
    count = 0
    for info in zf.infolist():
        name = info.filename.replace("\\", "/")
        if prefix:
            if not name.startswith(prefix):
                continue
            name = name[len(prefix):]
        elif name.startswith(("client-overrides/", "overrides/", "server-overrides/")):
            continue
        if not name or info.is_dir():
            continue
        if skip_client and (name.startswith("client-overrides/") or name.startswith("shaderpacks/")):
            continue
        if info.external_attr >> 16 & 0o170000 == 0o120000:
            raise ValueError("Archive mit symbolischen Links werden nicht importiert.")
        relative = safe_relative(name)
        total += info.file_size
        if total > MAX_UNPACKED or info.file_size > MAX_FILE or count > 100000:
            raise ValueError("Archiv ist zu groß oder enthält zu viele Dateien.")
        dest = root / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src, dest.open("wb") as out:
            shutil.copyfileobj(src, out, 1024 * 1024)
        count += 1
    return count


def java_major(path: str) -> int | None:
    try:
        result = subprocess.run([path, "-version"], capture_output=True, text=True, timeout=8)
        match = re.search(r'"(?:1\.)?(\d+)(?:[._+-]|\")', result.stderr + result.stdout)
        return int(match.group(1)) if match else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def java_candidates() -> list[dict]:
    global JAVA_CACHE
    if time.time() - JAVA_CACHE[0] < 45:
        return JAVA_CACHE[1]
    paths = []
    from_path = shutil.which("java")
    if from_path:
        paths.append(Path(from_path))
    paths.append(Path("C:/Program Files (x86)/Common Files/Oracle/Java/java8path/java.exe"))
    paths.append(Path("C:/Program Files/Common Files/Oracle/Java/java8path/java.exe"))
    for env in ("JAVA_HOME", "JDK_HOME"):
        if os.environ.get(env):
            paths.append(Path(os.environ[env]) / "bin" / "java.exe")
    for parent in (Path("C:/Program Files/Java"), Path("C:/Program Files/Eclipse Adoptium"), Path("C:/Program Files/Microsoft")):
        if parent.exists():
            paths.extend(parent.glob("*/bin/java.exe"))
    runtime = Path(os.environ.get("APPDATA", "")) / ".minecraft" / "runtime"
    if runtime.exists():
        paths.extend(runtime.glob("**/bin/java.exe"))
    modrinth_java = Path(os.environ.get("APPDATA", "")) / "ModrinthApp" / "meta" / "java_versions"
    if modrinth_java.exists():
        paths.extend(modrinth_java.glob("*/bin/java.exe"))
    result = []
    seen = set()
    for p in paths:
        key = str(p).lower()
        if key in seen or not p.exists():
            continue
        seen.add(key)
        version = java_major(str(p))
        if version:
            result.append({"path": str(p), "major": version})
    result = sorted(result, key=lambda x: x["major"], reverse=True)
    JAVA_CACHE = (time.time(), result)
    return result


def minimum_java(p: dict) -> int:
    if p.get("min_java"):
        return int(p["min_java"])
    version = p.get("minecraft", "")
    if version.startswith("26."):
        return 25
    if version.startswith("1.21") or version.startswith("1.20.5") or version.startswith("1.20.6"):
        return 21
    if version.startswith(("1.18", "1.19", "1.20")):
        return 17
    return 8


def pick_java(p: dict) -> tuple[str, int]:
    requested = p.get("java_path", "").strip()
    if requested:
        major = java_major(requested)
        if not major:
            raise ValueError("Der eingestellte Java-Pfad funktioniert nicht.")
        return requested, major
    candidates = java_candidates()
    if not candidates:
        raise ValueError("Java wurde nicht gefunden. Installiere ein passendes JDK und trage java.exe ein.")
    target = minimum_java(p)
    best = next((j for j in sorted(candidates, key=lambda x: x["major"]) if j["major"] >= target), candidates[0])
    return best["path"], best["major"]


def install_loader(p: dict) -> None:
    root = server_dir(p)
    root.mkdir(parents=True, exist_ok=True)
    kind = p["loader"]
    version = p["minecraft"]
    if kind == "fabric":
        loaders = get_json("https://meta.fabricmc.net/v2/versions/loader")
        installers = get_json("https://meta.fabricmc.net/v2/versions/installer")
        loader_version = p.get("loader_version") or next(x["version"] for x in loaders if x.get("stable"))
        installer_version = next(x["version"] for x in installers if x.get("stable"))
        url = f"https://meta.fabricmc.net/v2/versions/loader/{version}/{loader_version}/{installer_version}/server/jar"
        download(url, root / "server.jar")
        p["jar"] = "server.jar"
    elif kind == "vanilla":
        manifest = get_json("https://piston-meta.mojang.com/mc/game/version_manifest_v2.json")
        found = next((x for x in manifest["versions"] if x["id"] == version), None)
        if not found:
            raise ValueError("Minecraft-Version wurde bei Mojang nicht gefunden.")
        detail = get_json(found["url"])
        p["min_java"] = detail.get("javaVersion", {}).get("majorVersion", minimum_java(p))
        server = detail.get("downloads", {}).get("server")
        if not server:
            raise ValueError("Für diese Minecraft-Version gibt es keinen Serverdownload.")
        download(server["url"], root / "server.jar", {"sha1": server["sha1"]})
        p["jar"] = "server.jar"
    elif kind in ("neoforge", "forge"):
        java, major = pick_java(p)
        needed = minimum_java(p)
        if major < needed:
            raise ValueError(f"Für die Loader-Installation ist Java {needed} oder neuer nötig. Gefunden: Java {major}.")
        lv = p.get("loader_version", "").strip()
        if not re.fullmatch(r"[A-Za-z0-9.+_-]+", lv):
            raise ValueError("Loader-Version fehlt oder enthält ungültige Zeichen.")
        if kind == "neoforge":
            url = f"https://maven.neoforged.net/releases/net/neoforged/neoforge/{lv}/neoforge-{lv}-installer.jar"
        else:
            full = lv if lv.startswith(version + "-") else version + "-" + lv
            url = f"https://maven.minecraftforge.net/net/minecraftforge/forge/{full}/forge-{full}-installer.jar"
        installer = root / "loader-installer.jar"
        download(url, installer)
        add_log(p["id"], f"[Minecraft Host] Installiere {kind} {lv} …")
        proc = subprocess.run([java, "-jar", str(installer), "--installServer"], cwd=root, capture_output=True, text=True, timeout=600)
        for line in (proc.stdout + "\n" + proc.stderr).splitlines()[-80:]:
            add_log(p["id"], line)
        if proc.returncode != 0:
            raise ValueError(f"{kind}-Installer beendet mit Code {proc.returncode}. Siehe Konsole.")
        args = list(root.glob("libraries/**/win_args.txt"))
        if not args:
            args = list(root.glob("libraries/**/unix_args.txt"))
        if not args:
            raise ValueError("Installer hat keine Server-Startargumente erzeugt.")
        p["args_file"] = str(args[0].relative_to(root)).replace("\\", "/")
    elif kind == "custom":
        if not p.get("jar") or not (root / p["jar"]).exists():
            raise ValueError("Bitte zuerst eine startbare Server-JAR importieren.")
    else:
        raise ValueError(f"Loader {kind} wird noch nicht unterstützt.")
    with LOCK:
        p["installed"] = True
        save_config()


def import_archive(path: Path, filename: str, pid: str) -> None:
    job = JOBS[pid]
    p = profile(pid)
    root = server_dir(p)
    try:
        if not zipfile.is_zipfile(path):
            raise ValueError("Bitte eine gültige .mrpack- oder .zip-Datei auswählen.")
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            if "modrinth.index.json" in names:
                index = json.loads(zf.read("modrinth.index.json"))
                if index.get("game") != "minecraft":
                    raise ValueError("Dies ist kein Minecraft-Modpack.")
                deps = index.get("dependencies", {})
                p["name"] = str(index.get("name") or filename)[:80]
                p["minecraft"] = str(deps.get("minecraft", ""))
                for dep, kind in (("neoforge", "neoforge"), ("forge", "forge"), ("fabric-loader", "fabric")):
                    if dep in deps:
                        p["loader"] = kind
                        p["loader_version"] = str(deps[dep])
                        break
                else:
                    p["loader"] = "vanilla"
                if not p["minecraft"]:
                    raise ValueError("Im Modpack fehlt die Minecraft-Version.")
                with LOCK:
                    save_config()
                files = [f for f in index.get("files", []) if f.get("env", {}).get("server") != "unsupported"]
                for i, entry in enumerate(files):
                    rel = safe_relative(entry["path"])
                    urls = entry.get("downloads") or []
                    if not urls:
                        raise ValueError(f"Keine Download-URL für {rel}")
                    for url in urls:
                        host = urlparse(url).hostname
                        if host not in ("cdn.modrinth.com", "github.com", "raw.githubusercontent.com", "gitlab.com"):
                            raise ValueError(f"Download-Domain im Modpack nicht erlaubt: {host}")
                    job["progress"] = f"Datei {i + 1} von {len(files)}: {rel.name}"
                    error = None
                    for url in urls:
                        try:
                            download(url, root / rel, entry.get("hashes"))
                            error = None
                            break
                        except Exception as ex:
                            error = ex
                    if error:
                        raise ValueError(f"Download fehlgeschlagen: {rel}: {error}")
                safe_extract(zf, root, "overrides/")
                safe_extract(zf, root, "server-overrides/")
            elif "manifest.json" in names:
                manifest = json.loads(zf.read("manifest.json"))
                if manifest.get("manifestType") == "minecraftModpack":
                    raise ValueError("CurseForge-Export erkannt. Bitte das Serverpack als ZIP oder einen Modrinth-.mrpack-Export verwenden; CurseForge-Dateien sind im Export nicht enthalten.")
                raise ValueError("Unbekanntes ZIP-Format.")
            else:
                job["progress"] = "Serverpack wird entpackt …"
                files = [n.replace("\\", "/") for n in names if n and not n.endswith("/")]
                top = {n.split("/", 1)[0] for n in files}
                prefix = next(iter(top)) + "/" if len(top) == 1 and all("/" in n for n in files) else ""
                safe_extract(zf, root, prefix=prefix, skip_client=True)
                args = list(root.glob("libraries/**/win_args.txt"))
                jars = [x for x in root.glob("*.jar") if "installer" not in x.name.lower()]
                preferred = next((x for x in jars if any(w in x.name.lower() for w in ("server", "fabric", "forge"))), None)
                if args:
                    p["loader"] = "custom"
                    p["args_file"] = str(args[0].relative_to(root)).replace("\\", "/")
                    p["installed"] = True
                elif preferred or jars:
                    p["loader"] = "custom"
                    p["jar"] = (preferred or jars[0]).name
                    p["installed"] = True
                else:
                    raise ValueError("ZIP entpackt, aber keine startbare Server-JAR im Hauptordner gefunden. Bitte ein Serverpack mit JAR verwenden.")
                with LOCK:
                    save_config()
        if p["loader"] != "custom":
            job["progress"] = "Server-Loader wird installiert …"
            install_loader(p)
        job.update(status="done", progress="Import abgeschlossen.")
        add_log(pid, "[Minecraft Host] Import abgeschlossen.")
    except Exception as ex:
        job.update(status="error", progress=str(ex))
        add_log(pid, "[Minecraft Host] Importfehler: " + str(ex))
    finally:
        path.unlink(missing_ok=True)


def write_properties(p: dict) -> None:
    props = server_dir(p) / "server.properties"
    lines = props.read_text(encoding="utf-8", errors="replace").splitlines() if props.exists() else []
    proxied = bool(p.get("minekube_enabled"))
    values = {"server-port": str(p["port"]), "server-ip": "127.0.0.1" if proxied else "",
              "motd": p["name"], "online-mode": "false" if proxied else "true"}
    values["enforce-secure-profile"] = "false" if proxied else "true"
    found = set()
    result = []
    for line in lines:
        key = line.split("=", 1)[0].strip()
        if key in values:
            result.append(key + "=" + values[key].replace("\n", " ").replace("\r", " "))
            found.add(key)
        else:
            result.append(line)
    result.extend(k + "=" + v for k, v in values.items() if k not in found)
    props.write_text("\n".join(result) + "\n", encoding="utf-8")


def encode_varint(value: int) -> bytes:
    result = bytearray()
    while True:
        part = value & 0x7f
        value >>= 7
        result.append(part | (0x80 if value else 0))
        if not value:
            return bytes(result)


def read_varint(sock: socket.socket) -> int:
    value = 0
    for shift in range(0, 35, 7):
        part = sock.recv(1)
        if not part:
            raise OSError("Server hat die Statusverbindung geschlossen.")
        value |= (part[0] & 0x7f) << shift
        if not part[0] & 0x80:
            return value
    raise ValueError("Ungültige Minecraft-Statusantwort.")


def read_exact(sock: socket.socket, length: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < length:
        chunk = sock.recv(length - len(chunks))
        if not chunk:
            raise OSError("Unvollständige Minecraft-Statusantwort.")
        chunks.extend(chunk)
    return bytes(chunks)


def server_status(port: int) -> dict:
    with socket.create_connection((HOST, port), timeout=2) as sock:
        sock.settimeout(2)
        address = HOST.encode("utf-8")
        handshake = b"\x00" + encode_varint(767) + encode_varint(len(address)) + address + port.to_bytes(2, "big") + b"\x01"
        sock.sendall(encode_varint(len(handshake)) + handshake)
        started = time.perf_counter()
        sock.sendall(b"\x01\x00")
        length = read_varint(sock)
        if not (1 <= length <= 1024 * 1024):
            raise ValueError("Statusantwort ist zu groß.")
        payload = read_exact(sock, length)
        if payload[0] != 0:
            raise ValueError("Unerwartete Minecraft-Statusantwort.")
        data_length = 0
        shift = 0
        offset = 1
        while True:
            part = payload[offset]
            offset += 1
            data_length |= (part & 0x7f) << shift
            if not part & 0x80:
                break
            shift += 7
            if shift > 28:
                raise ValueError("Ungültige Minecraft-Statusantwort.")
        response = json.loads(payload[offset:offset + data_length])
        players = response.get("players", {})
        return {"online": int(players.get("online", 0)), "max": int(players.get("max", 0)),
                "response_ms": round((time.perf_counter() - started) * 1000, 1),
                "version": response.get("version", {}).get("name", "")}


def supports_tick_query(version: str) -> bool:
    if version.startswith("26."):
        return True
    match = re.match(r"^1\.(\d+)(?:\.(\d+))?", version)
    return bool(match and (int(match.group(1)) > 20 or (int(match.group(1)) == 20 and int(match.group(2) or 0) >= 3)))


def process_memory_mb(pid: int) -> int | None:
    if os.name != "nt":
        return None
    from ctypes import wintypes
    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessMemoryCounters), wintypes.DWORD]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x0410, False, pid)
    if not handle:
        return None
    try:
        info = ProcessMemoryCounters()
        info.cb = ctypes.sizeof(info)
        if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(info), info.cb):
            return None
        return round(info.WorkingSetSize / 1024 / 1024)
    finally:
        kernel.CloseHandle(handle)


def monitor_server(pid: str, process: subprocess.Popen, port: int, tick_query: bool) -> None:
    next_tick = 0.0
    while process.poll() is None:
        try:
            result = server_status(port)
            result["ram_used_mb"] = process_memory_mb(process.pid)
            if pid in STOP_REQUESTED and pid not in STOP_RESENT:
                with LOCK:
                    process.stdin.write("stop\n")
                    process.stdin.flush()
                    STOP_RESENT.add(pid)
            with LOCK:
                METRICS.setdefault(pid, {}).update(result)
            if tick_query and time.monotonic() >= next_tick:
                try:
                    with LOCK:
                        process.stdin.write("tick query\n")
                        process.stdin.flush()
                        TICK_PENDING[pid] = time.monotonic()
                    next_tick = time.monotonic() + 15
                except OSError:
                    pass
        except (OSError, ValueError, KeyError, IndexError, json.JSONDecodeError):
            with LOCK:
                METRICS.setdefault(pid, {}).update(online=None, max=None, response_ms=None)
        time.sleep(5)


def start_server(p: dict) -> None:
    with WORLD_LOCK:
        _start_server(p)


def _start_server(p: dict) -> None:
    pid = p["id"]
    if pid in SHARED_PUBLISHING:
        raise ValueError("Die Weltübergabe läuft noch. Bitte warten.")
    with LOCK:
        if pid in PROCESSES and PROCESSES[pid].poll() is None:
            raise ValueError("Server läuft bereits.")
    if p.get("shared_role") and p.get("shared_peer"):
        SHARED.claim(p, server_dir(p))
        with LOCK:
            save_config()
    if not p.get("installed"):
        raise ValueError("Serverdateien sind noch nicht installiert.")
    if not p.get("eula"):
        raise ValueError("Bitte zuerst die Minecraft-EULA akzeptieren.")
    if p.get("minekube_enabled"):
        proxy_mod = server_dir(p) / "mods" / p.get("minekube_mod_filename", "FabricProxy-Lite-2.12.0.jar")
        if not proxy_mod.is_file() or not (server_dir(p) / "config" / "FabricProxy-Lite.toml").is_file():
            raise ValueError("Minekube-Proxy-Mod fehlt. Bitte Minekube erneut einrichten.")
        if GATE and GATE.poll() is None and GATE_PROFILE_ID != pid:
            raise ValueError("Minekube läuft bereits für einen anderen Server.")
    java, major = pick_java(p)
    needed = minimum_java(p)
    if major < needed:
        raise ValueError(f"Minecraft {p['minecraft']} benötigt Java {needed} oder neuer. Gefunden: Java {major}.")
    root = server_dir(p)
    (root / "eula.txt").write_text("eula=true\n", encoding="utf-8")
    write_properties(p)
    ram = int(p["ram_mb"])
    cmd = [java, f"-Xms{min(1024, ram)}M", f"-Xmx{ram}M"]
    if p.get("args_file"):
        args = root / safe_relative(p["args_file"])
        if not args.exists():
            raise ValueError("Startargumente des Loaders fehlen. Bitte neu installieren.")
        cmd += ["@" + str(args), "nogui"]
    else:
        jar = root / safe_relative(p.get("jar", "server.jar"))
        if not jar.exists():
            raise ValueError("Server-JAR fehlt. Bitte neu installieren.")
        cmd += ["-jar", str(jar), "nogui"]
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    proc = subprocess.Popen(cmd, cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", bufsize=1, creationflags=flags)
    with LOCK:
        PROCESSES[pid] = proc
        METRICS[pid] = {"started_at": time.time(), "online": None, "max": None,
                        "response_ms": None, "mspt": None, "tps_estimate": None}
    add_log(pid, "[Minecraft Host] Server wird gestartet …")
    reader = threading.Thread(target=read_stream, args=(pid, proc), daemon=True)
    READ_THREADS[pid] = reader
    reader.start()
    threading.Thread(target=monitor_server, args=(pid, proc, int(p["port"]), supports_tick_query(p.get("minecraft", ""))), daemon=True).start()
    if p.get("shared_role") and p.get("shared_peer"):
        def heartbeat():
            while proc.poll() is None:
                try:
                    SHARED.mark_active(p)
                except Exception as ex:
                    add_log(pid, "[Minecraft Host] Partnerstatus konnte nicht aktualisiert werden: " + str(ex))
                time.sleep(10)
        threading.Thread(target=heartbeat, daemon=True).start()
    if p.get("minekube_enabled"):
        try:
            start_gate(p)
        except (OSError, ValueError) as ex:
            add_log(pid, "[Minecraft Host] Minekube konnte nicht gestartet werden: " + str(ex))


def stop_server(pid: str) -> None:
    with LOCK:
        proc = PROCESSES.get(pid)
    if not proc or proc.poll() is not None:
        raise ValueError("Server läuft nicht.")
    try:
        add_log(pid, "[Minecraft Host] Stopp angefordert.")
        STOP_REQUESTED.add(pid)
        STOP_RESENT.discard(pid)
        if profile(pid).get("shared_peer"):
            SHARED_PUBLISHING.add(pid)
        proc.stdin.write("stop\n")
        proc.stdin.flush()
        stop_gate(pid)
    except OSError:
        SHARED_PUBLISHING.discard(pid)
        raise ValueError("Serverkonsole ist nicht erreichbar.")


def publish_shared_initial(p: dict) -> None:
    SHARED_PUBLISHING.add(p["id"])
    try:
        p["shared_status"] = "Erster Weltstand wird vorbereitet …"
        with WORLD_LOCK:
            SHARED.publish(p, server_dir(p))
            with LOCK:
                save_config()
        add_log(p["id"], "[Minecraft Host] Erster Weltstand bereit. Warte auf die Übertragung zum Partner.")
    except Exception as ex:
        p["shared_status"] = "Weltübergabe fehlgeschlagen: " + str(ex)
        add_log(p["id"], "[Minecraft Host] Weltübergabe fehlgeschlagen: " + str(ex))
    finally:
        SHARED_PUBLISHING.discard(p["id"])


def local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("1.1.1.1", 80))
            return sock.getsockname()[0]
    except OSError:
        return ""


def radmin_status() -> dict:
    global RADMIN_CACHE
    if os.name != "nt":
        return {}
    if time.monotonic() - RADMIN_CACHE[0] < 15:
        return RADMIN_CACHE[1]
    script = ("$a=Get-NetAdapter -ErrorAction SilentlyContinue | Where-Object "
              "{$_.InterfaceDescription -match 'Radmin VPN' -or $_.Name -match 'Radmin VPN'} | "
              "Select-Object -First 1; if($a){$ip=Get-NetIPAddress -InterfaceIndex $a.ifIndex "
              "-AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object "
              "{$_.IPAddress -notlike '169.254*'} | Select-Object -First 1; "
              "[PSCustomObject]@{address=$ip.IPAddress;status=[string]$a.Status} | ConvertTo-Json -Compress}")
    try:
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                                capture_output=True, text=True, timeout=8,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        info = json.loads(result.stdout) if result.returncode == 0 and result.stdout.strip() else {}
        if not isinstance(info, dict):
            info = {}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        info = {}
    RADMIN_CACHE = (time.monotonic(), info)
    return info


FIREWALL_CACHE: dict[str, tuple[float, dict]] = {}
FIREWALL_RULE_NAME = "Minecraft Host - Java"


def firewall_status(java_path: str) -> dict:
    """Checks whether Windows lets inbound connections reach this Java on public networks (Radmin VPN counts as public)."""
    if os.name != "nt" or not java_path or not os.path.isabs(java_path):
        return {}
    cached = FIREWALL_CACHE.get(java_path)
    if cached and time.monotonic() - cached[0] < 30:
        return cached[1]
    script = ("$p=$env:MCH_JAVA; $r=Get-NetFirewallApplicationFilter -Program $p -ErrorAction SilentlyContinue | "
              "Get-NetFirewallRule -ErrorAction SilentlyContinue | Where-Object {$_.Direction -eq 'Inbound' -and "
              "$_.Enabled -eq 'True'}; $allow=@($r | Where-Object {$_.Action -eq 'Allow' -and "
              "($_.Profile -match 'Public|Any')}).Count -gt 0; $block=@($r | Where-Object {$_.Action -eq 'Block' -and "
              "($_.Profile -match 'Public|Any')}).Count -gt 0; [PSCustomObject]@{allowed=$allow;blocked=$block} | "
              "ConvertTo-Json -Compress")
    try:
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                                capture_output=True, text=True, timeout=10,
                                env={**os.environ, "MCH_JAVA": java_path},
                                creationflags=subprocess.CREATE_NO_WINDOW)
        info = json.loads(result.stdout) if result.returncode == 0 and result.stdout.strip() else {}
        if not isinstance(info, dict):
            info = {}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        info = {}
    if info:
        info["checked"] = True
        info["java"] = java_path
    FIREWALL_CACHE[java_path] = (time.monotonic(), info)
    return info


def request_firewall_rule(java_path: str) -> None:
    """Opens an elevated netsh call; Windows shows its own administrator prompt, the user decides."""
    if os.name != "nt" or not os.path.isfile(java_path):
        raise ValueError("Java-Datei für die Firewall-Freigabe nicht gefunden.")
    args = (f'advfirewall firewall add rule name="{FIREWALL_RULE_NAME}" dir=in action=allow '
            f'program="{java_path}" enable=yes profile=private,public')
    script = "Start-Process -FilePath netsh.exe -Verb RunAs -WindowStyle Hidden -Wait -ArgumentList $env:MCH_ARGS"
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                            capture_output=True, text=True, timeout=180,
                            env={**os.environ, "MCH_ARGS": args},
                            creationflags=subprocess.CREATE_NO_WINDOW)
    FIREWALL_CACHE.pop(java_path, None)
    if result.returncode != 0:
        raise ValueError("Firewall-Freigabe wurde nicht eingerichtet (Administratorabfrage abgelehnt?).")


def profile_java_path(p: dict) -> str:
    requested = p.get("java_path", "").strip()
    if requested:
        return requested
    try:
        return pick_java(p)[0]
    except Exception:
        return ""


def state() -> dict:
    with LOCK:
        profiles = []
        for p in CONFIG["profiles"]:
            item = dict(p)
            proc = PROCESSES.get(p["id"])
            item["running"] = bool(proc and proc.poll() is None)
            item["logs"] = list(LOGS.get(p["id"], []))[-180:]
            item["job"] = JOBS.get(p["id"])
            item["metrics"] = dict(METRICS.get(p["id"], {}))
            item["firewall"] = firewall_status(profile_java_path(p)) if p.get("installed") else {}
            if p.get("shared_role"):
                cached = SHARED_INFO_CACHE.get(p["id"])
                if not cached or time.monotonic() - cached[0] > 5:
                    cached = (time.monotonic(), SHARED.info(p))
                    SHARED_INFO_CACHE[p["id"]] = cached
                item["shared_info"] = cached[1]
            profiles.append(item)
        return {"profiles": profiles, "java": java_candidates(), "local_ip": local_ip(),
                "radmin": radmin_status(),
                "playit_path": CONFIG.get("playit_path", ""),
                "tunnel_running": bool(TUNNEL and TUNNEL.poll() is None),
                "tunnel_claim": TUNNEL_CLAIM if TUNNEL and TUNNEL.poll() is None else "",
                "gate_running": bool(GATE and GATE.poll() is None),
                "gate_profile_id": GATE_PROFILE_ID if GATE and GATE.poll() is None else "",
                "gate_connected": GATE_CONNECTED if GATE and GATE.poll() is None else False,
                "app": updater.public_status(),
                "remote": remote_state(),
                "tailscale_ip": next((a["address"] for a in remote.addresses() if a["kind"] == "tailscale"), "")}


def remote_state() -> dict:
    cfg = CONFIG["remote"]
    return {"enabled": bool(cfg.get("enabled")), "port": int(cfg.get("port") or remote.DEFAULT_PORT),
            "code": remote.format_token(cfg.get("token", "")), "running": bool(REMOTE and REMOTE.running),
            "error": REMOTE.error if REMOTE else "", "addresses": remote.addresses() if cfg.get("enabled") else []}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        pass

    def json(self, obj: dict, code: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def error_json(self, error: Exception) -> None:
        self.json({"error": str(error)}, 400)

    def body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 100000:
            raise ValueError("Anfrage ist zu groß.")
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self) -> None:
        if not self.allowed_origin():
            self.send_error(403)
            return
        path = urlparse(self.path).path
        if path == "/api/state":
            return self.json(state())
        if path == "/":
            path = "/index.html"
        if path not in ("/index.html", "/styles.css", "/app.js", "/scene-cherry.webp", "/scene-aurora.webp", "/scene-crystal.webp"):
            self.send_error(404)
            return
        file = APP_DIR / path[1:]
        body = file.read_bytes()
        mime = ("text/html; charset=utf-8" if path.endswith("html") else "text/css; charset=utf-8" if path.endswith("css")
                else "image/webp" if path.endswith("webp") else "text/javascript; charset=utf-8")
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        global TUNNEL, TUNNEL_CLAIM
        if not self.allowed_origin():
            return self.json({"error": "Anfrage von fremder Website blockiert."}, 403)
        path = urlparse(self.path).path
        try:
            if path == "/api/update/check":
                updater.check(force=True)
                return self.json(updater.public_status())
            if path == "/api/update/install":
                with LOCK:
                    running = [p["name"] for p in CONFIG["profiles"] if (proc := PROCESSES.get(p["id"])) and proc.poll() is None]
                    working = [p["name"] for p in CONFIG["profiles"] if (JOBS.get(p["id"]) or {}).get("status") == "working"]
                busy = (f"Bitte zuerst den Server stoppen: {', '.join(running)}." if running
                        else f"Bitte warten, bis die Einrichtung fertig ist: {', '.join(working)}." if working
                        else "Bitte warten, bis der Weltstand übertragen ist." if SHARED_PUBLISHING else "")
                result = updater.install(DATA_DIR, busy)
                # Give the response time to reach the browser, then exit so the helper can swap files.
                threading.Timer(1.0, lambda: SERVER and SERVER.shutdown()).start()
                return self.json(result)
            if path == "/api/remote/config":
                data = self.body()
                port = int(data.get("port", CONFIG["remote"].get("port") or remote.DEFAULT_PORT))
                if not (1024 <= port <= 65535) or port == PORT:
                    raise ValueError("Port für die Fernsteuerung: 1024–65535, nicht der Port dieser App.")
                with LOCK:
                    CONFIG["remote"]["enabled"] = bool(data.get("enabled"))
                    CONFIG["remote"]["port"] = port
                    if not CONFIG["remote"].get("token"):
                        CONFIG["remote"]["token"] = remote.new_token()
                    save_config()
                if REMOTE:
                    REMOTE.apply(CONFIG["remote"])
                return self.json(remote_state())
            if path == "/api/remote/rotate":
                with LOCK:
                    CONFIG["remote"]["token"] = remote.new_token()
                    save_config()
                if REMOTE:
                    REMOTE.apply(CONFIG["remote"])
                return self.json(remote_state())
            if path == "/api/modrinth/inspect":
                return self.json(inspect_modrinth(str(self.body().get("url", ""))))
            if path == "/api/modrinth/import":
                data = self.body()
                slug, _ = parse_modrinth_link(str(data.get("url", "")))
                version_id = str(data.get("version_id", ""))
                if not re.fullmatch(r"[A-Za-z0-9]{8,16}", version_id):
                    raise ValueError("Bitte eine Modpack-Version auswählen.")
                project = get_json("https://api.modrinth.com/v2/project/" + quote(slug, safe=""))
                if project.get("project_type") != "modpack":
                    raise ValueError("Der Link verweist nicht auf ein Modpack.")
                version = get_json("https://api.modrinth.com/v2/version/" + quote(version_id, safe=""))
                if version.get("project_id") != project["id"]:
                    raise ValueError("Die ausgewählte Version gehört nicht zu diesem Modpack.")
                files = [f for f in version.get("files", []) if f.get("filename", "").lower().endswith(".mrpack")]
                if not files:
                    raise ValueError("Diese Version enthält keine .mrpack-Datei.")
                pack = next((f for f in files if f.get("primary")), files[0])
                if urlparse(pack["url"]).hostname != "cdn.modrinth.com":
                    raise ValueError("Modpack-Datei liegt nicht auf dem Modrinth-CDN.")
                pid = uuid.uuid4().hex[:12]
                p = {"id": pid, "name": str(project["title"])[:80], "minecraft": "", "loader": "",
                     "loader_version": "", "ram_mb": 4096, "port": 25565, "java_path": "",
                     "public_address": "", "eula": False, "installed": False, "jar": ""}
                with LOCK:
                    CONFIG["profiles"].append(p)
                    JOBS[pid] = {"status": "working", "kind": "import", "progress": "Modrinth-Datei wird geladen …"}
                    save_config()
                def import_from_modrinth():
                    temp = DATA_DIR / (pid + ".mrpack")
                    try:
                        download(pack["url"], temp, pack.get("hashes"))
                        import_archive(temp, pack["filename"], pid)
                    except Exception as ex:
                        JOBS[pid].update(status="error", progress=str(ex))
                        add_log(pid, "[Minecraft Host] Modrinth-Importfehler: " + str(ex))
                    finally:
                        temp.unlink(missing_ok=True)
                threading.Thread(target=import_from_modrinth, daemon=True).start()
                return self.json({"id": pid})
            if path == "/api/import":
                name = os.path.basename(self.headers.get("X-Filename", "modpack.zip"))[:120]
                length = int(self.headers.get("Content-Length", "0"))
                if not (0 < length <= MAX_ARCHIVE):
                    raise ValueError("Importdatei muss zwischen 1 Byte und 4 GB groß sein.")
                pid = uuid.uuid4().hex[:12]
                p = {"id": pid, "name": Path(name).stem[:80], "minecraft": "", "loader": "",
                     "loader_version": "", "ram_mb": 4096, "port": 25565, "java_path": "",
                     "public_address": "", "eula": False, "installed": False, "jar": ""}
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                temp = DATA_DIR / (pid + ".upload")
                with temp.open("wb") as out:
                    remaining = length
                    while remaining:
                        chunk = self.rfile.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise ValueError("Upload wurde unterbrochen.")
                        out.write(chunk)
                        remaining -= len(chunk)
                with LOCK:
                    CONFIG["profiles"].append(p)
                    JOBS[pid] = {"status": "working", "kind": "import", "progress": "Archiv wird geprüft …"}
                    save_config()
                threading.Thread(target=import_archive, args=(temp, name, pid), daemon=True).start()
                return self.json({"id": pid})
            if path == "/api/create":
                body = self.body()
                name = str(body.get("name", "Neuer Server")).strip()[:80]
                version = str(body.get("minecraft", "1.21.1")).strip()
                loader = str(body.get("loader", "fabric"))
                if not name or not re.fullmatch(r"[A-Za-z0-9._-]+", version) or loader not in ("fabric", "vanilla", "neoforge", "forge"):
                    raise ValueError("Name, Version oder Loader ungültig.")
                pid = uuid.uuid4().hex[:12]
                p = {"id": pid, "name": name, "minecraft": version, "loader": loader,
                     "loader_version": str(body.get("loader_version", ""))[:40], "ram_mb": 4096,
                     "port": 25565, "java_path": "", "public_address": "", "eula": False, "installed": False, "jar": ""}
                with LOCK:
                    CONFIG["profiles"].append(p)
                    JOBS[pid] = {"status": "working", "kind": "create", "progress": "Serverdateien werden installiert …"}
                    save_config()
                def worker():
                    try:
                        install_loader(p)
                        JOBS[pid].update(status="done", progress="Serverdateien sind bereit.")
                    except Exception as ex:
                        JOBS[pid].update(status="error", progress=str(ex))
                        add_log(pid, "[Minecraft Host] Installationsfehler: " + str(ex))
                threading.Thread(target=worker, daemon=True).start()
                return self.json({"id": pid})
            if path == "/api/shared/join":
                incoming = str(self.body().get("code", "")).strip()
                p, own_id = SHARED.join(incoming, CONFIG["profiles"])
                with LOCK:
                    CONFIG["profiles"].append(p)
                    save_config()
                return self.json({"id": p["id"], "device_id": own_id})
            if path.startswith("/api/profiles/"):
                parts = path.strip("/").split("/")
                if len(parts) != 4:
                    raise ValueError("Unbekannte Aktion.")
                pid, action = parts[2], parts[3]
                p = profile(pid)
                if action == "save":
                    data = self.body()
                    if pid in PROCESSES and PROCESSES[pid].poll() is None:
                        raise ValueError("Server zum Ändern der Einstellungen zuerst stoppen.")
                    ram = int(data.get("ram_mb", p["ram_mb"]))
                    port = int(data.get("port", p["port"]))
                    if not (1024 <= ram <= 65536) or not (1024 <= port <= 65535):
                        raise ValueError("RAM: 1024–65536 MB; Port: 1024–65535.")
                    java_path = str(data.get("java_path", "")).strip()
                    if java_path and not Path(java_path).is_file():
                        raise ValueError("Java-Datei wurde nicht gefunden.")
                    public_address = str(data.get("public_address", "")).strip()
                    if len(public_address) > 255 or any(c.isspace() for c in public_address):
                        raise ValueError("Öffentliche Adresse ist ungültig.")
                    with LOCK:
                        p.update(ram_mb=ram, port=port, java_path=java_path, public_address=public_address,
                                 eula=bool(data.get("eula", p["eula"])))
                        save_config()
                elif action == "start":
                    start_server(p)
                elif action == "stop":
                    stop_server(pid)
                elif action == "command":
                    command = str(self.body().get("command", "")).strip()
                    if not command or len(command) > 200 or "\n" in command or "\r" in command:
                        raise ValueError("Ungültiger Konsolenbefehl.")
                    proc = PROCESSES.get(pid)
                    if not proc or proc.poll() is not None:
                        raise ValueError("Server läuft nicht.")
                    proc.stdin.write(command + "\n")
                    proc.stdin.flush()
                    add_log(pid, "> " + command)
                elif action == "retry":
                    if p.get("installed"):
                        raise ValueError("Server ist bereits installiert.")
                    if JOBS.get(pid, {}).get("kind") == "import":
                        raise ValueError("Bitte die Modpack-Datei erneut importieren.")
                    JOBS[pid] = {"status": "working", "kind": "create", "progress": "Serverdateien werden installiert …"}
                    def retry():
                        try:
                            install_loader(p)
                            JOBS[pid].update(status="done", progress="Serverdateien sind bereit.")
                        except Exception as ex:
                            JOBS[pid].update(status="error", progress=str(ex))
                    threading.Thread(target=retry, daemon=True).start()
                elif action == "minekube":
                    setup_minekube(p)
                elif action == "minekube_off":
                    disable_minekube(p)
                elif action == "firewall":
                    request_firewall_rule(profile_java_path(p))
                elif action == "share_invite":
                    if not p.get("shared_role") and pid in PROCESSES and PROCESSES[pid].poll() is None:
                        raise ValueError("Server zuerst stoppen, bevor du ihn teilst.")
                    if p.get("minekube_enabled"):
                        raise ValueError("Minekube für diesen Server zuerst ausschalten. Der Proxy-Zugang lässt sich nicht sicher mit übernehmen.")
                    code = SHARED.invite(p)
                    with LOCK:
                        save_config()
                    return self.json({"code": code, "device_id": SHARED.ensure()})
                elif action == "share_accept":
                    device_id = str(self.body().get("device_id", ""))
                    SHARED.accept_peer(p, device_id)
                    with LOCK:
                        save_config()
                    threading.Thread(target=lambda: publish_shared_initial(p), daemon=True).start()
                elif action == "share_publish":
                    with WORLD_LOCK:
                        if pid in PROCESSES and PROCESSES[pid].poll() is None:
                            raise ValueError("Für eine sichere Weltübergabe den Server zuerst stoppen.")
                        if pid in SHARED_PUBLISHING:
                            raise ValueError("Die Weltübergabe läuft bereits.")
                        SHARED_PUBLISHING.add(pid)
                        try:
                            SHARED.publish(p, server_dir(p))
                            with LOCK:
                                save_config()
                        finally:
                            SHARED_PUBLISHING.discard(pid)
                elif action == "folder":
                    root = server_dir(p)
                    root.mkdir(parents=True, exist_ok=True)
                    if os.name == "nt":
                        os.startfile(root)
                    else:
                        subprocess.Popen(["xdg-open", str(root)])
                else:
                    raise ValueError("Unbekannte Aktion.")
                return self.json({"ok": True})
            if path == "/api/tunnel/select":
                import tkinter as tk
                from tkinter import filedialog
                window = tk.Tk()
                window.withdraw()
                window.attributes("-topmost", True)
                selected = filedialog.askopenfilename(title="playit-Agent auswählen", filetypes=[("Programme", "*.exe"), ("Alle Dateien", "*.*")])
                window.destroy()
                if selected:
                    if Path(selected).suffix.lower() != ".exe":
                        raise ValueError("Bitte eine .exe-Datei auswählen.")
                    CONFIG["playit_path"] = selected
                    save_config()
                return self.json({"path": CONFIG["playit_path"]})
            if path == "/api/tunnel/start":
                exe = Path(CONFIG.get("playit_path", ""))
                if not exe.is_file():
                    raise ValueError("Bitte zuerst den playit-Agenten auswählen.")
                if TUNNEL and TUNNEL.poll() is None:
                    return self.json({"ok": True})
                TUNNEL_CLAIM = ""
                TUNNEL_LOGS.clear()
                flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                TUNNEL = subprocess.Popen([str(exe), "--stdout"], cwd=exe.parent, stdout=subprocess.PIPE,
                                          stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                          bufsize=1, creationflags=flags)
                threading.Thread(target=read_tunnel, args=(TUNNEL,), daemon=True).start()
                return self.json({"ok": True})
            if path == "/api/tunnel/stop":
                if TUNNEL and TUNNEL.poll() is None:
                    TUNNEL.terminate()
                return self.json({"ok": True})
            raise ValueError("Unbekannte Aktion.")
        except Exception as ex:
            self.error_json(ex)

    def allowed_origin(self) -> bool:
        host = self.headers.get("Host", "")
        if host not in (f"{HOST}:{PORT}", f"localhost:{PORT}"):
            return False
        origin = self.headers.get("Origin")
        return not origin or origin in (f"http://{HOST}:{PORT}", f"http://localhost:{PORT}")


class LocalHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = False

    def server_bind(self) -> None:
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def main() -> None:
    global SERVER, REMOTE
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        server = SERVER = LocalHTTPServer((HOST, PORT), Handler)
    except OSError as ex:
        print(f"Minecraft Host kann Port {PORT} nicht öffnen: {ex}")
        print("Ist Minecraft Host bereits geöffnet? Schließe zuerst die andere Instanz.")
        try:
            input("Enter zum Beenden …")
        except EOFError:
            pass
        return
    print(f"Minecraft Host läuft unter http://{HOST}:{PORT}")
    # After a self-update the old browser tab reloads itself, so no second tab is opened.
    if "--no-browser" not in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open(f"http://{HOST}:{PORT}")).start()
    updater.start_background_checks()
    REMOTE = remote.RemoteServer(Handler)
    REMOTE.apply(CONFIG["remote"])
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        REMOTE.stop()
        for pid, proc in list(PROCESSES.items()):
            if proc.poll() is None:
                try:
                    STOP_REQUESTED.add(pid)
                    proc.stdin.write("stop\n")
                    proc.stdin.flush()
                    proc.wait(timeout=15)
                except Exception:
                    proc.terminate()
        for reader in list(READ_THREADS.values()):
            reader.join(timeout=120)
        if TUNNEL and TUNNEL.poll() is None:
            TUNNEL.terminate()
        stop_gate()
        SHARED.stop()
        server.server_close()


if __name__ == "__main__":
    main()
