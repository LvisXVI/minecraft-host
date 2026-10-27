'use strict';
// MC Host remote: controls Minecraft Host on the PC through its remote API (pairing code as bearer token).
const DEFAULT_PORT = 18766;
const POLL_MS = 3000;
const $ = (sel, root = document) => root.querySelector(sel);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

// connection storage: the Android bridge, or localStorage when the page runs in a desktop browser
const store = {
  get: key => { try { return window.HostApp ? HostApp.get(key) : (localStorage.getItem('mchost.' + key) || ''); } catch { return ''; } },
  set: (key, value) => { try { window.HostApp ? HostApp.set(key, value) : localStorage.setItem('mchost.' + key, value); } catch { /* not persisted */ } },
};
let conn = { base: store.get('base'), code: store.get('code') };
let state = null;
let pollTimer = 0;
let paused = false;
const stack = [];

function normalizeAddress(input) {
  let value = input.trim().replace(/\/+$/, '');
  if (!value) throw new Error('Bitte die Adresse vom PC eintragen.');
  if (!/^https?:\/\//i.test(value)) value = 'http://' + value;
  const url = new URL(value);
  if (!url.port) url.port = String(DEFAULT_PORT);
  return url.origin;
}

async function api(path, body, target = conn) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 10000);
  try {
    const res = await fetch(target.base + path, {
      method: body === undefined ? 'GET' : 'POST', signal: controller.signal,
      headers: { Authorization: 'Bearer ' + target.code, ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `Fehler ${res.status}`);
    return data;
  } catch (err) {
    if (err.name === 'AbortError' || err instanceof TypeError) throw new Error('PC nicht erreichbar. Ist Minecraft Host offen und das Handy im selben Netz oder in Tailscale?');
    throw err;
  } finally { clearTimeout(timer); }
}

function toast(message, bad = false) {
  const el = $('#toast');
  el.textContent = message; el.className = 'toast' + (bad ? ' bad' : ''); el.hidden = false;
  clearTimeout(toast.timer); toast.timer = setTimeout(() => { el.hidden = true; }, 3400);
}

async function act(button, work, done) {
  const label = button?.innerHTML;
  if (button) { button.disabled = true; button.innerHTML = '…'; }
  try { const result = await work(); if (done) toast(done); await refresh(); return result; }
  catch (err) { toast(err.message, true); }
  finally { if (button?.isConnected) { button.disabled = false; button.innerHTML = label; } }
}

/* ---------- navigation ---------- */
const screens = {};
function show(name, params = {}, { replace = false, back = false } = {}) {
  const host = $('#screens');
  const old = host.lastElementChild;
  if (old) { old.classList.add('leaving'); setTimeout(() => old.remove(), 300); }
  if (!back) { if (replace) stack.pop(); stack.push({ name, params }); }
  const el = document.createElement('section');
  el.className = 'screen' + (back ? ' back-in' : '');
  host.append(el);
  const screen = screens[name];
  $('#bar-title').textContent = screen.title(params);
  $('#bar-kicker').textContent = screen.kicker || 'MINECRAFT HOST';
  $('#back').hidden = stack.length < 2;
  $('#tabs').hidden = name === 'pair';
  document.querySelectorAll('[data-tab]').forEach(tab => tab.setAttribute('aria-current', String(tab.dataset.tab === (screen.tab || name))));
  el.update = screen.render(el, params) || (() => {});
  if (state) el.update(state);
}
function current() { return $('#screens').lastElementChild; }
function goBack() {
  if (stack.length < 2) return false;
  stack.pop();
  const prev = stack[stack.length - 1];
  show(prev.name, prev.params, { back: true });
  return true;
}
window.hostAppBack = goBack;
window.hostAppPause = () => { paused = true; };
window.hostAppResume = () => { paused = false; refresh(); };
$('#back').onclick = goBack;
document.querySelectorAll('[data-tab]').forEach(tab => tab.onclick = () => { stack.length = 0; show(tab.dataset.tab); });

/* ---------- polling ---------- */
async function refresh() {
  if (!conn.base || !conn.code) return;
  try {
    state = await api('/api/state');
    $('#link-dot').className = 'link-dot ok';
    const screen = current();
    if (screen?.update) screen.update(state);
  } catch (err) {
    $('#link-dot').className = 'link-dot bad';
    const screen = current();
    if (screen?.update) screen.update(state, err.message);
  }
}
function startPolling() {
  clearInterval(pollTimer);
  pollTimer = setInterval(() => { if (!paused && !document.hidden) refresh(); }, POLL_MS);
}
document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });

