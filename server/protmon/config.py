"""Config loader and validator. Stdlib only: preflight runs it under system python3."""
import ipaddress
import json
import re
import sys

KEY_RE = re.compile(r"[A-Za-z0-9_-]+")
FIELDS = {"text", "priority", "camera", "duration", "style"}
STYLE_FIELDS = {"fg", "bg", "alt", "animation"}
ANIMATIONS = ("none", "flash", "flash-alt", "invert")
COLOR_RE = re.compile(r"#(?:[0-9A-Fa-f]{3}|[0-9A-Fa-f]{6})")
PRIORITIES = (1, 2, 3)
PROTECT_FIELDS = {"address", "interval", "failures"}
HOST_RE = re.compile(r"[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?")


class ConfigError(Exception):
    pass


def _no_dupes(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise ConfigError(f"duplicate key {k!r}")
        out[k] = v
    return out


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _style(key, s):
    if s is None:
        return None
    if not isinstance(s, dict):
        raise ConfigError(f"alert {key!r}: style must be an object")
    unknown = sorted(set(s) - STYLE_FIELDS)
    if unknown:
        raise ConfigError(f"alert {key!r}: unknown style field(s) {', '.join(unknown)}")
    for f in ("fg", "bg", "alt"):
        if f in s and not (isinstance(s[f], str) and COLOR_RE.fullmatch(s[f])):
            raise ConfigError(f"alert {key!r}: style {f} must be a hex color like #rgb or #rrggbb, got {s[f]!r}")
    if "animation" in s and s["animation"] not in ANIMATIONS:
        raise ConfigError(f"alert {key!r}: style animation must be one of {', '.join(ANIMATIONS)}, got {s['animation']!r}")
    return dict(s) or None


def _alert(key, a, cameras):
    if not KEY_RE.fullmatch(key):
        raise ConfigError(f"alert {key!r}: key must match [A-Za-z0-9_-]+")
    if not isinstance(a, dict):
        raise ConfigError(f"alert {key!r}: must be an object")
    unknown = sorted(set(a) - FIELDS)
    if unknown:
        raise ConfigError(f"alert {key!r}: unknown field(s) {', '.join(unknown)}")
    text = a.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ConfigError(f"alert {key!r}: text is required and must be a non-empty string")
    pri = a.get("priority")
    if not _is_int(pri) or pri not in PRIORITIES:
        raise ConfigError(f"alert {key!r}: priority must be 1, 2 or 3, got {pri!r}")
    cam = a.get("camera")
    if cam is not None and (not isinstance(cam, str) or cam not in cameras):
        raise ConfigError(f"alert {key!r}: unknown camera {cam!r}")
    dur = a.get("duration")
    if not _is_int(dur) or dur <= 0:
        raise ConfigError(f"alert {key!r}: duration must be a positive integer, got {dur!r}")
    return {"text": text, "priority": pri, "camera": cam, "duration": dur, "style": _style(key, a.get("style"))}


def _protect(p):
    if p is None:
        return None
    if not isinstance(p, dict):
        raise ConfigError("protect must be an object")
    unknown = sorted(set(p) - PROTECT_FIELDS)
    if unknown:
        raise ConfigError(f"protect: unknown field(s) {', '.join(unknown)}")
    addr = p.get("address")
    if not isinstance(addr, str) or not HOST_RE.fullmatch(addr):
        raise ConfigError(f"protect: address must be a host name or IPv4 address without scheme or path, got {addr!r}")
    out = {"address": addr}
    for f, default in (("interval", 30), ("failures", 3)):
        v = p.get(f, default)
        if not _is_int(v) or v <= 0:
            raise ConfigError(f"protect: {f} must be a positive integer, got {v!r}")
        out[f] = v
    return out


def validate(raw):
    if not isinstance(raw, dict) or not {"cameras", "alerts"} <= set(raw) <= {"cameras", "alerts", "protect"}:
        raise ConfigError("top level must be an object with 'cameras' and 'alerts', and optionally 'protect'")
    cameras, alerts = raw["cameras"], raw["alerts"]
    if not isinstance(cameras, dict) or not isinstance(alerts, dict):
        raise ConfigError("'cameras' and 'alerts' must be objects")
    for name, url in cameras.items():
        if not isinstance(url, str) or not url.strip():
            raise ConfigError(f"camera {name!r}: must be a non-empty string")
    return {
        "cameras": dict(cameras),
        "alerts": {k: _alert(k, a, cameras) for k, a in alerts.items()},
        "protect": _protect(raw.get("protect")),
    }


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f, object_pairs_hook=_no_dupes)
    except OSError as e:
        raise ConfigError(f"{path}: {e.strerror or e}") from e
    except json.JSONDecodeError as e:
        raise ConfigError(f"{path}: invalid JSON: {e}") from e
    except ConfigError as e:
        raise ConfigError(f"{path}: {e}") from e
    try:
        return validate(raw)
    except ConfigError as e:
        raise ConfigError(f"{path}: {e}") from e


def load_secrets(env):
    """Return (token, allowed_ips) from PROTMON_TOKEN and PROTMON_ALLOWED_IPS."""
    token = env.get("PROTMON_TOKEN", "").strip()
    if not token:
        raise ConfigError("PROTMON_TOKEN is not set (see secrets.env)")
    raw = [p.strip() for p in env.get("PROTMON_ALLOWED_IPS", "").split(",") if p.strip()]
    if not raw:
        raise ConfigError("PROTMON_ALLOWED_IPS is not set (comma-separated IPs, see secrets.env)")
    try:
        return token, {ipaddress.ip_address(p) for p in raw}
    except ValueError as e:
        raise ConfigError(f"PROTMON_ALLOWED_IPS: {e}") from e


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python3 -m protmon.config PATH")
    try:
        load(sys.argv[1])
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        sys.exit(1)
