"""Load indicator snapshots and produce Phase-1 structured analysis."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from modules.candlestick import analyze_candlestick
from modules.data_fetcher import _normalize_symbol, load_quotes
from modules.db import get_connection
from modules.oscillator_prep import PREP_LOOKBACK, evaluate_prep_zone
from modules.patterns import analyze_price_patterns
from modules.trend_structure import analyze_trend_structure


@dataclass
class Snapshot:
    symbol: str
    trade_date: str
    close: float | None
    volume: float | None
    turnover_rate: float | None
    updated_at: str | None
    ma5: float | None = None
    ma10: float | None = None
    ma20: float | None = None
    ma60: float | None = None
    ma120: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    macd_hist: float | None = None
    rsi14: float | None = None
    k: float | None = None
    d: float | None = None
    j: float | None = None
    boll_mid: float | None = None
    boll_upper: float | None = None
    boll_lower: float | None = None
    volume_ratio: float | None = None
    cci20: float | None = None
    williams_r14: float | None = None
    plus_di14: float | None = None
    minus_di14: float | None = None
    adx14: float | None = None
    prev: dict[str, Any] | None = None


def load_indicator_history(symbol: str, lookback: int = PREP_LOOKBACK) -> list[dict[str, Any]]:
    symbol = _normalize_symbol(symbol)
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT trade_date, d, rsi14, cci20, williams_r14, adx14,
                   plus_di14, minus_di14, macd, macd_signal, macd_hist, k, j
            FROM indicators
            WHERE symbol = ?
            ORDER BY trade_date DESC
            LIMIT ?
            """,
            (symbol, lookback),
        ).fetchall()
    return [dict(r) for r in rows]


