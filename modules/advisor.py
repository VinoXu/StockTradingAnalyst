"""Murphy-style technical advisor: structured data + Skill checklist + actionable brief.

Usage:
    from modules.advisor import render_advice_report, build_agent_prompt
"""

from __future__ import annotations

import json
from typing import Any

from modules.candlestick import analyze_candlestick
from modules.data_fetcher import _normalize_symbol
from modules.data_timestamps import market_data_as_of, symbol_data_as_of
from modules.market_data import (
    analyze_dow_confirmation,
    analyze_market_breadth,
    analyze_relative_strength,
    sync_market_breadth,
)
from modules.patterns import analyze_price_patterns
from modules.portfolio import portfolio_summary
from modules.skill_loader import load_skill, runtime_skill_names
from modules.ta_analysis import (
    analyze_capital_flow,
    analyze_ma_boll,
    analyze_oscillators,
    analyze_volume_price,
    load_indicator_history,
    load_snapshot,
    render_report,
)
from modules.trend_structure import analyze_trend_structure

_BIAS_SCORE = {
    "bullish": 1.0,
    "weak_bullish": 0.5,
    "neutral": 0.0,
    "weak_bearish": -0.5,
    "bearish": -1.0,
}


def collect_analysis(symbol: str) -> dict[str, Any]:
    """Gather all domain analyses for one symbol."""
    s = load_snapshot(symbol)
    if not s:
        return {"available": False, "error": f"无本地数据：{symbol}"}

    history = load_indicator_history(symbol)
    osc = analyze_oscillators(s, history)
    trend = analyze_trend_structure(symbol)
    as_of = symbol_data_as_of(s.symbol)
    quote_as_of = as_of["quote"]
    return {
        "available": True,
        "symbol": s.symbol,
        "trade_date": s.trade_date,
        "data_as_of_date": quote_as_of.get("as_of_date"),
        "data_as_of_label": quote_as_of.get("as_of_label"),
        "data_as_of": as_of,
        "snapshot": s,
        "trend": trend,
        "oscillators": osc,
        "ma_boll": analyze_ma_boll(s),
        "volume_price": analyze_volume_price(s),
        "candlestick": analyze_candlestick(symbol, prep_zone=osc.get("prep_zone")),
        "price_patterns": analyze_price_patterns(symbol),
        "capital_flow": analyze_capital_flow(symbol),
        "relative_strength": analyze_relative_strength(symbol),
    }


