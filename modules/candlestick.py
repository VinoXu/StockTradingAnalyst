"""Single- and dual-candle pattern detection from OHLC."""

from __future__ import annotations

from typing import Any

import pandas as pd

from modules.data_fetcher import _normalize_symbol, load_quotes

_BULLISH_REVERSAL = {
    "hammer",
    "bullish_engulfing",
    "morning_star",
    "doji_morning_star",
    "piercing",
    "belt_hold_bull",
    "bullish_harami",
    "three_white_soldiers",
    "bullish_counterattack",
}
_BEARISH_REVERSAL = {
    "shooting_star",
    "bearish_engulfing",
    "evening_star",
    "doji_evening_star",
    "dark_cloud",
    "belt_hold_bear",
    "bearish_harami",
    "three_black_crows",
    "bearish_counterattack",
}
_CONTINUATION = {"rising_three_methods", "falling_three_methods"}


def _candle_parts(row: pd.Series) -> dict[str, float]:
    o, h, lo, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
    body = abs(c - o)
    upper = h - max(o, c)
    lower = min(o, c) - lo
    total = h - lo if h > lo else 1e-9
    return {
        "open": o,
        "high": h,
        "low": lo,
        "close": c,
        "body": body,
        "upper": upper,
        "lower": lower,
        "total": total,
        "bullish": c >= o,
    }


def _enrich_bar(row: pd.Series, prev: dict[str, float] | None = None) -> dict[str, Any]:
    """OHLC + Skill 计量字段，供 LLM 对照 nison 判据逐条核对。"""
    p = _candle_parts(row)
    mid = (p["open"] + p["close"]) / 2
    out: dict[str, Any] = {
        "trade_date": str(row.get("trade_date", "")),
        "open": round(p["open"], 4),
        "high": round(p["high"], 4),
        "low": round(p["low"], 4),
        "close": round(p["close"], 4),
        "body": round(p["body"], 4),
        "upper": round(p["upper"], 4),
        "lower": round(p["lower"], 4),
        "range": round(p["total"], 4),
        "mid": round(mid, 4),
        "body_ratio": round(p["body"] / p["total"], 4) if p["total"] else 0.0,
        "color": "阳" if p["bullish"] else "阴",
    }
    if prev is not None:
        out["gap_up"] = p["low"] > prev["high"]
        out["gap_down"] = p["high"] < prev["low"]
    return out


def build_recent_bars(symbol: str, lookback: int = 10) -> list[dict[str, Any]]:
    symbol = _normalize_symbol(symbol)
    df = load_quotes(symbol, limit=max(lookback, 2))
    if df.empty:
        return []
    bars: list[dict[str, Any]] = []
    prev_parts: dict[str, float] | None = None
    for _, row in df.iterrows():
        parts = _candle_parts(row)
        bars.append(_enrich_bar(row, prev_parts))
        prev_parts = parts
    return bars[-lookback:]


def _detect_single(p: dict[str, float]) -> list[str]:
    found: list[str] = []
    body_ratio = p["body"] / p["total"]
    if body_ratio < 0.1:
        found.append("doji")
    if p["lower"] >= 2 * max(p["body"], 1e-9) and p["upper"] <= p["body"]:
        found.append("hammer")
    if p["upper"] >= 2 * max(p["body"], 1e-9) and p["lower"] <= p["body"]:
        found.append("shooting_star")
    if body_ratio > 0.7:
        if p["bullish"] and p["lower"] <= p["body"] * 0.1:
            found.append("belt_hold_bull")
        if not p["bullish"] and p["upper"] <= p["body"] * 0.1:
            found.append("belt_hold_bear")
    return found


