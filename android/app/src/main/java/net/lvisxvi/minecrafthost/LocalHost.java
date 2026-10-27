package net.lvisxvi.minecrafthost;

import android.app.ActivityManager;
import android.content.Context;
import android.content.Intent;
import android.os.Build;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.io.OutputStreamWriter;
import java.io.Writer;
import java.net.HttpURLConnection;
import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.NetworkInterface;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Deque;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Properties;
import java.util.UUID;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;

/**
 * Hosts Minecraft Java servers (Vanilla, Fabric) on the phone. Speaks the same small JSON API as
 * Minecraft Host on the PC (/api/state, /api/create, /api/profiles/{id}/{action}), so the bundled
 * UI works for both. The Java runtime ships as assets/runtime/jre.zip and is unpacked once.
 */
final class LocalHost {
    private static LocalHost instance;
    private static final int LOG_LINES = 180;
    private static final Pattern ID = Pattern.compile("[0-9a-f]{12}");
    private static final Pattern PROFILE_PATH = Pattern.compile("^/api/profiles/([0-9a-f]{12})/([a-z_]+)$");
    private static final Pattern VERSION = Pattern.compile("[0-9A-Za-z._-]{1,40}");
    private static final Pattern JOIN = Pattern.compile("\\]: ([A-Za-z0-9_]{1,16}) joined the game");
    private static final Pattern LEAVE = Pattern.compile("\\]: ([A-Za-z0-9_]{1,16}) left the game");

    private final Context context;
    private final File root;
    private final File runtimeDir;
    private final Map<String, JSONObject> profiles = new HashMap<>();
    private final Map<String, Process> processes = new HashMap<>();
    private final Map<String, Writer> inputs = new HashMap<>();
    private final Map<String, Deque<String>> logs = new HashMap<>();
    private final Map<String, JSONObject> jobs = new HashMap<>();
    private final Map<String, JSONObject> metrics = new HashMap<>();
    private final Map<String, List<String>> players = new HashMap<>();
    private volatile String runtimeStatus = "";

    static synchronized LocalHost get(Context context) {
        if (instance == null) instance = new LocalHost(context.getApplicationContext());
        return instance;
    }

    private LocalHost(Context context) {
        this.context = context;
        this.root = new File(context.getFilesDir(), "servers");
        this.runtimeDir = new File(context.getFilesDir(), "runtime");
        root.mkdirs();
        File[] dirs = root.listFiles();
        if (dirs != null) for (File dir : dirs) {
            try {
                JSONObject p = new JSONObject(read(new File(dir, "profile.json")));
                profiles.put(p.getString("id"), p);
            } catch (Exception ignored) { /* not a server folder */ }
        }
    }

    /** Entry point for the UI bridge. Returns {"status": code, "body": {...}}. */
    String call(String method, String path, String body) {
        JSONObject result = new JSONObject();
        try {
            JSONObject data = body == null || body.isEmpty() ? new JSONObject() : new JSONObject(body);
            Object answer = route(method, path, data);
            result.put("status", 200).put("body", answer);
        } catch (Exception ex) {
            try {
                result.put("status", 400).put("body", new JSONObject().put("error", ex.getMessage() == null ? ex.toString() : ex.getMessage()));
            } catch (JSONException ignored) { }
        }
        return result.toString();
    }

    private Object route(String method, String path, JSONObject data) throws Exception {
        if ("GET".equals(method) && "/api/state".equals(path)) return state();
        if ("POST".equals(method) && "/api/create".equals(path)) return create(data);
        Matcher m = PROFILE_PATH.matcher(path);
        if ("POST".equals(method) && m.matches()) {
            String id = m.group(1), action = m.group(2);
            JSONObject p = profile(id);
            switch (action) {
                case "start": start(p); break;
                case "stop": stop(id); break;
                case "command": command(id, data.optString("command", "")); break;
                case "save": save(p, data); break;
                case "retry": install(p); break;
                case "delete": delete(id); break;
                default: throw new IllegalArgumentException("Unbekannte Aktion.");
            }
            return new JSONObject().put("ok", true);
        }
        throw new IllegalArgumentException("Auf dem Handy nicht verfügbar.");
    }

    /* ---------- state ---------- */