def run_murphy_checklist(data: dict[str, Any]) -> list[dict[str, str]]:
    """第 19 章大会串（A 股裁剪 16 项）。"""
    if not data.get("available"):
        return []

    trend = data["trend"]
    osc = data["oscillators"]
    ma = data["ma_boll"]
    vol = data["volume_price"]
    candle = data["candlestick"]
    pat = data["price_patterns"]
    data["capital_flow"]
    rs = data.get("relative_strength", {})
    mtf = trend.get("multi_timeframe") or {}
    dow = trend.get("dow_confirmation") or {}
    breadth = trend.get("market_breadth") or {}

    def _item(n: int, topic: str, status: str, detail: str) -> dict[str, str]:
        return {"#": str(n), "topic": topic, "status": status, "detail": detail}

    items: list[dict[str, str]] = []

    # 1 总体市场
    if dow.get("available"):
        items.append(_item(1, "总体市场方向", dow.get("state_cn", ""), "; ".join(dow.get("notes", [])[:2])))
    else:
        items.append(_item(1, "总体市场方向", "未验证", "请 sync_market"))

    # 2 板块 — MVP
    items.append(_item(2, "板块/行业相对强弱", "未验证", "需行业数据或人工"))

    # 3 多周期
    if mtf.get("available"):
        items.append(_item(3, "周线/月线同向", mtf.get("alignment_cn", ""), "; ".join(mtf.get("notes", [])[:3])))
    else:
        items.append(_item(3, "周线/月线同向", "未验证", ""))

    # 4 主趋势
    items.append(_item(4, "主/次趋势", trend.get("trend", ""), trend.get("regime", "")))

    # 5 支撑阻力
    sup, res = trend.get("support"), trend.get("resistance")
    items.append(_item(5, "支撑/阻力", "已标注" if sup and res else "未验证", f"支撑{sup} 阻力{res}"))

    # 6 趋势线/管道
    items.append(_item(6, "趋势线/管道", "人工读图", "程序未自动画线，对照K线"))

    # 7 成交量
    items.append(_item(7, "成交量验证", vol.get("bias", ""), "; ".join(vol.get("notes", [])[:2])))

    # 8 回撤
    ret = trend.get("retracements") or {}
    items.append(_item(8, "回撤位", "已算" if ret else "未验证", str(list(ret.keys())[:3]) if ret else ""))

    # 9 缺口
    gaps = [p for p in pat.get("patterns", []) if "gap" in p]
    items.append(_item(9, "缺口", "有" if gaps else "无显著", ", ".join(gaps[:2]) if gaps else ""))

    # 10 反转形态
    rev = candle.get("patterns") or pat.get("patterns", [])
    rev_f = [p for p in rev if "top" in p or "bottom" in p or "evening" in p or "morning" in p or "head" in p]
    items.append(_item(10, "反转形态", "有" if rev_f else "无", ", ".join(rev_f[:3]) if rev_f else ""))

    # 11 持续形态
    cont = [p for p in pat.get("patterns", []) if "flag" in p or "triangle" in p or "rectangle" in p]
    items.append(_item(11, "持续形态", "有" if cont else "无", ", ".join(cont[:3]) if cont else ""))

    # 12 量度目标
    items.append(_item(12, "形态量度目标", "人工", "见 price_patterns Skill 量度节"))

    # 13 均线
    items.append(_item(13, "均线方向", ma.get("bias", ""), "; ".join(ma.get("notes", [])[:2])))

    # 14 振荡区
    prep = osc.get("prep_zone", {}).get("state_cn", "")
    items.append(_item(14, "振荡超买/超卖/预备区", prep, f"market_mode={osc.get('market_mode')}"))

    # 15 背离 — 从 notes 粗判
    div_note = next((n for n in osc.get("notes", []) if "背离" in n), "未见明确背离")
    items.append(_item(15, "振荡背离", "关注" if "背离" in div_note else "无", div_note))

    # 16 反意见
    items.append(_item(16, "反意见极端", "无A股标准数据", "仅作环境提示，见 ta-oscillators"))

    # RS 扩展
    if rs.get("available"):
        items.append(_item("—", "个股相对强度", rs.get("bias_cn", ""), rs.get("note", "")))

    if breadth.get("available"):
        items.append(_item("—", "涨跌家数广度", breadth.get("bias", ""), breadth.get("notes", [""])[0]))

    return items


