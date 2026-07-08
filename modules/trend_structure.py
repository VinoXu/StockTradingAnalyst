"""Peak-trough trend, support/resistance, and market regime heuristics."""

from __future__ import annotations

from typing import Any

import pandas as pd

from modules.data_fetcher import _normalize_symbol, load_quotes
from modules.db import get_connection


def _latest_indicator_rows(symbol: str, limit: int = 2) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT q.close, i.ma20, i.ma60, i.volume_ratio, i.adx14, q.trade_date
            FROM indicators i
            JOIN quotes q ON q.symbol = i.symbol AND q.trade_date = i.trade_date
            WHERE i.symbol = ?
            ORDER BY i.trade_date DESC
            LIMIT ?
            """,
            (symbol, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def _find_swings(df: pd.DataFrame, window: int = 3) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    highs: list[tuple[int, float]] = []
    lows: list[tuple[int, float]] = []
    if len(df) < window * 2 + 1:
        return highs, lows

    h = df["high"].astype(float)
    lo = df["low"].astype(float)
    for i in range(window, len(df) - window):
        if h.iloc[i] == h.iloc[i - window : i + window + 1].max():
            highs.append((i, float(h.iloc[i])))
        if lo.iloc[i] == lo.iloc[i - window : i + window + 1].min():
            lows.append((i, float(lo.iloc[i])))
    return highs, lows


def _classify_trend(highs: list[tuple[int, float]], lows: list[tuple[int, float]]) -> str:
    if len(highs) < 2 or len(lows) < 2:
        return "unknown"
    h1, h2 = highs[-2][1], highs[-1][1]
    l1, l2 = lows[-2][1], lows[-1][1]
    if h2 > h1 and l2 > l1:
        return "uptrend"
    if h2 < h1 and l2 < l1:
        return "downtrend"
    return "sideways"


def _support_resistance(df: pd.DataFrame, lookback: int = 20) -> tuple[float | None, float | None]:
    tail = df.tail(lookback)
    if tail.empty:
        return None, None
    return float(tail["low"].min()), float(tail["high"].max())


def _compute_retracements(
    highs: list[tuple[int, float]],
    lows: list[tuple[int, float]],
    trend: str,
    last_close: float | None,
) -> dict[str, Any]:
    """Percentage retracement levels from the latest completed swing leg."""
    empty: dict[str, Any] = {"levels": {}, "leg": None, "near_level": None}
    if not last_close or len(highs) < 1 or len(lows) < 1:
        return empty

    if trend == "uptrend" and len(lows) >= 1 and len(highs) >= 1:
        leg_low = lows[-1][1]
        leg_high = highs[-1][1]
        if leg_high <= leg_low:
            return empty
        span = leg_high - leg_low
        levels = {
            "33%": leg_high - span * 0.33,
            "38%": leg_high - span * 0.38,
            "50%": leg_high - span * 0.50,
            "62%": leg_high - span * 0.62,
            "66%": leg_high - span * 0.66,
        }
        leg = {"from": leg_low, "to": leg_high, "direction": "up"}
    elif trend == "downtrend" and len(highs) >= 1 and len(lows) >= 1:
        leg_high = highs[-1][1]
        leg_low = lows[-1][1]
        if leg_high <= leg_low:
            return empty
        span = leg_high - leg_low
        levels = {
            "33%": leg_low + span * 0.33,
            "38%": leg_low + span * 0.38,
            "50%": leg_low + span * 0.50,
            "62%": leg_low + span * 0.62,
            "66%": leg_low + span * 0.66,
        }
        leg = {"from": leg_high, "to": leg_low, "direction": "down"}
    else:
        return empty

    near_level = None
    for name, price in levels.items():
        if abs(last_close - price) / price <= 0.015:
            near_level = name
            break

    return {"levels": levels, "leg": leg, "near_level": near_level}


def _classify_regime(
    trend: str,
    rows: list[dict],
    df: pd.DataFrame,
) -> str:
    close = df["close"].astype(float)
    latest = rows[0] if rows else {}
    prev = rows[1] if len(rows) > 1 else {}
    vol_ratio = latest.get("volume_ratio")
    if len(close) >= 2:
        daily_ret = (close.iloc[-1] - close.iloc[-2]) / close.iloc[-2]
        if daily_ret <= -0.05 or (daily_ret <= -0.03 and (vol_ratio or 0) >= 2.0):
            return "panic"

    ma20, ma60 = latest.get("ma20"), latest.get("ma60")
    last_close = latest.get("close")
    if ma20 and ma60:
        if trend == "uptrend" and ma20 > ma60:
            return "bull"
        if trend == "downtrend" and ma20 < ma60:
            prev_close = prev.get("close")
            if last_close and ma20 and last_close < ma20 and prev_close and last_close > prev_close:
                return "weak_rebound"
            return "bear"

    if trend == "sideways":
        return "range"
    if trend == "uptrend":
        return "bull"
    if trend == "downtrend":
        return "bear"
    return "range"


def analyze_trend_structure(symbol: str, lookback: int = 60) -> dict[str, Any]:
    symbol = _normalize_symbol(symbol)
    df = load_quotes(symbol, limit=lookback)
    if df.empty or len(df) < 10:
        return {
            "trend": "unknown",
            "support": None,
            "resistance": None,
            "regime": "unknown",
            "notes": ["K 线不足，无法判断趋势结构"],
            "invalidation": "数据不足 10 根 K 线",
        }

    highs, lows = _find_swings(df)
    trend = _classify_trend(highs, lows)
    support, resistance = _support_resistance(df)
    ind_rows = _latest_indicator_rows(symbol)
    regime = _classify_regime(trend, ind_rows, df)
    latest = ind_rows[0] if ind_rows else {}

    notes: list[str] = []
    trend_cn = {"uptrend": "上升", "downtrend": "下降", "sideways": "横盘", "unknown": "未知"}.get(trend, trend)
    notes.append(f"峰谷分析：{trend_cn}结构")
    if support is not None and resistance is not None:
        notes.append(f"近 {min(20, len(df))} 日支撑 {support:.2f} / 阻力 {resistance:.2f}")
    last_close = latest.get("close")
    if last_close and support and resistance:
        if last_close <= support * 1.02:
            notes.append("价格贴近支撑区")
        elif last_close >= resistance * 0.98:
            notes.append("价格贴近阻力区")

    regime_cn = {
        "bull": "牛市",
        "bear": "熊市",
        "range": "震荡",
        "weak_rebound": "弱反弹",
        "panic": "恐慌",
        "unknown": "未知",
    }.get(regime, regime)
    notes.append(f"市场状态：{regime_cn}")

    adx = latest.get("adx14")
    if adx is not None:
        if adx < 20:
            notes.append(f"ADX {adx:.1f} 偏低，震荡/振荡环境")
        elif adx >= 25:
            notes.append(f"ADX {adx:.1f} 趋势性偏强")

    retrace = _compute_retracements(highs, lows, trend, last_close)
    retrace_levels = retrace.get("levels") or {}
    if retrace_levels:
        lvl_str = " / ".join(f"{k} {v:.2f}" for k, v in retrace_levels.items())
        notes.append(f"回撤参考位：{lvl_str}")
        if retrace.get("near_level"):
            notes.append(f"价格贴近 {retrace['near_level']} 回撤区")

    from modules.market_data import (
        analyze_dow_confirmation,
        analyze_market_breadth,
        analyze_multi_timeframe,
        analyze_relative_strength,
    )

    mtf = analyze_multi_timeframe(symbol, daily_lookback=max(lookback, 120))
    if mtf.get("available"):
        notes.extend(mtf["notes"])

    dow = analyze_dow_confirmation()
    if dow.get("available"):
        notes.append(f"道氏双指数：{dow['state_cn']}")
    else:
        notes.append("道氏双指数：未验证（请 sync_market）")

    breadth = analyze_market_breadth()
    if breadth.get("available"):
        notes.extend(breadth["notes"][:2])
        notes.append("注：涨跌家数≠AD累积线；AD/52周新高新低未验证时不得替代AD背离结论")

    rs = analyze_relative_strength(symbol)
    if rs.get("available"):
        notes.append(rs["note"])

    return {
        "trend": trend,
        "support": support,
        "resistance": resistance,
        "regime": regime,
        "retracements": retrace_levels,
        "retrace_near": retrace.get("near_level"),
        "multi_timeframe": mtf,
        "dow_confirmation": dow,
        "market_breadth": breadth,
        "relative_strength": rs,
        "swing_highs": len(highs),
        "swing_lows": len(lows),
        "notes": notes,
        "invalidation": "除权、消息日导致峰谷失真；短样本趋势不可靠",
    }