    synchronized JSONObject state() throws JSONException {
        JSONArray list = new JSONArray();
        List<JSONObject> sorted = new ArrayList<>(profiles.values());
        Collections.sort(sorted, (a, b) -> Long.compare(a.optLong("created"), b.optLong("created")));
        for (JSONObject p : sorted) {
            String id = p.getString("id");
            JSONObject item = new JSONObject(p.toString());
            item.put("running", running(id));
            item.put("logs", new JSONArray(logs.containsKey(id) ? new ArrayList<>(logs.get(id)) : new ArrayList<String>()));
            item.put("job", jobs.containsKey(id) ? jobs.get(id) : JSONObject.NULL);
            item.put("metrics", metrics.containsKey(id) ? metrics.get(id) : new JSONObject());
            list.put(item);
        }
        String ip = "", tailscale = "";
        for (String[] address : addresses()) {
            if ("tailscale".equals(address[1]) && tailscale.isEmpty()) tailscale = address[0];
            else if (ip.isEmpty()) ip = address[0];
        }
        return new JSONObject().put("profiles", list).put("local_ip", ip).put("tailscale_ip", tailscale)
                .put("local", true).put("max_ram_mb", maxRamMb()).put("runtime", runtimeStatus)
                .put("tunnel_running", false).put("tunnel_claim", "").put("playit_path", "")
                .put("app", new JSONObject().put("current", BuildConfigVersion.name(context)).put("available", false));
    }

    private boolean running(String id) {
        Process proc = processes.get(id);
        return proc != null && proc.isAlive();
    }

    boolean anyRunning() {
        synchronized (this) {
            for (String id : processes.keySet()) if (running(id)) return true;
            return false;
        }
    }

    private JSONObject profile(String id) {
        JSONObject p = profiles.get(id);
        if (p == null) throw new IllegalArgumentException("Server nicht gefunden.");
        return p;
    }

    /* ---------- create & install ---------- */

    private synchronized JSONObject create(JSONObject data) throws Exception {
        String name = data.optString("name", "Handy-Server").trim();
        String version = data.optString("minecraft", "1.21.1").trim();
        String loader = data.optString("loader", "fabric");
        if (name.isEmpty() || name.length() > 80 || !VERSION.matcher(version).matches()) throw new IllegalArgumentException("Name oder Version ungültig.");
        if (!"fabric".equals(loader) && !"vanilla".equals(loader)) throw new IllegalArgumentException("Auf dem Handy gehen Fabric und Vanilla.");
        String id = UUID.randomUUID().toString().replace("-", "").substring(0, 12);
        JSONObject p = new JSONObject().put("id", id).put("name", name).put("minecraft", version).put("loader", loader)
                .put("loader_version", "").put("ram_mb", Math.min(2048, maxRamMb())).put("port", 25565)
                .put("public_address", "").put("eula", false).put("installed", false).put("jar", "")
                .put("created", System.currentTimeMillis());
        profiles.put(id, p);
        dir(id).mkdirs();
        persist(p);
        install(p);
        return new JSONObject().put("id", id);
    }

    private void install(JSONObject p) throws JSONException {
        String id = p.getString("id");
        job(id, "working", "Serverdateien werden geladen …");
        new Thread(() -> {
            try {
                File dir = dir(id);
                String version = p.getString("minecraft");
                String jar;
                if ("fabric".equals(p.getString("loader"))) {
                    String loaderVersion = firstStable(getJsonArray("https://meta.fabricmc.net/v2/versions/loader/" + version), "loader", "Fabric gibt es für diese Version nicht.");
                    String installer = firstStable(getJsonArray("https://meta.fabricmc.net/v2/versions/installer"), null, "Fabric-Installer nicht gefunden.");
                    download("https://meta.fabricmc.net/v2/versions/loader/" + version + "/" + loaderVersion + "/" + installer + "/server/jar", new File(dir, "fabric-server-launch.jar"), null);
                    synchronized (this) { p.put("loader_version", loaderVersion); }
                    jar = "fabric-server-launch.jar";
                } else {
                    JSONObject manifest = getJson("https://piston-meta.mojang.com/mc/game/version_manifest_v2.json");
                    String url = null;
                    JSONArray versions = manifest.getJSONArray("versions");
                    for (int i = 0; i < versions.length(); i++) if (version.equals(versions.getJSONObject(i).getString("id"))) url = versions.getJSONObject(i).getString("url");
                    if (url == null) throw new IOException("Minecraft " + version + " gibt es nicht.");
                    JSONObject server = getJson(url).getJSONObject("downloads").getJSONObject("server");
                    download(server.getString("url"), new File(dir, "server.jar"), server.getString("sha1"));
                    jar = "server.jar";
                }
                writeDefaults(dir, p.optInt("port", 25565));
                synchronized (this) { p.put("jar", jar).put("installed", true); persist(p); }
                job(id, "done", "Serverdateien sind bereit.");
            } catch (Exception ex) {
                job(id, "error", "Einrichtung fehlgeschlagen: " + ex.getMessage());
                log(id, "[MC Host] " + ex);
            }
        }, "install-" + id).start();
    }

