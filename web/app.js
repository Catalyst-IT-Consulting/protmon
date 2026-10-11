const STALE_MS = 20000;  // server pings every 15s when quiet
const DRIFT_MS = 60000;
const $ = id => document.getElementById(id);
const els = new Map();
let ws, retry, delay = 1000, live = false, commit, alerts = [];
let lastMsg = performance.now(), srv = null;  // srv: {ms, at} from the latest server_time

function render(list) {
  const box = $("alerts"), seen = new Set();
  list.forEach((a, i) => {
    seen.add(a.key);
    let el = els.get(a.key);
    if (!el) {
      els.set(a.key, el = document.createElement("div"));
      el.appendChild(document.createElement("span"));
    }
    const cls = "banner p" + Math.min(Math.max(a.priority, 1), 3) + (a.unmapped ? " u" : "");
    if (el.className !== cls) el.className = cls;
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
  const n = list.length;
  box.dataset.n = n > 10 ? "many" : n;
  box.style.setProperty("--cols", Math.ceil(n / 10));
}

function onMsg(m) {
  lastMsg = performance.now();
  live = true;
  delay = 1000;
  if (m.server_time) srv = { ms: Date.parse(m.server_time), at: lastMsg };
  if (m.type !== "state") return;
  if (commit && m.commit !== commit) return location.reload();
  commit = m.commit;
  alerts = m.alerts;
  $("commit").textContent = commit.slice(0, 7);
  render(alerts);
}

function drop(s) {
  if (s !== ws) return;
  ws = null;
  live = false;
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
  if (!live) return "Server not reachable";
  if (performance.now() - lastMsg > STALE_MS) return "Server not responding";
  if (srv && Math.abs(Date.now() - (srv.ms + performance.now() - srv.at)) > DRIFT_MS) return "Clock out of sync with server";
}

function tick() {
  const bad = problem();
  if (live && performance.now() - lastMsg > STALE_MS) drop(ws);
  if (bad) {
    const s = Math.round((performance.now() - lastMsg) / 1000);
    $("lost-why").textContent = bad;
    $("lost-last").textContent = (commit ? `No contact for ${s < 120 ? s + "s" : Math.floor(s / 60) + "m"}. ` : "Connecting. ") +
      (alerts.length ? "Last known alerts: " + alerts.map(a => a.text).join(" | ") : "");
  }
  $("clock").textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  document.body.dataset.view = bad ? "lost" : alerts.length ? "alerts" : "idle";
}

setInterval(tick, 1000);
tick();
connect();
