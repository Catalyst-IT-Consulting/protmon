import json
from pathlib import Path

import pytest

GOOD = {
    "cameras": {"garage": "rtsp://192.0.2.21/s"},
    "alerts": {
        "garage-motion": {"text": "Garage Motion", "priority": 3, "camera": "garage", "duration": 30},
        "panic": {"text": "Panic", "priority": 1, "camera": None, "duration": 1800},
    },
}


@pytest.fixture
def cfg_path(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text(json.dumps(GOOD))
    monkeypatch.setenv("PROTMON_CONFIG", str(p))
    monkeypatch.setenv("PROTMON_EVENT_LOG", str(tmp_path / "events.jsonl"))
    monkeypatch.setenv("PROTMON_STATE", str(tmp_path / "active.json"))
    monkeypatch.setenv("PROTMON_TOKEN", "s3cret")
    monkeypatch.setenv("PROTMON_ALLOWED_IPS", "198.51.100.30, 127.0.0.1")
    monkeypatch.setenv("PROTMON_ADMIN_PASSWORD", "pw")
    return p


@pytest.fixture
def events(tmp_path):
    def read():
        f = Path(tmp_path / "events.jsonl")
        return [json.loads(line) for line in f.read_text().splitlines()] if f.exists() else []
    return read
