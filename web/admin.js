"use strict";
const ANIMATIONS = ["none", "flash", "flash-alt", "invert"];
const $ = (id) => document.getElementById(id);
let version, alerts, cameras, protect, video;

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

function renderAlerts() {
  const names = cameras.map((c) => c.name).filter(Boolean);
  $("alerts").replaceChildren(...alerts.map((a) => {
    const webhook = () => location.origin + "/alert/" + a.key;
    const url = copyBox("Webhook URL (POST or GET)", webhook());
    const hdr = copyBox("Header", "X-Protmon-Token: <token from secrets.env>");
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
        field("Text color", input(a, "fg", { className: "short", placeholder: "#rrggbb" })),
        field("Background", input(a, "bg", { className: "short", placeholder: "#rrggbb" })),
        field("Alternate color", input(a, "alt", { className: "short", placeholder: "#rrggbb" })),
        field("Animation", select(a, "animation", [["", "default"], ...ANIMATIONS.map((n) => [n, n])]))),
      el("div", { className: "row" }, url.box, hdr.box),
      warn);
  }));
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
      type: "password", autocomplete: "off", required: !c.existing, placeholder: c.existing ? "unchanged" : "rtsp://",
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
  cameras = Object.keys(cfg.cameras || {}).map((name) => ({ name, existing: true, url: "" }));
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
    if (!c.existing && !c.url) throw new Error(`Camera ${c.name} needs an RTSP URL.`);
    cfg.cameras[c.name] = c.url || null;
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

$("add-alert").onclick = () => {
  alerts.push({ key: "", orig: "", text: "", priority: "2", duration: "60", camera: "", fg: "", bg: "", alt: "", animation: "" });
  renderAlerts();
};
$("add-camera").onclick = () => { cameras.push({ name: "", existing: false, url: "" }); renderCameras(); };
$("save").onclick = save;
$("reload").onclick = () => load("Reloaded.");
load();
