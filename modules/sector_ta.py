"""Technical scoring for sector picks via lead-stock price structure."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import pandas as pd

from modules.advisor import collect_analysis, synthesize_advice
from modules.akshare_client import resolve_stock_code_by_name
from modules.data_fetcher import _normalize_symbol, fetch_daily_quotes, sync_symbol
from modules.trend_structure import _classify_trend, _find_swings

_REGIME_SCORE = {
    "bull": 2.0,
    "range": 0.3,
    "weak_rebound": -0.8,
    "bear": -2.0,
    "panic": -2.5,
    "unknown": 0.0,
}
_TREND_SCORE = {
    "uptrend": 1.5,
    "sideways": 0.0,
    "downtrend": -1.5,
    "unknown": 0.0,
}
_BIAS_SCORE = {
    "bullish": 1.2,
    "weak_bullish": 0.6,
    "neutral": 0.0,
    "weak_bearish": -0.6,
    "bearish": -1.2,
}
_BULL_PATTERNS = frozenset(
    {
        "double_bottom",
        "double_bottom_hint",
        "bull_flag_hint",
        "falling_wedge_hint",
        "breakaway_gap_up",
        "runaway_gap_up",
    }
)
_BEAR_PATTERNS = frozenset(
    {
        "double_top",
        "double_top_hint",
        "bear_flag_hint",
        "rising_wedge_hint",
        "head_shoulders_top",
        "breakaway_gap_down",
        "exhaustion_gap_up",
    }
)

_TA_CACHE: dict[str, dict[str, Any]] = {}


def _clean_lead_name(name: str) -> str:
    s = (name or "").strip()
    s = re.sub(r"^XD", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^[*＊]", "", s)
    return s.strip()


def _breadth_ratio(row: dict[str, Any]) -> float | None:
    try:
        rising = float(row.get("rising_count") or 0)
        falling = float(row.get("falling_count") or 0)
    except (TypeError, ValueError):
        return None
    total = rising + falling
    if total <= 0:
        return None
    return rising / total


def _daily_change_penalty(change_pct: Any) -> float:
    """Penalize overheated boards; do not reward today's gain for forward picks."""
    try:
        pct = float(change_pct or 0)
    except (TypeError, ValueError):
        return 0.0
    if pct >= 5.0:
        return -0.25 * (pct - 5.0) - 0.5
    if pct >= 3.0:
        return -0.12 * (pct - 3.0)
    if pct <= -4.0:
        return -0.4
    return 0.0


def _lead_stock_anomaly_penalty(row: dict[str, Any]) -> float:
    """Lone wolf lead or listing anomaly should not pull board into scan pool."""
    try:
        lead_pct = float(row.get("lead_change_pct") or 0)
        board_pct = float(row.get("change_pct") or 0)
    except (TypeError, ValueError):
        return 0.0
    if abs(lead_pct) > 80:
        return -3.0
    if lead_pct - board_pct > 10 and board_pct < 2:
        return -1.0
    if lead_pct >= 0 and board_pct >= 0 and lead_pct - board_pct <= 4:
        return 0.4
    return 0.0


def _pre_screen_eligible(row: dict[str, Any]) -> bool:
    """Who deserves a K-line look — breadth/setup rules, not today's gain leaderboard."""
    br = _breadth_ratio(row)
    try:
        pct = float(row.get("change_pct") or 0)
        lead_pct = float(row.get("lead_change_pct") or 0) if row.get("lead_change_pct") is not None else pct
    except (TypeError, ValueError):
        pct, lead_pct = 0.0, 0.0

    if abs(lead_pct) > 80:
        return False
    if br is not None and br < 0.35:
        return False
    if pct >= 6.0 and (br is None or br < 0.55):
        return False

    if br is not None and br >= 0.55:
        return True
    if br is not None and br >= 0.5 and -2.0 <= pct <= 3.0:
        return True
    if br is not None and br >= 0.45 and -1.0 <= pct <= 2.0:
        return True
    if br is None and -2.0 <= pct <= 2.5:
        return True
    return False


def _pre_score_board(row: dict[str, Any]) -> float:
    """Rank within eligible pool: breadth synergy + setup zone; change_pct not a reward."""
    score = 0.0
    br = _breadth_ratio(row)
    if br is not None:
        score += (br - 0.5) * 6.0
    try:
        pct = float(row.get("change_pct") or 0)
    except (TypeError, ValueError):
        pct = 0.0
    score += _daily_change_penalty(pct)
    score += _lead_stock_anomaly_penalty(row)
    if -1.0 <= pct <= 2.0 and (br is None or br >= 0.5):
        score += 0.6
    if br is not None and br >= 0.6 and -0.5 <= pct <= 1.5:
        score += 0.5
    return score