def synthesize_advice(data: dict[str, Any]) -> dict[str, Any]:
    """Rule-based Murphy-weighted stance and actionable brief (非自动交易指令)。"""
    if not data.get("available"):
        return {"stance": "无法分析", "confidence": "低", "reasons": [data.get("error", "")]}

    trend = data["trend"]
    regime = trend.get("regime", "unknown")
    score = 0.0
    reasons: list[str] = []
    actions: list[str] = []
    risks: list[str] = []
    unverified: list[str] = []

    def add_score(domain: str, bias: str, weight: float = 1.0) -> None:
        nonlocal score
        b = _BIAS_SCORE.get(bias, 0.0)
        score += b * weight
        if b != 0:
            reasons.append(f"{domain}：{bias}（权重×{weight}）")

    # 环境约束（墨菲：趋势与环境第一）
    env_weight = 1.5
    if regime == "panic":
        score -= 2.0
        risks.append("恐慌态：暂停追势，等缩量止跌 2～3 日再评估")
    elif regime == "bear":
        score -= 0.8
        risks.append("熊市/弱环境：做多信号一律降权，超卖不等于抄底")
    elif regime == "range":
        env_weight = 0.7
        actions.append("震荡市：重区间边界，趋势突破须放量+收盘确认")
    elif regime == "bull":
        score += 0.5
        reasons.append("环境：牛市/偏多 regime，趋势信号可加权")

    dow = trend.get("dow_confirmation") or {}
    if dow.get("available"):
        if dow.get("state") in ("divergence_bull", "divergence_bear"):
            score *= 0.7
            risks.append(f"道氏双指数：{dow.get('state_cn')}，突破类结论降置信度")
        elif dow.get("state") == "bear_confirmed":
            score -= 0.5
            risks.append("大盘双指数同弱，个股做多需相对强度支撑")
    else:
        unverified.append("道氏双指数未 sync")

    breadth = trend.get("market_breadth") or {}
    if breadth.get("available") and breadth.get("bias") == "bearish":
        score -= 0.4
        risks.append("市场广度偏空：指数失真风险，不宜仅凭指数做多")

    rs = data.get("relative_strength") or {}
    if rs.get("available"):
        if rs.get("bias") == "strong":
            score += 0.4
            reasons.append(f"相对强度：{rs.get('note')}")
        elif rs.get("bias") == "weak":
            score -= 0.4
            risks.append(f"相对强度偏弱：{rs.get('note')}")

    mtf = trend.get("multi_timeframe") or {}
    if mtf.get("alignment") == "aligned" and mtf.get("daily_trend") == "uptrend":
        score += 0.5
        reasons.append("日/周/月（月线为 resample 近似）同向偏多")
    elif mtf.get("alignment") == "aligned_bear":
        score -= 0.5
        risks.append("多周期偏空共振（月线为 resample 近似，结论降权）")
    elif mtf.get("alignment") == "mixed":
        actions.append("多周期不一致：以长期方向为准，短线信号仅作观察")

    add_score("趋势结构", "bullish" if trend.get("trend") == "uptrend" else "bearish" if trend.get("trend") == "downtrend" else "neutral", env_weight)
    add_score("摆动指标", data["oscillators"].get("bias", "neutral"), 1.0)
    add_score("均线布林", data["ma_boll"].get("bias", "neutral"), 1.0)
    add_score("量价", data["volume_price"].get("bias", "neutral"), 1.2)
    add_score("价格形态", data["price_patterns"].get("bias", "neutral"), 1.0)
    add_score("蜡烛图", data["candlestick"].get("bias", "neutral"), 0.8)

    flow = data["capital_flow"]
    if flow.get("available"):
        add_score("资金面", flow.get("bias", "neutral"), 0.9)
    else:
        unverified.append("资金面数据缺失")

    data["oscillators"].get("prep_zone", {})
    if data["candlestick"].get("patterns_rejected"):
        risks.append("部分蜡烛反转形态未过预备区，已过滤（第12章）")

    if data["volume_price"].get("stage") == "blow_off":
        risks.append("胀爆/高位巨量预警：不宜追价")
    if data["volume_price"].get("stage") == "selling_climax":
        actions.append("抛售高潮区：关注是否恐慌出清，但仍需结构确认再行动")

    sup, res = trend.get("support"), trend.get("resistance")
    if sup and res:
        actions.append(f"关键位：支撑区约 {sup:.2f}，阻力区约 {res:.2f}（收盘确认突破/跌破）")
    if trend.get("retrace_near"):
        actions.append(f"价格贴近 {trend['retrace_near']} 回撤区，观察是否趋势内调整")

    # 立场
    if score >= 1.5:
        stance = "偏多观察（顺势）"
        confidence = "中高" if score >= 2.5 and len(unverified) <= 1 else "中"
    elif score <= -1.5:
        stance = "偏空观察（顺势或防风险）"
        confidence = "中高" if score <= -2.5 and len(unverified) <= 1 else "中"
    else:
        stance = "中性观望"
        confidence = "中" if len(unverified) <= 2 else "低"

    if unverified:
        confidence = "低" if len(unverified) >= 3 else confidence
        actions.append("先补数据：" + "；".join(unverified))

    actions.append("非买卖指令：须结合持仓周期、风控与基本面；突破用收盘价确认")

    return {
        "stance": stance,
        "confidence": confidence,
        "score": round(score, 2),
        "reasons": reasons,
        "actions": actions,
        "risks": risks,
        "unverified": unverified,
    }