def load_snapshot(symbol: str, lookback: int = 5) -> Snapshot | None:
    symbol = _normalize_symbol(symbol)
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT q.symbol, q.trade_date, q.close, q.volume, q.turnover_rate, q.updated_at,
                   i.ma5, i.ma10, i.ma20, i.ma60, i.ma120,
                   i.macd, i.macd_signal, i.macd_hist,
                   i.rsi14, i.k, i.d, i.j,
                   i.boll_mid, i.boll_upper, i.boll_lower,
                   i.volume_ratio, i.cci20, i.williams_r14,
                   i.plus_di14, i.minus_di14, i.adx14
            FROM indicators i
            JOIN quotes q ON q.symbol = i.symbol AND q.trade_date = i.trade_date
            WHERE i.symbol = ?
            ORDER BY i.trade_date DESC
            LIMIT ?
            """,
            (symbol, lookback),
        ).fetchall()
    if not rows:
        return None

    latest = dict(rows[0])
    prev = dict(rows[1]) if len(rows) > 1 else None
    return Snapshot(prev=prev, **latest)


def _cross(prev_a: float | None, prev_b: float | None, a: float | None, b: float | None) -> str | None:
    if None in (prev_a, prev_b, a, b):
        return None
    if prev_a <= prev_b and a > b:
        return "golden_cross"
    if prev_a >= prev_b and a < b:
        return "death_cross"
    return None


def analyze_oscillators(s: Snapshot, history: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    notes: list[str] = []
    bias = "neutral"

    prep = evaluate_prep_zone(history or [{"d": s.d, "rsi14": s.rsi14, "cci20": s.cci20, "williams_r14": s.williams_r14}])
    if prep["d_min"] is not None and prep["d_max"] is not None and prep["d_current"] is not None:
        prep_summary = (
            f"预备区：{prep['state_cn']}（近{prep['lookback_days']}日 "
            f"D {prep['d_min']:.1f}~{prep['d_max']:.1f}，当前 {prep['d_current']:.1f}）"
        )
    else:
        prep_summary = f"预备区：{prep['state_cn']}"
    notes.append(prep_summary)

    if s.macd is not None and s.macd_signal is not None:
        zone = "above_zero" if s.macd > 0 else "below_zero"
        cross = _cross(
            s.prev.get("macd") if s.prev else None,
            s.prev.get("macd_signal") if s.prev else None,
            s.macd,
            s.macd_signal,
        )
        if s.macd_hist is not None and s.prev and s.prev.get("macd_hist") is not None:
            if (s.prev["macd_hist"] or 0) > 0 and s.macd_hist < s.prev["macd_hist"]:
                notes.append("MACD 柱体缩短，动量衰减")
            elif (s.prev["macd_hist"] or 0) < 0 and s.macd_hist > s.prev["macd_hist"]:
                notes.append("MACD 柱体回升")
        if zone == "above_zero" and (cross == "golden_cross" or (s.macd_hist or 0) > 0):
            bias = "bullish"
            notes.append("MACD 零轴上方，动量偏强")
        elif zone == "below_zero" and cross == "golden_cross":
            bias = "weak_bullish"
            notes.append("MACD 零轴下方金叉，属弱反弹，需成交量确认")
        elif cross == "death_cross":
            bias = "bearish"
            notes.append("MACD 死叉，动量转弱")
        else:
            notes.append(f"MACD 位于零轴{'上' if zone == 'above_zero' else '下'}")

    if s.rsi14 is not None:
        if s.rsi14 >= 70:
            notes.append("RSI 进入超买区，趋势市中可能钝化")
        elif s.rsi14 <= 30:
            notes.append("RSI 进入超卖区，关注反弹条件")
        elif s.rsi14 >= 50:
            notes.append("RSI 强势区运行")
        else:
            notes.append("RSI 弱势区运行")

    if s.cci20 is not None:
        if s.cci20 > 100:
            notes.append(f"CCI {s.cci20:.1f} 超买区")
        elif s.cci20 < -100:
            notes.append(f"CCI {s.cci20:.1f} 超卖区")
        elif s.cci20 > 0:
            notes.append(f"CCI {s.cci20:.1f} 偏多")

    if s.williams_r14 is not None:
        if s.williams_r14 > 80:
            notes.append(f"Williams %R {s.williams_r14:.1f} 超买")
        elif s.williams_r14 < 20:
            notes.append(f"Williams %R {s.williams_r14:.1f} 超卖")

    if s.k is not None and s.d is not None:
        cross = _cross(s.prev.get("k") if s.prev else None, s.prev.get("d") if s.prev else None, s.k, s.d)
        if s.k > 80 and s.d > 80:
            notes.append("KDJ 高位钝化")
        elif s.k < 20 and s.d < 20 and cross == "golden_cross":
            notes.append("KDJ 低位金叉")
            if bias == "neutral":
                bias = "weak_bullish"
        elif cross == "death_cross" and s.k > 70:
            notes.append("KDJ 高位死叉")
            bias = "bearish" if bias != "bullish" else bias

    market_mode = "unknown"
    if s.adx14 is not None:
        prev_adx = s.prev.get("adx14") if s.prev else None
        if s.adx14 >= 25:
            market_mode = "trending"
            notes.append(f"ADX {s.adx14:.1f} 趋势态")
        elif s.adx14 < 20:
            market_mode = "ranging"
            notes.append(f"ADX {s.adx14:.1f} 横盘态，振荡权重↑")
        else:
            market_mode = "transition"
            notes.append(f"ADX {s.adx14:.1f} 过渡区")
        if prev_adx is not None:
            if prev_adx >= 40 and s.adx14 < prev_adx:
                notes.append("ADX 从高位回落，趋势性减弱")
            elif prev_adx < 20 and s.adx14 > prev_adx:
                notes.append("ADX 自低位上行，或进入趋势态")
        if s.plus_di14 is not None and s.minus_di14 is not None:
            if s.plus_di14 > s.minus_di14:
                notes.append("+DI 高于 -DI，方向偏多")
            elif s.plus_di14 < s.minus_di14:
                notes.append("-DI 高于 +DI，方向偏空")

    return {
        "bias": bias,
        "macd": s.macd,
        "macd_signal": s.macd_signal,
        "macd_hist": s.macd_hist,
        "rsi14": s.rsi14,
        "cci20": s.cci20,
        "williams_r14": s.williams_r14,
        "adx14": s.adx14,
        "plus_di14": s.plus_di14,
        "minus_di14": s.minus_di14,
        "k": s.k,
        "d": s.d,
        "j": s.j,
        "prep_zone": prep,
        "market_mode": market_mode,
        "notes": notes,
        "invalidation": "横盘整固中频繁交叉，或数据不足 35 根 K 线",
    }


def analyze_ma_boll(s: Snapshot) -> dict[str, Any]:
    notes: list[str] = []
    bias = "neutral"

    mas = [s.ma5, s.ma10, s.ma20, s.ma60]
    if all(v is not None for v in mas):
        if s.ma5 > s.ma10 > s.ma20 > s.ma60:
            bias = "bullish"
            notes.append("均线多头排列")
        elif s.ma5 < s.ma10 < s.ma20 < s.ma60:
            bias = "bearish"
            notes.append("均线空头排列")
        else:
            notes.append("均线纠缠，方向待选择")

    if s.close is not None and s.boll_upper and s.boll_mid and s.boll_lower:
        width = (s.boll_upper - s.boll_lower) / s.boll_mid if s.boll_mid else 0
        if s.close > s.boll_upper:
            notes.append("价格突破布林上轨")
        elif s.close < s.boll_lower:
            notes.append("价格跌破布林下轨")
        elif abs(s.close - s.boll_mid) / s.boll_mid < 0.01:
            notes.append("价格围绕布林中轨震荡")
        notes.append(f"布林带宽 {width:.2%}")

    if s.close is not None and s.ma20:
        env_upper = s.ma20 * 1.03
        env_lower = s.ma20 * 0.97
        if s.close > env_upper:
            notes.append("超 MA20+3% 包络上沿（固定百分比带，短线过度延伸）")
        elif s.close < env_lower:
            notes.append("跌破 MA20-3% 包络下沿")

    pipe_days = 20
    if s.adx14 is not None and s.adx14 < 20:
        pipe_days = 40
        notes.append("ADX<20 横盘：4周规则扩展为近40日管道")

    with get_connection() as conn:
        pipe_row = conn.execute(
            f"""
            SELECT MAX(high) AS hi, MIN(low) AS lo
            FROM (
                SELECT high, low FROM quotes
                WHERE symbol = ?
                ORDER BY trade_date DESC
                LIMIT {pipe_days}
            )
            """,
            (s.symbol,),
        ).fetchone()
    if pipe_row and s.close and pipe_row["hi"] and pipe_row["lo"]:
        label = f"近{pipe_days}日"
        if s.close > pipe_row["hi"]:
            notes.append(f"4周规则：收价突破{label}最高（管道向上突破）")
        elif s.close < pipe_row["lo"]:
            notes.append(f"4周规则：收价跌破{label}最低（管道向下突破）")

    return {
        "bias": bias,
        "ma5": s.ma5,
        "ma10": s.ma10,
        "ma20": s.ma20,
        "ma60": s.ma60,
        "boll_mid": s.boll_mid,
        "boll_upper": s.boll_upper,
        "boll_lower": s.boll_lower,
        "notes": notes,
        "invalidation": "均线粘合区假突破，或除权导致均线失真",
    }


def analyze_volume_price(s: Snapshot) -> dict[str, Any]:
    notes: list[str] = []
    bias = "neutral"
    stage = "unknown"

    if s.volume_ratio is not None:
        if s.volume_ratio >= 1.5:
            notes.append(f"量比 {s.volume_ratio:.2f}，明显放量")
        elif s.volume_ratio <= 0.7:
            notes.append(f"量比 {s.volume_ratio:.2f}，缩量")
        else:
            notes.append(f"量比 {s.volume_ratio:.2f}，量能平稳")

    price_chg = 0.0
    if s.prev and s.close and s.prev.get("close"):
        price_chg = (s.close - s.prev["close"]) / s.prev["close"]
        if price_chg > 0 and (s.volume_ratio or 0) > 1.2:
            bias = "bullish"
            notes.append("价涨量增，量价配合")
        elif price_chg > 0 and (s.volume_ratio or 0) < 0.8:
            bias = "weak_bullish"
            notes.append("价涨量缩，上涨动能待确认")
        elif price_chg < 0 and (s.volume_ratio or 0) > 1.2:
            bias = "bearish"
            notes.append("价跌量增，抛压偏大")
        elif price_chg < 0 and (s.volume_ratio or 0) < 0.8:
            notes.append("价跌量缩，下跌中继或接近调整末端")

    df = load_quotes(s.symbol, limit=25)
    if not df.empty and len(df) >= 5 and s.close:
        close = df["close"].astype(float)
        volume = df["volume"].astype(float)
        ret_20 = (float(close.iloc[-1]) - float(close.iloc[0])) / float(close.iloc[0]) if len(close) >= 20 else 0

        tail5 = df.tail(5)
        avg_close = float(tail5["close"].astype(float).mean())
        range_pct = (float(tail5["high"].max()) - float(tail5["low"].min())) / avg_close if avg_close else 0
        vol5 = tail5["volume"].astype(float)
        vol_avg = float(volume.tail(20).mean()) if len(volume) >= 20 else float(volume.mean())
        vol_tail_avg = float(vol5.mean())

        if range_pct < 0.025 and vol_tail_avg > vol_avg * 1.3:
            stage = "churning"
            notes.append("近5日价平量增（Churning），筹码换手激烈，等方向选择")

        if (s.volume_ratio or 0) >= 1.8:
            if ret_20 > 0.12 and abs(price_chg) < 0.015:
                stage = "blow_off"
                notes.append("胀爆预警：长期上涨后巨量但价平/微涨")
                if bias == "neutral":
                    bias = "bearish"
            elif ret_20 < -0.12 and price_chg < -0.02:
                stage = "selling_climax"
                notes.append("抛售高潮：长期下跌后急跌+巨量，关注是否恐慌出清")

        up_vol = float(volume[close.diff() > 0].tail(10).sum())
        down_vol = float(volume[close.diff() < 0].tail(10).sum())
        if up_vol > down_vol * 1.3 and ret_20 > 0:
            notes.append("近10日涨日量合计 > 跌日量，OBV式方向偏多")
        elif down_vol > up_vol * 1.3 and ret_20 < 0:
            notes.append("近10日跌日量合计 > 涨日量，OBV式方向偏空")

    return {
        "bias": bias,
        "stage": stage,
        "volume": s.volume,
        "turnover_rate": s.turnover_rate,
        "volume_ratio": s.volume_ratio,
        "notes": notes,
        "invalidation": "除权、事件日、一字板导致量能不可比",
    }


def analyze_capital_flow(symbol: str) -> dict[str, Any]:
    symbol = _normalize_symbol(symbol)
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT * FROM capital_flow
            WHERE symbol = ?
            ORDER BY trade_date DESC
            LIMIT 1
            """,
            (symbol,),
        ).fetchone()

    if not row:
        return {
            "bias": "neutral",
            "notes": ["无本地资金面数据，请先 sync 或等待接口可用"],
            "invalidation": "数据缺失",
            "available": False,
        }

    r = dict(row)
    notes: list[str] = []
    bias = "neutral"
    main = r.get("main_net_inflow")
    if main is not None:
        if main > 0:
            bias = "bullish"
            notes.append(f"主力净流入 {main:,.0f}")
        elif main < 0:
            bias = "bearish"
            notes.append(f"主力净流出 {abs(main):,.0f}")

    streak = 0
    with get_connection() as conn:
        streak_rows = conn.execute(
            """
            SELECT main_net_inflow FROM capital_flow
            WHERE symbol = ? ORDER BY trade_date DESC LIMIT 30
            """,
            (symbol,),
        ).fetchall()
    for sr in streak_rows:
        if sr["main_net_inflow"] is None or sr["main_net_inflow"] <= 0:
            break
        streak += 1
    if streak >= 3:
        notes.append(f"连续 {streak} 日主力净流入")

    if r.get("northbound_hold_ratio") is not None:
        notes.append(f"北向持股占比 {r['northbound_hold_ratio']:.2f}%")
    if r.get("margin_balance") is not None:
        notes.append(f"融资余额 {r['margin_balance']:,.0f}")
    if r.get("on_lhb"):
        notes.append("近期登上龙虎榜")

    if not notes:
        notes.append("资金面记录存在但关键字段为空")

    return {
        "bias": bias,
        "main_net_inflow": main,
        "streak_days": streak,
        "northbound_hold_ratio": r.get("northbound_hold_ratio"),
        "margin_balance": r.get("margin_balance"),
        "on_lhb": bool(r.get("on_lhb")),
        "trade_date": r.get("trade_date"),
        "notes": notes,
        "invalidation": "East Money 估算口径；接口失败或涨停日失真",
        "available": True,
    }


