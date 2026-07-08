"""Load `.env` from project root into os.environ (no extra dependency)."""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
_ENV_LOADED = False


def load_env(env_path: Path | None = None) -> None:
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    path = env_path or PROJECT_ROOT / ".env"
    if not path.is_file():
        _ENV_LOADED = True
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val
    _ENV_LOADED = True
