import base64
import copy
import json
import stat

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from protmon.app import app
from protmon.config import ConfigError
from conftest import GOOD

LOCAL = ("127.0.0.1", 40000)
H = {"x-protmon-admin": "1"}
AUTH = {"authorization": "Basic " + base64.b64encode(b"admin:pw").decode()}
URL = "rtsp://192.0.2.21/s"


@pytest.fixture
def client(cfg_path):
    with TestClient(app, client=LOCAL, headers=AUTH) as c:
        yield c


def load(c):
    return c.get("/admin/api/config").json()


def put(c, cfg, version, headers=H):
    return c.put("/admin/api/config", json={"config": cfg, "version": version}, headers=headers)


def test_outside_network_forbidden(cfg_path):
    with TestClient(app, client=("198.51.100.30", 1), headers=AUTH) as c:
        assert c.get("/admin/api/config").status_code == 403
        assert put(c, GOOD, "x").status_code == 403


def test_admin_network_allowed(cfg_path, monkeypatch):
    monkeypatch.setenv("PROTMON_ADMIN_NETWORKS", "198.51.100.0/24")
    with TestClient(app, client=("198.51.100.30", 1), headers=AUTH) as c:
        assert c.get("/admin/api/config").status_code == 200


def test_bad_admin_networks_fail_startup(cfg_path, monkeypatch):
    monkeypatch.setenv("PROTMON_ADMIN_NETWORKS", "nope")
    with pytest.raises(ConfigError, match="PROTMON_ADMIN_NETWORKS"):
        with TestClient(app, client=LOCAL):
            pass


def test_get_shows_camera_urls_and_secrets(client):
    doc = load(client)
    assert doc["config"]["cameras"] == {"garage": URL}
    assert doc["secrets"] == {"token": "s3cret", "allowed_ips": ["127.0.0.1", "198.51.100.30"], "admin_networks": []}
    assert "pw" not in json.dumps(doc)


def test_no_password_set_refuses_admin(cfg_path, monkeypatch, events):
    monkeypatch.delenv("PROTMON_ADMIN_PASSWORD")
    with TestClient(app, client=LOCAL, headers=AUTH) as c:
        assert c.get("/admin").status_code == 403
        assert c.get("/admin/api/config").status_code == 403
    assert [e["reason"] for e in events() if e["event"] == "rejected-admin"] == ["no admin password set"] * 2


@pytest.mark.parametrize("hdr", [{}, {"authorization": "Basic " + base64.b64encode(b"admin:nope").decode()},
                                 {"authorization": "Basic !!"}, {"authorization": "Bearer pw"}])
def test_bad_credentials_unauthorized(cfg_path, events, hdr):
    with TestClient(app, client=LOCAL, headers=hdr) as c:
        r = c.get("/admin")
        assert r.status_code == 401 and r.headers["www-authenticate"] == 'Basic realm="protmon admin"'
        assert c.get("/admin/api/config").status_code == 401
    ev = [e for e in events() if e["event"] == "rejected-admin"]
    assert len(ev) == 2 and ev[0]["source_ip"] == "127.0.0.1"


def test_password_with_colon_and_any_username(cfg_path, monkeypatch):
    monkeypatch.setenv("PROTMON_ADMIN_PASSWORD", "a:b")
    hdr = {"authorization": "Basic " + base64.b64encode(b"whoever:a:b").decode()}
    with TestClient(app, client=LOCAL, headers=hdr) as c:
        assert c.get("/admin/api/config").status_code == 200


def test_go2rtc_ws_requires_password(cfg_path):
    with TestClient(app, client=LOCAL) as c:
        assert _closed(c, "/admin/go2rtc/api/ws?src=garage") == 1008


def test_put_round_trip_reloads_without_restart(client, cfg_path, events):
    doc = load(client)
    cfg = doc["config"]
    cfg["alerts"]["panic"]["text"] = "Help"
    r = put(client, cfg, doc["version"])
    assert r.status_code == 200
    assert app.state.config["alerts"]["panic"]["text"] == "Help"
    assert json.loads(cfg_path.read_text())["cameras"]["garage"] == URL
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


def test_null_keeps_existing_camera_url(client, cfg_path):
    doc = load(client)
    doc["config"]["cameras"]["garage"] = None
    assert put(client, doc["config"], doc["version"]).status_code == 200
    assert json.loads(cfg_path.read_text())["cameras"]["garage"] == URL


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


def test_admin_page_and_assets_served(client):
    r = client.get("/admin")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert r.headers["cache-control"] == "no-cache"
    for name, kind in (("admin.js", "javascript"), ("admin.css", "css")):
        r = client.get(f"/admin/web/{name}")
        assert r.status_code == 200 and kind in r.headers["content-type"]


def test_admin_unknown_asset_404(client):
    assert client.get("/admin/web/admin.html").status_code == 404
    assert client.get("/admin/web/nope.js").status_code == 404


