"use strict";
const ANIMATIONS = ["none", "flash", "flash-alt", "invert"];
const $ = (id) => document.getElementById(id);
let version, alerts, cameras, protect, video, secrets;

function el(tag, props, ...kids) {
  const e = Object.assign(document.createElement(tag), props);
  e.append(...kids);
  return e;
}

function field(label, control, cls) {
  return el("label", { className: cls || "" }, label, control);
}

// Text or number input bound to m[f] (kept as a string until save).
function input(m, f, props) {
  const i = el("input", props);
  i.value = m[f];
  i.oninput = () => { m[f] = i.value; };
  return i;
}

function select(m, f, options, onchange) {
  const s = el("select", {}, ...options.map(([v, t]) => el("option", { value: v, textContent: t })));
  s.value = m[f];
  s.onchange = () => { m[f] = s.value; if (onchange) onchange(); };
  return s;
}

function copyBox(label, value) {
  const i = el("input", { readOnly: true, value });
  const b = el("button", { type: "button", textContent: "Copy" });
  b.onclick = () => {
    i.select();
    const legacy = () => document.execCommand("copy");
    const done = (ok) => { b.textContent = ok ? "Copied" : "Press Ctrl+C"; setTimeout(() => { b.textContent = "Copy"; }, 1500); };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(value).then(() => done(true), () => done(legacy()));
    } else {
      done(legacy());
    }
  };
  return { box: el("div", { className: "copy" }, field(label, i), b), input: i };
}

async function api(method, body) {
  const r = await fetch("/admin/api/config", {
    method,
    headers: { "content-type": "application/json", "x-protmon-admin": "1" },
    body: body && JSON.stringify(body),
  });
  const doc = await r.json().catch(() => ({}));
  return { status: r.status, doc };
}

function setStatus(msg, cls) {
  const s = $("status");
  s.textContent = msg;
  s.className = cls || "";
}

function usedBy(camera) {
  return alerts.filter((a) => a.camera === camera).map((a) => a.key);
}

function previewBox(a) {
  return el("input", { type: "checkbox", checked: !!a.preview, onchange(ev) { a.preview = ev.target.checked; } });
}

// The wall page renders this draft; first_seen and the like are stand-ins for the server's.
function postPreview() {
  if (!alerts || !video) return;
  const now = new Date(), iso = now.toISOString();
  const list = alerts.filter((a) => a.preview).sort((x, y) => x.priority - y.priority).map((a) => {
    const style = {};
    for (const f of ["fg", "bg", "alt", "animation"]) if (a[f]) style[f] = a[f];
    return {
      key: a.key || "(new)", text: a.text, priority: Number(a.priority), camera: a.camera || null,
      style: Object.keys(style).length ? style : null, unmapped: false, first_seen: iso, last_seen: iso,
      expires_at: new Date(now.getTime() + (Number(a.duration) || 60) * 1000).toISOString(),
    };
  });
  const w = $("preview").contentWindow;
  if (w) w.postMessage({ type: "state", alerts: list, max_streams: Number(video.max_streams) || 2, server_time: iso }, location.origin);
}

function fitPreview() {
  const w = $("two-screens").checked ? 3840 : 1920;
  $("stage").classList.toggle("wide", w > 1920);
  $("preview").style.width = w + "px";
  $("preview").style.transform = `scale(${$("stage").clientWidth / w})`;
}

