"""Semantic planner: LLM judges intent first, then routes/fetch/agents (no keyword routing)."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from modules.llm import chat, llm_available
from modules.query_planner import QueryPlan
from modules.skill_registry import ALL_SKILL_NAMES

# --- Allow-lists (LLM 只能在此范围内拆任务；映射表作参考) ---

ALLOWED_WORKFLOWS: tuple[str, ...] = (
    "symbol_research",
    "sector_research",
    "news_pulse",
    "portfolio_review",
    "ta_screen",
    "dyp_ask",
    "market_overview",
    "sector_deep_dive",
    "risk_scan",
    "opportunity_scan",
    "capital_flow",
    "named_symbols",
    "question_deep_dive",
    "situation_advice",
    "direct_chat",
)

ALLOWED_INTENTS: tuple[str, ...] = (
    "market",
    "sector",
    "capital_flow",
    "risk",
    "opportunity",
    "tech",
    "compare",
)

# agent_id → 默认 Skill（参考表，LLM 可改 skill 只要仍在 ALL_SKILL_NAMES）
AGENT_SKILL_REFERENCE: dict[str, str] = {
    "nison": "nison-candlestick-patterns",
    "murphy": "ta-trend-structure",
    "duan": "master-duan",
    "buffett": "master-buffett",
    "munger": "master-munger",
    "li": "master-li",
    "news": "news-pulse",
    "portfolio": "portfolio-review",
    "checklist": "ta-investment-checklist",
    "quality": "ta-quality-screen",
    "earnings": "symbol-earnings-review",
}

# 场景参考：LLM 拆任务时可参考，非硬编码强制全开
WORKFLOW_REFERENCE: dict[str, dict[str, Any]] = {
    "symbol_research": {
        "typical_agents": ["nison", "murphy", "duan", "buffett", "munger", "li"],
        "team_lead_skills": [
            "symbol-deep-research",
            "research-report-reader",
            "financial-data",
            "symbol-earnings-review",
            "ta-investment-checklist",
        ],
        "fetch": ["quotes", "research_reports", "fundamentals"],
        "note": "个股深研；可按问题精简大师 Agent，不必六人全开",
    },
    "sector_research": {
        "typical_agents": ["nison", "murphy", "munger", "li"],
        "team_lead_skills": [
            "sector-deep-research",
            "sector-funnel",
            "ta-investment-checklist",
            "ta-quality-screen",
        ],
        "fetch": ["sectors", "sector_picks", "quotes"],
        "note": "板块无研报无个股财报；通常不派段/巴",
    },
    "news_pulse": {
        "typical_agents": ["murphy", "munger", "news"],
        "team_lead_skills": ["news-pulse", "ta-trend-structure"],
        "fetch": ["quotes", "news"],
        "note": "异动归因，追求快",
    },
    "portfolio_review": {
        "typical_agents": ["murphy", "munger", "portfolio"],
        "team_lead_skills": ["portfolio-review", "ta-trend-structure", "thesis-drift"],
        "fetch": ["holdings", "quotes"],
        "note": "组合复盘",
    },
    "ta_screen": {
        "typical_agents": ["murphy", "checklist", "quality"],
        "team_lead_skills": [
            "ta-investment-checklist",
            "ta-quality-screen",
            "ta-trend-structure",
        ],
        "fetch": ["quotes", "sector_picks"],
        "note": "快筛决定是否值得深研",
    },
    "dyp_ask": {
        "typical_agents": ["duan"],
        "team_lead_skills": ["dyp-ask", "master-duan"],
        "fetch": ["quotes"],
        "note": "段式本质问答；可单独一人",
    },
    "situation_advice": {
        "typical_agents": ["murphy", "munger", "checklist"],
        "team_lead_skills": [
            "ta-trend-structure",
            "ta-investment-checklist",
            "ta-volume-price",
        ],
        "fetch": ["market", "sectors", "participant_flow"],
        "note": "开放式整盘/账户被套回本；按组合层回答，禁止扭成单票ETF或杠杆鉴定",
    },
}

_PARALLEL_WORKFLOWS = frozenset(
    {
        "symbol_research",
        "sector_research",
        "news_pulse",
        "portfolio_review",
        "ta_screen",
        "dyp_ask",
        "situation_advice",
    }
)


@dataclass
class AgentTask:
    agent_id: str
    skill: str
    goal: str = ""


@dataclass
class SemanticResult:
    """LLM 意图判定 + 路由/任务拆分结果。"""

    source: str = "llm"  # llm | rules_fallback
    confidence: str = "C"
    intent_summary: str = ""
    workflow_hint: str = ""
    tasks: list[AgentTask] = field(default_factory=list)
    team_lead_skills: tuple[str, ...] = ()
    task_briefs: list[dict[str, Any]] = field(default_factory=list)
    raw_llm: dict[str, Any] = field(default_factory=dict)


def semantic_llm_enabled() -> bool:
    return os.environ.get("SEMANTIC_LLM_PLANNER", "1").strip().lower() not in ("0", "false", "no")


def _extract_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        return {}
    if "```" in text:
        m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if m:
            text = m.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return {}
    return {}


def _structural_facts(plan: QueryPlan) -> dict[str, Any]:
    """仅结构事实，不含规则意图/关键词路由结论。"""
    from modules.portfolio import list_holdings

    holdings = [
        {"symbol": (h.get("symbol") or "").split(".")[0], "name": (h.get("name") or "").strip()}
        for h in list_holdings()[:20]
        if h.get("symbol")
    ]
    return {
        "message_codes": list(plan.symbols or []),
        "ui_selected_candidates": list(plan.selected_candidates or []),
        "holdings_brief": holdings,
    }


def _prior_context_blob(prior_turns: list[tuple[str, str]] | None, *, limit: int = 2) -> str:
    if not prior_turns:
        return ""
    from modules.conversation_memory import _brief, _strip_time_banner

    parts: list[str] = []
    for q, a in prior_turns[-limit:]:
        parts.append(f"用户：{_brief(q, 80)}\n助手：{_brief(_strip_time_banner(a), 160)}")
    return "\n---\n".join(parts)


def _reference_catalog() -> dict[str, Any]:
    from modules.route_planner import ALLOWED_ROUTES, ROUTE_FETCH, route_catalog_for_llm

    return {
        "allowed_workflows": list(ALLOWED_WORKFLOWS),
        "allowed_intents": list(ALLOWED_INTENTS),
        "allowed_routes": list(ALLOWED_ROUTES),
        "route_definitions": route_catalog_for_llm(),
        "route_fetch_reference": ROUTE_FETCH,
        "agent_skill_reference": AGENT_SKILL_REFERENCE,
        "workflow_reference": WORKFLOW_REFERENCE,
        "allowed_skills": list(ALL_SKILL_NAMES),
        "instruction": (
            "先判意图再选 routes；定义见 route_definitions。"
            "与投资无关必须 routes=[\"direct_chat\"]（互斥，不可叠投研路由）。"
        ),
    }


def _planner_system_prompt() -> str:
    return (
        "你是投研语义规划器：必须先理解用户真实意图，再决定路由与取数，禁止用关键词机械匹配。\n"
        "输入：用户问题 + 可选上文 + 结构事实（消息内代码/UI候选/持仓列表）+ 路由定义表。\n"
        "结构事实不是意图结论；是否挂载 UI 候选标的、是否承接上文，一律由你按语义判断。\n"
        "输出：仅一行 JSON，不要 Markdown。\n"
        "schema:\n"
        "{"
        '"intent_summary":"一句话意图",'
        '"confidence":"A|B|C",'
        '"intents":["market","sector"],'
        '"routes":["market"],'
        '"workflow_hint":"可选主标签（可留空，优先用 routes）",'
        '"symbols":["600519"],'
        '"attach_ui_selected":false,'
        '"sector_keywords":["电池"],'
        '"fetch":{"quotes":true,"research_reports":false,"fundamentals":false,'
        '"news":false,"holdings":false,"sectors":false,"sector_picks":false,'
        '"sector_period_rank":false,"market":false,"lookback_trading_days":null},'
        '"agent_tasks":[{"agent_id":"murphy","skill":"ta-trend-structure","goal":"…"}],'
        '"team_lead_skills":["…"],'
        '"task_briefs":[{"id":"t1","goal":"…","depends_on":[]}]'
        "}\n"
        "意图 intents（可多选，仅用白名单）：market/sector/capital_flow/risk/opportunity/tech/compare\n"
        "路由 routes（白名单，详见 route_definitions）：\n"
        "- market：大盘/指数/广度/成交额\n"
        "- sector：板块/主线\n"
        "- symbol：个股/点名标的\n"
        "- discuss：整盘回本/解套/处境策略（无代码也要答）\n"
        "- capital：资金面\n"
        "- holdings：持仓/自选对照\n"
        "- direct_chat：与 A 股投研无关（寒暄/天气/编程/闲聊等）；"
        "互斥，只能单独出现；fetch 全 false；agent_tasks=[]；attach_ui_selected=false；"
        "workflow_hint=direct_chat\n"
        "判定原则：\n"
        "1. 先写 intent_summary，再填 intents/routes/fetch；禁止词表硬套。\n"
        "2. 无投研取数需要 → 必须 routes=[\"direct_chat\"]，不要留空数组装傻。\n"
        "3. 上文追问若承接投研话题，按上文主题选投研路由，禁止误判 direct_chat。\n"
        "4. 整盘亏损/怎么回本 → 至少 discuss，常叠加 market+holdings。\n"
        "5. 「大盘怎么样，顺便看下600519」→ market+symbol。\n"
        "6. attach_ui_selected=true 仅当本轮确实要分析 UI 候选；direct_chat 必须 false。\n"
        "7. agent_id / skill 须在白名单；轻问题少拆 Agent。\n"
        "8. fetch 须覆盖所选投研路由；direct_chat 时 fetch 全 false。\n"
        "9. 近一周/两周板块排行：sectors 与 sector_period_rank=true。\n"
    )


def _call_llm_planner(
    message: str,
    plan: QueryPlan,
    *,
    prior_turns: list[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    prior_txt = _prior_context_blob(prior_turns)
    prior_block = f"【上文（供指代/追问承接）】\n{prior_txt}\n\n" if prior_txt else ""
    user = (
        f"【用户问题】\n{message}\n\n"
        f"{prior_block}"
        f"【结构事实·非意图结论】\n```json\n"
        f"{json.dumps(_structural_facts(plan), ensure_ascii=False)}\n```\n\n"
        f"【参考映射表·非强制】\n```json\n"
        f"{json.dumps(_reference_catalog(), ensure_ascii=False)}\n```\n\n"
        "请先判断意图，再输出路由与任务拆分 JSON。"
    )
    raw = chat(
        [
            {"role": "system", "content": _planner_system_prompt()},
            {"role": "user", "content": user},
        ],
        temperature=0.1,
        timeout=90.0,
    )
    return _extract_json(raw)


def _sanitize_skill(name: str) -> str | None:
    n = (name or "").strip()
    return n if n in ALL_SKILL_NAMES else None


def _sanitize_agent_tasks(rows: Any) -> list[AgentTask]:
    out: list[AgentTask] = []
    seen: set[str] = set()
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        aid = str(row.get("agent_id") or row.get("agent") or "").strip().lower()
        if aid not in AGENT_SKILL_REFERENCE or aid in seen:
            continue
        skill = _sanitize_skill(str(row.get("skill") or "")) or AGENT_SKILL_REFERENCE[aid]
        # 允许 LLM 给 duan 配 dyp-ask 等
        if skill not in ALL_SKILL_NAMES:
            skill = AGENT_SKILL_REFERENCE[aid]
        goal = str(row.get("goal") or "")[:120]
        out.append(AgentTask(agent_id=aid, skill=skill, goal=goal))
        seen.add(aid)
    return out[:8]


def _sanitize_team_skills(names: Any, workflow_hint: str) -> tuple[str, ...]:
    cleaned: list[str] = []
    if isinstance(names, list):
        for n in names:
            s = _sanitize_skill(str(n))
            if s and s not in cleaned:
                cleaned.append(s)
    if cleaned:
        return tuple(cleaned[:6])
    ref = WORKFLOW_REFERENCE.get(workflow_hint) or {}
    return tuple(ref.get("team_lead_skills") or ())


def _rules_fallback_result(plan: QueryPlan) -> SemanticResult:
    from modules.agent_specs import agent_roster_for_plan, team_lead_skills_for_plan

    roster = agent_roster_for_plan(plan)
    tasks = [
        AgentTask(agent_id=a, skill=s, goal=f"规则参考任务·{a}")
        for a, s in roster
    ]
    return SemanticResult(
        source="rules",
        confidence="C",
        intent_summary="规则引擎回退",
        workflow_hint=plan.workflow or plan.research_mode or "",
        tasks=tasks,
        team_lead_skills=team_lead_skills_for_plan(plan),
        task_briefs=[{"id": f"r-{a}", "goal": f"规则映射·{a}", "source": "rules"} for a, _ in roster],
    )


def _apply_fetch_flags(plan: QueryPlan, fetch: dict[str, Any]) -> None:
    """Overlay LLM fetch hints onto QueryPlan needs_* fields."""
    if not isinstance(fetch, dict):
        return
    if "market" in fetch:
        plan.needs_market = bool(fetch.get("market"))
    if "sectors" in fetch or "sector_picks" in fetch or "sector_period_rank" in fetch:
        plan.needs_sectors = bool(
            fetch.get("sectors") or fetch.get("sector_picks") or fetch.get("sector_period_rank")
        )
    if fetch.get("sector_picks"):
        plan.wants_sector_pick = True
    if fetch.get("sector_period_rank"):
        plan.wants_sector_period_rank = True
        plan.needs_sectors = True
    raw_lb = fetch.get("lookback_trading_days")
    if raw_lb is not None:
        try:
            lb = int(raw_lb)
            if 2 <= lb <= 60:
                plan.sector_lookback_days = lb
                plan.wants_sector_period_rank = True
                plan.needs_sectors = True
        except (TypeError, ValueError):
            pass
    plan.semantic_fetch = {
        "quotes": bool(fetch.get("quotes", True)),
        "research_reports": bool(fetch.get("research_reports")),
        "fundamentals": bool(fetch.get("fundamentals")),
        "news": bool(fetch.get("news")),
        "holdings": bool(fetch.get("holdings")),
        "sectors": bool(fetch.get("sectors")),
        "sector_picks": bool(fetch.get("sector_picks")),
        "sector_period_rank": bool(fetch.get("sector_period_rank") or plan.wants_sector_period_rank),
        "market": bool(fetch.get("market", plan.needs_market)),
        "lookback_trading_days": plan.sector_lookback_days,
    }


def apply_semantic_to_plan(plan: QueryPlan, sem: SemanticResult, *, llm_blob: dict[str, Any] | None = None) -> QueryPlan:
    """以 LLM 意图/路由结果写入 QueryPlan（主路径不合并关键词规则路由）。"""
    from modules.route_planner import (
        fetch_flags_for_routes,
        is_direct_chat_routes,
        primary_workflow,
        sanitize_routes,
    )

    plan.semantic_source = sem.source
    plan.intent_summary = sem.intent_summary
    plan.semantic_confidence = sem.confidence
    plan.task_briefs = list(sem.task_briefs)
    plan.agent_roster_override = tuple((t.agent_id, t.skill) for t in sem.tasks)
    plan.team_lead_skills_override = sem.team_lead_skills

    blob = llm_blob or sem.raw_llm or {}
    hint = (sem.workflow_hint or "").strip()

    # intents：仅采纳白名单
    raw_intents = blob.get("intents") if isinstance(blob.get("intents"), list) else []
    intents = [str(x).strip() for x in raw_intents if str(x).strip() in ALLOWED_INTENTS]
    if intents:
        plan.intents = list(dict.fromkeys(intents))

    # sector_keywords → matched_sectors / keywords
    sk = blob.get("sector_keywords") if isinstance(blob.get("sector_keywords"), list) else []
    sk_clean = [str(x).strip() for x in sk if str(x).strip()][:8]
    if sk_clean:
        plan.matched_sectors = list(dict.fromkeys(sk_clean))
        plan.keywords = list(dict.fromkeys((plan.keywords or []) + sk_clean))[:12]

    # symbols from LLM + optional UI attach
    llm_syms = []
    if isinstance(blob.get("symbols"), list):
        llm_syms = [
            str(s).split(".")[0]
            for s in blob["symbols"]
            if re.fullmatch(r"\d{6}", str(s).split(".")[0])
        ]
    syms = set(plan.symbols or []) | set(llm_syms)
    if blob.get("attach_ui_selected") and plan.selected_candidates:
        syms |= {s.split(".")[0] for s in plan.selected_candidates}
    plan.symbols = sorted(syms)

    # routes：LLM 结果覆盖（含 direct_chat 规范化）
    llm_routes = sanitize_routes(blob.get("routes"))
    if sem.source.startswith("llm"):
        plan.routes = llm_routes
    elif llm_routes:
        plan.routes = llm_routes
    elif not plan.routes:
        plan.routes = []

    # direct_chat：显式路由 / 空路由 / workflow_hint
    if (
        hint == "direct_chat"
        or is_direct_chat_routes(plan.routes)
        or (sem.source.startswith("llm") and not plan.routes)
    ):
        plan.routes = ["direct_chat"]
        plan.workflow = "direct_chat"
        plan.research_mode = ""
        plan.needs_market = False
        plan.needs_sectors = False
        plan.needs_charts = False
        plan.chart_kinds = []
        plan.agent_roster_override = ()
        plan.team_lead_skills_override = ()
        plan.task_briefs = []
        plan.semantic_fetch = {
            "quotes": False,
            "research_reports": False,
            "fundamentals": False,
            "news": False,
            "holdings": False,
            "sectors": False,
            "sector_picks": False,
            "sector_period_rank": False,
            "market": False,
            "lookback_trading_days": None,
        }
        return plan

    if plan.routes:
        if "discuss" in plan.routes:
            plan.workflow = "situation_advice"
            plan.research_mode = ""
        else:
            plan.workflow = primary_workflow(plan.routes, has_symbols=bool(plan.symbols))
            if "symbol" in plan.routes and plan.symbols:
                plan.research_mode = "symbol_research"
            elif "sector" in plan.routes and not plan.symbols:
                plan.research_mode = plan.research_mode or "sector_research"
                plan.needs_sectors = True
        if "holdings" in plan.routes:
            plan.portfolio_focus = plan.portfolio_focus or (
                "discuss" not in plan.routes and "market" not in plan.routes
            )
    elif hint in ALLOWED_WORKFLOWS:
        plan.workflow = hint
        if hint in ("symbol_research", "sector_research"):
            plan.research_mode = hint
        elif hint in _PARALLEL_WORKFLOWS:
            plan.research_mode = ""
        if hint == "portfolio_review":
            plan.portfolio_focus = True
        if hint == "sector_research":
            plan.sector_only = plan.sector_only or not plan.symbols
            plan.needs_sectors = True
    else:
        plan.workflow = plan.workflow or "question_deep_dive"

    fetch = blob.get("fetch") if isinstance(blob.get("fetch"), dict) else {}
    merged_fetch = dict(fetch)
    for k, v in fetch_flags_for_routes(plan.routes or []).items():
        if v:
            merged_fetch[k] = True
    if merged_fetch:
        _apply_fetch_flags(plan, merged_fetch)
    elif plan.routes:
        _apply_fetch_flags(plan, fetch_flags_for_routes(plan.routes))

    return plan


def plan_semantics(
    message: str,
    base_plan: QueryPlan,
    *,
    prior_turns: list[tuple[str, str]] | None = None,
) -> tuple[QueryPlan, SemanticResult]:
    """
    主路径：大模型判断意图 → 再写 routes / fetch / agents。
    仅当 LLM 关闭或失败时，才回退关键词规则 plan_query_by_rules。
    """
    from modules.query_planner import plan_query_by_rules

    def _apply_rules_fallback(reason: str) -> tuple[QueryPlan, SemanticResult]:
        selected = list(base_plan.selected_candidates or [])
        rule_plan = plan_query_by_rules(message, selected)
        # 保留结构槽位上的 message codes
        rule_plan.symbols = sorted(set(rule_plan.symbols or []) | set(base_plan.symbols or []))
        rule_plan.selected_candidates = list(base_plan.selected_candidates or [])
        sem = _rules_fallback_result(rule_plan)
        sem.source = "rules_fallback"
        sem.intent_summary = f"{reason}；{sem.intent_summary}"[:300]
        apply_semantic_to_plan(rule_plan, sem)
        # 把结果写回调用方持有的对象
        for field_name in rule_plan.__dataclass_fields__:
            setattr(base_plan, field_name, getattr(rule_plan, field_name))
        return base_plan, sem

    if not semantic_llm_enabled() or not llm_available():
        return _apply_rules_fallback("语义 LLM 关闭或不可用")

    try:
        blob = _call_llm_planner(message, base_plan, prior_turns=prior_turns)
    except Exception as exc:  # noqa: BLE001
        return _apply_rules_fallback(f"LLM 规划失败：{exc}")

    if not blob:
        return _apply_rules_fallback("LLM 规划无 JSON")

    conf = str(blob.get("confidence") or "B").upper()[:1]
    if conf not in ("A", "B", "C"):
        conf = "B"
    hint = str(blob.get("workflow_hint") or "").strip()
    if hint and hint not in ALLOWED_WORKFLOWS:
        hint = ""

    tasks = _sanitize_agent_tasks(blob.get("agent_tasks"))
    # direct_chat / 轻问可以无 agent；仅在有路由且无任务时用参考表补齐
    llm_routes_preview = blob.get("routes") if isinstance(blob.get("routes"), list) else []
    if (not tasks) and hint != "direct_chat" and llm_routes_preview:
        # 临时用空 workflow 的 roster 参考：先写入 routes 再取
        tmp = QueryPlan(
            routes=[str(r) for r in llm_routes_preview if r],
            symbols=list(base_plan.symbols or []),
            workflow=hint or "question_deep_dive",
        )
        from modules.route_planner import primary_workflow, sanitize_routes

        tmp.routes = sanitize_routes(tmp.routes)
        if tmp.routes and not tmp.workflow:
            tmp.workflow = primary_workflow(tmp.routes, has_symbols=bool(tmp.symbols))
        fallback = _rules_fallback_result(tmp)
        tasks = fallback.tasks

    briefs = blob.get("task_briefs") if isinstance(blob.get("task_briefs"), list) else []
    if not briefs and tasks:
        briefs = [{"id": f"a-{t.agent_id}", "goal": t.goal or t.agent_id} for t in tasks]

    tl = _sanitize_team_skills(blob.get("team_lead_skills"), hint or "")

    sem = SemanticResult(
        source="llm",
        confidence=conf,
        intent_summary=str(blob.get("intent_summary") or "")[:300],
        workflow_hint=hint,
        tasks=tasks,
        team_lead_skills=tl,
        task_briefs=[b for b in briefs if isinstance(b, dict)][:12],
        raw_llm=blob,
    )
    apply_semantic_to_plan(base_plan, sem, llm_blob=blob)
    if not sem.team_lead_skills and base_plan.workflow != "direct_chat":
        fb = _rules_fallback_result(base_plan)
        sem.team_lead_skills = fb.team_lead_skills
        base_plan.team_lead_skills_override = fb.team_lead_skills
    return base_plan, sem


def semantic_debug_dict(sem: SemanticResult) -> dict[str, Any]:
    return {
        "source": sem.source,
        "confidence": sem.confidence,
        "intent_summary": sem.intent_summary,
        "workflow_hint": sem.workflow_hint,
        "tasks": [asdict(t) for t in sem.tasks],
        "team_lead_skills": list(sem.team_lead_skills),
        "task_briefs": sem.task_briefs,
    }
