<div align="center">

<img src="docs/media/host-server.webp" alt="Minecraft Host – Serverübersicht im Liquid-Glass-Design" width="100%">

# Minecraft Host

**Dein Minecraft-Java-Server auf deinem PC. Modpack rein, Start drücken, Freunde einladen.**

[![Release](https://img.shields.io/github/v/release/LvisXVI/minecraft-host?style=flat-square&color=6fe38a&label=Release)](https://github.com/LvisXVI/minecraft-host/releases/latest)
[![Windows](https://img.shields.io/badge/Windows-Portable-15263b?style=flat-square&logo=windows)](https://github.com/LvisXVI/minecraft-host/releases/latest)
[![Python](https://img.shields.io/badge/Python-nur%20Standardbibliothek-15263b?style=flat-square&logo=python&logoColor=white)](app.py)

[🌐 Website](https://lvisxvi.github.io/minecraft-host/) · [⬇️ Portable herunterladen](https://github.com/LvisXVI/minecraft-host/releases/latest) · [🧩 Modspire](https://lvisxvi.github.io/modspire/) · [🛠️ Mod Studio](https://lvisxvi.github.io/minecraft-mod-studio/)

</div>

---

Minecraft Host ist ein lokaler Servermanager für **Minecraft Java Edition unter Windows**. Die Oberfläche öffnet sich im Browser unter `127.0.0.1`, der Server selbst läuft auf deinem PC. Modrinth-Packs, `.mrpack`-Dateien und fertige Serverpacks werden importiert, Java wird automatisch erkannt, und für das Spielen mit Freunden gibt es gleich mehrere Wege – ohne Routerfreigabe.

## ✨ Was drin ist

| | |
|---|---|
| 📦 **Modrinth-Import** | `modrinth.com/modpack/...`-Link einfügen, Pack-Version wählen. Servergeeignete Dateien werden per HTTPS geladen und per SHA1/SHA512 geprüft. |
| 🗂️ **Serverpacks & `.mrpack`** | `.mrpack` importieren oder ein fertiges Serverpack als ZIP entpacken (Server-JAR oder Forge/NeoForge-Startargumente). |
| 🆕 **Neue Server** | Fabric und Vanilla direkt; Forge und NeoForge über den offiziellen Installer mit exakter Loader-Version. |
| ☕ **Java automatisch** | Vorhandene Java-8/17/21/25-Laufzeiten (auch die der Modrinth-App) werden erkannt. 1.21.x braucht Java 21, 26.x Java 25. |
| 📈 **Live-Daten** | Spielerzahl, lokale Antwortzeit, echte RAM-Nutzung, Laufzeit und ab 1.20.3 mittlere Tickzeit samt geschätzten TPS. |
| 💻 **Konsole** | Befehle wie `list`, `say Hallo` oder `whitelist add Name` direkt aus der App (ohne `/`). |
| 🤝 **Gemeinsam hosten** | Zwei PCs, eine Welt: Syncthing überträgt den geprüften Weltstand verschlüsselt, sodass ein Freund übernehmen kann, wenn dein PC aus ist. |

<p align="center"><img src="docs/media/host-connect.webp" alt="Radmin-VPN-Adresse, Firewall-Prüfung und gemeinsames Hosten" width="100%"></p>

## 🚀 Start

1. Unter [Releases](https://github.com/LvisXVI/minecraft-host/releases/latest) die **Portable-ZIP** laden und entpacken.
2. **`MinecraftHost.exe`** starten. Kein Python nötig. Das Dashboard öffnet sich unter `http://127.0.0.1:18765` und ist nur von diesem PC erreichbar.
3. Modpack importieren oder neuen Server erstellen, RAM/Port/Java einstellen.
4. Die [Minecraft-EULA](https://www.minecraft.net/eula) lesen, in der App bestätigen, speichern und den Server starten.

Aus dem Quellcode: Python 3.11+ installieren und `Start Minecraft Host.cmd` ausführen. Die App nutzt nur die Python-Standardbibliothek.

## 🌍 Freunde einladen

**Empfohlen: [Radmin VPN](https://www.radmin-vpn.com/) (kostenlos, ohne Routerfreigabe).** Alle Mitspieler treten demselben privaten Radmin-Netzwerk bei. Minecraft Host zeigt die Radmin-Adresse des hostenden PCs samt Port und prüft, ob die Windows-Firewall Java im (öffentlichen) Radmin-Netz durchlässt.

Weitere Wege:

- **Im selben WLAN:** Die LAN-Adresse wird automatisch angezeigt.
- **playit-Tunnel:** Der beiliegende, signierte [playit-Agent](https://github.com/playit-cloud/playit-agent/releases) wird aus der App gestartet; nach der Verknüpfung einen Minecraft-Java-Tunnel auf den lokalen Port anlegen.
- **Minekube Connect (Fabric, optional):** „Kostenlos einrichten“ lädt FabricProxy-Lite in den Server und startet den beiliegenden [Gate-Proxy](https://github.com/minekube/gate). Vorher einen vollständigen Beitritt mit genau deinem Modpack testen – manche Modpacks brechen über den Proxy mit einem Protokollfehler ab; dann eine direkte TCP-Verbindung nutzen.
- **Portfreigabe im Router:** Funktioniert auch, Minecraft Host ändert aber selbst keine Routereinstellungen.

> Für einen privaten Freundeskreis `whitelist on` aktivieren und Freunde mit `whitelist add Spielername` hinzufügen.

## 🤝 Gemeinsam mit einem Freund hosten

1. Beide PCs nutzen das portable Paket (inkl. `syncthing.exe`) und ein passendes Java.
2. Auf dem Hauptrechner den Server stoppen → **„Freund einladen“** → Code an den Freund senden.
3. Der Freund klickt **„Freundes-Server verbinden“**, fügt den Code ein und schickt seine **Geräte-ID** zurück.
4. Geräte-ID bestätigen. Der geprüfte Weltstand wird übertragen (beide PCs einmal gleichzeitig online).
5. Ist der Hauptrechner offline, startet der Freund den Server; nach dem Stoppen geht der neue Weltstand zurück.

Die App blockiert den Start, wenn die andere Kopie als aktiv gemeldet wird oder der Weltstand unvollständig ist. Nie absichtlich parallel starten. Die gemeinsame Kopie ersetzt kein eigenes Backup.

## 🔒 Daten & Sicherheit

- Server, Welten und Konfiguration: `%LOCALAPPDATA%\MinecraftHost\servers\<Server-ID>`; App-Einstellungen in `%LOCALAPPDATA%\MinecraftHost\config.json`.
- Das Dashboard bindet nur an `127.0.0.1` und blockiert Anfragen fremder Websites.
- Ohne Minekube läuft der Server mit `online-mode=true`. Mit Minekube bindet das Backend nur an `127.0.0.1` und Gate übernimmt die Kontoanmeldung.
- Es werden keine Dateien aus dem Serverordner hochgeladen. Proxy-Schlüssel und Minekube-Token bleiben unter `%LOCALAPPDATA%\MinecraftHost\gate`.

## 🛠️ Entwicklung & Build

```powershell
python app.py                      # Dashboard auf http://127.0.0.1:18765
pip install pyinstaller
pyinstaller MinecraftHost.spec     # dist\MinecraftHost.exe
```

Für das portable Paket `gate.exe`, `playit-agent.exe` und `syncthing.exe` neben die EXE legen.

## 📚 Technische Hinweise & Drittsoftware

- `.mrpack` nach der [Modrinth-Formatspezifikation](https://support.modrinth.com/en/articles/8802351-modrinth-modpack-format-mrpack); NeoForge nach der [offiziellen Serveranleitung](https://docs.neoforged.net/user/docs/server/).
- Im Release-Paket enthalten, jeweils aus den offiziellen Releases und per SHA256 geprüft: [playit-agent 0.17.1](https://github.com/playit-cloud/playit-agent/releases/tag/v0.17.1), [Gate 0.75.3](https://github.com/minekube/gate/releases/tag/v0.75.3), [Syncthing](https://github.com/syncthing/syncthing/releases). Es gelten deren eigene Lizenzen.
- Client-only-Mods oder fehlerhafte Pack-Metadaten können einen Serverstart verhindern. CurseForge-Clientexporte enthalten nicht alle Dateien – dort das **Serverpack** des Anbieters verwenden.

Minecraft Host ist ein unabhängiges Projekt und nicht mit Mojang oder Microsoft verbunden.
