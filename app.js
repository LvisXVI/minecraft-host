const $ = (s, root = document) => root.querySelector(s);
const main = $('#main');
let state = {profiles: [], java: [], local_ip: '', radmin: {}, playit_path: '', tunnel_running: false, tunnel_claim: '', gate_running: false, gate_connected: false};
let selected = null;
let modalOpen = false;
let lastLogCount = 0;

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function showToast(message) {
  const el = $('#toast'); el.textContent = message; el.classList.add('show');
  clearTimeout(showToast.timer); showToast.timer = setTimeout(() => el.classList.remove('show'), 5000);
}
async function api(path, data, raw = false) {
  const options = {method: 'POST', headers: {}};
  if (raw) { options.body = data.file; options.headers['X-Filename'] = data.file.name; }
  else { options.headers['Content-Type'] = 'application/json'; options.body = JSON.stringify(data ?? {}); }
  const res = await fetch(path, options);
  const json = await res.json();
  if (!res.ok) throw new Error(json.error || 'Aktion fehlgeschlagen.');
  return json;
}
async function refresh(force = false) {
  try {
    const res = await fetch('/api/state', {cache:'no-store'});
    if (!res.ok) throw new Error('Verbindung zur App verloren.');
    state = await res.json();
    if (!selected && state.profiles.length) selected = state.profiles[0].id;
    if (selected && !state.profiles.some(p => p.id === selected)) selected = state.profiles[0]?.id || null;
    const editing = ['INPUT','SELECT','TEXTAREA'].includes(document.activeElement?.tagName);
    // Polling returns the same state most of the time; rebuild the DOM only when something
    // visible changed (logs are patched separately, uptime is shown per minute).
    const key = viewKey();
    if (force || (!editing && !modalOpen && key !== lastViewKey)) { render(); lastViewKey = key; }
    else updateLogs();
    renderUpdate();
    endLaunch();
  } catch (err) { showToast(err.message); }
}
let lastViewKey = '';
function viewKey() {
  const profiles = state.profiles.map(p => ({...p, logs: p.logs?.length || 0}));
  return JSON.stringify([{...state, profiles}, selected, Math.floor(Date.now() / 60000)]);
}
function profile() { return state.profiles.find(p => p.id === selected); }
function status(p) {
  if (p.running) return ['ONLINE','online'];
  if (p.job?.status === 'working') return ['WIRD EINGERICHTET','busy'];
  if (p.job?.status === 'error') return ['FEHLER',''];
  return [p.installed ? 'OFFLINE' : 'NICHT BEREIT',''];
}
function renderSidebar() {
  $('#server-count').textContent = String(state.profiles.length).padStart(2,'0');
  $('#server-list').innerHTML = state.profiles.length ? state.profiles.map(p => {
    const [label] = status(p);
    return `<button class="server-link ${p.id === selected ? 'active' : ''}" data-select="${esc(p.id)}"><span style="min-width:0"><span class="server-link-name">${esc(p.name)}</span><small>${esc(p.minecraft || 'Import läuft')} · ${esc(label.toLowerCase())}</small></span><span class="status-dot ${p.running ? 'online' : ''}"></span></button>`;
  }).join('') : '<div class="empty-sidebar">Noch kein Server angelegt.</div>';
}
function renderWelcome() {
  main.innerHTML = `<div class="eyebrow">DEIN SERVER, DEIN PC</div><h1 class="page-title">Zusammen<br>spielen.</h1><p class="lead">Starte einen Minecraft-Java-Server mit deinem Modpack. Stelle RAM und Port ein und teile anschließend eine Adresse mit deinen Freunden.</p><div class="hero-line"></div>
  <div class="welcome-grid"><section class="welcome-primary"><div class="number">01 / IMPORT</div><h2>Modpack importieren</h2><p>Füge einen Modrinth-Modpack-Link ein und wähle die Version. Eine .mrpack-Datei oder ein fertiges Serverpack als ZIP geht ebenfalls.</p><div class="button-row"><button class="button primary" id="link-import-button-main">Modrinth-Link einfügen <span class="link-arrow">↗</span></button><button class="button" id="import-button">Datei auswählen</button></div></section><section class="welcome-secondary"><div class="number">02 / NEU</div><h2>Ohne Modpack starten</h2><p>Erstelle einen Fabric-, NeoForge-, Forge- oder Vanilla-Server und füge später eigene Mods hinzu.</p><button class="button" id="create-button">Server erstellen <span class="link-arrow">↗</span></button></section></div>
  <div class="section"><div class="section-head"><h2>So funktioniert es</h2></div><div class="setup-steps"><div class="setup-step"><strong>1. Server einrichten</strong><p>Modpack importieren oder einen neuen Server erstellen.</p></div><div class="setup-step"><strong>2. Starten</strong><p>Passendes Java auswählen, EULA bestätigen und Server starten.</p></div><div class="setup-step"><strong>3. Freunde einladen</strong><p>Für Spieler außerhalb deines WLANs einen playit-Tunnel oder eine Portfreigabe einrichten.</p></div></div></div>`;
  main.querySelector('.setup-step:last-child p').textContent = 'Radmin VPN verbindet eure Windows-PCs kostenlos. Jeder Mitspieler tritt dem gleichen privaten Netzwerk bei.';
}
function renderProfile(p) {
  const [label, badgeClass] = status(p);
  const lanAddress = state.local_ip ? `${state.local_ip}:${p.port}` : `localhost:${p.port}`;
  const address = p.public_address || 'Noch keine öffentliche Adresse';
  const javaOptions = state.java.map(j => `<option value="${esc(j.path)}">Java ${j.major}</option>`).join('');
  const claimNotice = state.tunnel_claim ? `<div class="notice"><strong>Agent verknüpfen:</strong> <a href="${esc(state.tunnel_claim)}" target="_blank" rel="noopener noreferrer">playit-Einrichtungsseite öffnen ↗</a></div>` : '';
  const job = p.job?.status === 'working' ? `<div class="notice" role="status"><strong>${p.job.kind === 'start' ? 'Serverstart läuft.' : 'Einrichtung läuft.'}</strong> ${esc(p.job.progress)}</div>` : p.job?.status === 'error' ? `<div class="notice error" role="alert"><strong>${p.job.kind === 'start' ? 'Serverstart fehlgeschlagen.' : 'Einrichtung fehlgeschlagen.'}</strong> ${esc(p.job.progress)} ${p.job.kind === 'import' ? 'Bitte die Datei erneut importieren.' : '<button class="small-link" id="retry-button">Erneut versuchen</button>'}</div>` : '';
  const m = p.metrics || {};
  const duration = p.running && m.started_at ? Math.max(0, Math.floor(Date.now()/1000 - m.started_at)) : 0;
  const uptime = duration >= 3600 ? `${Math.floor(duration/3600)} h ${Math.floor(duration%3600/60)} min` : `${Math.floor(duration/60)} min`;
  const liveMetrics = `<section class="section"><div class="section-head"><h2>Live-Daten</h2><span>AKTUALISIERUNG ALLE 5 SEKUNDEN</span></div><div class="metrics metrics-live"><div class="metric"><div class="metric-label">SPIELER</div><strong>${p.running && m.online != null ? `${m.online} / ${m.max}` : '—'}</strong></div><div class="metric"><div class="metric-label">LOKALE ANTWORTZEIT</div><strong>${p.running && m.response_ms != null ? `${m.response_ms} ms` : '—'}</strong></div><div class="metric"><div class="metric-label">TPS (GESCHÄTZT)</div><strong>${p.running && m.tps_estimate != null ? m.tps_estimate : '—'}</strong></div><div class="metric"><div class="metric-label">Ø TICKZEIT</div><strong>${p.running && m.mspt != null ? `${m.mspt} ms` : '—'}</strong></div><div class="metric"><div class="metric-label">RAM-NUTZUNG</div><strong>${p.running && m.ram_used_mb != null ? `${m.ram_used_mb} MB` : '—'}</strong></div><div class="metric"><div class="metric-label">LAUFZEIT</div><strong>${p.running ? uptime : '—'}</strong></div></div><p class="helper">Die Antwortzeit wird auf diesem PC gemessen und ist nicht der Ping deiner Freunde. TPS ist aus der mittleren Tickzeit geschätzt; bei älteren Serverversionen oder ohne Messantwort bleibt der Wert leer.</p></section>`;
  main.innerHTML = `<div class="eyebrow">SERVER / ${esc(p.loader || 'IMPORT')}</div><div class="server-heading"><div><h1 class="page-title">${esc(p.name)}</h1><p class="lead">Minecraft ${esc(p.minecraft || 'Version wird gelesen')} · ${esc(p.loader || 'Loader wird erkannt')}${p.loader_version ? ' ' + esc(p.loader_version) : ''}</p></div><span class="state-badge ${badgeClass}">${label}</span></div><div class="hero-line"></div>${job}
  <div class="overview-grid section"><div><section class="panel"><h2 class="panel-title">Für deine Freunde <small>VERBINDUNG</small></h2><div class="address-card"><div class="section-label">ÖFFENTLICHE SERVERADRESSE</div><strong>${esc(address)}</strong><div class="address-bottom"><span>${p.public_address ? 'In Minecraft → Multiplayer → Server hinzufügen' : 'Trage die Adresse deines Tunnels unten ein.'}</span><button class="copy-button" id="copy-address" ${p.public_address ? '' : 'disabled'}>KOPIEREN</button></div></div><p class="helper">Im selben Heimnetz: <strong>${esc(lanAddress)}</strong>. Für Freunde außerhalb brauchst du den Tunnel oder eine Portfreigabe.</p></section>
  <div class="metrics"><div class="metric"><div class="metric-label">ARBEITSSPEICHER</div><strong>${(p.ram_mb/1024).toFixed(p.ram_mb % 1024 ? 1 : 0)} GB</strong></div><div class="metric"><div class="metric-label">LOKALER PORT</div><strong>${esc(p.port)}</strong></div><div class="metric"><div class="metric-label">SERVERSTATUS</div><strong>${p.running ? 'Online' : p.installed ? 'Offline' : 'Einrichtung'}</strong></div></div></div>
  <div class="action-card"><div><h2>Server steuern</h2><p>${p.running ? 'Der Server läuft. Änderungen an RAM, Port und Java werden nach einem Neustart wirksam.' : p.installed ? 'Starte den Server, sobald die EULA bestätigt und ein passendes Java verfügbar ist.' : 'Warte auf die Einrichtung der Serverdateien.'}</p></div><div>${p.running ? '<button class="button danger" id="stop-button">Server stoppen</button>' : `<button class="button primary" id="start-button" ${!p.installed && !p.shared_role ? 'disabled' : ''}>Server starten <span class="link-arrow">↗</span></button>`}<button class="button" id="open-folder-button">Serverordner öffnen</button></div></div></div>${liveMetrics}
  <section class="section"><div class="section-head"><h2>Einstellungen</h2><span>Vor dem Start speichern</span></div><div class="panel"><form id="settings-form"><div class="field-grid"><div class="field"><label for="ram">RAM für den Server</label><select id="ram" name="ram_mb">${[2048,3072,4096,5120,6144,8192,10240,12288,16384,24576,32768].map(n => `<option value="${n}" ${p.ram_mb === n ? 'selected' : ''}>${n/1024} GB</option>`).join('')}</select><span class="hint">Lass für Windows und Minecraft auf deinem PC genug RAM frei.</span></div><div class="field"><label for="port">Lokaler Port</label><input id="port" name="port" type="number" min="1024" max="65535" value="${esc(p.port)}"><span class="hint">Standard: 25565. Der Tunnel muss auf denselben Port zeigen.</span></div><div class="field wide"><label for="java">Java-Datei</label><input id="java" name="java_path" list="java-options" placeholder="Leer lassen für automatische Auswahl" value="${esc(p.java_path || '')}"><datalist id="java-options">${javaOptions}</datalist><span class="hint">Gefunden: ${state.java.length ? state.java.map(j => 'Java ' + j.major).join(', ') : 'kein Java'}. Bei Bedarf den vollständigen Pfad zu java.exe eintragen.</span></div><div class="field wide"><label for="public-address">Öffentliche Adresse (z. B. von playit)</label><input id="public-address" name="public_address" placeholder="beispiel.at.ply.gg" value="${esc(p.public_address)}"><span class="hint">Die Adresse erscheint nach dem Erstellen eines Minecraft-Java-Tunnels im playit-Dashboard.</span></div></div><label class="checkbox"><input type="checkbox" name="eula" ${p.eula ? 'checked' : ''}><span>Ich habe die <a href="https://www.minecraft.net/eula" target="_blank" rel="noopener noreferrer">Minecraft-EULA</a> gelesen und akzeptiere sie für diesen Server.</span></label><div class="button-row"><button class="button primary" type="submit" ${p.running ? 'disabled' : ''}>Einstellungen speichern</button></div></form></div></section>
  <section class="section"><div class="section-head"><h2>Freunde außerhalb deines Netzwerks</h2><span>KEINE ROUTERÄNDERUNG NÖTIG</span></div><div class="panel"><ol class="step-list"><li><span class="step-number">1</span><div><strong>Agent starten</strong><p>${state.playit_path ? 'Der playit-Agent ist bereit.' : 'Lade den playit-Agenten herunter und wähle die EXE-Datei aus.'} Starte ihn für die Verbindung von außen.</p></div></li><li><span class="step-number">2</span><div><strong>Agent verknüpfen und Tunnel anlegen</strong><p>Beim ersten Start erscheint ein Link zur Anmeldung. Erstelle dort einen Minecraft-Java-Tunnel für den lokalen Port <strong>${esc(p.port)}</strong>.</p></div></li><li><span class="step-number">3</span><div><strong>Adresse oben eintragen</strong><p>Öffentliche Adresse aus playit kopieren, in den Einstellungen speichern und mit Freunden teilen. Der Agent muss während des Spielens laufen.</p></div></li></ol>${claimNotice}<div class="button-row"><a class="button" href="https://playit.gg/download/windows" target="_blank" rel="noopener noreferrer">playit herunterladen ↗</a><button class="button" id="select-agent-button">Anderen Agenten auswählen</button>${state.playit_path ? `<button class="button ${state.tunnel_running ? 'danger' : 'primary'}" id="tunnel-button">Agent ${state.tunnel_running ? 'stoppen' : 'starten'}</button>` : ''}</div><p class="helper">${state.playit_path ? 'Ausgewählt: ' + esc(state.playit_path) : 'Noch kein Agent ausgewählt.'} ${state.tunnel_running ? '· Agent läuft. Prüfe die Tunnel-Adresse im playit-Dashboard.' : ''}</p></div></section>
  <section class="section"><div class="section-head"><h2>Konsole</h2><span>LETZTE 180 ZEILEN</span></div><div id="console" class="console ${p.logs.length ? '' : 'empty'}">${p.logs.length ? consoleHtml(p.logs) : 'Noch keine Konsolenausgabe.'}</div><form id="command-form" class="command-row"><input name="command" aria-label="Serverbefehl" placeholder="Befehl eingeben, z. B. list" autocomplete="off" ${p.running ? '' : 'disabled'}><button class="button" ${p.running ? '' : 'disabled'}>Senden</button></form><p class="helper">Befehle ohne führenden Schrägstrich eingeben. Der Server läuft nur, solange diese App geöffnet ist.</p></section>`;
  const minekubeAvailable = p.loader === 'fabric' && p.installed && !p.minekube_incompatible;
  const gateActive = state.gate_running && state.gate_profile_id === p.id;
  const gateError = state.gate_profile_id === p.id ? state.gate_error : '';
  const gateStatus = !p.minekube_dependency_ready ? 'Fabric API fehlt noch. Vervollständige die Einrichtung bei gestopptem Server.' : gateActive ? state.gate_connected ? 'Tunnel verbunden. Der Server muss zum Spielen ebenfalls laufen.' : gateError || 'Gate verbindet sich mit Minekube …' : gateError || 'Gate ist offline. Du kannst den Tunnel jetzt verbinden; beim Serverstart startet er ebenfalls.';
  const minekubePanel = `<section class="section"><div class="section-head"><h2>Minekube Gate</h2><span>OPTIONALER PROXY</span></div><div class="panel"><p>${p.minekube_incompatible ? 'Bei diesem Modpack verursachte Gate einen „Network Protocol Error“. Direkter Beitritt funktioniert; verwende hier eine direkte TCP-Verbindung oder einen rohen TCP-Tunnel.' : p.minekube_enabled ? `Adresse: <strong>${esc(p.public_address)}</strong>. ${esc(gateStatus)}` : 'Für Fabric-Server richtet Minecraft Host Gate, FabricProxy-Lite und die benötigte Fabric API ein. Der öffentliche Endpoint wird beim Verbinden registriert.'}</p><div class="button-row">${minekubeAvailable && (!p.minekube_enabled || !p.minekube_dependency_ready) ? `<button class="button" id="minekube-button" ${p.running ? 'disabled' : ''}>${p.minekube_enabled ? 'Einrichtung vervollständigen' : 'Minekube einrichten'}</button>` : ''}${p.minekube_enabled && p.minekube_dependency_ready && (!gateActive || !state.gate_connected) ? '<button class="button" id="minekube-connect-button">Gate verbinden</button>' : ''}<a class="button" href="https://app.minekube.com/" target="_blank" rel="noopener noreferrer">Minekube-Dashboard ↗</a></div><p class="helper">Die Adresse ist erst nutzbar, wenn Gate verbunden und der Minecraft-Server gestartet ist. Ein vollständiger Spielerbeitritt muss mit deinem Modpack geprüft werden.</p></div></section>`;
  const playitSection = [...main.querySelectorAll('.section')].find(el => el.querySelector('h2')?.textContent.includes('Freunde außerhalb deines Netzwerks'));
  if (playitSection) { playitSection.insertAdjacentHTML('beforebegin', minekubePanel); playitSection.querySelector('h2').textContent = 'Alternative: playit'; }
  if (p.minekube_enabled && !p.running && playitSection?.previousElementSibling) playitSection.previousElementSibling.querySelector('.button-row')?.insertAdjacentHTML('afterbegin', '<button class="button danger" id="minekube-off-button">Minekube ausschalten</button>');
  if (p.minekube_enabled) { const helper = main.querySelector('.address-card + .helper'); if (helper) helper.textContent = `Auf diesem PC über Gate: localhost:${p.port - 1}. Freunde nutzen die öffentliche Adresse.`; const addressInput = main.querySelector('#public-address'); if (addressInput) { addressInput.readOnly = true; addressInput.closest('.field').querySelector('.hint').textContent = 'Die kostenlose Minekube-Adresse wird automatisch gesetzt.'; } }
  const si = p.shared_info || {};
  const sharedSyncText = si.error ? esc(si.error)
    : p.shared_role === 'owner' && !p.shared_peer ? 'Noch nicht gekoppelt. Dein Freund muss den Einladungscode eingeben und dir seine Geräte-ID schicken.'
    : !si.snapshot_ready ? (p.shared_role === 'guest' ? 'Warte auf den ersten Weltstand vom Hauptrechner. Beide PCs müssen online sein.' : 'Geräte gekoppelt. Der erste Weltstand wird vorbereitet; beide PCs online lassen.')
    : si.sync_complete ? `Weltstand ${si.revision ?? ''} ist auf diesem PC vollständig.`
    : `Weltstand ${si.revision ?? ''} wird übertragen. Beide PCs online lassen.`;
  const sharedPanel = `<section class="section"><div class="section-head"><h2>Gemeinsam hosten</h2><span>2 PCS · 1 WELT</span></div><div class="panel">${p.shared_role ? `<p><strong>${p.shared_role === 'owner' ? 'Hauptrechner' : 'Partnerrechner'}</strong> · ${esc(p.shared_status || '')}</p><p class="helper">${sharedSyncText}</p>${p.shared_role === 'guest' ? `<div class="address-card"><div class="section-label">DEINE GERÄTE-ID · AN DEN HAUPTRECHNER SENDEN</div><strong style="font-size:14px;overflow-wrap:anywhere">${esc(si.device_id || 'Syncthing startet …')}</strong><div class="address-bottom"><span>Der Besitzer bestätigt diese ID in Minecraft Host.</span><button class="copy-button" id="copy-share-device-id" ${si.device_id ? '' : 'disabled'}>KOPIEREN</button></div></div>` : ''}<div class="button-row">${p.shared_role === 'owner' ? '<button class="button" id="share-invite-button">Einladung anzeigen</button>' : ''}${p.shared_role === 'owner' && !p.shared_peer ? '<button class="button primary" id="share-accept-button">Geräte-ID bestätigen</button>' : ''}${p.shared_peer && !p.running ? '<button class="button" id="share-publish-button">Weltstand jetzt übertragen</button>' : ''}</div>` : `<p>Dein Freund kann dieselbe Welt auf seinem PC starten, wenn dein PC offline ist. Beide installieren Minecraft Host und koppeln ihre Geräte einmalig. Nach dem Serverstopp überträgt die App die Welt verschlüsselt.</p><div class="button-row"><button class="button primary" id="share-invite-button" ${p.running ? 'disabled' : ''}>Freund einladen</button></div>`}<p class="helper">Weltübergabe und Spielverbindung sind zwei getrennte Schritte: Für den ersten Weltabgleich beide PCs online lassen. Zum Spielen müssen beide dasselbe Radmin-VPN-Netzwerk nutzen; Freunde verbinden sich mit der Radmin-Adresse des PCs, der gerade hostet. Nur ein PC darf die Welt gleichzeitig starten.</p></div></section>`;
  const vpnAddress = state.radmin?.address ? `${state.radmin.address}:${p.port}` : '';
  const fw = p.firewall || {};
  const firewallNotice = !fw.checked ? '' : (fw.allowed && !fw.blocked)
    ? '<p class="helper">✓ Windows-Firewall lässt Verbindungen zu diesem Server durch (auch über Radmin VPN).</p>'
    : `<div class="address-card"><div class="section-label">FIREWALL</div><strong style="font-size:15px">${fw.blocked ? 'Die Windows-Firewall blockiert Java in öffentlichen Netzwerken.' : 'Für Java fehlt eine Firewall-Freigabe für öffentliche Netzwerke.'}</strong><div class="address-bottom"><span>Radmin VPN zählt als öffentliches Netzwerk. Ohne Freigabe können Freunde nicht beitreten.</span><button class="copy-button" id="firewall-button">FREIGEBEN</button></div></div>`;
  const radminPanel = `<section class="section"><div class="section-head"><h2>Mit Radmin VPN spielen</h2><span>KOSTENLOS · OHNE ROUTERFREIGABE</span></div><div class="panel">${vpnAddress ? `<div class="address-card"><div class="section-label">DEINE VPN-SERVERADRESSE</div><strong>${esc(vpnAddress)}</strong><div class="address-bottom"><span>Freunde nutzen diese Adresse, wenn dieser PC hostet.</span><button class="copy-button" id="copy-vpn-address">KOPIEREN</button></div></div>` : `<p>Radmin VPN ist auf diesem PC noch nicht bereit. Installiere es und erstelle dort ein privates Netzwerk.</p>`}${firewallNotice}<ol class="step-list"><li><span class="step-number">1</span><div><strong>Privates Netzwerk erstellen</strong><p>Öffne Radmin VPN und wähle „Netzwerk erstellen“. Lege einen Namen und ein starkes Passwort fest.</p></div></li><li><span class="step-number">2</span><div><strong>Freunde einladen</strong><p>Alle Mitspieler installieren Radmin VPN und treten mit Name und Passwort diesem Netzwerk bei.</p></div></li><li><span class="step-number">3</span><div><strong>Serveradresse verwenden</strong><p>Wer gerade hostet, startet den Minecraft-Server. Die anderen verbinden sich mit dessen Radmin-IP und Port ${esc(p.port)}.</p></div></li></ol><div class="button-row"><a class="button" href="https://www.radmin-vpn.com/" target="_blank" rel="noopener noreferrer">Radmin VPN ↗</a></div><p class="helper">Radmin überträgt nur die Spielverbindung. Die gemeinsame Welt wird weiterhin über die gekoppelte Geräte-Synchronisierung übertragen. Bei Verbindungsproblemen Windows-Firewall für Java auf der Radmin-Verbindung prüfen.</p></div></section>`;
  const settingsSection = [...main.querySelectorAll('.section')].find(el => el.querySelector('h2')?.textContent === 'Einstellungen');
  const tsAddress = state.tailscale_ip ? `${state.tailscale_ip}:${p.port}` : '';
  const tailscalePanel = `<section class="section"><div class="section-head"><h2>Mit Tailscale spielen</h2><span>KOSTENLOS · VERSCHLÜSSELT · OHNE ROUTERFREIGABE</span></div><div class="panel">${tsAddress ? `<div class="address-card"><div class="section-label">DEINE TAILSCALE-SERVERADRESSE</div><strong>${esc(tsAddress)}</strong><div class="address-bottom"><span>Für alle in deinem Tailnet oder mit denen du diesen PC teilst.</span><button class="copy-button" id="copy-ts-address">KOPIEREN</button></div></div>` : `<p>Tailscale ist auf diesem PC nicht aktiv. Installiere es und melde dich an – danach erscheint hier deine 100.x-Adresse.</p>`}${firewallNotice}<ol class="step-list"><li><span class="step-number">1</span><div><strong>Tailscale installieren</strong><p>Auf diesem PC installieren und anmelden. Das kostenlose Konto reicht für bis zu 100 Geräte.</p></div></li><li><span class="step-number">2</span><div><strong>Freunde dazuholen</strong><p>In der Tailscale-Verwaltung diesen PC über „Share“ mit Freunden teilen oder sie ins Tailnet einladen. Sie installieren Tailscale ebenfalls.</p></div></li><li><span class="step-number">3</span><div><strong>Verbinden</strong><p>Freunde tragen in Minecraft die Tailscale-Adresse mit Port ${esc(p.port)} ein. Dieselbe Verbindung nutzt auch die Handy-App.</p></div></li></ol><div class="button-row"><a class="button" href="https://tailscale.com/download" target="_blank" rel="noopener noreferrer">Tailscale ↗</a><a class="button" href="https://login.tailscale.com/admin/machines" target="_blank" rel="noopener noreferrer">Geräte teilen ↗</a></div><p class="helper">Tailscale verbindet die Geräte direkt und verschlüsselt (WireGuard). Freunde brauchen kein öffentliches Port-Forwarding und sehen nur die Geräte, die du teilst.</p></div></section>`;
  if (settingsSection) settingsSection.insertAdjacentHTML('beforebegin', tailscalePanel + radminPanel + sharedPanel);
  const startButton = main.querySelector('#start-button');
  if (startButton && p.job?.status === 'working') startButton.disabled = true;
  const javaHint = main.querySelector('#java')?.closest('.field')?.querySelector('.hint');
  if (javaHint) javaHint.textContent = `Gefunden: ${state.java.length ? state.java.map(j => 'Java ' + j.major).join(', ') : 'kein Java'}. Fehlt die passende Version, lädt Minecraft Host sie beim ersten Start automatisch von Eclipse Adoptium herunter. Das Feld kann leer bleiben.`;
  lastLogCount = p.logs.length;
  const consoleEl = $('#console'); if (consoleEl) consoleEl.scrollTop = consoleEl.scrollHeight;
}
function updateLogs() {
  const p = profile(); const el = $('#console');
  if (!p || !el || p.logs.length === lastLogCount) return;
  // keep the reader's place when they scrolled up to read older lines
  const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  if (p.logs.length) el.innerHTML = consoleHtml(p.logs); else el.textContent = 'Noch keine Konsolenausgabe.';
  el.classList.toggle('empty', !p.logs.length); if (atBottom) el.scrollTop = el.scrollHeight; lastLogCount = p.logs.length;
}
// One line per log entry, tagged so errors, warnings, player chat and joins stand out.
function consoleHtml(logs) {
  return logs.map(line => {
    const kind = /\b(ERROR|FATAL|SEVERE)\b|Exception/.test(line) ? 'err' : /\bWARN/.test(line) ? 'warn' : /\]: <[^>]+> /.test(line) ? 'chat' : /joined the game|left the game/.test(line) ? 'join' : /Done \(|\bStarted\b/.test(line) ? 'ok' : '';
    const m = /^(\[[0-9:. ]+\])(.*)$/.exec(line);
    const body = m ? `<span class="log-time">${esc(m[1])}</span>${esc(m[2])}` : esc(line);
    return `<span class="log-line${kind ? ' log-' + kind : ''}">${body}</span>`;
  }).join('');
}
let lastView = null;
// Re-renders happen every poll; only a real view change (other server / welcome) animates in.
function render() {
  renderSidebar(); const p = profile(); p ? renderProfile(p) : renderWelcome();
  const view = p ? p.id : 'welcome';
  if (view !== lastView) {
    lastView = view; main.classList.remove('view-enter'); void main.offsetWidth; main.classList.add('view-enter');
    clearTimeout(render.timer); render.timer = setTimeout(() => main.classList.remove('view-enter'), 900);
  }
}
const launchStarted = Date.now();
function endLaunch() {
  const el = $('#launch'); if (!el || el.classList.contains('done')) return;
  setTimeout(() => { el.classList.add('done'); setTimeout(() => el.remove(), 800); }, Math.max(0, 1100 - (Date.now() - launchStarted)));
}
setTimeout(endLaunch, 12000);
function showCreateModal() {
  modalOpen = true;
  const layer = document.createElement('div'); layer.className = 'modal-layer'; layer.id = 'modal-layer';
  layer.innerHTML = `<div class="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title"><div class="modal-top"><div><div class="eyebrow">NEUER SERVER</div><h2 id="modal-title">Server erstellen</h2></div><button class="icon-button" id="close-modal" aria-label="Schließen">×</button></div><form id="create-form"><div class="field-grid"><div class="field wide"><label for="new-name">Servername</label><input id="new-name" name="name" value="Mein Server" maxlength="80" required></div><div class="field"><label for="new-version">Minecraft-Version</label><input id="new-version" name="minecraft" value="1.21.1" required><span class="hint">Muss zum Modpack deiner Freunde passen.</span></div><div class="field"><label for="new-loader">Servertyp</label><select id="new-loader" name="loader"><option value="fabric">Fabric</option><option value="neoforge">NeoForge</option><option value="forge">Forge</option><option value="vanilla">Vanilla</option></select></div><div class="field wide" id="loader-version-field"><label for="new-loader-version">Loader-Version</label><input id="new-loader-version" name="loader_version" placeholder="Fabric: leer = aktuell; NeoForge/Forge: Versionsnummer"><span class="hint">Für NeoForge und Forge ist die genaue Loader-Version erforderlich.</span></div></div><div class="button-row"><button type="button" class="button" id="cancel-create">Abbrechen</button><button type="submit" class="button primary">Server erstellen</button></div></form></div>`;
  document.body.append(layer); $('#new-name', layer).focus();
  const close = () => { layer.remove(); modalOpen = false; };
  $('#close-modal', layer).onclick = close; $('#cancel-create', layer).onclick = close;
  layer.addEventListener('click', e => { if (e.target === layer) close(); });
  $('#new-loader', layer).onchange = e => { $('#loader-version-field', layer).classList.toggle('hidden', e.target.value === 'vanilla'); };
  $('#create-form', layer).onsubmit = async e => {
    e.preventDefault(); const data = Object.fromEntries(new FormData(e.target));
    if (['forge','neoforge'].includes(data.loader) && !data.loader_version.trim()) { showToast('Bitte die genaue Loader-Version eintragen.'); return; }
    try { const result = await api('/api/create', data); close(); selected = result.id; await refresh(true); showToast('Server wird eingerichtet.'); }
    catch (err) { showToast(err.message); }
  };
}
function showLinkModal() {
  modalOpen = true;
  const layer = document.createElement('div'); layer.className = 'modal-layer'; layer.id = 'link-modal-layer';
  layer.innerHTML = `<div class="modal" role="dialog" aria-modal="true" aria-labelledby="link-modal-title"><div class="modal-top"><div><div class="eyebrow">MODRINTH</div><h2 id="link-modal-title">Modpack per Link</h2></div><button class="icon-button" id="close-link-modal" aria-label="Schließen">×</button></div><form id="inspect-link-form"><div class="field"><label for="modrinth-url">Modpack-Link</label><input id="modrinth-url" type="url" required placeholder="https://modrinth.com/modpack/..." autocomplete="url"><span class="hint">Projekt- oder Versionslink von Modrinth. Danach kannst du die genaue Pack-Version auswählen.</span></div><div class="button-row"><button class="button primary" type="submit">Versionen laden</button></div></form><div id="link-version-area"></div></div>`;
  document.body.append(layer);
  $('#modrinth-url', layer).focus();
  const close = () => { layer.remove(); modalOpen = false; };
  $('#close-link-modal', layer).onclick = close;
  layer.addEventListener('click', e => { if (e.target === layer) close(); });
  $('#inspect-link-form', layer).onsubmit = async e => {
    e.preventDefault();
    const url = $('#modrinth-url', layer).value.trim();
    const button = e.target.querySelector('button[type=submit]');
    button.disabled = true; button.textContent = 'Lade Versionen …';
    try {
      const info = await api('/api/modrinth/inspect', {url});
      const options = info.versions.map(v => `<option value="${esc(v.id)}" ${v.id === info.preferred ? 'selected' : ''}>${esc(v.name || v.version_number)} · ${esc(v.game_versions.slice(0,3).join(', '))} · ${esc(v.loaders.join(', '))}</option>`).join('');
      $('#link-version-area', layer).innerHTML = `<hr class="divider"><form id="import-link-form"><p><strong>${esc(info.title)}</strong></p><div class="field"><label for="modrinth-version">Pack-Version</label><select id="modrinth-version" required>${options}</select><span class="hint">Neueste Version zuerst. Achte darauf, dass deine Freunde dieselbe Minecraft- und Modpack-Version verwenden.</span></div><div class="button-row"><button class="button primary" type="submit">Diese Version importieren</button></div></form>`;
      $('#import-link-form', layer).onsubmit = async event => {
        event.preventDefault();
        const importButton = event.target.querySelector('button[type=submit]');
        importButton.disabled = true; importButton.textContent = 'Import startet …';
        try {
          const result = await api('/api/modrinth/import', {url, version_id: $('#modrinth-version', layer).value});
          close(); selected = result.id; await refresh(true); showToast('Modrinth-Import gestartet.');
        } catch (err) { showToast(err.message); importButton.disabled = false; importButton.textContent = 'Diese Version importieren'; }
      };
    } catch (err) { showToast(err.message); }
    finally { button.disabled = false; button.textContent = 'Versionen laden'; }
  };
}
function shareModal(title, body) {
  modalOpen = true;
  const layer = document.createElement('div'); layer.className = 'modal-layer'; layer.id = 'share-modal-layer';
  layer.innerHTML = `<div class="modal" role="dialog" aria-modal="true"><div class="modal-top"><div><div class="eyebrow">GEMEINSAM HOSTEN</div><h2>${esc(title)}</h2></div><button class="icon-button" id="close-share-modal" aria-label="Schließen">×</button></div>${body}</div>`;
  document.body.append(layer);
  const close = () => { layer.remove(); modalOpen = false; };
  $('#close-share-modal', layer).onclick = close;
  layer.addEventListener('click', e => { if (e.target === layer) close(); });
  return {layer, close};
}
function showJoinModal() {
  const {layer, close} = shareModal('Einladung einfügen', `<form id="join-share-form"><div class="field"><label for="share-code">Einladungscode vom Hauptrechner</label><textarea id="share-code" rows="5" required style="width:100%;resize:vertical"></textarea></div><div class="button-row"><button class="button primary" type="submit">Server verbinden</button></div></form>`);
  $('#join-share-form', layer).onsubmit = async e => {
    e.preventDefault();
    try { const result = await api('/api/shared/join', {code: $('#share-code', layer).value.trim()}); close(); selected = result.id; await refresh(true); showToast('Gerät eingebunden. Gib deine Geräte-ID an den Hauptrechner weiter.'); }
    catch (err) { showToast(err.message); }
  };
}
async function showInvite(p) {
  try {
    const result = await api(`/api/profiles/${p.id}/share_invite`, {});
    await refresh(true);
    const {layer} = shareModal('Freund einladen', `<p><strong>1.</strong> Sende deinem Freund diesen Code. Er öffnet Minecraft Host, wählt „Freundes-Server verbinden“ und fügt den Code ein.</p><div class="field"><textarea id="invite-code" rows="6" readonly style="width:100%;resize:vertical;overflow-wrap:anywhere">${esc(result.code)}</textarea></div><div class="button-row"><button class="button primary" id="copy-invite">Code kopieren</button></div><p class="helper"><strong>2.</strong> Dein Freund kopiert danach seine Geräte-ID aus dem Bereich „Gemeinsam hosten“ und schickt sie dir. Du klickst auf „Geräte-ID bestätigen“.</p><p class="helper"><strong>3.</strong> Lasst beide PCs online, bis die App „Weltstand … ist auf diesem PC vollständig“ meldet. Fürs Spielen verbindet ihr euch zusätzlich über dasselbe Radmin-VPN-Netzwerk.</p>`);
    $('#copy-invite', layer).onclick = async () => { try { await navigator.clipboard.writeText(result.code); showToast('Einladung kopiert.'); } catch { showToast('Bitte den Code markieren und kopieren.'); } };
  } catch (err) { showToast(err.message); }
}
function showAcceptModal(p) {
  const {layer, close} = shareModal('Gerät bestätigen', `<p>Füge die Geräte-ID ein, die dein Freund in Minecraft Host kopiert hat. Danach startet der erste Weltabgleich automatisch. Beide PCs müssen dafür online bleiben.</p><form id="accept-share-form"><div class="field"><label for="peer-id">Geräte-ID deines Freundes</label><input id="peer-id" required autocomplete="off" placeholder="XXXXXXX-XXXXXXX-..."></div><div class="button-row"><button class="button primary" type="submit">Gerät koppeln</button></div></form>`);
  $('#accept-share-form', layer).onsubmit = async e => {
    e.preventDefault();
    try { await api(`/api/profiles/${p.id}/share_accept`, {device_id: $('#peer-id', layer).value.trim()}); close(); await refresh(true); showToast('Gerät verbunden. Die erste Übertragung startet.'); }
    catch (err) { showToast(err.message); }
  };
}
async function action(path, data, message) {
  try { await api(path, data); await refresh(true); if (message) showToast(message); }
  catch (err) { showToast(err.message); }
}
document.addEventListener('click', async e => {
  const target = e.target.closest('button'); if (!target) return;
  if (target.dataset.select) { selected = target.dataset.select; render(); return; }
  const id = target.id; const p = profile();
  if (id === 'new-server-button' || id === 'create-button') showCreateModal();
  if (id === 'link-import-button' || id === 'link-import-button-main') showLinkModal();
  if (id === 'join-share-button') showJoinModal();
  if (id === 'import-button') $('#import-input').click();
  if (!p) return;
  if (id === 'copy-address') { try { await navigator.clipboard.writeText(p.public_address); showToast('Adresse kopiert.'); } catch { showToast('Kopieren nicht möglich.'); } }
  if (id === 'copy-ts-address') { try { await navigator.clipboard.writeText(`${state.tailscale_ip}:${p.port}`); showToast('Tailscale-Adresse kopiert.'); } catch { showToast('Kopieren nicht möglich.'); } return; }
  if (id === 'copy-vpn-address') { try { await navigator.clipboard.writeText(`${state.radmin.address}:${p.port}`); showToast('VPN-Adresse kopiert.'); } catch { showToast('Kopieren nicht möglich.'); } }
  if (id === 'copy-share-device-id') { try { await navigator.clipboard.writeText(p.shared_info.device_id); showToast('Geräte-ID kopiert.'); } catch { showToast('Kopieren nicht möglich.'); } }
  if (id === 'firewall-button') await action(`/api/profiles/${p.id}/firewall`, {}, 'Firewall-Freigabe eingerichtet.');
  if (id === 'start-button') await action(`/api/profiles/${p.id}/start`, {}, 'Server startet.');
  if (id === 'stop-button') await action(`/api/profiles/${p.id}/stop`, {}, 'Stop-Befehl gesendet.');
  if (id === 'retry-button') await action(`/api/profiles/${p.id}/${p.job?.kind === 'start' ? 'start' : 'retry'}`, {}, 'Erneut gestartet.');
  if (id === 'open-folder-button') await action(`/api/profiles/${p.id}/folder`, {}, 'Serverordner geöffnet.');
  if (id === 'minekube-button') await action(`/api/profiles/${p.id}/minekube`, {}, 'Minekube wird verbunden. Prüfe den Status im Gate-Bereich.');
  if (id === 'minekube-connect-button') await action(`/api/profiles/${p.id}/minekube_connect`, {}, 'Gate verbindet sich.');
  if (id === 'minekube-off-button') await action(`/api/profiles/${p.id}/minekube_off`, {}, 'Minekube ist ausgeschaltet.');
  if (id === 'share-invite-button') await showInvite(p);
  if (id === 'share-accept-button') showAcceptModal(p);
  if (id === 'share-publish-button') await action(`/api/profiles/${p.id}/share_publish`, {}, 'Weltstand wird übertragen.');
  if (id === 'select-agent-button') await action('/api/tunnel/select', {}, 'Agent ausgewählt.');
  if (id === 'tunnel-button') await action(`/api/tunnel/${state.tunnel_running ? 'stop' : 'start'}`, {}, state.tunnel_running ? 'Agent gestoppt.' : 'Agent gestartet.');
});
$('#import-input').addEventListener('change', async e => {
  const file = e.target.files[0]; if (!file) return;
  if (!/\.(mrpack|zip)$/i.test(file.name)) { showToast('Bitte .mrpack oder .zip auswählen.'); return; }
  try { const result = await api('/api/import', {file}, true); selected = result.id; await refresh(true); showToast('Import gestartet.'); }
  catch (err) { showToast(err.message); }
  e.target.value = '';
});
document.addEventListener('submit', async e => {
  const p = profile(); if (!p) return;
  if (e.target.id === 'settings-form') {
    e.preventDefault(); const form = new FormData(e.target); const data = Object.fromEntries(form);
    data.eula = form.has('eula');
    await action(`/api/profiles/${p.id}/save`, data, 'Einstellungen gespeichert.');
  }
  if (e.target.id === 'command-form') {
    e.preventDefault(); const input = $('input[name=command]', e.target); const command = input.value.trim();
    if (!command) return;
    await action(`/api/profiles/${p.id}/command`, {command}, 'Befehl gesendet.');
  }
});
$('#clock').textContent = new Intl.DateTimeFormat('de-DE', {day:'2-digit',month:'2-digit',year:'numeric'}).format(new Date());
refresh(true); setInterval(() => refresh(), 2200);

