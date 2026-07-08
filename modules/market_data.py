"""Market index sync, breadth, and Dow Theory cross-index verification."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import akshare as ak
import pandas as pd

from modules.akshare_client import fetch_market_breadth_em, fetch_market_breadth_sina, fetch_with_retry
from modules.data_fetcher import load_quotes, save_quotes
from modules.db import get_connection, init_db
from modules.trend_structure import _classify_trend, _find_swings


def UTC_NOW():
    return datetime.now(timezone.utc).isoformat()

# symbol in DB -> Tencent index code
DEFAULT_INDICES: dict[str, tuple[str, str]] = {
    "INDEX.SH000001": ("sh000001", "上证指数"),
    "INDEX.SZ399001": ("sz399001", "深证成指"),
    "INDEX.SH000300": ("sh000300", "沪深300"),
}


def _normalize_index_quotes(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame()
    df = raw.rename(
        columns={
            "date": "trade_date",
            "open": "open",
            "close": "close",
            "high": "high",
            "low": "low",
            "amount": "volume",
        }
    )
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d")
    df["symbol"] = symbol
    df["turnover_rate"] = None
    df["amount"] = df["volume"]
    return df[["symbol", "trade_date", "open", "high", "low", "close", "volume", "turnover_rate", "amount"]]


def fetch_index_daily(tx_symbol: str) -> pd.DataFrame:
    raw = fetch_with_retry(ak.stock_zh_index_daily_tx, symbol=tx_symbol)
    return raw


def sync_indices() -> dict[str, Any]:
    """Fetch major A-share indices into quotes table."""
    init_db()
    results: dict[str, Any] = {}
    for symbol, (tx_code, name) in DEFAULT_INDICES.items():
        try:
            raw = fetch_index_daily(tx_code)
            df = _normalize_index_quotes(raw, symbol)
            if df.empty:
                results[symbol] = {"status": "failed", "rows": 0, "name": name}
                continue
            # keep recent 500 trading days
            df = df.tail(500)
            count = save_quotes(df)
            results[symbol] = {"status": "ok", "rows": count, "name": name}
        except Exception as exc:  # noqa: BLE001
            results[symbol] = {"status": "failed", "error": str(exc), "name": name}
    return results


def _parse_breadth_row(item: str) -> str | None:
    text = str(item)
    if "上涨" in text or "涨" == text.strip():
        return "rising"
    if "下跌" in text or "跌" == text.strip():
        return "falling"
    if "平" in text:
        return "flat"
    if "涨停" in text and "st" not in text.lower():
        return "limit_up"
    if "跌停" in text and "st" not in text.lower():
        return "limit_down"
    if "活跃" in text:
        return "activity"
    if "统计" in text:
        return "as_of"
    return None


def fetch_market_breadth_legu() -> dict[str, Any]:
    """Market breadth from legulegu.com (fallback when East Money is blocked)."""
    raw = fetch_with_retry(ak.stock_market_activity_legu)
    if raw is None or raw.empty:
        return {"available": False}

    parsed: dict[str, Any] = {"available": True, "source": "legulegu", "raw": {"source": "legulegu"}}
    for _, row in raw.iterrows():
        key = _parse_breadth_row(str(row["item"]))
        val = row["value"]
        if key == "rising" and "rising_count" not in parsed:
            parsed["rising_count"] = int(val)
        elif key == "falling" and "falling_count" not in parsed:
            parsed["falling_count"] = int(val)
        elif key == "flat" and "flat_count" not in parsed:
            parsed["flat_count"] = int(val)
        elif key == "limit_up" and "limit_up" not in parsed:
            parsed["limit_up"] = int(val)
        elif key == "limit_down" and "limit_down" not in parsed:
            parsed["limit_down"] = int(val)
        elif key == "activity":
            parsed["activity_pct"] = float(str(val).replace("%", ""))
        elif key == "as_of":
            parsed["trade_date"] = pd.to_datetime(val).strftime("%Y-%m-%d")
        parsed["raw"][str(row["item"])] = val

    if "trade_date" not in parsed:
        parsed["trade_date"] = datetime.now().strftime("%Y-%m-%d")

    # Legu table order is stable; fallback if label parse fails
    if "rising_count" not in parsed and len(raw) >= 1:
        parsed["rising_count"] = int(raw.iloc[0]["value"])
    if "falling_count" not in parsed and len(raw) >= 5:
        parsed["falling_count"] = int(raw.iloc[4]["value"])
    if "flat_count" not in parsed and len(raw) >= 8:
        parsed["flat_count"] = int(raw.iloc[8]["value"])

    return parsed


def fetch_market_breadth() -> dict[str, Any]:
    """Latest market breadth: East Money → Sina → legulegu fallback."""
    for fetcher in (fetch_market_breadth_em, fetch_market_breadth_sina, fetch_market_breadth_legu):
        try:
            data = fetcher()
            if data.get("available"):
                return data
        except Exception:
            continue
    return {"available": False}


def save_market_breadth(data: dict[str, Any]) -> None:
    if not data.get("available"):
        return
    init_db()
    now = UTC_NOW()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO market_breadth (
                trade_date, rising_count, falling_count, flat_count,
                limit_up, limit_down, activity_pct, raw_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(trade_date) DO UPDATE SET
                rising_count=excluded.rising_count,
                falling_count=excluded.falling_count,
                flat_count=excluded.flat_count,
                limit_up=excluded.limit_up,
                limit_down=excluded.limit_down,
                activity_pct=excluded.activity_pct,
                raw_json=excluded.raw_json,
                updated_at=excluded.updated_at
            """,
            (
                data.get("trade_date"),
                data.get("rising_count"),
                data.get("falling_count"),
                data.get("flat_count"),
                data.get("limit_up"),
                data.get("limit_down"),
                data.get("activity_pct"),
                json.dumps(
                    {
                        **(data.get("raw") or {}),
                        "source": data.get("source"),
                    },
                    ensure_ascii=False,
                ),
                now,
            ),
        )
        conn.commit()


