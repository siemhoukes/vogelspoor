/* Vogelspoor UI — praat met de Python-backend via window.pywebview.api */
"use strict";

let api = null;
let state = { track: null, photos: [] };
let matchData = { results: [], suggestion: null };
let offsetS = 0;

const manual = new Map();   // path -> {lat, lon}, handmatig geprikt
const selected = new Set(); // paden van aangevinkte foto's
let lastIndex = null;       // voor shift-klik bereik
let pinMode = false;

let map = null, trackLine = null, photoLayer = null, mapViewInit = false;
const markersByPath = new Map();

const $ = (id) => document.getElementById(id);

/* ---------------------------------------------------------------- init */

function start() {
  api = window.pywebview.api;

  $("drop-gpx").addEventListener("click", () => busyCall("GPX laden…", api.pick_gpx()));
  $("drop-photos").addEventListener("click", (e) => {
    if (e.target.id === "pick-folder") {
      e.preventDefault();
      busyCall("Map scannen…", api.pick_folder());
    } else {
      busyCall("Foto's lezen…", api.pick_photos());
    }
  });
  $("btn-reset").addEventListener("click", () => busyCall("Opnieuw…", api.reset()));

  document.querySelectorAll(".offset-controls button[data-off]").forEach((b) =>
    b.addEventListener("click", () => setOffset(offsetS + Number(b.dataset.off))));
  $("offset-reset").addEventListener("click", () => setOffset(0));
  $("suggestion-apply").addEventListener("click", () => {
    if (matchData.suggestion) setOffset(matchData.suggestion.offset_s);
  });

  $("btn-write").addEventListener("click", doWrite);
  $("skip-existing").addEventListener("change", renderAll);

  $("btn-pin").addEventListener("click", enterPinMode);
  $("btn-clear-sel").addEventListener("click", () => {
    selected.clear();
    renderAll();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if (pinMode) exitPinMode();
      $("shift-box").classList.add("hidden");
    }
  });

  $("btn-shift").addEventListener("click", openShiftBox);
  $("shift-cancel").addEventListener("click", () => $("shift-box").classList.add("hidden"));
  $("shift-apply").addEventListener("click", applyShift);
  ["sh-d", "sh-h", "sh-m", "sh-s"].forEach((id) =>
    $(id).addEventListener("input", updateShiftPreview));

  $("sel-all").addEventListener("click", (e) => {
    e.preventDefault();
    state.photos.forEach((p) => selected.add(p.path));
    renderSelectBar();
    renderList();
  });
  $("sel-none").addEventListener("click", (e) => {
    e.preventDefault();
    selected.clear();
    renderSelectBar();
    renderList();
  });

  // slepen: hele venster is dropzone. De paden zelf worden aan de
  // Python-kant afgehandeld (app.py -> vgsBusy/vgsPushState); hier alleen
  // de overlay en het voorkomen dat de browser het bestand opent.
  window.addEventListener("dragover", (e) => {
    e.preventDefault();
    $("drop-overlay").classList.remove("hidden");
  });
  window.addEventListener("dragleave", (e) => {
    if (!e.relatedTarget) $("drop-overlay").classList.add("hidden");
  });
  window.addEventListener("drop", (e) => {
    e.preventDefault();
    $("drop-overlay").classList.add("hidden");
  });
}

/* aangeroepen vanuit Python (drag & drop-afhandeling in app.py) */
window.vgsBusy = (text) => {
  $("busy-text").textContent = text;
  $("busy").classList.remove("hidden");
};
window.vgsPushState = (st) => {
  $("busy").classList.add("hidden");
  applyState(st);
};

if (window.pywebview && window.pywebview.api) start();
else window.addEventListener("pywebviewready", start);

/* ---------------------------------------------------------------- helpers */

async function busyCall(text, promise) {
  $("busy-text").textContent = text;
  $("busy").classList.remove("hidden");
  try {
    const st = await promise;
    if (st) applyState(st);
  } catch (err) {
    alert("Er ging iets mis: " + err);
  } finally {
    $("busy").classList.add("hidden");
  }
}

let lastTrackPath = null;

async function applyState(st) {
  state = st;
  const tp = st.track ? st.track.path : null;
  if (tp !== lastTrackPath) {
    mapViewInit = false; // nieuwe track: kaart opnieuw centreren
    lastTrackPath = tp;
  }
  if (st.errors && st.errors.length) alert(st.errors.join("\n"));
  await refreshMatch();
  renderAll();
}

