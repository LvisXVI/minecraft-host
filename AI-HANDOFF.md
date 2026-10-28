# AI handoff

## 2026-10-28

- Local build `1.2.4-local`: new Windows icon in `MinecraftHost.ico` and source artwork in `branding/minecraft-host-icon.png`. The desktop standalone EXE and its existing shortcut were updated; no release or commit was made.
- The last local dashboard tab closing shuts down the app after a short reload grace period. Browser sessions and an event stream detect closed tabs/windows; other open tabs and reloads keep the app running. Managed Minecraft servers receive `stop` and up to 120 seconds to exit before forced termination; helpers are stopped afterwards. Remote endpoints cannot access the session API.
- Verification: 30 Python tests passed, including a real dummy managed process that received `stop`. The built one-file EXE was exercised in an isolated data directory with two Chrome tabs, a reload, and a browser-window close; the EXE and HTTP server exited after the last window closed. A real Minecraft server/world was not used in these tests.
- Build output: `Codex/2026-10-27/ich/outputs/MinecraftHost-Update`; desktop and output EXE SHA-256 `F0692DCB11DC796F20F278546B960F548D80790EA1DC73045F36FDD9433295F9`. Previous desktop EXE and shortcut are backed up under the same task's `work/` folder.
- Next: user can try the updated desktop shortcut; if hosting a server, closing the last dashboard also stops it.

## 2026-10-28 — private tunnel

- Minecraft Host `1.2.5` adds a per-server private TCP forward through the locally installed Tailscale client. The local dashboard can create, inspect, copy and remove its own forward; changing the Minecraft port retargets that forward while preserving the address port. It leaves unrelated Tailscale Serve ports alone. Minekube Gate cannot run alongside this direct forward.
- Friends need Tailscale installed and access to whichever PC currently hosts. The world continues to move separately through Syncthing. Tailscale is currently installed but not signed in on this PC, so a live cross-PC Minecraft join was unavailable.
- Verification: 39 Python tests passed, including simulated Tailscale CLI conflicts, a local HTTP create/remove round trip and the browser UI in Tailscale's disconnected state. `node --check app.js` and `git diff --check` passed. Portable and standalone Windows builds completed; the final standalone EXE was started from an isolated data folder and returned version 1.2.5 and the expected Tailscale status via `/api/state`. The portable ZIP passed integrity checks.
- World handoff was additionally exercised end to end with two isolated real Syncthing v2.1.7 instances on this PC: owner published a sample world, guest claimed and changed it, guest published it, owner claimed the change. This verifies the local transfer path; two physical PCs and a live Minecraft server still need a friend-assisted test.
- The Desktop standalone EXE and existing shortcut now point to build 1.2.5, SHA-256 `88B0C6CE67C93BD19AAB20B5F2DCE44EF930D2F491EDADB5D47CA59776A4F126`; the previous Desktop EXE was backed up under this chat's `work/` folder.
- Next: sign in to Tailscale on both PCs, share the hosting device, and test one full Minecraft join plus world handoff with the friend. The release step is tracked in this chat's final update.
