"""Keyword / slot extraction and targeted data retrieval."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from modules.advisor import collect_analysis, collect_market_context, summarize_symbol
from modules.data_fetcher import _normalize_symbol
from modules.data_timestamps import _now_label, collect_reference_meta, symbol_data_as_of
from modules.portfolio import get_holding, list_holdings
from modules.sector_data import build_sector_pick_summary, collect_sector_rankings

_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")

_INTENT_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("market", ("大盘", "市场", "指数", "两市", "环境", "成交额", "涨跌家数", "上证", "深证", "创业板")),
    ("sector", ("板块", "行业", "主线", "龙头", "题材", "概念")),
    ("capital_flow", ("资金", "流入", "流出", "北向", "主力", "融资", "龙虎榜")),
    ("risk", ("风险", "回调", "利空", "下跌", "破位")),
    ("opportunity", ("机会", "利好", "上涨", "突破", "潜力", "看好", "推荐", "值得关注", "强势")),
    ("tech", ("macd", "rsi", "均线", "布林", "kdj", "cci", "adx", "技术", "指标", "形态", "趋势")),
    ("compare", ("对比", "比较", "哪个好", "强弱", "排名")),
]

_CHART_HINTS = ("走势", "图表", "图形", "k线", "曲线", "画图", "趋势图", "股价", "收盘")

_SECTOR_HINTS = (
    "板块", "行业", "主线", "龙头", "题材", "概念",
    "石油", "半导体", "新能源", "银行", "军工", "贵金属", "黄金", "电网",
    "通信", "煤炭", "医药", "白酒", "消费", "科技", "人工智能", "电力",
    "汽车", "锂电", "光伏", "钢铁", "有色", "地产", "券商", "保险", "农业",
    "食品", "饮料", "纺织", "化工", "机械", "传媒", "游戏", "航运", "建筑",
    "建材", "环保", "存储", "芯片", "算力", "机器人", "电力设备",
)

_WORKFLOW_SECTOR = frozenset({
    "sector_deep_dive", "risk_scan", "opportunity_scan", "question_deep_dive",
})
_WORKFLOW_MARKET = frozenset({
    "market_overview", "risk_scan", "opportunity_scan", "question_deep_dive", "capital_flow",
})


def _match_sectors_in_message(message: str) -> list[str]:
    hits = [h for h in _SECTOR_HINTS if len(h) >= 2 and h in message]
    return list(dict.fromkeys(hits))[:8]


_SECTOR_PICK_HINTS = (
    "哪个板块", "哪些板块", "什么板块", "哪块板块", "买什么板块", "选哪个板块",
    "看好", "推荐", "值得关注", "有机会", "主线在哪", "主线是什么",
)


def _wants_sector_pick(message: str) -> bool:
    msg = (message or "").strip()
    if not any(h in msg for h in _SECTOR_PICK_HINTS):
        return False
    return any(h in msg for h in ("板块", "行业", "主线", "概念", "赛道"))


def _detect_workflow(message: str, intents: list[str], plan: QueryPlan) -> str:
    if plan.wants_sector_pick:
        return "opportunity_scan"
    if plan.sector_only:
        return "sector_deep_dive"
    if plan.symbols and not plan.portfolio_focus:
        return "named_symbols"
    if _explicit_market_question(message, intents) and "sector" not in intents:
        return "market_overview"
    if "sector" in intents or plan.matched_sectors:
        return "sector_deep_dive"
    if "risk" in intents:
        return "risk_scan"
    if "opportunity" in intents:
        return "opportunity_scan"
    if "capital_flow" in intents:
        return "capital_flow"
    if "compare" in intents:
        return "sector_deep_dive"
    return "question_deep_dive"

_PORTFOLIO_VERBS = (
    "分析", "怎么样", "如何", "评价", "看看", "诊断", "建议", "走势",
    "涨幅", "下跌", "突破", "支撑", "阻力", "持有", "自选", "标的",
)


def _explicit_market_question(message: str, intents: list[str]) -> bool:
    if "market" in intents:
        return True
    return any(k in message for k in ("大盘", "市场", "指数", "两市", "上证", "深证", "创业板", "成交额", "涨跌家数"))


def _wants_portfolio_analysis(message: str, intents: list[str]) -> bool:
    if any(v in message for v in _PORTFOLIO_VERBS):
        return True
    return not intents and len(message) <= 16


@dataclass
class QueryPlan:
    keywords: list[str] = field(default_factory=list)
    intents: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    needs_market: bool = False
    needs_sectors: bool = False
    sector_only: bool = False
    portfolio_focus: bool = False
    question_driven: bool = False
    workflow: str = ""
    wants_sector_pick: bool = False
    matched_sectors: list[str] = field(default_factory=list)
    needs_charts: bool = False
    chart_kinds: list[str] = field(default_factory=list)


def _sector_only_query(message: str, intents: list[str]) -> bool:
    """Broad sector/market question without naming specific tickers."""
    if "sector" not in intents and not any(h in message for h in _SECTOR_HINTS):
        return False
    if _CODE_RE.search(message):
        return False
    for h in list_holdings():
        nm = (h.get("name") or "").strip()
        if len(nm) >= 2 and nm in message:
            return False
    return True


def plan_query(message: str, selected: list[str] | None = None) -> QueryPlan:
    msg = (message or "").strip()
    lower = msg.lower()
    plan = QueryPlan()

    for intent, kws in _INTENT_RULES:
        if any(k in msg or k in lower for k in kws):
            plan.intents.append(intent)
            plan.keywords.extend([k for k in kws if k in msg or k in lower])

    codes: set[str] = set()
    for m in _CODE_RE.finditer(msg):
        codes.add(m.group(1).split(".")[0])

    plan.sector_only = _sector_only_query(msg, plan.intents)
    if not plan.sector_only:
        for sel in selected or []:
            codes.add(sel.split(".")[0])
        for h in list_holdings():
            nm = h.get("name") or ""
            if len(nm) >= 2 and nm in msg:
                codes.add(h["symbol"].split(".")[0])

    plan.symbols = sorted(codes)
    plan.needs_sectors = plan.sector_only or "sector" in plan.intents
    plan.needs_market = (
        "market" in plan.intents
        or plan.needs_sectors
        or plan.sector_only
        or not plan.symbols
    )
    plan.needs_charts = any(h in msg for h in _CHART_HINTS) or "tech" in plan.intents

    if plan.needs_charts:
        plan.chart_kinds.append("price")
        if any(k in lower for k in ("rsi", "指标", "macd")):
            plan.chart_kinds.append("rsi")

    if (selected or []) and not plan.sector_only:
        sel_codes = {s.split(".")[0] for s in selected or []}
        plan.symbols = sorted(set(plan.symbols) | sel_codes)
        broad_market = _explicit_market_question(msg, plan.intents)
        wants_portfolio = _wants_portfolio_analysis(msg, plan.intents)
        if wants_portfolio and not broad_market and "sector" not in plan.intents:
            plan.portfolio_focus = True
            plan.needs_market = False
            plan.needs_sectors = False
    else:
        plan.question_driven = True
        plan.wants_sector_pick = _wants_sector_pick(msg)
        plan.matched_sectors = _match_sectors_in_message(msg)
        plan.workflow = _detect_workflow(msg, plan.intents, plan)
        if plan.workflow in _WORKFLOW_SECTOR or plan.matched_sectors:
            plan.needs_sectors = True
        if plan.workflow in _WORKFLOW_MARKET:
            plan.needs_market = True
        if plan.workflow == "named_symbols" and plan.symbols:
            plan.needs_market = bool("market" in plan.intents)
        elif plan.workflow == "question_deep_dive":
            plan.needs_market = True
            plan.needs_sectors = True

    plan.keywords = list(dict.fromkeys(plan.keywords))[:12]
    return plan


def _compact_symbol(symbol: str) -> dict[str, Any]:
    sym = _normalize_symbol(symbol)
    data = collect_analysis(sym)
    if not data.get("available"):
        return {"symbol": sym, "available": False, "error": data.get("error")}
    holding = get_holding(sym)
    brief = summarize_symbol(data, holding=holding)
    times = data.get("data_as_of") or symbol_data_as_of(sym)
    quote = times.get("quote") or {}
    display_name = ((holding or {}).get("name") or "").strip() or sym.split(".")[0]
    candle = data.get("candlestick") or {}
    return {
        "symbol": sym,
        "name": display_name,
        "available": True,
        "trade_date": data.get("trade_date"),
        "data_as_of_date": quote.get("as_of_date"),
        "data_as_of_label": quote.get("as_of_label"),
        "data_as_of": times,
        "summary": brief,
        "candle_bars": candle.get("bars") or [],
        "candle_patterns_cn": candle.get("patterns_cn") or [],
        "candle_notes": (candle.get("notes") or [])[:3],
        "trend_regime": (data.get("trend") or {}).get("regime"),
        "osc_bias": (data.get("oscillators") or {}).get("bias"),
        "capital_flow_note": (data.get("capital_flow") or {}).get("note"),
    }


def fetch_data_for_plan(plan: QueryPlan, fallback_symbols: list[str] | None = None) -> dict[str, Any]:
    """Call domain APIs based on extracted slots."""
    symbols = list(plan.symbols)
    if (
        not symbols
        and fallback_symbols
        and not plan.sector_only
        and not plan.question_driven
    ):
        symbols = list(fallback_symbols)
    if (
        not symbols
        and fallback_symbols is None
        and not plan.sector_only
        and not plan.needs_sectors
        and not plan.question_driven
    ):
        symbols = [h["symbol"].split(".")[0] for h in list_holdings()]

    meta_symbols = None if plan.sector_only else (symbols if symbols else None)
    skip_meta_live = bool(plan.portfolio_focus and symbols)
    payload: dict[str, Any] = {
        "retrieved_at": _now_label(),
        "query_keywords": plan.keywords,
        "query_intents": plan.intents,
        "sector_only": plan.sector_only,
        "portfolio_focus": plan.portfolio_focus,
        "question_driven": plan.question_driven,
        "workflow": plan.workflow,
        "wants_sector_pick": plan.wants_sector_pick,
        "matched_sectors": plan.matched_sectors,
        "data_reference": collect_reference_meta(
            meta_symbols,
            include_live=not plan.sector_only and not skip_meta_live,
        ),
    }

    if not plan.portfolio_focus and (plan.needs_market or plan.sector_only or not symbols):
        from modules.realtime_quotes import get_live_quote

        market = collect_market_context(refresh_breadth=plan.sector_only or plan.needs_market)
        index_live = get_live_quote("INDEX.SH000001")
        if index_live and index_live.get("available"):
            market["index_live"] = index_live
        payload["market"] = market

    if plan.needs_sectors:
        sectors = collect_sector_rankings()
        payload["sectors"] = sectors
        if plan.wants_sector_pick:
            payload["sector_picks"] = build_sector_pick_summary(sectors)

    if symbols and not plan.sector_only:
        from modules.realtime_quotes import attach_live_to_symbol_payload, get_live_quotes

        norm_syms = [_normalize_symbol(s) for s in symbols[:8]]
        live_map = get_live_quotes(norm_syms)
        payload["symbols"] = [
            attach_live_to_symbol_payload(
                _compact_symbol(s),
                s,
                live=live_map.get(_normalize_symbol(s)),
            )
            for s in symbols[:8]
        ]

    return payload


def format_sector_pick_hint(picks: dict[str, Any]) -> str:
    """Plain-text hint so LLM opens with direct sector names."""
    if not picks or not picks.get("available"):
        return ""
    lines = ["【板块形态优选·请据此直接回答】"]
    lines.append("排序依据：领涨股股价形态+趋势+板块广度，不是单纯今日涨幅。")
    for i, row in enumerate(picks.get("top_picks") or [], 1):
        name = row.get("name") or ""
        btype = row.get("board_type") or ""
        reason = row.get("pick_reason") or ""
        lead = row.get("lead_stock") or ""
        try:
            pct_s = f"{float(row.get('change_pct')):.2f}%"
        except (TypeError, ValueError):
            pct_s = ""
        extra = f"，今日涨{pct_s}" if pct_s else ""
        lead_part = f"，领涨{lead}" if lead else ""
        lines.append(f"{i}. {name}（{btype}）{reason}{lead_part}{extra}")
    scanned = picks.get("scanned") or {}
    lines.append(
        f"已扫描行业{scanned.get('industry_count') or '?'}个+概念{scanned.get('concept_count') or '?'}个板块，"
        f"对前{scanned.get('ta_scanned') or 8}名做了形态分析。"
    )
    lines.append(
        "用户问哪个板块看好：第一句话直接说第1～3个板块名，并用形态/趋势说理由；"
        "禁止只按涨幅回答、禁止先讲大盘、禁止只列回避板块。"
    )
    return "\n".join(lines)


def format_fetch_block(data: dict[str, Any]) -> str:
    return "【本轮检索数据】\n```json\n" + json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n```"
