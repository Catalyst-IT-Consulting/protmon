import copy
import gzip
import hashlib
import ipaddress
import json
import os
import re
import tempfile
import threading
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse

from . import config

LOCAL = {"127.0.0.1", "::1"}
MUTATING = {"PUT", "POST", "DELETE", "PATCH"}
_write_lock = threading.Lock()


def admin_only(request: Request):
    host = request.client.host if request.client else None
    try:
        ok = host in LOCAL or any(ipaddress.ip_address(host) in n for n in request.app.state.admin_networks)
    except ValueError:
        ok = False
    if not ok:
        request.app.state.log.write("rejected-admin", source_ip=host, path=request.url.path)
        raise HTTPException(403, "forbidden")
    # A custom header forces a CORS preflight, which a cross-site page cannot pass.
    if request.method in MUTATING and (
        request.headers.get("x-protmon-admin") != "1"
        or not request.headers.get("content-type", "").startswith("application/json")
    ):
        raise HTTPException(403, "missing X-Protmon-Admin header or JSON content type")


router = APIRouter(prefix="/admin/api", dependencies=[Depends(admin_only)])


def _version(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read(path):
    with open(path, "rb") as f:
        data = f.read()
    return data, json.loads(data)


def _summary(old, new):
    out = {}
    for section in ("alerts", "cameras"):
        a, b = old.get(section, {}), new.get(section, {})
        out[f"{section}_added"] = sorted(b.keys() - a.keys())
        out[f"{section}_removed"] = sorted(a.keys() - b.keys())
        out[f"{section}_changed"] = sorted(k for k in a.keys() & b.keys() if a[k] != b[k])
    other = sorted(k for k in old.keys() | new.keys() if k not in ("alerts", "cameras") and old.get(k) != new.get(k))
    return {**out, "other_changed": other}


def _write(path, old_data, new_data):
    d = os.path.dirname(os.path.abspath(path))
    fd = os.open(path + ".bak", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(old_data)
    os.chmod(path + ".bak", 0o600)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".config-")  # mkstemp creates 0600
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(new_data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


WEB = Path(__file__).resolve().parents[2] / "web"
ASSETS = {"admin.js": "text/javascript", "admin.css": "text/css"}
NO_CACHE = {"cache-control": "no-cache"}
pages = APIRouter(dependencies=[Depends(admin_only)])


@pages.get("/admin")
def page():
    return FileResponse(WEB / "admin.html", headers=NO_CACHE)


@pages.get("/admin/web/{name}")
def asset(name: str):
    if name not in ASSETS:
        raise HTTPException(404)
    return FileResponse(WEB / name, media_type=ASSETS[name], headers=NO_CACHE)


@router.get("/config")
def get_config(request: Request):
    try:
        data, raw = _read(request.app.state.config_path)
    except (OSError, ValueError) as e:
        raise HTTPException(500, f"cannot read config: {e}")
    if isinstance(raw.get("cameras"), dict):
        raw["cameras"] = dict.fromkeys(raw["cameras"])  # camera URLs hold credentials
    return {"config": raw, "version": _version(data)}


@router.put("/config")
def put_config(request: Request, body: dict):
    from .app import reload_config  # app imports this module

    app = request.app
    new, version = body.get("config"), body.get("version")
    if not isinstance(new, dict) or not isinstance(version, str):
        return JSONResponse({"error": "body must be {config: object, version: string}"}, 400)
    with _write_lock:
        try:
            old_data, old = _read(app.state.config_path)
        except (OSError, ValueError) as e:
            return JSONResponse({"error": f"cannot read config: {e}"}, 500)
        if _version(old_data) != version:
            return JSONResponse({"error": "config changed since it was loaded; reload and retry"}, 409)
        merged = copy.deepcopy(new)
        cams = merged.get("cameras")
        if isinstance(cams, dict):
            for name, url in cams.items():
                if url is None:
                    if not isinstance(old.get("cameras"), dict) or name not in old["cameras"]:
                        return JSONResponse({"error": f"camera {name!r} needs a URL"}, 400)
                    cams[name] = old["cameras"][name]
        try:
            config.validate(copy.deepcopy(merged))
        except config.ConfigError as e:
            return JSONResponse({"error": str(e)}, 400)
        new_data = (json.dumps(merged, indent=2) + "\n").encode()
        _write(app.state.config_path, old_data, new_data)
        if not reload_config(app):
            return JSONResponse({"error": "saved but reload failed, see event log"}, 500)
        app.state.log.write("config-changed", source_ip=request.client.host if request.client else None,
                            **_summary(old, merged))
    return {"version": _version(new_data)}


def _log_files(path):
    """The live log, then rotated files from newest to oldest."""
    p = Path(path)
    rotated = []
    for f in p.parent.glob(p.name + ".*"):
        m = re.fullmatch(r"(\d+)(\.gz)?", f.name[len(p.name) + 1:])
        if m:
            rotated.append((int(m[1]), f))
    return [p] + [f for _, f in sorted(rotated)]


def _records(path):
    """Every parsable event, newest first."""
    for f in _log_files(path):
        try:
            opener = gzip.open if f.suffix == ".gz" else open
            with opener(f, "rt", encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
        except (OSError, EOFError):
            continue
        for line in reversed(lines):
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict) and isinstance(rec.get("ts"), str):
                yield rec


@router.get("/events")
def get_events(request: Request, key: str = "", event: str = "", since: str = "", until: str = "",
               before: str = "", limit: int = Query(100, ge=1, le=500)):
    # before is "<ts>,<n>": resume after n records already shown at that timestamp.
    cur_ts, _, n = before.partition(",")
    skip = int(n) if n.isdigit() else 0
    out, more = [], False
    for rec in _records(request.app.state.log.path):
        ts = rec["ts"]
        if (key and rec.get("key") != key) or (event and rec.get("event") != event):
            continue
        if (since and ts[:10] < since) or (until and ts[:10] > until):
            continue
        if cur_ts and (ts > cur_ts or (ts == cur_ts and skip > 0)):
            skip -= ts == cur_ts
            continue
        if len(out) == limit:
            more = True
            break
        out.append(rec)
    nxt = None
    if more:
        last = out[-1]["ts"]
        nxt = f"{last},{sum(r['ts'] == last for r in out) + (int(n) if last == cur_ts and n.isdigit() else 0)}"
    return {"events": out, "more": more, "next": nxt}
