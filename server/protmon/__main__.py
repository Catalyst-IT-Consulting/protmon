import os
import sys

import uvicorn

from . import config

try:
    config.load(os.environ.get("PROTMON_CONFIG", "/etc/protmon/config.json"))
    config.load_secrets(os.environ)
    config.load_admin_networks(os.environ)
except config.ConfigError as e:
    sys.exit(f"protmon: config error: {e}")

uvicorn.run("protmon.app:app", host="0.0.0.0", port=8080, proxy_headers=False)
