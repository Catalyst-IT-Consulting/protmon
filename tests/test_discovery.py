import base64
import http.client
import json
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import discovery  # noqa: E402


class DiscoveryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fd, cls.log = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        discovery.LOG_PATH = cls.log
        cls.srv = discovery.ThreadingHTTPServer(("127.0.0.1", 0), discovery.Handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        os.unlink(cls.log)

    def setUp(self):
        open(self.log, "w").close()

    def req(self, method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_port)
        c.request(method, path, body)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, data

    def lines(self):
        with open(self.log) as f:
            return [json.loads(x) for x in f]

    def test_post_logged_in_full(self):
        self.assertEqual(self.req("POST", "/alert/test?token=x", '{"a":1}')[0], 200)
        (e,) = self.lines()
        self.assertEqual(e["method"], "POST")
        self.assertEqual(e["path"], "/alert/test?token=x")
        self.assertEqual(e["body"], '{"a":1}')
        self.assertEqual(e["source_ip"], "127.0.0.1")
        self.assertIn(["Content-Length", "7"], e["headers"])
        self.assertTrue(e["ts"].endswith("+00:00"))

    def test_binary_body_kept(self):
        self.req("PUT", "/x", b"\xff\xfe\x00")
        (e,) = self.lines()
        self.assertTrue(e["body_base64"])
        self.assertEqual(base64.b64decode(e["body"]), b"\xff\xfe\x00")

    def test_fail_prefix_500_and_logged(self):
        self.assertEqual(self.req("POST", "/fail/alert", "{}")[0], 500)
        self.assertEqual(len(self.lines()), 1)

    def test_healthz(self):
        self.assertEqual(self.req("GET", "/healthz")[0], 200)

    def test_state(self):
        status, data = self.req("GET", "/api/state")
        self.assertEqual((status, json.loads(data)), (200, {"alerts": []}))

    def test_log_failure_still_responds(self):
        old, discovery.LOG_PATH = discovery.LOG_PATH, "/nonexistent/dir/x.jsonl"
        try:
            self.assertEqual(self.req("POST", "/a", "{}")[0], 200)
        finally:
            discovery.LOG_PATH = old


if __name__ == "__main__":
    unittest.main()