def sync_market_breadth() -> dict[str, Any]:
    try:
        data = fetch_market_breadth()
        if data.get("available"):
            save_market_breadth(data)
            return {
                "status": "ok",
                "source": data.get("source"),
                **{k: data[k] for k in ("trade_date", "rising_count", "falling_count")},
            }
        return {"status": "failed", "error": "empty breadth"}
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "error": str(exc)}


def _breadth_source_label(row: dict[str, Any]) -> tuple[str, str]:
    try:
        raw = json.loads(row.get("raw_json") or "{}")
        source = raw.get("source") or "unknown"
    except (json.JSONDecodeError, TypeError):
        source = "unknown"
    if source == "eastmoney":
        return source, "东方财富沪深京A股全市场（与同花顺/理财通口径一致）"
    if source == "sina":
        return source, "新浪财经沪深京A股全市场（与同花顺/理财通口径一致）"
    if source == "legulegu":
        return source, "乐咕乐股（备用源，可能与主流APP有差异）"
    return source, "本地缓存"


def load_latest_breadth() -> dict[str, Any] | None:
    init_db()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM market_breadth ORDER BY trade_date DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def _swing_breakout(highs: list[tuple[int, float]], lows: list[tuple[int, float]], close: float) -> str:
    """Did price confirm above prior swing high or below prior swing low?"""
    if len(highs) < 2 or len(lows) < 2:
        return "unknown"
    prev_high = highs[-2][1]
    prev_low = lows[-2][1]
    if close > prev_high:
        return "above_prior_high"
    if close < prev_low:
        return "below_prior_low"
    return "inside_range"


