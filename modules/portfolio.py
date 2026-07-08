"""Portfolio holdings CRUD and position summary."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from modules.data_fetcher import _normalize_symbol
from modules.db import get_connection, init_db
from modules.ta_analysis import load_snapshot


def UTC_NOW():
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row) -> dict[str, Any]:
    return dict(row) if row else {}


def add_holding(
    symbol: str,
    *,
    name: str | None = None,
    quantity: float = 0,
    cost_price: float | None = None,
    buy_date: str | None = None,
    asset_type: str = "stock",
    notes: str | None = None,
) -> dict[str, Any]:
    init_db()
    sym = _normalize_symbol(symbol)
    now = UTC_NOW()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO holdings (symbol, name, quantity, cost_price, buy_date, asset_type, notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                name=COALESCE(excluded.name, holdings.name),
                quantity=excluded.quantity,
                cost_price=COALESCE(excluded.cost_price, holdings.cost_price),
                buy_date=COALESCE(excluded.buy_date, holdings.buy_date),
                asset_type=excluded.asset_type,
                notes=COALESCE(excluded.notes, holdings.notes),
                updated_at=excluded.updated_at
            """,
            (sym, name, quantity, cost_price, buy_date, asset_type, notes, now, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM holdings WHERE symbol = ?", (sym,)).fetchone()
    return _row_to_dict(row)


def remove_holding(symbol: str) -> bool:
    init_db()
    sym = _normalize_symbol(symbol)
    with get_connection() as conn:
        cur = conn.execute("DELETE FROM holdings WHERE symbol = ?", (sym,))
        conn.commit()
        return cur.rowcount > 0


def list_holdings() -> list[dict[str, Any]]:
    init_db()
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM holdings ORDER BY symbol").fetchall()
    return [_row_to_dict(r) for r in rows]


def get_holding(symbol: str) -> dict[str, Any] | None:
    init_db()
    sym = _normalize_symbol(symbol)
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM holdings WHERE symbol = ?", (sym,)).fetchone()
    return _row_to_dict(row) if row else None


def ensure_holding_name(symbol: str) -> str | None:
    """Return stored name or resolve from market and persist."""
    from modules.data_fetcher import _symbol_to_ak_code, resolve_stock_name

    row = get_holding(symbol) or {}
    existing = (row.get("name") or "").strip()
    code = _symbol_to_ak_code(symbol)
    if existing and existing != code:
        return existing
    resolved = resolve_stock_name(symbol)
    if not resolved:
        return None
    qty = float(row.get("quantity") or 0)
    add_holding(symbol, name=resolved, quantity=qty)
    return resolved


def position_summary(holding: dict[str, Any]) -> dict[str, Any]:
    """Attach latest price and PnL to one holding row."""
    resolved_name = ensure_holding_name(holding["symbol"])
    if resolved_name:
        holding = {**holding, "name": resolved_name}
    snap = load_snapshot(holding["symbol"])
    qty = float(holding.get("quantity") or 0)
    cost = holding.get("cost_price")
    close = snap.close if snap else None

    market_value = round(qty * close, 2) if close is not None and qty else None
    cost_value = round(qty * cost, 2) if cost is not None and qty else None
    pnl = round(market_value - cost_value, 2) if market_value is not None and cost_value is not None else None
    pnl_pct = round((close - cost) / cost * 100, 2) if close and cost else None

    daily_change_pct = None
    if close is not None and snap and snap.prev and snap.prev.get("close"):
        prev_close = snap.prev["close"]
        if prev_close:
            daily_change_pct = round((close - prev_close) / prev_close * 100, 2)

    return {
        **holding,
        "trade_date": snap.trade_date if snap else None,
        "last_close": close,
        "daily_change_pct": daily_change_pct,
        "market_value": market_value,
        "cost_value": cost_value,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "has_quotes": snap is not None,
    }


def portfolio_summary() -> dict[str, Any]:
    holdings = list_holdings()
    positions = [position_summary(h) for h in holdings]
    total_mv = sum(p["market_value"] or 0 for p in positions)
    total_cost = sum(p["cost_value"] or 0 for p in positions if p.get("cost_value"))
    total_pnl = round(total_mv - total_cost, 2) if total_cost else None

    weights: dict[str, float] = {}
    if total_mv > 0:
        for p in positions:
            mv = p.get("market_value") or 0
            if mv:
                weights[p["symbol"]] = round(mv / total_mv * 100, 1)

    return {
        "count": len(positions),
        "positions": positions,
        "total_market_value": round(total_mv, 2) if total_mv else None,
        "total_cost_value": round(total_cost, 2) if total_cost else None,
        "total_pnl": total_pnl,
        "weights_pct": weights,
    }
