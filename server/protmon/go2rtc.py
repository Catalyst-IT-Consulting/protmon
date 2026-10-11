"""Render go2rtc's stream config from the validated `cameras` section.

Stdlib only so the go2rtc unit can run it with system python3:
    python3 -m protmon.go2rtc CONFIG OUT
"""
import json
import os
import sys
import tempfile

from . import config

# Everything on loopback: the browser is on this box. Streams open on demand
# because no stream is preloaded.
HEAD = """\
api:
  listen: "127.0.0.1:1984"
rtsp:
  listen: ""
srtp:
  listen: ""
webrtc:
  listen: "127.0.0.1:8555"
  candidates: ["127.0.0.1:8555"]
  ice_servers: []
streams:
"""


def render(cameras: dict) -> str:
    # JSON strings are valid YAML double-quoted scalars.
    lines = [f"  {json.dumps(name)}: {json.dumps(url)}\n" for name, url in cameras.items()]
    return HEAD + ("".join(lines) or "  {}\n")


def write(cameras: dict, out: str) -> None:
    """Atomically write the config, mode 0600."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(out) or ".")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(render(cameras))
        os.replace(tmp, out)
    except BaseException:
        os.unlink(tmp)
        raise


def main(argv) -> int:
    if len(argv) != 2:
        print("usage: python3 -m protmon.go2rtc CONFIG OUT", file=sys.stderr)
        return 2
    try:
        write(config.load(argv[0])["cameras"], argv[1])
    except (config.ConfigError, OSError) as e:
        print(f"go2rtc config: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
