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
                                              "camera": "garage", "duration": 30, "style": None}


def test_missing_duration_rejected(tmp_path):
    c = copy.deepcopy(GOOD)
    del c["alerts"]["garage-motion"]["duration"]
    with pytest.raises(ConfigError, match="duration must be a positive integer"):
        load(write(tmp_path, c))


def test_priority1_with_duration_accepted(tmp_path):
    cfg = load(write(tmp_path, mutated(priority=1, duration=1800)))
    assert cfg["alerts"]["garage-motion"]["duration"] == 1800


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
    (mutated(duration=None), "duration must be a positive integer"),
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


def test_style_accepted():
    st = {"fg": "#fff", "bg": "#0a0A0a", "alt": "#123", "animation": "flash-alt"}
    assert load_dict(mutated(style=st))["alerts"]["garage-motion"]["style"] == st


@pytest.mark.parametrize("style,msg", [
    ({"color": "#fff"}, "unknown style field"),
    ({"fg": "red"}, "style fg must be a hex color"),
    ({"bg": "#12345"}, "style bg must be a hex color"),
    ({"alt": "#fff; x"}, "style alt must be a hex color"),
    ({"animation": "spin"}, "style animation must be one of"),
    ("flash", "style must be an object"),
])
def test_bad_style_rejected(tmp_path, style, msg):
    with pytest.raises(ConfigError, match=msg):
        load(write(tmp_path, mutated(style=style)))


def load_dict(data):
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        return load(write(Path(d), data))