// Designs: scene + accent sets, saved per device; switching cross-fades via a view transition.
const DESIGNS = [
  {id: 'cherry', name: 'Kirschhain', note: 'Liquid Glass · Grasgrün', bg: 'url(/scene-cherry.webp)'},
  {id: 'aurora', name: 'Polarlicht', note: 'Kühles Glas · Türkis', bg: 'url(/scene-aurora.webp)'},
  {id: 'crystal', name: 'Kristalltal', note: 'Amethyst · Violett', bg: 'url(/scene-crystal.webp)'},
  {id: 'nether', name: 'Nether', note: 'Glut · ohne Hintergrundbild', bg: 'linear-gradient(160deg,#5a1a12,#1a0609)'},
  {id: 'day', name: 'Tageslicht', note: 'Helles Milchglas', bg: 'linear-gradient(160deg,#ffffff,#ffd9ec)'},
];
function applyDesign(id, animate = false) {
  const design = DESIGNS.find(d => d.id === id) || DESIGNS[0];
  const set = () => {
    if (design.id === 'cherry') delete document.body.dataset.design; else document.body.dataset.design = design.id;
    $('#design-menu').querySelectorAll('.design-option').forEach(b => b.setAttribute('aria-checked', String(b.dataset.design === design.id)));
    document.querySelector('meta[name=theme-color]')?.setAttribute('content', design.id === 'day' ? '#f4f7fb' : '#08111c');
  };
  if (animate && document.startViewTransition && !matchMedia('(prefers-reduced-motion: reduce)').matches) document.startViewTransition(set); else set();
  try { localStorage.setItem('mch-design', design.id); } catch {}
}
(() => {
  const menu = $('#design-menu'), toggle = $('#design-toggle');
  menu.innerHTML = DESIGNS.map(d => `<button class="design-option" type="button" role="menuitemradio" aria-checked="false" data-design="${d.id}"><i style="background-image:${d.bg}"></i><span><strong>${d.name}</strong><small>${d.note}</small></span></button>`).join('');
  const close = () => { menu.hidden = true; toggle.setAttribute('aria-expanded', 'false'); };
  toggle.addEventListener('click', e => { e.stopPropagation(); menu.hidden = !menu.hidden; toggle.setAttribute('aria-expanded', String(!menu.hidden)); });
  menu.addEventListener('click', e => { const b = e.target.closest('.design-option'); if (b) { applyDesign(b.dataset.design, true); close(); } });
  document.addEventListener('click', e => { if (!e.target.closest('.design-picker')) close(); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') close(); });
  let saved = 'cherry'; try { saved = localStorage.getItem('mch-design') || 'cherry'; } catch {}
  applyDesign(saved);
})();

// Self-update: version tag, "update available" pill, dialog with notes, install overlay.
let updateDismissed = '';
function renderUpdate() {
  const app = state.app || {}; const pill = $('#update-pill'), tag = $('#version-tag');
  tag.textContent = `v${app.current || '–'}`;
  const show = app.available && app.latest !== updateDismissed && !app.installing;
  pill.hidden = !show;
  if (show) pill.textContent = `Update ${app.latest}`;
}
function updateNotes(text) {
  return esc(text || 'Keine Versionshinweise.').replace(/^##+ (.*)$/gm, '<strong>$1</strong>').replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/`([^`]+)`/g, '<code>$1</code>').replace(/^- /gm, '• ').replace(/\n/g, '<br>');
}
async function showUpdateModal(recheck = false) {
  if (recheck) { try { state.app = await api('/api/update/check', {}); renderUpdate(); } catch (err) { showToast(err.message); return; } }
  const app = state.app || {}; modalOpen = true;
  const layer = document.createElement('div'); layer.className = 'modal-layer'; layer.id = 'update-modal-layer';
  const body = app.available
    ? `<p class="lead" style="font-size:14px">Version <strong>${esc(app.latest)}</strong> ist verfügbar (installiert: ${esc(app.current)}).</p><div class="update-notes">${updateNotes(app.notes)}</div>
       <p class="helper">Das Update wird von GitHub geladen und per SHA-256 geprüft. Deine Server und Welten bleiben unverändert.${app.installable ? ' Minecraft Host startet danach neu.' : app.onefile ? ' <strong>Für diese einzelne EXE lade bitte eine neue Standalone-EXE herunter.</strong>' : ' <strong>In der Quellcode-Version bitte die neue Version manuell von GitHub laden.</strong>'}</p>
       <div class="button-row"><button class="button" id="update-later" type="button">Später</button>${app.installable ? '<button class="button primary" id="update-now" type="button">Jetzt aktualisieren</button>' : `<a class="button primary" href="${esc(app.page)}" target="_blank" rel="noopener noreferrer">Release öffnen ↗</a>`}</div>`
    : `<p class="lead" style="font-size:14px">${app.error ? esc(app.error) : `Du nutzt die neueste Version <strong>${esc(app.current)}</strong>.`}</p><div class="button-row"><button class="button" id="update-later" type="button">Schließen</button></div>`;
  layer.innerHTML = `<div class="modal" role="dialog" aria-modal="true" aria-labelledby="update-title"><div class="modal-top"><div><div class="eyebrow">MINECRAFT HOST</div><h2 id="update-title">${app.available ? 'Update verfügbar' : 'Updates'}</h2></div><button class="icon-button" id="close-update-modal" aria-label="Schließen">×</button></div>${body}</div>`;
  document.body.appendChild(layer);
  const close = () => { layer.remove(); modalOpen = false; };
  $('#close-update-modal', layer).onclick = close;
  $('#update-later', layer).onclick = () => { if (app.available) updateDismissed = app.latest; renderUpdate(); close(); };
  const now = $('#update-now', layer);
  if (now) now.onclick = async () => {
    now.disabled = true; now.textContent = 'Wird geladen …';
    try { const result = await api('/api/update/install', {}); close(); showUpdateOverlay(result.version); }
    catch (err) { showToast(err.message); now.disabled = false; now.textContent = 'Jetzt aktualisieren'; }
  };
}
function showUpdateOverlay(version) {
  const el = document.createElement('div'); el.className = 'launch'; el.setAttribute('role', 'status');
  el.innerHTML = `<div class="launch-core"><span class="launch-block" aria-hidden="true"></span><div class="launch-title">UPDATE ${esc(version)}</div><div class="launch-sub">Wird installiert · Minecraft Host startet neu …</div><div class="launch-bar" aria-hidden="true"></div></div>`;
  document.body.appendChild(el);
  // Wait for the old instance to go away and the new one to answer, then reload this tab.
  const started = Date.now(); let wentDown = false;
  const poll = setInterval(async () => {
    try {
      const res = await fetch('/api/state', {cache: 'no-store'}); const data = await res.json();
      if (wentDown || data.app?.current === version) { clearInterval(poll); location.reload(); }
    } catch { wentDown = true; }
    if (Date.now() - started > 180000) { clearInterval(poll); el.querySelector('.launch-sub').textContent = 'Neustart dauert ungewöhnlich lange. Bitte MinecraftHost.exe manuell starten.'; }
  }, 1500);
}
$('#update-pill').addEventListener('click', () => showUpdateModal(false));
$('#version-tag').addEventListener('click', () => showUpdateModal(true));
$('#phone-toggle').addEventListener('click', showRemoteModal);
// Remote control for the Android app: switch, addresses, pairing code. Only reachable from this PC.
function showRemoteModal() {
  modalOpen = true;
  const layer = document.createElement('div'); layer.className = 'modal-layer'; layer.id = 'remote-modal-layer';
  document.body.appendChild(layer);
  const close = () => { layer.remove(); modalOpen = false; };
  const render = (r = state.remote || {}) => {
    const list = (r.addresses || []).map(a => `<li><code>${esc(a.address)}:${esc(r.port)}</code><span class="remote-kind remote-${esc(a.kind)}">${a.kind === 'tailscale' ? 'Tailscale · unterwegs' : a.kind === 'lan' ? 'WLAN zu Hause' : 'Netzwerk'}</span></li>`).join('');
    const tailscale = (r.addresses || []).some(a => a.kind === 'tailscale');
    layer.innerHTML = `<div class="modal remote-modal" role="dialog" aria-modal="true" aria-labelledby="remote-title"><div class="modal-top"><div><div class="eyebrow">ANDROID-APP</div><h2 id="remote-title">Vom Handy steuern</h2></div><button class="icon-button" id="close-remote-modal" aria-label="Schließen">×</button></div>
      <label class="remote-switch"><span><strong>Fernsteuerung erlauben</strong><small>Starten, stoppen, Konsole, Einstellungen und neue Server – von der Minecraft-Host-App aus.</small></span><input type="checkbox" role="switch" id="remote-enabled" ${r.enabled ? 'checked' : ''}><i aria-hidden="true"></i></label>
      ${r.enabled ? `<div class="remote-pair"><div class="section-label">KOPPLUNGSCODE</div><strong class="remote-code" id="remote-code">${esc(r.code)}</strong><div class="button-row"><button class="button" id="remote-copy" type="button">Kopieren</button><button class="button" id="remote-rotate" type="button">Neuen Code erzeugen</button></div></div>
      <div class="section-label">ADRESSE IN DER APP</div><ul class="remote-addresses">${list || '<li>Keine Netzwerkadresse gefunden.</li>'}</ul>
      ${r.error ? `<p class="helper remote-error">${esc(r.error)}</p>` : ''}
      <p class="helper">${tailscale ? 'Tailscale ist aktiv: Mit der Tailscale-Adresse klappt es auch unterwegs.' : 'Für unterwegs: <a href="https://tailscale.com/download" target="_blank" rel="noopener noreferrer">Tailscale</a> kostenlos auf PC und Handy installieren und mit demselben Konto anmelden. Danach erscheint hier eine 100.x-Adresse, die überall funktioniert.'} Windows fragt beim ersten Mal eventuell nach einer Firewall-Freigabe – für private Netzwerke erlauben. Den Code nur in deine eigene App eintragen; „Neuen Code erzeugen“ trennt alle gekoppelten Handys.</p>` : '<p class="helper">Aus: Die App kann diesen PC nicht erreichen.</p>'}
      <div class="button-row"><a class="button" href="https://github.com/LvisXVI/minecraft-host/releases" target="_blank" rel="noopener noreferrer">App herunterladen ↗</a><button class="button primary" id="remote-done" type="button">Fertig</button></div></div>`;
    $('#close-remote-modal', layer).onclick = close; $('#remote-done', layer).onclick = close;
    $('#remote-enabled', layer).onchange = async e => { try { state.remote = await api('/api/remote/config', {enabled: e.target.checked, port: r.port}); render(state.remote); } catch (err) { showToast(err.message); render(); } };
    const rotate = $('#remote-rotate', layer); if (rotate) rotate.onclick = async () => { try { state.remote = await api('/api/remote/rotate', {}); render(state.remote); showToast('Neuer Code erzeugt. Gekoppelte Handys müssen ihn neu eintragen.'); } catch (err) { showToast(err.message); } };
    const copy = $('#remote-copy', layer); if (copy) copy.onclick = () => navigator.clipboard.writeText(r.code).then(() => showToast('Code kopiert.'), () => showToast('Kopieren nicht möglich.'));
  };
  render();
}