/* ---------- helpers for server views ---------- */
function status(p) {
  if (p.job?.status === 'working') return ['work', 'Einrichtung'];
  if (p.job?.status === 'error' && !p.installed) return ['err', 'Fehler'];
  if (p.running) return ['on', 'Online'];
  return ['', p.installed ? 'Offline' : 'Nicht bereit'];
}
function consoleHtml(logs) {
  return logs.map(line => {
    const kind = /\b(ERROR|FATAL|SEVERE)\b|Exception/.test(line) ? 'err' : /\bWARN/.test(line) ? 'warn' : /\]: <[^>]+> /.test(line) ? 'chat' : /joined the game|left the game/.test(line) ? 'join' : /Done \(|\bStarted\b/.test(line) ? 'ok' : '';
    const m = /^(\[[0-9:. ]+\])(.*)$/.exec(line);
    return `<span class="log-line${kind ? ' log-' + kind : ''}">${m ? `<span class="log-time">${esc(m[1])}</span>${esc(m[2])}` : esc(line)}</span>`;
  }).join('');
}
const LOADER_ICON = { fabric: '🧵', forge: '⚒', neoforge: '🦊', vanilla: '🟩' };

/* ---------- screens ---------- */
screens.pair = {
  title: () => 'Verbinden', kicker: 'EINRICHTUNG',
  render(el) {
    el.innerHTML = `<div class="pair-hero"><div class="launch-block"><span></span><span></span><span></span></div><h1>Mit deinem PC koppeln</h1><p class="muted">Steuere deine Minecraft-Server von überall.</p></div>
      <div class="card"><ol class="steps"><li>Am PC in Minecraft Host oben auf <strong>📱 Handy</strong> tippen und <strong>Fernsteuerung erlauben</strong> einschalten.</li><li>Für unterwegs: <a href="https://tailscale.com/download" style="color:var(--accent)">Tailscale</a> auf PC und Handy mit demselben Konto anmelden.</li><li>Adresse und Kopplungscode hier eintragen.</li></ol></div>
      <form class="card" id="pair-form"><div class="field"><span class="label">Adresse vom PC</span><input id="pair-base" inputmode="url" autocomplete="off" autocapitalize="off" placeholder="100.101.102.103:18766" value="${esc(conn.base.replace(/^https?:\/\//, ''))}"></div>
      <div class="field"><span class="label">Kopplungscode</span><input id="pair-code" class="mono" autocomplete="off" autocapitalize="characters" spellcheck="false" placeholder="XXXX-XXXX-XXXX-XXXX-XXXX-XXXX" value="${esc(conn.code)}"></div>
      <button class="btn primary block" type="submit">Verbinden</button></form>`;
    $('#pair-form', el).onsubmit = async event => {
      event.preventDefault();
      const button = event.target.querySelector('button');
      button.disabled = true; button.textContent = 'Verbinde …';
      try {
        const target = { base: normalizeAddress($('#pair-base', el).value), code: $('#pair-code', el).value.trim() };
        const info = await api('/api/remote/ping', undefined, target);
        conn = target; store.set('base', conn.base); store.set('code', conn.code);
        toast(`Verbunden mit ${info.name || 'deinem PC'}.`);
        startPolling(); await refresh();
        stack.length = 0; show('servers');
      } catch (err) { toast(err.message, true); button.disabled = false; button.textContent = 'Verbinden'; }
    };
  },
};

screens.servers = {
  title: () => 'Deine Server',
  render(el) {
    el.innerHTML = '<div id="list"><div class="card"><div class="progress"><i></i></div></div></div>';
    return (s, error) => {
      const list = $('#list', el);
      if (!s) { if (error) list.innerHTML = `<div class="card"><h2>Keine Verbindung</h2><p class="muted">${esc(error)}</p></div>`; return; }
      if (!s.profiles.length) { list.innerHTML = '<div class="empty">Noch keine Server.<br>Tippe unten auf <strong>＋ Neu</strong>.</div>'; return; }
      const key = JSON.stringify(s.profiles.map(p => [p.id, p.name, p.running, p.job?.status, p.metrics?.online, p.installed]));
      if (list.dataset.key === key) return;
      list.dataset.key = key;
      list.innerHTML = s.profiles.map(p => {
        const [cls, label] = status(p); const m = p.metrics || {};
        return `<button class="card server" type="button" data-id="${esc(p.id)}"><span class="server-icon">${LOADER_ICON[p.loader] || '⛏'}</span><span class="server-main"><strong>${esc(p.name)}</strong><small>${esc(p.minecraft || '?')} · ${esc(p.loader || 'Import')}${p.running && m.online != null ? ` · ${m.online}/${m.max} Spieler` : ''}</small></span><span class="badge ${cls}">${label}</span></button>`;
      }).join('');
      list.querySelectorAll('[data-id]').forEach(b => b.onclick = () => show('server', { id: b.dataset.id }));
    };
  },
};

