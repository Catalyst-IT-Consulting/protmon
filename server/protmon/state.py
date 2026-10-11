import time
from datetime import datetime


def _iso(t):
    return datetime.fromtimestamp(t).astimezone().isoformat(timespec="seconds")


class State:
    """Active alerts. Monotonic clock drives expiry, wall clock is for display and the log."""

    def __init__(self, log, mono=time.monotonic, wall=time.time):
        self.log, self.mono, self.wall = log, mono, wall
        self.alerts = {}
        self.on_change = lambda: None

    def trigger(self, key, entry, source_ip, body=None):
        """Create or refresh an alert. entry is a config alert dict, or None if unmapped."""
        unmapped = entry is None
        if entry is None:
            entry = {"text": f"Unmapped alert: {key}", "priority": 1, "camera": None, "duration": None}
        now, wall = self.mono(), self.wall()
        dur = entry["duration"]
        a = self.alerts.get(key)
        event = "refreshed" if a else "unmapped" if unmapped else "received"
        if not a:
            a = self.alerts[key] = {"key": key, "first_seen": wall}
        a.update(text=entry["text"], priority=entry["priority"], camera=entry["camera"],
                 last_seen=wall, deadline=None if dur is None else now + dur,
                 expires_wall=None if dur is None else wall + dur)
        extra = {} if body is None else {"body": body}
        self.log.write(event, key, priority=a["priority"], source_ip=source_ip, **extra)
        self.on_change()
        return event

    def expire_due(self):
        now = self.mono()
        due = [k for k, a in self.alerts.items() if a["deadline"] is not None and now >= a["deadline"]]
        for k in due:
            a = self.alerts.pop(k)
            self.log.write("expired", k, priority=a["priority"])
        if due:
            self.on_change()
        return due

    def snapshot(self):
        rows = sorted(self.alerts.values(), key=lambda a: (a["priority"], a["first_seen"]))
        return [{"key": a["key"], "text": a["text"], "priority": a["priority"], "camera": a["camera"],
                 "first_seen": _iso(a["first_seen"]), "last_seen": _iso(a["last_seen"]),
                 "expires_at": None if a["expires_wall"] is None else _iso(a["expires_wall"])}
                for a in rows]
