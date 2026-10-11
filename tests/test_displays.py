import asyncio
from types import SimpleNamespace

from protmon import displays


def connector(root, name, status):
    d = root / f"card1-{name}"
    d.mkdir(exist_ok=True)
    (d / "status").write_text(status + "\n")


def test_scan_reads_hdmi_only_and_tolerates_missing_drm(tmp_path):
    connector(tmp_path, "HDMI-A-1", "connected")
    connector(tmp_path, "HDMI-A-2", "disconnected")
    connector(tmp_path, "DSI-1", "connected")
    assert displays.scan(tmp_path) == {"HDMI-A-1": True, "HDMI-A-2": False}
    assert displays.scan(tmp_path / "nope") == {}


def test_changes():
    old = {"HDMI-A-1": True, "HDMI-A-2": True}
    assert displays.changes(old, old) == []
    assert displays.changes(old, {"HDMI-A-1": True, "HDMI-A-2": False}) == [("display-disconnected", "HDMI-A-2")]
    assert displays.changes({"HDMI-A-1": False}, {"HDMI-A-1": True}) == [("display-connected", "HDMI-A-1")]


def test_watch_logs_initial_state_then_changes(tmp_path):
    connector(tmp_path, "HDMI-A-1", "connected")
    connector(tmp_path, "HDMI-A-2", "connected")
    rows = []
    app = SimpleNamespace(state=SimpleNamespace(log=SimpleNamespace(write=lambda e, **f: rows.append((e, f)))))

    async def run():
        t = asyncio.create_task(displays.watch(app, tmp_path, interval=0.01))
        await asyncio.sleep(0.1)
        connector(tmp_path, "HDMI-A-2", "disconnected")
        await asyncio.sleep(0.1)
        t.cancel()

    asyncio.run(run())
    assert rows[0] == ("displays", {"connected": ["HDMI-A-1", "HDMI-A-2"], "connectors": ["HDMI-A-1", "HDMI-A-2"]})
    assert rows[1:] == [("display-disconnected", {"connector": "HDMI-A-2"})]


def test_watch_without_drm_is_silent(tmp_path):
    rows = []
    app = SimpleNamespace(state=SimpleNamespace(log=SimpleNamespace(write=lambda e, **f: rows.append(e))))

    async def run():
        t = asyncio.create_task(displays.watch(app, tmp_path / "none", interval=0.01))
        await asyncio.sleep(0.05)
        t.cancel()

    asyncio.run(run())
    assert rows == []