def render_advice_report(symbol: str) -> str:
    """Human-readable report with Murphy checklist + advice section."""
    data = collect_analysis(symbol)
    if not data.get("available"):
        return data.get("error", "分析失败")

    checklist = run_murphy_checklist(data)
    advice = synthesize_advice(data)
    base = render_report(symbol)

    lines = [
        base,
        "",
        "## 墨菲大会串核对（第19章）",
        "",
        "| # | 检查项 | 状态 | 说明 |",
        "|---|--------|------|------|",
    ]
    for row in checklist:
        lines.append(f"| {row['#']} | {row['topic']} | {row['status']} | {row['detail'][:80]} |")

    unverified_lines = [f"- {u}" for u in advice.get("unverified", [])] or ["- 无"]

    lines.extend(
        [
            "",
            "## 投研建议（基于全书 in-scope 技巧综合，非自动交易）",
            "",
            f"**综合立场**：{advice['stance']}",
            f"**置信度**：{advice['confidence']}（多因素得分 {advice.get('score', 0)}）",
            "",
            "### 依据（多 Skill 共振）",
            *[f"- {r}" for r in advice.get("reasons", [])],
            "",
            "### 建议动作",
            *[f"- {a}" for a in advice.get("actions", [])],
            "",
            "### 风险与过滤",
            *[f"- {r}" for r in advice.get("risks", [])],
            "",
            "### 数据缺口",
            *unverified_lines,
            "",
            "> Agent 深度解读：`python scripts/advise_symbol.py <code> --agent`",
        ]
    )
    return "\n".join(lines)


def build_agent_prompt(symbol: str, include_skills: bool = True) -> str:
    """Full context for local LLM (Ollama): Skills + structured JSON + instruction."""
    data = collect_analysis(symbol)
    if not data.get("available"):
        return data.get("error", "")

    checklist = run_murphy_checklist(data)
    advice = synthesize_advice(data)

    # Strip non-serializable snapshot
    payload = {k: v for k, v in data.items() if k not in ("snapshot",)}
    payload["checklist"] = checklist
    payload["rule_advice"] = advice

    parts = [
        "# 角色",
        "你是墨菲《金融市场技术分析》风格的 A 股投研助手。",
        "你必须严格依据下方【Skill 判据】与【结构化数据】综合推理，禁止编造未给出的数值。",
        "缺数据项须写「未验证」，不得假装有 AD 线、反意见指数、SAR 等缺失字段。",
        "输出须包含：环境约束 → 多周期 → 各域分析 → 大会串核对 → 综合立场 → 具体观察/等待/风险点。",
        "禁止直接给出「买入/卖出」指令；用「偏多观察/偏空观察/观望/降权」等表述。",
        "",
        "# 结构化数据（modules 计算）",
        "```json",
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        "```",
        "",
    ]

    if include_skills:
        parts.append("# Skill 判据（运行时唯一依据）")
        for name in runtime_skill_names():
            parts.append(f"\n## Skill: {name}\n")
            parts.append(load_skill(name))

    parts.extend(
        [
            "",
            "# 请输出",
            f"针对 {data['symbol']}（{data['trade_date']}）的完整技术分析建议报告（中文 Markdown）。",
        ]
    )
    return "\n".join(parts)


def collect_market_context(*, refresh_breadth: bool = False) -> dict[str, Any]:
    """大盘环境（不依赖个股）。"""
    if refresh_breadth:
        sync_market_breadth()
    dow = analyze_dow_confirmation()
    breadth = analyze_market_breadth()
    times = market_data_as_of()
    breadth_as_of = times.get("breadth") or {}
    return {
        "dow": dow,
        "breadth": breadth,
        "data_as_of": times,
        "breadth_as_of_date": breadth_as_of.get("as_of_date"),
        "breadth_as_of_label": breadth_as_of.get("as_of_label"),
        "notes": [
            *(dow.get("notes") or [])[:2],
            *((breadth.get("notes") or [])[:1]),
        ],
    }


