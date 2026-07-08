"""Multi-bar price structure heuristics (double top/bottom, gaps, wedges)."""

from __future__ import annotations

from typing import Any

import pandas as pd

from modules.data_fetcher import _normalize_symbol, load_quotes


def _near(a: float, b: float, pct: float = 0.02) -> bool:
    if b == 0:
        return False
    return abs(a - b) / abs(b) <= pct


def _detect_gaps(df: pd.DataFrame) -> list[tuple[str, str]]:
    """Return list of (gap_type_hint, note)."""
    found: list[tuple[str, str]] = []
    if len(df) < 10:
        return found

    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    volume = df["volume"].astype(float)
    ret_30 = (float(close.iloc[-1]) - float(close.iloc[-30])) / float(close.iloc[-30]) if len(close) >= 30 else 0

    for i in range(max(1, len(df) - 15), len(df)):
        prev_h, prev_l = float(high.iloc[i - 1]), float(low.iloc[i - 1])
        cur_h, cur_l = float(high.iloc[i]), float(low.iloc[i])
        if cur_l > prev_h:
            gap_pct = (cur_l - prev_h) / prev_h
            vol_ratio = float(volume.iloc[i]) / float(volume.iloc[i - 5 : i].mean()) if i >= 5 else 1.0
            if abs(ret_30) > 0.08 and vol_ratio >= 1.3:
                found.append(("breakaway_gap_up", f"向上突破跳空 {gap_pct:.1%}（近段趋势+放量）"))
            elif abs(ret_30) > 0.15:
                found.append(("exhaustion_gap_up", "向上衰竭跳空（趋势末段，警惕回补）"))
            else:
                found.append(("runaway_gap_up", "向上中继跳空"))
        elif cur_h < prev_l:
            gap_pct = (prev_l - cur_h) / prev_l
            vol_ratio = float(volume.iloc[i]) / float(volume.iloc[i - 5 : i].mean()) if i >= 5 else 1.0
            if abs(ret_30) > 0.08 and vol_ratio >= 1.3:
                found.append(("breakaway_gap_down", f"向下跌破跳空 {gap_pct:.1%}"))
            elif abs(ret_30) > 0.15:
                found.append(("exhaustion_gap_down", "向下衰竭跳空"))
            else:
                found.append(("runaway_gap_down", "向下中继跳空"))
    return found[-2:]


def _detect_wedge_flag(df: pd.DataFrame) -> list[str]:
    hints: list[str] = []
    if len(df) < 25:
        return hints

    tail = df.tail(20)
    highs = tail["high"].astype(float)
    lows = tail["low"].astype(float)
    x = range(len(tail))
    if len(x) < 10:
        return hints

    hi_slope = (float(highs.iloc[-1]) - float(highs.iloc[0])) / len(tail)
    lo_slope = (float(lows.iloc[-1]) - float(lows.iloc[0])) / len(tail)
    span = float(highs.max() - lows.min())
    if span <= 0:
        return hints

    converging = abs(float(highs.iloc[-1] - lows.iloc[-1])) < abs(float(highs.iloc[0] - lows.iloc[0])) * 0.7
    if converging and hi_slope > 0 and lo_slope > 0:
        hints.append("rising_wedge_hint")
    elif converging and hi_slope < 0 and lo_slope < 0:
        hints.append("falling_wedge_hint")

    prior = df.iloc[-25:-20]
    recent = df.tail(5)
    if len(prior) >= 5:
        prior_move = float(prior["close"].iloc[-1] - prior["close"].iloc[0]) / float(prior["close"].iloc[0])
        recent_range = (float(recent["high"].max()) - float(recent["low"].min())) / float(recent["close"].mean())
        if prior_move > 0.05 and recent_range < 0.04:
            hints.append("bull_flag_hint")
        elif prior_move < -0.05 and recent_range < 0.04:
            hints.append("bear_flag_hint")

    return hints


def _detect_head_shoulders(high: pd.Series, low: pd.Series, close: pd.Series) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    if len(high) < 40:
        return found

    tail_h = high.iloc[-50:].reset_index(drop=True)
    tail_l = low.iloc[-50:].reset_index(drop=True)
    peaks: list[tuple[int, float]] = []
    troughs: list[tuple[int, float]] = []

    for i in range(2, len(tail_h) - 2):
        hv = float(tail_h.iloc[i])
        lv = float(tail_l.iloc[i])
        if hv >= float(tail_h.iloc[i - 1]) and hv >= float(tail_h.iloc[i + 1]):
            if hv >= float(tail_h.iloc[i - 2]) and hv >= float(tail_h.iloc[i + 2]):
                peaks.append((i, hv))
        if lv <= float(tail_l.iloc[i - 1]) and lv <= float(tail_l.iloc[i + 1]):
            troughs.append((i, lv))

    if len(peaks) >= 3:
        left, head, right = peaks[-3], peaks[-2], peaks[-1]
        if head[1] > left[1] * 1.01 and head[1] > right[1] * 1.01 and right[1] < head[1]:
            neck_candidates = [t for t in troughs if left[0] < t[0] < right[0]]
            if neck_candidates:
                neck = sum(t[1] for t in neck_candidates) / len(neck_candidates)
                last = float(close.iloc[-1])
                if last < neck * 0.995:
                    found.append(("head_shoulders_top", "头肩顶：已跌破颈线，偏空"))
                else:
                    found.append(("head_shoulders_top_hint", "头肩顶雏形：左肩-头-右肩，待跌破颈线确认"))

    if len(troughs) >= 3:
        left, head, right = troughs[-3], troughs[-2], troughs[-1]
        if head[1] < left[1] * 0.99 and head[1] < right[1] * 0.99 and right[1] > head[1]:
            neck_candidates = [p for p in peaks if left[0] < p[0] < right[0]]
            if neck_candidates:
                neck = sum(p[1] for p in neck_candidates) / len(neck_candidates)
                last = float(close.iloc[-1])
                if last > neck * 1.005:
                    found.append(("head_shoulders_bottom", "头肩底：已突破颈线，偏多"))
                else:
                    found.append(("head_shoulders_bottom_hint", "头肩底雏形，待突破颈线确认"))

    return found


