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

LOCAL = ("127.0.0.1", 50000)
SERVER = Path(__file__).resolve().parents[1] / "server"


def test_healthz_reports_commit(cfg_path):
    with TestClient(app, client=LOCAL) as client:
        r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["commit"]


def test_state_has_no_alerts_and_startup_logged(cfg_path, events):
    with TestClient(app, client=LOCAL) as client:
        r = client.get("/api/state")
    assert r.status_code == 200
    assert r.json()["alerts"] == []
    ev = events()
    assert ev[0]["event"] == "startup" and ev[0]["key"] is None and ev[0]["alerts"] == 2
    assert ev[0]["ts"][-6] in "+-"


def test_bad_camera_fails_startup_with_clear_error(cfg_path):
    bad = json.loads(cfg_path.read_text())
    bad["alerts"]["garage-motion"]["camera"] = "nope"
    cfg_path.write_text(json.dumps(bad))
    with pytest.raises(ConfigError, match="garage-motion.*unknown camera 'nope'"):
        with TestClient(app, client=LOCAL):
            pass


def test_process_exits_nonzero_on_bad_config(cfg_path):
    cfg_path.write_text(json.dumps({"cameras": {}, "alerts": {"a": {"text": "A", "priority": 2, "camera": "x"}}}))
    r = subprocess.run([sys.executable, "-m", "protmon"], cwd=SERVER, capture_output=True, text=True,
                       timeout=30, env={**os.environ, "PROTMON_CONFIG": str(cfg_path)})
    assert r.returncode != 0
    assert "unknown camera 'x'" in r.stderr


def test_reload_success_and_failure_keep_old(cfg_path, events):
    with TestClient(app, client=LOCAL):
        new = json.loads(cfg_path.read_text())
        new["alerts"]["extra"] = {"text": "Extra", "priority": 2, "duration": 60}
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
            new["alerts"]["extra"] = {"text": "Extra", "priority": 2, "duration": 60}
            cfg_path.write_text(json.dumps(new))
            os.kill(os.getpid(), signal.SIGHUP)
            for _ in range(50):
                if "extra" in app.state.config["alerts"]:
                    return
                await asyncio.sleep(0.05)
            raise AssertionError("SIGHUP did not reload")

    asyncio.run(scenario())
    assert "config-reloaded" in [e["event"] for e in events()]


@pytest.mark.parametrize("var", ["PROTMON_TOKEN", "PROTMON_ALLOWED_IPS"])
def test_missing_secret_fails_startup(cfg_path, monkeypatch, var):
    monkeypatch.delenv(var)
    with pytest.raises(ConfigError, match=var):
        with TestClient(app, client=LOCAL):
            pass
    r = subprocess.run([sys.executable, "-m", "protmon"], cwd=SERVER, capture_output=True, text=True,
                       timeout=30, env=dict(os.environ))
    assert r.returncode != 0 and var in r.stderr


def test_alert_restored_after_restart(cfg_path, events):
    with TestClient(app, client=LOCAL) as client:
        app.state.alerts.trigger("panic", app.state.config["alerts"]["panic"], "127.0.0.1")
    with TestClient(app, client=LOCAL) as client:
        assert [a["key"] for a in client.get("/api/state").json()["alerts"]] == ["panic"]
    assert "restored" in [e["event"] for e in events()]


def test_reload_restarts_go2rtc_only_when_cameras_change(cfg_path, events, monkeypatch):
    calls = []
    monkeypatch.setattr(appmod.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    with TestClient(app, client=LOCAL):
        new = json.loads(cfg_path.read_text())
        new["alerts"]["extra"] = {"text": "Extra", "priority": 2, "duration": 60}
        cfg_path.write_text(json.dumps(new))
        assert appmod.reload_config(app) is True
        assert calls == []

        new["cameras"]["lobby"] = "rtsp://192.0.2.22/s"
        cfg_path.write_text(json.dumps(new))
        assert appmod.reload_config(app) is True
        assert len(calls) == 1 and calls[0][1:2] == ["try-restart"] and "protmon-go2rtc.service" in calls[0]
    assert "go2rtc-restarted" in [e["event"] for e in events()]


def test_go2rtc_restart_failure_is_logged_not_raised(cfg_path, events, monkeypatch):
    def boom(cmd, **kw):
        raise OSError("no systemctl")
    monkeypatch.setattr(appmod.subprocess, "run", boom)
    with TestClient(app, client=LOCAL):
        new = json.loads(cfg_path.read_text())
        new["cameras"] = {}
        new["alerts"] = {}
        cfg_path.write_text(json.dumps(new))
        assert appmod.reload_config(app) is True
    assert "go2rtc-restart-failed" in [e["event"] for e in events()]


def test_clock_synced_follows_flag_file(cfg_path, tmp_path, monkeypatch):
    flag = tmp_path / "synchronized"
    monkeypatch.setenv("PROTMON_TIMESYNC_FLAG", str(flag))
    with TestClient(app, client=LOCAL) as client:
        assert client.get("/api/state").json()["clock_synced"] is False
        with client.websocket_connect("/ws") as ws:
            assert ws.receive_json()["clock_synced"] is False
        flag.touch()
        assert client.get("/api/state").json()["clock_synced"] is True
        assert appmod._clock_synced() is True
