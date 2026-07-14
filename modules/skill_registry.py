"""Canonical Skill catalog: layers, agents, and routing metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SkillStatus = Literal["live", "stub"]

MURPHY_SKILL_NAMES: tuple[str, ...] = (
    "ta-trend-structure",
    "ta-oscillators",
    "ta-moving-average-boll",
    "ta-volume-price",
    "ta-price-patterns",
    "ta-candlestick",
)

NISON_SKILL_NAMES: tuple[str, ...] = (
    "nison-candlestick-patterns",
    "nison-signal-confluence",
    "nison-ta-integration",
)

CAPITAL_SKILL_NAMES: tuple[str, ...] = ("a-share-capital-flow",)

MASTER_SKILL_NAMES: tuple[str, ...] = (
    "master-duan",
    "master-buffett",
    "master-munger",
    "master-li",
)

SCENARIO_SKILL_NAMES: tuple[str, ...] = (
    "symbol-deep-research",
    "sector-deep-research",
)

SCREEN_SKILL_NAMES: tuple[str, ...] = (
    "ta-investment-checklist",
    "ta-quality-screen",
    "sector-funnel",
)

FUNDAMENTAL_SKILL_NAMES: tuple[str, ...] = (
    "research-report-reader",
    "symbol-earnings-review",
    "financial-data",
)

PORTFOLIO_SKILL_NAMES: tuple[str, ...] = (
    "thesis-tracker",
    "thesis-drift",
    "portfolio-review",
)

MARKET_SKILL_NAMES: tuple[str, ...] = ("news-pulse",)

MIND_SKILL_NAMES: tuple[str, ...] = ("dyp-ask",)

# 原 10 个 TA + 资金面（始终 live）
CORE_TA_SKILL_NAMES: tuple[str, ...] = MURPHY_SKILL_NAMES + NISON_SKILL_NAMES + CAPITAL_SKILL_NAMES

# 向后兼容别名
LEGACY_SKILL_NAMES = CORE_TA_SKILL_NAMES

EXTENDED_SKILL_NAMES: tuple[str, ...] = (
    MASTER_SKILL_NAMES
    + SCENARIO_SKILL_NAMES
    + SCREEN_SKILL_NAMES
    + FUNDAMENTAL_SKILL_NAMES
    + PORTFOLIO_SKILL_NAMES
    + MARKET_SKILL_NAMES
    + MIND_SKILL_NAMES
)

ALL_SKILL_NAMES: tuple[str, ...] = CORE_TA_SKILL_NAMES + EXTENDED_SKILL_NAMES

# 已有 SKILL.md、已接入运行时（含深研 / 并行 Agent / 专用 workflow）
LIVE_EXTENDED_SKILLS: frozenset[str] = frozenset(EXTENDED_SKILL_NAMES)

# 已全部接线；保留空集合供兼容旧调用
STUB_SKILL_NAMES: frozenset[str] = frozenset()


def skill_status(name: str) -> SkillStatus:
    if name in CORE_TA_SKILL_NAMES or name in LIVE_EXTENDED_SKILLS:
        return "live"
    return "stub"


@dataclass(frozen=True)
class SkillEntry:
    name: str
    layer: str
    agent: str
    symbol: bool
    sector: bool
    status: SkillStatus
    trigger: str = ""


def _catalog_entry(
    name: str,
    layer: str,
    agent: str,
    symbol: bool,
    sector: bool,
    trigger: str,
) -> SkillEntry:
    return SkillEntry(name, layer, agent, symbol, sector, skill_status(name), trigger)


SKILL_CATALOG: tuple[SkillEntry, ...] = (
    *[_catalog_entry(n, "murphy", "murphy", True, True, "intent/workflow") for n in MURPHY_SKILL_NAMES],
    *[_catalog_entry(n, "nison", "nison", True, True, "intent/workflow") for n in NISON_SKILL_NAMES],
    _catalog_entry("a-share-capital-flow", "capital", "shared", True, True, "capital_flow"),
    _catalog_entry("master-duan", "master", "duan", True, True, "symbol_research"),
    _catalog_entry("master-buffett", "master", "buffett", True, False, "symbol_research"),
    _catalog_entry("master-munger", "master", "munger", True, True, "symbol_research|sector_research|news_pulse"),
    _catalog_entry("master-li", "master", "li", True, True, "symbol_research|sector_research"),
    _catalog_entry("symbol-deep-research", "scenario", "team_lead", True, False, "symbol_research"),
    _catalog_entry("sector-deep-research", "scenario", "team_lead", False, True, "sector_research"),
    _catalog_entry("ta-investment-checklist", "screen", "shared", True, True, "ta_screen|symbol_research|sector_research"),
    _catalog_entry("ta-quality-screen", "screen", "shared", True, True, "ta_screen|sector_research"),
    _catalog_entry("sector-funnel", "screen", "shared", False, True, "sector_research"),
    _catalog_entry("research-report-reader", "fundamental", "shared", True, False, "symbol_research"),
    _catalog_entry("symbol-earnings-review", "fundamental", "buffett", True, False, "symbol_research"),
    _catalog_entry("financial-data", "fundamental", "shared", True, False, "symbol_research"),
    _catalog_entry("thesis-tracker", "portfolio", "shared", True, False, "symbol_research"),
    _catalog_entry("thesis-drift", "portfolio", "shared", True, False, "symbol_research|portfolio_review"),
    _catalog_entry("portfolio-review", "portfolio", "shared", True, False, "portfolio_review"),
    _catalog_entry("news-pulse", "market", "shared", True, True, "news_pulse"),
    _catalog_entry("dyp-ask", "mind", "duan", True, True, "dyp_ask"),
)

# 非并行路径：单轮 LLM 仍注入完整 Skill 子集
SYMBOL_RESEARCH_SKILLS: tuple[str, ...] = (
    *MURPHY_SKILL_NAMES,
    *NISON_SKILL_NAMES,
    *CAPITAL_SKILL_NAMES,
    *MASTER_SKILL_NAMES,
    "symbol-deep-research",
    "research-report-reader",
    "financial-data",
    "symbol-earnings-review",
    "ta-investment-checklist",
)

SECTOR_RESEARCH_SKILLS: tuple[str, ...] = (
    *MURPHY_SKILL_NAMES,
    *NISON_SKILL_NAMES,
    *CAPITAL_SKILL_NAMES,
    "master-munger",
    "master-li",
    "sector-deep-research",
    "sector-funnel",
    "ta-investment-checklist",
    "ta-quality-screen",
)
