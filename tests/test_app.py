import asyncio
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from protmon import app as appmod
from protmon.app import app
from protmon.config import ConfigError

SERVER = Path(__file__).resolve().parents[1] / "server"


def test_healthz_reports_commit(cfg_path):
    with TestClient(app) as client:
        r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["commit"]


def test_state_has_no_alerts_and_startup_logged(cfg_path, events):
    with TestClient(app) as client:
        r = client.get("/api/state")
    assert r.status_code == 200
    assert r.json() == {"alerts": []}
    ev = events()
    assert ev[0]["event"] == "startup" and ev[0]["key"] is None and ev[0]["alerts"] == 2
    assert ev[0]["ts"][-6] in "+-"


def test_bad_camera_fails_startup_with_clear_error(cfg_path):
    bad = json.loads(cfg_path.read_text())
    bad["alerts"]["garage-motion"]["camera"] = "nope"
    cfg_path.write_text(json.dumps(bad))
    with pytest.raises(ConfigError, match="garage-motion.*unknown camera 'nope'"):
        with TestClient(app):
            pass


def test_process_exits_nonzero_on_bad_config(cfg_path):
    cfg_path.write_text(json.dumps({"cameras": {}, "alerts": {"a": {"text": "A", "priority": 2, "camera": "x"}}}))
    r = subprocess.run([sys.executable, "-m", "protmon"], cwd=SERVER, capture_output=True, text=True,
                       timeout=30, env={**os.environ, "PROTMON_CONFIG": str(cfg_path)})
    assert r.returncode != 0
    assert "unknown camera 'x'" in r.stderr


def test_reload_success_and_failure_keep_old(cfg_path, events):
    with TestClient(app):
        new = json.loads(cfg_path.read_text())
        new["alerts"]["extra"] = {"text": "Extra", "priority": 2}
        cfg_path.write_text(json.dumps(new))
        assert appmod.reload_config(app) is True
        assert "extra" in app.state.config["alerts"]

        cfg_path.write_text("{not json")
        assert appmod.reload_config(app) is False
        assert "extra" in app.state.config["alerts"]
    names = [e["event"] for e in events()]
    assert names == ["startup", "config-reloaded", "config-reload-failed"]
    assert "invalid JSON" in events()[-1]["error"]


def test_sighup_reloads(cfg_path, events):
    async def scenario():
        async with app.router.lifespan_context(app):
            new = json.loads(cfg_path.read_text())
            new["alerts"]["extra"] = {"text": "Extra", "priority": 2}
            cfg_path.write_text(json.dumps(new))
            os.kill(os.getpid(), signal.SIGHUP)
            for _ in range(50):
                if "extra" in app.state.config["alerts"]:
                    return
                await asyncio.sleep(0.05)
            raise AssertionError("SIGHUP did not reload")

    asyncio.run(scenario())
    assert "config-reloaded" in [e["event"] for e in events()]
