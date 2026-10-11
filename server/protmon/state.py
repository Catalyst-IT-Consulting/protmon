import json
import os
import sys
import time
from datetime import datetime


LOST_KEY = "system-state-lost"
SYSTEM_DURATION = 1800
FIELDS = ("key", "text", "priority", "camera", "first_seen", "last_seen", "expires_wall", "unmapped")


def _iso(t):
    return datetime.fromtimestamp(t).astimezone().isoformat(timespec="seconds")


class State:
    """Active alerts. Monotonic clock drives expiry, wall clock is for display and the log."""

    def __init__(self, log, mono=time.monotonic, wall=time.time, path=None):
        self.log, self.mono, self.wall, self.path = log, mono, wall, path
        self.alerts = {}
        self.on_change = lambda: None

    def trigger(self, key, entry, source_ip, body=None):
        """Create or refresh an alert. entry is a config alert dict, or None if unmapped."""
        unmapped = entry is None
        if entry is None:
            entry = {"text": f"Unmapped alert: {key}", "priority": 1, "camera": None, "duration": SYSTEM_DURATION}
        now, wall = self.mono(), self.wall()
        dur = entry["duration"]
        a = self.alerts.get(key)
        event = "refreshed" if a else "unmapped" if unmapped else "received"
        if not a:
            a = self.alerts[key] = {"key": key, "first_seen": wall}
        a.update(unmapped=unmapped, text=entry["text"], priority=entry["priority"], camera=entry["camera"],
                 last_seen=wall, deadline=None if dur is None else now + dur,
                 expires_wall=None if dur is None else wall + dur)
        extra = {} if body is None else {"body": body}
        self.log.write(event, key, priority=a["priority"], source_ip=source_ip, **extra)
        self.save()
        self.on_change()
        return event

    def expire_due(self):
        now = self.mono()
        due = [k for k, a in self.alerts.items() if a["deadline"] is not None and now >= a["deadline"]]
        for k in due:
            a = self.alerts.pop(k)
            self.log.write("expired", k, priority=a["priority"])
        if due:
            self.save()
            self.on_change()
        return due

    def save(self):
        """Atomically write active alerts. The lost-state notice is not persisted."""
        if not self.path:
            return
        rows = [{f: a[f] for f in FIELDS} for k, a in self.alerts.items() if k != LOST_KEY]
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"alerts": rows}, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except OSError as e:
            print(f"protmon: state save {self.path}: {e}", file=sys.stderr)
            self.log.write("state-save-failed", error=str(e))

    def restore(self):
        """Load saved alerts. Timed ones keep their wall-clock end; missing file is a clean start."""
        if not self.path:
            return
        try:
            with open(self.path, encoding="utf-8") as f:
                rows = [{k: r[k] for k in FIELDS} for r in json.load(f)["alerts"]]
        except FileNotFoundError:
            return
        except (OSError, ValueError, KeyError, TypeError) as e:
            print(f"protmon: state restore {self.path}: {e!r}", file=sys.stderr)
            self.log.write("state-restore-failed", error=repr(e))
            try:
                os.replace(self.path, self.path + ".corrupt")
            except OSError:
                pass
            self.trigger(LOST_KEY, {"text": "Alert state lost on restart: check active alerts in Protect",
                                    "priority": 1, "camera": None, "duration": SYSTEM_DURATION}, "local")
            return
        now, wall = self.mono(), self.wall()
        for r in rows:
            end = r["expires_wall"]
            if end is not None and end <= wall:
                self.log.write("expired", r["key"], priority=r["priority"], restored=True)
                continue
            self.alerts[r["key"]] = {**r, "deadline": None if end is None else now + end - wall}
        self.log.write("restored", count=len(self.alerts))
        self.save()

    def snapshot(self):
        rows = sorted(self.alerts.values(), key=lambda a: (a["priority"], a["first_seen"]))
        return [{"key": a["key"], "text": a["text"], "priority": a["priority"], "camera": a["camera"],
                 "unmapped": a.get("unmapped", False), "first_seen": _iso(a["first_seen"]), "last_seen": _iso(a["last_seen"]),
                 "expires_at": None if a["expires_wall"] is None else _iso(a["expires_wall"])}
                for a in rows]
