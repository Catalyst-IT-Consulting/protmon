import json
import os
import sys
from datetime import datetime

DEFAULT_PATH = "/var/log/protmon/events.jsonl"


class EventLog:
    def __init__(self, path=None):
        path = self.path = path or os.environ.get("PROTMON_EVENT_LOG", DEFAULT_PATH)
        try:
            self._f = open(path, "a", encoding="utf-8")
        except OSError as e:
            print(f"protmon: event log {path}: {e}", file=sys.stderr)
            self._f = None

    def write(self, event, key=None, **details):
        rec = {"ts": datetime.now().astimezone().isoformat(timespec="seconds"),
               "event": event, "key": key, **details}
        try:
            self._f.write(json.dumps(rec) + "\n")
            self._f.flush()
        except (OSError, AttributeError, ValueError) as e:
            print(f"protmon: event log write failed: {e}: {rec}", file=sys.stderr)

    def close(self):
        if self._f:
            self._f.close()