async function refreshMatch() {
  if (state.track && state.photos.length) {
    matchData = await api.match(offsetS);
  } else {
    matchData = { results: [], suggestion: null };
  }
}

function setOffset(v) {
  offsetS = v;
  refreshMatch().then(renderAll);
}

function resultFor(path) {
  return matchData.results.find((r) => r.path === path) || null;
}

function fmtOffset(s) {
  const sign = s < 0 ? "−" : s > 0 ? "+" : "";
  s = Math.abs(s);
  const d = Math.floor(s / 86400);
  const rest = s % 86400;
  const h = Math.floor(rest / 3600), m = Math.floor((rest % 3600) / 60),
        sec = Math.round(rest % 60);
  const hms = `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
  if (d) return `${sign}${d} dag${d > 1 ? "en" : ""}${rest ? " " + hms : ""}`;
  return `${sign}${hms}`;
}

function fmtGap(s) {
  if (s < 90) return `±${s} s`;
  return `±${Math.round(s / 60)} min`;
}

const STATUS_LABEL = {
  ok: "op de track",
  edge: "rand van track",
  outside: "buiten de track",
  no_time: "geen tijd",
  has_gps: "heeft al GPS",
};

/* ---------------------------------------------------------------- render */

function renderAll() {
  renderDropCards();
  renderOffset();
  renderSummary();
  renderSelectBar();
  renderList();
  renderMap();
  renderWriteBox();
}

function renderSelectBar() {
  // opruimen: selectie kan foto's bevatten die weg zijn na reset
  const valid = new Set(state.photos.map((p) => p.path));
  for (const p of [...selected]) if (!valid.has(p)) selected.delete(p);
  for (const p of [...manual.keys()]) if (!valid.has(p)) manual.delete(p);

  const bar = $("select-bar");
  bar.classList.toggle("hidden", selected.size === 0);
  const n = selected.size;
  $("select-count").textContent = `${n} foto${n === 1 ? "" : "'s"} geselecteerd`;
  $("list-tools").classList.toggle("hidden", state.photos.length === 0);
  if (selected.size === 0) $("shift-box").classList.add("hidden");
}

function renderDropCards() {
  const t = state.track;
  const gc = $("drop-gpx");
  gc.classList.toggle("loaded", !!t);
  $("gpx-title").textContent = t ? t.name : "GPX-track";
  const sameDay = t && t.start_local.slice(0, 10) === t.end_local.slice(0, 10);
  $("gpx-sub").textContent = t
    ? `${t.n_points} punten · ${t.start_local} – ${sameDay ? t.end_local.slice(11) : t.end_local}`
    : "sleep hierheen of klik";

  const n = state.photos.length;
  $("drop-photos").classList.toggle("loaded", n > 0);
  $("photos-title").textContent = n ? `${n} foto's` : "Foto's";
  $("photos-sub").innerHTML = (n ? "klik om meer toe te voegen" : "sleep hierheen of klik") +
    ' · <a id="pick-folder" href="#">hele map</a>';
}

function renderOffset() {
  const show = state.track && state.photos.length;
  $("offset-box").classList.toggle("hidden", !show);
  $("offset-value").textContent = fmtOffset(offsetS);

  const sug = matchData.suggestion;
  $("suggestion").classList.toggle("hidden", !sug);
  if (sug) {
    const dayOff = Math.abs(sug.offset_s) >= 86400;
    $("suggestion-text").textContent =
      `Met correctie ${fmtOffset(sug.offset_s)} vallen ${sug.matched} foto's op de track — ` +
      (dayOff ? "staat de datum van de camera goed?" : "klok verkeerd ingesteld?");
  }
}

function statusOf(ph) {
  const r = resultFor(ph.path);
  if (!r) return null;
  return r.status;
}

function renderSummary() {
  const el = $("summary");
  if (!state.photos.length || !state.track) {
    el.classList.add("hidden");
    return;
  }
  const counts = { ok: 0, edge: 0, outside: 0, no_time: 0 };
  let hasGps = 0;
  for (const ph of state.photos) {
    const s = statusOf(ph);
    if (s) counts[s]++;
    if (ph.has_gps) hasGps++;
  }
  el.innerHTML = `
    <span class="chip c-ok"><b>${counts.ok}</b> op de track</span>
    <span class="chip c-edge"><b>${counts.edge}</b> aan de rand</span>
    <span class="chip c-bad"><b>${counts.outside}</b> erbuiten</span>
    <span class="chip"><b>${counts.no_time}</b> zonder tijd</span>
    <span class="chip c-info"><b>${hasGps}</b> al GPS</span>` +
    (manual.size ? `<span class="chip c-edge"><b>${manual.size}</b> handmatig</span>` : "");
  el.classList.remove("hidden");
}

