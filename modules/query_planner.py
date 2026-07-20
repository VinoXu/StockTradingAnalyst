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
from modules.runtime_cache import get_or_set
from modules.sector_data import build_sector_pick_summary, collect_sector_rankings
from modules.sector_period import (
    build_sector_period_rank,
    infer_sector_lookback_trading_days,
    wants_sector_period_rank,
)

_SECTOR_CACHE_TTL = 120.0
_SECTOR_PERIOD_CACHE_TTL = 1800.0  # 区间榜拉取成本高，半小时复用
_MARKET_CACHE_TTL = 60.0

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

_NEWS_PULSE_HINTS = (
    "为什么涨", "为什么跌", "为何涨", "为何跌", "怎么回事", "什么原因",
    "异动", "大跌", "大涨", "暴跌", "暴涨", "涨停", "跌停", "跳水", "暴雷", "冲高回落",
)

_DYP_ASK_HINTS = (
    "本质是什么", "本质是啥", "10年后", "十年后", "还在吗", "护城河",
    "好生意", "差生意", "段永平", "生意模式", "商业模式本质", "第一性原理",
)

_TA_SCREEN_HINTS = (
    "快筛", "六关", "过关", "去劣", "硬筛", "质量筛", "能否深研", "值不值得深研",
    "先筛", "淘汰规则", "红线", "checklist",
)

_PORTFOLIO_REVIEW_HINTS = (
    "组合复盘", "复盘组合", "持仓复盘", "仓位结构", "集中度", "再平衡",
    "组合风险", "持仓配置", "组合怎么样", "我的持仓", "看看组合", "组合诊断",
)


def _wants_sector_pick(message: str) -> bool:
    msg = (message or "").strip()
    if not any(h in msg for h in _SECTOR_PICK_HINTS):
        return False
    return any(h in msg for h in ("板块", "行业", "主线", "概念", "赛道"))


def _wants_news_pulse(message: str) -> bool:
    msg = (message or "").strip()
    return any(h in msg for h in _NEWS_PULSE_HINTS)


def _wants_dyp_ask(message: str) -> bool:
    msg = (message or "").strip()
    return any(h in msg for h in _DYP_ASK_HINTS)


def _wants_ta_screen(message: str) -> bool:
    msg = (message or "").strip().lower()
    return any(h in msg for h in _TA_SCREEN_HINTS)


def _wants_portfolio_review(message: str, plan: QueryPlan) -> bool:
    """显式组合复盘话术才走 portfolio-review；普通「分析持仓」仍走个股深研。"""
    msg = (message or "").strip()
    return any(h in msg for h in _PORTFOLIO_REVIEW_HINTS)


def _resolve_research_mode(plan: QueryPlan) -> str:
    """symbol_research: 个股+研报；sector_research: 板块无研报。"""
    # 专用 workflow 不占深研 mode，避免误跑 6 Agent / 误写 thesis
    if plan.workflow in ("news_pulse", "dyp_ask", "portfolio_review", "ta_screen"):
        return ""
    if plan.portfolio_focus and plan.workflow == "portfolio_review":
        return ""
    if plan.symbols and not plan.sector_only:
        return "symbol_research"
    if plan.sector_only or plan.wants_sector_pick:
        return "sector_research"
    if plan.needs_sectors and not plan.symbols and "sector" in plan.intents:
        return "sector_research"
    return ""


