"""Diagnose AKShare network connectivity.

Usage:
    python scripts/diagnose_akshare.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.akshare_client import diagnose

if __name__ == "__main__":
    print(json.dumps(diagnose(), ensure_ascii=False, indent=2))
