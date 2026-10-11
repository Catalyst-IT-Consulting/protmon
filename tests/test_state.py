import json

import pytest

from protmon.eventlog import EventLog
from protmon.state import State

TIMED = {"text": "Motion", "priority": 3, "camera": "garage", "duration": 30}
STICKY = {"text": "Panic", "priority": 1, "camera": None, "duration": None}


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return 1_000_000.0 + self.t


@pytest.fixture
def env(cfg_path, events):
    clock = Clock()
    # wall clock deliberately differs from monotonic to prove expiry uses the latter
    st = State(EventLog(), mono=lambda: clock.t, wall=clock)
    return st, clock, events


def test_single_trigger(env):
    st, _, events = env
    assert st.trigger("m", TIMED, "10.0.0.5") == "received"
    (a,) = st.snapshot()
    assert (a["key"], a["text"], a["priority"], a["camera"]) == ("m", "Motion", 3, "garage")
    assert a["expires_at"] and a["first_seen"] == a["last_seen"]
    assert events() == [{**events()[0], "event": "received", "key": "m", "priority": 3, "source_ip": "10.0.0.5"}]


def test_repeat_refreshes_without_duplicate(env):
    st, clock, events = env
    st.trigger("m", TIMED, "10.0.0.5")
    first = st.snapshot()[0]
    clock.t = 20
    assert st.trigger("m", TIMED, "10.0.0.6") == "refreshed"
    snap = st.snapshot()
    assert len(snap) == 1
    assert snap[0]["first_seen"] == first["first_seen"]
    assert snap[0]["last_seen"] != first["last_seen"]
    assert snap[0]["expires_at"] != first["expires_at"]
    clock.t = 40  # past the original deadline, inside the refreshed one
    assert st.expire_due() == [] and len(st.snapshot()) == 1
    assert [e["event"] for e in events()] == ["received", "refreshed"]
    assert events()[1]["source_ip"] == "10.0.0.6"


def test_timed_expiry(env):
    st, clock, events = env
    st.trigger("m", TIMED, "10.0.0.5")
    clock.t = 29
    assert st.expire_due() == []
    clock.t = 30
    assert st.expire_due() == ["m"]
    assert st.snapshot() == []
    assert events()[-1]["event"] == "expired" and events()[-1]["key"] == "m"


def test_sticky_survives_any_timer(env):
    st, clock, events = env
    st.trigger("p", STICKY, "10.0.0.5")
    clock.t = 10**7
    assert st.expire_due() == []
    (a,) = st.snapshot()
    assert a["expires_at"] is None
    assert [e["event"] for e in events()] == ["received"]


def test_unmapped_is_sticky_and_sorts_first(env):
    st, clock, events = env
    st.trigger("m", TIMED, "10.0.0.5")
    st.trigger("mystery", None, "10.0.0.9")
    snap = st.snapshot()
    assert [a["key"] for a in snap] == ["mystery", "m"]
    assert snap[0]["text"] == "Unmapped alert: mystery" and snap[0]["priority"] == 1
    assert snap[0]["expires_at"] is None
    clock.t = 10**6
    st.expire_due()
    assert [a["key"] for a in st.snapshot()] == ["mystery"]
    assert events()[1]["key"] == "mystery" and events()[1]["priority"] == 1


def test_log_is_one_json_object_per_line(env, tmp_path):
    st, _, _ = env
    st.trigger("m", TIMED, "10.0.0.5")
    lines = (tmp_path / "events.jsonl").read_text().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["ts"]


@pytest.fixture
def mk(env, tmp_path):
    _, clock, events = env
    path = str(tmp_path / "active.json")
    return (lambda: State(EventLog(), mono=lambda: clock.t, wall=clock, path=path)), clock, events, path


def test_sticky_survives_restart(mk):
    new, clock, _, path = mk
    a = new()
    a.trigger("p", STICKY, "10.0.0.5")
    clock.t += 3600
    b = new()
    b.restore()
    (r,) = b.snapshot()
    assert (r["key"], r["expires_at"]) == ("p", None)
    assert b.expire_due() == []


def test_timed_remaining_recomputed_and_outage_expiry_logged(mk):
    new, clock, events, path = mk
    a = new()
    a.trigger("m", TIMED, "10.0.0.5")
    a.trigger("x", {**TIMED, "duration": 5}, "10.0.0.5")
    clock.t += 10
    b = new()
    b.restore()
    assert [r["key"] for r in b.snapshot()] == ["m"]
    assert any(e["event"] == "expired" and e["key"] == "x" and e["restored"] for e in events())
    assert [e for e in events() if e["event"] == "restored"][-1]["count"] == 1
    clock.t += 19
    assert b.expire_due() == []
    clock.t += 1
    assert b.expire_due() == ["m"]


def test_corrupt_file_raises_system_alert(mk):
    new, _, events, path = mk
    open(path, "w").write("{not json")
    b = new()
    b.restore()
    (r,) = b.snapshot()
    assert r["key"] == "system-state-lost" and r["priority"] == 1 and r["expires_at"] is None
    assert any(e["event"] == "state-restore-failed" for e in events())
    assert open(path + ".corrupt").read() == "{not json"
    c = new()
    c.restore()
    assert c.snapshot() == []


def test_missing_file_is_clean_start(mk):
    b = mk[0]()
    b.restore()
    assert b.snapshot() == []


def test_save_is_atomic_and_failure_does_not_raise(mk, tmp_path):
    new, _, events, path = mk
    a = new()
    a.trigger("p", STICKY, "10.0.0.5")
    assert sorted(p.name for p in tmp_path.iterdir() if p.name.startswith("active")) == ["active.json"]
    a.path = str(tmp_path / "nodir" / "active.json")
    a.trigger("q", STICKY, "10.0.0.5")
    assert any(e["event"] == "state-save-failed" for e in events())
