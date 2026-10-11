from fastapi.testclient import TestClient

from protmon.app import app

client = TestClient(app)


def test_healthz_reports_commit():
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["commit"]


def test_state_has_no_alerts():
    r = client.get("/api/state")
    assert r.status_code == 200
    assert r.json() == {"alerts": []}
