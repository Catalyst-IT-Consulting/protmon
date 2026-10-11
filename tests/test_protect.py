import pytest

from protmon.eventlog import EventLog
from protmon.protect import Watch
from protmon.state import PROTECT_KEY, State


@pytest.fixture
def env(cfg_path, events):
    log = EventLog()
    st = State(log)
    return st, Watch(st, log), events


def names(events):
    return [e["event"] for e in events()]


def test_raises_only_after_threshold(env):
    st, w, events = env
    w.record("h", "timeout", 3)
    w.record("h", "timeout", 3)
    assert PROTECT_KEY not in st.alerts
    w.record("h", "timeout", 3)
    assert st.alerts[PROTECT_KEY]["text"] == "Protect unreachable"
    assert names(events).count("protect-unreachable") == 1


def test_success_resets_count(env):
    st, w, _ = env
    w.record("h", "x", 2)
    w.record("h", None, 2)
    w.record("h", "x", 2)
    assert PROTECT_KEY not in st.alerts


def test_refreshed_while_down_logged_once(env):
    st, w, events = env
    for _ in range(4):
        w.record("h", "x", 2)
    assert names(events).count("protect-unreachable") == 1
    assert "refreshed" in names(events)


def test_recovery_clears_and_logs(env):
    st, w, events = env
    for _ in range(3):
        w.record("h", "x", 3)
    w.record("h", None, 3)
    assert PROTECT_KEY not in st.alerts
    assert names(events)[-2:] == ["cleared", "protect-restored"]
    w.record("h", None, 3)
    assert names(events).count("protect-restored") == 1


def test_clears_alert_restored_from_disk(env, tmp_path):
    st, w, events = env
    st.path = str(tmp_path / "a.json")
    for _ in range(3):
        w.record("h", "x", 3)
    st2 = State(EventLog(), path=st.path)
    st2.restore()
    assert PROTECT_KEY in st2.alerts
    Watch(st2, st2.log).record("h", None, 3)
    assert PROTECT_KEY not in st2.alerts
    assert "protect-restored" in names(events)