def render_report(symbol: str) -> str:
    s = load_snapshot(symbol)
    if not s:
        return f"无本地数据：{symbol}。请先运行 python scripts/sync_symbol.py {symbol}"

    history = load_indicator_history(symbol)
    osc = analyze_oscillators(s, history)
    ma = analyze_ma_boll(s)
    vol = analyze_volume_price(s)
    trend = analyze_trend_structure(symbol)
    candle = analyze_candlestick(symbol, prep_zone=osc.get("prep_zone"))
    price_pat = analyze_price_patterns(symbol)
    flow = analyze_capital_flow(symbol)

    lines = [
        f"## {s.symbol} 技术分析快照 ({s.trade_date})",
        f"数据时间：{s.updated_at}",
        "",
        "### 趋势结构",
        f"- 趋势：{trend['trend']} / 状态：{trend['regime']}",
        *[f"- {n}" for n in trend["notes"]],
        f"- 失效：{trend['invalidation']}",
        "",
        "### 大盘环境",
        f"- 道氏双指数：{trend.get('dow_confirmation', {}).get('state_cn', '未验证')}",
        *[f"- {n}" for n in trend.get("dow_confirmation", {}).get("notes", [])],
        f"- 市场广度：{trend.get('market_breadth', {}).get('notes', ['未验证'])[0] if trend.get('market_breadth', {}).get('available') else '未验证'}",
        "",
        "### 摆动指标",
        f"- 倾向：{osc['bias']}",
        f"- 市场模式：{osc.get('market_mode', 'unknown')}",
        f"- 预备区：{osc['prep_zone']['state_cn']}",
        *[f"- {n}" for n in osc["notes"] if not n.startswith("预备区：")],
        f"- 失效：{osc['invalidation']}",
        "",
        "### 均线与布林",
        f"- 倾向：{ma['bias']}",
        *[f"- {n}" for n in ma["notes"]],
        f"- 失效：{ma['invalidation']}",
        "",
        "### 量价",
        f"- 倾向：{vol['bias']}",
        *[f"- {n}" for n in vol["notes"]],
        f"- 失效：{vol['invalidation']}",
        "",
        "### 蜡烛图",
        f"- 倾向：{candle['bias']}",
        f"- 预备区过滤：{candle.get('prep_filter', '未验证')}",
        *[f"- {n}" for n in candle["notes"]],
        f"- 失效：{candle['invalidation']}",
    ]
    bars = candle.get("bars") or []
    if bars:
        lines.append("- 近" + str(len(bars)) + "根K线计量（对照 nison 判据核对 body/upper/lower/range/mid/gap）：")
        for b in bars[-5:]:
            gap = ""
            if b.get("gap_up"):
                gap = " 向上跳空"
            elif b.get("gap_down"):
                gap = " 向下跳空"
            lines.append(
                f"  {b.get('trade_date')} {b.get('color')} "
                f"O{b.get('open')} H{b.get('high')} L{b.get('low')} C{b.get('close')} "
                f"body={b.get('body')} upper={b.get('upper')} lower={b.get('lower')} "
                f"range={b.get('range')} mid={b.get('mid')}{gap}"
            )
    lines.extend(
        [
        "",
        "### 价格形态",
        f"- 倾向：{price_pat['bias']}",
        *[f"- {n}" for n in price_pat["notes"]],
        f"- 失效：{price_pat['invalidation']}",
        "",
        "### 资金面",
        f"- 倾向：{flow['bias']}",
        *[f"- {n}" for n in flow["notes"]],
        f"- 失效：{flow['invalidation']}",
        "",
        "### 综合提示",
        "- 以上为多 Skill 结构化输出，非买卖指令",
        f"- 当前市场状态「{trend['regime']}」应约束趋势类信号权重",
        ]
    )
    return "\n".join(lines)


