"""Quick price-move news attribution (news-pulse workflow)."""

from __future__ import annotations

import re
from typing import Any

import akshare as ak
import pandas as pd

from modules.akshare_client import fetch_with_retry
from modules.data_fetcher import _normalize_symbol

_NEWS_COL_ALIASES: dict[str, tuple[str, ...]] = {
    "title": ("新闻标题",),
    "summary": ("新闻内容", "内容"),
    "time": ("发布时间",),
    "source": ("文章来源", "来源"),
    "url": ("新闻链接", "链接"),
}


def _pick_col(df: pd.DataFrame, key: str) -> str | None:
    for alias in _NEWS_COL_ALIASES.get(key, ()):
        if alias in df.columns:
            return alias
    return None


def _fetch_stock_news(code: str, *, limit: int = 12) -> list[dict[str, Any]]:
    try:
        df = fetch_with_retry(ak.stock_news_em, symbol=code)
    except Exception:  # noqa: BLE001
        try:
            df = fetch_with_retry(ak.stock_news_em, symbol=f"{code}")
        except Exception as exc:  # noqa: BLE001
            return [{"error": str(exc)}]
    if df is None or df.empty:
        return []
    cols = {k: _pick_col(df, k) for k in _NEWS_COL_ALIASES}
    items: list[dict[str, Any]] = []
    for _, row in df.head(limit).iterrows():
        title = str(row.get(cols["title"]) or "")[:160] if cols["title"] else ""
        if not title:
            continue
        items.append(
            {
                "title": title,
                "summary": str(row.get(cols["summary"]) or "")[:400] if cols["summary"] else "",
                "time": str(row.get(cols["time"]) or "") if cols["time"] else "",
                "source": str(row.get(cols["source"]) or "") if cols["source"] else "",
                "url": str(row.get(cols["url"]) or "") if cols["url"] else "",
            }
        )
    return items


def _classify_nature(titles: list[str], *, change_pct: float | None) -> str:
    blob = " ".join(titles)
    if any(k in blob for k in ("立案", "退市", "亏损", "减持", "处罚", "诉讼")):
        return "value_event"
    if any(k in blob for k in ("涨停", "跌停", "异动", "资金", "北向", "融资")):
        return "mixed"
    if change_pct is not None and abs(change_pct) >= 5:
        return "sentiment"
    return "unknown"


def collect_news_pulse(
    *,
    symbols: list[str] | None = None,
    sector_keywords: list[str] | None = None,
    change_pct_hint: float | None = None,
) -> dict[str, Any]:
    """10-minute style attribution pack."""
    symbol_rows: list[dict[str, Any]] = []
    for sym in (symbols or [])[:3]:
        code = _normalize_symbol(sym).split(".")[0]
        news = _fetch_stock_news(code)
        titles = [n.get("title") or "" for n in news if n.get("title")]
        symbol_rows.append(
            {
                "symbol": code,
                "available": bool(news),
                "news_count": len(news),
                "recent_news": news[:8],
                "nature_guess": _classify_nature(titles, change_pct=change_pct_hint),
            }
        )

    sector_news: list[dict[str, Any]] = []
    for kw in (sector_keywords or [])[:3]:
        if len(kw) < 2:
            continue
        try:
            df = fetch_with_retry(ak.stock_news_em, symbol=kw)
            if df is not None and not df.empty:
                tcol = _pick_col(df, "title") or df.columns[1]
                sector_news.append(
                    {
                        "keyword": kw,
                        "titles": [str(x)[:120] for x in df[tcol].head(5).tolist()],
                    }
                )
        except Exception:  # noqa: BLE001
            continue

    return {
        "available": bool(symbol_rows) or bool(sector_news),
        "symbols": symbol_rows,
        "sector_news": sector_news,
        "change_pct_hint": change_pct_hint,
        "note": "归因须区分价值事件/情绪波动/真因不明；禁止把所有新闻等同原因。",
    }


def parse_change_pct_from_message(message: str) -> float | None:
    m = re.search(r"([+-]?\d+(?:\.\d+)?)\s*%", message or "")
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None