def analyze_dow_confirmation(lookback: int = 120) -> dict[str, Any]:
    """Shanghai + Shenzhen index mutual verification."""
    sh_sym, sz_sym = "INDEX.SH000001", "INDEX.SZ399001"
    sh_df = load_quotes(sh_sym, limit=lookback)
    sz_df = load_quotes(sz_sym, limit=lookback)

    if sh_df.empty or sz_df.empty:
        return {
            "available": False,
            "state": "unverified",
            "state_cn": "指数数据缺失",
            "notes": ["请先运行 python scripts/sync_market.py 同步指数"],
        }

    notes: list[str] = []
    states: dict[str, str] = {}
    for label, sym, df in [("上证", sh_sym, sh_df), ("深证", sz_sym, sz_df)]:
        highs, lows = _find_swings(df)
        close = float(df["close"].iloc[-1])
        br = _swing_breakout(highs, lows, close)
        states[sym] = br
        trend = _classify_trend(highs, lows)
        notes.append(f"{label}：{trend} / 收盘相对前波段={br}")

    sh, sz = states[sh_sym], states[sz_sym]
    if sh == "above_prior_high" and sz == "above_prior_high":
        state, state_cn = "bull_confirmed", "双指数同创新高确认"
    elif sh == "below_prior_low" and sz == "below_prior_low":
        state, state_cn = "bear_confirmed", "双指数同创新低确认"
    elif sh == "above_prior_high" or sz == "above_prior_high":
        state, state_cn = "divergence_bull", "仅单指数创新高（相互背离警告）"
    elif sh == "below_prior_low" or sz == "below_prior_low":
        state, state_cn = "divergence_bear", "仅单指数创新低（相互背离警告）"
    else:
        state, state_cn = "neutral", "双指数均未突破前波段极值"

    return {
        "available": True,
        "state": state,
        "state_cn": state_cn,
        "shanghai": states[sh_sym],
        "shenzhen": states[sz_sym],
        "notes": notes,
    }


def resample_weekly(daily: pd.DataFrame) -> pd.DataFrame:
    if daily.empty:
        return pd.DataFrame()
    df = daily.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.set_index("trade_date").sort_index()
    weekly = df.resample("W-FRI").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    weekly = weekly.dropna(subset=["close"]).reset_index()
    weekly["trade_date"] = weekly["trade_date"].dt.strftime("%Y-%m-%d")
    return weekly


def resample_monthly(daily: pd.DataFrame) -> pd.DataFrame:
    if daily.empty:
        return pd.DataFrame()
    df = daily.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.set_index("trade_date").sort_index()
    monthly = df.resample("ME").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    monthly = monthly.dropna(subset=["close"]).reset_index()
    monthly["trade_date"] = monthly["trade_date"].dt.strftime("%Y-%m-%d")
    return monthly


def analyze_relative_strength(
    symbol: str,
    benchmark: str = "INDEX.SH000300",
    lookback: int = 60,
) -> dict[str, Any]:
    """Stock vs index return over lookback (Murphy ch18 relative strength)."""
    from modules.data_fetcher import _normalize_symbol

    symbol = _normalize_symbol(symbol)
    stock = load_quotes(symbol, limit=lookback)
    bench = load_quotes(benchmark, limit=lookback)
    if stock.empty or bench.empty or len(stock) < 10:
        return {
            "available": False,
            "bias": "unknown",
            "note": "相对强度未验证（缺个股或指数数据，请 sync_market + sync_symbol）",
        }

    sc = stock["close"].astype(float)
    bc = bench["close"].astype(float)
    stock_ret = (float(sc.iloc[-1]) - float(sc.iloc[0])) / float(sc.iloc[0])
    bench_ret = (float(bc.iloc[-1]) - float(bc.iloc[0])) / float(bc.iloc[0])
    diff = stock_ret - bench_ret

    if diff > 0.05:
        bias, bias_cn = "strong", "强于基准"
        note = f"近{len(stock)}日个股 {stock_ret:.1%} vs 沪深300 {bench_ret:.1%}，相对强势"
    elif diff < -0.05:
        bias, bias_cn = "weak", "弱于基准"
        note = f"近{len(stock)}日个股 {stock_ret:.1%} vs 沪深300 {bench_ret:.1%}，逆势偏弱"
    else:
        bias, bias_cn = "neutral", "与基准接近"
        note = f"近{len(stock)}日收益与沪深300接近（差 {diff:.1%}）"

    return {
        "available": True,
        "benchmark": benchmark,
        "stock_return": stock_ret,
        "benchmark_return": bench_ret,
        "spread": diff,
        "bias": bias,
        "bias_cn": bias_cn,
        "note": note,
    }


