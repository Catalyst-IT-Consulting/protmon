import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from protmon.app import app

FIX = json.loads((Path(__file__).parent / "fixtures" / "alarm_manager_webhook.json").read_text())
BODY = FIX["body"]
HEAD = {"x-protmon-token": "s3cret"}
CONSOLE = ("127.0.0.1", 40000)  # allowlisted by the cfg_path fixture, and local


def post(c, key, headers=HEAD):
    return c.post(f"/alert/{key}", content=BODY, headers={"content-type": "application/json", **headers})


def test_mapped_key_accepted(cfg_path, events):
    with TestClient(app, client=CONSOLE) as c:
        assert post(c, "garage-motion").status_code == 204
        assert [a["key"] for a in c.get("/api/state").json()["alerts"]] == ["garage-motion"]
    e = events()[-1]
    assert e["event"] == "received" and e["source_ip"] == "127.0.0.1" and e["body"] == BODY


def test_get_is_accepted_too(cfg_path):
    with TestClient(app, client=CONSOLE) as c:
        assert c.get("/alert/garage-motion", headers=HEAD).status_code == 204


def test_allowlist_entry_other_than_local(cfg_path, events):
    with TestClient(app, client=("198.51.100.30", 1)) as c:
        assert post(c, "garage-motion").status_code == 204


@pytest.mark.parametrize("headers,addr", [
    ({"x-protmon-token": "wrong"}, CONSOLE),
    ({}, CONSOLE),
    (HEAD, ("203.0.113.9", 40000)),
])
def test_rejected(cfg_path, events, headers, addr):
    with TestClient(app, client=addr) as c:
        r = post(c, "garage-motion", headers=headers)
        assert r.status_code == 403 and r.content == b""
        assert app.state.alerts.snapshot() == []
    e = events()[-1]
    assert e["event"] == "rejected-auth" and e["source_ip"] == addr[0] and e["path"] == "/alert/garage-motion"
    assert "s3cret" not in json.dumps(events())


def test_query_token_alone_is_rejected(cfg_path, events):
    with TestClient(app, client=CONSOLE) as c:
        assert c.post("/alert/garage-motion?token=s3cret", content=BODY).status_code == 403
    assert events()[-1]["event"] == "rejected-auth"
    assert "s3cret" not in json.dumps(events())


def test_unknown_key_raises_unmapped_alert(cfg_path, events):
    with TestClient(app, client=CONSOLE) as c:
        assert post(c, "mystery").status_code == 204
        alerts = c.get("/api/state").json()["alerts"]
    assert alerts[0]["text"] == "Unmapped alert: mystery" and alerts[0]["priority"] == 1
    assert [e["event"] for e in events()] == ["startup", "unmapped"]


def test_bad_key_charset_is_404(cfg_path):
    with TestClient(app, client=CONSOLE) as c:
        assert post(c, "a.b").status_code == 404
        assert c.get("/api/state").json()["alerts"] == []


@pytest.mark.parametrize("addr,code", [(("192.0.2.7", 1), 404), (("::1", 1), 200), (("127.0.0.1", 1), 200)])
@pytest.mark.parametrize("path", ["/healthz", "/api/state"])
def test_local_only_http(cfg_path, path, addr, code):
    with TestClient(app, client=addr) as c:
        assert c.get(path).status_code == code


def test_local_only_ws(cfg_path):
    with TestClient(app, client=("192.0.2.7", 1)) as c:
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect("/ws"):
                pass


def test_state_shape_and_no_camera_url(cfg_path):
    with TestClient(app, client=CONSOLE) as c:
        post(c, "garage-motion")
        post(c, "panic")
        doc = c.get("/api/state").json()
    assert set(doc) == {"alerts", "commit", "server_time", "clock_synced", "max_streams", "ip"}
    assert doc["max_streams"] == 2
    assert doc["server_time"][-6] in "+-" and doc["commit"]
    assert [a["key"] for a in doc["alerts"]] == ["panic", "garage-motion"]
    assert doc["alerts"][1]["camera"] == "garage"
    assert "rtsp" not in json.dumps(doc)


def test_ws_initial_state_then_webhook_update(cfg_path):
    with TestClient(app, client=CONSOLE) as c, c.websocket_connect("/ws") as ws:
        first = ws.receive_json()
        assert first["type"] == "state" and first["alerts"] == [] and first["commit"]
        post(c, "garage-motion")
        update = ws.receive_json()
        assert update["type"] == "state" and [a["key"] for a in update["alerts"]] == ["garage-motion"]


def test_page_and_assets_served_locally(cfg_path):
    with TestClient(app, client=CONSOLE) as c:
        assert "text/html" in (r := c.get("/")).headers["content-type"] and "<script" in r.text
        assert "javascript" in c.get("/web/app.js").headers["content-type"]
        assert "text/css" in c.get("/web/style.css").headers["content-type"]
        assert c.get("/web/index.html").status_code == 404
        assert c.get("/web/..%2Fserver%2Fprotmon%2Fapp.py").status_code == 404


@pytest.mark.parametrize("path", ["/", "/web/app.js", "/web/style.css"])
def test_page_hidden_from_lan(cfg_path, path):
    with TestClient(app, client=("198.51.100.30", 1)) as c:
        assert c.get(path).status_code == 404


def test_unmapped_flag_in_state(cfg_path):
    with TestClient(app, client=CONSOLE) as c:
        post(c, "garage-motion")
        post(c, "nope")
        flags = {a["key"]: a["unmapped"] for a in c.get("/api/state").json()["alerts"]}
    assert flags == {"garage-motion": False, "nope": True}


def test_ws_ping_carries_server_time(cfg_path, monkeypatch):
    monkeypatch.setattr("protmon.app.PING_SECONDS", 0.05)
    with TestClient(app, client=CONSOLE) as c, c.websocket_connect("/ws") as ws:
        ws.receive_json()
        ping = ws.receive_json()
    assert ping["type"] == "ping" and ping["server_time"][-6] in "+-"


def test_state_ip_hidden(cfg_path):
    cfg_path.write_text(json.dumps({**json.loads(cfg_path.read_text()), "display": {"show_ip": False}}))
    with TestClient(app, client=CONSOLE) as c:
        assert c.get("/api/state").json()["ip"] is None