def _detect_workflow(message: str, intents: list[str], plan: QueryPlan) -> str:
    if _wants_news_pulse(message) and (plan.symbols or plan.sector_only or plan.matched_sectors):
        return "news_pulse"
    if _wants_dyp_ask(message):
        return "dyp_ask"
    if _wants_portfolio_review(message, plan):
        return "portfolio_review"
    if _wants_ta_screen(message) and (plan.symbols or plan.sector_only or plan.wants_sector_pick or plan.matched_sectors):
        return "ta_screen"
    mode = _resolve_research_mode(plan)
    if mode == "symbol_research":
        return "symbol_research"
    if mode == "sector_research":
        return "sector_research"
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
    # 区间板块排行：由语义窗口（如近两周）驱动，需拉指数并计算
    sector_lookback_days: int | None = None
    wants_sector_period_rank: bool = False
    needs_charts: bool = False
    chart_kinds: list[str] = field(default_factory=list)
    research_mode: str = ""
    # --- 语义规划（规则 + LLM）；映射表仅作参考 ---
    semantic_source: str = ""
    intent_summary: str = ""
    semantic_confidence: str = ""
    agent_roster_override: tuple[tuple[str, str], ...] = ()
    team_lead_skills_override: tuple[str, ...] = ()
    task_briefs: list[dict] = field(default_factory=list)
    semantic_fetch: dict = field(default_factory=dict)


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
        plan.workflow = _detect_workflow(msg, plan.intents, plan)
        plan.research_mode = _resolve_research_mode(plan)
    else:
        plan.question_driven = True
        plan.wants_sector_pick = _wants_sector_pick(msg)
        plan.matched_sectors = _match_sectors_in_message(msg)
        plan.workflow = _detect_workflow(msg, plan.intents, plan)
        plan.research_mode = _resolve_research_mode(plan)
        if plan.workflow in _WORKFLOW_SECTOR or plan.matched_sectors:
            plan.needs_sectors = True
        if plan.workflow in _WORKFLOW_MARKET:
            plan.needs_market = True
        if plan.workflow == "named_symbols" and plan.symbols:
            plan.needs_market = bool("market" in plan.intents)
        elif plan.workflow == "question_deep_dive":
            plan.needs_market = True
            plan.needs_sectors = True

    if plan.workflow == "portfolio_review":
        plan.needs_market = False
        plan.needs_sectors = False
        if not plan.symbols:
            plan.symbols = [h["symbol"].split(".")[0] for h in list_holdings()][:8]

    if plan.workflow == "dyp_ask":
        # 段式问答可不拉板块广度
        plan.needs_sectors = False

    lookback = infer_sector_lookback_trading_days(msg)
    plan.sector_lookback_days = lookback
    plan.wants_sector_period_rank = wants_sector_period_rank(msg, lookback=lookback)
    if plan.wants_sector_period_rank:
        plan.needs_sectors = True
        plan.sector_only = plan.sector_only or not plan.symbols
        # 区间排行 ≠ 板块优选：清掉 sector_research，避免 TA 扫描/重型编排
        if not plan.wants_sector_pick:
            plan.research_mode = ""
            if plan.workflow in ("", "question_deep_dive", "named_symbols", "sector_research"):
                plan.workflow = "sector_deep_dive"
        else:
            if not plan.workflow or plan.workflow in ("question_deep_dive", "named_symbols"):
                plan.workflow = "sector_deep_dive"
            if not plan.research_mode:
                plan.research_mode = "sector_research"

    plan.keywords = list(dict.fromkeys(plan.keywords))[:12]
    return plan


def _compact_symbol(symbol: str) -> dict[str, Any]:
    from modules.data_fetcher import resolve_stock_name

    sym = _normalize_symbol(symbol)
    data = collect_analysis(sym)  # 内部已在缺本地日K时自动 sync
    if not data.get("available"):
        return {
            "symbol": sym,
            "name": resolve_stock_name(sym) or sym.split(".")[0],
            "available": False,
            "error": data.get("error") or "行情同步失败",
        }
    holding = get_holding(sym)
    brief = summarize_symbol(data, holding=holding)
    times = data.get("data_as_of") or symbol_data_as_of(sym)
    quote = times.get("quote") or {}
    display_name = (
        ((holding or {}).get("name") or "").strip()
        or resolve_stock_name(sym)
        or sym.split(".")[0]
    )
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