function renderList() {
  const ul = $("photo-list");
  ul.innerHTML = "";
  state.photos.forEach((ph, idx) => {
    const li = document.createElement("li");
    if (selected.has(ph.path)) li.classList.add("selected");
    const r = resultFor(ph.path);

    const thumb = ph.thumb
      ? `<img src="${ph.thumb}" alt="">`
      : `<div class="no-thumb">🕊</div>`;

    let chips = "";
    if (manual.has(ph.path)) {
      chips += `<span class="status manual" title="Handmatig geplaatst — sleep de pin om te verfijnen">handmatig<span class="chip-x" title="Handmatige locatie verwijderen">×</span></span>`;
    } else if (r) {
      let label = STATUS_LABEL[r.status];
      if (r.status === "edge") label += ` (${fmtGap(r.gap_s)})`;
      chips += `<span class="status ${r.status}">${label}</span>`;
    }
    if (ph.has_gps) chips += `<span class="status has_gps">al GPS</span>`;

    li.innerHTML = `<input type="checkbox" class="row-check" ${selected.has(ph.path) ? "checked" : ""}>${thumb}
      <div class="li-main">
        <div class="name" title="${ph.path}">${ph.name}</div>
        <div class="time">${ph.time_str || ph.time_error || ""}</div>
      </div>${chips}`;

    li.addEventListener("click", (e) => onRowClick(e, ph, idx, li));
    ul.appendChild(li);
  });
}

function onRowClick(e, ph, idx, li) {
  if (e.target.classList.contains("chip-x")) {
    manual.delete(ph.path);
    renderAll();
    return;
  }
  if (e.shiftKey && lastIndex !== null) {
    const [a, b] = [Math.min(lastIndex, idx), Math.max(lastIndex, idx)];
    for (let i = a; i <= b; i++) selected.add(state.photos[i].path);
  } else {
    if (selected.has(ph.path)) selected.delete(ph.path);
    else selected.add(ph.path);
    lastIndex = idx;
  }
  renderSelectBar();
  renderList();
  focusPhoto(ph.path, null);
}

function renderWriteBox() {
  const show = state.photos.length > 0;
  $("write-box").classList.toggle("hidden", !show);
  if (!show) return;
  const n = writablePhotos().length;
  const btn = $("btn-write");
  btn.disabled = n === 0;
  btn.textContent = n
    ? `Schrijf GPS naar ${n} foto${n === 1 ? "" : "'s"}`
    : "Geen foto's om te schrijven";
}

function writablePhotos() {
  const skip = $("skip-existing").checked;
  return state.photos.filter((ph) => {
    if (manual.has(ph.path)) return true; // handmatig prikken = bewuste keuze
    const r = resultFor(ph.path);
    if (!r || (r.status !== "ok" && r.status !== "edge")) return false;
    if (skip && ph.has_gps) return false;
    return true;
  });
}

/* ---------------------------------------------------------------- kaart */

const STATUS_COLOR = { ok: "#5c7a4f", edge: "#b98a2e" };

function ensureMap() {
  if (map || typeof L === "undefined") return;
  map = L.map("map", { zoomSnap: 0.5 });

  const osm = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  });
  const topo = L.tileLayer("https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", {
    maxZoom: 17,
    attribution: 'Kaart: &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> | ' +
      'Weergave: &copy; <a href="https://opentopomap.org">OpenTopoMap</a> (CC-BY-SA)',
  });
  const layers = { "OpenStreetMap": osm, "OpenTopoMap": topo };

  let saved = "OpenStreetMap";
  try { saved = localStorage.getItem("vgs-layer") || saved; } catch (e) {}
  (layers[saved] || osm).addTo(map);
  L.control.layers(layers, null, { position: "topright" }).addTo(map);
  map.on("baselayerchange", (e) => {
    try { localStorage.setItem("vgs-layer", e.name); } catch (err) {}
  });
  map.on("click", (e) => {
    if (pinMode) applyPin(e.latlng);
  });

  photoLayer = L.layerGroup().addTo(map);
}