    private static String firstStable(JSONArray list, String key, String missing) throws Exception {
        for (int i = 0; i < list.length(); i++) {
            JSONObject entry = list.getJSONObject(i);
            JSONObject item = key == null ? entry : entry.getJSONObject(key);
            if (item.optBoolean("stable", false)) return item.getString("version");
        }
        if (list.length() > 0) { JSONObject entry = list.getJSONObject(0); return (key == null ? entry : entry.getJSONObject(key)).getString("version"); }
        throw new IOException(missing);
    }

    /** Lighter defaults for a phone; existing values are kept. */
    private static void writeDefaults(File dir, int port) throws IOException {
        File file = new File(dir, "server.properties");
        Properties props = new Properties();
        if (file.exists()) try (InputStream in = new FileInputStream(file)) { props.load(in); }
        props.setProperty("server-port", String.valueOf(port));
        if (!props.containsKey("view-distance")) props.setProperty("view-distance", "6");
        if (!props.containsKey("simulation-distance")) props.setProperty("simulation-distance", "4");
        if (!props.containsKey("max-players")) props.setProperty("max-players", "8");
        if (!props.containsKey("motd")) props.setProperty("motd", "Gehostet mit MC Host auf dem Handy");
        if (!props.containsKey("online-mode")) props.setProperty("online-mode", "true");
        try (OutputStream out = new FileOutputStream(file)) { props.store(out, "Minecraft server properties"); }
    }

    /* ---------- run ---------- */

    private void start(JSONObject p) throws Exception {
        String id = p.getString("id");
        synchronized (this) {
            if (running(id)) return;
            if (!p.optBoolean("installed")) throw new IllegalStateException("Der Server ist noch nicht eingerichtet.");
            if (!p.optBoolean("eula")) throw new IllegalStateException("Bitte zuerst die Minecraft-EULA akzeptieren.");
            for (JSONObject other : profiles.values()) {
                if (!other.getString("id").equals(id) && running(other.getString("id")) && other.optInt("port") == p.optInt("port"))
                    throw new IllegalStateException("Port " + p.optInt("port") + " wird schon von „" + other.optString("name") + "“ genutzt.");
            }
        }
        job(id, "working", "Java wird vorbereitet …");
        new Thread(() -> {
            try {
                File java = ensureRuntime(id);
                File dir = dir(id);
                writeDefaults(dir, p.optInt("port", 25565));
                write(new File(dir, "eula.txt"), "eula=true\n");
                int ram = p.optInt("ram_mb", 2048);
                List<String> cmd = new ArrayList<>();
                cmd.add(java.getAbsolutePath());
                cmd.add("-Xmx" + ram + "m");
                cmd.add("-Xms" + Math.min(512, ram) + "m");
                cmd.add("-XX:+UseSerialGC");
                cmd.add("-Djava.awt.headless=true");
                cmd.add("-Dfile.encoding=UTF-8");
                cmd.add("-Djava.io.tmpdir=" + context.getCacheDir().getAbsolutePath());
                cmd.add("-jar");
                cmd.add(p.getString("jar"));
                cmd.add("nogui");
                ProcessBuilder builder = new ProcessBuilder(cmd).directory(dir).redirectErrorStream(true);
                File home = java.getParentFile().getParentFile();
                builder.environment().put("JAVA_HOME", home.getAbsolutePath());
                builder.environment().put("LD_LIBRARY_PATH", new File(home, "lib").getAbsolutePath() + ":" + new File(home, "lib/server").getAbsolutePath());
                builder.environment().put("HOME", dir.getAbsolutePath());
                builder.environment().put("TMPDIR", context.getCacheDir().getAbsolutePath());
                Process proc = builder.start();
                synchronized (this) {
                    processes.put(id, proc);
                    inputs.put(id, new OutputStreamWriter(proc.getOutputStream(), StandardCharsets.UTF_8));
                    players.put(id, new ArrayList<>());
                    metrics.put(id, new JSONObject().put("started_at", System.currentTimeMillis() / 1000.0).put("online", 0).put("max", maxPlayers(dir)));
                }
                synchronized (this) { jobs.remove(id); }
                log(id, "[MC Host] Server startet mit " + ram + " MB RAM …");
                ServerService.update(context);
                pump(id, proc);
            } catch (Exception ex) {
                job(id, "error", "Start fehlgeschlagen: " + ex.getMessage());
                log(id, "[MC Host] Start fehlgeschlagen: " + ex);
            }
        }, "start-" + id).start();
    }