def ensure_demo_data(symbol: str = "600000") -> None:
    """Seed synthetic data when AKShare is unavailable."""
    import numpy as np

    from modules.data_fetcher import refresh_indicators, save_indicators, save_quotes
    from modules.db import init_db
    from modules.indicators import enrich_indicators

    init_db()
    quotes = load_quotes(symbol, limit=1)
    if quotes.empty:
        symbol_norm = _normalize_symbol(symbol)
        dates = pd.date_range("2024-01-01", periods=150, freq="B")
        np.random.seed(abs(hash(symbol_norm)) % 2**32)
        close = 100 + np.cumsum(np.random.randn(150))
        df = pd.DataFrame(
            {
                "symbol": symbol_norm,
                "trade_date": dates.strftime("%Y-%m-%d"),
                "open": close - 0.5,
                "high": close + 1,
                "low": close - 1,
                "close": close,
                "volume": np.random.randint(1_000_000, 5_000_000, 150),
                "turnover_rate": np.random.uniform(0.5, 3.0, 150),
                "amount": close * 1_000_000,
            }
        )
        enriched = enrich_indicators(df)
        save_quotes(df)
        save_indicators(enriched)
        return

    s = load_snapshot(symbol)
    if s and (s.cci20 is None or s.adx14 is None):
        refresh_indicators(symbol)
