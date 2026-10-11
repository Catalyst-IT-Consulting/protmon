import asyncio
import os
import signal
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from . import config
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


def reload_config(app) -> bool:
    """Swap in a freshly validated config; on failure keep the old one."""
    try:
        cfg = config.load(app.state.config_path)
    except config.ConfigError as e:
        app.state.log.write("config-reload-failed", error=str(e))
        return False
    app.state.config = cfg
    app.state.log.write("config-reloaded", alerts=len(cfg["alerts"]), cameras=len(cfg["cameras"]))
    return True


async def _sweep(state):
    while True:
        await asyncio.sleep(1)
        state.expire_due()


@asynccontextmanager
async def lifespan(app):
    app.state.config_path = os.environ.get("PROTMON_CONFIG", CONFIG_PATH)
    app.state.config = config.load(app.state.config_path)  # ConfigError stops startup
    app.state.log = log = EventLog()
    app.state.alerts = State(log)
    cfg = app.state.config
    log.write("startup", commit=COMMIT, alerts=len(cfg["alerts"]), cameras=len(cfg["cameras"]))
    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(signal.SIGHUP, reload_config, app)
    except RuntimeError as e:  # not the main thread (test clients)
        print(f"protmon: SIGHUP reload disabled: {e}", file=sys.stderr)
    sweeper = asyncio.create_task(_sweep(app.state.alerts))
    try:
        yield
    finally:
        sweeper.cancel()
        loop.remove_signal_handler(signal.SIGHUP)  # False if never installed
        log.close()


app = FastAPI(title="Protmon", lifespan=lifespan)


@app.get("/healthz")
def healthz():
    return {"status": "ok", "commit": COMMIT}


@app.get("/api/state")
def state():
    return {"alerts": app.state.alerts.snapshot()}
