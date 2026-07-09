"""Forward-looking analysis mandate injected into chat turns."""

from __future__ import annotations

from typing import Any

from modules.outlook_format import resolve_guidance_sector_label

CORE_ANALYSIS_MANDATE = """【分析使命·必遵】
你的价值不是复述行情，而是用 Skill 判据做「向前看」的投研：
1. 机会挖掘：对照 Skill 找趋势延续、形态突破、量价配合、多周期共振；说明接下来 1～2 周可能的演化与触发条件（若…则…）。
2. 风险预警：对照 Skill 找尚未完全兑现、但概率升高的风险（假突破、顶背离、指数与广度背离、胀爆、关键支撑失守等）。
3. 禁止把「今天涨了多少」当唯一结论；每条判断须挂钩 Skill 判据或数据字段（如 trend.regime、sector_picks.pick_reason、candle_bars）。
4. 结构配合时须明确机会：可说顺势参与、分批加仓、回调低吸窗口；只有结构不配合才强调减仓观望。
5. 禁止保证收益、禁止具体下单价位；仍用偏多观察/偏空观察/观望表述立场。"""


def build_closing_guidance(fetched: dict[str, Any]) -> str:
    """Workflow-aware closing: opportunity-seeking vs risk-first vs balanced."""
    workflow = fetched.get("workflow") or ""
    intents = set(fetched.get("query_intents") or [])
    label = resolve_guidance_sector_label(fetched)
    wants_opportunity = (
        workflow in ("opportunity_scan", "sector_deep_dive")
        or fetched.get("wants_sector_pick")
        or "opportunity" in intents
        or any(k in " ".join(fetched.get("query_keywords") or []) for k in ("加仓", "机会", "看好", "推荐", "买入", "购买"))
    )
    wants_risk = workflow == "risk_scan" or "risk" in intents

    if wants_opportunity and not wants_risk:
        return f"""【收尾态度】正文结束后用 1～2 句给出可执行的仓位观点（不要小标题）：
结构配合（趋势+形态+板块广度 OK）：明确点名{label}，可说「趋势确认，可分批跟进；回调到支撑可加仓，失效条件是…」
已涨但未破坏结构：可说「主线仍在，不追涨停但分时回踩/五日线附近可低吸」
必须附带 1 个证伪条件（如「若放量跌破某某支撑则观点作废」）。
禁止只报涨跌；禁止空泛「可关注」；禁止结构明明配合却一味说别追高。"""

    if wants_risk:
        return f"""【收尾态度】正文结束后用 1～2 句说风险与仓位（不要小标题）：
点明{label}及相关板块最可能触发的 1～2 个尚未兑现风险（Skill 依据），再说减仓/观望/回避。
若数据支持个别逆势机会，可一句带过，但本问以规避为主。"""

    return f"""【收尾态度】正文结束后用 1～2 句平衡机会与风控（不要小标题）：
先说{label}方向：结构配合则给「参与方式」（分批/低吸/加仓窗口）；结构不配合则给「回避或减仓」。
必须同时回答「机会在哪」和「什么信号出现要收手」，各至少一点。"""
