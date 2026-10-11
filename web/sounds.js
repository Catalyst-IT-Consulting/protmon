"use strict";
// Alert tones made with Web Audio. Names must match TONES in server/protmon/config.py.
const GAP_S = 0.5;  // silence between plays
let audio;

// One oscillator note at time t. f is a frequency or a list swept evenly over d.
function note(c, out, t, f, d, { type = "sine", vol = 0.3, decay = false } = {}) {
  const o = c.createOscillator(), g = c.createGain();
  o.type = type;
  if (Array.isArray(f)) o.frequency.setValueCurveAtTime(f, t, d); else o.frequency.value = f;
  g.gain.setValueAtTime(0, t);
  g.gain.linearRampToValueAtTime(vol, t + 0.005);
  if (decay) g.gain.exponentialRampToValueAtTime(0.001, t + d);
  else { g.gain.setValueAtTime(vol, t + d - 0.01); g.gain.linearRampToValueAtTime(0, t + d); }
  o.connect(g).connect(out);
  o.start(t);
  o.stop(t + d);
}

// Struck bell: inharmonic partials with a long decay.
function strike(c, out, t, f, d) {
  [[1, 0.3], [2.76, 0.1], [5.4, 0.05]].forEach(([m, v]) => note(c, out, t, f * m, d, { vol: v, decay: true }));
}

function beeps(n, f, on, off) {
  return (c, out, t) => {
    for (let i = 0; i < n; i++) note(c, out, t + i * (on + off), f, on, { type: "square", vol: 0.15 });
    return n * (on + off);
  };
}

// Each tone schedules itself at t and returns its length in seconds.
const TONES = {
  "chime": (c, out, t) => { note(c, out, t, 659, 0.9, { decay: true }); note(c, out, t + 0.4, 523, 1.2, { decay: true }); return 1.6; },
  "ding-dong": (c, out, t) => { strike(c, out, t, 784, 1.2); strike(c, out, t + 0.6, 622, 1.6); return 2.2; },
  "bell": (c, out, t) => { strike(c, out, t, 880, 2); return 2; },
  "beep": beeps(1, 1000, 0.3, 0),
  "double-beep": beeps(2, 1200, 0.15, 0.1),
  "triple-beep": beeps(3, 1200, 0.12, 0.08),
  "pulse": beeps(6, 880, 0.08, 0.08),
  "alarm": (c, out, t) => {
    for (let i = 0; i < 6; i++) note(c, out, t + i * 0.25, i % 2 ? 770 : 960, 0.25, { type: "square", vol: 0.15 });
    return 1.5;
  },
  "klaxon": (c, out, t) => {
    for (let i = 0; i < 2; i++) note(c, out, t + i * 0.9, [200, 260], 0.7, { type: "sawtooth", vol: 0.2 });
    return 1.6;
  },
  "siren": (c, out, t) => { note(c, out, t, [600, 1200, 600], 2.4); return 2.4; },
  "warble": (c, out, t) => { note(c, out, t, Array.from({ length: 25 }, (_, i) => i % 2 ? 1000 : 700), 1.5, { type: "triangle" }); return 1.5; },
  "rising": (c, out, t) => { note(c, out, t, [400, 1600], 0.8); return 0.8; },
  "falling": (c, out, t) => { note(c, out, t, [1600, 400], 0.8); return 0.8; },
  "chirp": (c, out, t) => {
    for (let i = 0; i < 3; i++) note(c, out, t + i * 0.15, [2000, 3200], 0.06, { vol: 0.2 });
    return 0.45;
  },
};

// Play sound {tone, plays}; onend runs when it finishes unless stopped first.
function playSound(sound, onend) {
  const c = audio ||= new AudioContext();
  if (c.state === "suspended") c.resume();
  const out = c.createGain();
  out.connect(c.destination);
  let t = c.currentTime + 0.05;
  for (let i = 0; i < sound.plays; i++) t += TONES[sound.tone](c, out, t) + GAP_S;
  const timer = setTimeout(() => { out.disconnect(); if (onend) onend(); }, (t - c.currentTime) * 1000);
  return { stop() { clearTimeout(timer); out.disconnect(); } };
}