def fetch_data_for_plan(
    plan: QueryPlan,
    fallback_symbols: list[str] | None = None,
    *,
    message: str = "",
) -> dict[str, Any]:
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
        "research_mode": plan.research_mode,
        "wants_sector_pick": plan.wants_sector_pick,
        "matched_sectors": plan.matched_sectors,
        "sector_lookback_days": plan.sector_lookback_days,
        "wants_sector_period_rank": plan.wants_sector_period_rank,
        "semantic_source": plan.semantic_source,
        "intent_summary": plan.intent_summary,
        "semantic_confidence": plan.semantic_confidence,
        "task_briefs": plan.task_briefs,
        "data_reference": collect_reference_meta(
            meta_symbols,
            include_live=not plan.sector_only and not skip_meta_live,
        ),
    }

    sf = plan.semantic_fetch or {}
    want_market = plan.needs_market if not sf else bool(sf.get("market", plan.needs_market))
    want_sectors = plan.needs_sectors if not sf else bool(sf.get("sectors") or sf.get("sector_picks") or plan.needs_sectors)

    if not plan.portfolio_focus and (want_market or plan.sector_only or not symbols):
        from modules.realtime_quotes import get_live_quote

        refresh_breadth = plan.sector_only or want_market
        cache_key = f"data:market:{'breadth' if refresh_breadth else 'basic'}"
        market = get_or_set(
            cache_key,
            _MARKET_CACHE_TTL,
            lambda: collect_market_context(refresh_breadth=refresh_breadth),
        )
        index_live = get_live_quote("INDEX.SH000001")
        index_live_sz = get_live_quote("INDEX.SZ399001")
        if (index_live and index_live.get("available")) or (index_live_sz and index_live_sz.get("available")):
            market = dict(market)
            if index_live and index_live.get("available"):
                market["index_live"] = index_live
            if index_live_sz and index_live_sz.get("available"):
                market["index_live_sz"] = index_live_sz
        payload["market"] = market

    if want_sectors:
        sectors = get_or_set(
            "data:sector_rankings",
            _SECTOR_CACHE_TTL,
            collect_sector_rankings,
        )
        payload["sectors"] = sectors
        want_picks = (
            plan.wants_sector_pick
            or (
                plan.research_mode == "sector_research"
                and not plan.wants_sector_period_rank
            )
            or plan.workflow == "ta_screen"
            or bool(sf.get("sector_picks"))
        )
        if want_picks:
            payload["sector_picks"] = get_or_set(
                "data:sector_picks",
                _SECTOR_CACHE_TTL,
                lambda: build_sector_pick_summary(sectors),
            )
        want_period = plan.wants_sector_period_rank or bool(sf.get("sector_period_rank"))
        lookback = plan.sector_lookback_days
        if want_period and not lookback:
            lookback = infer_sector_lookback_trading_days(message) or 10
        if want_period and lookback:
            concept_heavy = "概念" in (message or "") or "题材" in (message or "")
            days = int(lookback)
            # 默认只算行业全量；点名概念/题材再加样本（同花顺概念指数冷启动很慢）
            c_limit = 20 if concept_heavy else 0
            cache_key = f"data:sector_period:{days}:c{c_limit}"
            payload["sector_period_rank"] = get_or_set(
                cache_key,
                _SECTOR_PERIOD_CACHE_TTL,
                lambda d=days, lim=c_limit: build_sector_period_rank(
                    sectors,
                    trading_days=d,
                    include_concepts=lim > 0,
                    concept_limit=lim,
                ),
            )

    needs_participant = (
        want_sectors
        or want_market
        or plan.sector_only
        or "capital_flow" in plan.intents
        or plan.wants_sector_pick
    )
    # 纯区间排行不拉参与者资金流，减负
    if plan.wants_sector_period_rank and not plan.wants_sector_pick and "capital_flow" not in plan.intents:
        needs_participant = False
    if needs_participant:
        from modules.participant_flow import collect_market_participant_context

        # 仅用具体板块名匹配资金流；泛化意图词（如「概念」「板块」）会误命中大量板块
        _flow_kw_skip = frozenset({
            "板块", "行业", "主线", "龙头", "题材", "概念", "机会", "风险", "利好", "上涨",
            "突破", "潜力", "看好", "推荐", "值得关注", "强势", "资金", "流入", "流出",
            "北向", "主力", "融资", "龙虎榜", "回调", "利空", "下跌", "破位",
        })
        kws = [
            k
            for k in dict.fromkeys((plan.matched_sectors or []) + (plan.keywords or []))
            if k and len(k) >= 2 and k not in _flow_kw_skip
        ]
        payload["participant_flow"] = collect_market_participant_context(sector_keywords=kws)

    # 研报/财报：规则保底（个股深研必拉）∪ 语义 fetch 开关（语义关了也不能关掉深研保底）
    rule_symbol_research = (
        plan.research_mode == "symbol_research"
        and plan.workflow != "news_pulse"
        and bool(symbols)
        and not plan.sector_only
    )
    need_research = rule_symbol_research or bool(sf.get("research_reports"))
    need_fundamentals = (
        rule_symbol_research
        or bool(sf.get("fundamentals"))
        or (plan.workflow == "ta_screen" and bool(symbols) and not plan.sector_only)
    )
    if need_research and symbols and not plan.sector_only:
        from modules.research_reports import collect_research_for_symbols

        payload["research_reports"] = collect_research_for_symbols(symbols)
    if need_fundamentals and symbols and not plan.sector_only:
        from modules.fundamentals import collect_fundamentals_for_symbols

        payload["fundamentals"] = collect_fundamentals_for_symbols(symbols)

    need_holdings = bool(sf.get("holdings")) if sf else (plan.workflow == "portfolio_review" or plan.portfolio_focus)
    if need_holdings:
        holdings = list_holdings()
        payload["holdings"] = [
            {
                "symbol": h.get("symbol"),
                "name": h.get("name"),
                "qty": h.get("qty"),
                "cost": h.get("cost"),
            }
            for h in holdings[:30]
        ]

    need_news = bool(sf.get("news")) if sf else (plan.workflow == "news_pulse")
    if need_news:
        from modules.news_pulse import collect_news_pulse, parse_change_pct_from_message

        payload["news_pulse"] = collect_news_pulse(
            symbols=symbols if not plan.sector_only else None,
            sector_keywords=(plan.matched_sectors or plan.keywords or [])[:5],
            change_pct_hint=parse_change_pct_from_message(message),
        )

    want_quotes = True if not sf else bool(sf.get("quotes", True))
    if want_quotes and symbols and not plan.sector_only:
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
    lines = ["【板块形态优选·机会推断依据】"]
    lines.append("以下板块按领涨股形态+趋势+广度评分，用于推断「可能延续的趋势」，不是单纯今日涨幅榜。")
    lines.append("大涨板块次日轮动概率高，优选会降权过热板块；须结合广度与证伪条件。")
    lines.append("回答须说明：为何可能继续、参与方式（顺势/回踩）、若何种 Skill 信号出现则观点作废。")
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
        "用户问机会/加仓：第一句话直接说第1～3个板块名，并给 Skill 依据的延续逻辑与参与条件；"
        "结构配合时须明确参与方式，禁止只列风险或只说「别追高」。"
    )
    return "\n".join(lines)