    private void pump(String id, Process proc) {
        new Thread(() -> {
            try (BufferedReader reader = new BufferedReader(new InputStreamReader(proc.getInputStream(), StandardCharsets.UTF_8))) {
                String line;
                while ((line = reader.readLine()) != null) {
                    log(id, line);
                    track(id, line);
                }
            } catch (IOException ignored) { }
            int code;
            try { code = proc.waitFor(); } catch (InterruptedException ex) { code = -1; }
            synchronized (this) {
                processes.remove(id);
                inputs.remove(id);
                metrics.remove(id);
            }
            log(id, "[MC Host] Server beendet (Code " + code + ").");
            ServerService.update(context);
        }, "log-" + id).start();
    }

    private synchronized void track(String id, String line) {
        List<String> online = players.get(id);
        JSONObject m = metrics.get(id);
        if (online == null || m == null) return;
        Matcher join = JOIN.matcher(line), leave = LEAVE.matcher(line);
        if (join.find() && !online.contains(join.group(1))) online.add(join.group(1));
        if (leave.find()) online.remove(leave.group(1));
        try { m.put("online", online.size()); } catch (JSONException ignored) { }
    }

    private void stop(String id) throws IOException {
        Process proc;
        synchronized (this) { proc = processes.get(id); }
        if (proc == null || !proc.isAlive()) return;
        command(id, "stop");
        new Thread(() -> {
            try {
                for (int i = 0; i < 40 && proc.isAlive(); i++) Thread.sleep(500);
            } catch (InterruptedException ignored) { }
            if (proc.isAlive()) proc.destroyForcibly();
        }, "stop-" + id).start();
    }

    void stopAll() {
        List<String> ids;
        synchronized (this) { ids = new ArrayList<>(processes.keySet()); }
        for (String id : ids) try { stop(id); } catch (IOException ignored) { }
    }

    private void command(String id, String command) throws IOException {
        command = command.trim();
        if (command.isEmpty() || command.length() > 200 || command.contains("\n") || command.contains("\r")) throw new IllegalArgumentException("Ungültiger Konsolenbefehl.");
        Writer input;
        synchronized (this) { input = inputs.get(id); }
        if (input == null || !running(id)) throw new IllegalStateException("Server läuft nicht.");
        synchronized (input) { input.write(command + "\n"); input.flush(); }
        log(id, "> " + command);
    }

    private synchronized void save(JSONObject p, JSONObject data) throws Exception {
        if (running(p.getString("id"))) throw new IllegalStateException("Server zum Ändern der Einstellungen zuerst stoppen.");
        int ram = data.optInt("ram_mb", p.optInt("ram_mb"));
        int port = data.optInt("port", p.optInt("port"));
        if (ram < 512 || ram > maxRamMb() || port < 1024 || port > 65535) throw new IllegalArgumentException("RAM: 512–" + maxRamMb() + " MB; Port: 1024–65535.");
        String address = data.optString("public_address", p.optString("public_address")).trim();
        if (address.length() > 255 || address.contains(" ")) throw new IllegalArgumentException("Öffentliche Adresse ist ungültig.");
        p.put("ram_mb", ram).put("port", port).put("public_address", address).put("eula", data.optBoolean("eula", p.optBoolean("eula")));
        persist(p);
    }

