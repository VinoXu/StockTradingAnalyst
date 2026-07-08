"""Sync quotes, indicators, and capital flow for one symbol.

Usage:
    python scripts/sync_symbol.py 600000
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.capital_flow import sync_capital_flow
from modules.data_fetcher import sync_symbol


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python scripts/sync_symbol.py <symbol>")

    symbol = sys.argv[1]
    quote_result = sync_symbol(symbol)
    flow_result = sync_capital_flow(symbol)
    print({"quotes": quote_result, "capital_flow": flow_result})


if __name__ == "__main__":
    main()
