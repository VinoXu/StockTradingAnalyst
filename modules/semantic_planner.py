"""Semantic planner: rule hints + LLM intent/task split (mapping is reference only)."""

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
}

_PARALLEL_WORKFLOWS = frozenset(
    {
        "symbol_research",
        "sector_research",
        "news_pulse",
        "portfolio_review",
        "ta_screen",
        "dyp_ask",
    }
)


@dataclass
class AgentTask:
    agent_id: str
    skill: str
    goal: str = ""


@dataclass
class SemanticResult:
    """Merged plan after rules + LLM."""

    source: str = "rules"  # rules | llm+rules
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
    # strip markdown fences if any
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


def _rule_hint_blob(plan: QueryPlan) -> dict[str, Any]:
    return {
        "rule_intents": plan.intents,
        "rule_keywords": plan.keywords,
        "rule_symbols": plan.symbols,
        "rule_workflow": plan.workflow,
        "rule_research_mode": plan.research_mode,
        "sector_only": plan.sector_only,
        "portfolio_focus": plan.portfolio_focus,
        "wants_sector_pick": plan.wants_sector_pick,
        "matched_sectors": plan.matched_sectors,
        "needs_market": plan.needs_market,
        "needs_sectors": plan.needs_sectors,
    }


def _reference_catalog() -> dict[str, Any]:
    return {
        "allowed_workflows": list(ALLOWED_WORKFLOWS),
        "agent_skill_reference": AGENT_SKILL_REFERENCE,
        "workflow_reference": WORKFLOW_REFERENCE,
        "allowed_skills": list(ALL_SKILL_NAMES),
        "instruction": (
            "映射表只是参考。请根据用户真实语义拆分任务："
            "可选精简 Agent，可调整每人 skill（须在 allowed_skills），"
            "可组合多种目标；不要机械照抄 typical_agents 全员。"
        ),
    }


def _planner_system_prompt() -> str:
    return (
        "你是投研语义规划器，不是最终分析师。\n"
        "输入：用户问题 + 规则引擎抽取结果（互补线索，可纠正）+ 工作流/Agent/Skill 参考表。\n"
        "输出：仅一行 JSON，不要 Markdown。\n"
        "schema:\n"
        "{"
        '"intent_summary":"一句话意图",'
        '"confidence":"A|B|C",'
        '"workflow_hint":"参考工作流名（可留空）",'
        '"symbols":["600519"],'
        '"fetch":{"quotes":true,"research_reports":false,"fundamentals":false,'
        '"news":false,"holdings":false,"sectors":false,"sector_picks":false,"market":false},'
        '"agent_tasks":[{"agent_id":"murphy","skill":"ta-trend-structure","goal":"…"}],'
        '"team_lead_skills":["…"],'
        '"task_briefs":[{"id":"t1","goal":"…","depends_on":[]}]'
        "}\n"
        "规则：\n"
        "1. agent_id 须在 agent_skill_reference 的 key 中；skill 须在 allowed_skills。\n"
        "2. 轻问题少拆任务（1～3 Agent）；深研可多但仍应说明 goal。\n"
        "3. 若规则 workflow 与语义冲突，以语义为准并写 confidence。\n"
        "4. team_lead_skills 用于最终综合，2～5 个即可。\n"
        "5. 禁止编造不存在的 agent/skill。"
    )