def test_admin_page_forbidden_outside_network(cfg_path):
    with TestClient(app, client=("198.51.100.30", 1), headers=AUTH) as c:
        assert c.get("/admin").status_code == 403
        assert c.get("/admin/web/admin.js").status_code == 403


def test_preview_page_uses_admin_assets(client):
    r = client.get("/admin/preview")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"
    assert "/admin/web/app.js" in r.text and "/admin/web/style.css" in r.text and '"/web/' not in r.text
    for name, kind in (("app.js", "javascript"), ("style.css", "css")):
        r = client.get(f"/admin/web/{name}")
        assert r.status_code == 200 and kind in r.headers["content-type"]


def test_preview_forbidden_outside_network(cfg_path):
    with TestClient(app, client=("198.51.100.30", 1), headers=AUTH) as c:
        assert c.get("/admin/preview").status_code == 403
        assert c.get("/admin/go2rtc/video-stream.js").status_code == 403


def test_go2rtc_proxy_only_serves_player_scripts(client):
    for name in ("api", "config.json", "nope.js"):
        assert client.get(f"/admin/go2rtc/{name}").status_code == 404


def _closed(c, url):
    with pytest.raises(WebSocketDisconnect) as e:
        with c.websocket_connect(url):
            pass
    return e.value.code


def test_go2rtc_ws_rejects_unknown_camera(client):
    assert _closed(client, "/admin/go2rtc/api/ws?src=nope") == 1008
    assert _closed(client, "/admin/go2rtc/api/ws") == 1008


def test_go2rtc_ws_forbidden_outside_network(cfg_path):
    with TestClient(app, client=("198.51.100.30", 1), headers=AUTH) as c:
        assert _closed(c, "/admin/go2rtc/api/ws?src=garage") == 1008


def _log(tmp_path, name, recs, raw=()):
    import gzip
    lines = "".join(json.dumps(r) + "\n" for r in recs) + "".join(raw)
    if name.endswith(".gz"):
        (tmp_path / name).write_bytes(gzip.compress(lines.encode()))
    else:
        (tmp_path / name).write_text(lines)


def _ev(ts, event="raised", key="k1", **kw):
    return {"ts": ts, "event": event, "key": key, **kw}


@pytest.fixture
def logs(cfg_path, tmp_path):
    _log(tmp_path, "events.jsonl.2.gz", [_ev("2026-01-01T10:00:00+00:00")])
    _log(tmp_path, "events.jsonl.1", [_ev("2026-01-02T10:00:00+00:00", "expired"),
                                      _ev("2026-01-02T11:00:00+00:00", key="k2", source_ip="10.0.0.5", body="hi")],
         raw=["not json\n", "\n"])
    _log(tmp_path, "events.jsonl", [_ev("2026-01-03T10:00:00+00:00", "rejected-auth", key=None)])


def events_of(c, **q):
    r = c.get("/admin/api/events", params=q)
    assert r.status_code == 200
    return r.json()


def test_events_newest_first_across_files(client, logs):
    got = events_of(client, since="2026-01-01")["events"]
    assert [e["ts"][:13] for e in got] == ["2026-01-03T10", "2026-01-02T11", "2026-01-02T10", "2026-01-01T10"]
    assert got[1]["source_ip"] == "10.0.0.5" and got[1]["body"] == "hi"


def test_events_filters(client, logs):
    assert [e["ts"][:13] for e in events_of(client, key="k1")["events"]] == ["2026-01-02T10", "2026-01-01T10"]
    assert [e["event"] for e in events_of(client, event="rejected-auth")["events"]] == ["rejected-auth"]
    got = events_of(client, key="k1", since="2026-01-02", until="2026-01-02")["events"]
    assert [e["event"] for e in got] == ["expired"]


def test_events_paging(client, logs):
    seen, q = [], {"since": "2026-01-01", "limit": 3}
    while True:
        doc = events_of(client, **q)
        seen += [e["ts"] for e in doc["events"]]
        if not doc["more"]:
            break
        q["before"] = doc["next"]
    assert seen == sorted(seen, reverse=True) and len(seen) == 4


def test_events_paging_same_second(client, cfg_path, tmp_path):
    _log(tmp_path, "events.jsonl", [_ev("2026-01-03T10:00:00+00:00", key="p", body=str(i)) for i in range(5)])
    seen, q = [], {"key": "p", "limit": 2}
    while True:
        doc = events_of(client, **q)
        seen += [e["body"] for e in doc["events"]]
        if not doc["more"]:
            break
        q["before"] = doc["next"]
    assert seen == ["4", "3", "2", "1", "0"]


def test_events_forbidden_outside_network(cfg_path):
    with TestClient(app, client=("198.51.100.30", 1), headers=AUTH) as c:
        assert c.get("/admin/api/events").status_code == 403