def compact_payload_for_llm(data: dict[str, Any]) -> dict[str, Any]:
    """Strip process-only fields (agent evidence/cards) and trim LLM-facing packs."""
    out: dict[str, Any] = {}
    for key in (
        "retrieved_at",
        "query_keywords",
        "query_intents",
        "sector_only",
        "portfolio_focus",
        "question_driven",
        "workflow",
        "research_mode",
        "wants_sector_pick",
        "research_reports",
        "fundamentals",
        "news_pulse",
        "report_qa",
        "research_synthesis_hint",
        "matched_sectors",
        "data_reference",
        "market",
        "sector_picks",
        "sector_period_rank",
        "participant_flow",
        "symbols",
    ):
        if key in data:
            out[key] = data[key]

    period = out.get("sector_period_rank")
    if isinstance(period, dict) and period.get("available"):
        out["sector_period_rank"] = {
            "available": True,
            "trading_days": period.get("trading_days"),
            "window": period.get("window"),
            "note": period.get("note"),
            "source": period.get("source"),
            "scanned": period.get("scanned"),
            "ok": period.get("ok"),
            "top_losers": (period.get("top_losers") or [])[:12],
            "top_gainers": (period.get("top_gainers") or [])[:8],
        }

    sectors = data.get("sectors")
    if sectors:
        compact_sectors: dict[str, Any] = {
            "available": sectors.get("available"),
            "note": sectors.get("note"),
            "data_sources": sectors.get("data_sources"),
        }
        for board_key in ("industry", "concept"):
            block = sectors.get(board_key) or {}
            compact_sectors[board_key] = {
                "available": block.get("available"),
                "source": block.get("source"),
                "count": block.get("count"),
                "top_gainers": (block.get("top_gainers") or [])[:12],
                "top_losers": (block.get("top_losers") or [])[:12],
            }
        out["sectors"] = compact_sectors

    if "symbols" in out:
        trimmed: list[dict[str, Any]] = []
        for sym in out.get("symbols") or []:
            row = dict(sym)
            bars = row.get("candle_bars") or []
            if len(bars) > 5:
                row["candle_bars"] = bars[-5:]
            trimmed.append(row)
        out["symbols"] = trimmed

    rr = data.get("research_reports")
    if rr:
        compact_rr: dict[str, Any] = {"available": rr.get("available"), "symbols": []}
        for row in rr.get("symbols") or []:
            compact_rr["symbols"].append(
                {
                    "symbol": row.get("symbol"),
                    "available": row.get("available"),
                    "confidence": row.get("confidence"),
                    "consensus_note": row.get("consensus_note"),
                    "rating_summary": row.get("rating_summary"),
                    "recent_reports": (row.get("recent_reports") or [])[:5],
                }
            )
        out["research_reports"] = compact_rr

    fund = data.get("fundamentals")
    if fund:
        compact_fund: dict[str, Any] = {"available": fund.get("available"), "symbols": []}
        for row in fund.get("symbols") or []:
            compact_fund["symbols"].append(
                {
                    "symbol": row.get("symbol"),
                    "available": row.get("available"),
                    "confidence": row.get("confidence"),
                    "highlights": row.get("highlights"),
                    "rigor": {
                        "all_ok": (row.get("rigor") or {}).get("all_ok"),
                        "warnings": (row.get("rigor") or {}).get("warnings"),
                    },
                }
            )
        out["fundamentals"] = compact_fund

    if data.get("news_pulse"):
        out["news_pulse"] = data["news_pulse"]

    if data.get("report_qa"):
        out["report_qa"] = data["report_qa"]

    if data.get("research_synthesis_hint"):
        out["research_synthesis_hint"] = data["research_synthesis_hint"]

    # Process-only: never forward agent_evidence / agent_cards / full thesis_drift JSON
    notes = data.get("thesis_drift_notes") or []
    if notes:
        out["thesis_drift_notes"] = list(notes)[:3]

    return out


def format_fetch_block(data: dict[str, Any]) -> str:
    compact = compact_payload_for_llm(data)
    return "【本轮检索数据】\n```json\n" + json.dumps(compact, ensure_ascii=False, indent=2, default=str) + "\n```"