def _detect_symmetric_triangle(high: pd.Series, low: pd.Series) -> str | None:
    if len(high) < 30:
        return None
    tail = 25
    h = high.iloc[-tail:].astype(float)
    lo = low.iloc[-tail:].astype(float)
    hi_slope = (float(h.iloc[-1]) - float(h.iloc[0])) / tail
    lo_slope = (float(lo.iloc[-1]) - float(lo.iloc[0])) / tail
    span_start = float(h.iloc[0] - lo.iloc[0])
    span_end = float(h.iloc[-1] - lo.iloc[-1])
    if span_start <= 0:
        return None
    converging = span_end < span_start * 0.75
    if converging and hi_slope < 0 and lo_slope > 0:
        return "symmetric_triangle_hint"
    return None


def analyze_price_patterns(symbol: str, lookback: int = 60) -> dict[str, Any]:
    symbol = _normalize_symbol(symbol)
    df = load_quotes(symbol, limit=lookback)
    if df.empty or len(df) < 20:
        return {
            "patterns": [],
            "bias": "neutral",
            "notes": ["K 线不足，无法识别价格结构形态"],
            "invalidation": "数据不足 20 根 K 线",
        }

    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    patterns: list[str] = []
    notes: list[str] = []
    bias = "neutral"

    window = df.tail(30)
    box_high = float(window["high"].max())
    box_low = float(window["low"].min())
    mid = (box_high + box_low) / 2
    box_width = (box_high - box_low) / mid if mid else 0

    if box_width < 0.12:
        patterns.append("rectangle_range")
        notes.append(f"近 30 日箱体震荡，宽约 {box_width:.1%}")

    peaks_idx = high.iloc[-40:].nlargest(3).index
    if len(peaks_idx) >= 2:
        pvals = sorted([float(high.loc[i]) for i in peaks_idx], reverse=True)
        if _near(pvals[0], pvals[1]):
            mid_low = float(low.iloc[-40:].min())
            last_close = float(close.iloc[-1])
            if last_close < mid_low * 0.995:
                patterns.append("double_top")
                notes.append("双顶：已跌破中间低点（颈线），形态确认偏空")
                bias = "bearish"
            else:
                patterns.append("double_top_hint")
                notes.append("双顶雏形：两顶相近，须跌破中间低点才确认（触前高回落≠双顶）")
                bias = "weak_bearish"

    troughs_idx = low.iloc[-40:].nsmallest(3).index
    if len(troughs_idx) >= 2:
        tvals = sorted([float(low.loc[i]) for i in troughs_idx])
        if _near(tvals[0], tvals[1]):
            mid_high = float(high.iloc[-40:].max())
            last_close = float(close.iloc[-1])
            if last_close > mid_high * 1.005:
                patterns.append("double_bottom")
                notes.append("双底：已突破中间高点，形态确认偏多")
                bias = "bullish" if bias != "bearish" else bias
            else:
                patterns.append("double_bottom_hint")
                notes.append("双底雏形：两底相近，须突破中间高点才确认")
                if bias == "neutral":
                    bias = "weak_bullish"

    for gap_type, gap_note in _detect_gaps(df):
        patterns.append(gap_type)
        notes.append(gap_note)

    wedge_flag = _detect_wedge_flag(df)
    _wf_cn = {
        "rising_wedge_hint": "上升楔形雏形（两线同向上倾且收敛，偏空）",
        "falling_wedge_hint": "下降楔形雏形（偏反弹）",
        "bull_flag_hint": "上升旗形雏形（急涨后窄幅整理）",
        "bear_flag_hint": "下降旗形雏形",
    }
    for wf in wedge_flag:
        patterns.append(wf)
        notes.append(_wf_cn.get(wf, wf))

    for hs_type, hs_note in _detect_head_shoulders(high, low, close):
        patterns.append(hs_type)
        notes.append(hs_note)
        if "top" in hs_type and "hint" not in hs_type:
            bias = "bearish"
        elif "bottom" in hs_type and "hint" not in hs_type:
            bias = "bullish" if bias != "bearish" else bias
        elif "top" in hs_type:
            bias = "weak_bearish" if bias == "neutral" else bias

    tri = _detect_symmetric_triangle(high, low)
    if tri:
        patterns.append(tri)
        notes.append("对称三角雏形：高点降+低点升且收敛，待放量突破方向")

    last_close = float(close.iloc[-1])
    if "rectangle_range" in patterns:
        if last_close >= box_high * 0.98:
            notes.append("价格测试箱体上沿")
        elif last_close <= box_low * 1.02:
            notes.append("价格测试箱体下沿")

    if not notes:
        notes.append("未识别典型中期结构形态")

    return {
        "patterns": patterns,
        "box_high": box_high if "rectangle_range" in patterns else None,
        "box_low": box_low if "rectangle_range" in patterns else None,
        "bias": bias,
        "notes": notes,
        "invalidation": "形态需颈线/边界突破确认；短样本不可靠",
    }
