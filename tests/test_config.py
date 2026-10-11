import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import GOOD
from protmon.config import ConfigError, load

ROOT = Path(__file__).resolve().parents[1]


def write(tmp_path, data):
    p = tmp_path / "c.json"
    p.write_text(data if isinstance(data, str) else json.dumps(data))
    return p


def mutated(**alert):
    c = copy.deepcopy(GOOD)
    c["alerts"]["garage-motion"].update(alert)
    return c


def test_good_config(tmp_path):
    cfg = load(write(tmp_path, GOOD))
    assert cfg["alerts"]["garage-motion"] == {"text": "Garage Motion", "priority": 3,
                                              "camera": "garage", "duration": 30}


def test_example_config_loads():
    cfg = load(ROOT / "config.example.json")
    assert cfg["alerts"]["panic-button"]["camera"] is None


@pytest.mark.parametrize("text,msg", [
    ('{"cameras": {}, "alerts": {}, "alerts": {}}', "duplicate key 'alerts'"),
    ('{"cameras": {}, "alerts": {"a": {"text": "A", "priority": 2}, "a": {}}}', "duplicate key 'a'"),
    ('{"cameras": {}, "alerts": {"a": {"text": "A", "text": "B", "priority": 2}}}', "duplicate key 'text'"),
])
def test_duplicate_keys(tmp_path, text, msg):
    with pytest.raises(ConfigError, match=msg):
        load(write(tmp_path, text))


@pytest.mark.parametrize("data,msg", [
    (mutated(camera="nope"), "garage-motion.*unknown camera 'nope'"),
    (mutated(camera=5), "unknown camera"),
    (mutated(text=5), "text"),
    (mutated(text=""), "text"),
    (mutated(priority=0), "priority"),
    (mutated(priority=4), "priority"),
    (mutated(priority="1"), "priority"),
    (mutated(priority=True), "priority"),
    (mutated(duration=0), "duration"),
    (mutated(duration=-1), "duration"),
    (mutated(duration=1.5), "duration"),
    (mutated(duration="30"), "duration"),
    (mutated(duration=True), "duration"),
    (mutated(priority=1, duration=30), "priority 1 must have duration null"),
    (mutated(duraton=5), "unknown field.*duraton"),
    ({**GOOD, "extra": {}}, "top level"),
    ({"cameras": {"g": ""}, "alerts": {}}, "camera 'g'"),
    ({"cameras": [], "alerts": {}}, "must be objects"),
    ({"cameras": {}, "alerts": {"a": "x"}}, "must be an object"),
])
def test_rejections(tmp_path, data, msg):
    with pytest.raises(ConfigError, match=msg):
        load(write(tmp_path, data))


def test_missing_text(tmp_path):
    c = copy.deepcopy(GOOD)
    del c["alerts"]["garage-motion"]["text"]
    with pytest.raises(ConfigError, match="garage-motion.*text is required"):
        load(write(tmp_path, c))


@pytest.mark.parametrize("key", ["..", ".", "a/b", "a b", "a?x", ""])
def test_unsafe_key(tmp_path, key):
    c = {"cameras": {}, "alerts": {key: {"text": "A", "priority": 2}}}
    with pytest.raises(ConfigError, match="key must match"):
        load(write(tmp_path, c))


def test_missing_file_and_bad_json(tmp_path):
    with pytest.raises(ConfigError):
        load(tmp_path / "absent.json")
    with pytest.raises(ConfigError, match="invalid JSON"):
        load(write(tmp_path, "{"))


def run_cli(path):
    return subprocess.run([sys.executable, "-m", "protmon.config", str(path)], capture_output=True,
                          text=True, env={"PYTHONPATH": str(ROOT / "server")})


def test_cli_exit_codes(tmp_path):
    assert run_cli(ROOT / "config.example.json").returncode == 0
    r = run_cli(write(tmp_path, mutated(camera="nope")))
    assert r.returncode == 1
    assert "unknown camera 'nope'" in r.stderr