    private synchronized void delete(String id) throws IOException {
        if (running(id)) throw new IllegalStateException("Server zuerst stoppen.");
        if (jobs.containsKey(id) && "working".equals(jobs.get(id).optString("status"))) throw new IllegalStateException("Bitte warten, bis die Einrichtung fertig ist.");
        profiles.remove(id);
        logs.remove(id);
        jobs.remove(id);
        if (!ID.matcher(id).matches()) return;
        deleteTree(dir(id));
    }

    /* ---------- Java runtime ---------- */

    /** Unpacks the bundled runtime on first use; returns bin/java. */
    private File ensureRuntime(String id) throws IOException {
        String version = BuildConfigVersion.code(context);
        File marker = new File(runtimeDir, "installed-" + version);
        File java = new File(runtimeDir, "jre/bin/java");
        synchronized (LocalHost.class) {
            if (!marker.exists() || !java.exists()) {
                job(id, "working", "Java wird beim ersten Start entpackt (einmalig, ca. 1 Minute) …");
                runtimeStatus = "unpacking";
                deleteTree(runtimeDir);
                File target = new File(runtimeDir, "jre");
                target.mkdirs();
                try (ZipInputStream zip = new ZipInputStream(context.getAssets().open("runtime/jre.zip"))) {
                    ZipEntry entry;
                    byte[] buffer = new byte[1 << 16];
                    String base = target.getCanonicalPath() + File.separator;
                    while ((entry = zip.getNextEntry()) != null) {
                        File out = new File(target, entry.getName());
                        if (!out.getCanonicalPath().startsWith(base)) throw new IOException("Unsicherer Pfad in der Java-Laufzeit.");
                        if (entry.isDirectory()) { out.mkdirs(); continue; }
                        out.getParentFile().mkdirs();
                        try (OutputStream o = new FileOutputStream(out)) { int n; while ((n = zip.read(buffer)) > 0) o.write(buffer, 0, n); }
                    }
                }
                for (String name : new String[]{"bin/java", "lib/jspawnhelper"}) {
                    File f = new File(target, name);
                    if (f.exists() && !f.setExecutable(true, true)) throw new IOException(name + " konnte nicht ausführbar gemacht werden.");
                }
                marker.createNewFile();
                runtimeStatus = "ready";
            }
        }
        return java;
    }

    /* ---------- helpers ---------- */

    private File dir(String id) {
        return new File(root, id);
    }

    private void persist(JSONObject p) {
        try { write(new File(dir(p.getString("id")), "profile.json"), p.toString(2)); }
        catch (Exception ex) { log(p.optString("id"), "[MC Host] Konnte Einstellungen nicht speichern: " + ex.getMessage()); }
    }

    private synchronized void job(String id, String status, String progress) {
        try { jobs.put(id, new JSONObject().put("status", status).put("progress", progress)); } catch (JSONException ignored) { }
    }

    private synchronized void log(String id, String line) {
        Deque<String> lines = logs.get(id);
        if (lines == null) { lines = new ArrayDeque<>(); logs.put(id, lines); }
        lines.addLast(line);
        while (lines.size() > LOG_LINES) lines.removeFirst();
    }

    private static int maxPlayers(File dir) {
        Properties props = new Properties();
        try (InputStream in = new FileInputStream(new File(dir, "server.properties"))) { props.load(in); }
        catch (IOException ignored) { }
        try { return Integer.parseInt(props.getProperty("max-players", "8").trim()); } catch (NumberFormatException ex) { return 8; }
    }

    /** About half of the phone's memory, so Android keeps room for itself. */
    int maxRamMb() {
        ActivityManager.MemoryInfo info = new ActivityManager.MemoryInfo();
        ((ActivityManager) context.getSystemService(Context.ACTIVITY_SERVICE)).getMemoryInfo(info);
        int half = (int) (info.totalMem / (1024 * 1024) / 2);
        return Math.max(1024, Math.min(half, 8192)) / 256 * 256;
    }

