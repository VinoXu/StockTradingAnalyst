"""Oscillator prep-zone evaluation (Chapter 12 filter chain)."""

from __future__ import annotations

from typing import Any

PREP_LOOKBACK = 20


def _series_stats(values: list[float | None]) -> tuple[float | None, float | None, float | None]:
    clean = [v for v in values if v is not None]
    if not clean:
        return None, None, None
    return min(clean), max(clean), clean[0]


def _bull_prep(min_v: float | None, cur: float | None, low: float) -> bool:
    if cur is not None and cur < low:
        return True
    return min_v is not None and min_v < low


def _bear_prep(max_v: float | None, cur: float | None, high: float) -> bool:
    if cur is not None and cur > high:
        return True
    return max_v is not None and max_v > high


def evaluate_prep_zone(history: list[dict[str, Any]]) -> dict[str, Any]:
    """
    history: newest-first rows with optional d, rsi14, cci20, williams_r14.
    Returns prep zone state for candlestick reversal filtering.
    """
    window = history[:PREP_LOOKBACK]
    d_vals = [r.get("d") for r in window]
    rsi_vals = [r.get("rsi14") for r in window]
    cci_vals = [r.get("cci20") for r in window]
    wr_vals = [r.get("williams_r14") for r in window]

    d_min, d_max, d_cur = _series_stats(d_vals)
    rsi_min, rsi_max, rsi_cur = _series_stats(rsi_vals)
    cci_min, cci_max, cci_cur = _series_stats(cci_vals)
    wr_min, wr_max, wr_cur = _series_stats(wr_vals)

    bull_d = _bull_prep(d_min, d_cur, 20)
    bear_d = _bear_prep(d_max, d_cur, 80)
    bull_rsi = _bull_prep(rsi_min, rsi_cur, 30)
    bear_rsi = _bear_prep(rsi_max, rsi_cur, 70)
    bull_cci = _bull_prep(cci_min, cci_cur, -100)
    bear_cci = _bear_prep(cci_max, cci_cur, 100)
    bull_wr = _bull_prep(wr_min, wr_cur, 20)
    bear_wr = _bear_prep(wr_max, wr_cur, 80)

    bull_prep = bull_d or bull_rsi or bull_cci or bull_wr
    bear_prep = bear_d or bear_rsi or bear_cci or bear_wr

    bull_signal = bull_prep and d_cur is not None and d_cur > 20
    bear_signal = bear_prep and d_cur is not None and d_max is not None and d_max > 80 and d_cur < d_max

    if not bull_prep and not bear_prep:
        state = "outside"
    elif bull_signal:
        state = "bull_signal_formed"
    elif bear_signal:
        state = "bear_signal_formed"
    elif bull_prep and not bear_prep:
        state = "bull_prep"
    elif bear_prep and not bull_prep:
        state = "bear_prep"
    else:
        state = "mixed"

    state_cn = {
        "outside": "未进预备区",
        "bull_prep": "看多预备区",
        "bear_prep": "看空预备区",
        "bull_signal_formed": "看多信号形成",
        "bear_signal_formed": "看空信号形成",
        "mixed": "多空预备区混杂",
    }.get(state, state)

    return {
        "state": state,
        "state_cn": state_cn,
        "lookback_days": len(window),
        "d_min": d_min,
        "d_max": d_max,
        "d_current": d_cur,
        "rsi_min": rsi_min,
        "rsi_max": rsi_max,
        "rsi_current": rsi_cur,
        "cci_min": cci_min,
        "cci_max": cci_max,
        "cci_current": cci_cur,
        "williams_r_min": wr_min,
        "williams_r_max": wr_max,
        "williams_r_current": wr_cur,
        "bull_prep": bull_prep,
        "bear_prep": bear_prep,
        "allows_bullish_reversal": bull_prep,
        "allows_bearish_reversal": bear_prep,
    }
