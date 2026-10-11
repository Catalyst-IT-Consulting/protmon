import copy
import json
import stat

import pytest
from fastapi.testclient import TestClient

from protmon.app import app
from protmon.config import ConfigError
from conftest import GOOD

LOCAL = ("127.0.0.1", 40000)
H = {"x-protmon-admin": "1"}
URL = "rtsp://192.0.2.21/s"


@pytest.fixture
def client(cfg_path):
    with TestClient(app, client=LOCAL) as c:
        yield c


def load(c):
    return c.get("/admin/api/config").json()


def put(c, cfg, version, headers=H):
    return c.put("/admin/api/config", json={"config": cfg, "version": version}, headers=headers)


def test_outside_network_forbidden(cfg_path):
    with TestClient(app, client=("198.51.100.30", 1)) as c:
        assert c.get("/admin/api/config").status_code == 403
        assert put(c, GOOD, "x").status_code == 403


def test_admin_network_allowed(cfg_path, monkeypatch):
    monkeypatch.setenv("PROTMON_ADMIN_NETWORKS", "198.51.100.0/24")
    with TestClient(app, client=("198.51.100.30", 1)) as c:
        assert c.get("/admin/api/config").status_code == 200


def test_bad_admin_networks_fail_startup(cfg_path, monkeypatch):
    monkeypatch.setenv("PROTMON_ADMIN_NETWORKS", "nope")
    with pytest.raises(ConfigError, match="PROTMON_ADMIN_NETWORKS"):
        with TestClient(app, client=LOCAL):
            pass


def test_get_redacts_camera_urls(client):
    doc = load(client)
    assert doc["config"]["cameras"] == {"garage": None}
    assert URL not in json.dumps(doc)


def test_put_round_trip_reloads_without_restart(client, cfg_path, events):
    doc = load(client)
    cfg = doc["config"]
    cfg["alerts"]["panic"]["text"] = "Help"
    r = put(client, cfg, doc["version"])
    assert r.status_code == 200
    assert app.state.config["alerts"]["panic"]["text"] == "Help"
    assert json.loads(cfg_path.read_text())["cameras"]["garage"] == URL  # null kept the URL
    assert load(client)["version"] == r.json()["version"]
    ev = [e for e in events() if e["event"] == "config-changed"][0]
    assert ev["alerts_changed"] == ["panic"] and URL not in json.dumps(ev)


def test_backup_and_mode(client, cfg_path):
    before = cfg_path.read_bytes()
    doc = load(client)
    doc["config"]["alerts"]["panic"]["text"] = "Help"
    assert put(client, doc["config"], doc["version"]).status_code == 200
    bak = cfg_path.with_name("config.json.bak")
    assert bak.read_bytes() == before
    assert stat.S_IMODE(bak.stat().st_mode) == 0o600
    assert stat.S_IMODE(cfg_path.stat().st_mode) == 0o600


def test_invalid_config_refused(client, cfg_path):
    before = cfg_path.read_bytes()
    doc = load(client)
    doc["config"]["alerts"]["panic"]["priority"] = 9
    r = put(client, doc["config"], doc["version"])
    assert r.status_code == 400 and "priority" in r.json()["error"]
    assert cfg_path.read_bytes() == before
    assert not cfg_path.with_name("config.json.bak").exists()


def test_stale_version_conflict(client, cfg_path):
    doc = load(client)
    cfg_path.write_text(json.dumps(GOOD) + "\n")  # hand edit after the form loaded
    r = put(client, doc["config"], doc["version"])
    assert r.status_code == 409


def test_missing_csrf_header_forbidden(client):
    doc = load(client)
    assert put(client, doc["config"], doc["version"], headers={}).status_code == 403
    r = client.put("/admin/api/config", content=json.dumps({"config": doc["config"], "version": doc["version"]}),
                   headers={**H, "content-type": "text/plain"})
    assert r.status_code == 403


def test_null_for_new_camera_refused(client):
    doc = load(client)
    cfg = copy.deepcopy(doc["config"])
    cfg["cameras"]["porch"] = None
    assert put(client, cfg, doc["version"]).status_code == 400


def test_new_camera_url_replaces_and_is_written(client, cfg_path):
    doc = load(client)
    cfg = copy.deepcopy(doc["config"])
    cfg["cameras"]["porch"] = "rtsp://192.0.2.22/s"
    assert put(client, cfg, doc["version"]).status_code == 200
    assert json.loads(cfg_path.read_text())["cameras"]["porch"] == "rtsp://192.0.2.22/s"
