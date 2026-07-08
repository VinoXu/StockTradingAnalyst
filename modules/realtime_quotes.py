"""Intraday live quotes — per-symbol fast path, separate from daily bars."""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime
from datetime import time as dtime
from typing import Any

import pandas as pd

from modules.akshare_client import fetch_a_spot_em, fetch_index_spot_em, fetch_intraday_em
from modules.data_fetcher import _normalize_symbol, _symbol_to_ak_code
from modules.portfolio import get_holding
from modules.ta_analysis import load_snapshot

_CACHE_TTL = float(os.environ.get("REALTIME_QUOTE_TTL", "20"))
_USE_FULL_MARKET = os.environ.get("REALTIME_FULL_MARKET", "").strip().lower() in ("1", "true", "yes")
_INDEX_MAP = {
    "INDEX.SH000001": "000001",
    "INDEX.SZ399001": "399001",
    "INDEX.SZ399006": "399006",
}

_lock = threading.Lock()
_symbol_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_spot_ts = 0.0
_spot_by_code: dict[str, dict[str, Any]] = {}
_index_ts = 0.0
_index_by_code: dict[str, dict[str, Any]] = {}


def is_a_share_trading_hours(now: datetime | None = None) -> bool:
    now = now or datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.time()
    return (dtime(9, 15) <= t <= dtime(11, 30)) or (dtime(13, 0) <= t <= dtime(15, 5))


def _session_label(now: datetime | None = None) -> str:
    return "trading" if is_a_share_trading_hours(now) else "closed"


def _num(value: Any) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_of_label(quote_time: str, session: str) -> str:
    if session == "trading":
        return f"{quote_time} 盘中"
    return f"{datetime.now().strftime('%Y-%m-%d')} 最新价 {quote_time}"


def _spot_row_to_quote(row: pd.Series, *, fetched_at: str, kind: str) -> dict[str, Any]:
    price = _num(row.get("最新价"))
    prev_close = _num(row.get("昨收"))
    change_pct = _num(row.get("涨跌幅"))
    change_amount = _num(row.get("涨跌额"))
    if change_amount is None and price is not None and prev_close is not None:
        change_amount = round(price - prev_close, 4)
    session = _session_label()
    return {
        "available": price is not None,
        "kind": kind,
        "code": str(row.get("代码") or ""),
        "name": row.get("名称"),
        "price": price,
        "change_pct": change_pct,
        "change_amount": change_amount,
        "open": _num(row.get("今开")),
        "high": _num(row.get("最高")),
        "low": _num(row.get("最低")),
        "prev_close": prev_close,
        "volume": _num(row.get("成交量")),
        "amount": _num(row.get("成交额")),
        "turnover_rate": _num(row.get("换手率")),
        "quote_time": fetched_at,
        "as_of_label": _as_of_label(fetched_at, session),
        "granularity": "intraday",
        "session": session,
        "source": "akshare_spot_em",
    }


def _intraday_df_to_quote(symbol: str, df: pd.DataFrame, *, fetched_at: str) -> dict[str, Any] | None:
    if df is None or df.empty:
        return None
    price_col = "成交价" if "成交价" in df.columns else None
    time_col = "时间" if "时间" in df.columns else None
    if not price_col:
        return None

    prices = df[price_col].map(_num).dropna()
    if prices.empty:
        return None

    price = float(prices.iloc[-1])
    tick = str(df.iloc[-1][time_col]) if time_col else fetched_at.split(" ")[-1]
    quote_time = f"{datetime.now().strftime('%Y-%m-%d')} {tick}"

    snap = load_snapshot(symbol)
    prev_close = None
    if snap and snap.prev:
        prev_close = _num(snap.prev.get("close"))
    if prev_close is None and snap:
        prev_close = _num(snap.close)

    change_amount = round(price - prev_close, 4) if prev_close is not None else None
    change_pct = round(change_amount / prev_close * 100, 2) if change_amount is not None and prev_close else None
    session = _session_label()
    code = _symbol_to_ak_code(symbol)
    holding = get_holding(_normalize_symbol(symbol))
    stock_name = (holding or {}).get("name")

    return {
        "available": True,
        "kind": "stock",
        "code": code,
        "name": stock_name,
        "price": price,
        "change_pct": change_pct,
        "change_amount": change_amount,
        "open": float(prices.iloc[0]),
        "high": float(prices.max()),
        "low": float(prices.min()),
        "prev_close": prev_close,
        "volume": None,
        "amount": None,
        "turnover_rate": None,
        "quote_time": quote_time,
        "as_of_label": _as_of_label(quote_time, session),
        "granularity": "intraday",
        "session": session,
        "source": "akshare_intraday_em",
    }


