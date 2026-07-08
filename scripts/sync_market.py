"""Sync market indices and breadth data.

Usage:
    python scripts/sync_market.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.market_data import sync_market


def main() -> None:
    result = sync_market()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
