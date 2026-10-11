"""HDMI connector watch: logs displays being plugged or unplugged."""
import asyncio
from pathlib import Path

DRM = Path("/sys/class/drm")
INTERVAL = 5


def scan(root=DRM):
    """Connector name -> connected, e.g. {"HDMI-A-1": True}. Empty without DRM."""
    out = {}
    for p in sorted(root.glob("card*-HDMI-*/status")):
        try:
            out[p.parent.name.split("-", 1)[1]] = p.read_text().strip() == "connected"
        except OSError:
            pass
    return out


def changes(old, new):
    """Log events for the difference between two scans."""
    return [("display-connected" if up else "display-disconnected", name)
            for name, up in new.items() if old.get(name) != up and (name in old or up)]


async def watch(app, root=DRM, interval=INTERVAL):
    seen = await asyncio.to_thread(scan, root)
    if seen:
        app.state.log.write("displays", connected=[n for n, up in seen.items() if up], connectors=list(seen))
    while True:
        await asyncio.sleep(interval)
        now = await asyncio.to_thread(scan, root)
        for event, name in changes(seen, now):
            app.state.log.write(event, connector=name)
        seen = now
