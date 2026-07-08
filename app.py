"""Profit Protector — Web UI (FastAPI + static page).

Run: python app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import uvicorn

from modules.api_server import app

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=7860, log_level="info")
