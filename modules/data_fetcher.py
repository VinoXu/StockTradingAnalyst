"""Fetch market data via AKShare and persist to SQLite."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd

from modules.akshare_client import fetch_daily_hist
from modules.db import get_connection, init_db
from modules.indicators import enrich_indicators


def UTC_NOW():
    return datetime.now(timezone.utc).isoformat()


def _normalize_symbol(symbol: str) -> str:
    code = symbol.strip().upper()
    if "." in code:
        return code
    if code.startswith(("6", "5", "9")):
        return f"{code}.SH"
    return f"{code}.SZ"


def _symbol_to_ak_code(symbol: str) -> str:
    return _normalize_symbol(symbol).split(".")[0]


def resolve_stock_name(symbol: str) -> str | None:
    """Fetch A-share short name: Tencent first, East Money as fallback."""
    from modules.akshare_client import fetch_stock_name_tencent, fetch_with_retry

    code = _symbol_to_ak_code(symbol)
    name = fetch_stock_name_tencent(code)
    if name:
        return name

    try:
        import akshare as ak

        df = fetch_with_retry(
            ak.stock_individual_info_em,
            symbol=code,
            max_retry=1,
            base_delay=0.5,
        )
        if df is not None and not df.empty and "item" in df.columns:
            hit = df.loc[df["item"] == "股票简称", "value"]
            if not hit.empty:
                resolved = str(hit.iloc[0]).strip()
                return resolved or None
    except Exception:
        pass
    return None


def fetch_daily_quotes(
    symbol: str,
    start_date: str = "20200101",
    end_date: str | None = None,
    adjust: str = "qfq",
) -> pd.DataFrame:
    """Fetch A-share daily bars. Dates use YYYYMMDD."""
    end_date = end_date or datetime.now().strftime("%Y%m%d")
    code = _symbol_to_ak_code(symbol)
    raw = fetch_daily_hist(code, start_date=start_date, end_date=end_date, adjust=adjust)
    if raw is None or raw.empty:
        return pd.DataFrame()

    df = raw.rename(
        columns={
            "日期": "trade_date",
            "开盘": "open",
            "收盘": "close",
            "最高": "high",
            "最低": "low",
            "成交量": "volume",
            "成交额": "amount",
            "换手率": "turnover_rate",
        }
    )
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d")
    df["symbol"] = _normalize_symbol(symbol)
    df = df.where(pd.notna(df), None)
    return df[
        [
            "symbol",
            "trade_date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "turnover_rate",
            "amount",
        ]
    ]


def save_quotes(df: pd.DataFrame) -> int:
    if df.empty:
        return 0

    now = UTC_NOW()
    rows = [
        (
            row.symbol,
            row.trade_date,
            row.open,
            row.high,
            row.low,
            row.close,
            row.volume,
            row.turnover_rate,
            row.amount,
            "akshare",
            now,
        )
        for row in df.itertuples(index=False)
    ]
    with get_connection() as conn:
        conn.executemany(
            """
            INSERT INTO quotes (
                symbol, trade_date, open, high, low, close, volume,
                turnover_rate, amount, source, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, trade_date) DO UPDATE SET
                open=excluded.open,
                high=excluded.high,
                low=excluded.low,
                close=excluded.close,
                volume=excluded.volume,
                turnover_rate=excluded.turnover_rate,
                amount=excluded.amount,
                source=excluded.source,
                updated_at=excluded.updated_at
            """,
            rows,
        )
        conn.commit()
    return len(rows)


def save_indicators(df: pd.DataFrame) -> int:
    if df.empty:
        return 0

    now = UTC_NOW()
    indicator_cols = [
        "ma5",
        "ma10",
        "ma20",
        "ma60",
        "ma120",
        "ema12",
        "ema26",
        "macd",
        "macd_signal",
        "macd_hist",
        "rsi14",
        "k",
        "d",
        "j",
        "boll_mid",
        "boll_upper",
        "boll_lower",
        "volume_ratio",
        "cci20",
        "williams_r14",
        "plus_di14",
        "minus_di14",
        "adx14",
    ]
    rows = []
    for row in df.itertuples(index=False):
        values = [getattr(row, col, None) for col in indicator_cols]
        rows.append((row.symbol, row.trade_date, *values, now))

    placeholders = ", ".join(["?"] * (2 + len(indicator_cols) + 1))
    col_list = ", ".join(["symbol", "trade_date", *indicator_cols, "updated_at"])
    update_set = ", ".join(f"{col}=excluded.{col}" for col in indicator_cols + ["updated_at"])

    with get_connection() as conn:
        conn.executemany(
            f"""
            INSERT INTO indicators ({col_list})
            VALUES ({placeholders})
            ON CONFLICT(symbol, trade_date) DO UPDATE SET {update_set}
            """,
            rows,
        )
        conn.commit()
    return len(rows)


def sync_symbol(symbol: str, start_date: str = "20200101") -> dict[str, Any]:
    """Fetch quotes, compute indicators, and persist."""
    init_db()
    try:
        quotes = fetch_daily_quotes(symbol, start_date=start_date)
    except Exception as exc:  # noqa: BLE001
        _log_fetch("sync_symbol", symbol, "failed", str(exc))
        return {"symbol": _normalize_symbol(symbol), "quotes": 0, "indicators": 0, "status": "failed", "error": str(exc)}
    if quotes.empty:
        _log_fetch("sync_symbol", symbol, "failed", "empty quotes")
        return {"symbol": symbol, "quotes": 0, "indicators": 0, "status": "failed"}

    enriched = enrich_indicators(quotes)
    q_count = save_quotes(quotes)
    i_count = save_indicators(enriched)
    _log_fetch("sync_symbol", symbol, "ok", f"quotes={q_count}, indicators={i_count}")
    return {
        "symbol": _normalize_symbol(symbol),
        "quotes": q_count,
        "indicators": i_count,
        "status": "ok",
    }


def refresh_indicators(symbol: str, limit: int = 500) -> int:
    """Recompute indicators from stored quotes (e.g. after schema migration)."""
    init_db()
    quotes = load_quotes(symbol, limit=limit)
    if quotes.empty:
        return 0
    enriched = enrich_indicators(quotes)
    return save_indicators(enriched)


def load_quotes(symbol: str, limit: int = 250) -> pd.DataFrame:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM quotes
            WHERE symbol = ?
            ORDER BY trade_date DESC
            LIMIT ?
            """,
            (_normalize_symbol(symbol), limit),
        ).fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([dict(r) for r in rows])
    return df.sort_values("trade_date")


def _log_fetch(task: str, symbol: str | None, status: str, message: str) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO fetch_log (task, symbol, status, message, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (task, symbol, status, message, UTC_NOW()),
        )
        conn.commit()
