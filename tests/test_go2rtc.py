import json
import stat
import subprocess
import sys
from pathlib import Path

from protmon import go2rtc

CAMS = {"garage": "rtsp://192.0.2.21/s", "front door": "rtsp://u:p%40ss@192.0.2.22:554/x?a=1"}
SERVER = Path(__file__).resolve().parents[1] / "server"


def test_render_has_streams_and_loopback_only():
    out = go2rtc.render(CAMS)
    assert '  "garage": "rtsp://192.0.2.21/s"\n' in out
    assert '  "front door": "rtsp://u:p%40ss@192.0.2.22:554/x?a=1"\n' in out
    assert 'listen: "127.0.0.1:1984"' in out
    assert 'listen: "127.0.0.1:8555"' in out
    assert "stun" not in out and "0.0.0.0" not in out and "preload" not in out


def test_write_is_0600(tmp_path):
    out = tmp_path / "go2rtc.yaml"
    go2rtc.write(CAMS, str(out))
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    assert "garage" in out.read_text()
    assert [p.name for p in tmp_path.iterdir()] == ["go2rtc.yaml"]


def test_cli_generates_from_config_and_rejects_bad_config(tmp_path):
    cfg, out = tmp_path / "c.json", tmp_path / "o.yaml"
    cfg.write_text(json.dumps({"cameras": CAMS, "alerts": {}}))
    run = lambda: subprocess.run([sys.executable, "-m", "protmon.go2rtc", str(cfg), str(out)],
                                 cwd=SERVER, capture_output=True, text=True)
    assert run().returncode == 0 and "garage" in out.read_text()
    cfg.write_text("{nope")
    assert run().returncode == 1