def _fetch_stock_live(symbol: str, *, force_refresh: bool = False) -> dict[str, Any] | None:
    sym = _normalize_symbol(symbol)
    code = _symbol_to_ak_code(sym)
    now = time.time()
    with _lock:
        cached = _symbol_cache.get(code)
        if not force_refresh and cached and now - cached[0] < _CACHE_TTL:
            return cached[1]

    fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        df = fetch_intraday_em(code)
        quote = _intraday_df_to_quote(sym, df, fetched_at=fetched_at)
        if quote:
            with _lock:
                _symbol_cache[code] = (time.time(), quote)
        return quote
    except Exception:
        return None


def _refresh_spot_cache(force: bool = False) -> dict[str, dict[str, Any]]:
    """Full-market spot — slow; only when REALTIME_FULL_MARKET=1."""
    global _spot_ts, _spot_by_code
    now = time.time()
    with _lock:
        if not force and _spot_by_code and now - _spot_ts < _CACHE_TTL:
            return _spot_by_code

    fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    df = fetch_a_spot_em()
    by_code: dict[str, dict[str, Any]] = {}
    for _, row in df.iterrows():
        code = str(row.get("代码") or "").zfill(6)
        by_code[code] = _spot_row_to_quote(row, fetched_at=fetched_at, kind="stock")

    with _lock:
        _spot_by_code = by_code
        _spot_ts = time.time()
    return by_code


def _refresh_index_cache(force: bool = False) -> dict[str, dict[str, Any]]:
    global _index_ts, _index_by_code
    now = time.time()
    with _lock:
        if not force and _index_by_code and now - _index_ts < _CACHE_TTL:
            return _index_by_code

    fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    df = fetch_index_spot_em()
    by_code: dict[str, dict[str, Any]] = {}
    for _, row in df.iterrows():
        code = str(row.get("代码") or "")
        by_code[code] = _spot_row_to_quote(row, fetched_at=fetched_at, kind="index")

    with _lock:
        _index_by_code = by_code
        _index_ts = time.time()
    return by_code


def get_live_quote(symbol: str, *, force_refresh: bool = False) -> dict[str, Any] | None:
    sym = _normalize_symbol(symbol)
    try:
        if sym.startswith("INDEX."):
            index_code = _INDEX_MAP.get(sym)
            if not index_code:
                return None
            cache = _refresh_index_cache(force=force_refresh)
            return cache.get(index_code)
        if _USE_FULL_MARKET:
            cache = _refresh_spot_cache(force=force_refresh)
            return cache.get(_symbol_to_ak_code(sym))
        return _fetch_stock_live(sym, force_refresh=force_refresh)
    except Exception:
        return None


def get_live_quotes(symbols: list[str], *, force_refresh: bool = False) -> dict[str, dict[str, Any] | None]:
    if not symbols:
        return {}

    out: dict[str, dict[str, Any] | None] = {}
    index_cache: dict[str, dict[str, Any]] | None = None
    stock_syms: list[str] = []

    for raw in symbols:
        sym = _normalize_symbol(raw)
        if sym.startswith("INDEX."):
            if index_cache is None:
                try:
                    index_cache = _refresh_index_cache(force=force_refresh)
                except Exception:
                    index_cache = {}
            index_code = _INDEX_MAP.get(sym)
            out[sym] = index_cache.get(index_code) if index_code else None
            continue

        if _USE_FULL_MARKET:
            try:
                stock_cache = _refresh_spot_cache(force=force_refresh)
            except Exception:
                stock_cache = {}
            out[sym] = stock_cache.get(_symbol_to_ak_code(sym))
        else:
            stock_syms.append(sym)

    if stock_syms:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        with ThreadPoolExecutor(max_workers=min(4, len(stock_syms))) as pool:
            futures = {
                pool.submit(_fetch_stock_live, sym, force_refresh=force_refresh): sym
                for sym in stock_syms
            }
            for fut in as_completed(futures):
                sym = futures[fut]
                try:
                    out[sym] = fut.result()
                except Exception:
                    out[sym] = None

    return out


def attach_live_to_symbol_payload(
    payload: dict[str, Any],
    symbol: str,
    *,
    live: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sym = _normalize_symbol(symbol)
    quote = live if live is not None else get_live_quote(sym)
    if not quote or not quote.get("available"):
        payload["price_source"] = "daily_close"
        return payload

    payload["price_source"] = "intraday"
    payload["live_quote"] = quote
    payload["display_price"] = quote.get("price")
    payload["display_change_pct"] = quote.get("change_pct")
    payload["price_as_of_label"] = quote.get("as_of_label")
    return payload