def analyze_multi_timeframe(symbol: str, daily_lookback: int = 120) -> dict[str, Any]:
    """Daily + weekly trend alignment from resampled OHLC."""
    df = load_quotes(symbol, limit=daily_lookback)
    if df.empty or len(df) < 20:
        return {
            "available": False,
            "alignment": "unknown",
            "notes": ["K 线不足，无法判多周期"],
        }

    d_highs, d_lows = _find_swings(df)
    daily_trend = _classify_trend(d_highs, d_lows)

    weekly = resample_weekly(df)
    w_highs, w_lows = _find_swings(weekly) if len(weekly) >= 10 else ([], [])
    weekly_trend = _classify_trend(w_highs, w_lows) if w_highs and w_lows else "unknown"

    monthly = resample_monthly(df)
    m_highs, m_lows = _find_swings(monthly) if len(monthly) >= 6 else ([], [])
    monthly_trend = _classify_trend(m_highs, m_lows) if m_highs and m_lows else "unknown"

    trend_cn = {"uptrend": "上升", "downtrend": "下降", "sideways": "横盘", "unknown": "未知"}
    trends = [daily_trend, weekly_trend, monthly_trend]
    up = sum(1 for t in trends if t == "uptrend")
    down = sum(1 for t in trends if t == "downtrend")

    if up >= 2 and down == 0:
        alignment = "aligned"
        align_cn = "日/周/月至少两周期偏多（突破态，月线为 resample 近似）"
    elif down >= 2 and up == 0:
        alignment = "aligned_bear"
        align_cn = "日/周/月至少两周期偏空"
    elif daily_trend == weekly_trend and daily_trend in ("uptrend", "downtrend"):
        alignment = "aligned"
        align_cn = "日/周同向（突破态）"
    elif "sideways" in trends or len(set(t for t in trends if t != "unknown")) > 1:
        alignment = "mixed"
        align_cn = "多周期不一致或横盘（平衡态，以长期为准）"
    else:
        alignment = "unknown"
        align_cn = "未知"

    notes = [
        f"日线：{trend_cn.get(daily_trend, daily_trend)}",
        f"周线（resample）：{trend_cn.get(weekly_trend, weekly_trend)}",
        f"月线（resample 近似，结论降权）：{trend_cn.get(monthly_trend, monthly_trend)}",
        align_cn,
    ]
    if daily_trend == "downtrend" and weekly_trend == "uptrend":
        notes.append("日线转弱但周线仍强 → 次要调整，不宜直接定熊")

    return {
        "available": True,
        "daily_trend": daily_trend,
        "weekly_trend": weekly_trend,
        "monthly_trend": monthly_trend,
        "monthly_approximate": True,
        "alignment": alignment,
        "alignment_cn": align_cn,
        "notes": notes,
    }


def analyze_market_breadth() -> dict[str, Any]:
    row = load_latest_breadth()
    if not row:
        return {
            "available": False,
            "notes": ["广度数据缺失，请 sync_market"],
        }

    rising = row.get("rising_count") or 0
    falling = row.get("falling_count") or 0
    source, source_label = _breadth_source_label(row)
    notes: list[str] = [f"涨跌家数来源：{source_label}"]
    bias = "neutral"
    if rising > falling * 1.2:
        bias = "bullish"
        notes.append(f"上涨 {rising} > 下跌 {falling}，广度偏多")
    elif falling > rising * 1.2:
        bias = "bearish"
        notes.append(f"下跌 {falling} > 上涨 {rising}，广度偏空")
    else:
        notes.append(f"上涨 {rising} / 下跌 {falling}，广度中性")

    if row.get("limit_up") is not None:
        notes.append(f"涨停 {row['limit_up']} / 跌停 {row.get('limit_down', '—')}")

    return {
        "available": True,
        "source": source,
        "source_label": source_label,
        "trade_date": row.get("trade_date"),
        "rising_count": rising,
        "falling_count": falling,
        "flat_count": row.get("flat_count"),
        "bias": bias,
        "notes": notes,
    }


def sync_market() -> dict[str, Any]:
    """Sync indices + latest breadth snapshot."""
    init_db()
    indices = sync_indices()
    breadth = sync_market_breadth()
    return {"indices": indices, "breadth": breadth}
