import copy
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import GOOD
from protmon.config import TONES, ConfigError, load

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
                                              "camera": "garage", "duration": 30, "style": None, "sound": None}


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


def test_sound_accepted():
    snd = {"tone": "siren", "plays": 3}
    assert load_dict(mutated(sound=snd))["alerts"]["garage-motion"]["sound"] == snd


@pytest.mark.parametrize("sound,msg", [
    ({"tone": "siren"}, "sound must be an object with tone and plays"),
    ({"tone": "siren", "plays": 1, "vol": 1}, "sound must be an object with tone and plays"),
    ({"tone": "kazoo", "plays": 1}, "sound tone must be one of"),
    ({"tone": "siren", "plays": 0}, "sound plays must be an integer"),
    ({"tone": "siren", "plays": 21}, "sound plays must be an integer"),
    ({"tone": "siren", "plays": True}, "sound plays must be an integer"),
])
def test_bad_sound_rejected(tmp_path, sound, msg):
    with pytest.raises(ConfigError, match=msg):
        load(write(tmp_path, mutated(sound=sound)))


def test_tones_match_page():
    js = (ROOT / "web" / "sounds.js").read_text().split("const TONES = {", 1)[1].split("\n};", 1)[0]
    assert tuple(re.findall(r'^  "([a-z-]+)":', js, re.M)) == TONES


def load_dict(data):
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        return load(write(Path(d), data))


def with_protect(p):
    return {**GOOD, "protect": p}


def test_protect_absent_is_off(tmp_path):
    assert load(write(tmp_path, GOOD))["protect"] is None


def test_protect_defaults(tmp_path):
    cfg = load(write(tmp_path, with_protect({"address": "192.0.2.10"})))
    assert cfg["protect"] == {"address": "192.0.2.10", "interval": 30, "failures": 3}


@pytest.mark.parametrize("p,msg", [
    ({}, "address"),
    ({"address": ""}, "address"),
    ({"address": "https://192.0.2.10"}, "address"),
    ({"address": "192.0.2.10/x"}, "address"),
    ({"address": "h", "interval": 0}, "interval"),
    ({"address": "h", "interval": True}, "interval"),
    ({"address": "h", "failures": -1}, "failures"),
    ({"address": "h", "failures": "3"}, "failures"),
    ({"address": "h", "port": 443}, "unknown field.*port"),
    ("10.0.0.1", "must be an object"),
])
def test_protect_rejections(tmp_path, p, msg):
    with pytest.raises(ConfigError, match=msg):
        load(write(tmp_path, with_protect(p)))


def test_video_defaults_and_override(tmp_path):
    assert load(write(tmp_path, GOOD))["video"] == {"max_streams": 2}
    assert load(write(tmp_path, {**GOOD, "video": {"max_streams": 4}}))["video"] == {"max_streams": 4}


@pytest.mark.parametrize("v,msg", [
    ({"max_streams": 0}, "max_streams"),
    ({"max_streams": True}, "max_streams"),
    ({"max_streams": "2"}, "max_streams"),
    ({"max": 2}, "unknown field.*max"),
    (2, "must be an object"),
])
def test_video_rejections(tmp_path, v, msg):
    with pytest.raises(ConfigError, match=msg):
        load(write(tmp_path, {**GOOD, "video": v}))


def test_display_defaults_and_override(tmp_path):
    assert load(write(tmp_path, GOOD))["display"] == {"show_ip": True}
    assert load(write(tmp_path, {**GOOD, "display": {"show_ip": False}}))["display"] == {"show_ip": False}


@pytest.mark.parametrize("v,msg", [
    ({"show_ip": "yes"}, "show_ip"),
    ({"show_ip": 1}, "show_ip"),
    ({"ip": True}, "unknown field.*ip"),
    (True, "must be an object"),
])
def test_display_rejections(tmp_path, v, msg):
    with pytest.raises(ConfigError, match=msg):
        load(write(tmp_path, {**GOOD, "display": v}))