function renderAlerts() {
  const names = cameras.map((c) => c.name).filter(Boolean);
  $("alerts").replaceChildren(...alerts.map((a) => {
    const webhook = () => location.origin + "/alert/" + a.key;
    const url = copyBox("Webhook URL (POST or GET)", webhook());
    const hdr = copyBox("Header", "X-Protmon-Token: " + secrets.token);
    const warn = el("div", { className: "warn" });
    const keyIn = input(a, "key", { required: true, pattern: "[A-Za-z0-9_\\-]+", className: "short" });
    keyIn.oninput = () => {
      a.key = keyIn.value;
      url.input.value = webhook();
      warn.textContent = a.orig && a.orig !== a.key
        ? `Renaming changes the URL: update the Alarm Manager rule that uses /alert/${a.orig}.` : "";
    };
    const del = el("button", { type: "button", textContent: "Delete" });
    del.onclick = () => { alerts = alerts.filter((x) => x !== a); renderAlerts(); };
    return el("div", { className: "card" },
      el("div", { className: "row" },
        field("Key", keyIn),
        field("Priority", select(a, "priority", [["1", "1 (highest)"], ["2", "2"], ["3", "3"]])),
        field("Duration (s)", input(a, "duration", { type: "number", min: 1, step: 1 })),
        field("Camera", select(a, "camera", [["", "none (text only)"], ...names.map((n) => [n, n])])),
        field("Text", input(a, "text"), "grow"),
        del),
      el("div", { className: "row" },
        el("label", { className: "inline" }, previewBox(a), "Preview"),
        field("Text color", input(a, "fg", { className: "short", placeholder: "#rrggbb" })),
        field("Background", input(a, "bg", { className: "short", placeholder: "#rrggbb" })),
        field("Alternate color", input(a, "alt", { className: "short", placeholder: "#rrggbb" })),
        field("Animation", select(a, "animation", [["", "default"], ...ANIMATIONS.map((n) => [n, n])]))),
      el("div", { className: "row" }, url.box, hdr.box),
      warn);
  }));
  postPreview();
}

function renderCameras() {
  $("cameras").replaceChildren(...cameras.map((c) => {
    const del = el("button", { type: "button", textContent: "Delete" });
    del.onclick = () => {
      const users = usedBy(c.name);
      if (users.length) return setStatus(`Camera ${c.name} is used by: ${users.join(", ")}`, "err");
      cameras = cameras.filter((x) => x !== c);
      setStatus("");
      renderCameras();
      renderAlerts();
    };
    const name = c.existing
      ? el("input", { value: c.name, readOnly: true, className: "short" })
      : input(c, "name", { required: true, pattern: "\\S+", className: "short", onchange: renderAlerts });
    const url = input(c, "url", {
      autocomplete: "off", required: true, placeholder: "rtsp://",
    });
    return el("div", { className: "card row" }, field("Name", name), field("RTSP URL", url, "grow"), del);
  }));
}

function renderSettings() {
  const on = el("input", { type: "checkbox", checked: !!protect });
  on.onchange = () => { protect = on.checked ? { address: "", interval: "30", failures: "3" } : null; renderSettings(); };
  const rows = [el("div", { className: "row" }, el("label", { className: "inline" }, on, "Monitor UniFi Protect reachability"))];
  if (protect) {
    rows.push(el("div", { className: "row" },
      field("Address (host or IPv4)", input(protect, "address", { required: true })),
      field("Check interval (s)", input(protect, "interval", { type: "number", min: 1, step: 1 })),
      field("Failures before alarm", input(protect, "failures", { type: "number", min: 1, step: 1 }))));
  }
  rows.push(el("div", { className: "row" },
    field("Max simultaneous video streams", input(video, "max_streams", { type: "number", min: 1, step: 1 }))));
  const ro = (label, list) => field(label, el("input", { readOnly: true, value: list.join(", ") }), "grow");
  rows.push(el("div", { className: "row" },
    ro("Allowed webhook source IPs (PROTMON_ALLOWED_IPS)", secrets.allowed_ips),
    ro("Admin networks (PROTMON_ADMIN_NETWORKS)", secrets.admin_networks)));
  $("settings").replaceChildren(el("div", { className: "card" }, ...rows));
}

function render() {
  renderAlerts();
  renderCameras();
  renderSettings();
}

