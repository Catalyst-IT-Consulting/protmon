import asyncio
import hmac
import ipaddress
import os
import signal
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, Response, WebSocket, WebSocketException
from fastapi.responses import FileResponse
from starlette.requests import HTTPConnection

from . import config, protect
from .eventlog import EventLog
from .state import State

REPO = Path(__file__).resolve().parents[2]
CONFIG_PATH = "/etc/protmon/config.json"


def _commit() -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5, check=True,
        )
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


COMMIT = _commit()
LOCAL = {"127.0.0.1", "::1"}
BODY_LOG_MAX = 2048
PING_SECONDS = 15


def reload_config(app) -> bool:
    """Swap in a freshly validated config; on failure keep the old one."""
    try:
        cfg = config.load(app.state.config_path)
    except config.ConfigError as e:
        app.state.log.write("config-reload-failed", error=str(e))
        return False
    cameras_changed = cfg["cameras"] != app.state.config["cameras"]
    app.state.config = cfg
    app.state.log.write("config-reloaded", alerts=len(cfg["alerts"]), cameras=len(cfg["cameras"]))
    if cameras_changed:
        _restart_go2rtc(app)
    return True


def _restart_go2rtc(app):
    """go2rtc regenerates its stream config on start, so a restart applies new cameras."""
    try:
        subprocess.run(["systemctl", "try-restart", "--no-block", "protmon-go2rtc.service"],
                       check=True, capture_output=True, timeout=10)
        app.state.log.write("go2rtc-restarted")
    except (OSError, subprocess.SubprocessError) as e:
        app.state.log.write("go2rtc-restart-failed", error=str(e))


async def _sweep(state):
    while True:
        await asyncio.sleep(1)
        state.expire_due()


@asynccontextmanager
async def lifespan(app):
    app.state.config_path = os.environ.get("PROTMON_CONFIG", CONFIG_PATH)
    app.state.config = config.load(app.state.config_path)  # ConfigError stops startup
    app.state.token, app.state.allowed_ips = config.load_secrets(os.environ)
    app.state.log = log = EventLog()
    app.state.alerts = State(log, path=os.environ.get("PROTMON_STATE", "/var/lib/protmon/active.json"))
    app.state.watchers = set()
    app.state.alerts.on_change = lambda: [ev.set() for ev in app.state.watchers]
    cfg = app.state.config
    log.write("startup", commit=COMMIT, alerts=len(cfg["alerts"]), cameras=len(cfg["cameras"]))
    app.state.alerts.restore()
    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(signal.SIGHUP, reload_config, app)
    except RuntimeError as e:  # not the main thread (test clients)
        print(f"protmon: SIGHUP reload disabled: {e}", file=sys.stderr)
    sweeper = asyncio.create_task(_sweep(app.state.alerts))
    checker = asyncio.create_task(protect.watch(app))
    try:
        yield
    finally:
        sweeper.cancel()
        checker.cancel()
        loop.remove_signal_handler(signal.SIGHUP)  # False if never installed
        log.close()


app = FastAPI(title="Protmon", lifespan=lifespan)


def local_only(conn: HTTPConnection):
    if not conn.client or conn.client.host not in LOCAL:
        if conn.scope["type"] == "websocket":
            raise WebSocketException(code=1008)
        raise HTTPException(404)


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _state_doc():
    return {"alerts": app.state.alerts.snapshot(), "commit": COMMIT, "server_time": _now()}


PAGE_FILES = {"app.js": "text/javascript", "style.css": "text/css"}


@app.get("/", dependencies=[Depends(local_only)])
def index():
    return FileResponse(REPO / "web" / "index.html", headers={"cache-control": "no-cache"})


@app.get("/web/{name}", dependencies=[Depends(local_only)])
def asset(name: str):
    if name not in PAGE_FILES:
        raise HTTPException(404)
    return FileResponse(REPO / "web" / name, media_type=PAGE_FILES[name], headers={"cache-control": "no-cache"})


@app.get("/healthz", dependencies=[Depends(local_only)])
def healthz():
    return {"status": "ok", "commit": COMMIT}


@app.get("/api/state", dependencies=[Depends(local_only)])
def state():
    return _state_doc()


@app.websocket("/ws", dependencies=[Depends(local_only)])
async def ws(ws: WebSocket):
    await ws.accept()
    changed = asyncio.Event()
    app.state.watchers.add(changed)
    try:
        await ws.send_json({"type": "state", **_state_doc()})
        while True:
            try:
                await asyncio.wait_for(changed.wait(), PING_SECONDS)
            except asyncio.TimeoutError:
                await ws.send_json({"type": "ping", "server_time": _now()})
                continue
            changed.clear()
            await ws.send_json({"type": "state", **_state_doc()})
    except Exception:  # client went away
        pass
    finally:
        app.state.watchers.discard(changed)


def _authorized(request: Request) -> bool:
    try:
        ip_ok = ipaddress.ip_address(request.client.host) in request.app.state.allowed_ips
    except (AttributeError, ValueError):
        ip_ok = False
    sent = request.headers.get("x-protmon-token", "").encode()
    token_ok = hmac.compare_digest(sent, request.app.state.token.encode())
    return ip_ok and token_ok


async def _fire(key, source_ip, body):
    st = app.state
    st.alerts.trigger(key, st.config["alerts"].get(key), source_ip, body)


@app.api_route("/alert/{key}", methods=["GET", "POST"], status_code=204)
async def alert(key: str, request: Request, background: BackgroundTasks):
    ip = request.client.host if request.client else None
    if not _authorized(request):
        request.app.state.log.write("rejected-auth", source_ip=ip, path=request.url.path)
        return Response(status_code=403)
    if not config.KEY_RE.fullmatch(key):
        raise HTTPException(404)
    body = (await request.body())[:BODY_LOG_MAX].decode(errors="replace")
    background.add_task(_fire, key, ip, body)
    return Response(status_code=204)
