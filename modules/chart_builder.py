"""Build chart specs for frontend rendering."""

from __future__ import annotations

from typing import Any

from modules.data_fetcher import _normalize_symbol, load_quotes
from modules.ta_analysis import load_indicator_history


def build_charts(symbols: list[str], kinds: list[str] | None = None) -> list[dict[str, Any]]:
    kinds = kinds or ["price"]
    charts: list[dict[str, Any]] = []
    for raw in symbols[:3]:
        sym = _normalize_symbol(raw)
        code = sym.split(".")[0]
        if "price" in kinds:
            c = _price_chart(sym, code)
            if c:
                charts.append(c)
        if "rsi" in kinds:
            c = _rsi_chart(sym, code)
            if c:
                charts.append(c)
    return charts


def _price_chart(symbol: str, code: str) -> dict[str, Any] | None:
    df = load_quotes(symbol, limit=60)
    if df.empty:
        return None
    df = df.sort_values("trade_date")
    return {
        "id": f"{code}_price",
        "type": "line",
        "title": f"{code} 近60日收盘价",
        "labels": df["trade_date"].tolist(),
        "datasets": [
            {
                "label": "收盘",
                "data": [round(float(x), 2) for x in df["close"].tolist()],
                "borderColor": "#165DFF",
                "backgroundColor": "rgba(22,93,255,0.08)",
                "fill": True,
            }
        ],
    }


def _rsi_chart(symbol: str, code: str) -> dict[str, Any] | None:
    hist = load_indicator_history(symbol, lookback=60)
    if not hist:
        return None
    hist = list(reversed(hist))
    labels = [h["trade_date"] for h in hist if h.get("rsi14") is not None]
    values = [round(float(h["rsi14"]), 1) for h in hist if h.get("rsi14") is not None]
    if not labels:
        return None
    return {
        "id": f"{code}_rsi",
        "type": "line",
        "title": f"{code} RSI14",
        "labels": labels,
        "datasets": [
            {
                "label": "RSI14",
                "data": values,
                "borderColor": "#00B42A",
                "backgroundColor": "rgba(0,180,42,0.06)",
                "fill": False,
            }
        ],
        "yMin": 0,
        "yMax": 100,
    }