async function load(msg) {
  const { status, doc } = await api("GET");
  if (status !== 200) return setStatus(doc.detail || `Load failed (${status})`, "err");
  const cfg = doc.config;
  version = doc.version;
  secrets = doc.secrets;
  cameras = Object.entries(cfg.cameras || {}).map(([name, url]) => ({ name, existing: true, url }));
  alerts = Object.entries(cfg.alerts || {})
    .map(([key, a]) => ({
      key, orig: key, text: a.text, priority: String(a.priority), duration: String(a.duration), camera: a.camera || "",
      fg: (a.style || {}).fg || "", bg: (a.style || {}).bg || "", alt: (a.style || {}).alt || "",
      animation: (a.style || {}).animation || "",
    }))
    .sort((x, y) => x.priority - y.priority);
  const p = cfg.protect;
  protect = p ? { address: p.address, interval: String(p.interval ?? 30), failures: String(p.failures ?? 3) } : null;
  video = { max_streams: String((cfg.video || {}).max_streams ?? 2) };
  $("reload").hidden = true;
  render();
  setStatus(msg || "", msg ? "ok" : "");
}

function build() {
  const num = (v) => (v === "" ? null : Number(v));
  const cfg = { cameras: {}, alerts: {} };
  for (const c of cameras) {
    if (!c.name) throw new Error("Every camera needs a name.");
    if (c.name in cfg.cameras) throw new Error(`Duplicate camera name: ${c.name}`);
    if (!c.url) throw new Error(`Camera ${c.name} needs an RTSP URL.`);
    cfg.cameras[c.name] = c.url;
  }
  for (const a of alerts) {
    if (a.key in cfg.alerts) throw new Error(`Duplicate alert key: ${a.key}`);
    const style = {};
    for (const f of ["fg", "bg", "alt", "animation"]) if (a[f]) style[f] = a[f];
    cfg.alerts[a.key] = {
      text: a.text, priority: Number(a.priority), camera: a.camera || null, duration: num(a.duration),
      ...(Object.keys(style).length ? { style } : {}),
    };
  }
  if (protect) cfg.protect = { address: protect.address, interval: num(protect.interval), failures: num(protect.failures) };
  cfg.video = { max_streams: num(video.max_streams) };
  return cfg;
}

async function save() {
  let config;
  try { config = build(); } catch (e) { return setStatus(e.message, "err"); }
  setStatus("Saving...");
  const { status, doc } = await api("PUT", { config, version });
  if (status === 200) return load("Saved. The wall is using the new config.");
  if (status === 409) $("reload").hidden = false;
  setStatus(doc.error || doc.detail || `Save failed (${status})`, "err");
}

let logNext = null;

async function loadLog(more) {
  const q = new URLSearchParams(new FormData($("log-filter")));
  for (const [k, v] of [...q]) if (!v) q.delete(k);
  if (more) q.set("before", logNext);
  const r = await fetch("/admin/api/events?" + q);
  const doc = await r.json().catch(() => ({}));
  if (r.status !== 200) return ($("log-status").textContent = doc.detail || `Log load failed (${r.status})`);
  const body = $("log").tBodies[0];
  if (!more) body.replaceChildren();
  for (const e of doc.events) {
    const { ts, event, key, source_ip, ...rest } = e;
    body.append(el("tr", {},
      el("td", { textContent: ts.replace("T", " ") }), el("td", { textContent: event }),
      el("td", { textContent: key ?? "" }), el("td", { textContent: source_ip ?? "" }),
      el("td", { className: "details", textContent: Object.keys(rest).length ? JSON.stringify(rest) : "" })));
  }
  logNext = doc.next;
  $("log-older").hidden = !doc.more;
  $("log-status").textContent = body.rows.length ? "" : "No matching events.";
}

new ResizeObserver(fitPreview).observe($("stage"));
$("two-screens").addEventListener("change", fitPreview);
$("preview").onload = postPreview;
$("fullscreen").onclick = () => $("preview").requestFullscreen();
for (const ev of ["input", "change"]) document.addEventListener(ev, postPreview);
$("log-filter").onsubmit = (ev) => { ev.preventDefault(); loadLog(false); };
$("log-older").onclick = () => loadLog(true);
$("add-alert").onclick = () => {
  alerts.push({ key: "", orig: "", text: "", priority: "2", duration: "60", camera: "", fg: "", bg: "", alt: "", animation: "" });
  renderAlerts();
};
$("add-camera").onclick = () => { cameras.push({ name: "", existing: false, url: "" }); renderCameras(); };
$("save").onclick = save;
$("reload").onclick = () => load("Reloaded.");
load();
loadLog(false);