function renderMap() {
  const has = !!state.track || state.photos.length > 0;
  $("map-empty").classList.toggle("hidden", has);
  if (!has) return;
  if (typeof L === "undefined") {
    $("map-empty").classList.remove("hidden");
    $("map-empty").querySelector("p").textContent =
      "Kaart niet beschikbaar (geen internet) — matchen en schrijven werkt gewoon.";
    return;
  }
  ensureMap();
  setTimeout(() => map.invalidateSize(), 50);

  if (trackLine) { map.removeLayer(trackLine); trackLine = null; }
  if (state.track) {
    trackLine = L.polyline(state.track.latlngs, {
      color: "#33523d", weight: 3.5, opacity: 0.85,
    }).addTo(map);
    if (!mapViewInit) {
      map.fitBounds(trackLine.getBounds(), { padding: [30, 30] });
      mapViewInit = true;
    }
  } else if (!mapViewInit) {
    map.setView([52.2, 5.3], 7); // Nederland, tot er iets beters is
    mapViewInit = true;
  }

  photoLayer.clearLayers();
  markersByPath.clear();
  for (const r of matchData.results) {
    if (manual.has(r.path)) continue;
    if (r.status !== "ok" && r.status !== "edge") continue;
    const ph = state.photos.find((p) => p.path === r.path);
    const m = L.circleMarker([r.lat, r.lon], {
      radius: 7,
      color: "#fff",
      weight: 1.5,
      fillColor: STATUS_COLOR[r.status],
      fillOpacity: 0.95,
    }).addTo(photoLayer);
    m.bindPopup(
      `<b>${ph ? ph.name : ""}</b><br>${ph ? ph.time_str : ""}<br>` +
      `${r.lat.toFixed(5)}, ${r.lon.toFixed(5)}`
    );
    markersByPath.set(r.path, m);
  }

  const pinIcon = L.divIcon({
    className: "manual-pin", html: "<div></div>",
    iconSize: [16, 16], iconAnchor: [8, 8],
  });
  for (const [path, pos] of manual) {
    const ph = state.photos.find((p) => p.path === path);
    const m = L.marker([pos.lat, pos.lon], { icon: pinIcon, draggable: true })
      .addTo(photoLayer);
    m.bindPopup(`<b>${ph ? ph.name : ""}</b><br>handmatig geplaatst — sleep om te verfijnen`);
    m.on("dragend", () => {
      const ll = m.getLatLng();
      manual.set(path, { lat: ll.lat, lon: ll.lng });
      renderSummary();
      renderWriteBox();
    });
    markersByPath.set(path, m);
  }
}

/* ---------------------------------------------------------------- tijd verschuiven */

let shiftTargets = [];