def _call_llm_planner(message: str, rule_plan: QueryPlan) -> dict[str, Any]:
    user = (
        f"【用户问题】\n{message}\n\n"
        f"【规则引擎线索·互补】\n```json\n"
        f"{json.dumps(_rule_hint_blob(rule_plan), ensure_ascii=False)}\n```\n\n"
        f"【参考映射表·非强制】\n```json\n"
        f"{json.dumps(_reference_catalog(), ensure_ascii=False)}\n```\n\n"
        "请输出任务拆分 JSON。"
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
    if "sectors" in fetch or "sector_picks" in fetch:
        plan.needs_sectors = bool(fetch.get("sectors") or fetch.get("sector_picks"))
    if fetch.get("sector_picks"):
        plan.wants_sector_pick = True
    plan.semantic_fetch = {
        "quotes": bool(fetch.get("quotes", True)),
        "research_reports": bool(fetch.get("research_reports")),
        "fundamentals": bool(fetch.get("fundamentals")),
        "news": bool(fetch.get("news")),
        "holdings": bool(fetch.get("holdings")),
        "sectors": bool(fetch.get("sectors")),
        "sector_picks": bool(fetch.get("sector_picks")),
        "market": bool(fetch.get("market", plan.needs_market)),
    }


def apply_semantic_to_plan(plan: QueryPlan, sem: SemanticResult, *, llm_blob: dict[str, Any] | None = None) -> QueryPlan:
    """Mutate plan with semantic overrides; keep rule slots as fallback."""
    plan.semantic_source = sem.source
    plan.intent_summary = sem.intent_summary
    plan.semantic_confidence = sem.confidence
    plan.task_briefs = list(sem.task_briefs)
    plan.agent_roster_override = tuple((t.agent_id, t.skill) for t in sem.tasks)
    plan.team_lead_skills_override = sem.team_lead_skills

    blob = llm_blob or sem.raw_llm
    # symbols: union
    llm_syms = []
    if isinstance(blob.get("symbols"), list):
        llm_syms = [str(s).split(".")[0] for s in blob["symbols"] if re.fullmatch(r"\d{6}", str(s).split(".")[0])]
    if llm_syms:
        plan.symbols = sorted(set(plan.symbols) | set(llm_syms))

    hint = (sem.workflow_hint or "").strip()
    if hint in ALLOWED_WORKFLOWS and sem.source.startswith("llm"):
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

    if isinstance(blob.get("fetch"), dict):
        _apply_fetch_flags(plan, blob["fetch"])

    return plan


def plan_semantics(message: str, rule_plan: QueryPlan) -> tuple[QueryPlan, SemanticResult]:
    """
    Rules first as complementary hints; LLM splits tasks using mapping as reference.
    On LLM failure / disabled → pure rules roster.
    """
    if not semantic_llm_enabled() or not llm_available():
        sem = _rules_fallback_result(rule_plan)
        apply_semantic_to_plan(rule_plan, sem)
        return rule_plan, sem

    try:
        blob = _call_llm_planner(message, rule_plan)
    except Exception as exc:  # noqa: BLE001
        sem = _rules_fallback_result(rule_plan)
        sem.intent_summary = f"LLM 规划失败，规则回退：{exc}"
        apply_semantic_to_plan(rule_plan, sem)
        return rule_plan, sem

    if not blob:
        sem = _rules_fallback_result(rule_plan)
        sem.intent_summary = "LLM 规划无 JSON，规则回退"
        apply_semantic_to_plan(rule_plan, sem)
        return rule_plan, sem

    conf = str(blob.get("confidence") or "B").upper()[:1]
    if conf not in ("A", "B", "C"):
        conf = "B"
    hint = str(blob.get("workflow_hint") or rule_plan.workflow or "").strip()
    if hint and hint not in ALLOWED_WORKFLOWS:
        hint = rule_plan.workflow or rule_plan.research_mode or ""

    tasks = _sanitize_agent_tasks(blob.get("agent_tasks"))
    # 低置信或无任务 → 用规则参考表补齐，但仍记录 LLM 意图摘要
    if conf == "C" or not tasks:
        fallback = _rules_fallback_result(rule_plan)
        if not tasks:
            tasks = fallback.tasks
        if conf == "C" and fallback.tasks and not blob.get("agent_tasks"):
            tasks = fallback.tasks

    briefs = blob.get("task_briefs") if isinstance(blob.get("task_briefs"), list) else []
    if not briefs and tasks:
        briefs = [{"id": f"a-{t.agent_id}", "goal": t.goal or t.agent_id} for t in tasks]

    tl = _sanitize_team_skills(blob.get("team_lead_skills"), hint or rule_plan.workflow)

    sem = SemanticResult(
        source="llm+rules",
        confidence=conf,
        intent_summary=str(blob.get("intent_summary") or "")[:300],
        workflow_hint=hint,
        tasks=tasks,
        team_lead_skills=tl or _rules_fallback_result(rule_plan).team_lead_skills,
        task_briefs=[b for b in briefs if isinstance(b, dict)][:12],
        raw_llm=blob,
    )
    apply_semantic_to_plan(rule_plan, sem, llm_blob=blob)
    return rule_plan, sem


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
