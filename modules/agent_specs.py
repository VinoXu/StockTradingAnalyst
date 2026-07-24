"""Single source of truth: Agent roster, evidence packs, Team Lead skills."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from modules.query_planner import QueryPlan
from modules.skill_registry import (
    MASTER_SKILL_NAMES,
    MURPHY_SKILL_NAMES,
    NISON_SKILL_NAMES,
)

# --- Parallel Agent roster (agent_id → primary Skill) ---

SYMBOL_AGENT_ROSTER: tuple[tuple[str, str], ...] = (
    ("nison", "nison-candlestick-patterns"),
    ("murphy", "ta-trend-structure"),
    ("duan", "master-duan"),
    ("buffett", "master-buffett"),
    ("munger", "master-munger"),
    ("li", "master-li"),
)

SECTOR_AGENT_ROSTER: tuple[tuple[str, str], ...] = (
    ("nison", "nison-candlestick-patterns"),
    ("murphy", "ta-trend-structure"),
    ("munger", "master-munger"),
    ("li", "master-li"),
)

NEWS_PULSE_AGENT_ROSTER: tuple[tuple[str, str], ...] = (
    ("murphy", "ta-trend-structure"),
    ("munger", "master-munger"),
    ("news", "news-pulse"),
)

PORTFOLIO_AGENT_ROSTER: tuple[tuple[str, str], ...] = (
    ("murphy", "ta-trend-structure"),
    ("munger", "master-munger"),
    ("portfolio", "portfolio-review"),
)

DYP_ASK_AGENT_ROSTER: tuple[tuple[str, str], ...] = (
    ("duan", "dyp-ask"),
)

TA_SCREEN_AGENT_ROSTER: tuple[tuple[str, str], ...] = (
    ("murphy", "ta-trend-structure"),
    ("checklist", "ta-investment-checklist"),
    ("quality", "ta-quality-screen"),
)

SITUATION_ADVICE_AGENT_ROSTER: tuple[tuple[str, str], ...] = (
    ("murphy", "ta-trend-structure"),
    ("munger", "master-munger"),
    ("checklist", "ta-investment-checklist"),
)

MASTER_AGENT_IDS = frozenset({"duan", "buffett", "munger", "li"})


@dataclass(frozen=True)
class AgentBinding:
    agent_id: str
    skill_name: str


def agent_roster_for_plan(plan: QueryPlan) -> tuple[tuple[str, str], ...]:
    # LLM 任务拆分优先；白名单校验后的 override
    if plan.agent_roster_override:
        return plan.agent_roster_override
    wf = plan.workflow or ""
    if wf == "news_pulse":
        return NEWS_PULSE_AGENT_ROSTER
    if wf == "dyp_ask":
        return DYP_ASK_AGENT_ROSTER
    if wf == "portfolio_review":
        return PORTFOLIO_AGENT_ROSTER
    if wf == "ta_screen":
        return TA_SCREEN_AGENT_ROSTER
    if wf == "situation_advice":
        return SITUATION_ADVICE_AGENT_ROSTER
    if plan.research_mode == "sector_research":
        return SECTOR_AGENT_ROSTER
    if plan.research_mode == "symbol_research":
        return SYMBOL_AGENT_ROSTER
    return ()


def team_lead_skills_for_plan(plan: QueryPlan) -> tuple[str, ...]:
    """Skills injected into Team Lead system prompt (not per-Agent calls)."""
    if plan.team_lead_skills_override:
        return plan.team_lead_skills_override
    wf = plan.workflow or ""
    if wf == "news_pulse":
        return ("news-pulse", "ta-trend-structure")
    if wf == "dyp_ask":
        return ("dyp-ask", "master-duan")
    if wf == "portfolio_review":
        return ("portfolio-review", "ta-trend-structure", "thesis-drift")
    if wf == "ta_screen":
        return ("ta-investment-checklist", "ta-quality-screen", "ta-trend-structure")
    if wf == "situation_advice":
        return ("ta-trend-structure", "ta-investment-checklist", "ta-volume-price")
    if plan.research_mode == "symbol_research":
        return (
            "symbol-deep-research",
            "research-report-reader",
            "financial-data",
            "symbol-earnings-review",
            "ta-investment-checklist",
        )
    if plan.research_mode == "sector_research":
        return (
            "sector-deep-research",
            "sector-funnel",
            "ta-investment-checklist",
            "ta-quality-screen",
        )
    return ()


def uses_parallel_agents(plan: QueryPlan) -> bool:
    return bool(agent_roster_for_plan(plan))


def should_persist_thesis(plan: QueryPlan) -> bool:
    """Only full symbol deep-research writes investment thesis."""
    if plan.workflow in ("news_pulse", "dyp_ask", "portfolio_review", "ta_screen", "situation_advice"):
        return False
    return plan.research_mode == "symbol_research" and bool(plan.symbols)


# --- Evidence packs (fed to parallel Agent LLM calls) ---


def _compact_market_for_agents(fetched: dict[str, Any]) -> dict[str, Any] | None:
    """Slim market pack so Agents can conclude on breadth/index, not only stocks."""
    market = fetched.get("market")
    if not isinstance(market, dict) or not market:
        return None
    breadth = market.get("breadth") or {}
    dow = market.get("dow") or {}
    live = market.get("index_live") or {}
    live_sz = market.get("index_live_sz") or {}
    pack: dict[str, Any] = {}
    if breadth.get("available"):
        pack["breadth"] = {
            "available": True,
            "trade_date": breadth.get("trade_date"),
            "rising_count": breadth.get("rising_count"),
            "falling_count": breadth.get("falling_count"),
            "limit_up": breadth.get("limit_up"),
            "limit_down": breadth.get("limit_down"),
            "bias": breadth.get("bias"),
            "source_label": breadth.get("source_label"),
        }
    else:
        pack["breadth"] = {"available": False}
    pack["dow"] = {
        "available": bool(dow.get("available")),
        "state_cn": dow.get("state_cn"),
        "notes": (dow.get("notes") or [])[:2],
    }
    if live.get("available") and live.get("price") is not None:
        pack["index_live_sh"] = {
            "name": live.get("name") or "上证指数",
            "price": live.get("price"),
            "change_pct": live.get("change_pct"),
            "amount": live.get("amount"),
            "as_of_label": live.get("as_of_label"),
        }
    else:
        pack["index_live_sh"] = {
            "available": False,
            "error": (live or {}).get("error") or "上证盘中价本轮未返回",
        }
    if live_sz.get("available") and live_sz.get("price") is not None:
        pack["index_live_sz"] = {
            "name": live_sz.get("name") or "深证成指",
            "price": live_sz.get("price"),
            "change_pct": live_sz.get("change_pct"),
            "amount": live_sz.get("amount"),
            "as_of_label": live_sz.get("as_of_label"),
        }
    else:
        pack["index_live_sz"] = {
            "available": False,
            "error": (live_sz or {}).get("error") or "深证盘中价本轮未返回",
        }
    turnover = market.get("two_market_turnover") or {}
    pack["two_market_turnover"] = {
        "available": bool(turnover.get("available")),
        "amount_yi": turnover.get("amount_yi"),
        "amount_yi_text": turnover.get("amount_yi_text"),
        "partial": turnover.get("partial"),
        "error": turnover.get("error"),
        "note": turnover.get("note"),
    }
    essentials = market.get("essentials")
    if isinstance(essentials, dict):
        pack["essentials"] = essentials
    return pack


def build_agent_evidence_symbol(fetched: dict[str, Any]) -> dict[str, Any]:
    symbols = fetched.get("symbols") or []
    research = fetched.get("research_reports") or {}
    fundamentals = fetched.get("fundamentals") or {}
    market = _compact_market_for_agents(fetched)
    data_errors = [
        {
            "symbol": s.get("symbol"),
            "name": s.get("name"),
            "error": s.get("error"),
        }
        for s in symbols
        if isinstance(s, dict) and not s.get("available", True)
    ]
    err_note = (
        "若有 data_errors：必须把 error 原文告知用户，禁止改口猜「是否为A股」"
        if data_errors
        else ""
    )
    return {
        "nison": {
            "agent": "nison",
            "skills": list(NISON_SKILL_NAMES),
            "focus": "蜡烛图形态与信号汇合；使用 symbols[].candle_bars 计量字段",
            "market": market,
            "data_errors": data_errors,
            "period_note": err_note,
            "symbols": [
                {
                    "name": s.get("name") or s.get("symbol"),
                    "patterns_cn": s.get("candle_patterns_cn") or [],
                    "bars_count": len(s.get("candle_bars") or []),
                }
                for s in symbols
                if s.get("available")
            ],
        },
        "murphy": {
            "agent": "murphy",
            "skills": list(MURPHY_SKILL_NAMES),
            "focus": "趋势结构、振荡指标、量价、价格形态；有 market 时必须先对大盘广度/指数表态",
            "market": market,
            "data_errors": data_errors,
            "period_note": err_note,
            "symbols": [
                {
                    "name": s.get("name") or s.get("symbol"),
                    "trend_regime": s.get("trend_regime"),
                    "osc_bias": s.get("osc_bias"),
                    "capital_flow_note": s.get("capital_flow_note"),
                }
                for s in symbols
                if s.get("available")
            ],
        },
        "duan": {
            **_master_evidence("duan", research, fundamentals, symbols),
            "market": market,
            "data_errors": data_errors,
            "period_note": err_note,
        },
        "buffett": {
            **_master_evidence("buffett", research, fundamentals, symbols),
            "market": market,
            "data_errors": data_errors,
            "period_note": err_note,
            "skill_extra": "symbol-earnings-review",
            "focus": "财务质量、估值安全边际；对照 profits/ROE/毛利率与研报 EPS",
        },
        "munger": {
            **_master_evidence("munger", research, fundamentals, symbols),
            "market": market,
            "data_errors": data_errors,
            "period_note": err_note,
        },
        "li": {
            **_master_evidence("li", research, fundamentals, symbols),
            "market": market,
            "data_errors": data_errors,
            "period_note": err_note,
        },
    }


def _master_evidence(
    agent_id: str,
    research: dict[str, Any],
    fundamentals: dict[str, Any],
    symbols: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "agent": agent_id,
        "skills": list(MASTER_SKILL_NAMES),
        "research": research,
        "fundamentals": fundamentals,
        "symbol_ta": symbols[:2],
    }


def build_agent_evidence_sector(fetched: dict[str, Any]) -> dict[str, Any]:
    picks = fetched.get("sector_picks") or {}
    top = (picks.get("top_picks") or [])[:5]
    weak = (picks.get("weak_boards") or [])[:8]
    market = _compact_market_for_agents(fetched)
    period = fetched.get("sector_period_rank") or {}
    period_pack: dict[str, Any] | None = None
    if isinstance(period, dict) and period.get("available"):
        period_pack = {
            "trading_days": period.get("trading_days"),
            "window": period.get("window"),
            "top_losers": (period.get("top_losers") or [])[:10],
            "top_gainers": (period.get("top_gainers") or [])[:5],
            "note": period.get("note"),
        }
    # 显式跌幅榜（当日口径），避免 Agent 只围绕优选板块打转
    sectors = fetched.get("sectors") or {}
    daily_losers: list[dict[str, Any]] = []
    if isinstance(sectors, dict):
        for key in ("industry", "concept"):
            block = sectors.get(key) or {}
            if isinstance(block, dict) and block.get("available"):
                for row in (block.get("top_losers") or [])[:6]:
                    if isinstance(row, dict):
                        daily_losers.append(
                            {
                                "name": row.get("name"),
                                "change_pct": row.get("change_pct"),
                                "board_type": key,
                            }
                        )
        daily_losers.sort(key=lambda r: float(r.get("change_pct") or 0))
        daily_losers = daily_losers[:10]
    if period_pack:
        period_note = (
            f"用户要区间排行：必须优先引用 sector_period_rank（近"
            f"{period_pack.get('trading_days')}个交易日累计），禁止只用当日涨跌幅敷衍"
        )
    else:
        period_note = "板块涨跌幅口径为最新交易日，不是近2周累计收益"
    return {
        "nison": {
            "agent": "nison",
            "skills": list(NISON_SKILL_NAMES),
            "focus": "板块领涨股蜡烛图形态",
            "sector_picks": top,
            "daily_losers": daily_losers,
            "sector_period_rank": period_pack,
            "period_note": period_note,
            "market": market,
        },
        "murphy": {
            "agent": "murphy",
            "skills": list(MURPHY_SKILL_NAMES),
            "focus": "板块趋势、广度、量价；有区间榜时必须直接报 period losers",
            "sector_picks": top,
            "weak_boards": weak,
            "daily_losers": daily_losers,
            "sector_period_rank": period_pack,
            "period_note": period_note,
            "participant_flow": fetched.get("participant_flow"),
            "market": market,
        },
        "munger": {
            "agent": "munger",
            "skill": "master-munger",
            "focus": "逆向：过热、广度差、独狼领涨、失败情景",
            "sector_picks": top,
            "daily_losers": daily_losers,
            "sector_period_rank": period_pack,
            "period_note": period_note,
            "market": market,
        },
        "li": {
            "agent": "li",
            "skill": "master-li",
            "focus": "产业趋势与长期确定性（无个股财报）",
            "sector_picks": top,
            "daily_losers": daily_losers,
            "sector_period_rank": period_pack,
            "period_note": period_note,
            "market": market,
        },
    }


def build_agent_evidence_news_pulse(fetched: dict[str, Any]) -> dict[str, Any]:
    return {
        "news": {
            "agent": "news",
            "news_pulse": fetched.get("news_pulse"),
            "symbols": fetched.get("symbols"),
        },
        "murphy": {
            "agent": "murphy",
            "symbols": fetched.get("symbols"),
            "skills": list(MURPHY_SKILL_NAMES),
        },
        "munger": {
            "agent": "munger",
            "skill": "master-munger",
            "news_pulse": fetched.get("news_pulse"),
        },
    }


def build_agent_evidence_portfolio(fetched: dict[str, Any]) -> dict[str, Any]:
    return {
        "murphy": {
            "agent": "murphy",
            "focus": "持仓标的趋势与结构",
            "symbols": fetched.get("symbols"),
            "skills": list(MURPHY_SKILL_NAMES),
        },
        "munger": {
            "agent": "munger",
            "skill": "master-munger",
            "focus": "组合失败情景、集中度风险",
            "holdings": fetched.get("holdings"),
            "symbols": fetched.get("symbols"),
        },
        "portfolio": {
            "agent": "portfolio",
            "skill": "portfolio-review",
            "holdings": fetched.get("holdings"),
            "symbols": fetched.get("symbols"),
            "thesis_drift": fetched.get("thesis_drift"),
        },
    }


def build_agent_evidence_dyp(fetched: dict[str, Any]) -> dict[str, Any]:
    return {
        "duan": {
            "agent": "duan",
            "skill": "dyp-ask",
            "symbols": fetched.get("symbols"),
            "fundamentals": fetched.get("fundamentals"),
            "research": fetched.get("research_reports"),
            "holdings": fetched.get("holdings"),
        }
    }


def build_agent_evidence_ta_screen(fetched: dict[str, Any]) -> dict[str, Any]:
    picks = fetched.get("sector_picks") or {}
    return {
        "murphy": {
            "agent": "murphy",
            "symbols": fetched.get("symbols"),
            "sector_picks": (picks.get("top_picks") or [])[:5],
            "skills": list(MURPHY_SKILL_NAMES),
        },
        "checklist": {
            "agent": "checklist",
            "skill": "ta-investment-checklist",
            "symbols": fetched.get("symbols"),
            "sector_picks": (picks.get("top_picks") or [])[:5],
            "fundamentals": fetched.get("fundamentals"),
            "participant_flow": fetched.get("participant_flow"),
        },
        "quality": {
            "agent": "quality",
            "skill": "ta-quality-screen",
            "symbols": fetched.get("symbols"),
            "sector_picks": (picks.get("top_picks") or [])[:5],
            "sectors": fetched.get("sectors"),
        },
    }


def build_agent_evidence(fetched: dict[str, Any], plan: QueryPlan) -> dict[str, Any]:
    wf = plan.workflow or ""
    if wf == "news_pulse":
        return build_agent_evidence_news_pulse(fetched)
    if wf == "dyp_ask":
        return build_agent_evidence_dyp(fetched)
    if wf == "portfolio_review":
        return build_agent_evidence_portfolio(fetched)
    if wf == "ta_screen":
        return build_agent_evidence_ta_screen(fetched)
    if plan.research_mode == "symbol_research":
        return build_agent_evidence_symbol(fetched)
    if plan.research_mode == "sector_research":
        return build_agent_evidence_sector(fetched)
    return {}


def evidence_for_agent(agent_id: str, fetched: dict[str, Any]) -> dict[str, Any]:
    """Slice of agent_evidence for one parallel LLM call."""
    pack = fetched.get("agent_evidence") or {}
    if agent_id in pack:
        return pack[agent_id]
    # 语义拆分的非常规 agent：从 fetched 拼最小证据
    symbols = fetched.get("symbols")
    picks = ((fetched.get("sector_picks") or {}).get("top_picks") or [])[:5]
    if agent_id == "news":
        return {"news_pulse": fetched.get("news_pulse"), "symbols": symbols}
    if agent_id == "portfolio":
        return {"holdings": fetched.get("holdings"), "symbols": symbols}
    if agent_id in ("checklist", "quality"):
        return {
            "symbols": symbols,
            "sector_picks": picks,
            "fundamentals": fetched.get("fundamentals"),
        }
    if agent_id == "earnings":
        return {"fundamentals": fetched.get("fundamentals"), "research": fetched.get("research_reports")}
    if agent_id in ("murphy", "nison"):
        return {"symbols": symbols, "sector_picks": picks}
    if agent_id in ("duan", "buffett", "munger", "li"):
        return {
            "fundamentals": fetched.get("fundamentals"),
            "research": fetched.get("research_reports"),
            "symbols": symbols,
        }
    return {"symbols": symbols}


def per_agent_skill_map(plan: QueryPlan) -> dict[str, str]:
    return dict(agent_roster_for_plan(plan))
