"""Structured A-share fundamentals and valuation (no PDF parsing)."""

from __future__ import annotations

import re
from typing import Any

import akshare as ak
import pandas as pd

from modules.akshare_client import fetch_with_retry
from modules.data_fetcher import _normalize_symbol

_ABSTRACT_ALIASES: dict[str, tuple[str, ...]] = {
    "period": ("报告期",),
    "net_profit": ("净利润",),
    "net_profit_yoy": ("净利润同比增长率",),
    "revenue": ("营业总收入", "营业收入"),
    "revenue_yoy": ("营业总收入同比增长率", "营业收入同比增长率"),
    "gross_margin": ("销售毛利率", "毛利率"),
    "roe": ("净资产收益率",),
    "debt_ratio": ("资产负债率",),
    "eps": ("基本每股收益", "每股收益"),
    "ocf_per_share": ("每股经营现金流",),
}

_VALUE_ALIASES: dict[str, tuple[str, ...]] = {
    "date": ("数据日期", "交易日"),
    "close": ("当日收盘价", "收盘价"),
    "market_cap": ("总市值",),
    "pe_ttm": ("PE(TTM)", "市盈率TTM"),
    "pe_static": ("PE(静)", "市盈率(静)"),
    "pb": ("市净率", "市净率PB"),
    "dividend_yield": ("股息率",),
    "total_shares": ("总股本",),
}

_SINA_PROFIT_ALIASES: dict[str, tuple[str, ...]] = {
    "period": ("报告日", "报告期"),
    "net_profit": ("净利润",),
    "revenue": ("营业收入", "营业总收入"),
}


def _pick_col(df: pd.DataFrame, aliases: dict[str, tuple[str, ...]], key: str) -> str | None:
    for alias in aliases.get(key, ()):
        if alias in df.columns:
            return alias
    return None


def _parse_num(val: Any) -> float | None:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().replace(",", "")
    if s in ("", "False", "—", "-", "nan"):
        return None
    if s.endswith("%"):
        try:
            return float(s[:-1])
        except ValueError:
            return None
    mult = 1.0
    if s.endswith("亿"):
        mult = 1e8
        s = s[:-1]
    elif s.endswith("万"):
        mult = 1e4
        s = s[:-1]
    try:
        return float(s) * mult
    except ValueError:
        return None


def _period_sort_key(val: Any) -> str:
    s = str(val).strip().replace("-", "")[:8]
    digits = re.sub(r"\D", "", s)
    return digits.zfill(8)


