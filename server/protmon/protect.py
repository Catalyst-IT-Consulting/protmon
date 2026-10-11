"""Protect console reachability check: proves the console answers HTTPS, nothing more."""
import asyncio
import ssl
import urllib.error
import urllib.request

from .state import PROTECT_KEY, SYSTEM_DURATION

TIMEOUT = 5
ENTRY = {"text": "Protect unreachable", "priority": 1, "camera": None, "duration": SYSTEM_DURATION}


def probe(address, timeout=TIMEOUT):
    """Return None if the console answered, else an error string. Any HTTP status counts as up."""
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE  # self-signed console cert
    try:
        urllib.request.urlopen(f"https://{address}/", timeout=timeout, context=ctx).close()
    except urllib.error.HTTPError:
        pass
    except Exception as e:  # connection, TLS and timeout errors, malformed replies
        return str(getattr(e, "reason", e))


class Watch:
    def __init__(self, state, log):
        self.state, self.log, self.fails, self.down = state, log, 0, False

    def record(self, address, error, threshold):
        if error is None:
            self.fails, self.down = 0, False
            if self.state.clear(PROTECT_KEY):
                self.log.write("protect-restored", address=address)
            return
        self.fails += 1
        if self.fails < threshold:
            return
        if not self.down:
            self.down = True
            self.log.write("protect-unreachable", address=address, error=error, failures=self.fails)
        self.state.trigger(PROTECT_KEY, ENTRY, "local")  # refresh so it outlasts a long outage


async def watch(app):
    w = Watch(app.state.alerts, app.state.log)
    while True:
        p = app.state.config["protect"]  # re-read each loop so a reload applies
        if p:
            err = await asyncio.to_thread(probe, p["address"], min(TIMEOUT, p["interval"]))
            w.record(p["address"], err, p["failures"])
        await asyncio.sleep(p["interval"] if p else 30)
