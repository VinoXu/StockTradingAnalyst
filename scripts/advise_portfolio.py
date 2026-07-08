"""Portfolio-level Murphy advice (batch holdings).

Usage:
    python scripts/advise_portfolio.py              # 规则速览
    python scripts/advise_portfolio.py --agent      # LLM 对话体组合建议
    python scripts/advise_portfolio.py --sync       # 先 sync 大盘+全部持仓
    python scripts/advise_portfolio.py --json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.advisor import collect_portfolio_analysis, render_portfolio_brief, synthesize_portfolio_advice
from modules.llm import generate_portfolio_advice, llm_available, llm_setup_hint
from modules.portfolio import list_holdings


def _sync_all() -> None:
    from modules.capital_flow import sync_capital_flow
    from modules.data_fetcher import sync_symbol
    from modules.market_data import sync_indices, sync_market_breadth

    print("同步大盘…")
    print(sync_indices())
    print(sync_market_breadth())
    for h in list_holdings():
        sym = h["symbol"].split(".")[0]
        print(f"同步 {sym}…")
        sync_symbol(sym)
        sync_capital_flow(sym)


def main() -> None:
    if "--sync" in sys.argv:
        _sync_all()

    if "--json" in sys.argv:
        bundle = collect_portfolio_analysis()
        print(
            json.dumps(
                {"portfolio": bundle, "advice": synthesize_portfolio_advice(bundle)},
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
        return

    if "--agent" in sys.argv:
        if not llm_available():
            raise SystemExit("LLM 未就绪。请在 .env 配置 DASHSCOPE_API_KEY。")
        try:
            print(generate_portfolio_advice())
        except RuntimeError as exc:
            raise SystemExit(f"{exc}\n当前：{llm_setup_hint()}") from exc
        return

    print(render_portfolio_brief())


if __name__ == "__main__":
    main()