def _detect_pair(prev: dict[str, float], curr: dict[str, float]) -> list[str]:
    found: list[str] = []
    if not prev["bullish"] and curr["bullish"]:
        if curr["open"] <= prev["close"] and curr["close"] >= prev["open"]:
            found.append("bullish_engulfing")
        prev_mid = (prev["open"] + prev["close"]) / 2
        if curr["open"] < prev["low"] and curr["close"] > prev_mid:
            found.append("piercing")
    if prev["bullish"] and not curr["bullish"]:
        if curr["open"] >= prev["close"] and curr["close"] <= prev["open"]:
            found.append("bearish_engulfing")
        prev_mid = (prev["open"] + prev["close"]) / 2
        if curr["open"] > prev["high"] and curr["close"] < prev_mid:
            found.append("dark_cloud")

    if curr["body"] < prev["body"] and curr["body"] > 0:
        top = max(curr["open"], curr["close"])
        bot = min(curr["open"], curr["close"])
        if top <= max(prev["open"], prev["close"]) and bot >= min(prev["open"], prev["close"]):
            if prev["bullish"] and not curr["bullish"]:
                found.append("bearish_harami")
            elif not prev["bullish"] and curr["bullish"]:
                found.append("bullish_harami")

    if abs(curr["close"] - prev["close"]) / max(prev["close"], 1e-9) < 0.003:
        if prev["bullish"] and not curr["bullish"]:
            found.append("bearish_counterattack")
        elif not prev["bullish"] and curr["bullish"]:
            found.append("bullish_counterattack")

    return found


def _detect_three(rows: list[dict[str, float]]) -> list[str]:
    if len(rows) < 3:
        return []
    a, b, c = rows[0], rows[1], rows[2]
    found: list[str] = []
    b_is_doji = b["body"] / b["total"] < 0.15
    if not a["bullish"] and c["bullish"] and c["close"] > (a["open"] + a["close"]) / 2:
        if b["body"] < min(a["body"], c["body"]) * 0.5:
            found.append("doji_morning_star" if b_is_doji else "morning_star")
    if a["bullish"] and not c["bullish"] and c["close"] < (a["open"] + a["close"]) / 2:
        if b["body"] < min(a["body"], c["body"]) * 0.5:
            found.append("doji_evening_star" if b_is_doji else "evening_star")

    if not a["bullish"] and not c["bullish"] and not b["bullish"]:
        if c["close"] < b["close"] < a["close"]:
            found.append("three_black_crows")
    if a["bullish"] and c["bullish"] and b["bullish"]:
        if c["close"] > b["close"] > a["close"]:
            found.append("three_white_soldiers")

    return found


def _detect_five(rows: list[dict[str, float]]) -> list[str]:
    if len(rows) < 5:
        return []
    a, b, c, d, e = rows[0], rows[1], rows[2], rows[3], rows[4]
    found: list[str] = []
    if a["bullish"] and e["bullish"] and e["close"] > a["high"]:
        mid_inside = all(r["high"] <= a["high"] and r["low"] >= a["low"] for r in (b, c, d))
        if mid_inside and e["body"] > a["body"] * 0.8:
            found.append("rising_three_methods")
    if not a["bullish"] and not e["bullish"] and e["close"] < a["low"]:
        mid_inside = all(r["high"] <= a["high"] and r["low"] >= a["low"] for r in (b, c, d))
        if mid_inside and e["body"] > a["body"] * 0.8:
            found.append("falling_three_methods")
    return found


_PATTERN_CN = {
    "doji": "十字线",
    "hammer": "锤头线",
    "shooting_star": "射击之星",
    "belt_hold_bull": "看涨捉腰带",
    "belt_hold_bear": "看跌捉腰带",
    "bullish_engulfing": "看涨吞没",
    "bearish_engulfing": "看跌吞没",
    "piercing": "刺透形态",
    "dark_cloud": "乌云盖顶",
    "bullish_harami": "看涨孕线",
    "bearish_harami": "看跌孕线",
    "bullish_counterattack": "看涨反击线",
    "bearish_counterattack": "看跌反击线",
    "morning_star": "启明星",
    "evening_star": "黄昏星",
    "doji_morning_star": "十字启明星",
    "doji_evening_star": "十字黄昏星",
    "three_black_crows": "三只乌鸦",
    "three_white_soldiers": "白色三兵",
    "rising_three_methods": "上升三法",
    "falling_three_methods": "下降三法",
}


