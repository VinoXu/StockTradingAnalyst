"""A-share capital flow data via AKShare."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import akshare as ak
import pandas as pd

from modules.akshare_client import fetch_with_retry
from modules.data_fetcher import _normalize_symbol, _symbol_to_ak_code
from modules.db import get_connection, init_db


def UTC_NOW():
    return datetime.now(timezone.utc).isoformat()


def fetch_stock_fund_flow(symbol: str) -> pd.DataFrame:
    """Individual stock fund flow history (East Money algorithm)."""
    code = _symbol_to_ak_code(symbol)
    market = "sh" if code.startswith(("6", "5", "9")) else "sz"
    raw = fetch_with_retry(ak.stock_individual_fund_flow, stock=code, market=market)
    if raw is None or raw.empty:
        return pd.DataFrame()

    df = raw.rename(
        columns={
            "日期": "trade_date",
            "主力净流入-净额": "main_net_inflow",
            "超大单净流入-净额": "super_large_net",
            "大单净流入-净额": "large_net",
            "中单净流入-净额": "medium_net",
            "小单净流入-净额": "small_net",
        }
    )
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d")
    df["symbol"] = _normalize_symbol(symbol)
    return df[
        [
            "symbol",
            "trade_date",
            "main_net_inflow",
            "super_large_net",
            "large_net",
            "medium_net",
            "small_net",
        ]
    ]


def save_capital_flow(df: pd.DataFrame) -> int:
    if df.empty:
        return 0

    now = UTC_NOW()
    rows = [
        (
            row.symbol,
            row.trade_date,
            row.main_net_inflow,
            row.super_large_net,
            row.large_net,
            row.medium_net,
            row.small_net,
            None,
            None,
            None,
            None,
            0,
            "akshare",
            now,
        )
        for row in df.itertuples(index=False)
    ]
    with get_connection() as conn:
        conn.executemany(
            """
            INSERT INTO capital_flow (
                symbol, trade_date, main_net_inflow, super_large_net, large_net,
                medium_net, small_net, northbound_hold_shares, northbound_hold_ratio,
                margin_balance, short_balance, on_lhb, source, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, trade_date) DO UPDATE SET
                main_net_inflow=excluded.main_net_inflow,
                super_large_net=excluded.super_large_net,
                large_net=excluded.large_net,
                medium_net=excluded.medium_net,
                small_net=excluded.small_net,
                source=excluded.source,
                updated_at=excluded.updated_at
            """,
            rows,
        )
        conn.commit()
    return len(rows)


def sync_capital_flow(symbol: str) -> dict[str, Any]:
    init_db()
    try:
        df = fetch_stock_fund_flow(symbol)
    except Exception as exc:  # noqa: BLE001
        return {"symbol": _normalize_symbol(symbol), "rows": 0, "status": "failed", "error": str(exc)}
    if df.empty:
        return {"symbol": _normalize_symbol(symbol), "rows": 0, "status": "failed"}
    count = save_capital_flow(df)
    extras: dict[str, Any] = {}
    for name, fn in (
        ("northbound", lambda: enrich_northbound(symbol)),
        ("margin", lambda: enrich_margin(symbol)),
        ("lhb", lambda: enrich_lhb_flag(symbol)),
    ):
        try:
            extras[name] = fn()
        except Exception as exc:  # noqa: BLE001
            extras[name] = {"status": "failed", "error": str(exc)}
    return {"symbol": _normalize_symbol(symbol), "rows": count, "status": "ok", "extras": extras}


def enrich_northbound(symbol: str) -> dict[str, Any]:
    """Attach northbound holding series for the symbol."""
    code = _symbol_to_ak_code(symbol)
    raw = fetch_with_retry(ak.stock_hsgt_individual_em, symbol=code)
    if raw is None or raw.empty:
        return {"status": "empty"}
    df = raw.rename(columns={"日期": "trade_date", "持股数量": "northbound_hold_shares", "持股市值占流通市值比": "northbound_hold_ratio"})
    if "trade_date" not in df.columns:
        return {"status": "empty"}
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d")
    now = UTC_NOW()
    with get_connection() as conn:
        for row in df.tail(30).itertuples(index=False):
            conn.execute(
                """
                UPDATE capital_flow SET
                    northbound_hold_shares = ?,
                    northbound_hold_ratio = ?,
                    updated_at = ?
                WHERE symbol = ? AND trade_date = ?
                """,
                (
                    getattr(row, "northbound_hold_shares", None),
                    getattr(row, "northbound_hold_ratio", None),
                    now,
                    _normalize_symbol(symbol),
                    row.trade_date,
                ),
            )
        conn.commit()
    return {"status": "ok", "rows": min(len(df), 30)}


def enrich_margin(symbol: str) -> dict[str, Any]:
    """Attach margin balance from exchange detail tables when available."""
    code = _symbol_to_ak_code(symbol)
    date = datetime.now().strftime("%Y%m%d")
    try:
        if code.startswith(("6", "5", "9")):
            raw = fetch_with_retry(ak.stock_margin_detail_sse, date=date)
        else:
            raw = fetch_with_retry(ak.stock_margin_detail_szse, date=date)
    except Exception:
        return {"status": "skipped", "reason": "margin detail unavailable for date"}
    if raw is None or raw.empty:
        return {"status": "empty"}
    code_col = "标的证券代码" if "标的证券代码" in raw.columns else raw.columns[0]
    row = raw[raw[code_col].astype(str).str.zfill(6) == code]
    if row.empty:
        return {"status": "not_in_list"}
    latest = row.iloc[0]
    margin_col = "融资余额" if "融资余额" in raw.columns else None
    short_col = "融券余额" if "融券余额" in raw.columns else None
    with get_connection() as conn:
        last = conn.execute(
            "SELECT trade_date FROM capital_flow WHERE symbol = ? ORDER BY trade_date DESC LIMIT 1",
            (_normalize_symbol(symbol),),
        ).fetchone()
        if last and margin_col:
            conn.execute(
                """
                UPDATE capital_flow SET margin_balance = ?, short_balance = ?, updated_at = ?
                WHERE symbol = ? AND trade_date = ?
                """,
                (
                    float(latest[margin_col]),
                    float(latest[short_col]) if short_col else None,
                    UTC_NOW(),
                    _normalize_symbol(symbol),
                    last["trade_date"],
                ),
            )
            conn.commit()
    return {"status": "ok"}


def enrich_lhb_flag(symbol: str) -> dict[str, Any]:
    """Flag if symbol appeared on latest dragon-tiger list."""
    code = _symbol_to_ak_code(symbol)
    date = datetime.now().strftime("%Y%m%d")
    raw = fetch_with_retry(ak.stock_lhb_detail_daily_sina, date=date)
    if raw is None or raw.empty:
        return {"status": "empty"}
    code_col = "股票代码" if "股票代码" in raw.columns else raw.columns[0]
    on_lhb = int(code in raw[code_col].astype(str).str.zfill(6).tolist())
    if not on_lhb:
        return {"status": "not_listed"}
    with get_connection() as conn:
        last = conn.execute(
            "SELECT trade_date FROM capital_flow WHERE symbol = ? ORDER BY trade_date DESC LIMIT 1",
            (_normalize_symbol(symbol),),
        ).fetchone()
        if last:
            conn.execute(
                "UPDATE capital_flow SET on_lhb = ?, updated_at = ? WHERE symbol = ? AND trade_date = ?",
                (1, UTC_NOW(), _normalize_symbol(symbol), last["trade_date"]),
            )
            conn.commit()
    return {"status": "ok", "on_lhb": True}


def consecutive_net_inflow_days(symbol: str) -> int:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT main_net_inflow FROM capital_flow
            WHERE symbol = ?
            ORDER BY trade_date DESC
            LIMIT 30
            """,
            (_normalize_symbol(symbol),),
        ).fetchall()
    streak = 0
    for row in rows:
        if row["main_net_inflow"] is None or row["main_net_inflow"] <= 0:
            break
        streak += 1
    return streak