screens.server = {
  title: ({ id }) => state?.profiles.find(p => p.id === id)?.name || 'Server', kicker: 'SERVER', tab: 'servers',
  render(el, { id }) {
    el.innerHTML = `<div class="card hero"><div class="row" style="align-items:center"><div><span class="label">Status</span><strong id="sv-status" style="font-size:20px">…</strong></div><span class="badge" id="sv-badge"></span></div>
        <div id="sv-job"></div><button class="btn big-action" id="sv-toggle" type="button" disabled>…</button></div>
      <div class="card"><span class="label">Live</span><div class="metrics" id="sv-metrics"></div></div>
      <div class="card"><span class="label">Adresse für Freunde</span><div class="address"><code id="sv-address">–</code></div><p class="hint" id="sv-address-hint"></p></div>
      <div class="card"><span class="label">Konsole</span><div class="console" id="sv-console"></div>
        <form class="command" id="sv-command"><input name="command" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="Befehl, z. B. list"><button class="btn primary" type="submit" aria-label="Senden">↑</button></form>
        <div class="quick">${['list', 'say Hallo!', 'time set day', 'weather clear', 'save-all', 'whitelist list'].map(c => `<button type="button" data-cmd="${esc(c)}">${esc(c)}</button>`).join('')}</div></div>
      <form class="card" id="sv-settings"><span class="label">Einstellungen</span>
        <div class="row"><div class="field"><span class="hint">RAM</span><select name="ram_mb">${[2048, 3072, 4096, 5120, 6144, 8192, 10240, 12288, 16384].map(n => `<option value="${n}">${n / 1024} GB</option>`).join('')}</select></div>
        <div class="field"><span class="hint">Port</span><input name="port" inputmode="numeric"></div></div>
        <div class="field"><span class="hint">Öffentliche Adresse (Tunnel)</span><input name="public_address" autocapitalize="off" placeholder="z. B. name.joinmc.link"></div>
        <label class="check"><input type="checkbox" name="eula"> Minecraft-EULA akzeptiert</label>
        <button class="btn block" type="submit">Speichern</button><p class="hint">Änderungen nur bei gestopptem Server.</p></form>`;
    const consoleEl = $('#sv-console', el);
    const send = async command => {
      try { await api(`/api/profiles/${id}/command`, { command }); await refresh(); consoleEl.scrollTop = consoleEl.scrollHeight; }
      catch (err) { toast(err.message, true); }
    };
    $('#sv-command', el).onsubmit = event => { event.preventDefault(); const input = event.target.command; const value = input.value.trim().replace(/^\//, ''); if (value) { input.value = ''; send(value); } };
    el.querySelectorAll('[data-cmd]').forEach(b => b.onclick = () => send(b.dataset.cmd));
    $('#sv-settings', el).onsubmit = event => {
      event.preventDefault(); const f = event.target;
      act(f.querySelector('button'), () => api(`/api/profiles/${id}/save`, { ram_mb: +f.ram_mb.value, port: +f.port.value, public_address: f.public_address.value.trim(), eula: f.eula.checked }), 'Gespeichert.');
    };
    let formFilled = false, lastLogs = -1;
    return (s, error) => {
      const p = s?.profiles.find(x => x.id === id);
      if (!p) { $('#sv-status', el).textContent = error ? 'Keine Verbindung' : 'Nicht gefunden'; return; }
      $('#bar-title').textContent = p.name;
      const [cls, label] = status(p); const m = p.metrics || {};
      $('#sv-status', el).textContent = label;
      const badge = $('#sv-badge', el); badge.className = 'badge ' + cls; badge.textContent = `${p.minecraft || '?'} · ${p.loader || '?'}`;
      $('#sv-job', el).innerHTML = p.job?.status === 'working' ? `<div class="progress"><i></i></div><p class="hint">${esc(p.job.progress)}</p>` : p.job?.status === 'error' && !p.installed ? `<p class="hint" style="color:var(--bad)">${esc(p.job.progress)}</p><button class="btn block" id="sv-retry" type="button">Erneut versuchen</button>` : '';
      const retry = $('#sv-retry', el); if (retry) retry.onclick = () => act(retry, () => api(`/api/profiles/${id}/retry`, {}), 'Einrichtung gestartet.');
      const toggle = $('#sv-toggle', el);
      toggle.disabled = !p.installed && !p.running;
      toggle.className = 'btn big-action ' + (p.running ? 'danger' : 'primary');
      toggle.textContent = p.running ? '■  Server stoppen' : p.eula ? '▶  Server starten' : '▶  EULA bestätigen & starten';
      toggle.onclick = () => act(toggle, async () => {
        if (p.running) return api(`/api/profiles/${id}/stop`, {});
        if (!p.eula) await api(`/api/profiles/${id}/save`, { ram_mb: p.ram_mb, port: p.port, public_address: p.public_address || '', eula: true });
        return api(`/api/profiles/${id}/start`, {});
      }, p.running ? 'Server wird gestoppt …' : 'Server startet …');
      const up = m.started_at ? Math.max(0, Date.now() / 1000 - m.started_at) : 0;
      $('#sv-metrics', el).innerHTML = [
        ['SPIELER', p.running && m.online != null ? `${m.online} / ${m.max}` : '—'],
        ['TPS', p.running && m.tps_estimate ? m.tps_estimate : '—'],
        ['RAM', p.running && m.ram_used_mb ? `${(m.ram_used_mb / 1024).toFixed(1)} / ${p.ram_mb / 1024} GB` : `${p.ram_mb / 1024} GB`],
        ['LAUFZEIT', p.running && up ? (up >= 3600 ? `${Math.floor(up / 3600)} h ${Math.floor(up % 3600 / 60)} min` : `${Math.floor(up / 60)} min`) : '—'],
      ].map(([k, v]) => `<div class="metric"><small>${k}</small><strong>${esc(v)}</strong></div>`).join('');
      $('#sv-address', el).textContent = p.public_address || (s.local_ip ? `${s.local_ip}:${p.port}` : `Port ${p.port}`);
      $('#sv-address-hint', el).textContent = p.public_address ? 'Über deinen Tunnel erreichbar.' : 'Nur im Heimnetz. Für Freunde von außen im Tab „Tunnel“ einen Tunnel starten.';
      if (p.logs.length !== lastLogs) {
        const atBottom = consoleEl.scrollHeight - consoleEl.scrollTop - consoleEl.clientHeight < 40;
        consoleEl.innerHTML = p.logs.length ? consoleHtml(p.logs) : '<span class="hint">Noch keine Ausgabe.</span>';
        if (atBottom || lastLogs < 0) consoleEl.scrollTop = consoleEl.scrollHeight;
        lastLogs = p.logs.length;
      }
      $('#sv-command', el).command.disabled = !p.running;
      if (!formFilled) {
        const f = $('#sv-settings', el); f.ram_mb.value = String(p.ram_mb); f.port.value = p.port; f.public_address.value = p.public_address || ''; f.eula.checked = Boolean(p.eula); formFilled = true;
      }
    };
  },
};

screens.create = {
  title: () => 'Neuer Server',
  render(el) {
    el.innerHTML = `<form class="card" id="new-form"><h2>Leerer Server</h2><p class="muted">Installiert Minecraft und den Loader auf dem PC.</p>
        <div class="field"><span class="label">Name</span><input name="name" value="Mein Server" maxlength="80"></div>
        <div class="row"><div class="field"><span class="label">Version</span><input name="minecraft" value="1.21.1" autocapitalize="off"></div>
        <div class="field"><span class="label">Loader</span><select name="loader"><option value="fabric">Fabric</option><option value="neoforge">NeoForge</option><option value="forge">Forge</option><option value="vanilla">Vanilla</option></select></div></div>
        <button class="btn primary block" type="submit">Server erstellen</button></form>
      <form class="card" id="mr-form"><h2>Modpack von Modrinth</h2><p class="muted">Link einfügen, Version wählen, fertig.</p>
        <div class="field"><input name="url" inputmode="url" autocapitalize="off" placeholder="https://modrinth.com/modpack/…"></div>
        <button class="btn block" type="submit">Versionen laden</button><div id="mr-versions"></div></form>`;
    $('#new-form', el).onsubmit = event => {
      event.preventDefault(); const f = event.target;
      act(f.querySelector('button'), async () => { const r = await api('/api/create', { name: f.name.value.trim(), minecraft: f.minecraft.value.trim(), loader: f.loader.value }); await refresh(); show('server', { id: r.id }); }, 'Server wird eingerichtet …');
    };
    $('#mr-form', el).onsubmit = event => {
      event.preventDefault(); const url = event.target.url.value.trim();
      act(event.target.querySelector('button'), async () => {
        const info = await api('/api/modrinth/inspect', { url });
        const box = $('#mr-versions', el);
        box.innerHTML = `<div class="field" style="margin-top:12px"><span class="label">${esc(info.title)}</span><select id="mr-version">${info.versions.map(v => `<option value="${esc(v.id)}" ${v.id === info.preferred ? 'selected' : ''}>${esc(v.name || v.version_number)} · ${esc(v.game_versions.slice(0, 2).join(', '))}</option>`).join('')}</select></div><button class="btn primary block" type="button" id="mr-import">Importieren</button>`;
        const go = $('#mr-import', el);
        go.onclick = () => act(go, async () => { const r = await api('/api/modrinth/import', { url, version_id: $('#mr-version', el).value }); await refresh(); show('server', { id: r.id }); }, 'Import gestartet.');
      });
    };
  },
};

screens.tunnel = {
  title: () => 'Tunnel',
  render(el) {
    el.innerHTML = `<div class="card"><h2>playit.gg-Tunnel</h2><p class="muted">Macht deinen Server für Freunde außerhalb deines Netzes erreichbar – kostenlos, ohne Router-Einstellungen.</p>
      <div id="tn-body"><div class="progress"><i></i></div></div></div>
      <div class="card"><h2>Tipp</h2><p class="muted">Die Tunnel-Adresse trägst du im Server unter „Öffentliche Adresse“ ein, damit sie hier und am PC angezeigt wird.</p></div>`;
    return s => {
      if (!s) return;
      const body = $('#tn-body', el);
      const key = [s.tunnel_running, s.tunnel_claim, Boolean(s.playit_path)].join('|');
      if (body.dataset.key === key) return;
      body.dataset.key = key;
      body.innerHTML = !s.playit_path ? '<p class="hint">Der playit-Agent ist am PC noch nicht eingerichtet. Wähle ihn einmal in Minecraft Host aus.</p>'
        : `<p><span class="badge ${s.tunnel_running ? 'on' : ''}">${s.tunnel_running ? 'Läuft' : 'Aus'}</span></p>${s.tunnel_claim ? `<p class="muted">Einmalig verknüpfen: <a href="${esc(s.tunnel_claim)}" style="color:var(--accent)">playit-Konto bestätigen ↗</a></p>` : ''}
           <button class="btn ${s.tunnel_running ? 'danger' : 'primary'} block" id="tn-toggle" type="button">${s.tunnel_running ? 'Tunnel stoppen' : 'Tunnel starten'}</button>`;
      const t = $('#tn-toggle', el);
      if (t) t.onclick = () => act(t, () => api(s.tunnel_running ? '/api/tunnel/stop' : '/api/tunnel/start', {}), s.tunnel_running ? 'Tunnel gestoppt.' : 'Tunnel startet …');
    };
  },
};

screens.settings = {
  title: () => 'Verbindung',
  render(el) {
    el.innerHTML = `<div class="card"><span class="label">PC</span><div class="address"><code>${esc(conn.base.replace(/^https?:\/\//, ''))}</code></div><p class="hint" id="st-state"></p></div>
      <div class="card"><span class="label">Minecraft Host</span><p class="muted" id="st-version">–</p></div>
      <button class="btn block" id="st-change" type="button">Anderen PC koppeln</button>
      <p class="hint" style="text-align:center;margin-top:18px">MC Host · Fernsteuerung für Minecraft Host<br>Quellcode: github.com/LvisXVI/minecraft-host</p>`;
    $('#st-change', el).onclick = () => { stack.length = 0; show('pair'); };
    return (s, error) => {
      $('#st-state', el).textContent = error ? error : 'Verbunden';
      if (s?.app) $('#st-version', el).textContent = `Version ${s.app.current}${s.app.available ? ` · Update ${s.app.latest} am PC verfügbar` : ''}`;
    };
  },
};

/* ---------- start ---------- */
(async () => {
  const launch = $('#launch');
  const minimum = new Promise(resolve => setTimeout(resolve, 1100));
  if (conn.base && conn.code) { startPolling(); await Promise.all([refresh(), minimum]); show('servers'); }
  else { await minimum; show('pair'); }
  launch.classList.add('done');
})();