def summarize_symbol(data: dict[str, Any], *, holding: dict[str, Any] | None = None) -> dict[str, Any]:
    """紧凑摘要，供组合/问答使用（非长报告）。"""
    if not data.get("available"):
        return {"symbol": data.get("symbol", "?"), "available": False, "error": data.get("error")}

    advice = synthesize_advice(data)
    trend = data["trend"]
    pat = data["price_patterns"].get("patterns") or []
    candle = data["candlestick"].get("patterns") or []
    candle_detail = data.get("candlestick") or {}

    brief: dict[str, Any] = {
        "available": True,
        "symbol": data["symbol"],
        "name": (holding or {}).get("name"),
        "trade_date": data["trade_date"],
        "data_as_of_date": data.get("data_as_of_date"),
        "data_as_of_label": data.get("data_as_of_label"),
        "data_as_of": data.get("data_as_of"),
        "stance": advice["stance"],
        "score": advice["score"],
        "confidence": advice["confidence"],
        "regime": trend.get("regime"),
        "trend": trend.get("trend"),
        "support": trend.get("support"),
        "resistance": trend.get("resistance"),
        "reasons": advice["reasons"][:4],
        "risks": advice["risks"][:4],
        "actions": advice["actions"][:3],
        "unverified": advice["unverified"],
        "prep_zone": data["oscillators"].get("prep_zone", {}).get("state_cn"),
        "osc_bias": data["oscillators"].get("bias"),
        "ma_bias": data["ma_boll"].get("bias"),
        "volume_bias": data["volume_price"].get("bias"),
        "patterns": (pat + candle)[:5],
        "candle_bars": candle_detail.get("bars") or [],
        "candle_patterns_cn": candle_detail.get("patterns_cn") or [],
        "candle_notes": (candle_detail.get("notes") or [])[:3],
        "candle_prep_filter": candle_detail.get("prep_filter"),
        "relative_strength": (data.get("relative_strength") or {}).get("note"),
    }
    if holding:
        from modules.portfolio import position_summary

        pos = position_summary(holding)
        brief["position"] = {
            "quantity": pos.get("quantity"),
            "cost_price": pos.get("cost_price"),
            "last_close": pos.get("last_close"),
            "pnl_pct": pos.get("pnl_pct"),
            "weight_pct": None,
        }
    return brief


def collect_portfolio_analysis(symbols: list[str] | None = None) -> dict[str, Any]:
    """批量分析持仓或指定代码列表。"""
    pf = portfolio_summary()
    positions = pf["positions"]
    if symbols:
        norm = {_normalize_portfolio_symbol(s) for s in symbols}
        positions = [p for p in positions if p["symbol"] in norm]
        if not positions:
            positions = [{"symbol": _normalize_portfolio_symbol(s), "quantity": 0} for s in symbols]

    items: list[dict[str, Any]] = []
    for pos in positions:
        sym = pos["symbol"]
        data = collect_analysis(sym)
        holding = get_holding_dict(pos)
        brief = summarize_symbol(data, holding=holding)
        w = pf["weights_pct"].get(sym)
        if w is not None and brief.get("position"):
            brief["position"]["weight_pct"] = w
        items.append(brief)

    market = collect_market_context()
    return {
        "portfolio": {
            "count": len(items),
            "total_market_value": pf.get("total_market_value"),
            "total_pnl": pf.get("total_pnl"),
            "weights_pct": pf.get("weights_pct"),
        },
        "market": market,
        "symbols": items,
    }


def _normalize_portfolio_symbol(symbol: str) -> str:
    return _normalize_symbol(symbol)


def get_holding_dict(pos: dict[str, Any]) -> dict[str, Any]:
    return {k: pos[k] for k in ("symbol", "name", "quantity", "cost_price", "buy_date", "notes") if k in pos}


def synthesize_portfolio_advice(bundle: dict[str, Any]) -> dict[str, Any]:
    """组合层规则建议（优先级排序，非长报告）。"""
    items = [s for s in bundle.get("symbols", []) if s.get("available")]
    if not items:
        return {
            "stance": "无法分析",
            "confidence": "低",
            "environment": "无持仓或无数据",
            "priorities": [],
            "watch": [],
            "relief": [],
            "portfolio_actions": ["请先录入持仓：python scripts/portfolio_cli.py add <code>"],
        }

    market = bundle.get("market") or {}
    dow = market.get("dow") or {}
    breadth = market.get("breadth") or {}

    env_risks: list[str] = []
    if breadth.get("bias") == "bearish":
        env_risks.append("市场广度偏空，指数可能失真")
    if dow.get("state") in ("divergence_bull", "divergence_bear"):
        env_risks.append(f"道氏双指数：{dow.get('state_cn', '背离')}")

    weights = bundle.get("portfolio", {}).get("weights_pct") or {}

    def urgency(s: dict) -> float:
        w = weights.get(s["symbol"], 100 / max(len(items), 1)) / 100
        return -float(s.get("score") or 0) * (0.5 + w)

    ranked = sorted(items, key=urgency, reverse=True)
    avg_score = sum(s.get("score") or 0 for s in items) / len(items)

    priorities: list[str] = []
    for s in ranked[:3]:
        w = weights.get(s["symbol"])
        label = s.get("name") or s["symbol"]
        priorities.append(
            f"{label}（{s['stance']}，得分{s.get('score')}，"
            f"仓位{w}%）— {'；'.join(s.get('risks', [])[:2]) or '见各域信号'}"
        )

    watch = [f"{s.get('name') or s['symbol']}：{s.get('risks', ['无'])[0]}" for s in ranked[:3] if s.get("risks")]
    relief = [
        f"{s.get('name') or s['symbol']}：{s['stance']}"
        for s in sorted(items, key=lambda x: x.get("score") or 0, reverse=True)[:2]
        if (s.get("score") or 0) >= 1.0
    ]

    if avg_score >= 0.8:
        stance = "组合整体偏多观察"
    elif avg_score <= -0.8:
        stance = "组合整体偏空，优先控风险"
    else:
        stance = "组合分化，按标的分别处理"

    actions = [
        "重配关注：得分最低且仓位重的标的优先复核",
        "环境约束：弱广度/背离时，突破信号一律降权",
        "非买卖指令：结合成本线与持仓周期决策",
    ]
    if env_risks:
        actions = env_risks + actions

    missing = sorted({u for s in items for u in s.get("unverified", [])})
    if missing:
        actions.append("数据缺口：" + "；".join(missing[:3]))

    return {
        "stance": stance,
        "confidence": "中" if len(missing) <= 2 else "低",
        "avg_score": round(avg_score, 2),
        "environment": "; ".join(env_risks) or (dow.get("state_cn") or "见大盘 sync"),
        "priorities": priorities,
        "watch": watch,
        "relief": relief,
        "portfolio_actions": actions,
    }