    static List<String[]> addresses() {
        List<String[]> result = new ArrayList<>();
        try {
            for (NetworkInterface nic : Collections.list(NetworkInterface.getNetworkInterfaces())) {
                if (!nic.isUp() || nic.isLoopback()) continue;
                for (InetAddress address : Collections.list(nic.getInetAddresses())) {
                    if (!(address instanceof Inet4Address) || address.isLinkLocalAddress()) continue;
                    byte[] b = address.getAddress();
                    boolean tailnet = (b[0] & 0xff) == 100 && (b[1] & 0xc0) == 64;
                    String kind = tailnet ? "tailscale" : nic.getName().startsWith("wlan") ? "wifi" : "other";
                    String[] entry = {address.getHostAddress(), kind};
                    if ("wifi".equals(kind)) result.add(0, entry); else result.add(entry);
                }
            }
        } catch (Exception ignored) { }
        return result;
    }

    private static JSONObject getJson(String url) throws Exception { return new JSONObject(fetch(url)); }
    private static JSONArray getJsonArray(String url) throws Exception { return new JSONArray(fetch(url)); }

    private static String fetch(String url) throws IOException {
        HttpURLConnection c = open(url);
        try (InputStream in = c.getInputStream()) { return new String(readAll(in), StandardCharsets.UTF_8); }
        finally { c.disconnect(); }
    }

    private static void download(String url, File target, String sha1) throws Exception {
        HttpURLConnection c = open(url);
        File part = new File(target.getPath() + ".part");
        MessageDigest digest = MessageDigest.getInstance("SHA-1");
        try (InputStream in = c.getInputStream(); OutputStream out = new FileOutputStream(part)) {
            byte[] buffer = new byte[1 << 16];
            int n;
            while ((n = in.read(buffer)) > 0) { out.write(buffer, 0, n); digest.update(buffer, 0, n); }
        } finally { c.disconnect(); }
        if (sha1 != null) {
            StringBuilder hex = new StringBuilder();
            for (byte b : digest.digest()) hex.append(String.format("%02x", b));
            if (!hex.toString().equalsIgnoreCase(sha1)) { part.delete(); throw new IOException("Prüfsumme stimmt nicht – Download beschädigt."); }
        }
        if (target.exists() && !target.delete()) throw new IOException("Alte Datei kann nicht ersetzt werden.");
        if (!part.renameTo(target)) throw new IOException("Datei konnte nicht gespeichert werden.");
    }

    private static HttpURLConnection open(String url) throws IOException {
        if (!url.startsWith("https://")) throw new IOException("Nur HTTPS-Downloads sind erlaubt.");
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        c.setConnectTimeout(15000);
        c.setReadTimeout(60000);
        c.setRequestProperty("User-Agent", "MCHost-Android (github.com/LvisXVI/minecraft-host)");
        if (c.getResponseCode() >= 400) { int code = c.getResponseCode(); c.disconnect(); throw new IOException("Server antwortet mit " + code + "."); }
        return c;
    }

    private static byte[] readAll(InputStream in) throws IOException {
        java.io.ByteArrayOutputStream out = new java.io.ByteArrayOutputStream();
        byte[] buffer = new byte[1 << 14];
        int n;
        while ((n = in.read(buffer)) > 0) out.write(buffer, 0, n);
        return out.toByteArray();
    }

    private static String read(File file) throws IOException {
        try (InputStream in = new FileInputStream(file)) { return new String(readAll(in), StandardCharsets.UTF_8); }
    }

    private static void write(File file, String text) throws IOException {
        try (OutputStream out = new FileOutputStream(file)) { out.write(text.getBytes(StandardCharsets.UTF_8)); }
    }

    private static void deleteTree(File file) {
        File[] children = file.listFiles();
        if (children != null) for (File child : children) deleteTree(child);
        file.delete();
    }

    /** Small helper: version info without generated BuildConfig. */
    static final class BuildConfigVersion {
        static String name(Context c) {
            try { return c.getPackageManager().getPackageInfo(c.getPackageName(), 0).versionName; } catch (Exception ex) { return "?"; }
        }
        @SuppressWarnings("deprecation")
        static String code(Context c) {
            try {
                android.content.pm.PackageInfo info = c.getPackageManager().getPackageInfo(c.getPackageName(), 0);
                return String.valueOf(Build.VERSION.SDK_INT >= 28 ? info.getLongVersionCode() : info.versionCode);
            } catch (Exception ex) { return "0"; }
        }
    }

    static Intent serviceIntent(Context context) {
        return new Intent(context, ServerService.class);
    }
}
