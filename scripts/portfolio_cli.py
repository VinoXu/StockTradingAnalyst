"""Manage portfolio holdings.

Usage:
    python scripts/portfolio_cli.py add 600000 --name 浦发银行 --qty 1000 --cost 10.5
    python scripts/portfolio_cli.py list
    python scripts/portfolio_cli.py remove 600000
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.db import init_db
from modules.portfolio import add_holding, list_holdings, portfolio_summary, remove_holding


def main() -> None:
    init_db()
    parser = argparse.ArgumentParser(description="Portfolio holdings CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="Add or update holding")
    p_add.add_argument("symbol")
    p_add.add_argument("--name", default=None)
    p_add.add_argument("--qty", type=float, default=0)
    p_add.add_argument("--cost", type=float, default=None)
    p_add.add_argument("--buy-date", default=None)
    p_add.add_argument("--notes", default=None)

    p_rm = sub.add_parser("remove", help="Remove holding")
    p_rm.add_argument("symbol")

    sub.add_parser("list", help="List holdings with PnL")

    args = parser.parse_args()

    if args.cmd == "add":
        row = add_holding(
            args.symbol,
            name=args.name,
            quantity=args.qty,
            cost_price=args.cost,
            buy_date=args.buy_date,
            notes=args.notes,
        )
        print(json.dumps(row, ensure_ascii=False, indent=2))
        return

    if args.cmd == "remove":
        ok = remove_holding(args.symbol)
        print("已删除" if ok else "未找到")
        return

    pf = portfolio_summary()
    if not pf["positions"]:
        print("（空组合）使用 add 子命令录入")
        return
    for p in pf["positions"]:
        w = pf["weights_pct"].get(p["symbol"])
        pnl = f"{p['pnl_pct']}%" if p.get("pnl_pct") is not None else "—"
        wtxt = f"  仓位{w}%" if w else ""
        print(
            f"{p['symbol']}  {p.get('name') or ''}  "
            f"qty={p.get('quantity')}  cost={p.get('cost_price')}  "
            f"close={p.get('last_close')}  pnl={pnl}{wtxt}"
        )
    if pf.get("total_market_value"):
        print(f"\n总市值 {pf['total_market_value']}  浮盈 {pf.get('total_pnl', '—')}")


if __name__ == "__main__":
    main()
