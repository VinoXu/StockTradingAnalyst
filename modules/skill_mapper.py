"""Map query intents / workflow to runtime Skill markdown files."""

from __future__ import annotations

from modules.query_planner import QueryPlan
from modules.skill_loader import runtime_skill_names

# intent → Skill 判据（仅 LLM 解读规则，数值由 modules 预计算）
_INTENT_SKILLS: dict[str, tuple[str, ...]] = {
    "market": ("ta-trend-structure",),
    "sector": ("ta-trend-structure",),
    "risk": ("ta-trend-structure", "ta-volume-price"),
    "opportunity": ("ta-trend-structure",),
    "capital_flow": ("a-share-capital-flow", "ta-trend-structure"),
    "tech": (
        "ta-oscillators",
        "ta-moving-average-boll",
        "ta-candlestick",
        "nison-candlestick-patterns",
    ),
    "compare": ("ta-trend-structure", "ta-oscillators"),
}

_WORKFLOW_SKILLS: dict[str, tuple[str, ...]] = {
    "market_overview": ("ta-trend-structure",),
    "sector_deep_dive": ("ta-trend-structure",),
    "risk_scan": ("ta-trend-structure", "ta-volume-price"),
    "opportunity_scan": ("ta-trend-structure",),
    "capital_flow": ("a-share-capital-flow", "ta-trend-structure"),
    "question_deep_dive": ("ta-trend-structure",),
}

_SYMBOL_BASE = ("ta-trend-structure",)
_SYMBOL_TECH = ("ta-oscillators", "ta-moving-average-boll")
_SYMBOL_CANDLE = ("nison-candlestick-patterns", "ta-candlestick", "nison-signal-confluence", "nison-ta-integration")
_SYMBOL_VOLUME = ("ta-volume-price",)
_SYMBOL_PATTERN = ("ta-price-patterns",)
_SYMBOL_CAPITAL = ("a-share-capital-flow",)

_DEFAULT = ("ta-trend-structure",)


def select_skills_for_plan(plan: QueryPlan, *, scope: str = "open") -> tuple[str, ...]:
    """Return Skill names to inject for this turn (subset of runtime skills)."""
    chosen: set[str] = set()

    if plan.portfolio_focus or (plan.symbols and not plan.sector_only):
        chosen.update(_SYMBOL_BASE)
        intents = set(plan.intents)
        if intents & {"tech", "compare"} or plan.needs_charts:
            chosen.update(_SYMBOL_TECH)
            chosen.update(_SYMBOL_CANDLE)
        if intents & {"risk", "opportunity"} or "volume" in " ".join(plan.keywords).lower():
            chosen.update(_SYMBOL_VOLUME)
        if "capital_flow" in intents:
            chosen.update(_SYMBOL_CAPITAL)
        if scope == "symbol" and not plan.sector_only:
            chosen.update(_SYMBOL_PATTERN)
    elif plan.sector_only or plan.needs_sectors or plan.wants_sector_pick:
        chosen.update(_WORKFLOW_SKILLS.get(plan.workflow or "", ("ta-trend-structure",)))
        chosen.update(_INTENT_SKILLS.get("capital_flow", ()))
        if plan.wants_sector_pick:
            chosen.update(_SYMBOL_CANDLE)
            chosen.update(_SYMBOL_VOLUME)
            chosen.update(_SYMBOL_PATTERN)
    else:
        wf = plan.workflow or ""
        if wf in _WORKFLOW_SKILLS:
            chosen.update(_WORKFLOW_SKILLS[wf])
        for intent in plan.intents:
            chosen.update(_INTENT_SKILLS.get(intent, ()))

    if not chosen:
        chosen.update(_DEFAULT)

    ordered = [n for n in runtime_skill_names() if n in chosen]
    return tuple(ordered)


def skills_summary(names: tuple[str, ...]) -> str:
    return "、".join(names) if names else "（无）"