def _apply_prep_filter(
    patterns: list[str],
    prep_zone: dict[str, Any] | None,
) -> tuple[list[str], list[str], str | None]:
    if not prep_zone:
        reversal = [p for p in patterns if p in _BULLISH_REVERSAL | _BEARISH_REVERSAL]
        if reversal:
            return patterns, [], "预备区未验证（无指标历史）"
        return patterns, [], None

    accepted: list[str] = []
    rejected: list[str] = []
    for p in patterns:
        if p in _BULLISH_REVERSAL:
            if prep_zone.get("allows_bullish_reversal"):
                accepted.append(p)
            else:
                rejected.append(p)
        elif p in _BEARISH_REVERSAL:
            if prep_zone.get("allows_bearish_reversal"):
                accepted.append(p)
            else:
                rejected.append(p)
        else:
            accepted.append(p)

    reason = None
    if rejected:
        reason = f"反转形态未过预备区（{prep_zone.get('state_cn', '未进预备区')}）"
    return accepted, rejected, reason


def analyze_candlestick(
    symbol: str,
    lookback: int = 10,
    prep_zone: dict[str, Any] | None = None,
) -> dict[str, Any]:
    symbol = _normalize_symbol(symbol)
    df = load_quotes(symbol, limit=max(lookback, 10))
    if df.empty or len(df) < 2:
        return {
            "patterns": [],
            "patterns_raw": [],
            "patterns_rejected": [],
            "prep_filter": prep_zone.get("state_cn") if prep_zone else "未验证",
            "bias": "neutral",
            "notes": ["OHLC 不足，无法识别 K 线形态"],
            "invalidation": "无 K 线数据",
            "bars": [],
        }

    parts = [_candle_parts(row) for _, row in df.iterrows()]
    raw_patterns: list[str] = []
    raw_patterns.extend(_detect_single(parts[-1]))
    if len(parts) >= 2:
        raw_patterns.extend(_detect_pair(parts[-2], parts[-1]))
    if len(parts) >= 3:
        raw_patterns.extend(_detect_three(parts[-3:]))
    if len(parts) >= 5:
        raw_patterns.extend(_detect_five(parts[-5:]))

    raw_patterns = list(dict.fromkeys(raw_patterns))
    patterns, rejected, filter_reason = _apply_prep_filter(raw_patterns, prep_zone)

    bias = "neutral"
    if patterns and _BULLISH_REVERSAL.intersection(patterns):
        bias = "weak_bullish"
    if patterns and _BEARISH_REVERSAL.intersection(patterns):
        bias = "weak_bearish" if bias == "neutral" else bias
    if patterns and _CONTINUATION.intersection(patterns):
        if "rising_three_methods" in patterns:
            bias = "bullish" if bias != "bearish" else bias
        if "falling_three_methods" in patterns:
            bias = "bearish" if bias != "bullish" else bias

    notes: list[str] = []
    if patterns:
        notes.append(f"采信形态：{', '.join(_PATTERN_CN.get(p, p) for p in patterns)}")
    elif raw_patterns:
        notes.append(f"识别但未采信：{', '.join(_PATTERN_CN.get(p, p) for p in raw_patterns)}")
    else:
        notes.append("未识别典型短 K 线形态")
    if rejected and filter_reason:
        notes.append(filter_reason)
    elif not prep_zone and raw_patterns and _BULLISH_REVERSAL.union(_BEARISH_REVERSAL).intersection(raw_patterns):
        notes.append("预备区未验证，反转形态仅供参考")
    else:
        notes.append("需结合 trend_structure 位置与量能")

    return {
        "patterns": patterns,
        "patterns_raw": raw_patterns,
        "patterns_rejected": rejected,
        "patterns_cn": [_PATTERN_CN.get(p, p) for p in patterns],
        "prep_filter": prep_zone.get("state_cn") if prep_zone else "未验证",
        "bias": bias,
        "notes": notes,
        "invalidation": "横盘中部、涨跌停、预备区未通过时反转形态失效",
        "bars": build_recent_bars(symbol, lookback=lookback),
    }