function fmtDateTime(t) {
  const d = new Date(t * 1000);
  const p = (n) => String(n).padStart(2, "0");
  return `${p(d.getDate())}-${p(d.getMonth() + 1)}-${d.getFullYear()} ` +
    `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function shiftSeconds() {
  const v = (id) => Number($(id).value) || 0;
  return v("sh-d") * 86400 + v("sh-h") * 3600 + v("sh-m") * 60 + v("sh-s");
}

function fmtShift(s) {
  const sign = s < 0 ? "−" : "+";
  s = Math.abs(s);
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600),
    m = Math.floor((s % 3600) / 60), sec = s % 60;
  const parts = [];
  if (d) parts.push(`${d} dag${d === 1 ? "" : "en"}`);
  if (h) parts.push(`${h} u`);
  if (m) parts.push(`${m} m`);
  if (sec) parts.push(`${sec} s`);
  return sign + (parts.join(" ") || "0");
}

function openShiftBox() {
  if (!selected.size) return;
  shiftTargets = [...selected];
  $("shift-count").textContent = shiftTargets.length;
  ["sh-d", "sh-h", "sh-m", "sh-s"].forEach((id) => { $(id).value = 0; });
  updateShiftPreview();
  $("shift-box").classList.remove("hidden");
}

function updateShiftPreview() {
  const s = shiftSeconds();
  const ph = state.photos.find((p) => shiftTargets.includes(p.path) && p.t_utc != null);
  if (!ph) {
    $("shift-preview").textContent = "geen leesbare opnametijd in de selectie";
    return;
  }
  $("shift-preview").textContent =
    `${ph.name}:  ${fmtDateTime(ph.t_utc)}  →  ${fmtDateTime(ph.t_utc + s)}`;
}

async function applyShift() {
  const s = shiftSeconds();
  if (!s || !shiftTargets.length) {
    $("shift-box").classList.add("hidden");
    return;
  }
  const n = shiftTargets.length;
  if (!confirm(`Opnametijd van ${n} foto('s) verschuiven met ${fmtShift(s)}?\n\nDit past de EXIF-tijd in de fotobestanden zelf aan.`)) return;

  $("busy-text").textContent = "Tijd aanpassen…";
  $("busy").classList.remove("hidden");
  try {
    const res = await api.shift_time(shiftTargets, s, $("make-backup").checked);
    $("shift-box").classList.add("hidden");
    selected.clear();
    shiftTargets = [];
    await applyState(res.state);
    let msg = `${res.shifted} foto's aangepast (${fmtShift(s)}).`;
    if (res.backup_dirs.length) msg += `\nBackups: ${res.backup_dirs.join(", ")}`;
    if (res.failed.length) msg += `\nMislukt: ${res.failed.join(", ")}`;
    alert(msg);
  } catch (err) {
    alert("Tijd aanpassen mislukt: " + err);
  } finally {
    $("busy").classList.add("hidden");
  }
}

/* ---------------------------------------------------------------- prikken */

function enterPinMode() {
  if (!selected.size) return;
  pinMode = true;
  renderMap(); // zorg dat de kaart bestaat, ook zonder track
  if (map) map.getContainer().style.cursor = "crosshair";
  const n = selected.size;
  $("pin-hint").textContent =
    `Klik op de kaart om ${n} foto${n === 1 ? "" : "'s"} daar te plaatsen — Esc om te annuleren`;
  $("pin-hint").classList.remove("hidden");
}

function exitPinMode() {
  pinMode = false;
  if (map) map.getContainer().style.cursor = "";
  $("pin-hint").classList.add("hidden");
}

function applyPin(latlng) {
  for (const path of selected) {
    manual.set(path, { lat: latlng.lat, lon: latlng.lng });
  }
  selected.clear();
  lastIndex = null;
  exitPinMode();
  renderAll();
}

function focusPhoto(path, li) {
  document.querySelectorAll("#photo-list li.active")
    .forEach((el) => el.classList.remove("active"));
  if (li) li.classList.add("active");
  const m = markersByPath.get(path);
  if (m && map) {
    map.panTo(m.getLatLng());
    if (m.getPopup()) m.openPopup();
  }
}

/* ---------------------------------------------------------------- schrijven */

async function doWrite() {
  const n = writablePhotos().length;
  if (!n) return;
  const backup = $("make-backup").checked;
  const warn = backup
    ? "Er wordt eerst een backup gemaakt."
    : "LET OP: geen backup — de originelen worden direct aangepast!";
  if (!confirm(`GPS-coördinaten schrijven naar ${n} foto('s)?\n\n${warn}`)) return;

  $("busy-text").textContent = "GPS schrijven…";
  $("busy").classList.remove("hidden");
  try {
    const manualArg = {};
    for (const [p, pos] of manual) manualArg[p] = [pos.lat, pos.lon];
    const res = await api.write(offsetS, $("skip-existing").checked, backup, manualArg);
    const box = $("write-result");
    box.classList.remove("hidden");
    box.classList.toggle("has-fail", res.failed.length > 0);
    let html = `✔ <b>${res.written}</b> foto's voorzien van GPS.`;
    let plain = `Klaar! ${res.written} foto's voorzien van GPS.`;
    if (res.skipped_existing) {
      html += ` ${res.skipped_existing} overgeslagen (hadden al GPS).`;
      plain += `\n${res.skipped_existing} overgeslagen (hadden al GPS).`;
    }
    if (res.no_match) {
      html += ` ${res.no_match} niet gematcht.`;
      plain += `\n${res.no_match} niet gematcht.`;
    }
    if (res.backup_dirs.length) {
      html += `<br>Backups: <small>${res.backup_dirs.join("<br>")}</small>`;
      plain += `\n\nBackups: ${res.backup_dirs.join("\n")}`;
    }
    if (res.failed.length) {
      html += `<br><b>Mislukt:</b><ul>` +
        res.failed.map((f) => `<li>${f.name} — ${f.error}</li>`).join("") + `</ul>`;
      plain += `\n\nMislukt:\n` +
        res.failed.map((f) => `- ${f.name}: ${f.error}`).join("\n");
    }
    box.innerHTML = html;
    alert(plain);
    state = await api.state(); // has_gps is bijgewerkt na het schrijven
    for (const [p] of [...manual]) {
      const ph = state.photos.find((x) => x.path === p);
      if (ph && ph.has_gps) manual.delete(p); // geschreven: pin mag weg
    }
    await refreshMatch();
    renderAll();
    $("write-result").classList.remove("hidden");
  } catch (err) {
    alert("Schrijven mislukt: " + err);
  } finally {
    $("busy").classList.add("hidden");
  }
}
