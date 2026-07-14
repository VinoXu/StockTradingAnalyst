"""East Money broker research reports for A-share symbols."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import akshare as ak
import pandas as pd

from modules.akshare_client import fetch_with_retry
from modules.data_fetcher import _normalize_symbol

_REPORT_COL_ALIASES: dict[str, tuple[str, ...]] = {
    "code": ("股票代码",),
    "name": ("股票简称",),
    "title": ("报告名称",),
    "report_type": ("报告类型",),
    "org": ("机构",),
    "rating": ("评级",),
    "prev_rating": ("上月投资评级",),
    "industry": ("行业",),
    "publish_date": ("日期",),
    "pdf_url": ("报告PDF链接", "研报PDF链接"),
}


def _pick_col(df: pd.DataFrame, key: str) -> str | None:
    for alias in _REPORT_COL_ALIASES.get(key, ()):
        if alias in df.columns:
            return alias
    return None


def _cell(row: pd.Series, col: str | None) -> Any:
    if not col:
        return None
    val = row.get(col)
    if pd.isna(val):
        return None
    return val


def _parse_date(val: Any) -> datetime | None:
    if val is None:
        return None
    s = str(val).strip()[:10]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _rating_bucket(rating: Any) -> str:
    s = str(rating or "").strip()
    if not s:
        return "unknown"
    if any(k in s for k in ("买入", "增持", "推荐", "强推", "优于大市")):
        return "bullish"
    if any(k in s for k in ("卖出", "减持", "回避")):
        return "bearish"
    if any(k in s for k in ("中性", "持有", "观望")):
        return "neutral"
    return "other"


def fetch_symbol_research_reports(
    symbol: str,
    *,
    max_reports: int = 12,
    days: int = 180,
) -> dict[str, Any]:
    """Fetch and aggregate broker research for one A-share code."""
    code = _normalize_symbol(symbol).split(".")[0]
    try:
        raw = fetch_with_retry(ak.stock_research_report_em, symbol=code)
    except Exception as exc:  # noqa: BLE001
        return {
            "symbol": code,
            "available": False,
            "error": str(exc),
        }

    if raw is None or raw.empty:
        return {"symbol": code, "available": False, "error": "无研报数据"}

    cols = {k: _pick_col(raw, k) for k in _REPORT_COL_ALIASES}
    cutoff = datetime.now() - timedelta(days=days)
    items: list[dict[str, Any]] = []

    for _, row in raw.iterrows():
        pub = _parse_date(_cell(row, cols["publish_date"]))
        if pub and pub < cutoff:
            continue
        rating = _cell(row, cols["rating"])
        items.append(
            {
                "title": str(_cell(row, cols["title"]) or "")[:120],
                "org": str(_cell(row, cols["org"]) or ""),
                "rating": str(rating or ""),
                "rating_bucket": _rating_bucket(rating),
                "report_type": str(_cell(row, cols["report_type"]) or ""),
                "industry": str(_cell(row, cols["industry"]) or ""),
                "publish_date": pub.strftime("%Y-%m-%d") if pub else str(_cell(row, cols["publish_date"]) or ""),
                "pdf_url": str(_cell(row, cols["pdf_url"]) or ""),
            }
        )

    items.sort(key=lambda x: x.get("publish_date") or "", reverse=True)
    items = items[:max_reports]

    buckets = {"bullish": 0, "neutral": 0, "bearish": 0, "other": 0, "unknown": 0}
    orgs: set[str] = set()
    for it in items:
        buckets[it.get("rating_bucket") or "unknown"] = buckets.get(it.get("rating_bucket") or "unknown", 0) + 1
        if it.get("org"):
            orgs.add(it["org"])

    confidence = "A" if len(items) >= 8 else "B" if len(items) >= 3 else "C"

    return {
        "symbol": code,
        "available": bool(items),
        "confidence": confidence,
        "window_days": days,
        "total_in_window": len(items),
        "rating_summary": buckets,
        "org_count": len(orgs),
        "recent_reports": items,
        "consensus_note": _consensus_note(buckets, len(items)),
    }


def _consensus_note(buckets: dict[str, int], total: int) -> str:
    if total <= 0:
        return "窗口期内无研报，共识不可评估"
    bull = buckets.get("bullish", 0)
    bear = buckets.get("bearish", 0)
    neu = buckets.get("neutral", 0)
    if bull >= bear * 2 and bull >= neu:
        return f"近端研报偏乐观（看多{bull}篇/共{total}篇）"
    if bear >= bull and bear > 0:
        return f"近端研报存在分歧或偏谨慎（看空/减持{bear}篇/共{total}篇）"
    return f"近端研报中性居多（中性{neu}篇/共{total}篇）"


def collect_research_for_symbols(symbols: list[str], *, max_symbols: int = 3) -> dict[str, Any]:
    """Batch research report pack for symbol deep-research workflow."""
    out: dict[str, Any] = {"available": False, "symbols": []}
    picked = symbols[:max_symbols]
    rows: list[dict[str, Any]] = []
    for sym in picked:
        row = fetch_symbol_research_reports(sym)
        rows.append(row)
    out["symbols"] = rows
    out["available"] = any(r.get("available") for r in rows)
    return out
