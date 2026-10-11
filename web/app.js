const STALE_MS = 20000;  // server pings every 15s when quiet
const DRIFT_MS = 60000;
const GRACE_MS = 5000;  // reconnect time before the lost screen shows
const $ = id => document.getElementById(id);
const els = new Map();
let ws, retry, delay = 1000, live = false, commit, alerts = [];
// two-screen layout: ?video=right puts the video on the right screen
if (new URLSearchParams(location.search).get("video") === "right") document.body.dataset.side = "right";
const PREVIEW = location.pathname === "/admin/preview";  // admin embeds the page and posts alerts to it
const GO2RTC = PREVIEW ? `${location.host}/admin/go2rtc` : `${location.hostname}:1984`;
const SHOW_MS = 5000, RETRY_MS = 15000;  // note after 5s without frames, new player every 15s
const tiles = new Map();  // camera -> {div, el, gen, t0, ok, pos}
let player, fails = 0, cap = 2;
let lastMsg = performance.now(), lostAt = lastMsg, srv = null;  // srv: {ms, at} from the latest server_time
let heard = null, sounding = null;  // heard: key -> last_seen; sounding: {key, priority, h}

function render(list) {
  const box = $("alerts"), seen = new Set();
  list.forEach((a, i) => {
    seen.add(a.key);
    let el = els.get(a.key);
    if (!el) {
      els.set(a.key, el = document.createElement("div"));
      el.appendChild(document.createElement("span"));
    }
    const st = a.unmapped ? null : a.style;
    const cls = "banner p" + Math.min(Math.max(a.priority, 1), 3) + (a.unmapped ? " u" : "") +
      (st ? " s a-" + (st.animation || "tier") : "");
    if (el.className !== cls) el.className = cls;
    for (const k of ["fg", "bg", "alt"]) {
      if (st && st[k]) el.style.setProperty("--" + k, st[k]); else el.style.removeProperty("--" + k);
    }
    const text = el.firstChild;
    if (text.textContent !== a.text) text.textContent = a.text;
    if (a.unmapped && !el.querySelector(".tag")) {
      const t = document.createElement("b");
      t.className = "tag";
      t.textContent = "UNMAPPED";
      el.appendChild(t);
    }
    if (box.children[i] !== el) box.insertBefore(el, box.children[i] || null);
  });
  for (const [k, el] of els) if (!seen.has(k)) { el.remove(); els.delete(k); }
  const n = list.length, rows = n < 3 ? 1 : n < 9 ? 2 : n < 13 ? 3 : 4;
  box.dataset.n = n > 10 ? "many" : n;
  box.style.setProperty("--cols", Math.ceil(n / 10));
  box.dataset.r = rows;  // text panel under video: rows and columns
  box.style.setProperty("--rows", rows);
  box.style.setProperty("--vcols", Math.ceil(n / rows));
  syncTiles(list);  // after the banners, so text never waits on video
}

function loadPlayer() {  // the query defeats the browser's cached import failure
  return player ||= import(`http://${GO2RTC}/video-stream.js${fails ? "?" + fails : ""}`)
    .catch(e => { player = null; fails++; throw e; });
}

function stop(t) {
  t.gen++;
  if (!t.el) return;
  t.el.DISCONNECT_TIMEOUT = 0;  // close the stream now, not after the player's grace period
  t.el.remove();
  t.el = null;
}

function start(t, cam) {
  stop(t);
  const gen = t.gen;
  t.t0 = performance.now();
  t.ok = t.pos = 0;
  loadPlayer().then(() => {
    if (t.gen !== gen) return;
    const el = t.el = document.createElement("video-stream");
    el.mode = "mse";  // Chromium gathers no loopback ICE candidates, so WebRTC cannot reach go2rtc on 127.0.0.1
    el.visibilityCheck = false;
    el.src = `ws://${GO2RTC}/api/ws?src=${encodeURIComponent(cam)}`;
    t.div.prepend(el);
    el.video.controls = false;
    el.video.muted = true;
  }, () => {});
}