def render_portfolio_brief(symbols: list[str] | None = None) -> str:
    """短格式组合建议（可读性优先，非研报）。"""
    bundle = collect_portfolio_analysis(symbols)
    advice = synthesize_portfolio_advice(bundle)
    pf = bundle["portfolio"]
    lines = [
        f"## 组合速览（{pf.get('count', 0)} 只）",
        "",
        f"**整体**：{advice['stance']}（均分 {advice.get('avg_score')}，置信 {advice['confidence']}）",
        f"**环境**：{advice['environment']}",
    ]
    if pf.get("total_market_value"):
        lines.append(f"**总市值**：{pf['total_market_value']}（浮盈 {pf.get('total_pnl', '—')}）")
    lines.extend(["", "**优先关注**"])
    if advice["priorities"]:
        lines.extend(f"- {p}" for p in advice["priorities"])
    else:
        lines.append("- 无")
    lines.extend(["", "**各标的**"])
    for s in bundle["symbols"]:
        if not s.get("available"):
            lines.append(f"- {s.get('symbol')}：{s.get('error', '无数据')}")
            continue
        pos = s.get("position") or {}
        pnl = f"，浮盈{pos['pnl_pct']}%" if pos.get("pnl_pct") is not None else ""
        w = f"，仓位{pos['weight_pct']}%" if pos.get("weight_pct") else ""
        lines.append(
            f"- {s.get('name') or s['symbol']}：{s['stance']}（{s.get('score')}）{pnl}{w}"
        )
        if s.get("risks"):
            lines.append(f"  风险：{s['risks'][0]}")
    lines.extend(
        [
            "",
            "**组合动作**",
            *[f"- {a}" for a in advice["portfolio_actions"]],
            "",
            "> 对话式深度分析：`python scripts/chat_advisor.py`",
        ]
    )
    return "\n".join(lines)


def build_chat_context(
    *,
    scope: str = "portfolio",
    symbol: str | None = None,
    symbols: list[str] | None = None,
) -> dict[str, Any]:
    """问答会话用的紧凑上下文（非报告体）。"""
    from modules.portfolio import get_holding

    market = collect_market_context()
    if scope == "symbol" and symbol:
        data = collect_analysis(symbol)
        holding = get_holding(symbol)
        return {
            "scope": "symbol",
            "market": market,
            "focus": summarize_symbol(data, holding=holding),
            "checklist": run_murphy_checklist(data) if data.get("available") else [],
        }

    if scope == "open":
        return {
            "scope": "open",
            "market": market,
            "symbols": [],
            "note": "用户未勾选标的，按提问匹配板块与市场数据",
        }

    bundle = collect_portfolio_analysis(symbols)
    return {
        "scope": "portfolio",
        "market": market,
        "portfolio": bundle["portfolio"],
        "symbols": bundle["symbols"],
        "rule_advice": synthesize_portfolio_advice(bundle),
    }
