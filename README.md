<div align="center">

<img src="docs/media/host-server.webp" alt="Minecraft Host server overview in the Liquid Glass design" width="100%">

# Minecraft Host

**Your Minecraft Java server, on your PC. Drop in a modpack, press start, invite your friends.**

[![Release](https://img.shields.io/github/v/release/LvisXVI/minecraft-host?style=flat-square&color=6fe38a&label=release)](https://github.com/LvisXVI/minecraft-host/releases/latest)
[![Windows](https://img.shields.io/badge/Windows-portable-15263b?style=flat-square&logo=windows)](https://github.com/LvisXVI/minecraft-host/releases/latest)
[![Python](https://img.shields.io/badge/Python-standard%20library%20only-15263b?style=flat-square&logo=python&logoColor=white)](app.py)

[🌐 Website](https://lvisxvi.github.io/minecraft-host/) · [⬇️ Download portable](https://github.com/LvisXVI/minecraft-host/releases/latest) · [🧩 Modspire](https://lvisxvi.github.io/modspire/) · [🛠️ Mod Studio](https://lvisxvi.github.io/minecraft-mod-studio/)

</div>

---

Minecraft Host is a local server manager for **Minecraft Java Edition on Windows**. The dashboard opens in your browser on `127.0.0.1`, the server runs on your own PC. It imports Modrinth packs, `.mrpack` files and ready-made server packs, detects Java automatically, and gives you several ways to play with friends – no router configuration required.

> The app interface is currently in German; this documentation is in English.

## ✨ Features

| | |
|---|---|
| 📦 **Modrinth import** | Paste a `modrinth.com/modpack/...` link and pick the pack version. Server-side files are downloaded over HTTPS and verified with SHA1/SHA512; `overrides` and `server-overrides` are applied. |
| 🗂️ **Server packs & `.mrpack`** | Import a `.mrpack`, or unpack a ready-made server pack ZIP (server JAR or Forge/NeoForge start arguments). |
| 🆕 **New servers** | Fabric and Vanilla directly; Forge and NeoForge via the official installer with an exact loader version. |
| ☕ **Java, automatically** | Installed Java 8/17/21/25 runtimes are detected. If the matching version is missing, Minecraft Host downloads a verified Eclipse Temurin JDK on first use into `%LOCALAPPDATA%\MinecraftHost\runtimes`. No administrator rights or system-wide installation are needed. |
| 📈 **Live data** | Player count, local response time, real RAM usage, uptime and, from 1.20.3, mean tick time with estimated TPS. |
| 💻 **Console** | Run `list`, `say Hello` or `whitelist add Name` from the app (without `/`). |
| 🤝 **Host together** | Two PCs, one world: Syncthing transfers the verified world state encrypted, so a friend can take over while your PC is off. |
| 🔄 **Self-update** | The portable package can download and install newer releases after SHA-256 verification. The single EXE shows new releases but must be replaced manually; servers and worlds remain in local app data. |
| 📱 **Android app** | Host Vanilla/Fabric servers directly on your phone, or control this PC: the **MC Host** app starts and stops servers, shows the colour-coded console, sends commands, changes settings, creates servers, imports Modrinth packs and switches the tunnel – at home or, with Tailscale, from anywhere. |
| 🎨 **Five designs** | Cherry Grove, Aurora, Crystal Valley, Nether and Daylight – a Liquid Glass interface with smooth transitions, switchable in the top bar. |

<p align="center"><img src="docs/media/host-connect.webp" alt="Radmin VPN address, firewall check and shared hosting" width="100%"></p>

## 🚀 Getting started

1. Download the **portable ZIP** from [Releases](https://github.com/LvisXVI/minecraft-host/releases/latest) and unzip it.
2. Run **`MinecraftHost.exe`** from the portable package (keep its `_internal` folder) or the single **`MinecraftHost-Standalone.exe`** – no Python needed. The dashboard opens at `http://127.0.0.1:18765` and is only reachable from this PC.
3. Import a modpack or create a new server, then set RAM and port. Leave the Java field blank for automatic selection and download.
4. Read the [Minecraft EULA](https://www.minecraft.net/eula), accept it in the app, save and start the server.

From source: install Python 3.11+ and run `Start Minecraft Host.cmd`. The app only uses the Python standard library.

Closing the last Minecraft Host browser tab closes the app and gracefully stops its running Minecraft servers and helpers. Reloading the dashboard or leaving another Host tab open keeps it running. A `--no-browser` start stays available until a dashboard has been opened and then closed.

## 🔄 Updates

From 1.1.0 on, Minecraft Host checks GitHub for new releases at start and every few hours. Click **Update** in the top bar (or the version number to check now), read the release notes and choose **Update now**. The app downloads the portable ZIP, verifies it against `SHA256SUMS.txt`, closes itself, swaps its files and starts again; the browser tab reloads on its own. Stop running servers first – the update refuses to start while one is running. Coming from 1.0.x, download 1.1.0 once by hand.

> **Antivirus note:** Microsoft Defender's machine-learning scanner sometimes flags Python-built apps (reported as `Trojan:Win32/Bearfoos!ml`). From 1.1.0 on the app ships as a normal folder build without self-extraction or UPX packing, which avoids the trigger in testing. If it is flagged anyway, compare the file with `SHA256SUMS.txt` and report it to Microsoft as a false positive.

## 📱 Android app

The **MC Host** app has two modes – switch any time under *Verbindung*:

- **Control your PC** (below): everything Minecraft Host does, from your phone.
- **Host on the phone:** create a Vanilla or Fabric server (Minecraft 1.18+) right on the phone and start it with the bundled Java 25. Friends join on the same Wi-Fi, through Tailscale, or through a playit address. A notification keeps the server alive with the screen off; use a charger and Wi-Fi. Needs an ARM64 phone; 2 GB server RAM and a few players work well, large modpacks don't. The app targets Android API 28 on purpose: newer targets forbid starting a bundled runtime from app storage.

### Remote control setup

1. Download `MCHost-Remote-*.apk` from the [Android release](https://github.com/LvisXVI/minecraft-host/releases?q=android) and install it (allow installs from your browser once).
2. In Minecraft Host 1.2.0+, click **📱 Handy** in the top bar and turn on **Fernsteuerung erlauben**. The dialog shows the PC's addresses and a pairing code.
3. Enter address and code in the app. On the home Wi-Fi the LAN address works; **away from home** install [Tailscale](https://tailscale.com/download) on PC and phone with the same account and use the `100.x` address.

The remote listener (port 18766) is off by default, needs the 120-bit pairing code on every request, blocks a client after repeated wrong codes and only exposes server control – self-update, file dialogs, Explorer, firewall prompts and Syncthing pairing stay on the PC. It speaks plain HTTP, so use it in your home network or over Tailscale (WireGuard-encrypted), not through a public port forward. **Neuen Code erzeugen** disconnects every paired phone.

## 🌍 Inviting friends

**Recommended: [Radmin VPN](https://www.radmin-vpn.com/) (free, no port forwarding).** Everyone joins the same private Radmin network. Minecraft Host shows the hosting PC's Radmin address with the port and checks whether Windows Firewall lets Java through on the (public) Radmin network.

**Also free: [Tailscale](https://tailscale.com/download).** Install and sign in on the hosting PC, then click **Create private tunnel** in Minecraft Host. The app configures a persistent Tailscale Serve TCP forward for that server and shows the address to copy. Friends install Tailscale too and need access to the hosting PC (share it from the Tailscale admin console or invite them to your tailnet). They join with the displayed `100.x:port` address. This needs no router changes or VPS; it is private to the authorized devices and works only while the hosting PC and Minecraft server are running. The app never opens its local dashboard through this tunnel. When a profile port changes, Minecraft Host updates its own forward while keeping the address port stable. Removing this forward does not block direct VPN or LAN access to a running Minecraft server; use the Minecraft whitelist to control players. Tailscale Funnel only forwards HTTPS, so it cannot expose Minecraft publicly. A shared world still syncs separately through Syncthing before the other PC hosts it.

Other options:

- **Same Wi-Fi:** the LAN address is shown automatically.
- **playit tunnel:** start the bundled, signed [playit agent](https://github.com/playit-cloud/playit-agent/releases) from the app; after linking it, create a Minecraft Java tunnel to your local port.
- **Minekube Connect (Fabric, optional):** "set up" installs version-matched FabricProxy-Lite and its required Fabric API, starts the bundled [Gate proxy](https://github.com/minekube/gate), and shows whether Connect is actually linked. Gate creates a local endpoint token on first connection. The address works while Gate and the Minecraft server are running. Test a full join with your exact modpack first – some modpacks fail through the proxy with a protocol error; use a direct TCP connection then.
- **Port forwarding:** works too, but Minecraft Host never changes router settings itself.

> For a private group, enable `whitelist on` and add friends with `whitelist add PlayerName`.

## 🤝 Hosting together with a friend

1. Both PCs use either the complete portable package or the standalone EXE. If Java is missing, it downloads automatically on first use while online.
2. On the main PC, stop the server → **invite friend** → send the code.
3. Your friend clicks **connect friend's server**, pastes the code and sends back their **device ID**.
4. Confirm the device ID. The verified world state is transferred (both PCs online at the same time once).
5. While the main PC is offline, your friend starts the server; after stopping, the new world state travels back.

The app blocks a start while the other copy reports itself as active or the world state is incomplete. Never start both on purpose. The shared copy does not replace your own backups.

## 🔒 Data & security

- Servers, worlds and configuration: `%LOCALAPPDATA%\MinecraftHost\servers\<server-id>`; app settings in `%LOCALAPPDATA%\MinecraftHost\config.json`.
- The dashboard binds to `127.0.0.1` only and blocks requests from other websites.
- Without Minekube the server runs with `online-mode=true`. With Minekube the backend binds to `127.0.0.1` only and Gate handles account authentication.
- No files from the server folder are uploaded. Proxy keys and the Minekube token stay in `%LOCALAPPDATA%\MinecraftHost\gate`.

## 🛠️ Development & build

```powershell
python app.py                      # dashboard on http://127.0.0.1:18765
pip install pyinstaller
python -m unittest discover tests  # updater tests
pyinstaller MinecraftHost.spec     # dist\MinecraftHost\ (MinecraftHost.exe + _internal)
$env:MINECRAFT_HOST_TOOLS_DIR = 'C:\path\to\official\helpers'
pyinstaller MinecraftHost-onefile.spec  # dist\MinecraftHost-Standalone.exe
```

For the portable package, place `gate.exe`, `playit-agent.exe` and `syncthing.exe` next to the EXE.
For the standalone build, those verified helper EXEs are embedded in the single file and unpacked into a temporary directory while the app runs. Java runtimes, server files, worlds and settings stay in `%LOCALAPPDATA%\MinecraftHost`. Replace the standalone EXE manually when updating; the in-app updater only installs portable releases.

## 📚 Technical notes & third-party software

- **Android app:** bundles OpenJDK 25 for Android (GPL-2.0 with Classpath Exception) from [AngelAuraMC/angelauramc-openjdk-build](https://github.com/AngelAuraMC/angelauramc-openjdk-build), unmodified apart from removed developer tools; source at [openjdk.org](https://openjdk.org/).

- `.mrpack` handling follows the [Modrinth format specification](https://support.modrinth.com/en/articles/8802351-modrinth-modpack-format-mrpack); NeoForge setup follows the [official server guide](https://docs.neoforged.net/user/docs/server/).
- Bundled in the release, each taken from official releases and checked by SHA256: [playit-agent 0.17.1](https://github.com/playit-cloud/playit-agent/releases/tag/v0.17.1), [Gate 0.75.3](https://github.com/minekube/gate/releases/tag/v0.75.3), [Syncthing](https://github.com/syncthing/syncthing/releases). Their own licences apply.
- Client-only mods or broken pack metadata can still prevent a server from starting. CurseForge client exports don't contain every file – use the creator's **server pack** instead.

Minecraft Host is an independent project and is not affiliated with Mojang or Microsoft.