def _select_boards_for_ta_scan(boards: list[dict[str, Any]], *, limit: int = 12) -> list[dict[str, Any]]:
    """Stratified pre-screen: industry + concept quotas, not one global gain-sorted list."""
    if not boards:
        return []
    if len(boards) <= limit:
        return list(boards)

    eligible = [r for r in boards if _pre_screen_eligible(r)]
    pool = eligible if eligible else list(boards)

    industry = [r for r in pool if r.get("board_type") == "行业"]
    concept = [r for r in pool if r.get("board_type") == "概念"]
    n_industry = limit // 2 + (limit % 2)
    n_concept = limit - n_industry

    def _rank_pool(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(
            rows,
            key=lambda r: (
                _breadth_ratio(r) or 0.0,
                _pre_score_board(r),
            ),
            reverse=True,
        )

    picked: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(rows: list[dict[str, Any]]) -> None:
        for row in rows:
            name = str(row.get("name") or "")
            if name and name not in seen:
                seen.add(name)
                picked.append(row)

    _add(_rank_pool(industry)[:n_industry])
    _add(_rank_pool(concept)[:n_concept])

    if len(picked) < limit:
        for row in _rank_pool(pool):
            if len(picked) >= limit:
                break
            name = str(row.get("name") or "")
            if name and name not in seen:
                seen.add(name)
                picked.append(row)
    return picked[:limit]


def _regime_from_df(df: pd.DataFrame, trend: str) -> str:
    close = df["close"].astype(float)
    ma20 = close.rolling(20).mean().iloc[-1]
    ma60 = close.rolling(60).mean().iloc[-1]
    last = float(close.iloc[-1])
    if pd.isna(ma20) or pd.isna(ma60):
        return "unknown"
    if trend == "uptrend" and last > ma20 > ma60:
        return "bull"
    if trend == "downtrend" and last < ma20 < ma60:
        return "bear"
    if trend == "sideways":
        return "range"
    if trend == "uptrend":
        return "bull"
    if trend == "downtrend":
        return "bear"
    return "range"


def _pattern_bias(patterns: list[str]) -> str:
    bull = sum(1 for p in patterns if p in _BULL_PATTERNS)
    bear = sum(1 for p in patterns if p in _BEAR_PATTERNS)
    if bull > bear:
        return "bullish" if bull >= 2 else "weak_bullish"
    if bear > bull:
        return "bearish" if bear >= 2 else "weak_bearish"
    return "neutral"


def _ta_from_collect_analysis(data: dict[str, Any], *, lead_name: str) -> dict[str, Any]:
    advice = synthesize_advice(data)
    trend = data.get("trend") or {}
    pat = data.get("price_patterns") or {}
    candle = data.get("candlestick") or {}
    patterns = list(dict.fromkeys((pat.get("patterns") or []) + (candle.get("patterns") or [])))[:5]
    regime = trend.get("regime") or "unknown"
    trend_dir = trend.get("trend") or "unknown"
    bias = pat.get("bias") or _pattern_bias(patterns)
    score = (
        _REGIME_SCORE.get(regime, 0)
        + _TREND_SCORE.get(trend_dir, 0)
        + _BIAS_SCORE.get(bias, 0)
        + min(max(advice.get("score", 0) / 2.0, -2), 2)
    )
    return {
        "available": True,
        "source": "local_ta",
        "lead_name": lead_name,
        "symbol": data.get("symbol"),
        "regime": regime,
        "trend": trend_dir,
        "patterns": patterns,
        "pattern_bias": bias,
        "stance": advice.get("stance"),
        "ta_score": round(score, 2),
        "reason": _format_ta_reason(regime, trend_dir, patterns, bias),
    }


def _simple_patterns_from_df(df: pd.DataFrame) -> tuple[list[str], str]:
    if df.empty or len(df) < 20:
        return [], "neutral"
    close = df["close"].astype(float)
    df["high"].astype(float)
    df["low"].astype(float)
    patterns: list[str] = []
    window = df.tail(30)
    box_high = float(window["high"].max())
    box_low = float(window["low"].min())
    mid = (box_high + box_low) / 2
    box_width = (box_high - box_low) / mid if mid else 0
    if box_width < 0.12:
        patterns.append("rectangle_range")
    last_close = float(close.iloc[-1])
    if last_close >= box_high * 0.98:
        patterns.append("near_resistance")
    elif last_close <= box_low * 1.02:
        patterns.append("near_support")
    ret5 = (last_close - float(close.iloc[-6])) / float(close.iloc[-6]) if len(close) >= 6 else 0
    if ret5 > 0.04:
        patterns.append("short_term_strength")
    bias = _pattern_bias(patterns)
    return patterns, bias


def _ta_from_df(df: pd.DataFrame, *, symbol: str, lead_name: str) -> dict[str, Any]:
    if df.empty or len(df) < 20:
        return {"available": False, "lead_name": lead_name, "error": "K线不足"}
    highs, lows = _find_swings(df)
    trend_dir = _classify_trend(highs, lows)
    regime = _regime_from_df(df, trend_dir)
    patterns, bias = _simple_patterns_from_df(df)
    score = _REGIME_SCORE.get(regime, 0) + _TREND_SCORE.get(trend_dir, 0) + _BIAS_SCORE.get(bias, 0)
    return {
        "available": True,
        "source": "fetched_ta",
        "lead_name": lead_name,
        "symbol": symbol,
        "regime": regime,
        "trend": trend_dir,
        "patterns": patterns,
        "pattern_bias": bias,
        "ta_score": round(score, 2),
        "reason": _format_ta_reason(regime, trend_dir, patterns, bias),
    }


def _format_ta_reason(regime: str, trend: str, patterns: list[str], bias: str) -> str:
    regime_cn = {
        "bull": "偏多环境",
        "bear": "偏空环境",
        "range": "震荡",
        "weak_rebound": "弱反弹",
        "panic": "恐慌",
    }.get(regime, regime)
    trend_cn = {
        "uptrend": "上升趋势",
        "downtrend": "下降趋势",
        "sideways": "横盘",
    }.get(trend, trend)
    pat_cn: list[str] = []
    for p in patterns[:2]:
        if "double_bottom" in p:
            pat_cn.append("双底")
        elif "bull_flag" in p:
            pat_cn.append("旗形整理")
        elif "rectangle" in p:
            pat_cn.append("箱体")
        elif "wedge" in p:
            pat_cn.append("楔形")
        elif "double_top" in p:
            pat_cn.append("双顶风险")
        elif "gap" in p:
            pat_cn.append("缺口")
    pat_part = "、".join(pat_cn) if pat_cn else ("形态偏多" if bias.startswith("bull") else "形态偏空" if bias.startswith("bear") else "形态中性")
    return f"{trend_cn}+{regime_cn}，{pat_part}"


def analyze_lead_stock_ta(lead_name: str) -> dict[str, Any]:
    clean = _clean_lead_name(lead_name)
    if not clean:
        return {"available": False, "error": "无领涨股"}
    if clean in _TA_CACHE:
        return _TA_CACHE[clean]

    code = resolve_stock_code_by_name(clean)
    if not code:
        result = {"available": False, "lead_name": clean, "error": "未解析到股票代码"}
        _TA_CACHE[clean] = result
        return result

    sym = _normalize_symbol(code)
    data = collect_analysis(sym)
    if data.get("available"):
        result = _ta_from_collect_analysis(data, lead_name=clean)
        _TA_CACHE[clean] = result
        return result

    end = datetime.now().strftime("%Y%m%d")
    synced = sync_symbol(code, start_date="20240101")
    if synced.get("status") == "ok":
        data = collect_analysis(sym)
        if data.get("available"):
            result = _ta_from_collect_analysis(data, lead_name=clean)
            _TA_CACHE[clean] = result
            return result

    df = fetch_daily_quotes(code, start_date="20240101", end_date=end)
    result = _ta_from_df(df, symbol=sym, lead_name=clean)
    _TA_CACHE[clean] = result
    return result


def score_board_row(row: dict[str, Any], *, ta: dict[str, Any] | None = None) -> dict[str, Any]:
    enriched = dict(row)
    br = _breadth_ratio(row)
    ta_data = ta or analyze_lead_stock_ta(str(row.get("lead_stock") or ""))
    enriched["ta"] = ta_data
    ta_score = float(ta_data.get("ta_score") or 0) if ta_data.get("available") else 0.0
    breadth_pts = ((br - 0.5) * 2.5) if br is not None else 0.0
    heat_penalty = _daily_change_penalty(row.get("change_pct"))
    if ta_data.get("available"):
        total = ta_score * 1.5 + breadth_pts + heat_penalty
    else:
        total = breadth_pts + heat_penalty
    if br is not None and br < 0.45:
        total -= 1.5
        enriched["breadth_warning"] = "板块内多数个股走弱，形态参考降权"
    enriched["score"] = round(total, 2)
    enriched["breadth_ratio"] = round(br, 3) if br is not None else None
    reason = ta_data.get("reason") if ta_data.get("available") else "板块广度尚可，形态待验证"
    if enriched.get("breadth_warning"):
        reason = f"{reason}；{enriched['breadth_warning']}"
    enriched["pick_reason"] = reason
    return enriched


def rank_boards_by_ta(
    boards: list[dict[str, Any]],
    *,
    top_n: int = 5,
    ta_scan_limit: int = 12,
) -> list[dict[str, Any]]:
    if not boards:
        return []
    from concurrent.futures import ThreadPoolExecutor, as_completed

    candidates = _select_boards_for_ta_scan(boards, limit=ta_scan_limit)
    if not candidates:
        return []
    scanned: list[dict[str, Any]] = []
    workers = min(6, len(candidates))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(score_board_row, row): row for row in candidates}
        for fut in as_completed(futs):
            try:
                scanned.append(fut.result())
            except Exception:  # noqa: BLE001
                row = futs[fut]
                scanned.append({**row, "score": 0.0, "pick_reason": "形态分析失败"})
    return sorted(scanned, key=lambda x: float(x.get("score") or 0), reverse=True)[:top_n]