function syncTiles(list) {
  const want = [...new Set(list.map(a => a.camera).filter(Boolean))].slice(0, cap), box = $("video");
  for (const [cam, t] of tiles) if (!want.includes(cam)) { stop(t); t.div.remove(); tiles.delete(cam); }
  want.forEach((cam, i) => {
    let t = tiles.get(cam);
    if (!t) {
      tiles.set(cam, t = { div: document.createElement("div"), gen: 0 });
      t.div.className = "tile";
      t.div.innerHTML = '<div class="note">video unavailable</div>';
      start(t, cam);
    }
    if (box.children[i] !== t.div) box.insertBefore(t.div, box.children[i] || null);
    // letterbox bars flash with the first alert on this camera
    const b = els.get(list.find(a => a.camera === cam).key);
    const cls = "tile" + b.className.replace("banner", "");
    if (t.div.className.replace(" bad", "") !== cls) t.div.className = cls;  // watch() restores "bad"
    t.div.style.cssText = b.style.cssText;
  });
  box.dataset.k = want.length;
  box.style.setProperty("--r", Math.max(want.length - 1, 1));
  if (want.length) document.body.dataset.video = ""; else delete document.body.dataset.video;
}

function watch() {
  const now = performance.now();
  for (const [cam, t] of tiles) {
    const v = t.el && t.el.video;
    if (v && v.currentTime > t.pos) t.ok = now;
    t.pos = v ? v.currentTime : 0;
    const idle = now - Math.max(t.ok, t.t0);
    t.div.classList.toggle("bad", idle > SHOW_MS);
    if (idle > RETRY_MS) start(t, cam);
  }
}

// A raised or refreshed alert plays its sound unless a higher priority one is playing.
function sound(list) {
  const fresh = heard ? list.filter(a => a.sound && heard.get(a.key) !== a.last_seen) : [];
  heard = new Map(list.map(a => [a.key, a.last_seen]));
  if (sounding && !heard.has(sounding.key)) { sounding.h.stop(); sounding = null; }
  const best = fresh.reduce((b, a) => !b || a.priority < b.priority || (a.priority === b.priority && a.last_seen > b.last_seen) ? a : b, null);
  if (!best || (sounding && sounding.priority < best.priority)) return;
  if (sounding) sounding.h.stop();
  const s = sounding = { key: best.key, priority: best.priority };
  s.h = playSound(best.sound, () => { if (sounding === s) sounding = null; });
}

function onMsg(m) {
  lastMsg = performance.now();
  lostAt = 0;
  live = true;
  delay = 1000;
  if (m.clock_synced !== undefined) $("sync").hidden = PREVIEW || m.clock_synced;
  if (m.server_time) srv = { ms: Date.parse(m.server_time), at: lastMsg };
  if (m.type !== "state") return;
  if (commit && m.commit !== commit) return location.reload();
  commit = m.commit;
  alerts = m.alerts;
  cap = m.max_streams || 2;
  $("commit").textContent = commit.slice(0, 7);
  render(alerts);
  if (!PREVIEW) sound(alerts);
}

function drop(s) {
  if (s !== ws) return;
  ws = null;
  live = false;
  lostAt ||= performance.now();
  s.close();
  retry = setTimeout(connect, delay);
  delay = Math.min(delay * 2, 10000);
}

function connect() {
  clearTimeout(retry);
  const s = ws = new WebSocket(`ws://${location.host}/ws`);
  s.onmessage = e => s === ws && onMsg(JSON.parse(e.data));
  s.onclose = () => drop(s);
}

function problem() {
  if (!live) return performance.now() - lostAt > GRACE_MS ? "Server not reachable" : null;
  if (srv && Math.abs(Date.now() - (srv.ms + performance.now() - srv.at)) > DRIFT_MS) return "Clock out of sync with server";
}

function tick() {
  watch();
  if (!PREVIEW && live && performance.now() - lastMsg > STALE_MS) drop(ws);
  const bad = PREVIEW ? null : problem();
  if (bad) {
    const s = Math.round((performance.now() - (lostAt || lastMsg)) / 1000);
    $("lost-why").textContent = bad;
    $("lost-last").textContent = (commit ? `No contact for ${s < 120 ? s + "s" : Math.floor(s / 60) + "m"}. ` : "Connecting. ") +
      (alerts.length ? "Last known alerts: " + alerts.map(a => a.text).join(" | ") : "");
  }
  const now = new Date();
  $("clock").textContent = now.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  $("date").textContent = now.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" });
  document.body.dataset.view = bad ? "lost" : alerts.length ? "alerts" : "idle";
  for (const a of document.getAnimations()) a.startTime = 0;  // one clock, so video bars flash in step with banners
}

setInterval(tick, 1000);
tick();
if (PREVIEW) {
  addEventListener("message", e => {
    if (e.source === parent && e.origin === location.origin && e.data && e.data.type === "state") {
      onMsg({ ...e.data, commit: "preview" });
    }
  });
} else connect();
