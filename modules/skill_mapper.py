"""Map query intents / workflow to runtime Skill markdown files."""

from __future__ import annotations

from modules.agent_specs import team_lead_skills_for_plan, uses_parallel_agents
from modules.query_planner import QueryPlan
from modules.skill_loader import capital_skill_names, list_skills, murphy_skill_names, nison_skill_names
from modules.skill_registry import SECTOR_RESEARCH_SKILLS, SYMBOL_RESEARCH_SKILLS

# intent → Murphy 子集（不含 Nison）
_INTENT_MURPHY: dict[str, tuple[str, ...]] = {
    "market": ("ta-trend-structure",),
    "sector": ("ta-trend-structure",),
    "risk": ("ta-trend-structure", "ta-volume-price"),
    "opportunity": ("ta-trend-structure",),
    "capital_flow": ("ta-trend-structure",),
    "tech": ("ta-oscillators", "ta-moving-average-boll", "ta-candlestick"),
    "compare": ("ta-trend-structure", "ta-oscillators"),
}

_INTENT_NISON: dict[str, tuple[str, ...]] = {
    "tech": ("nison-candlestick-patterns",),
    "risk": ("nison-signal-confluence",),
    "opportunity": ("nison-candlestick-patterns", "nison-ta-integration"),
}

_WORKFLOW_MURPHY: dict[str, tuple[str, ...]] = {
    "market_overview": ("ta-trend-structure",),
    "sector_deep_dive": ("ta-trend-structure", "ta-volume-price"),
    "risk_scan": ("ta-trend-structure", "ta-volume-price"),
    "opportunity_scan": ("ta-trend-structure",),
    "capital_flow": ("ta-trend-structure",),
    "question_deep_dive": ("ta-trend-structure",),
    "symbol_research": ("ta-trend-structure", "ta-price-patterns", "ta-volume-price"),
    "sector_research": ("ta-trend-structure", "ta-volume-price"),
    "news_pulse": ("ta-trend-structure", "ta-volume-price"),
}

_WORKFLOW_NISON: dict[str, tuple[str, ...]] = {
    "sector_deep_dive": ("nison-candlestick-patterns",),
    "opportunity_scan": ("nison-candlestick-patterns", "nison-ta-integration"),
    "risk_scan": ("nison-signal-confluence",),
    "symbol_research": tuple(nison_skill_names()),
    "sector_research": ("nison-candlestick-patterns", "nison-ta-integration"),
    "news_pulse": ("nison-candlestick-patterns",),
}

_SYMBOL_MURPHY_EXTRA = ("ta-oscillators", "ta-moving-average-boll", "ta-price-patterns")
_SYMBOL_NISON = tuple(nison_skill_names())
_SYMBOL_VOLUME = ("ta-volume-price",)
_SYMBOL_CAPITAL = tuple(capital_skill_names())

_DEFAULT_MURPHY = ("ta-trend-structure",)


def _ordered_existing(names: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    live = set(list_skills())
    return tuple(n for n in names if n in live)


def select_skills_for_plan(plan: QueryPlan, *, scope: str = "open", team_lead: bool = False) -> tuple[str, ...]:
    """Return Skill names to inject for this turn (Murphy / Nison 分包，不混注)."""
    if team_lead or uses_parallel_agents(plan):
        tl = _ordered_existing(team_lead_skills_for_plan(plan))
        if tl:
            return tl

    if plan.workflow == "dyp_ask":
        return _ordered_existing(("dyp-ask", "master-duan"))
    if plan.workflow == "portfolio_review":
        return _ordered_existing(("portfolio-review", "ta-trend-structure", "thesis-drift", "a-share-capital-flow"))
    if plan.workflow == "ta_screen":
        return _ordered_existing(
            ("ta-investment-checklist", "ta-quality-screen", "ta-trend-structure", "a-share-capital-flow")
        )
    if plan.workflow == "news_pulse":
        return _ordered_existing(
            ("news-pulse", "ta-trend-structure", "master-munger", "a-share-capital-flow")
        )

    if plan.research_mode == "symbol_research":
        return _ordered_existing(SYMBOL_RESEARCH_SKILLS)
    if plan.research_mode == "sector_research":
        return _ordered_existing(SECTOR_RESEARCH_SKILLS)

    chosen: set[str] = set()

    if plan.portfolio_focus or (plan.symbols and not plan.sector_only):
        chosen.update(_DEFAULT_MURPHY)
        intents = set(plan.intents)
        if intents & {"tech", "compare"} or plan.needs_charts:
            chosen.update(_SYMBOL_MURPHY_EXTRA)
            chosen.update(_SYMBOL_NISON)
        if intents & {"risk", "opportunity"} or "volume" in " ".join(plan.keywords).lower():
            chosen.update(_SYMBOL_VOLUME)
        for intent in intents:
            chosen.update(_INTENT_MURPHY.get(intent, ()))
            chosen.update(_INTENT_NISON.get(intent, ()))
        if "capital_flow" in intents:
            chosen.update(_SYMBOL_CAPITAL)
        if scope == "symbol" and not plan.sector_only:
            chosen.update(("ta-price-patterns",))
    elif plan.sector_only or plan.needs_sectors or plan.wants_sector_pick:
        wf = plan.workflow or ""
        chosen.update(_WORKFLOW_MURPHY.get(wf, _DEFAULT_MURPHY))
        chosen.update(_WORKFLOW_NISON.get(wf, ()))
        chosen.update(_SYMBOL_CAPITAL)
        if plan.wants_sector_pick:
            chosen.update(_SYMBOL_NISON)
            chosen.update(_SYMBOL_VOLUME)
            chosen.update(("ta-price-patterns",))
    else:
        wf = plan.workflow or ""
        chosen.update(_WORKFLOW_MURPHY.get(wf, ()))
        chosen.update(_WORKFLOW_NISON.get(wf, ()))
        for intent in plan.intents:
            chosen.update(_INTENT_MURPHY.get(intent, ()))
            chosen.update(_INTENT_NISON.get(intent, ()))
        if "capital_flow" in plan.intents:
            chosen.update(_SYMBOL_CAPITAL)

    if not chosen:
        chosen.update(_DEFAULT_MURPHY)

    ordered = _ordered_existing(chosen)
    return ordered


def skills_summary(names: tuple[str, ...]) -> str:
    if not names:
        return "（无）"
    murphy = [n for n in names if n in murphy_skill_names()]
    nison = [n for n in names if n in nison_skill_names()]
    other = [n for n in names if n not in murphy and n not in nison]
    parts: list[str] = []
    if murphy:
        parts.append("Murphy:" + "、".join(murphy))
    if nison:
        parts.append("Nison:" + "、".join(nison))
    if other:
        parts.append("其他:" + "、".join(other))
    return " | ".join(parts)
