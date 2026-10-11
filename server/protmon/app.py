import subprocess
from pathlib import Path

from fastapi import FastAPI

REPO = Path(__file__).resolve().parents[2]


def _commit() -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5, check=True,
        )
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


COMMIT = _commit()

app = FastAPI(title="Protmon")


@app.get("/healthz")
def healthz():
    return {"status": "ok", "commit": COMMIT}


@app.get("/api/state")
def state():
    return {"alerts": []}
