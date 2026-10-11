#!/usr/bin/env python3
"""Throwaway discovery listener: log every webhook request in full.

Replaced by the real server in M2. Stdlib only.
"""
import base64
import json
import os
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG_PATH = os.environ.get("DISCOVERY_LOG", "/var/log/protmon/discovery.jsonl")
PORT = int(os.environ.get("DISCOVERY_PORT", "8080"))
MAX_BODY = 1 << 20  # listens on the LAN, so bound what we read


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _reply(self, code, body=b"", ctype="text/plain"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _log(self, body):
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "source_ip": self.client_address[0],
            "method": self.command,
            "path": self.path,
            "headers": [[k, v] for k, v in self.headers.items()],
        }
        try:
            entry["body"] = body.decode("utf-8")
        except UnicodeDecodeError:
            entry["body"] = base64.b64encode(body).decode("ascii")
            entry["body_base64"] = True
        try:
            # Reopen per write: survives logrotate, and one write() per line.
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError as e:
            print(f"log write failed: {e}", file=sys.stderr)

    def _handle(self):
        if self.command == "GET" and self.path == "/healthz":
            return self._reply(200, b"ok\n")
        if self.command == "GET" and self.path == "/api/state":
            return self._reply(200, b'{"alerts": []}', "application/json")
        try:
            n = max(0, int(self.headers.get("Content-Length") or 0))
        except ValueError:
            n = 0
        # A body over the cap is truncated in the log, and the connection is
        # closed so the unread rest is not parsed as the next request.
        body = self.rfile.read(min(n, MAX_BODY))
        if n > MAX_BODY:
            self.close_connection = True
        self._log(body)
        fail = os.environ.get("DISCOVERY_FAIL") == "1" or self.path.startswith("/fail/")
        self._reply(500 if fail else 200, b"fail\n" if fail else b"ok\n")

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = _handle

    def log_message(self, *args):
        pass  # the JSONL file is the record


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