def _row_dict(df: pd.DataFrame, row: pd.Series, aliases: dict[str, tuple[str, ...]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in aliases:
        col = _pick_col(df, aliases, key)
        if not col:
            continue
        raw = row.get(col)
        if key in ("period", "date"):
            out[key] = str(raw)[:10] if raw is not None else None
        elif key in ("pe_ttm", "pe_static", "pb", "dividend_yield", "close", "market_cap", "total_shares"):
            out[key] = _parse_num(raw)
        elif key.endswith("_yoy") or key in ("gross_margin", "roe", "debt_ratio"):
            out[key] = _parse_num(raw)
        else:
            out[key] = _parse_num(raw) if _parse_num(raw) is not None else str(raw) if raw is not None else None
    return out


def _fetch_ths_abstract(code: str) -> dict[str, Any]:
    try:
        df = fetch_with_retry(ak.stock_financial_abstract_ths, symbol=code)
    except Exception as exc:  # noqa: BLE001
        return {"source": "ths_abstract", "available": False, "error": str(exc)}
    if df is None or df.empty:
        return {"source": "ths_abstract", "available": False, "error": "无财务摘要"}
    pcol = _pick_col(df, _ABSTRACT_ALIASES, "period") or df.columns[0]
    df = df.copy()
    df["_sort"] = df[pcol].map(_period_sort_key)
    latest = df.sort_values("_sort").iloc[-1]
    prev = df.sort_values("_sort").iloc[-2] if len(df) >= 2 else None
    latest_row = _row_dict(df, latest, _ABSTRACT_ALIASES)
    if prev is not None:
        prev_row = _row_dict(df, prev, _ABSTRACT_ALIASES)
        for k in ("net_profit", "revenue"):
            cur = latest_row.get(k)
            old = prev_row.get(k)
            if isinstance(cur, (int, float)) and isinstance(old, (int, float)) and old:
                latest_row[f"{k}_qoq_calc"] = round((cur - old) / abs(old) * 100, 2)
    return {"source": "ths_abstract", "available": True, "latest": latest_row}


def _fetch_value_snapshot(code: str) -> dict[str, Any]:
    try:
        df = fetch_with_retry(ak.stock_value_em, symbol=code)
    except Exception as exc:  # noqa: BLE001
        return {"source": "value_em", "available": False, "error": str(exc)}
    if df is None or df.empty:
        return {"source": "value_em", "available": False, "error": "无估值序列"}
    dcol = _pick_col(df, _VALUE_ALIASES, "date") or df.columns[0]
    work = df.copy()
    work["_sort"] = pd.to_datetime(work[dcol], errors="coerce")
    work = work.dropna(subset=["_sort"])
    if work.empty:
        row = df.iloc[-1]
    else:
        row = work.sort_values("_sort").iloc[-1]
    snap = _row_dict(df, row, _VALUE_ALIASES)
    return {"source": "value_em", "available": True, "snapshot": snap}


def _fetch_sina_profit(code: str) -> dict[str, Any]:
    try:
        df = fetch_with_retry(ak.stock_financial_report_sina, stock=code, symbol="利润表")
    except Exception as exc:  # noqa: BLE001
        return {"source": "sina_profit", "available": False, "error": str(exc)}
    if df is None or df.empty:
        return {"source": "sina_profit", "available": False, "error": "无利润表"}
    pcol = _pick_col(df, _SINA_PROFIT_ALIASES, "period") or df.columns[0]
    work = df.copy()
    work["_sort"] = work[pcol].map(_period_sort_key)
    latest = work.sort_values("_sort").iloc[-1]
    latest_row = _row_dict(df, latest, _SINA_PROFIT_ALIASES)
    return {"source": "sina_profit", "available": True, "latest": latest_row}


def fetch_symbol_fundamentals(symbol: str) -> dict[str, Any]:
    """Aggregate structured fundamentals + valuation for one symbol."""
    code = _normalize_symbol(symbol).split(".")[0]
    ths = _fetch_ths_abstract(code)
    value = _fetch_value_snapshot(code)
    sina = _fetch_sina_profit(code)

    sources_ok = sum(1 for s in (ths, value, sina) if s.get("available"))
    confidence = "A" if sources_ok >= 2 else "B" if sources_ok == 1 else "C"

    merged: dict[str, Any] = {
        "symbol": code,
        "available": sources_ok > 0,
        "confidence": confidence,
        "sources_ok": sources_ok,
        "ths_abstract": ths,
        "valuation": value,
        "sina_profit": sina,
    }
    if ths.get("available") and value.get("available"):
        merged["highlights"] = _build_highlights(ths, value, sina)
    return merged


def _build_highlights(
    ths: dict[str, Any],
    value: dict[str, Any],
    sina: dict[str, Any],
) -> dict[str, Any]:
    t = ths.get("latest") or {}
    v = (value.get("snapshot") or {})
    s = (sina.get("latest") or {}) if sina.get("available") else {}
    return {
        "period": t.get("period") or s.get("period"),
        "revenue": t.get("revenue") or s.get("revenue"),
        "revenue_yoy": t.get("revenue_yoy"),
        "net_profit": t.get("net_profit") or s.get("net_profit"),
        "net_profit_yoy": t.get("net_profit_yoy"),
        "gross_margin": t.get("gross_margin"),
        "roe": t.get("roe"),
        "debt_ratio": t.get("debt_ratio"),
        "pe_ttm": v.get("pe_ttm"),
        "pb": v.get("pb"),
        "dividend_yield": v.get("dividend_yield"),
        "market_cap": v.get("market_cap"),
    }


def collect_fundamentals_for_symbols(symbols: list[str], *, max_symbols: int = 3) -> dict[str, Any]:
    from modules.financial_rigor import run_fundamental_checks

    rows: list[dict[str, Any]] = []
    for sym in symbols[:max_symbols]:
        row = fetch_symbol_fundamentals(sym)
        if row.get("available"):
            row["rigor"] = run_fundamental_checks(row)
        rows.append(row)
    return {
        "available": any(r.get("available") for r in rows),
        "symbols": rows,
    }
