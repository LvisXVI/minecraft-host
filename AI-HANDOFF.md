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
- Release `v1.2.5` published from commit `3772bd6` with portable ZIP, standalone EXE and SHA256SUMS. GitHub reports the same SHA-256 digests as the local assets, and `main` was pushed. The Desktop copy is version 1.2.5.
- Next: sign in to Tailscale on both PCs, share the hosting device, and test one full Minecraft join plus world handoff with the friend.

## 2026-10-28 — Android app 1.2.0 (Claude)

- Only `android/` changed (commit 73ea5cd, tag `android-v1.2.0`, built by the Android GitHub workflow); the Windows 1.2.5 release is untouched.
- New in phone-hosting mode: Modrinth modpack import (`/api/modrinth/inspect|import` in `LocalHost.java`; Fabric packs only, hosts limited to Modrinth/GitHub/GitLab, sha1-checked, path-checked), world backups (zip, last 8, restore with automatic safety backup), editable server.properties (MOTD, difficulty, game mode, max players, view distance, PvP, whitelist, online mode), player management buttons (op, whitelist, kick, ban) and a copy-address button.
- Checked: `node --check` on `app.js`, and the UI in an Electron stub of `HostApp` (cards render, save sends props, import form lists versions). The Java code was not compiled locally (no JDK); the CI build is the first compile. Not tested on a real phone.
- Next: confirm the CI build, install the APK on a phone, import a small Fabric pack end to end. The Android launcher icon is still the old grass block; align it with the new Windows icon if wanted.

## 2026-10-28 — living backgrounds and built-in music (Claude)

- Backgrounds: `docs/living-bg.js` runs a WebGL shader over the still picture `docs/media/living/<site>-bg.jpg`. `<site>-mask-a.png` (R aurora, G water, B glow) and `<site>-mask-b.png` (R stars, G mist, B waterfalls) decide which regions move. Blossom/spark particles come from a canvas layer. The old looping videos are no longer referenced; the files stay in the repo.
- Music: `docs/music-player.js` generates an ambient track with Web Audio. It starts on page load and falls back to the first tap or key press if the browser blocks sound. The old Spotify embed showed an empty panel.
- Checks: served the page locally; WebGL canvas ready, no console errors, music button present. Not yet checked on the live Pages build, on Safari, or on a phone.
- Uncommitted local edits to `docs/index.html` and `docs/liquid.css` (music controls, layout fixes) were not written by this work and were left out of the commit.
- Next: confirm the live build and motion/audio in a real browser; check performance on a low-end device.
